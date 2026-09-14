"""
Service: ContractBillingService (F11 — Contrato → nós financeiros com aprovação)
Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção F11.

O sistema lê o contrato de honorários do cliente, extrai um plano de
cobrança estruturado via IA (gateway existente — `app/ai/factory.get_model_gateway`)
e persiste como `ContractBillingProposal` (status=draft). A proposta é
SEMPRE draft — nada vira `FinanceEntry`/`FinanceRecurrence`/`FeeArrangement`
na extração. Valores extraídos por IA são PROPOSTA; qualquer item com
confiança baixa ou campo faltante nasce com `needs_review=True`.

Fail-closed: com `KLM_MODEL_APIS_ENABLED` fechado, `ModelGateway.generate`
recusa a chamada (`ModelAPIDisabledError`) — não fabricamos plano nenhum.
O extractor é injetável (`extractor` no construtor, mesmo molde de
`TranscriptionService.gateway`) para permitir testes 100% determinísticos
sem tocar o gateway.

Aprovação materializa EXACTLY-ONCE via `ApprovalService.resolve` (a mesma
CAS pending->approved de F9): uma segunda tentativa de aprovar recebe 409
antes de qualquer materialização. Rejeição nunca materializa nada.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalDecision, ApprovalQueueItem
from app.models.contract_billing import ContractBillingProposal, ContractBillingProposalStatus
from app.models.fee_arrangement import FeeArrangementKind
from app.models.finance import FinanceEntry, FinanceEntryType
from app.models.finance_charge import FinanceCharge
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.schemas.approval import ApprovalCreate, ApprovalResolveRequest
from app.schemas.contract_billing import ContractBillingItem, ContractBillingItemKind
from app.schemas.fee_arrangement import FeeArrangementCreate
from app.schemas.finance_charge import FinanceChargeCreate
from app.schemas.finance_recurrence import FinanceRecurrenceCreate
from app.services.approval_service import ApprovalService
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.fee_service import FeeService
from app.services.finance_charge_service import FinanceChargeService
from app.services.finance_recurrence_service import FinanceRecurrenceService

CONTRACT_BILLING_APPROVAL_TYPE = "contract_billing_proposal"
CONTRACT_BILLING_AUDIT_ENTITY_TYPE = "contract_billing_proposal"

# Abaixo desta confiança o item nasce needs_review=True mesmo com todos os
# campos obrigatórios presentes (a IA "acha" o valor, o humano confirma).
LOW_CONFIDENCE_THRESHOLD = Decimal("0.7")

_INSTALLMENT_KINDS = frozenset({ContractBillingItemKind.entrada, ContractBillingItemKind.parcela})

# Callable injetável que recebe o texto do contrato e devolve o payload bruto
# {"items": [...], "model": "..."} — em produção resolve para o gateway de IA;
# em teste, um fake determinístico (contrato: KLM_MODEL_APIS_ENABLED fechado
# → o teste injeta este extractor, nunca toca rede).
ContractExtractor = Callable[[str], Awaitable[dict[str, Any]]]

_EXTRACTION_SYSTEM_PROMPT = (
    "Você extrai o plano de cobrança de um contrato de honorários advocatícios "
    "brasileiro. Responda apenas o JSON solicitado, com um item por lançamento "
    "de cobrança identificado. kind deve ser um de: fixo, entrada, parcela, "
    "mensalidade, exito, por_hora. Nunca infira valores que não estejam "
    "explícitos no texto — deixe o campo ausente e confidence baixo nesse caso."
)

_BILLING_PLAN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["fixo", "entrada", "parcela", "mensalidade", "exito", "por_hora"],
                    },
                    "valor": {"type": ["number", "null"]},
                    "percentual": {"type": ["number", "null"]},
                    "vencimento": {"type": ["string", "null"]},
                    "ciclo": {"type": ["string", "null"]},
                    "descricao": {"type": ["string", "null"]},
                    "confidence": {"type": ["number", "null"]},
                },
                "required": ["kind"],
            },
        },
    },
    "required": ["items"],
}


class ContractBillingProposalNotFoundError(HTTPException):
    def __init__(self, detail: str = "Proposta de cobrança não encontrada.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ContractBillingProposalConflictError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


class ContractBillingService:
    def __init__(self, db: AsyncSession, extractor: Optional[ContractExtractor] = None) -> None:
        self.db = db
        self._extractor = extractor

    # ── Leitura auxiliar ─────────────────────────────────────────────────

    async def _get_matter(self, workspace_id: UUID, matter_id: UUID) -> Matter:
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

    async def _get_proposal(self, workspace_id: UUID, proposal_id: UUID) -> ContractBillingProposal:
        proposal = await self.db.scalar(
            select(ContractBillingProposal).where(
                ContractBillingProposal.id == proposal_id,
                ContractBillingProposal.workspace_id == workspace_id,
            )
        )
        if proposal is None:
            raise ContractBillingProposalNotFoundError()
        return proposal

    async def _get_proposal_by_approval(
        self, workspace_id: UUID, approval_id: UUID
    ) -> ContractBillingProposal:
        proposal = await self.db.scalar(
            select(ContractBillingProposal).where(
                ContractBillingProposal.approval_id == approval_id,
                ContractBillingProposal.workspace_id == workspace_id,
            )
        )
        if proposal is None:
            raise ContractBillingProposalNotFoundError(
                "Nenhuma proposta de cobrança associada a esta aprovação."
            )
        return proposal

    async def get_proposal(self, workspace_id: UUID, proposal_id: UUID) -> ContractBillingProposal:
        return await self._get_proposal(workspace_id, proposal_id)

    # ── Extração (sempre draft — nunca cria lançamento) ─────────────────

    async def _call_extractor(self, workspace_id: UUID, contract_text: str) -> dict[str, Any]:
        if self._extractor is not None:
            result = await self._extractor(contract_text)
            return result if isinstance(result, dict) else {}
        return await self._extract_via_gateway(workspace_id, contract_text)

    async def _extract_via_gateway(self, workspace_id: UUID, contract_text: str) -> dict[str, Any]:
        # Chamado como os demais agentes (ver TranscriptionService,
        # ReconciliationService._suggest_category_via_ai): fail-closed —
        # com o gate fechado, `gateway.generate` levanta `ModelAPIDisabledError`
        # e a extração não prossegue (nada é fabricado localmente).
        from app.ai.base import AIMessage
        from app.ai.factory import get_model_gateway
        from app.ai.models import ModelAttribution, ModelRequest, TaskClass

        gateway = get_model_gateway()
        result = await gateway.generate(
            ModelRequest(
                messages=[
                    AIMessage("system", _EXTRACTION_SYSTEM_PROMPT),
                    AIMessage("user", contract_text),
                ],
                workspace_id=str(workspace_id),
                agent_name="contract_billing_extractor",
                task_class=TaskClass.MEDIUM,
                response_schema=_BILLING_PLAN_RESPONSE_SCHEMA,
                max_output_tokens=2048,
                contains_sensitive_data=True,
                allow_response_cache=False,
                attribution=ModelAttribution(
                    operation="contract_billing.extract",
                    source_refs=(str(workspace_id),),
                ),
            )
        )
        data = dict(result.structured_data or {})
        data.setdefault("model", result.model_used)
        return data

    @staticmethod
    def _required_fields_present(item: ContractBillingItem) -> bool:
        if item.kind in (ContractBillingItemKind.fixo, *_INSTALLMENT_KINDS):
            return item.valor is not None and item.valor > 0 and item.vencimento is not None
        if item.kind == ContractBillingItemKind.mensalidade:
            return item.valor is not None and item.valor > 0
        if item.kind == ContractBillingItemKind.exito:
            return item.percentual is not None and item.percentual > 0
        if item.kind == ContractBillingItemKind.por_hora:
            return item.valor is not None and item.valor > 0
        return False

    def _normalize_item(self, raw: dict[str, Any]) -> Optional[ContractBillingItem]:
        try:
            item = ContractBillingItem.model_validate(raw)
        except ValidationError:
            # kind desconhecido/payload malformado: nunca fabricamos um item —
            # o chamador registra a rejeição em `notes` e o revisor humano decide.
            return None
        needs_review = not self._required_fields_present(item)
        if item.confidence is not None and item.confidence < LOW_CONFIDENCE_THRESHOLD:
            needs_review = True
        item.needs_review = needs_review
        return item

    async def extract_billing_plan(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        contract_text: str,
        *,
        source_document_name: Optional[str] = None,
        requested_by: Optional[UUID] = None,
    ) -> ContractBillingProposal:
        matter = await self._get_matter(workspace_id, matter_id)
        raw = await self._call_extractor(workspace_id, contract_text)
        raw_items = raw.get("items") or []

        items: list[dict[str, Any]] = []
        dropped: list[str] = []
        for raw_item in raw_items:
            normalized = self._normalize_item(raw_item if isinstance(raw_item, dict) else {})
            if normalized is None:
                dropped.append(str(raw_item)[:200])
                continue
            items.append(normalized.model_dump(mode="json"))

        proposal = ContractBillingProposal(
            workspace_id=workspace_id,
            matter_id=matter.id,
            source_document_name=source_document_name,
            items=items,
            status=ContractBillingProposalStatus.draft,
            extraction_model=str(raw.get("model") or "") or None,
            notes=(
                "Itens ignorados na extração (kind inválido/payload malformado): "
                + "; ".join(dropped)
            )
            if dropped
            else None,
        )
        self.db.add(proposal)
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=requested_by,
                actor_type="human" if requested_by else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, requested_by)
                    if requested_by
                    else "Sistema — Extração de plano de cobrança (F11)"
                ),
                event_type="contract_billing_proposal_extracted",
                entity_type=CONTRACT_BILLING_AUDIT_ENTITY_TYPE,
                entity_id=proposal.id,
                correlation_id=str(matter.id),
                summary=f"Plano de cobrança extraído do contrato — {len(items)} item(ns) (draft).",
                after_data={"matterId": str(matter.id), "itemCount": len(items)},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return proposal

    # ── Submissão para aprovação humana ─────────────────────────────────

    @staticmethod
    def _describe_items(items: list[dict[str, Any]]) -> str:
        parts = []
        for item in items:
            kind = item.get("kind", "?")
            valor = item.get("valor")
            percentual = item.get("percentual")
            flag = " (revisar)" if item.get("needs_review") else ""
            if percentual is not None:
                parts.append(f"{kind}: {percentual}%{flag}")
            else:
                parts.append(f"{kind}: R$ {valor}{flag}")
        return "; ".join(parts) or "Sem itens."

    async def submit_for_approval(
        self,
        workspace_id: UUID,
        proposal_id: UUID,
        *,
        requested_by: Optional[UUID] = None,
    ) -> ApprovalQueueItem:
        proposal = await self._get_proposal(workspace_id, proposal_id)
        if proposal.status != ContractBillingProposalStatus.draft:
            raise ContractBillingProposalConflictError(
                "Só propostas em draft podem ser submetidas à aprovação."
            )
        if not proposal.items:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Proposta sem itens extraídos — nada para submeter.",
            )

        intent_key = f"contract_billing_proposal:{proposal.id}"
        approval = await ApprovalService(self.db, workspace_id).create(
            ApprovalCreate(
                matter_id=proposal.matter_id,
                approval_type=CONTRACT_BILLING_APPROVAL_TYPE,
                intent_key=intent_key,
                title=f"Aprovar plano de cobrança extraído — {len(proposal.items)} item(ns)",
                description=self._describe_items(proposal.items),
                requested_by=requested_by,
            )
        )
        proposal.status = ContractBillingProposalStatus.pending_approval
        proposal.approval_id = approval.id
        await self.db.flush()
        return approval

    # ── Materialização (só aqui nascem lançamentos) ─────────────────────

    def _assert_materializable(self, item: ContractBillingItem, index: int) -> None:
        if self._required_fields_present(item):
            return
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Item {index} (kind={item.kind.value}) não pode ser materializado — "
                "campo obrigatório ausente (valor/percentual/vencimento). Rejeite e "
                "reextraia o plano de cobrança."
            ),
        )

    async def _materialize_fixed(
        self,
        workspace_id: UUID,
        matter: Matter,
        proposal: ContractBillingProposal,
        item: ContractBillingItem,
    ) -> tuple[FinanceEntry, FinanceCharge]:
        due = item.vencimento or date.today()
        entry = FinanceEntry(
            workspace_id=workspace_id,
            matter_id=matter.id,
            title=item.descricao or f"Honorários — {matter.demand_type}",
            entry_type=FinanceEntryType.receivable,
            amount=_quantize(item.valor),
            due_date=datetime.combine(due, datetime.min.time(), tzinfo=timezone.utc),
            entry_metadata={
                "contractBillingProposalId": str(proposal.id),
                "kind": item.kind.value,
            },
        )
        self.db.add(entry)
        await self.db.flush()
        charge = await FinanceChargeService(self.db).create_charge_for_entry(
            workspace_id, entry.id, FinanceChargeCreate(due_date=due)
        )
        return entry, charge

    async def _materialize_installments(
        self,
        workspace_id: UUID,
        matter: Matter,
        proposal: ContractBillingProposal,
        group: list[ContractBillingItem],
    ) -> tuple[str, list[FinanceEntry]]:
        total_value = _quantize(sum((item.valor for item in group), start=Decimal("0")))
        due_dates = [item.vencimento for item in group if item.vencimento is not None]
        first_due = min(due_dates) if due_dates else date.today()

        parent_entry = FinanceEntry(
            workspace_id=workspace_id,
            matter_id=matter.id,
            title=f"Honorários — {matter.demand_type}",
            entry_type=FinanceEntryType.receivable,
            amount=total_value,
            due_date=datetime.combine(first_due, datetime.min.time(), tzinfo=timezone.utc),
            entry_metadata={
                "contractBillingProposalId": str(proposal.id),
                "kind": "entrada_parcela",
            },
        )
        self.db.add(parent_entry)
        await self.db.flush()

        return await FinanceChargeService(self.db).create_installment_series(
            workspace_id,
            parent_entry.id,
            len(group),
            total_value=total_value,
            first_due_date=first_due,
        )

    async def _materialize_recurrence(
        self,
        workspace_id: UUID,
        matter: Matter,
        proposal: ContractBillingProposal,
        item: ContractBillingItem,
    ):
        return await FinanceRecurrenceService(self.db).create_recurrence(
            workspace_id,
            FinanceRecurrenceCreate(
                client_id=matter.client_id,
                matter_id=matter.id,
                entry_title=item.descricao or f"Honorários — {matter.demand_type}",
                entry_value=_quantize(item.valor),
                entry_type=FinanceEntryType.receivable,
                next_run=item.vencimento or date.today(),
            ),
        )

    async def _materialize_arrangement(
        self,
        workspace_id: UUID,
        matter: Matter,
        item: ContractBillingItem,
        kind: FeeArrangementKind,
    ):
        # Reaproveita FeeService — nunca duplica a lógica de arranjo de F9.
        return await FeeService(self.db).create_arrangement(
            workspace_id,
            FeeArrangementCreate(
                matter_id=matter.id,
                kind=kind,
                percent=item.percentual if kind == FeeArrangementKind.exito else None,
                hourly_rate=item.valor if kind == FeeArrangementKind.por_hora else None,
                notes=item.descricao,
            ),
        )

    async def approve_proposal(
        self,
        workspace_id: UUID,
        approval_id: UUID,
        *,
        resolved_by: Optional[UUID] = None,
    ) -> dict[str, Any]:
        proposal = await self._get_proposal_by_approval(workspace_id, approval_id)
        items = [ContractBillingItem.model_validate(raw) for raw in proposal.items]
        for index, item in enumerate(items):
            self._assert_materializable(item, index)

        # EXACTLY-ONCE: resolve primeiro. Uma segunda aprovação recebe 409 do
        # próprio ApprovalService aqui — antes de qualquer materialização —
        # o mesmo padrão de FeeService.approve_success_fee (F9).
        await ApprovalService(self.db, workspace_id).resolve(
            approval_id,
            ApprovalResolveRequest(status=ApprovalDecision.approved, resolved_by=resolved_by),
        )

        matter = await self._get_matter(workspace_id, proposal.matter_id)
        materialized: dict[str, Any] = {
            "fixedEntries": [],
            "installments": None,
            "recurrences": [],
            "arrangements": [],
        }

        installment_group = [item for item in items if item.kind in _INSTALLMENT_KINDS]
        for item in items:
            if item.kind == ContractBillingItemKind.fixo:
                entry, charge = await self._materialize_fixed(workspace_id, matter, proposal, item)
                materialized["fixedEntries"].append((entry, charge))
            elif item.kind == ContractBillingItemKind.mensalidade:
                recurrence = await self._materialize_recurrence(
                    workspace_id, matter, proposal, item
                )
                materialized["recurrences"].append(recurrence)
            elif item.kind == ContractBillingItemKind.exito:
                arrangement = await self._materialize_arrangement(
                    workspace_id, matter, item, FeeArrangementKind.exito
                )
                materialized["arrangements"].append(arrangement)
            elif item.kind == ContractBillingItemKind.por_hora:
                arrangement = await self._materialize_arrangement(
                    workspace_id, matter, item, FeeArrangementKind.por_hora
                )
                materialized["arrangements"].append(arrangement)
            # entrada/parcela são tratados em lote (installment_group) abaixo.

        if installment_group:
            installment_ref, entries = await self._materialize_installments(
                workspace_id, matter, proposal, installment_group
            )
            materialized["installments"] = (installment_ref, entries)

        proposal.status = ContractBillingProposalStatus.approved
        proposal.decided_at = datetime.now(UTC)
        proposal.decided_by = resolved_by
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=resolved_by,
                actor_type="human" if resolved_by else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, resolved_by)
                    if resolved_by
                    else "Sistema — Aprovação de plano de cobrança (F11)"
                ),
                event_type="contract_billing_proposal_approved",
                entity_type=CONTRACT_BILLING_AUDIT_ENTITY_TYPE,
                entity_id=proposal.id,
                correlation_id=str(matter.id),
                summary=f"Plano de cobrança aprovado e materializado — {len(items)} item(ns).",
                after_data={
                    "fixedCount": len(materialized["fixedEntries"]),
                    "hasInstallments": materialized["installments"] is not None,
                    "recurrenceCount": len(materialized["recurrences"]),
                    "arrangementCount": len(materialized["arrangements"]),
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return materialized

    async def reject_proposal(
        self,
        workspace_id: UUID,
        approval_id: UUID,
        *,
        resolved_by: Optional[UUID] = None,
        resolution_reason: Optional[str] = None,
    ) -> ContractBillingProposal:
        proposal = await self._get_proposal_by_approval(workspace_id, approval_id)

        # Exactly-once aqui também: segunda tentativa (aprovar ou rejeitar)
        # recebe 409 do ApprovalService — rejeição nunca materializa nada.
        await ApprovalService(self.db, workspace_id).resolve(
            approval_id,
            ApprovalResolveRequest(
                status=ApprovalDecision.rejected,
                resolved_by=resolved_by,
                resolution_reason=resolution_reason,
            ),
        )
        proposal.status = ContractBillingProposalStatus.rejected
        proposal.decided_at = datetime.now(UTC)
        proposal.decided_by = resolved_by
        await self.db.flush()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=resolved_by,
                actor_type="human" if resolved_by else "system",
                actor_label=(
                    await resolve_human_actor_label(self.db, resolved_by)
                    if resolved_by
                    else "Sistema — Rejeição de plano de cobrança (F11)"
                ),
                event_type="contract_billing_proposal_rejected",
                entity_type=CONTRACT_BILLING_AUDIT_ENTITY_TYPE,
                entity_id=proposal.id,
                correlation_id=str(proposal.matter_id),
                summary="Plano de cobrança rejeitado — nada materializado.",
                after_data={"resolutionReason": resolution_reason},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return proposal
