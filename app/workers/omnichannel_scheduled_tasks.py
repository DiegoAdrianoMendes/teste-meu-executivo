"""Durable native worker for canonical omnichannel scheduled messages."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.omnichannel_v6 import (
    OmnichannelChannelConnection,
    OmnichannelEndpoint,
    OmnichannelScheduledMessage,
    OmnichannelThreadBinding,
)
from app.models.workspace import Workspace
from app.schemas.klm_operations import OmnichannelMessageCreate
from app.schemas.omnichannel_v6 import InternalMessageDeliveryReceiptIngest
from app.services.klm_operations_service import KlmOperationsService
from app.services.messaging_cadence_service import MessagingCadenceService
from app.services.meta_policy_gate import (
    META_CHANNELS,
    MetaPolicyGate,
    clamp_service_window_hours,
)
from app.services.omnichannel_delivery import (
    ScheduledDispatchError,
    ScheduledDispatchReceipt,
    ScheduledDispatchRequest,
    ScheduledMessageDispatcher,
)
from app.services.omnichannel_service_window import latest_inbound_at_for_route
from app.services.omnichannel_v6_service import OmnichannelV6Service
from app.workers.celery_app import celery_app
from app.workers.fanout import FanoutMetrics, bounded_batch_size

SCHEDULE_LEASE = timedelta(minutes=5)
WORKSPACE_BATCH_SIZE = 64
TOTAL_BATCH_SIZE = 200


class ScheduledDeliveryFencedError(RuntimeError):
    """The worker no longer owns the generation allowed to settle delivery."""


class _UnconfiguredDispatcher:
    def __init__(self, connection: OmnichannelChannelConnection) -> None:
        self.supports_idempotency = bool(
            (connection.capabilities or {}).get("outboundIdempotency")
        )

    async def dispatch(self, request: ScheduledDispatchRequest) -> ScheduledDispatchReceipt:
        raise ScheduledDispatchError(
            "O adaptador oficial desse canal ainda não está conectado ao worker nativo.",
            code="adapter_not_configured",
            retryable=False,
        )


DispatcherFactory = Callable[[OmnichannelChannelConnection], ScheduledMessageDispatcher]


def _default_dispatcher_factory(
    connection: OmnichannelChannelConnection,
) -> ScheduledMessageDispatcher:
    return _UnconfiguredDispatcher(connection)


def _inject_fault(point: str, **details: Any) -> None:
    """Test hook for process-death simulation at committed boundaries."""


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _retry_at(schedule_id: UUID, attempts: int, *, now: datetime) -> datetime:
    exponent = max(0, min(attempts - 1, 8))
    base_seconds = min(30 * (2**exponent), 1800)
    jitter = int(hashlib.sha256(str(schedule_id).encode()).hexdigest()[:2], 16) % 16
    return now + timedelta(seconds=base_seconds + jitter)


def _cadence_not_before(message: OmnichannelMessage) -> datetime | None:
    cadence = (message.message_metadata or {}).get("deliveryCadence")
    if not isinstance(cadence, dict):
        return None
    value = cadence.get("notBefore")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


async def _due_workspace_ids(
    *,
    session_factory=WorkerSessionLocal,
    now: datetime | None = None,
    batch_size: int = WORKSPACE_BATCH_SIZE,
) -> list[UUID]:
    reference_now = _aware(now)
    limit = bounded_batch_size(batch_size)
    due = _due_predicate(reference_now)
    async with session_factory() as db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(db, family="omnichannel_scheduled")
        return list(
            (
                await db.scalars(
                    select(OmnichannelScheduledMessage.workspace_id)
                    .join(Workspace, Workspace.id == OmnichannelScheduledMessage.workspace_id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                        due,
                    )
                    .group_by(OmnichannelScheduledMessage.workspace_id)
                    .order_by(func.min(OmnichannelScheduledMessage.scheduled_at))
                    .limit(limit)
                )
            ).all()
        )


def _due_predicate(now: datetime):
    ready = and_(
        OmnichannelScheduledMessage.state.in_(["scheduled", "retrying"]),
        OmnichannelScheduledMessage.scheduled_at <= now,
        or_(
            OmnichannelScheduledMessage.next_attempt_at.is_(None),
            OmnichannelScheduledMessage.next_attempt_at <= now,
        ),
    )
    abandoned = and_(
        OmnichannelScheduledMessage.state == "processing",
        or_(
            OmnichannelScheduledMessage.lease_expires_at.is_(None),
            OmnichannelScheduledMessage.lease_expires_at <= now,
        ),
    )
    return or_(ready, abandoned)


async def _append_event(
    db: AsyncSession,
    schedule: OmnichannelScheduledMessage,
    *,
    event_type: str,
    previous_state: str,
    next_state: str,
    idempotency_key: str,
    payload: dict[str, Any],
    occurred_at: datetime,
    external_event_id: str | None = None,
    external_message_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await OmnichannelV6Service(db)._append_schedule_event(
        schedule,
        event_type=event_type,
        previous_state=previous_state,
        next_state=next_state,
        idempotency_key=idempotency_key,
        payload=payload,
        occurred_at=occurred_at,
        external_event_id=external_event_id,
        external_message_id=external_message_id,
        error_code=error_code,
        error_message=error_message,
        metadata=metadata,
    )


async def _claim(
    db: AsyncSession,
    schedule: OmnichannelScheduledMessage,
    *,
    now: datetime,
) -> tuple[str, int] | None:
    if schedule.state == "processing":
        if schedule.delivery_attempted_at and not schedule.provider_supports_idempotency:
            previous_state = schedule.state
            schedule.state = "delivery_uncertain"
            schedule.delivery_uncertain_at = now
            schedule.last_error_code = "expired_unkeyed_attempt"
            schedule.last_error_message = (
                "Entrega possivelmente aceita; reconciliação humana obrigatória."
            )
            schedule.completed_at = now
            await _append_event(
                db,
                schedule,
                event_type="delivery_uncertain",
                previous_state=previous_state,
                next_state=schedule.state,
                idempotency_key=(
                    f"schedule-uncertain:{schedule.id}:{schedule.lease_generation}"
                ),
                payload={"reason": schedule.last_error_code},
                occurred_at=now,
                error_code=schedule.last_error_code,
                error_message=schedule.last_error_message,
            )
            schedule.lease_owner = None
            schedule.lease_expires_at = None
            await db.commit()
            return None
    previous_state = schedule.state
    lease_owner = str(uuid4())
    generation = int(schedule.lease_generation or 0) + 1
    schedule.state = "processing"
    schedule.lease_owner = lease_owner
    schedule.lease_generation = generation
    schedule.lease_expires_at = now + SCHEDULE_LEASE
    schedule.last_error_code = None
    schedule.last_error_message = None
    await _append_event(
        db,
        schedule,
        event_type="claimed",
        previous_state=previous_state,
        next_state="processing",
        idempotency_key=f"schedule-claim:{schedule.id}:{generation}",
        payload={"leaseGeneration": generation},
        occurred_at=now,
        metadata={"leaseExpiresAt": schedule.lease_expires_at.isoformat()},
    )
    await db.commit()
    _inject_fault(
        "native_schedule.after_claim",
        scheduled_message_id=str(schedule.id),
        lease_owner=lease_owner,
        lease_generation=generation,
    )
    return lease_owner, generation


async def _claimed_schedule(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    schedule_id: UUID,
    lease_owner: str,
    generation: int,
    lock: bool = False,
) -> OmnichannelScheduledMessage | None:
    query = select(OmnichannelScheduledMessage).where(
        OmnichannelScheduledMessage.workspace_id == workspace_id,
        OmnichannelScheduledMessage.id == schedule_id,
        OmnichannelScheduledMessage.state == "processing",
        OmnichannelScheduledMessage.lease_owner == lease_owner,
        OmnichannelScheduledMessage.lease_generation == generation,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


async def _resolve_route(
    db: AsyncSession,
    schedule: OmnichannelScheduledMessage,
) -> tuple[OmnichannelChannelConnection, OmnichannelEndpoint, OmnichannelThreadBinding | None]:
    if schedule.connection_id is None or schedule.endpoint_id is None:
        raise ScheduledDispatchError(
            "Agendamento sem conexão ou destinatário exato.",
            code="route_incomplete",
            retryable=False,
        )
    connection = await db.scalar(
        select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.workspace_id == schedule.workspace_id,
            OmnichannelChannelConnection.id == schedule.connection_id,
            OmnichannelChannelConnection.connection_key == schedule.connection_key,
            OmnichannelChannelConnection.channel == schedule.channel,
        )
    )
    endpoint = await db.scalar(
        select(OmnichannelEndpoint).where(
            OmnichannelEndpoint.workspace_id == schedule.workspace_id,
            OmnichannelEndpoint.id == schedule.endpoint_id,
            OmnichannelEndpoint.connection_id == schedule.connection_id,
            OmnichannelEndpoint.channel == schedule.channel,
        )
    )
    if connection is None or endpoint is None:
        raise ScheduledDispatchError(
            "A conexão ou o destinatário do agendamento não está mais disponível.",
            code="route_unavailable",
            retryable=False,
        )
    if connection.status not in {"connected", "active", "configured_local"}:
        raise ScheduledDispatchError(
            "A conexão selecionada não está ativa.",
            code="connection_inactive",
            retryable=True,
        )
    if endpoint.blocked_at is not None or endpoint.consent_state in {"blocked", "opted_out"}:
        raise ScheduledDispatchError(
            "O destinatário bloqueou ou recusou novas mensagens.",
            code="recipient_suppressed",
            retryable=False,
        )
    binding = await db.scalar(
        select(OmnichannelThreadBinding).where(
            OmnichannelThreadBinding.workspace_id == schedule.workspace_id,
            OmnichannelThreadBinding.thread_id == schedule.thread_id,
            OmnichannelThreadBinding.connection_id == schedule.connection_id,
            OmnichannelThreadBinding.connection_key == schedule.connection_key,
            OmnichannelThreadBinding.binding_state == "active",
        )
    )
    return connection, endpoint, binding


async def _enforce_meta_policy_gate(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    connection: OmnichannelChannelConnection,
    last_inbound_at: datetime | None,
) -> None:
    """Aplica o Meta Policy Gate ANTES do dispatch de agendados, para canais Meta.

    Espelho de `app.workers.omnichannel_action_tasks._enforce_meta_policy_gate`.
    Mensagens agendadas não carregam `private_reply` (isso é só de action intents
    sociais); o `action_type` avaliado é sempre `send_message`. Exceção
    documentada: WhatsApp fora da janela (`requires="template"`) deixa passar —
    `MetaCloudScheduledDispatcher` já resolve template.
    """
    channel = (connection.channel or "").strip().lower()
    if channel not in META_CHANNELS:
        return
    now = datetime.now(UTC)
    automated_hourly_count: int | None = None
    if channel == "instagram":
        automated_hourly_count = await MessagingCadenceService(
            db
        ).count_recent_agent_messages_for_connection(workspace_id, connection.id, now=now)
    decision = MetaPolicyGate.evaluate(
        channel,
        last_inbound_at=last_inbound_at,
        now=now,
        author_type="agent",
        action_type="send_message",
        automated_hourly_count=automated_hourly_count,
    )
    if decision.allowed:
        return
    if channel == "whatsapp" and decision.requires == "template":
        return
    raise ScheduledDispatchError(
        decision.reason,
        code=f"policy_gate_{decision.reason_code}",
        retryable=False,
    )


async def _prepare_attempt(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    schedule_id: UUID,
    lease_owner: str,
    generation: int,
    dispatcher_factory: DispatcherFactory,
) -> tuple[ScheduledDispatchRequest, ScheduledMessageDispatcher] | None:
    schedule = await _claimed_schedule(
        db,
        workspace_id=workspace_id,
        schedule_id=schedule_id,
        lease_owner=lease_owner,
        generation=generation,
    )
    if schedule is None:
        raise ScheduledDeliveryFencedError("Lease substituído antes do checkpoint.")
    connection, endpoint, binding = await _resolve_route(db, schedule)
    if dispatcher_factory is _default_dispatcher_factory and connection.provider == "meta_cloud":
        from app.services.whatsapp.omnichannel_dispatcher import (
            MetaCloudScheduledDispatcher,
        )

        dispatcher = MetaCloudScheduledDispatcher(db, connection)
    else:
        dispatcher = dispatcher_factory(connection)
    thread = await db.scalar(
        select(OmnichannelThread).where(
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.id == schedule.thread_id,
        )
    )
    if thread is None:
        raise ScheduledDispatchError(
            "A conversa vinculada ao agendamento não foi encontrada.",
            code="thread_missing",
            retryable=False,
        )
    service_window_hours = clamp_service_window_hours(
        connection.channel, (connection.routing_policy or {}).get("serviceWindowHours", 24)
    )
    route_last_inbound_at = await latest_inbound_at_for_route(
        db,
        workspace_id=workspace_id,
        thread=thread,
        channel=connection.channel,
        connection_id=connection.id,
    )
    service_window_expires_at = (
        _aware(route_last_inbound_at) + timedelta(hours=service_window_hours)
        if route_last_inbound_at
        else None
    )
    await _enforce_meta_policy_gate(
        db,
        workspace_id=workspace_id,
        connection=connection,
        last_inbound_at=route_last_inbound_at,
    )
    canonical = None
    if schedule.canonical_message_id:
        canonical = await db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.id == schedule.canonical_message_id,
            )
        )
        if canonical is None:
            raise ScheduledDispatchError(
                "A mensagem canônica vinculada ao agendamento não foi encontrada.",
                code="canonical_message_missing",
                retryable=False,
            )
    else:
        try:
            author_type = MessageAuthorType(schedule.author_type)
        except ValueError:
            author_type = MessageAuthorType.system
        body = (schedule.body or "").strip() or "Mensagem com modelo ou anexo."
        canonical, _ = await KlmOperationsService(db).create_message(
            workspace_id,
            schedule.thread_id,
            schedule.created_by_user_id,
            OmnichannelMessageCreate(
                endpoint_id=schedule.endpoint_id,
                binding_id=binding.id if binding else None,
                channel=OmnichannelChannel(schedule.channel),
                direction=MessageDirection.outbound,
                author_type=author_type,
                author_label=schedule.author_label,
                body=body,
                content_type=(
                    "template"
                    if schedule.template_id
                    else "attachment"
                    if schedule.attachments
                    else "text"
                ),
                template_id=schedule.template_id,
                transport=connection.transport_mode,
                window_expires_at=service_window_expires_at,
                idempotency_key=f"scheduled:{schedule.id}",
                message_metadata={
                    "source": "native_scheduled_message",
                    "scheduledMessageId": str(schedule.id),
                    "connectionId": str(connection.id),
                    "connectionKey": connection.connection_key,
                    "provider": connection.provider,
                    "attachments": schedule.attachments,
                    "structuredContent": schedule.message_payload,
                    "originSurface": "cloud_api",
                    "transportMode": connection.transport_mode,
                },
            ),
        )

    now = datetime.now(UTC)
    not_before = _cadence_not_before(canonical)
    if not_before and not_before > now:
        transition = await db.execute(
            update(OmnichannelScheduledMessage)
            .where(
                OmnichannelScheduledMessage.workspace_id == workspace_id,
                OmnichannelScheduledMessage.id == schedule.id,
                OmnichannelScheduledMessage.state == "processing",
                OmnichannelScheduledMessage.lease_owner == lease_owner,
                OmnichannelScheduledMessage.lease_generation == generation,
            )
            .values(
                canonical_message_id=canonical.id,
                state="scheduled",
                next_attempt_at=not_before,
                lease_owner=None,
                lease_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if transition.rowcount != 1:
            await db.rollback()
            raise ScheduledDeliveryFencedError("Lease substituído ao adiar a cadência.")
        schedule.canonical_message_id = canonical.id
        schedule.state = "scheduled"
        schedule.next_attempt_at = not_before
        await _append_event(
            db,
            schedule,
            event_type="cadence_deferred",
            previous_state="processing",
            next_state="scheduled",
            idempotency_key=f"schedule-cadence:{schedule.id}:{generation}",
            payload={"notBefore": not_before.isoformat()},
            occurred_at=now,
            metadata={"canonicalMessageId": str(canonical.id)},
        )
        schedule.lease_owner = None
        schedule.lease_expires_at = None
        await db.commit()
        return None

    transition = await db.execute(
        update(OmnichannelScheduledMessage)
        .where(
            OmnichannelScheduledMessage.workspace_id == workspace_id,
            OmnichannelScheduledMessage.id == schedule.id,
            OmnichannelScheduledMessage.state == "processing",
            OmnichannelScheduledMessage.lease_owner == lease_owner,
            OmnichannelScheduledMessage.lease_generation == generation,
            OmnichannelScheduledMessage.lease_expires_at > now,
        )
        .values(
            canonical_message_id=canonical.id,
            delivery_attempted_at=now,
            attempts=OmnichannelScheduledMessage.attempts + 1,
            provider_supports_idempotency=dispatcher.supports_idempotency,
        )
        .execution_options(synchronize_session=False)
    )
    if transition.rowcount != 1:
        await db.rollback()
        raise ScheduledDeliveryFencedError("Lease expirado antes da tentativa externa.")
    schedule.canonical_message_id = canonical.id
    schedule.delivery_attempted_at = now
    schedule.attempts += 1
    schedule.provider_supports_idempotency = dispatcher.supports_idempotency
    await _append_event(
        db,
        schedule,
        event_type="attempt_started",
        previous_state="processing",
        next_state="processing",
        idempotency_key=f"schedule-attempt:{schedule.id}:{generation}",
        payload={
            "canonicalMessageId": str(canonical.id),
            "attempt": schedule.attempts,
            "providerSupportsIdempotency": dispatcher.supports_idempotency,
        },
        occurred_at=now,
    )
    await db.commit()
    _inject_fault(
        "native_schedule.after_attempt_checkpoint",
        scheduled_message_id=str(schedule.id),
        lease_owner=lease_owner,
        lease_generation=generation,
    )
    return (
        ScheduledDispatchRequest(
            workspace_id=workspace_id,
            scheduled_message_id=schedule.id,
            canonical_message_id=canonical.id,
            connection_id=connection.id,
            connection_key=connection.connection_key,
            provider=connection.provider,
            transport_mode=connection.transport_mode,
            channel=schedule.channel,
            endpoint_id=endpoint.id,
            external_user_id=endpoint.external_user_id,
            address=endpoint.address,
            body=schedule.body,
            template_id=schedule.template_id,
            attachments=list(schedule.attachments or []),
            message_payload=dict(schedule.message_payload or {}),
            idempotency_key=f"scheduled:{schedule.id}",
            service_window_expires_at=service_window_expires_at,
        ),
        dispatcher,
    )


async def _settle_success(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    schedule_id: UUID,
    lease_owner: str,
    generation: int,
    request: ScheduledDispatchRequest,
    receipt: ScheduledDispatchReceipt,
) -> None:
    schedule = await _claimed_schedule(
        db,
        workspace_id=workspace_id,
        schedule_id=schedule_id,
        lease_owner=lease_owner,
        generation=generation,
        lock=True,
    )
    if schedule is None:
        raise ScheduledDeliveryFencedError("Resultado externo chegou após troca do lease.")
    if receipt.provider != request.provider:
        raise ScheduledDispatchError(
            "O recibo retornou um provedor diferente da rota preparada.",
            code="provider_receipt_mismatch",
            retryable=False,
            outcome_uncertain=True,
        )
    now = _aware(receipt.accepted_at)
    external_event_id = receipt.external_event_id or (
        f"scheduled:{schedule.id}:accepted:{receipt.external_message_id}"
    )
    service = OmnichannelV6Service(db)
    await service.ingest_delivery_receipt(
        workspace_id,
        InternalMessageDeliveryReceiptIngest(
            message_id=request.canonical_message_id,
            provider=request.provider,
            connection_key=request.connection_key,
            external_event_id=external_event_id,
            external_message_id=receipt.external_message_id,
            status=MessageDeliveryStatus.sent,
            provider_status=receipt.provider_status,
            occurred_at=now,
            cost_amount=receipt.cost_amount,
            cost_currency=receipt.cost_currency,
            pricing_category=receipt.pricing_category,
            provider_invoice_reference=receipt.provider_invoice_reference,
            payload=receipt.payload,
            metadata={"source": "native_scheduled_worker"},
        ),
    )
    schedule.state = "sent"
    schedule.provider_message_id = receipt.external_message_id
    schedule.completed_at = now
    schedule.next_attempt_at = None
    schedule.delivery_uncertain_at = None
    schedule.last_error_code = None
    schedule.last_error_message = None
    schedule.provider_receipt = {
        "provider": receipt.provider,
        "externalMessageId": receipt.external_message_id,
        "externalEventId": external_event_id,
        "providerStatus": receipt.provider_status,
        "acceptedAt": now.isoformat(),
        "localOnly": False,
        "externalActionsExecuted": 1,
    }
    await _append_event(
        db,
        schedule,
        event_type="sent",
        previous_state="processing",
        next_state="sent",
        idempotency_key=f"schedule-sent:{schedule.id}:{generation}",
        payload=schedule.provider_receipt,
        occurred_at=now,
        external_event_id=external_event_id,
        external_message_id=receipt.external_message_id,
    )
    schedule.lease_owner = None
    schedule.lease_expires_at = None
    await service.operations.record_audit(
        workspace_id=workspace_id,
        actor_user_id=None,
        actor_type="system",
        actor_label="Worker de mensagens agendadas KLM",
        event_type="omnichannel_scheduled_message_sent",
        entity_type="omnichannel_scheduled_message",
        entity_id=schedule.id,
        summary="Mensagem agendada aceita pelo provedor.",
        correlation_id=external_event_id,
        after_data={
            "state": "sent",
            "canonicalMessageId": str(request.canonical_message_id),
            "externalMessageId": receipt.external_message_id,
        },
    )
    await db.commit()
    _inject_fault(
        "native_schedule.after_success_checkpoint",
        scheduled_message_id=str(schedule.id),
        lease_generation=generation,
    )


async def _settle_failure(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    schedule_id: UUID,
    lease_owner: str,
    generation: int,
    error: Exception,
    attempt_started: bool,
    provider_supports_idempotency: bool,
) -> None:
    schedule = await _claimed_schedule(
        db,
        workspace_id=workspace_id,
        schedule_id=schedule_id,
        lease_owner=lease_owner,
        generation=generation,
        lock=True,
    )
    if schedule is None:
        await db.rollback()
        return
    now = datetime.now(UTC)
    if not attempt_started:
        schedule.attempts += 1
    code = getattr(error, "code", safe_error_type(error))
    retryable = bool(getattr(error, "retryable", True))
    outcome_uncertain = bool(getattr(error, "outcome_uncertain", False))
    if attempt_started and not provider_supports_idempotency and not isinstance(
        error, ScheduledDispatchError
    ):
        outcome_uncertain = True
    previous_state = schedule.state
    if outcome_uncertain:
        schedule.state = "delivery_uncertain"
        schedule.delivery_uncertain_at = now
        schedule.completed_at = now
        event_type = "delivery_uncertain"
    elif retryable and schedule.attempts < schedule.max_attempts:
        schedule.state = "retrying"
        schedule.next_attempt_at = _retry_at(schedule.id, schedule.attempts, now=now)
        event_type = "retry_scheduled"
    else:
        schedule.state = "failed"
        schedule.completed_at = now
        event_type = "failed"
    schedule.last_error_code = str(code)[:120]
    schedule.last_error_message = str(error)[:4000]
    await _append_event(
        db,
        schedule,
        event_type=event_type,
        previous_state=previous_state,
        next_state=schedule.state,
        idempotency_key=f"schedule-{event_type}:{schedule.id}:{generation}",
        payload={
            "attempt": schedule.attempts,
            "retryAt": schedule.next_attempt_at.isoformat()
            if schedule.next_attempt_at
            else None,
            "providerSupportsIdempotency": provider_supports_idempotency,
        },
        occurred_at=now,
        error_code=schedule.last_error_code,
        error_message=schedule.last_error_message,
    )
    schedule.lease_owner = None
    schedule.lease_expires_at = None
    await db.commit()


async def _process_one(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    schedule_id: UUID,
    lease_owner: str,
    generation: int,
    dispatcher_factory: DispatcherFactory,
) -> bool:
    attempt_started = False
    supports_idempotency = False
    try:
        prepared = await _prepare_attempt(
            db,
            workspace_id=workspace_id,
            schedule_id=schedule_id,
            lease_owner=lease_owner,
            generation=generation,
            dispatcher_factory=dispatcher_factory,
        )
        if prepared is None:
            return False
        request, dispatcher = prepared
        attempt_started = True
        supports_idempotency = dispatcher.supports_idempotency
        receipt = await dispatcher.dispatch(request)
        if not receipt.external_message_id:
            raise ScheduledDispatchError(
                "O provedor aceitou a chamada sem devolver o ID da mensagem.",
                code="missing_provider_message_id",
                retryable=False,
                outcome_uncertain=True,
            )
        _inject_fault(
            "native_schedule.after_provider_accept",
            scheduled_message_id=str(schedule_id),
            lease_generation=generation,
        )
        await _settle_success(
            db,
            workspace_id=workspace_id,
            schedule_id=schedule_id,
            lease_owner=lease_owner,
            generation=generation,
            request=request,
            receipt=receipt,
        )
        return True
    except ScheduledDeliveryFencedError:
        await db.rollback()
        return False
    except Exception as error:
        await db.rollback()
        await _settle_failure(
            db,
            workspace_id=workspace_id,
            schedule_id=schedule_id,
            lease_owner=lease_owner,
            generation=generation,
            error=error,
            attempt_started=attempt_started,
            provider_supports_idempotency=supports_idempotency,
        )
        logger.warning(
            "omnichannel.schedule.delivery_failed",
            scheduled_message_id=str(schedule_id),
            error_type=safe_error_type(error),
        )
        return False


async def _process_workspace(
    db: AsyncSession,
    workspace_id: UUID,
    *,
    now: datetime | None = None,
    limit: int = 50,
    dispatcher_factory: DispatcherFactory = _default_dispatcher_factory,
) -> int:
    reference_now = _aware(now)
    schedule_ids = list(
        (
            await db.scalars(
                select(OmnichannelScheduledMessage.id)
                .where(
                    OmnichannelScheduledMessage.workspace_id == workspace_id,
                    _due_predicate(reference_now),
                )
                .order_by(
                    OmnichannelScheduledMessage.scheduled_at,
                    OmnichannelScheduledMessage.id,
                )
                .limit(max(1, min(limit, 200)))
            )
        ).all()
    )
    processed = 0
    for schedule_id in schedule_ids:
        schedule = await db.scalar(
            select(OmnichannelScheduledMessage)
            .where(
                OmnichannelScheduledMessage.workspace_id == workspace_id,
                OmnichannelScheduledMessage.id == schedule_id,
                _due_predicate(reference_now),
            )
            .with_for_update(skip_locked=True)
        )
        if schedule is None:
            continue
        claim = await _claim(db, schedule, now=reference_now)
        if claim is None:
            continue
        lease_owner, generation = claim
        if await _process_one(
            db,
            workspace_id=workspace_id,
            schedule_id=schedule.id,
            lease_owner=lease_owner,
            generation=generation,
            dispatcher_factory=dispatcher_factory,
        ):
            processed += 1
    return processed


async def _async_process_due(
    *,
    session_factory=WorkerSessionLocal,
    now: datetime | None = None,
    workspace_batch_size: int = WORKSPACE_BATCH_SIZE,
    total_batch_size: int = TOTAL_BATCH_SIZE,
    dispatcher_factory: DispatcherFactory = _default_dispatcher_factory,
    allow_external: bool | None = None,
) -> int:
    if allow_external is None:
        allow_external = external_delivery_allowed()
    if not allow_external:
        logger.info(
            "omnichannel.schedule.external_gate_closed",
            processed_count=0,
            external_actions_executed=0,
        )
        return 0
    reference_now = _aware(now)
    workspace_ids = await _due_workspace_ids(
        session_factory=session_factory,
        now=reference_now,
        batch_size=workspace_batch_size,
    )
    total_limit = max(1, min(total_batch_size, 1000))
    metrics = FanoutMetrics("omnichannel.scheduled_messages", total_limit)
    metrics.discovery_rows = len(workspace_ids)
    metrics.discovered_workspaces = len(workspace_ids)
    processed = 0
    active_workspaces = 0
    for workspace_id in workspace_ids:
        remaining = total_limit - processed
        if remaining <= 0:
            break
        async with session_factory() as db:
            await set_workspace_context(db, workspace_id)
            count = await _process_workspace(
                db,
                workspace_id,
                now=reference_now,
                limit=min(50, remaining),
                dispatcher_factory=dispatcher_factory,
            )
            processed += count
            active_workspaces += int(count > 0)
    metrics.enqueued_workspaces = active_workspaces
    metrics.processed_items = processed
    metrics.emit()
    return processed


def _run_async(coroutine):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


@celery_app.task(name="omnichannel_scheduled_tasks.process_due")
def process_due() -> int:
    return _run_async(_async_process_due())
