"""
Service: LegalDemandService (Onda 2 — Fundação jurídica: modelo canônico)

`LegalDemand` unifica as 7 naturezas de demanda jurídica (Plano Mestre §5)
sem duplicar o que já existe em `Matter`/`FinanceEntry`/
`ContractBillingProposal`: quando a demanda tem um caso operacional
correspondente, `matter_id` referencia o `Matter` já criado (o service só
valida que ele pertence ao mesmo workspace, nunca copia seus dados).

Idempotência: criação é idempotente por `external_ref` (mesmo padrão de
`FinanceEntry.external_ref` — `uq_legal_demands_workspace_external_ref`).
Uma segunda criação com o mesmo `external_ref` no mesmo workspace devolve a
demanda já existente em vez de duplicar. Transição de fase é idempotente
por valor: pedir a fase já corrente é um no-op (sem novo evento de
auditoria) — cobre reenvio de retry sem duplicar histórico.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal
from app.models.finance import FinanceEntry
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_demand import (
    REQUIRED_FIELDS_BY_NATURE,
    LegalDemand,
    LegalDemandNature,
    LegalDemandPhase,
)
from app.models.matter import Matter
from app.models.user import User
from app.schemas.legal_demand import LegalDemandCreate, LegalDemandUpdate
from app.services.audit_actor_service import resolve_human_actor_label

LEGAL_DEMAND_AUDIT_ENTITY_TYPE = "legal_demand"


class LegalDemandNotFoundError(HTTPException):
    def __init__(self, detail: str = "Demanda jurídica não encontrada.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class LegalDemandValidationError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


class LegalDemandService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Leitura auxiliar (sempre escopada por workspace) ────────────────

    async def _get_demand(self, workspace_id: UUID, demand_id: UUID) -> LegalDemand:
        demand = await self.db.scalar(
            select(LegalDemand).where(
                LegalDemand.id == demand_id,
                LegalDemand.workspace_id == workspace_id,
            )
        )
        if demand is None:
            raise LegalDemandNotFoundError()
        return demand

    async def get_demand(self, workspace_id: UUID, demand_id: UUID) -> LegalDemand:
        return await self._get_demand(workspace_id, demand_id)

    async def _get_by_external_ref(
        self, workspace_id: UUID, external_ref: str
    ) -> Optional[LegalDemand]:
        return await self.db.scalar(
            select(LegalDemand).where(
                LegalDemand.workspace_id == workspace_id,
                LegalDemand.external_ref == external_ref,
            )
        )

    async def _require_client(self, workspace_id: UUID, client_id: UUID) -> Client:
        client = await self.db.scalar(
            select(Client).where(
                Client.id == client_id,
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
        )
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado."
            )
        return client

    async def _require_matter(self, workspace_id: UUID, matter_id: UUID) -> Matter:
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado."
            )
        return matter

    async def _require_contract_proposal(
        self, workspace_id: UUID, contract_ref: UUID
    ) -> ContractBillingProposal:
        proposal = await self.db.scalar(
            select(ContractBillingProposal).where(
                ContractBillingProposal.id == contract_ref,
                ContractBillingProposal.workspace_id == workspace_id,
            )
        )
        if proposal is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Proposta de cobrança (contract_ref) não encontrada.",
            )
        return proposal

    async def _require_finance_entry(
        self, workspace_id: UUID, finance_ref: UUID
    ) -> FinanceEntry:
        entry = await self.db.scalar(
            select(FinanceEntry).where(
                FinanceEntry.id == finance_ref,
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.deleted_at.is_(None),
            )
        )
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lançamento financeiro (finance_ref) não encontrado.",
            )
        return entry

    async def _require_responsible_user(self, workspace_id: UUID, user_id: UUID) -> User:
        # Sem escopo de workspace na tabela users; a checagem de pertencimento
        # ao workspace é feita via membership no gateway HTTP (require_permission)
        # — aqui validamos apenas que o usuário existe e está ativo.
        user = await self.db.scalar(
            select(User).where(
                User.id == user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuário responsável não encontrado ou inativo.",
            )
        return user

    # ── Derivação por natureza ───────────────────────────────────────────

    @staticmethod
    def _assert_required_fields(payload: LegalDemandCreate) -> None:
        """Deriva os campos obrigatórios pela natureza (Plano Mestre §5) e
        recusa a criação se algum estiver ausente — nunca fabrica um valor
        default para um campo que a natureza exige."""
        required = REQUIRED_FIELDS_BY_NATURE.get(payload.nature, ())
        missing = [field for field in required if not getattr(payload, field, None)]
        if missing:
            raise LegalDemandValidationError(
                f"Natureza '{payload.nature.value}' exige os campos: {', '.join(missing)}."
            )

    # ── Criação (idempotente por external_ref) ──────────────────────────

    async def create(
        self,
        workspace_id: UUID,
        payload: LegalDemandCreate,
        *,
        actor_user_id: Optional[UUID] = None,
    ) -> LegalDemand:
        if payload.external_ref:
            existing = await self._get_by_external_ref(workspace_id, payload.external_ref)
            if existing is not None:
                return existing

        self._assert_required_fields(payload)

        await self._require_client(workspace_id, payload.client_id)
        if payload.matter_id is not None:
            await self._require_matter(workspace_id, payload.matter_id)
        if payload.contract_ref is not None:
            await self._require_contract_proposal(workspace_id, payload.contract_ref)
        if payload.finance_ref is not None:
            await self._require_finance_entry(workspace_id, payload.finance_ref)
        if payload.responsible_user_id is not None:
            await self._require_responsible_user(workspace_id, payload.responsible_user_id)

        demand = LegalDemand(
            workspace_id=workspace_id,
            external_ref=payload.external_ref,
            nature=payload.nature,
            area=payload.area,
            subarea=payload.subarea,
            client_id=payload.client_id,
            parties=[party.model_dump(mode="json") for party in payload.parties],
            unit=payload.unit,
            team=payload.team,
            responsible_user_id=payload.responsible_user_id,
            strategy=payload.strategy,
            risk=payload.risk,
            value=payload.value,
            phase=LegalDemandPhase.triagem,
            sla=payload.sla,
            playbook_ref=payload.playbook_ref,
            documents_required=list(payload.documents_required),
            matter_id=payload.matter_id,
            contract_ref=payload.contract_ref,
            finance_ref=payload.finance_ref,
        )
        self.db.add(demand)
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, actor_user_id)
                    if actor_user_id
                    else "Sistema — Criação de demanda jurídica"
                ),
                event_type="legal_demand_created",
                entity_type=LEGAL_DEMAND_AUDIT_ENTITY_TYPE,
                entity_id=demand.id,
                correlation_id=str(demand.matter_id) if demand.matter_id else None,
                summary=f"Demanda jurídica criada — natureza={demand.nature.value}.",
                after_data={
                    "nature": demand.nature.value,
                    "clientId": str(demand.client_id),
                    "matterId": str(demand.matter_id) if demand.matter_id else None,
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return demand

    # ── Atualização de campos mutáveis (nature/phase são imutáveis aqui) ─

    async def update(
        self,
        workspace_id: UUID,
        demand_id: UUID,
        payload: LegalDemandUpdate,
        *,
        actor_user_id: Optional[UUID] = None,
    ) -> LegalDemand:
        demand = await self._get_demand(workspace_id, demand_id)
        updates = payload.model_dump(exclude_unset=True, by_alias=False)
        if not updates:
            return demand

        if "matter_id" in updates and updates["matter_id"] is not None:
            await self._require_matter(workspace_id, updates["matter_id"])
        if "contract_ref" in updates and updates["contract_ref"] is not None:
            await self._require_contract_proposal(workspace_id, updates["contract_ref"])
        if "finance_ref" in updates and updates["finance_ref"] is not None:
            await self._require_finance_entry(workspace_id, updates["finance_ref"])
        if "responsible_user_id" in updates and updates["responsible_user_id"] is not None:
            await self._require_responsible_user(workspace_id, updates["responsible_user_id"])

        if "parties" in updates and updates["parties"] is not None:
            updates["parties"] = [
                party if isinstance(party, dict) else party.model_dump(mode="json")
                for party in payload.parties or []
            ]

        for field, value in updates.items():
            setattr(demand, field, value)
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, actor_user_id)
                    if actor_user_id
                    else "Sistema — Atualização de demanda jurídica"
                ),
                event_type="legal_demand_updated",
                entity_type=LEGAL_DEMAND_AUDIT_ENTITY_TYPE,
                entity_id=demand.id,
                correlation_id=str(demand.matter_id) if demand.matter_id else None,
                summary=f"Demanda jurídica atualizada — campos: {', '.join(sorted(updates))}.",
                after_data={key: str(value) for key, value in updates.items()},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return demand

    # ── Transição de fase (idempotente por valor) ───────────────────────

    async def transition_phase(
        self,
        workspace_id: UUID,
        demand_id: UUID,
        to_phase: LegalDemandPhase,
        *,
        reason: Optional[str] = None,
        actor_user_id: Optional[UUID] = None,
    ) -> LegalDemand:
        demand = await self._get_demand(workspace_id, demand_id)
        if demand.phase == to_phase:
            # Idempotente por valor: reenvio do mesmo pedido não gera novo
            # evento de auditoria nem toca updated_at.
            return demand

        from_phase = demand.phase
        demand.phase = to_phase
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, actor_user_id)
                    if actor_user_id
                    else "Sistema — Transição de fase da demanda jurídica"
                ),
                event_type="legal_demand_phase_transitioned",
                entity_type=LEGAL_DEMAND_AUDIT_ENTITY_TYPE,
                entity_id=demand.id,
                correlation_id=str(demand.matter_id) if demand.matter_id else None,
                summary=f"Fase alterada: {from_phase.value} -> {to_phase.value}.",
                after_data={
                    "fromPhase": from_phase.value,
                    "toPhase": to_phase.value,
                    "reason": reason,
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return demand

    # ── Listagem (filtros simples, sempre escopada por workspace) ───────

    async def list_demands(
        self,
        workspace_id: UUID,
        *,
        nature: Optional[LegalDemandNature] = None,
        phase: Optional[LegalDemandPhase] = None,
        matter_id: Optional[UUID] = None,
        client_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LegalDemand], int]:
        filters = [LegalDemand.workspace_id == workspace_id]
        if nature is not None:
            filters.append(LegalDemand.nature == nature)
        if phase is not None:
            filters.append(LegalDemand.phase == phase)
        if matter_id is not None:
            filters.append(LegalDemand.matter_id == matter_id)
        if client_id is not None:
            filters.append(LegalDemand.client_id == client_id)

        total = await self.db.scalar(
            select(func.count()).select_from(LegalDemand).where(*filters)
        )
        rows = await self.db.scalars(
            select(LegalDemand)
            .where(*filters)
            .order_by(LegalDemand.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), int(total or 0)
