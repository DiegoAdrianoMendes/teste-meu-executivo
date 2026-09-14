"""
Service: LegalBatchService — Operação Massificada / Lotes
(Onda 8 — Escala e gestão, Plano Mestre §7).

Regras que este serviço NUNCA quebra:

  1. `LegalBatch.status` é sempre um CACHE recomputado a partir da
     contagem real de `LegalBatchItem.state` (`_recompute_status`) — nunca
     incrementado paralelamente. O painel (`get_panel`) sempre conta os
     itens de novo, nunca lê um contador potencialmente desatualizado.
  2. Processar um lote é processar item a item, com captura de exceção
     POR ITEM (`process_all`) — a falha de um item nunca aborta os
     demais, e o item que falhou fica em `bloqueado` com `error_reason`
     preenchido, nunca escondido atrás do sucesso dos outros.
  3. Cada ação autônoma consulta `AutonomyPolicyService.evaluate`
     (Plano Mestre §14). Sem política casando o evento, o padrão é
     fail-closed: A4, sem preparo/execução autônoma, item vai para
     `aguardando_humano`. Mesmo com política em A2/A3, um item de
     `decision_kind` em `PROTECTED_DECISION_KINDS` (tese/prazo_fatal/
     texto_final/protocolo) NUNCA avança sozinho — sempre aguardando_humano.
  4. `approve_all` NUNCA aprova um item de `PROTECTED_DECISION_KINDS` —
     esses só saem de `aguardando_humano` via `approve_item`, item a item,
     sempre humano. `approve_all` reporta explicitamente os pulados.
  5. `rollback_item` nunca apaga a trilha: cada transição é um APPEND em
     `state_history` (nunca sobrescrita), e o rollback também é um append
     (marca de onde saiu e para onde voltou) — a pilha de estados
     anteriores é reconstruída lendo a trilha, nunca um campo solto que
     alguém possa esquecer de manter em sincronia.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_batch import (
    PROTECTED_DECISION_KINDS,
    LegalBatch,
    LegalBatchAction,
    LegalBatchCriterion,
    LegalBatchItem,
    LegalBatchItemState,
    LegalBatchStatus,
)
from app.models.legal_demand import LegalDemand
from app.schemas.legal_batch import (
    LegalBatchCreate,
    LegalBatchCreateFromContext,
    LegalBatchItemSeed,
)
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.autonomy_policy_service import AutonomyPolicyService

LEGAL_BATCH_ITEM_EVENT_TYPE = "legal_batch_item_process"
LOW_CONFIDENCE_THRESHOLD = 0.6

# Estado alcançado por uma ação autônoma bem-sucedida — sempre um estado
# INTERMEDIÁRIO (nunca `concluido` direto: concluir é sempre uma decisão
# humana, via `approve_item`/`approve_all`, mesmo para ações não críticas).
_ACTION_SUCCESS_STATE: dict[LegalBatchAction, str] = {
    LegalBatchAction.classificar: LegalBatchItemState.classificado.value,
    LegalBatchAction.agrupar: LegalBatchItemState.classificado.value,
    LegalBatchAction.vincular: LegalBatchItemState.vinculado.value,
    LegalBatchAction.sugerir_tarefa: LegalBatchItemState.revisado.value,
    LegalBatchAction.preencher_variavel: LegalBatchItemState.classificado.value,
    LegalBatchAction.gerar_rascunho: LegalBatchItemState.rascunho.value,
    LegalBatchAction.revisar: LegalBatchItemState.revisado.value,
    LegalBatchAction.pedir_documento: LegalBatchItemState.aguardando_humano.value,
    LegalBatchAction.organizar: LegalBatchItemState.classificado.value,
}

# Estados a partir dos quais um item pode ser aprovado (individualmente ou
# em `approve_all`) — bloqueado/baixa_confianca/divergente exigem
# investigação/rollback humano primeiro, não uma simples aprovação.
_APPROVABLE_STATES = frozenset(
    {
        LegalBatchItemState.aguardando_humano.value,
        LegalBatchItemState.classificado.value,
        LegalBatchItemState.vinculado.value,
        LegalBatchItemState.rascunho.value,
        LegalBatchItemState.revisado.value,
    }
)

_BLOCKING_STATES = frozenset(
    {
        LegalBatchItemState.bloqueado.value,
        LegalBatchItemState.baixa_confianca.value,
        LegalBatchItemState.divergente.value,
        LegalBatchItemState.aguardando_humano.value,
    }
)


class LegalBatchNotFoundError(HTTPException):
    def __init__(self, detail: str = "Lote não encontrado neste workspace.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class LegalBatchItemNotFoundError(HTTPException):
    def __init__(self, detail: str = "Item do lote não encontrado.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class LegalBatchItemNotApprovableError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class LegalBatchItemProtectedDecisionError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class LegalBatchRollbackUnavailableError(HTTPException):
    def __init__(
        self, detail: str = "Item não tem estado anterior para reverter."
    ) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


@dataclass(frozen=True)
class LegalBatchPanelResult:
    batch_id: UUID
    status: str
    total_items: int
    counts: dict[str, int]
    estimated_cost_total: float
    estimated_time_saved_minutes_total: int


@dataclass(frozen=True)
class LegalBatchProcessAllResult:
    batch_id: UUID
    processed_item_ids: list[UUID]
    succeeded_item_ids: list[UUID]
    failed_item_ids: list[UUID]
    panel: LegalBatchPanelResult


@dataclass(frozen=True)
class LegalBatchApproveAllResultData:
    batch_id: UUID
    approved_item_ids: list[UUID]
    skipped_protected_item_ids: list[UUID]
    skipped_not_pending_item_ids: list[UUID]


def _now() -> datetime:
    return datetime.now(UTC)


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


class LegalBatchService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── leitura auxiliar (sempre escopada por workspace) ────────────────

    async def _require_batch(self, workspace_id: UUID, batch_id: UUID) -> LegalBatch:
        batch = await self.db.scalar(
            select(LegalBatch).where(
                LegalBatch.id == batch_id, LegalBatch.workspace_id == workspace_id
            )
        )
        if batch is None:
            raise LegalBatchNotFoundError()
        return batch

    async def _require_item(
        self, workspace_id: UUID, batch_id: UUID, item_id: UUID
    ) -> LegalBatchItem:
        item = await self.db.scalar(
            select(LegalBatchItem).where(
                LegalBatchItem.id == item_id,
                LegalBatchItem.batch_id == batch_id,
                LegalBatchItem.workspace_id == workspace_id,
            )
        )
        if item is None:
            raise LegalBatchItemNotFoundError()
        return item

    # ── criação ──────────────────────────────────────────────────────────

    async def create_batch(
        self, *, workspace_id: UUID, user_id: UUID, payload: LegalBatchCreate
    ) -> LegalBatch:
        if payload.external_ref:
            existing = await self.db.scalar(
                select(LegalBatch).where(
                    LegalBatch.workspace_id == workspace_id,
                    LegalBatch.external_ref == payload.external_ref,
                )
            )
            if existing is not None:
                return existing

        batch = LegalBatch(
            workspace_id=workspace_id,
            name=payload.name,
            external_ref=payload.external_ref,
            criterion_type=payload.criterion_type,
            criterion_value=payload.criterion_value or {},
            playbook_ref=payload.playbook_ref,
            default_action=payload.default_action,
            status=LegalBatchStatus.aberto.value,
            created_by_user_id=user_id,
        )
        self.db.add(batch)
        await self.db.flush()

        seen_refs: set[str] = set()
        for seed in payload.items:
            if seed.item_ref and seed.item_ref in seen_refs:
                continue
            if seed.item_ref:
                seen_refs.add(seed.item_ref)

            if seed.legal_demand_id is not None:
                demand = await self.db.scalar(
                    select(LegalDemand).where(
                        LegalDemand.id == seed.legal_demand_id,
                        LegalDemand.workspace_id == workspace_id,
                    )
                )
                if demand is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=(
                            f"Demanda jurídica {seed.legal_demand_id} não encontrada "
                            "neste workspace (item do lote)."
                        ),
                    )

            item = LegalBatchItem(
                workspace_id=workspace_id,
                batch_id=batch.id,
                item_ref=seed.item_ref,
                legal_demand_id=seed.legal_demand_id,
                action=seed.action,
                decision_kind=seed.decision_kind,
                state=LegalBatchItemState.pendente.value,
                state_history=[],
                input_payload=seed.input_payload or {},
                result_payload={},
                estimated_cost=seed.estimated_cost,
                estimated_time_saved_minutes=seed.estimated_time_saved_minutes,
                responsible_user_id=seed.responsible_user_id,
            )
            self.db.add(item)

        await self.db.flush()
        await self._recompute_status(batch)

        actor_label = await resolve_human_actor_label(self.db, user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="legal_batch_created",
                entity_type="legal_batch",
                entity_id=batch.id,
                correlation_id=f"legal-batch:{batch.id}",
                summary=(
                    f"Lote '{batch.name}' criado por critério "
                    f"{_enum_value(batch.criterion_type)} com {len(payload.items)} item(ns)."
                ),
                before_data=None,
                after_data={"batchId": str(batch.id), "itemCount": len(payload.items)},
                occurred_at=_now(),
            )
        )
        await self.db.flush()
        return batch

    # ── criação por contexto (cliente OU demanda) ───────────────────────

    async def create_batch_from_context(
        self, *, workspace_id: UUID, user_id: UUID, payload: LegalBatchCreateFromContext
    ) -> LegalBatch:
        """Recorte por contexto: um item por `LegalDemand` do cliente
        informado, ou um único item para a demanda informada. Delega para
        `create_batch` (mesma idempotência por `external_ref`, mesmo
        painel, mesma trilha de auditoria) — nunca duplica essa mecânica."""
        if payload.external_ref:
            existing = await self.db.scalar(
                select(LegalBatch).where(
                    LegalBatch.workspace_id == workspace_id,
                    LegalBatch.external_ref == payload.external_ref,
                )
            )
            if existing is not None:
                return existing

        if payload.legal_demand_id is not None:
            demand = await self.db.scalar(
                select(LegalDemand).where(
                    LegalDemand.id == payload.legal_demand_id,
                    LegalDemand.workspace_id == workspace_id,
                )
            )
            if demand is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"Demanda jurídica {payload.legal_demand_id} não encontrada "
                        "neste workspace."
                    ),
                )
            demands = [demand]
            criterion_value = {"legalDemandId": str(payload.legal_demand_id)}
        else:
            client = await self.db.scalar(
                select(Client).where(
                    Client.id == payload.client_id,
                    Client.workspace_id == workspace_id,
                )
            )
            if client is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Cliente {payload.client_id} não encontrado neste workspace.",
                )
            demands = list(
                (
                    await self.db.scalars(
                        select(LegalDemand)
                        .where(
                            LegalDemand.workspace_id == workspace_id,
                            LegalDemand.client_id == payload.client_id,
                        )
                        .order_by(LegalDemand.created_at.asc())
                    )
                ).all()
            )
            criterion_value = {"clientId": str(payload.client_id)}

        items = [
            LegalBatchItemSeed(
                item_ref=str(demand.id),
                legal_demand_id=demand.id,
                action=payload.action,
                decision_kind=payload.decision_kind,
                responsible_user_id=demand.responsible_user_id,
            )
            for demand in demands
        ]

        return await self.create_batch(
            workspace_id=workspace_id,
            user_id=user_id,
            payload=LegalBatchCreate(
                name=payload.name,
                external_ref=payload.external_ref,
                criterion_type=LegalBatchCriterion.cliente,
                criterion_value=criterion_value,
                playbook_ref=payload.playbook_ref,
                default_action=payload.action,
                items=items,
            ),
        )

    # ── leitura ──────────────────────────────────────────────────────────

    async def list_batches(self, workspace_id: UUID) -> list[LegalBatch]:
        rows = await self.db.scalars(
            select(LegalBatch)
            .where(LegalBatch.workspace_id == workspace_id)
            .order_by(LegalBatch.created_at.desc())
        )
        return list(rows.all())

    async def get_batch(self, workspace_id: UUID, batch_id: UUID) -> LegalBatch:
        return await self._require_batch(workspace_id, batch_id)

    async def list_items(
        self, workspace_id: UUID, batch_id: UUID, *, state: Optional[str] = None
    ) -> list[LegalBatchItem]:
        await self._require_batch(workspace_id, batch_id)
        stmt = select(LegalBatchItem).where(
            LegalBatchItem.workspace_id == workspace_id,
            LegalBatchItem.batch_id == batch_id,
        )
        if state:
            stmt = stmt.where(LegalBatchItem.state == state)
        rows = await self.db.scalars(stmt.order_by(LegalBatchItem.created_at))
        return list(rows.all())

    # ── painel (Plano Mestre §7 — total/concluídos/bloqueados/... ) ─────

    async def get_panel(self, workspace_id: UUID, batch_id: UUID) -> LegalBatchPanelResult:
        batch = await self._require_batch(workspace_id, batch_id)
        counts = await self._count_by_state(batch.id)
        totals = await self.db.execute(
            select(
                func.count(LegalBatchItem.id),
                func.coalesce(func.sum(LegalBatchItem.estimated_cost), 0),
                func.coalesce(func.sum(LegalBatchItem.estimated_time_saved_minutes), 0),
            ).where(LegalBatchItem.batch_id == batch.id)
        )
        total_items, cost_total, time_total = totals.one()
        return LegalBatchPanelResult(
            batch_id=batch.id,
            status=batch.status,
            total_items=int(total_items or 0),
            counts=counts,
            estimated_cost_total=float(cost_total or 0),
            estimated_time_saved_minutes_total=int(time_total or 0),
        )

    async def _count_by_state(self, batch_id: UUID) -> dict[str, int]:
        rows = await self.db.execute(
            select(LegalBatchItem.state, func.count(LegalBatchItem.id))
            .where(LegalBatchItem.batch_id == batch_id)
            .group_by(LegalBatchItem.state)
        )
        counts = {state.value: 0 for state in LegalBatchItemState}
        for state_value, count in rows.all():
            counts[state_value] = int(count)
        return counts

    async def _recompute_status(self, batch: LegalBatch) -> None:
        counts = await self._count_by_state(batch.id)
        total = sum(counts.values())
        blocking = sum(counts[s] for s in _BLOCKING_STATES)
        concluido = counts[LegalBatchItemState.concluido.value]
        in_progress = total - blocking - concluido - counts[LegalBatchItemState.pendente.value]

        if total == 0:
            new_status = LegalBatchStatus.aberto.value
        elif concluido == total:
            new_status = LegalBatchStatus.concluido.value
        elif blocking > 0:
            new_status = LegalBatchStatus.com_pendencias.value
        elif in_progress > 0:
            new_status = LegalBatchStatus.processando.value
        else:
            new_status = LegalBatchStatus.aberto.value

        if batch.status != new_status:
            batch.status = new_status
            await self.db.flush()

    # ── transição de estado (append-only na trilha) ─────────────────────

    def _transition(
        self, item: LegalBatchItem, new_state: str, user_id: Optional[UUID]
    ) -> None:
        history = list(item.state_history or [])
        history.append(
            {
                "action": "transition",
                "state": item.state,
                "at": _now().isoformat(),
                "byUserId": str(user_id) if user_id else None,
            }
        )
        item.state_history = history
        item.state = new_state

    @staticmethod
    def _previous_state_from_history(history: list[dict]) -> Optional[str]:
        skip = 0
        for entry in reversed(history or []):
            action = entry.get("action")
            if action == "rollback":
                skip += 1
                continue
            if action == "transition":
                if skip > 0:
                    skip -= 1
                    continue
                return entry.get("state")
        return None

    # ── processamento (autonomia + falha parcial visível) ───────────────

    async def process_item(
        self,
        *,
        workspace_id: UUID,
        batch_id: UUID,
        item_id: UUID,
        user_id: UUID,
        result_payload: Optional[dict] = None,
        confidence: Optional[float] = None,
        error_reason: Optional[str] = None,
    ) -> LegalBatchItem:
        batch = await self._require_batch(workspace_id, batch_id)
        item = await self._require_item(workspace_id, batch_id, item_id)

        if item.state != LegalBatchItemState.pendente.value:
            # Idempotente: reprocessar um item que já saiu de `pendente`
            # nunca duplica trabalho nem sobrescreve o resultado anterior.
            return item

        if error_reason:
            item.error_reason = error_reason
            self._transition(item, LegalBatchItemState.bloqueado.value, user_id)
            await self.db.flush()
            await self._recompute_status(batch)
            return item

        if item.action == LegalBatchAction.vincular:
            target_demand_id = (result_payload or {}).get("legalDemandId")
            if target_demand_id:
                demand = await self.db.scalar(
                    select(LegalDemand).where(
                        LegalDemand.id == target_demand_id,
                        LegalDemand.workspace_id == workspace_id,
                    )
                )
                if demand is None:
                    item.error_reason = (
                        "Demanda jurídica não encontrada neste workspace — "
                        "vínculo não pôde ser confirmado."
                    )
                    self._transition(item, LegalBatchItemState.bloqueado.value, user_id)
                    await self.db.flush()
                    await self._recompute_status(batch)
                    return item
                item.legal_demand_id = demand.id

        item.result_payload = result_payload or {}
        if confidence is not None:
            item.confidence = confidence

        if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
            self._transition(item, LegalBatchItemState.baixa_confianca.value, user_id)
            await self.db.flush()
            await self._recompute_status(batch)
            return item

        decision = await AutonomyPolicyService(self.db).evaluate(
            workspace_id=workspace_id,
            event_type=LEGAL_BATCH_ITEM_EVENT_TYPE,
            action=item.action.value,
        )
        item.autonomy_level_applied = decision.autonomy_level
        item.autonomy_policy_id = (
            UUID(decision.matched_policy_id) if decision.matched_policy_id else None
        )

        is_protected = item.decision_kind in PROTECTED_DECISION_KINDS
        can_advance = (decision.can_prepare or decision.can_execute_internal) and not is_protected

        if can_advance:
            item.requires_human_approval = False
            success_state = _ACTION_SUCCESS_STATE[item.action]
            self._transition(item, success_state, user_id)
        else:
            item.requires_human_approval = True
            self._transition(item, LegalBatchItemState.aguardando_humano.value, user_id)

        await self.db.flush()
        await self._recompute_status(batch)
        return item

    async def process_all(
        self,
        *,
        workspace_id: UUID,
        batch_id: UUID,
        user_id: UUID,
        items_input: Optional[dict[UUID, dict[str, Any]]] = None,
    ) -> LegalBatchProcessAllResult:
        """Processa todo item ainda `pendente` do lote. A falha de UM item
        NUNCA aborta o processamento dos demais — cada item é isolado por
        try/except, e o item que falhar fica `bloqueado` com
        `error_reason`, visível no painel (`get_panel`)."""
        batch = await self._require_batch(workspace_id, batch_id)
        pending_items = await self.db.scalars(
            select(LegalBatchItem).where(
                LegalBatchItem.batch_id == batch.id,
                LegalBatchItem.state == LegalBatchItemState.pendente.value,
            )
        )
        items_input = items_input or {}

        processed: list[UUID] = []
        succeeded: list[UUID] = []
        failed: list[UUID] = []

        for item in list(pending_items.all()):
            item_input = items_input.get(item.id, {})
            processed.append(item.id)
            try:
                result = await self.process_item(
                    workspace_id=workspace_id,
                    batch_id=batch_id,
                    item_id=item.id,
                    user_id=user_id,
                    result_payload=item_input.get("resultPayload"),
                    confidence=item_input.get("confidence"),
                    error_reason=item_input.get("errorReason"),
                )
                if result.state == LegalBatchItemState.bloqueado.value:
                    failed.append(item.id)
                else:
                    succeeded.append(item.id)
            except Exception as exc:  # noqa: BLE001 — isolamento intencional por item
                item.error_reason = str(exc)
                self._transition(item, LegalBatchItemState.bloqueado.value, user_id)
                await self.db.flush()
                failed.append(item.id)

        await self._recompute_status(batch)
        panel = await self.get_panel(workspace_id, batch_id)
        return LegalBatchProcessAllResult(
            batch_id=batch.id,
            processed_item_ids=processed,
            succeeded_item_ids=succeeded,
            failed_item_ids=failed,
            panel=panel,
        )

    # ── aprovação (item a item sempre disponível; em lote nunca para crítico) ──

    async def approve_item(
        self, *, workspace_id: UUID, batch_id: UUID, item_id: UUID, user_id: UUID
    ) -> LegalBatchItem:
        batch = await self._require_batch(workspace_id, batch_id)
        item = await self._require_item(workspace_id, batch_id, item_id)

        if item.state == LegalBatchItemState.concluido.value:
            return item  # idempotente
        if item.state not in _APPROVABLE_STATES:
            raise LegalBatchItemNotApprovableError(
                f"Item em estado '{item.state}' não pode ser aprovado diretamente "
                "— resolva o bloqueio ou reverta antes."
            )

        item.approved_by_user_id = user_id
        item.approved_at = _now()
        self._transition(item, LegalBatchItemState.concluido.value, user_id)
        await self.db.flush()
        await self._recompute_status(batch)

        actor_label = await resolve_human_actor_label(self.db, user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="legal_batch_item_approved",
                entity_type="legal_batch_item",
                entity_id=item.id,
                correlation_id=f"legal-batch-item:{item.id}:approved",
                summary=f"Item do lote {batch.id} aprovado (decisão: {item.decision_kind.value}).",
                before_data=None,
                after_data={"itemId": str(item.id), "decisionKind": item.decision_kind.value},
                occurred_at=_now(),
            )
        )
        await self.db.flush()
        return item

    async def approve_all(
        self, *, workspace_id: UUID, batch_id: UUID, user_id: UUID
    ) -> LegalBatchApproveAllResultData:
        """REGRA DURA (Plano Mestre §7): NUNCA aprova tese/prazo_fatal/
        texto_final/protocolo em lote — esses ficam em
        `skipped_protected_item_ids`, exigindo `approve_item` humano."""
        batch = await self._require_batch(workspace_id, batch_id)
        candidates = await self.db.scalars(
            select(LegalBatchItem).where(
                LegalBatchItem.batch_id == batch.id,
                LegalBatchItem.state.in_(_APPROVABLE_STATES),
            )
        )

        approved: list[UUID] = []
        skipped_protected: list[UUID] = []
        skipped_not_pending: list[UUID] = []

        for item in list(candidates.all()):
            if item.decision_kind in PROTECTED_DECISION_KINDS:
                skipped_protected.append(item.id)
                continue
            await self.approve_item(
                workspace_id=workspace_id, batch_id=batch_id, item_id=item.id, user_id=user_id
            )
            approved.append(item.id)

        await self._recompute_status(batch)
        return LegalBatchApproveAllResultData(
            batch_id=batch.id,
            approved_item_ids=approved,
            skipped_protected_item_ids=skipped_protected,
            skipped_not_pending_item_ids=skipped_not_pending,
        )

    # ── rollback (nunca destrutivo — trilha sempre em append) ───────────

    async def rollback_item(
        self,
        *,
        workspace_id: UUID,
        batch_id: UUID,
        item_id: UUID,
        user_id: UUID,
        reason: Optional[str] = None,
    ) -> LegalBatchItem:
        batch = await self._require_batch(workspace_id, batch_id)
        item = await self._require_item(workspace_id, batch_id, item_id)

        previous_state = self._previous_state_from_history(item.state_history)
        if previous_state is None:
            raise LegalBatchRollbackUnavailableError()

        was_concluido = item.state == LegalBatchItemState.concluido.value
        history = list(item.state_history or [])
        history.append(
            {
                "action": "rollback",
                "fromState": item.state,
                "toState": previous_state,
                "at": _now().isoformat(),
                "byUserId": str(user_id),
                "reason": reason,
            }
        )
        item.state_history = history
        item.state = previous_state
        if was_concluido:
            item.approved_by_user_id = None
            item.approved_at = None

        await self.db.flush()
        await self._recompute_status(batch)

        actor_label = await resolve_human_actor_label(self.db, user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="legal_batch_item_rolled_back",
                entity_type="legal_batch_item",
                entity_id=item.id,
                correlation_id=f"legal-batch-item:{item.id}:rollback:{_now().isoformat()}",
                summary=f"Item do lote {batch.id} revertido para '{previous_state}'."
                + (f" Motivo: {reason}." if reason else ""),
                before_data=None,
                after_data={"itemId": str(item.id), "toState": previous_state},
                occurred_at=_now(),
            )
        )
        await self.db.flush()
        return item
