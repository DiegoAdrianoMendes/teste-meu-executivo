"""Tarefas assíncronas do módulo financeiro (F1 — Tarefa C).

Processa os recibos de webhook do Asaas gravados por
`app/api/v1/endpoints/asaas_webhook.py`: mapeia evento → estado interno do
`FinanceCharge` (docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §4.3), dá baixa
na `FinanceEntry` quando o evento é `PAYMENT_RECEIVED`, registra auditoria
(`KlmAuditEvent`) e timeline do caso quando houver `matter_id`.

Idempotência: reprocessar o mesmo recibo (`event_id`) é sempre no-op — o
recibo já processado é pulado no início, e a própria transição de estado do
charge é monotônica (eventos "atrasados" nunca regridem um estado mais
avançado). A baixa da FinanceEntry reaproveita `FinanceService.update_entry`,
que só dispara o marco de funil quando o status efetivamente muda para
`paid`.
"""
from __future__ import annotations

import asyncio
import calendar
from datetime import UTC, date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from celery.exceptions import Reject
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.asaas_webhook_receipt import AsaasWebhookReceipt, AsaasWebhookReceiptStatus
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus
from app.models.finance_charge import FinanceCharge, FinanceChargeStatus
from app.models.finance_dunning import (
    FinanceDunningLog,
    FinanceDunningLogStatus,
    FinanceDunningStep,
)
from app.models.finance_recurrence import (
    FinanceRecurrence,
    FinanceRecurrenceCycle,
    FinanceRecurrenceKind,
    FinanceRecurrenceRun,
    FinanceRecurrenceStatus,
)
from app.models.legal_exception import ExceptionKind
from app.models.matter import Matter
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.whatsapp import WhatsAppContact
from app.models.workspace import Workspace
from app.payments.port import BillingType
from app.schemas.finance import FinanceEntryUpdate
from app.schemas.finance_charge import FinanceChargeCreate
from app.schemas.finance_dunning import DUNNING_DEFAULT_STEP_CONFIG
from app.services.exception_center_service import ExceptionCenterService
from app.services.finance_charge_service import FinanceChargeService
from app.services.finance_dunning_service import FinanceDunningService
from app.services.finance_service import FinanceService
from app.services.klm_operations_service import KlmOperationsService
from app.services.meta_policy_gate import MetaPolicyGate
from app.services.referral_service import ReferralService
from app.services.whatsapp.helpers import phone_number_variants
from app.services.whatsapp_service import WhatsAppService
from app.workers.celery_app import TRANSIENT_ERRORS, celery_app

FINANCE_TASK_MAX_RETRIES = 5
FINANCE_TASK_RETRY_BACKOFF_MAX = 300
FINANCE_TAX_REFORM_ALERT_TICK_MAX_RETRIES = 3
# Tasks por workspace (S6-C): limites explícitos para uma carteira grande nunca
# segurar o worker nem estourar o visibility_timeout do Redis (3600s).
FINANCE_WORKSPACE_TASK_SOFT_TIME_LIMIT = 240
FINANCE_WORKSPACE_TASK_TIME_LIMIT = 300

# Rank "no caminho feliz" da cobrança — usado para nunca regredir um estado
# mais avançado por causa de um evento entregue fora de ordem (at-least-once
# delivery do Asaas, §3.6 do plano). Estados fora deste mapa (rascunho,
# vencida) valem rank 0; os estados terminais alternativos valem rank alto
# para nunca serem sobrescritos por um evento do fluxo principal atrasado.
_FORWARD_RANK: dict[FinanceChargeStatus, int] = {
    FinanceChargeStatus.criada: 1,
    FinanceChargeStatus.visualizada: 2,
    FinanceChargeStatus.confirmada: 3,
    FinanceChargeStatus.recebida: 4,
}
_TERMINAL_STATUSES = {
    FinanceChargeStatus.estornada,
    FinanceChargeStatus.chargeback,
    FinanceChargeStatus.cancelada,
}
_TERMINAL_RANK = 100


def _rank(charge_status: FinanceChargeStatus) -> int:
    if charge_status in _TERMINAL_STATUSES:
        return _TERMINAL_RANK
    return _FORWARD_RANK.get(charge_status, 0)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class AsaasWebhookReceiptNotFoundError(RuntimeError):
    """Recibo ainda não visível para esta sessão (replicação/commit em voo)."""


@celery_app.task(
    name="finance_tasks.tax_reform_alert_tick",
    bind=True,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=FINANCE_TAX_REFORM_ALERT_TICK_MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=FINANCE_TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
)
def tax_reform_alert_tick(self, reference_date: str | None = None) -> dict[str, int]:
    """Create due accountant review calls, never a fictitious phone delivery.

    The tick stores only durable delivery intents.  The dedicated human
    delivery worker owns provider dispatch and may mark a phone delivery as
    delivered only after receiving an actual provider receipt.
    """
    return _run_async(_async_tax_reform_alert_tick(reference_date=reference_date))


