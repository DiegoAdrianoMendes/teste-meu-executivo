"""Durable, fenced delivery worker for social action intents."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.post_commit import queue_realtime_event
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.klm_operations import OmnichannelThread
from app.models.omnichannel_v6 import (
    OmnichannelActionIntent,
    OmnichannelActionIntentEvent,
    OmnichannelChannelConnection,
    OmnichannelChannelEvent,
)
from app.models.workspace import Workspace
from app.services.messaging_cadence_service import MessagingCadenceService
from app.services.meta_policy_gate import META_CHANNELS, MetaPolicyGate
from app.services.omnichannel_service_window import latest_inbound_at_for_route
from app.services.social_action_delivery import (
    SocialActionDispatcher,
    SocialActionDispatchError,
    SocialActionDispatchReceipt,
    SocialActionDispatchRequest,
)
from app.workers.celery_app import celery_app
from app.workers.fanout import FanoutMetrics, bounded_batch_size

ACTION_LEASE = timedelta(minutes=5)
WORKSPACE_BATCH_SIZE = 64
TOTAL_BATCH_SIZE = 200


class SocialActionFencedError(RuntimeError):
    """A stale worker attempted to settle a newer lease generation."""


class _UnconfiguredDispatcher:
    supports_idempotency = False

    async def dispatch(
        self, request: SocialActionDispatchRequest
    ) -> SocialActionDispatchReceipt:
        raise SocialActionDispatchError(
            "O adaptador oficial desta ação social ainda não foi conectado.",
            code="adapter_not_configured",
            retryable=False,
        )


DispatcherFactory = Callable[[OmnichannelChannelConnection], SocialActionDispatcher]


def _default_dispatcher_factory(
    connection: OmnichannelChannelConnection,
) -> SocialActionDispatcher:
    return _UnconfiguredDispatcher()


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _payload_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _retry_at(intent_id: UUID, attempts: int, *, now: datetime) -> datetime:
    exponent = max(0, min(attempts - 1, 8))
    base_seconds = min(30 * (2**exponent), 1800)
    jitter = int(hashlib.sha256(str(intent_id).encode()).hexdigest()[:2], 16) % 16
    return now + timedelta(seconds=base_seconds + jitter)


def _due_predicate(now: datetime):
    ready = and_(
        OmnichannelActionIntent.state.in_(["queued", "retrying"]),
        or_(
            OmnichannelActionIntent.scheduled_at.is_(None),
            OmnichannelActionIntent.scheduled_at <= now,
        ),
        or_(
            OmnichannelActionIntent.next_attempt_at.is_(None),
            OmnichannelActionIntent.next_attempt_at <= now,
        ),
    )
    abandoned = and_(
        OmnichannelActionIntent.state == "processing",
        or_(
            OmnichannelActionIntent.lease_expires_at.is_(None),
            OmnichannelActionIntent.lease_expires_at <= now,
        ),
    )
    return or_(ready, abandoned)


def _append_event(
    db: AsyncSession,
    intent: OmnichannelActionIntent,
    *,
    provider: str,
    event_type: str,
    previous_state: str,
    next_state: str,
    idempotency_key: str,
    payload: dict[str, Any],
    occurred_at: datetime,
    external_action_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        OmnichannelActionIntentEvent(
            workspace_id=intent.workspace_id,
            action_intent_id=intent.id,
            thread_id=intent.thread_id,
            connection_id=intent.connection_id,
            provider=provider,
            action_type=intent.action_type,
            event_type=event_type,
            previous_state=previous_state,
            next_state=next_state,
            idempotency_key=idempotency_key,
            payload_sha256=_payload_hash(payload),
            lease_owner=intent.lease_owner,
            lease_generation=intent.lease_generation,
            external_action_id=external_action_id,
            error_code=error_code,
            error_message=error_message,
            occurred_at=occurred_at,
            event_metadata=metadata or {},
        )
    )


async def _due_workspace_ids(
    *,
    session_factory=WorkerSessionLocal,
    now: datetime | None = None,
    batch_size: int = WORKSPACE_BATCH_SIZE,
) -> list[UUID]:
    reference = _aware(now)
    async with session_factory() as db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(db, family="omnichannel_action")
        return list(
            (
                await db.scalars(
                    select(OmnichannelActionIntent.workspace_id)
                    .join(Workspace, Workspace.id == OmnichannelActionIntent.workspace_id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                        _due_predicate(reference),
                    )
                    .group_by(OmnichannelActionIntent.workspace_id)
                    .order_by(func.min(OmnichannelActionIntent.created_at))
                    .limit(bounded_batch_size(batch_size))
                )
            ).all()
        )


async def _claim(
    db: AsyncSession,
    intent: OmnichannelActionIntent,
    *,
    provider: str,
    now: datetime,
) -> tuple[str, int] | None:
    if (
        intent.state == "processing"
        and intent.delivery_attempted_at
        and not intent.provider_supports_idempotency
    ):
        previous = intent.state
        intent.state = "delivery_uncertain"
        intent.delivery_uncertain_at = now
        intent.completed_at = now
        intent.last_error_code = "expired_unkeyed_attempt"
        intent.last_error_message = (
            "A ação pode ter sido aceita pelo provedor; reconciliação humana obrigatória."
        )
        _append_event(
            db,
            intent,
            provider=provider,
            event_type="delivery_uncertain",
            previous_state=previous,
            next_state=intent.state,
            idempotency_key=f"action-uncertain:{intent.id}:{intent.lease_generation}",
            payload={"reason": intent.last_error_code},
            occurred_at=now,
            error_code=intent.last_error_code,
            error_message=intent.last_error_message,
        )
        intent.lease_owner = None
        intent.lease_expires_at = None
        await db.commit()
        return None
    previous = intent.state
    owner = str(uuid4())
    generation = int(intent.lease_generation or 0) + 1
    intent.state = "processing"
    intent.lease_owner = owner
    intent.lease_generation = generation
    intent.lease_expires_at = now + ACTION_LEASE
    intent.last_error_code = None
    intent.last_error_message = None
    _append_event(
        db,
        intent,
        provider=provider,
        event_type="claimed",
        previous_state=previous,
        next_state="processing",
        idempotency_key=f"action-claim:{intent.id}:{generation}",
        payload={"leaseGeneration": generation},
        occurred_at=now,
        metadata={"leaseExpiresAt": intent.lease_expires_at.isoformat()},
    )
    await db.commit()
    return owner, generation


async def _claimed_intent(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    intent_id: UUID,
    lease_owner: str,
    generation: int,
    lock: bool = False,
) -> OmnichannelActionIntent | None:
    query = select(OmnichannelActionIntent).where(
        OmnichannelActionIntent.workspace_id == workspace_id,
        OmnichannelActionIntent.id == intent_id,
        OmnichannelActionIntent.state == "processing",
        OmnichannelActionIntent.lease_owner == lease_owner,
        OmnichannelActionIntent.lease_generation == generation,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


async def _enforce_meta_policy_gate(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    intent: OmnichannelActionIntent,
    connection: OmnichannelChannelConnection,
) -> None:
    """Aplica o Meta Policy Gate ANTES do dispatcher, para canais Meta.

    Exceção documentada (PLANO seção 1 item 1 / seção 4-I1): quando a decisão
    nega com `requires="template"` (só acontece em WhatsApp), deixa passar — o
    dispatcher oficial do WhatsApp já resolve template e não deve ser duplicado
    aqui.
    """
    channel = (connection.channel or "").strip().lower()
    if channel not in META_CHANNELS:
        return
    now = datetime.now(UTC)
    thread = await db.get(OmnichannelThread, intent.thread_id)
    last_inbound_at = (
        await latest_inbound_at_for_route(
            db,
            workspace_id=workspace_id,
            thread=thread,
            channel=connection.channel,
            connection_id=connection.id,
        )
        if thread is not None
        else None
    )
    occurred_at_of_source: datetime | None = None
    if intent.action_type == "private_reply" and intent.event_id is not None:
        source_event = await db.get(OmnichannelChannelEvent, intent.event_id)
        if source_event is not None:
            occurred_at_of_source = source_event.occurred_at
    automated_hourly_count: int | None = None
    if channel == "instagram":
        automated_hourly_count = await MessagingCadenceService(
            db
        ).count_recent_agent_messages_for_connection(
            workspace_id, connection.id, now=now
        )
    decision = MetaPolicyGate.evaluate(
        channel,
        last_inbound_at=last_inbound_at,
        now=now,
        author_type="agent",
        action_type=intent.action_type,
        occurred_at_of_source=occurred_at_of_source,
        automated_hourly_count=automated_hourly_count,
    )
    if decision.allowed:
        return
    if channel == "whatsapp" and decision.requires == "template":
        # O dispatcher oficial do WhatsApp já resolve template fora da janela;
        # não duplicar a regra dele aqui.
        return
    raise SocialActionDispatchError(
        decision.reason,
        code=f"policy_gate_{decision.reason_code}",
        retryable=False,
    )


async def _prepare_attempt(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    intent_id: UUID,
    lease_owner: str,
    generation: int,
    dispatcher_factory: DispatcherFactory,
) -> tuple[SocialActionDispatchRequest, SocialActionDispatcher] | None:
    intent = await _claimed_intent(
        db,
        workspace_id=workspace_id,
        intent_id=intent_id,
        lease_owner=lease_owner,
        generation=generation,
    )
    if intent is None:
        raise SocialActionFencedError("Lease substituído antes da tentativa.")
    if not external_delivery_allowed():
        previous = intent.state
        intent.state = "blocked_external"
        intent.failure_reason = "Ação externa bloqueada pelo gate do Meu Executivo."
        intent.lease_owner = None
        intent.lease_expires_at = None
        _append_event(
            db,
            intent,
            provider="blocked",
            event_type="blocked_external",
            previous_state=previous,
            next_state=intent.state,
            idempotency_key=f"action-blocked:{intent.id}:{generation}",
            payload={"externalRequests": 0},
            occurred_at=datetime.now(UTC),
        )
        await db.commit()
        return None
    if intent.connection_id is None:
        raise SocialActionDispatchError(
            "A intenção não possui uma conexão oficial.",
            code="route_incomplete",
            retryable=False,
        )
    connection = await db.scalar(
        select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.workspace_id == workspace_id,
            OmnichannelChannelConnection.id == intent.connection_id,
        )
    )
    if connection is None:
        raise SocialActionDispatchError(
            "A conexão da intenção não foi encontrada.",
            code="route_unavailable",
            retryable=False,
        )
    if connection.status not in {"connected", "active", "configured_local"}:
        raise SocialActionDispatchError(
            "A conexão oficial não está ativa.",
            code="connection_inactive",
            retryable=True,
        )
    capability = _mapping(connection.capabilities).get(intent.required_capability) or {}
    if not capability.get("supported") or not capability.get("enabled"):
        raise SocialActionDispatchError(
            "A capacidade necessária está desativada.",
            code="capability_disabled",
            retryable=False,
        )
    await _enforce_meta_policy_gate(
        db,
        workspace_id=workspace_id,
        intent=intent,
        connection=connection,
    )
    dispatcher = dispatcher_factory(connection)
    now = datetime.now(UTC)
    transition = await db.execute(
        update(OmnichannelActionIntent)
        .where(
            OmnichannelActionIntent.workspace_id == workspace_id,
            OmnichannelActionIntent.id == intent.id,
            OmnichannelActionIntent.state == "processing",
            OmnichannelActionIntent.lease_owner == lease_owner,
            OmnichannelActionIntent.lease_generation == generation,
            OmnichannelActionIntent.lease_expires_at > now,
        )
        .values(
            delivery_attempted_at=now,
            attempt_count=OmnichannelActionIntent.attempt_count + 1,
            provider_supports_idempotency=dispatcher.supports_idempotency,
        )
        .execution_options(synchronize_session=False)
    )
    if transition.rowcount != 1:
        await db.rollback()
        raise SocialActionFencedError("Lease expirado antes da chamada externa.")
    intent.delivery_attempted_at = now
    intent.attempt_count += 1
    intent.provider_supports_idempotency = dispatcher.supports_idempotency
    _append_event(
        db,
        intent,
        provider=connection.provider,
        event_type="attempt_started",
        previous_state="processing",
        next_state="processing",
        idempotency_key=f"action-attempt:{intent.id}:{generation}",
        payload={
            "attempt": intent.attempt_count,
            "providerSupportsIdempotency": dispatcher.supports_idempotency,
        },
        occurred_at=now,
    )
    await db.commit()
    return (
        SocialActionDispatchRequest(
            workspace_id=workspace_id,
            action_intent_id=intent.id,
            connection_id=connection.id,
            provider=connection.provider,
            connection_key=connection.connection_key,
            action_type=intent.action_type,
            target_external_id=intent.target_external_id,
            body=intent.body,
            action_payload=dict(intent.action_payload or {}),
            idempotency_key=f"social-action:{intent.id}",
        ),
        dispatcher,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def _settle_success(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    intent_id: UUID,
    lease_owner: str,
    generation: int,
    request: SocialActionDispatchRequest,
    receipt: SocialActionDispatchReceipt,
) -> None:
    intent = await _claimed_intent(
        db,
        workspace_id=workspace_id,
        intent_id=intent_id,
        lease_owner=lease_owner,
        generation=generation,
        lock=True,
    )
    if intent is None:
        raise SocialActionFencedError("Resultado chegou após a troca do lease.")
    if receipt.provider != request.provider:
        raise SocialActionDispatchError(
            "O recibo retornou um provedor diferente da rota.",
            code="provider_receipt_mismatch",
            retryable=False,
            outcome_uncertain=True,
        )
    now = _aware(receipt.accepted_at)
    intent.state = "accepted"
    intent.provider_action_id = receipt.external_action_id
    intent.completed_at = now
    intent.next_attempt_at = None
    intent.delivery_uncertain_at = None
    intent.failure_reason = None
    intent.last_error_code = None
    intent.last_error_message = None
    intent.provider_receipt = {
        "provider": receipt.provider,
        "externalActionId": receipt.external_action_id,
        "providerStatus": receipt.provider_status,
        "acceptedAt": now.isoformat(),
        "externalRequests": 1,
        "payload": receipt.payload,
    }
    _append_event(
        db,
        intent,
        provider=receipt.provider,
        event_type="accepted",
        previous_state="processing",
        next_state="accepted",
        idempotency_key=f"action-accepted:{intent.id}:{generation}",
        payload=intent.provider_receipt,
        occurred_at=now,
        external_action_id=receipt.external_action_id,
    )
    intent.lease_owner = None
    intent.lease_expires_at = None
    queue_realtime_event(
        db,
        workspace_id=workspace_id,
        event_type="omnichannel.action.updated",
        data={"intentId": str(intent.id), "threadId": str(intent.thread_id), "state": intent.state},
    )
    await db.commit()


async def _settle_failure(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    intent_id: UUID,
    lease_owner: str,
    generation: int,
    provider: str,
    error: SocialActionDispatchError,
) -> str:
    intent = await _claimed_intent(
        db,
        workspace_id=workspace_id,
        intent_id=intent_id,
        lease_owner=lease_owner,
        generation=generation,
        lock=True,
    )
    if intent is None:
        raise SocialActionFencedError("Falha chegou após a troca do lease.")
    now = datetime.now(UTC)
    previous = intent.state
    if error.outcome_uncertain:
        next_state = "delivery_uncertain"
        intent.delivery_uncertain_at = now
        intent.completed_at = now
    elif error.retryable and intent.attempt_count < intent.max_attempts:
        next_state = "retrying"
        intent.next_attempt_at = _retry_at(intent.id, intent.attempt_count, now=now)
    else:
        next_state = "failed"
        intent.completed_at = now
    intent.state = next_state
    intent.last_error_code = error.code
    intent.last_error_message = str(error)
    intent.failure_reason = str(error)
    _append_event(
        db,
        intent,
        provider=provider,
        event_type=next_state,
        previous_state=previous,
        next_state=next_state,
        idempotency_key=f"action-{next_state}:{intent.id}:{generation}",
        payload={"retryable": error.retryable, "outcomeUncertain": error.outcome_uncertain},
        occurred_at=now,
        error_code=error.code,
        error_message=str(error),
    )
    intent.lease_owner = None
    intent.lease_expires_at = None
    queue_realtime_event(
        db,
        workspace_id=workspace_id,
        event_type="omnichannel.action.updated",
        data={"intentId": str(intent.id), "threadId": str(intent.thread_id), "state": next_state},
    )
    await db.commit()
    return next_state


async def process_action_intent(
    workspace_id: UUID,
    intent_id: UUID,
    *,
    session_factory=WorkerSessionLocal,
    dispatcher_factory: DispatcherFactory = _default_dispatcher_factory,
    now: datetime | None = None,
) -> str:
    reference = _aware(now)
    async with session_factory() as db:
        await set_workspace_context(db, workspace_id)
        intent = await db.scalar(
            select(OmnichannelActionIntent)
            .where(
                OmnichannelActionIntent.workspace_id == workspace_id,
                OmnichannelActionIntent.id == intent_id,
                _due_predicate(reference),
            )
            .with_for_update()
        )
        if intent is None:
            return "not_due"
        connection = None
        if intent.connection_id:
            connection = await db.scalar(
                select(OmnichannelChannelConnection).where(
                    OmnichannelChannelConnection.workspace_id == workspace_id,
                    OmnichannelChannelConnection.id == intent.connection_id,
                )
            )
        provider = connection.provider if connection else "unresolved"
        claim = await _claim(db, intent, provider=provider, now=reference)
        if claim is None:
            return "delivery_uncertain"
        owner, generation = claim
        try:
            prepared = await _prepare_attempt(
                db,
                workspace_id=workspace_id,
                intent_id=intent_id,
                lease_owner=owner,
                generation=generation,
                dispatcher_factory=dispatcher_factory,
            )
            if prepared is None:
                return "blocked_external"
            request, dispatcher = prepared
            receipt = await dispatcher.dispatch(request)
            await _settle_success(
                db,
                workspace_id=workspace_id,
                intent_id=intent_id,
                lease_owner=owner,
                generation=generation,
                request=request,
                receipt=receipt,
            )
            return "accepted"
        except SocialActionDispatchError as error:
            next_state = await _settle_failure(
                db,
                workspace_id=workspace_id,
                intent_id=intent_id,
                lease_owner=owner,
                generation=generation,
                provider=provider,
                error=error,
            )
            return next_state


async def process_workspace_actions(
    workspace_id: UUID,
    *,
    session_factory=WorkerSessionLocal,
    dispatcher_factory: DispatcherFactory = _default_dispatcher_factory,
    now: datetime | None = None,
    batch_size: int = TOTAL_BATCH_SIZE,
) -> dict[str, int]:
    reference = _aware(now)
    async with session_factory() as db:
        await set_workspace_context(db, workspace_id)
        ids = list(
            (
                await db.scalars(
                    select(OmnichannelActionIntent.id)
                    .where(
                        OmnichannelActionIntent.workspace_id == workspace_id,
                        _due_predicate(reference),
                    )
                    .order_by(
                        OmnichannelActionIntent.scheduled_at.asc().nullsfirst(),
                        OmnichannelActionIntent.created_at,
                    )
                    .limit(bounded_batch_size(batch_size))
                )
            ).all()
        )
    metrics: dict[str, int] = {}
    for intent_id in ids:
        outcome = await process_action_intent(
            workspace_id,
            intent_id,
            session_factory=session_factory,
            dispatcher_factory=dispatcher_factory,
            now=reference,
        )
        metrics[outcome] = metrics.get(outcome, 0) + 1
    return metrics


@celery_app.task(name="omnichannel_action_tasks.process_workspace")
def process_workspace_actions_task(workspace_id: str) -> dict[str, int]:
    return asyncio.run(process_workspace_actions(UUID(workspace_id)))


@celery_app.task(name="omnichannel_action_tasks.process_due")
def process_due_actions_task() -> dict[str, int]:
    async def _run() -> dict[str, int]:
        limit = bounded_batch_size(WORKSPACE_BATCH_SIZE)
        workspace_ids = await _due_workspace_ids(batch_size=limit)
        metrics = FanoutMetrics(
            event="omnichannel.actions.due",
            batch_limit=limit,
            discovery_rows=len(workspace_ids),
            discovered_workspaces=len(workspace_ids),
        )
        for workspace_id in workspace_ids:
            try:
                process_workspace_actions_task.delay(str(workspace_id))
                metrics.enqueued_workspaces += 1
            except Exception as error:
                metrics.publish_failures += 1
                logger.warning(
                    "omnichannel_action_tasks.dispatch_failed",
                    workspace_id=str(workspace_id),
                    error_type=safe_error_type(error),
                )
        metrics.emit()
        return {
            "dispatched": metrics.enqueued_workspaces,
            "failed": metrics.publish_failures,
        }

    return asyncio.run(_run())
