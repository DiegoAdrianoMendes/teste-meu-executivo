"""Entrega durável e minimizada das notificações externas do SAC."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from html import escape
from uuid import UUID

from sqlalchemy import or_, select

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.support_ticket import (
    SupportDeliveryChannel,
    SupportDeliveryStatus,
    SupportNotificationOutbox,
    SupportTicket,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.services.email_service import send_operational_alert_email
from app.workers.celery_app import celery_app
from app.workers.fanout import bounded_batch_size

SUPPORT_NOTIFICATION_BATCH_SIZE = 50
SUPPORT_NOTIFICATION_MAX_ATTEMPTS = 5
SUPPORT_NOTIFICATION_LEASE = timedelta(minutes=5)
_PROCESSING_MARKER = "processing"

_STATUS_LABELS = {
    "new": "Novo",
    "triage": "Em triagem",
    "in_progress": "Em atendimento",
    "waiting_customer": "Aguardando cliente",
    "waiting_internal": "Aguardando equipe interna",
    "validating": "Em validação",
    "resolved": "Resolvido",
    "closed": "Encerrado",
    "cancelled": "Cancelado",
}


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
        raise ValueError("workspace_id deve ser um UUID válido") from exc


async def _pending_workspace_ids(
    *,
    session_factory=None,
    batch_size: int = SUPPORT_NOTIFICATION_BATCH_SIZE,
) -> list[UUID]:
    session_maker = session_factory or AsyncSessionLocal
    limit = bounded_batch_size(batch_size)
    async with session_maker() as db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(db, family="support_notification")
        return list(
            (
                await db.scalars(
                    select(SupportNotificationOutbox.workspace_id)
                    .where(
                        SupportNotificationOutbox.channel
                        == SupportDeliveryChannel.email,
                        SupportNotificationOutbox.status.in_(
                            (
                                SupportDeliveryStatus.pending,
                                SupportDeliveryStatus.failed,
                            )
                        ),
                        SupportNotificationOutbox.attempts
                        < SUPPORT_NOTIFICATION_MAX_ATTEMPTS,
                    )
                    .distinct()
                    .limit(limit)
                )
            ).all()
        )


async def _claim_next_delivery(
    db,
    *,
    workspace_id: UUID,
    now: datetime,
) -> UUID | None:
    stale_before = now - SUPPORT_NOTIFICATION_LEASE
    delivery = await db.scalar(
        select(SupportNotificationOutbox)
        .where(
            SupportNotificationOutbox.workspace_id == workspace_id,
            SupportNotificationOutbox.channel == SupportDeliveryChannel.email,
            SupportNotificationOutbox.status.in_(
                (
                    SupportDeliveryStatus.pending,
                    SupportDeliveryStatus.failed,
                )
            ),
            SupportNotificationOutbox.attempts < SUPPORT_NOTIFICATION_MAX_ATTEMPTS,
            or_(
                SupportNotificationOutbox.last_error_code.is_(None),
                SupportNotificationOutbox.last_error_code != _PROCESSING_MARKER,
                SupportNotificationOutbox.updated_at <= stale_before,
            ),
        )
        .order_by(
            SupportNotificationOutbox.created_at,
            SupportNotificationOutbox.id,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if delivery is None:
        return None
    delivery.status = SupportDeliveryStatus.failed
    delivery.attempts += 1
    delivery.last_error_code = _PROCESSING_MARKER
    await db.commit()
    return delivery.id


async def _deliver_claim(
    db,
    *,
    workspace_id: UUID,
    delivery_id: UUID,
    now: datetime,
) -> bool:
    delivery = await db.get(SupportNotificationOutbox, delivery_id)
    if (
        delivery is None
        or delivery.workspace_id != workspace_id
        or delivery.last_error_code != _PROCESSING_MARKER
    ):
        return False

    ticket = await db.get(SupportTicket, delivery.ticket_id)
    user = await db.get(User, delivery.recipient_user_id)
    workspace = await db.get(Workspace, workspace_id)
    if (
        ticket is None
        or user is None
        or workspace is None
        or not user.is_active
        or not user.email
    ):
        delivery.status = SupportDeliveryStatus.disabled
        delivery.last_error_code = "recipient_unavailable"
        await db.commit()
        return False

    status_label = _STATUS_LABELS.get(ticket.status.value, ticket.status.value)
    secure_url = (
        f"{settings.FRONTEND_URL.rstrip('/')}/app"
        f"?support={ticket.id}"
    )
    try:
        delivered = await send_operational_alert_email(
            to_email=user.email,
            subject=f"Chamado {ticket.public_number}: {status_label}",
            workspace_name=escape(workspace.name),
            body=(
                f"O chamado {escape(ticket.public_number)} foi atualizado para "
                f"{escape(status_label)}."
            ),
            cta_label="Acompanhar chamado",
            cta_url=secure_url,
            meta_lines=[f"Situação: {escape(status_label)}"],
        )
    except Exception as error:
        logger.warning(
            "support_notification.delivery_failed",
            delivery_id=str(delivery.id),
            error_type=safe_error_type(error),
        )
        delivered = False

    delivery.status = (
        SupportDeliveryStatus.sent if delivered else SupportDeliveryStatus.failed
    )
    delivery.last_error_code = None if delivered else "provider_rejected"
    delivery.delivered_at = now if delivered else None
    await db.commit()
    return delivered


async def _async_process_workspace(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = SUPPORT_NOTIFICATION_BATCH_SIZE,
) -> dict[str, int]:
    resolved_workspace_id = _workspace_id(workspace_id)
    if not external_delivery_allowed():
        logger.info(
            "support_notification.delivery_blocked_by_klm_gate",
            workspace_id=str(resolved_workspace_id),
        )
        return {"processed": 0, "sent": 0}

    reference_now = now or datetime.now(UTC)
    session_maker = session_factory or AsyncSessionLocal
    processed = 0
    sent = 0
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        for _ in range(bounded_batch_size(batch_size)):
            delivery_id = await _claim_next_delivery(
                db,
                workspace_id=resolved_workspace_id,
                now=reference_now,
            )
            if delivery_id is None:
                break
            processed += 1
            sent += int(
                await _deliver_claim(
                    db,
                    workspace_id=resolved_workspace_id,
                    delivery_id=delivery_id,
                    now=reference_now,
                )
            )
    return {"processed": processed, "sent": sent}


async def _async_dispatch_due(
    *,
    session_factory=None,
    batch_size: int = SUPPORT_NOTIFICATION_BATCH_SIZE,
) -> int:
    if not external_delivery_allowed():
        logger.info("support_notification.dispatch_blocked_by_klm_gate")
        return 0
    workspace_ids = await _pending_workspace_ids(
        session_factory=session_factory,
        batch_size=batch_size,
    )
    for workspace_id in workspace_ids:
        process_support_notification_workspace.apply_async(
            args=[str(workspace_id)],
            countdown=0,
        )
    return len(workspace_ids)


@celery_app.task(name="support_notification_tasks.process_workspace")
def process_support_notification_workspace(workspace_id: str) -> dict[str, int]:
    return _run_async(_async_process_workspace(workspace_id))


@celery_app.task(name="support_notification_tasks.process_due")
def process_due_support_notifications() -> int:
    return _run_async(_async_dispatch_due())


__all__ = [
    "_async_dispatch_due",
    "_async_process_workspace",
    "process_due_support_notifications",
    "process_support_notification_workspace",
]
