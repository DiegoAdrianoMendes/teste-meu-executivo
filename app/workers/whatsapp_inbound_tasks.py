"""Durable recovery for versioned WhatsApp inbound media."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.models.whatsapp import (
    WaMessageDirection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.workspace import Workspace
from app.workers.celery_app import celery_app

MAX_RETRIES_PER_WORKSPACE = 100


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _workspace_id(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("workspace_id must be a valid UUID") from exc


def _retry_is_due(pipeline: dict, *, now: datetime) -> bool:
    retry = pipeline.get("mediaRetry")
    if not isinstance(retry, dict):
        return True
    raw_next_attempt = retry.get("nextAttemptAt")
    if not raw_next_attempt:
        return True
    try:
        next_attempt = datetime.fromisoformat(
            str(raw_next_attempt).replace("Z", "+00:00")
        )
    except ValueError:
        return True
    if next_attempt.tzinfo is None:
        next_attempt = next_attempt.replace(tzinfo=UTC)
    return next_attempt.astimezone(UTC) <= now.astimezone(UTC)


async def _active_workspace_ids(*, session_factory=None) -> list[UUID]:
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as discovery_db:
        return list(
            (
                await discovery_db.scalars(
                    select(Workspace.id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                    )
                    .order_by(Workspace.id)
                )
            ).all()
        )


async def _pending_media_message_ids(
    db,
    *,
    workspace_id: UUID,
    now: datetime,
) -> list[UUID]:
    if db.get_bind().dialect.name == "postgresql":
        media_state = func.jsonb_extract_path_text(
            WhatsAppMessage.inbound_pipeline,
            "mediaStorage",
            "state",
        )
    else:
        media_state = func.json_extract(
            WhatsAppMessage.inbound_pipeline,
            "$.mediaStorage.state",
        )
    rows = (
        await db.execute(
            select(WhatsAppMessage.id, WhatsAppMessage.inbound_pipeline)
            .join(
                WhatsAppContact,
                WhatsAppContact.id == WhatsAppMessage.contact_id,
            )
            .where(
                WhatsAppContact.workspace_id == workspace_id,
                WhatsAppMessage.direction == WaMessageDirection.inbound,
                WhatsAppMessage.media_type.is_not(None),
                media_state == "retry_required",
            )
            .order_by(WhatsAppMessage.timestamp, WhatsAppMessage.id)
            .limit(MAX_RETRIES_PER_WORKSPACE)
        )
    ).all()
    return [
        message_id
        for message_id, inbound_pipeline in rows
        if _retry_is_due(dict(inbound_pipeline or {}), now=now)
    ]


@celery_app.task(name="whatsapp_inbound_tasks.reconcile_media")
def reconcile_media(workspace_id: str | None = None) -> int:
    if workspace_id is None:
        return _run_async(_async_dispatch_media_retries())
    return _run_async(_async_reconcile_workspace_media(workspace_id))


async def _async_dispatch_media_retries(*, session_factory=None) -> int:
    if not external_delivery_allowed():
        logger.info("whatsapp_inbound.media_retry_dispatch_blocked_by_klm_gate")
        return 0
    workspace_ids = await _active_workspace_ids(session_factory=session_factory)
    for workspace_id in workspace_ids:
        reconcile_media.apply_async(kwargs={"workspace_id": str(workspace_id)})
    return len(workspace_ids)


async def _async_reconcile_workspace_media(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    service_factory=None,
    uploader=None,
) -> int:
    resolved_workspace_id = _workspace_id(workspace_id)
    if not external_delivery_allowed():
        logger.info(
            "whatsapp_inbound.media_retry_blocked_by_klm_gate",
            workspace_id=str(resolved_workspace_id),
        )
        return 0

    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        message_ids = await _pending_media_message_ids(
            db,
            workspace_id=resolved_workspace_id,
            now=datetime.now(UTC),
        )
        if service_factory is None:
            from app.services.whatsapp_service import WhatsAppService

            service_factory = WhatsAppService
        service = service_factory(db)
        webhook_service = getattr(service, "webhook_service", service)
        completed = 0
        for message_id in message_ids:
            try:
                completed += int(
                    await webhook_service.retry_inbound_media(
                        workspace_id=resolved_workspace_id,
                        message_id=message_id,
                        uploader=uploader,
                    )
                )
            except Exception as exc:
                await db.rollback()
                logger.error(
                    "whatsapp_inbound.media_retry_failed",
                    workspace_id=str(resolved_workspace_id),
                    message_id=str(message_id),
                    error_type=type(exc).__name__,
                )
        return completed