async def _async_tax_reform_alert_tick(
    *,
    reference_date: str | None = None,
    session_factory=None,
) -> dict[str, int]:
    from app.services.finance_tax_reform_alert_service import FinanceTaxReformAlertService

    today = date.fromisoformat(reference_date) if reference_date else date.today()
    session_maker = session_factory or AsyncSessionLocal
    totals = {"called": 0, "existing": 0, "blocked": 0, "failed": 0}
    # S30: a lista de workspaces vem de uma tabela sem RLS, mas o serviço lê e
    # grava tabelas de tenant (perfil tributário, alertas, decisões humanas).
    # Sem contexto, sob FORCE RLS, a leitura era cega e a gravação recusada, e a
    # task falhava todo dia. Uma sessão por workspace, com contexto e commit
    # próprios; um workspace com erro não derruba os outros.
    async with session_maker() as db:
        workspace_ids = list(
            (
                await db.scalars(
                    select(Workspace.id)
                    .where(Workspace.deleted_at.is_(None), Workspace.is_active.is_(True))
                    .order_by(Workspace.id)
                )
            ).all()
        )
    for workspace_id in workspace_ids:
        try:
            async with session_maker() as db:
                await set_workspace_context(db, workspace_id)
                outcome = await FinanceTaxReformAlertService(db).schedule_workspace(
                    workspace_id, as_of=today
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001 - isolado por workspace, contado e logado
            totals["failed"] += 1
            logger.error(
                "finance_tasks.tax_reform_workspace_failed",
                workspace_id=str(workspace_id),
                error_type=type(exc).__name__,
            )
            continue
        for key in ("called", "existing", "blocked"):
            totals[key] += int(outcome.get(key, 0))
    if totals["failed"]:
        logger.error("finance_tasks.tax_reform_tick_partial", **totals)
    return totals


@celery_app.task(
    name="finance_tasks.process_asaas_webhook_receipt",
    bind=True,
    autoretry_for=(Exception,),
    max_retries=FINANCE_TASK_MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=FINANCE_TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
)
def process_asaas_webhook_receipt(self, receipt_id: str, workspace_id: str | None = None) -> None:
    try:
        _run_async(_async_process_asaas_webhook_receipt(receipt_id, workspace_id=workspace_id))
    except (AsaasWebhookReceiptNotFoundError, Reject):
        raise
    except Exception as exc:
        if self.request.retries >= FINANCE_TASK_MAX_RETRIES:
            logger.error(
                "finance_tasks.asaas_webhook_giving_up",
                receipt_id=str(receipt_id),
                retries=self.request.retries,
                error_type=type(exc).__name__,
            )
        raise


def _event_target_status(event_type: str) -> Optional[FinanceChargeStatus]:
    normalized = (event_type or "").strip().upper()
    if "CHARGEBACK" in normalized:
        return FinanceChargeStatus.chargeback
    return {
        "PAYMENT_CREATED": FinanceChargeStatus.criada,
        "PAYMENT_CONFIRMED": FinanceChargeStatus.confirmada,
        "PAYMENT_RECEIVED": FinanceChargeStatus.recebida,
        "PAYMENT_OVERDUE": FinanceChargeStatus.vencida,
        "PAYMENT_DELETED": FinanceChargeStatus.cancelada,
        "PAYMENT_REFUNDED": FinanceChargeStatus.estornada,
        "PAYMENT_PARTIALLY_REFUNDED": FinanceChargeStatus.estornada,
        "PAYMENT_BANK_SLIP_VIEWED": FinanceChargeStatus.visualizada,
        "PAYMENT_CHECKOUT_VIEWED": FinanceChargeStatus.visualizada,
    }.get(normalized)


def _should_apply(current: FinanceChargeStatus, target: FinanceChargeStatus) -> bool:
    if target in (FinanceChargeStatus.cancelada, FinanceChargeStatus.estornada):
        # Cancelamento/estorno são decisões explícitas do gateway: sempre
        # aplicam, mesmo sobre um estado já avançado.
        return current != target
    if target == FinanceChargeStatus.chargeback:
        return current != target
    if target == FinanceChargeStatus.vencida:
        # Nunca marcar como vencida uma cobrança já recebida/estornada/
        # chargeback/cancelada.
        return current not in (
            FinanceChargeStatus.recebida,
            *_TERMINAL_STATUSES,
        )
    # Estados do caminho principal (criada/visualizada/confirmada/recebida):
    # só avança, nunca regride (ex.: VIEWED chegando depois de RECEIVED).
    return _rank(target) > _rank(current)


async def _async_process_asaas_webhook_receipt(
    receipt_id: UUID | str,
    *,
    workspace_id: UUID | str | None = None,
    session_factory=None,
) -> None:
    resolved_id = receipt_id if isinstance(receipt_id, UUID) else UUID(str(receipt_id))
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        if workspace_id is not None:
            # S7: o recibo é linha de tenant; sem contexto, sob FORCE RLS, db.get
            # devolvia None e a task reciclava até esgotar as tentativas.
            await set_workspace_context(db, UUID(str(workspace_id)))
        elif db.get_bind().dialect.name == "postgresql":
            logger.error("finance_tasks.asaas_receipt_sem_workspace", receipt_id=str(resolved_id))
            raise Reject("recibo Asaas sem workspace: ilegível sob RLS", requeue=False)
        receipt = await db.get(AsaasWebhookReceipt, resolved_id)
        if receipt is None:
            logger.warning("finance_tasks.asaas_receipt_not_found", receipt_id=str(resolved_id))
            raise AsaasWebhookReceiptNotFoundError(str(resolved_id))

        if receipt.status == AsaasWebhookReceiptStatus.processado:
            # Reentrega/retry do broker sobre um recibo já concluído: no-op.
            return

        try:
            await _apply_asaas_event(db, receipt)
            receipt.status = AsaasWebhookReceiptStatus.processado
            receipt.processed_at = _now()
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error(
                "finance_tasks.asaas_receipt_processing_error",
                receipt_id=str(resolved_id),
                error_type=type(exc).__name__,
            )
            raise


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def _apply_asaas_event(db, receipt: AsaasWebhookReceipt) -> None:
    payload: dict[str, Any] = receipt.payload or {}
    payment = payload.get("payment")
    payment = payment if isinstance(payment, dict) else {}
    external_id = payment.get("id")

    target_status = _event_target_status(receipt.event_type)
    if target_status is None:
        # Evento desconhecido/não mapeado: o Asaas pode introduzir novos
        # eventos a qualquer momento (§3.6). Nunca falhar por isso — apenas
        # registrar a nota no próprio recibo.
        receipt.payload = {**payload, "_processingNote": "evento_desconhecido_ignorado"}
        logger.info(
            "finance_tasks.asaas_event_unknown",
            event_type=receipt.event_type,
            workspace_id=str(receipt.workspace_id),
        )
        return

    if not external_id:
        receipt.payload = {**payload, "_processingNote": "payment_id_ausente"}
        logger.warning(
            "finance_tasks.asaas_event_missing_payment_id",
            event_type=receipt.event_type,
            workspace_id=str(receipt.workspace_id),
        )
        return

    charge = await db.scalar(
        select(FinanceCharge).where(
            FinanceCharge.workspace_id == receipt.workspace_id,
            FinanceCharge.external_id == str(external_id),
        )
    )
    if charge is None:
        # Ainda não temos a cobrança sincronizada localmente (ex.: criada por
        # outra via, ou corrida entre o create e o primeiro webhook). Não é
        # erro: fica registrado no recibo para a reconciliação periódica
        # (§3.8) pegar depois.
        receipt.payload = {**payload, "_processingNote": "charge_nao_encontrado_localmente"}
        logger.info(
            "finance_tasks.asaas_charge_not_found",
            event_type=receipt.event_type,
            workspace_id=str(receipt.workspace_id),
        )
        return

    previous_status = charge.status
    if not _should_apply(previous_status, target_status):
        logger.info(
            "finance_tasks.asaas_status_transition_skipped",
            event_type=receipt.event_type,
            charge_id=str(charge.id),
            previous_status=previous_status.value,
            target_status=target_status.value,
        )
        return

    charge.status = target_status
    charge.payload_snapshot = payment or charge.payload_snapshot
    if target_status == FinanceChargeStatus.recebida:
        paid_at_raw = payment.get("paymentDate") or payment.get("clientPaymentDate")
        charge.paid_at = _parse_date(paid_at_raw) or _now()
        net_value = payment.get("netValue")
        if net_value is not None:
            charge.net_value = net_value
    await db.flush()

    finance_service = FinanceService(db)
    entry = await db.get(FinanceEntry, charge.entry_id)

    if target_status == FinanceChargeStatus.recebida and entry is not None:
        if entry.status != FinanceEntryStatus.paid:
            await finance_service.update_entry(
                receipt.workspace_id,
                entry.id,
                FinanceEntryUpdate(status=FinanceEntryStatus.paid, paid_at=charge.paid_at),
            )
        # F13 — indicações com comissão: acúmulo no exato ponto de baixa do
        # pagamento. Idempotente por (workspace_id, charge_id) — reentregas
        # do mesmo webhook nunca geram uma segunda despesa de comissão.
        await ReferralService(db).register_commission_for_payment(
            receipt.workspace_id, charge, paid_amount=charge.value
        )
    elif target_status == FinanceChargeStatus.vencida and entry is not None:
        if entry.status not in (FinanceEntryStatus.paid, FinanceEntryStatus.cancelled):
            await finance_service.update_entry(
                receipt.workspace_id,
                entry.id,
                FinanceEntryUpdate(status=FinanceEntryStatus.overdue),
            )
    elif target_status == FinanceChargeStatus.cancelada and entry is not None:
        if entry.status not in (FinanceEntryStatus.paid,):
            await finance_service.update_entry(
                receipt.workspace_id,
                entry.id,
                FinanceEntryUpdate(status=FinanceEntryStatus.cancelled),
            )

    await KlmOperationsService(db).record_audit(
        workspace_id=receipt.workspace_id,
        actor_user_id=None,
        actor_type="system",
        actor_label="Webhook Asaas",
        event_type="finance.charge_status_transitioned",
        entity_type="finance_charge",
        entity_id=charge.id,
        summary=(
            f"Cobrança Asaas {external_id}: {previous_status.value} → {target_status.value} "
            f"(evento {receipt.event_type})."
        ),
        correlation_id=f"asaas:{receipt.event_id}",
        before_data={"status": previous_status.value},
        after_data={"status": target_status.value, "eventType": receipt.event_type},
    )

    if entry is not None and entry.matter_id is not None:
        db.add(
            TimelineEvent(
                matter_id=entry.matter_id,
                event_type=TimelineEventType.webhook_received,
                title=f"Cobrança atualizada: {target_status.value}",
                description=(
                    f"Webhook Asaas ({receipt.event_type}) atualizou a cobrança "
                    f"'{entry.title}' para '{target_status.value}'."
                ),
                event_data={
                    "gateway": "asaas",
                    "chargeId": str(charge.id),
                    "externalId": str(external_id),
                    "eventType": receipt.event_type,
                    "previousStatus": previous_status.value,
                    "newStatus": target_status.value,
                },
            )
        )
    await db.flush()


def _parse_date(value: Any):
    if not value:
        return None
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(str(value)[:10])
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Recorrência (F3) — docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F3
#
# Idempotência por competência: `FinanceRecurrenceRun` tem
# `UNIQUE(recurrence_id, competence)`. A task por workspace primeiro tenta inserir a linha
# de controle dentro de um SAVEPOINT (`db.begin_nested`); se colidir
# (`IntegrityError`), a competência já foi processada (por esta mesma task
# em uma chamada anterior, ou por uma execução concorrente) e a geração é
# pulada — rodar a task 10x na mesma competência sempre produz exatamente
# 1 FinanceEntry/FinanceCharge.
# ---------------------------------------------------------------------------

FINANCE_RECURRENCE_TICK_MAX_RETRIES = 3
# Teto de competências processadas em cascata por recorrência em uma única
# chamada da task por workspace — evita loop sem fim se a recorrência ficar muito tempo
# sem rodar (catch-up controlado, não infinito).
FINANCE_RECURRENCE_TICK_MAX_CATCHUP = 36

_CYCLE_DAY_DELTA: dict[FinanceRecurrenceCycle, int] = {
    FinanceRecurrenceCycle.semanal: 7,
    FinanceRecurrenceCycle.quinzenal: 15,
}
_CYCLE_MONTH_DELTA: dict[FinanceRecurrenceCycle, int] = {
    FinanceRecurrenceCycle.mensal: 1,
    FinanceRecurrenceCycle.bimestral: 2,
    FinanceRecurrenceCycle.trimestral: 3,
    FinanceRecurrenceCycle.semestral: 6,
    FinanceRecurrenceCycle.anual: 12,
}


def _add_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _advance_date(base: date, cycle: FinanceRecurrenceCycle) -> date:
    if cycle in _CYCLE_DAY_DELTA:
        from datetime import timedelta

        return base + timedelta(days=_CYCLE_DAY_DELTA[cycle])
    return _add_months(base, _CYCLE_MONTH_DELTA[cycle])


@celery_app.task(
    name="finance_tasks.finance_recurrence_tick",
    bind=True,
    autoretry_for=(Exception,),
    max_retries=FINANCE_RECURRENCE_TICK_MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=FINANCE_TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
)
def finance_recurrence_tick(self, reference_date: str | None = None) -> dict:
    return _run_async(_async_finance_recurrence_tick(reference_date=reference_date))


@celery_app.task(
    name="finance_tasks.finance_recurrence_workspace",
    bind=True,
    autoretry_for=(Exception,),
    max_retries=FINANCE_RECURRENCE_TICK_MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=FINANCE_TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
    soft_time_limit=FINANCE_WORKSPACE_TASK_SOFT_TIME_LIMIT,
    time_limit=FINANCE_WORKSPACE_TASK_TIME_LIMIT,
)
def finance_recurrence_workspace(self, workspace_id: str, reference_date: str) -> dict:
    return _run_async(
        _async_finance_recurrence_workspace(workspace_id, reference_date=reference_date)
    )


def _reference_day(reference_date: str | None) -> date:
    return date.fromisoformat(reference_date) if reference_date else date.today()


async def _async_finance_recurrence_tick(
    *,
    reference_date: str | None = None,
    session_factory=None,
) -> dict:
    """Descobre os workspaces com recorrência vencida e despacha uma task por
    workspace. Só lê: a geração acontece em `_async_finance_recurrence_workspace`,
    dentro do contexto do workspace. Sem o escopo de descoberta, a leitura sob
    FORCE RLS devolvia zero linhas e nenhuma recorrência era gerada (S6)."""
    today = _reference_day(reference_date)
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_worker_discovery_context(db, family="finance_recurrence")
        result = await db.execute(
            select(FinanceRecurrence.workspace_id)
            .join(Workspace, Workspace.id == FinanceRecurrence.workspace_id)
            .where(
                Workspace.deleted_at.is_(None),
                Workspace.is_active.is_(True),
                FinanceRecurrence.status == FinanceRecurrenceStatus.ativa,
                FinanceRecurrence.next_run <= today,
            )
            .distinct()
        )
        workspace_ids = [row[0] for row in result.all()]
    dispatched = 0
    for workspace_id in workspace_ids:
        finance_recurrence_workspace.apply_async(
            kwargs={"workspace_id": str(workspace_id), "reference_date": today.isoformat()}
        )
        dispatched += 1
    if not workspace_ids:
        logger.info("finance_recurrence.tick_sem_workspace", reference_date=today.isoformat())
    else:
        logger.info(
            "finance_recurrence.tick",
            reference_date=today.isoformat(),
            candidates=len(workspace_ids),
            dispatched=dispatched,
        )
    return {"candidates": len(workspace_ids), "dispatched": dispatched}


async def _async_finance_recurrence_workspace(
    workspace_id: UUID | str,
    *,
    reference_date: str | None = None,
    session_factory=None,
) -> dict:
    resolved_workspace_id = UUID(str(workspace_id))
    today = _reference_day(reference_date)
    session_maker = session_factory or AsyncSessionLocal
    generated_total = 0
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        result = await db.execute(
            select(FinanceRecurrence).where(
                FinanceRecurrence.workspace_id == resolved_workspace_id,
                FinanceRecurrence.status == FinanceRecurrenceStatus.ativa,
                FinanceRecurrence.next_run <= today,
            )
        )
        recurrences = list(result.scalars().all())
        for recurrence in recurrences:
            generated_total += await _process_recurrence(db, recurrence, today)
            # Commit por recorrência: sob soft_time_limit, o que já foi gerado fica
            # gravado e a próxima execução retoma do ponto certo (idempotente por
            # competência via UNIQUE + SAVEPOINT).
            await db.commit()
    return {"generated": generated_total}


async def _process_recurrence(db, recurrence: FinanceRecurrence, today: date) -> int:
    generated = 0
    guard = 0
    while (
        recurrence.status == FinanceRecurrenceStatus.ativa
        and recurrence.next_run <= today
        and guard < FINANCE_RECURRENCE_TICK_MAX_CATCHUP
    ):
        guard += 1
        competence = recurrence.next_run

        if (
            recurrence.kind == FinanceRecurrenceKind.assinatura_gateway
            and recurrence.gateway_subscription_id
            and external_delivery_allowed()
        ):
            # Assinatura ativa no gateway: o Asaas gera as cobranças sozinho
            # e elas chegam via webhook (_apply_asaas_event acima). Aqui só
            # avançamos o ponteiro local para não reprocessar esta janela.
            _advance_next_run(recurrence, competence)
            continue

        created = await _generate_local_entry_for_competence(db, recurrence, competence)
        if created:
            generated += 1
            _register_generation(recurrence)
        _advance_next_run(recurrence, competence)

    return generated


def _advance_next_run(recurrence: FinanceRecurrence, competence: date) -> None:
    recurrence.next_run = _advance_date(competence, recurrence.cycle)
    if recurrence.end_date is not None and recurrence.next_run > recurrence.end_date:
        recurrence.status = FinanceRecurrenceStatus.encerrada


def _register_generation(recurrence: FinanceRecurrence) -> None:
    recurrence.generated_count += 1
    if recurrence.max_count is not None and recurrence.generated_count >= recurrence.max_count:
        recurrence.status = FinanceRecurrenceStatus.encerrada


async def _generate_local_entry_for_competence(
    db, recurrence: FinanceRecurrence, competence: date
) -> bool:
    """Gera (no máximo uma vez por competência) a FinanceEntry + tentativa de
    cobrança de uma recorrência local (`geracao_programada`, ou
    `assinatura_gateway` sem assinatura ativa no gateway/gate fechado)."""
    try:
        async with db.begin_nested():
            run = FinanceRecurrenceRun(
                workspace_id=recurrence.workspace_id,
                recurrence_id=recurrence.id,
                competence=competence,
            )
            db.add(run)
            await db.flush()
    except IntegrityError:
        logger.info(
            "finance_tasks.recurrence_competence_already_processed",
            recurrence_id=str(recurrence.id),
            competence=competence.isoformat(),
        )
        return False

    entry = FinanceEntry(
        workspace_id=recurrence.workspace_id,
        matter_id=recurrence.matter_id,
        title=recurrence.entry_title,
        description=recurrence.entry_description,
        entry_type=recurrence.entry_type,
        status=FinanceEntryStatus.pending,
        amount=recurrence.entry_value,
        due_date=datetime.combine(competence, datetime.min.time(), tzinfo=timezone.utc),
        entry_metadata={
            "recurrenceId": str(recurrence.id),
            "clientId": str(recurrence.client_id),
            "competence": competence.isoformat(),
        },
    )
    db.add(entry)
    await db.flush()
    run.entry_id = entry.id

    billing_type = BillingType(recurrence.billing_type) if recurrence.billing_type else BillingType.UNDEFINED
    try:
        charge = await FinanceChargeService(db).create_charge_for_entry(
            recurrence.workspace_id, entry.id, FinanceChargeCreate(billing_type=billing_type)
        )
        run.charge_id = charge.id
    except Exception:
        # Falha ao criar a cobrança não pode reverter a geração local
        # idempotente do lançamento — fica registrado para reconciliação
        # manual/nova tentativa de cobrança sobre o lançamento já criado.
        logger.error(
            "finance_tasks.recurrence_charge_failed",
            recurrence_id=str(recurrence.id),
            competence=competence.isoformat(),
        )

    await db.flush()
    return True


# ---------------------------------------------------------------------------
# Régua de cobrança / dunning (F4) — docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F4
#
# Idempotência absoluta: `FinanceDunningLog` tem
# `UNIQUE(workspace_id, charge_id, step)`. A task por workspace insere a linha
# do degrau dentro de um SAVEPOINT (`db.begin_nested`); se colidir
# (`IntegrityError`), o degrau já foi processado (por esta task ou por outra
# concorrente) e é pulado — rodar a task 10x sobre a mesma cobrança produz no máximo
# 1 linha de log por degrau.
# ---------------------------------------------------------------------------

FINANCE_DUNNING_TICK_MAX_RETRIES = 3
_DUNNING_STEP_ORDER = (
    FinanceDunningStep.d1_lembrete,
    FinanceDunningStep.d7_segunda_via,
    FinanceDunningStep.d15_formal,
)
_FINAL_DUNNING_STEP = FinanceDunningStep.d15_formal


@celery_app.task(
    name="finance_tasks.dunning_tick",
    bind=True,
    autoretry_for=(Exception,),
    max_retries=FINANCE_DUNNING_TICK_MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=FINANCE_TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
)
def dunning_tick(self, reference_date: str | None = None) -> dict:
    return _run_async(_async_dunning_tick(reference_date=reference_date))


@celery_app.task(
    name="finance_tasks.dunning_workspace",
    bind=True,
    autoretry_for=(Exception,),
    max_retries=FINANCE_DUNNING_TICK_MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=FINANCE_TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
    soft_time_limit=FINANCE_WORKSPACE_TASK_SOFT_TIME_LIMIT,
    time_limit=FINANCE_WORKSPACE_TASK_TIME_LIMIT,
)
def dunning_workspace(self, workspace_id: str, reference_date: str) -> dict:
    return _run_async(_async_dunning_workspace(workspace_id, reference_date=reference_date))


def _overdue_charges_filter(now: datetime):
    return (
        FinanceCharge.status.notin_(
            (
                FinanceChargeStatus.recebida,
                FinanceChargeStatus.estornada,
                FinanceChargeStatus.cancelada,
            )
        ),
        FinanceEntry.status.notin_((FinanceEntryStatus.paid, FinanceEntryStatus.cancelled)),
        FinanceEntry.due_date.is_not(None),
        FinanceEntry.due_date <= now,
    )


async def _async_dunning_tick(
    *,
    reference_date: str | None = None,
    session_factory=None,
) -> dict:
    """Descobre os workspaces com cobrança vencida e despacha uma task por
    workspace; a régua roda em `_async_dunning_workspace`, dentro do contexto
    do workspace. Só lê (ver S6)."""
    today = _reference_day(reference_date)
    now = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_worker_discovery_context(db, family="dunning")
        result = await db.execute(
            select(FinanceCharge.workspace_id)
            .join(FinanceEntry, FinanceEntry.id == FinanceCharge.entry_id)
            .join(Workspace, Workspace.id == FinanceCharge.workspace_id)
            .where(
                Workspace.deleted_at.is_(None),
                Workspace.is_active.is_(True),
                *_overdue_charges_filter(now),
            )
            .distinct()
        )
        workspace_ids = [row[0] for row in result.all()]
    dispatched = 0
    for workspace_id in workspace_ids:
        dunning_workspace.apply_async(
            kwargs={"workspace_id": str(workspace_id), "reference_date": today.isoformat()}
        )
        dispatched += 1
    if not workspace_ids:
        logger.info("finance_dunning.tick_sem_workspace", reference_date=today.isoformat())
    else:
        logger.info(
            "finance_dunning.tick",
            reference_date=today.isoformat(),
            candidates=len(workspace_ids),
            dispatched=dispatched,
        )
    return {"candidates": len(workspace_ids), "dispatched": dispatched}


async def _async_dunning_workspace(
    workspace_id: UUID | str,
    *,
    reference_date: str | None = None,
    session_factory=None,
) -> dict:
    resolved_workspace_id = UUID(str(workspace_id))
    today = _reference_day(reference_date)
    now = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    session_maker = session_factory or AsyncSessionLocal
    processed_total = 0
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        result = await db.execute(
            select(FinanceCharge, FinanceEntry)
            .join(FinanceEntry, FinanceEntry.id == FinanceCharge.entry_id)
            .where(
                FinanceCharge.workspace_id == resolved_workspace_id,
                *_overdue_charges_filter(now),
            )
        )
        rows = list(result.all())
        for charge, entry in rows:
            processed_total += await _process_dunning_charge(db, charge, entry, today)
            # Commit por cobrança: mesmo motivo da recorrência (soft_time_limit).
            await db.commit()
    return {"processed": processed_total}


def _dunning_days_overdue(entry: FinanceEntry, today: date) -> int:
    due = entry.due_date
    due_date = due.date() if hasattr(due, "date") else due
    return (today - due_date).days


async def _process_dunning_charge(
    db, charge: FinanceCharge, entry: FinanceEntry, today: date
) -> int:
    """Processa os degraus devidos de UMA cobrança. Retorna quantos degraus
    foram efetivamente registrados nesta chamada (0, 1 ou vários, em ordem)."""
    days_overdue = _dunning_days_overdue(entry, today)
    if days_overdue < 0:
        return 0

    dunning_service = FinanceDunningService(db)
    settings = await dunning_service.get_config(charge.workspace_id)
    processed = 0

    for step in _DUNNING_STEP_ORDER:
        step_config = settings.steps.get(step.value)
        if step_config is None or not step_config.enabled:
            continue
        if days_overdue < step_config.offset_days:
            continue

        # Reentrância: verificar de novo o status atual da entry ANTES de
        # cada degrau — pagamento interrompe a régua a qualquer momento
        # (nenhum degrau novo é registrado para uma charge já recebida).
        await db.refresh(entry)
        await db.refresh(charge)
        if entry.status in (FinanceEntryStatus.paid, FinanceEntryStatus.cancelled):
            return processed
        if charge.status in (
            FinanceChargeStatus.recebida,
            FinanceChargeStatus.estornada,
            FinanceChargeStatus.cancelada,
        ):
            return processed

        created = await _register_dunning_step(
            db,
            charge=charge,
            entry=entry,
            step=step,
            requires_approval=step_config.requires_approval,
            message_template=step_config.message_template,
        )
        if created:
            processed += 1

    return processed


async def _register_dunning_step(
    db,
    *,
    charge: FinanceCharge,
    entry: FinanceEntry,
    step: FinanceDunningStep,
    requires_approval: bool,
    message_template: str,
) -> bool:
    viewed_before_dispatch = charge.status == FinanceChargeStatus.visualizada
    message_preview = _render_dunning_message(entry, message_template)

    try:
        async with db.begin_nested():
            log_entry = FinanceDunningLog(
                workspace_id=charge.workspace_id,
                charge_id=charge.id,
                step=step,
                status=FinanceDunningLogStatus.pending_approval
                if requires_approval
                else FinanceDunningLogStatus.local_draft,
                viewed_before_dispatch=viewed_before_dispatch,
                requires_approval=requires_approval,
                message_preview=message_preview,
            )
            db.add(log_entry)
            await db.flush()
    except IntegrityError:
        logger.info(
            "finance_tasks.dunning_step_already_processed",
            charge_id=str(charge.id),
            step=step.value,
        )
        return False

    if requires_approval:
        # Degrau exige aprovação humana explícita (default: D+15). Fica
        # registrado como pending_approval; o envio real só acontece em
        # `approve_dunning_log` após o clique humano.
        await db.flush()
        return True

    await _dispatch_dunning_log(db, log_entry, charge, entry)
    return True


def _render_dunning_message(entry: FinanceEntry, template: str) -> str:
    due_date = entry.due_date.date().isoformat() if entry.due_date else ""
    amount = f"R$ {float(entry.amount):.2f}"
    try:
        return template.format(
            clientName="cliente",
            entryTitle=entry.title,
            amount=amount,
            dueDate=due_date,
        )
    except (KeyError, IndexError, ValueError):
        return template


async def _resolve_dunning_phone(db, entry: FinanceEntry) -> Optional[str]:
    """Resolve o telefone do cliente da cobrança para o envio via WhatsApp.

    A `FinanceEntry` não guarda `client_id` diretamente; ele vem, quando
    existe, do `entry_metadata` (recorrências, F3) ou do `Matter` associado.
    """
    client_id_raw = (entry.entry_metadata or {}).get("clientId")
    client: Optional[Client] = None
    if client_id_raw:
        try:
            client = await db.get(Client, UUID(str(client_id_raw)))
        except (ValueError, TypeError):
            client = None
    if client is None and entry.matter_id is not None:
        matter = await db.get(Matter, entry.matter_id)
        if matter is not None and matter.client_id is not None:
            client = await db.get(Client, matter.client_id)
    if client is None or not client.phone:
        return None
    return client.phone


async def _resolve_dunning_last_inbound_at(db, workspace_id: UUID, phone: str):
    """Aproxima `last_inbound_at` pelo `last_seen` do contato WhatsApp já
    conhecido para este telefone (só existe se o cliente já escreveu para
    algum canal WhatsApp do workspace)."""
    contact = await db.scalar(
        select(WhatsAppContact).where(
            WhatsAppContact.workspace_id == workspace_id,
            WhatsAppContact.phone.in_(phone_number_variants(phone)),
        )
    )
    return contact.last_seen if contact is not None else None


async def _dispatch_dunning_log(
    db, log_entry: FinanceDunningLog, charge: FinanceCharge, entry: FinanceEntry
) -> None:
    """Tenta o envio real (WhatsApp) de um degrau da régua já registrado
    (idempotente — chamado no máximo uma vez por linha de log, seja pela
    task por workspace da régua, seja pela aprovação humana)."""
    if not external_delivery_allowed():
        log_entry.status = FinanceDunningLogStatus.local_draft
        log_entry.reason_code = "gates_closed"
        await _maybe_notify_owner(db, log_entry, charge)
        return

    phone = await _resolve_dunning_phone(db, entry)
    if not phone:
        log_entry.status = FinanceDunningLogStatus.local_draft
        log_entry.reason_code = "phone_not_found"
        await _maybe_notify_owner(db, log_entry, charge)
        return

    last_inbound_at = await _resolve_dunning_last_inbound_at(db, charge.workspace_id, phone)
    decision = MetaPolicyGate.evaluate(
        "whatsapp",
        last_inbound_at=last_inbound_at,
        now=_now(),
        author_type="agent",
        action_type="reminder",
    )
    if not decision.allowed:
        log_entry.status = FinanceDunningLogStatus.local_draft
        log_entry.reason_code = decision.reason_code
        await _maybe_notify_owner(db, log_entry, charge)
        return

    sent = False
    try:
        sent = await WhatsAppService(db)._send_text_direct(
            phone=phone,
            body=log_entry.message_preview or "",
            workspace_id=charge.workspace_id,
            idempotency_key=f"finance-dunning:{log_entry.id}",
        )
    except Exception:
        logger.error(
            "finance_tasks.dunning_send_failed",
            charge_id=str(charge.id),
            step=log_entry.step.value,
        )
        sent = False

    if sent:
        log_entry.status = FinanceDunningLogStatus.sent
        log_entry.reason_code = "sent"
        log_entry.sent_at = _now()
    else:
        log_entry.status = FinanceDunningLogStatus.local_draft
        log_entry.reason_code = "send_failed"
        # Antes disto o envio falhava e o degrau só ficava marcado
        # `local_draft`/`send_failed` — nenhum humano era avisado até o
        # degrau final (D+15). Abre a Central de Exceções (Plano Mestre
        # §6/§3) em TODO degrau que falhou o envio real, não só no final.
        await _open_dunning_send_failure_exception(db, log_entry, charge, entry)

    await _maybe_notify_owner(db, log_entry, charge)


async def _open_dunning_send_failure_exception(
    db,
    log_entry: FinanceDunningLog,
    charge: FinanceCharge,
    entry: FinanceEntry,
) -> None:
    await ExceptionCenterService(db, charge.workspace_id).open_exception(
        exception_kind=ExceptionKind.acao_externa_nao_autorizada,
        origin="finance_dunning.dispatch",
        dedupe_key=str(log_entry.id),
        title=f"Cobrança (WhatsApp) não enviada — degrau {log_entry.step.value}",
        description=(
            "A tentativa de envio real da régua de cobrança falhou "
            f"(cobrança {charge.id}, degrau {log_entry.step.value}). O "
            "cliente NÃO foi notificado — decida se reenvia manualmente "
            "ou usa outro canal."
        ),
        matter_id=entry.matter_id,
        payload={
            "chargeId": str(charge.id),
            "dunningLogId": str(log_entry.id),
            "step": log_entry.step.value,
            "financeEntryId": str(entry.id),
        },
    )


async def _maybe_notify_owner(db, log_entry: FinanceDunningLog, charge: FinanceCharge) -> None:
    """Hermes notifica o dono do workspace no degrau final (D+15)."""
    if log_entry.step != _FINAL_DUNNING_STEP or log_entry.owner_notified_at is not None:
        return
    await KlmOperationsService(db).record_audit(
        workspace_id=charge.workspace_id,
        actor_user_id=None,
        actor_type="agent",
        actor_label="hermes",
        event_type="finance.dunning_owner_notified",
        entity_type="finance_charge",
        entity_id=charge.id,
        summary=(
            f"Régua de cobrança atingiu o degrau formal (D+15) para a cobrança "
            f"{charge.id}. Hermes notificou o dono do workspace."
        ),
        correlation_id=f"dunning:{charge.id}:{log_entry.step.value}",
        before_data=None,
        after_data={"status": log_entry.status.value},
    )
    log_entry.owner_notified_at = _now()


async def approve_dunning_log(
    db,
    workspace_id: UUID,
    log_id: UUID,
    approved_by_user_id: Optional[UUID],
) -> FinanceDunningLog:
    """Aprovação humana de um degrau `pending_approval`: dispara o envio
    real (idempotente — só transiciona uma vez, dado que `status` deixa de
    ser `pending_approval` após a primeira aprovação)."""
    log_entry = await db.scalar(
        select(FinanceDunningLog).where(
            FinanceDunningLog.workspace_id == workspace_id,
            FinanceDunningLog.id == log_id,
        )
    )
    if log_entry is None:
        raise ValueError("finance_dunning_log_not_found")
    if log_entry.status != FinanceDunningLogStatus.pending_approval:
        # Já resolvido (aprovado/enviado) — idempotente, no-op.
        return log_entry

    charge = await db.get(FinanceCharge, log_entry.charge_id)
    entry = await db.get(FinanceEntry, charge.entry_id) if charge else None
    log_entry.approved_by_user_id = approved_by_user_id
    log_entry.approved_at = _now()

    if entry is not None and entry.status in (
        FinanceEntryStatus.paid,
        FinanceEntryStatus.cancelled,
    ):
        # Pagamento chegou antes da aprovação humana: régua interrompida,
        # nada é enviado.
        log_entry.status = FinanceDunningLogStatus.local_draft
        log_entry.reason_code = "paid_before_approval"
        await db.flush()
        return log_entry

    if charge is not None and entry is not None:
        await _dispatch_dunning_log(db, log_entry, charge, entry)

    await db.flush()
    return log_entry
