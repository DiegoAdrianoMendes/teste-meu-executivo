"""
Service: CaseStrategyService — Matriz Estratégia Fato–Prova–Pedido
(Onda 5 — Caso 360, Plano Mestre §3 item 8).

A matriz cruza fato → prova(s) → pedido(s) de uma `LegalDemand` e evidencia
LACUNAS, sem nunca fabricar um dado que não existe:

  * fato controvertido sem nenhuma prova em status 'tem' → gera pendência
    (kind "fato_sem_prova"). Toda vez que a matriz muda (fato/prova
    adicionados), o serviço reavalia essa lacuna e, se ainda aberta, abre
    (idempotente) um item na Central de Exceções já existente
    (`ExceptionKind.documento_indispensavel_ausente` — Onda 2) e anota o
    tipo de prova em falta em `LegalDemand.documents_required` (o mesmo
    checklist declarativo que a jornada da Onda 4 já lê — não nasce um
    pedido de documento paralelo). Nenhuma das duas ações é destrutiva:
    reabrir a mesma pendência é sempre um no-op (dedupe_key estável) e o
    checklist só cresce por união, nunca duplica entrada;
  * adicionar uma prova em status 'tem' à MESMA lacuna a fecha: a próxima
    leitura da matriz (`get_matrix`) simplesmente para de reportar aquele
    fato como lacuna — o item já aberto na Central de Exceções continua lá
    até um humano resolvê-lo explicitamente (`ExceptionCenterService.resolve`
    exige `resolved_by` humano; este serviço nunca resolve sozinho);
  * pedido sem nenhum fato de sustentação → apenas SINALIZADO como lacuna
    (kind "pedido_sem_fato") na leitura da matriz. Não abre exceção: não é
    um dos 11 tipos nomeados no Plano Mestre §6, e a lista vazia de
    `supporting_fact_ids` já é, por si, o sinal — reabrir uma exceção nova
    aqui duplicaria disciplina em vez de reusar a existente.

Criação de fato/prova/pedido é sempre escopada por workspace e valida que
`fact_id`/`document_id`/`supporting_fact_ids` pertencem à MESMA demanda —
nunca cruza dado entre casos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.approval import ApprovalPriority
from app.models.case_strategy import CaseFact, CasePleading, CaseProofLink, ProofForce, ProofStatus
from app.models.document import Document
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_demand import LegalDemand
from app.models.legal_exception import ExceptionKind, LegalException
from app.models.matter import Matter
from app.schemas.case_strategy import CaseFactCreate, CasePleadingCreate, CaseProofLinkCreate
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.exception_center_service import ExceptionCenterService

CASE_STRATEGY_AUDIT_ENTITY_TYPE = "case_strategy"
_GAP_EXCEPTION_ORIGIN = "case_strategy_service.sync_gaps"


class CaseStrategyNotFoundError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


@dataclass(frozen=True)
class CaseStrategyGapResult:
    kind: str
    fact_id: Optional[UUID]
    pleading_id: Optional[UUID]
    description: str
    exception_id: Optional[UUID]


@dataclass(frozen=True)
class CaseStrategyMatrix:
    legal_demand_id: UUID
    facts: list[CaseFact]
    proof_links: list[CaseProofLink]
    pleadings: list[CasePleading]
    gaps: list[CaseStrategyGapResult]


def _fact_gap_dedupe_key(fact_id: UUID) -> str:
    return f"fato_sem_prova:{fact_id}"


def _fact_document_requirement_label(fact: CaseFact) -> str:
    # Rótulo estável (não o texto do fato, que pode mudar) — usado tanto na
    # dedupe_key da exceção quanto no checklist declarativo, para que uma
    # nova varredura sempre reconheça a MESMA pendência.
    return f"prova_fato:{fact.id}"


class CaseStrategyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Leitura auxiliar (sempre escopada por workspace) ────────────────

    async def _require_demand(self, workspace_id: UUID, demand_id: UUID) -> LegalDemand:
        demand = await self.db.scalar(
            select(LegalDemand).where(
                LegalDemand.id == demand_id,
                LegalDemand.workspace_id == workspace_id,
            )
        )
        if demand is None:
            raise CaseStrategyNotFoundError("Demanda jurídica não encontrada.")
        return demand

    async def _require_fact(
        self, workspace_id: UUID, demand_id: UUID, fact_id: UUID
    ) -> CaseFact:
        fact = await self.db.scalar(
            select(CaseFact).where(
                CaseFact.id == fact_id,
                CaseFact.legal_demand_id == demand_id,
                CaseFact.workspace_id == workspace_id,
            )
        )
        if fact is None:
            raise CaseStrategyNotFoundError("Fato não encontrado nesta demanda.")
        return fact

    async def _require_document(
        self, workspace_id: UUID, demand: LegalDemand, document_id: UUID
    ) -> Document:
        document = await self.db.scalar(
            select(Document)
            .join(Matter, Matter.id == Document.matter_id)
            .where(
                Document.id == document_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if document is None:
            raise CaseStrategyNotFoundError(
                "Documento (prova) não encontrado neste workspace."
            )
        # Quando a demanda já tem um Matter operacional vinculado, a prova
        # precisa pertencer ao MESMO caso — nunca cruza documento de outro
        # caso, mesmo que do mesmo workspace.
        if demand.matter_id is not None and document.matter_id != demand.matter_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Documento pertence a um caso diferente do vinculado a esta demanda.",
            )
        return document

    # ── Fato ─────────────────────────────────────────────────────────────

    async def add_fact(
        self,
        workspace_id: UUID,
        demand_id: UUID,
        payload: CaseFactCreate,
        *,
        actor_user_id: Optional[UUID] = None,
    ) -> CaseFact:
        demand = await self._require_demand(workspace_id, demand_id)

        description = payload.description.strip()
        # Idempotência por conteúdo: reenviar o MESMO fato (mesma descrição
        # textual) para a mesma demanda devolve o fato já existente em vez
        # de duplicar — não há external_ref natural para um fato alegado.
        existing = await self.db.scalar(
            select(CaseFact).where(
                CaseFact.legal_demand_id == demand_id,
                CaseFact.workspace_id == workspace_id,
                CaseFact.description == description,
            )
        )
        if existing is not None:
            return existing

        fact = CaseFact(
            workspace_id=workspace_id,
            legal_demand_id=demand_id,
            description=description,
            occurred_at=payload.occurred_at,
            is_controverted=payload.is_controverted,
            origin=payload.origin,
            created_by_user_id=actor_user_id,
        )
        self.db.add(fact)
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, actor_user_id)
                    if actor_user_id
                    else "Sistema — Estratégia do caso"
                ),
                event_type="case_fact_added",
                entity_type=CASE_STRATEGY_AUDIT_ENTITY_TYPE,
                entity_id=fact.id,
                correlation_id=str(demand_id),
                summary=f"Fato adicionado à estratégia — controvertido={fact.is_controverted}.",
                after_data={"legalDemandId": str(demand_id), "description": description},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()

        await self._sync_gaps(workspace_id, demand)
        return fact

    # ── Prova ────────────────────────────────────────────────────────────

    async def add_proof(
        self,
        workspace_id: UUID,
        demand_id: UUID,
        fact_id: UUID,
        payload: CaseProofLinkCreate,
        *,
        actor_user_id: Optional[UUID] = None,
    ) -> CaseProofLink:
        demand = await self._require_demand(workspace_id, demand_id)
        fact = await self._require_fact(workspace_id, demand_id, fact_id)

        document: Optional[Document] = None
        if payload.document_id is not None:
            document = await self._require_document(workspace_id, demand, payload.document_id)
            # Idempotência: a MESMA prova (documento) já vinculada ao MESMO
            # fato devolve o vínculo existente em vez de duplicar.
            existing = await self.db.scalar(
                select(CaseProofLink).where(
                    CaseProofLink.fact_id == fact.id,
                    CaseProofLink.document_id == document.id,
                )
            )
            if existing is not None:
                return existing

        # Nunca fabrica 'tem' sem documento: deriva o status só quando o
        # chamador não informou um explicitamente.
        derived_status = payload.status or (
            ProofStatus.tem if document is not None else ProofStatus.falta
        )
        if derived_status == ProofStatus.tem and document is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Status 'tem' exige documentId — não é possível marcar prova obtida sem documento.",
            )

        now = datetime.now(UTC)
        proof = CaseProofLink(
            workspace_id=workspace_id,
            legal_demand_id=demand_id,
            fact_id=fact.id,
            document_id=document.id if document else None,
            force=payload.force,
            status=derived_status,
            notes=payload.notes,
            requested_at=now if derived_status == ProofStatus.solicitada else None,
            obtained_at=now if derived_status == ProofStatus.tem else None,
            created_by_user_id=actor_user_id,
        )
        self.db.add(proof)
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, actor_user_id)
                    if actor_user_id
                    else "Sistema — Estratégia do caso"
                ),
                event_type="case_proof_link_added",
                entity_type=CASE_STRATEGY_AUDIT_ENTITY_TYPE,
                entity_id=proof.id,
                correlation_id=str(demand_id),
                summary=f"Prova vinculada ao fato {fact.id} — status={derived_status.value}.",
                after_data={
                    "legalDemandId": str(demand_id),
                    "factId": str(fact.id),
                    "documentId": str(document.id) if document else None,
                    "force": payload.force.value,
                    "status": derived_status.value,
                },
                occurred_at=now,
            )
        )
        await self.db.flush()

        await self._sync_gaps(workspace_id, demand)
        return proof

    # ── Pedido ───────────────────────────────────────────────────────────

    async def add_pleading(
        self,
        workspace_id: UUID,
        demand_id: UUID,
        payload: CasePleadingCreate,
        *,
        actor_user_id: Optional[UUID] = None,
    ) -> CasePleading:
        await self._require_demand(workspace_id, demand_id)

        supporting_fact_ids = list(dict.fromkeys(payload.supporting_fact_ids))  # dedup, preserva ordem
        for fact_id in supporting_fact_ids:
            await self._require_fact(workspace_id, demand_id, fact_id)

        pleading = CasePleading(
            workspace_id=workspace_id,
            legal_demand_id=demand_id,
            request_text=payload.request_text.strip(),
            legal_basis=payload.legal_basis,
            supporting_fact_ids=[str(fact_id) for fact_id in supporting_fact_ids],
            created_by_user_id=actor_user_id,
        )
        self.db.add(pleading)
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, actor_user_id)
                    if actor_user_id
                    else "Sistema — Estratégia do caso"
                ),
                event_type="case_pleading_added",
                entity_type=CASE_STRATEGY_AUDIT_ENTITY_TYPE,
                entity_id=pleading.id,
                correlation_id=str(demand_id),
                summary=(
                    f"Pedido adicionado à estratégia — {len(supporting_fact_ids)} fato(s) de sustentação."
                ),
                after_data={
                    "legalDemandId": str(demand_id),
                    "supportingFactIds": [str(f) for f in supporting_fact_ids],
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return pleading

    # ── Lacunas — abertura idempotente na Central de Exceções ───────────

    async def _sync_gaps(self, workspace_id: UUID, demand: LegalDemand) -> None:
        """Reavalia fatos controvertidos sem prova 'tem' e abre (idempotente)
        a pendência correspondente. NUNCA fecha uma exceção já aberta —
        isso é decisão humana (ver docstring do módulo)."""
        facts = list(
            await self.db.scalars(
                select(CaseFact).where(
                    CaseFact.legal_demand_id == demand.id,
                    CaseFact.workspace_id == workspace_id,
                    CaseFact.is_controverted.is_(True),
                )
            )
        )
        if not facts:
            return

        proved_fact_ids = set(
            await self.db.scalars(
                select(CaseProofLink.fact_id).where(
                    CaseProofLink.legal_demand_id == demand.id,
                    CaseProofLink.workspace_id == workspace_id,
                    CaseProofLink.status == ProofStatus.tem,
                )
            )
        )

        exceptions = ExceptionCenterService(self.db, workspace_id)
        documents_required_changed = False
        documents_required = list(demand.documents_required)

        for fact in facts:
            if fact.id in proved_fact_ids:
                continue

            label = _fact_document_requirement_label(fact)
            if label not in documents_required:
                documents_required.append(label)
                documents_required_changed = True

            await exceptions.open_exception(
                exception_kind=ExceptionKind.documento_indispensavel_ausente,
                origin=_GAP_EXCEPTION_ORIGIN,
                dedupe_key=_fact_gap_dedupe_key(fact.id),
                title=f"Fato controvertido sem prova — demanda {demand.id}",
                description=(
                    "A matriz estratégia fato-prova-pedido tem um fato controvertido "
                    f"sem nenhuma prova em status 'tem': {fact.description[:300]}"
                ),
                priority=ApprovalPriority.alta,
                matter_id=demand.matter_id,
                payload={
                    "legalDemandId": str(demand.id),
                    "factId": str(fact.id),
                    "factDescription": fact.description,
                },
            )

        if documents_required_changed:
            demand.documents_required = documents_required
            await self.db.flush()

    # ── Leitura da matriz (nunca abre exceção — só lê) ──────────────────

    async def get_matrix(self, workspace_id: UUID, demand_id: UUID) -> CaseStrategyMatrix:
        await self._require_demand(workspace_id, demand_id)

        facts = list(
            await self.db.scalars(
                select(CaseFact)
                .where(
                    CaseFact.legal_demand_id == demand_id,
                    CaseFact.workspace_id == workspace_id,
                )
                .order_by(CaseFact.created_at.asc())
            )
        )
        proof_links = list(
            await self.db.scalars(
                select(CaseProofLink)
                .where(
                    CaseProofLink.legal_demand_id == demand_id,
                    CaseProofLink.workspace_id == workspace_id,
                )
                .order_by(CaseProofLink.created_at.asc())
            )
        )
        pleadings = list(
            await self.db.scalars(
                select(CasePleading)
                .where(
                    CasePleading.legal_demand_id == demand_id,
                    CasePleading.workspace_id == workspace_id,
                )
                .order_by(CasePleading.created_at.asc())
            )
        )

        proved_fact_ids = {
            link.fact_id for link in proof_links if link.status == ProofStatus.tem
        }

        gaps: list[CaseStrategyGapResult] = []

        open_gap_exceptions = {
            item.payload.get("factId"): item.id
            for item in await self.db.scalars(
                select(LegalException).where(
                    LegalException.workspace_id == workspace_id,
                    LegalException.exception_kind
                    == ExceptionKind.documento_indispensavel_ausente,
                    LegalException.origin == _GAP_EXCEPTION_ORIGIN,
                )
            )
            if item.payload
        }

        for fact in facts:
            if fact.is_controverted and fact.id not in proved_fact_ids:
                gaps.append(
                    CaseStrategyGapResult(
                        kind="fato_sem_prova",
                        fact_id=fact.id,
                        pleading_id=None,
                        description=(
                            f"Fato controvertido sem prova 'tem': {fact.description[:200]}"
                        ),
                        exception_id=open_gap_exceptions.get(str(fact.id)),
                    )
                )

        for pleading in pleadings:
            if not pleading.supporting_fact_ids:
                gaps.append(
                    CaseStrategyGapResult(
                        kind="pedido_sem_fato",
                        fact_id=None,
                        pleading_id=pleading.id,
                        description=(
                            f"Pedido sem fato de sustentação: {pleading.request_text[:200]}"
                        ),
                        exception_id=None,
                    )
                )

        return CaseStrategyMatrix(
            legal_demand_id=demand_id,
            facts=facts,
            proof_links=proof_links,
            pleadings=pleadings,
            gaps=gaps,
        )
