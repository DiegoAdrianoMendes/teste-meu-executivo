"""Durable post-commit delivery for meeting operational notifications."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, or_, select

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.post_commit import queue_realtime_event
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.models.meeting import (
    Meeting,
    MeetingNotificationOutbox,
    MeetingNotificationStatus,
    MeetingStatus,
)
from app.models.notification import Notification, NotificationType
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.operational_alert_service import OperationalAlertService
from app.workers.celery_app import celery_app
from app.workers.fanout import bounded_batch_size

MEETING_NOTIFICATION_BATCH_SIZE = 50
MEETING_NOTIFICATION_MAX_ATTEMPTS = 5
MEETING_NOTIFICATION_LEASE = timedelta(minutes=5)
WORKSPACE_SCAN_PAGE_SIZE = 256


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


def _workspace_cursor(
    created_at: datetime | str | None,
    workspace_id: UUID | str | None,
) -> tuple[datetime, UUID] | None:
    if created_at is None and workspace_id is None:
        return None
    if created_at is None or workspace_id is None:
        raise ValueError("cursor de workspace incompleto")
    try:
        resolved_created_at = (
            created_at if isinstance(created_at, datetime) else datetime.fromisoformat(created_at)
        )
    except ValueError as exc:
        raise ValueError("cursor_created_at deve ser um timestamp ISO válido") from exc
    return resolved_created_at, _workspace_id(workspace_id)


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _due_predicate(now: datetime):
    stale_before = now - MEETING_NOTIFICATION_LEASE
    return and_(
        MeetingNotificationOutbox.attempts < MEETING_NOTIFICATION_MAX_ATTEMPTS,
        MeetingNotificationOutbox.available_at <= now,
        or_(
            MeetingNotificationOutbox.status.in_(
                (
                    MeetingNotificationStatus.pending,
                    MeetingNotificationStatus.failed,
                )
            ),
            and_(
                MeetingNotificationOutbox.status == MeetingNotificationStatus.processing,
                MeetingNotificationOutbox.claimed_at.is_not(None),
                MeetingNotificationOutbox.claimed_at <= stale_before,
            ),
        ),
    )


def _terminalizable_predicate(now: datetime):
    stale_before = now - MEETING_NOTIFICATION_LEASE
    return and_(
        MeetingNotificationOutbox.attempts >= MEETING_NOTIFICATION_MAX_ATTEMPTS,
        or_(
            MeetingNotificationOutbox.status == MeetingNotificationStatus.failed,
            and_(
                MeetingNotificationOutbox.status == MeetingNotificationStatus.processing,
                or_(
                    MeetingNotificationOutbox.claimed_at.is_(None),
                    MeetingNotificationOutbox.claimed_at <= stale_before,
                ),
            ),
        ),
    )


async def _scan_active_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = MEETING_NOTIFICATION_BATCH_SIZE,
    after_created_at: datetime | str | None = None,
    after_workspace_id: UUID | str | None = None,
) -> tuple[list[UUID], tuple[datetime, UUID] | None]:
    """Find due tenants without ever querying the FORCE-RLS outbox globally."""

    reference_now = now or datetime.now(UTC)
    session_maker = session_factory or AsyncSessionLocal
    limit = bounded_batch_size(batch_size)
    selected: list[UUID] = []
    cursor = _workspace_cursor(after_created_at, after_workspace_id)
    while len(selected) < limit:
        predicates = [
            Workspace.status == WorkspaceStatus.active,
            Workspace.is_active.is_(True),
            Workspace.deleted_at.is_(None),
        ]
        if cursor is not None:
            created_at, workspace_id = cursor
            predicates.append(
                or_(
                    Workspace.created_at > created_at,
                    and_(
                        Workspace.created_at == created_at,
                        Workspace.id > workspace_id,
                    ),
                )
            )
        async with session_maker() as discovery_db:
            candidates = list(
                (
                    await discovery_db.execute(
                        select(Workspace.id, Workspace.created_at)
                        .where(*predicates)
                        .order_by(Workspace.created_at, Workspace.id)
                        .limit(WORKSPACE_SCAN_PAGE_SIZE)
                    )
                ).all()
            )
        if not candidates:
            break
        for index, (workspace_id, created_at) in enumerate(candidates):
            async with session_maker() as tenant_db:
                await set_workspace_context(tenant_db, workspace_id)
                due_id = await tenant_db.scalar(
                    select(MeetingNotificationOutbox.id)
                    .where(
                        MeetingNotificationOutbox.workspace_id == workspace_id,
                        or_(
                            _due_predicate(reference_now),
                            _terminalizable_predicate(reference_now),
                        ),
                    )
                    .limit(1)
                )
                await tenant_db.rollback()
            cursor = created_at, workspace_id
            if due_id is not None:
                selected.append(workspace_id)
                if len(selected) >= limit:
                    has_more = (
                        index < len(candidates) - 1 or len(candidates) == WORKSPACE_SCAN_PAGE_SIZE
                    )
                    return selected, cursor if has_more else None
        if len(candidates) < WORKSPACE_SCAN_PAGE_SIZE:
            return selected, None
    return selected, None


async def _active_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = MEETING_NOTIFICATION_BATCH_SIZE,
) -> list[UUID]:
    selected, _next_cursor = await _scan_active_workspace_ids(
        session_factory=session_factory,
        now=now,
        batch_size=batch_size,
    )
    return selected


async def _claim_next(
    db,
    *,
    workspace_id: UUID,
    now: datetime,
) -> tuple[UUID, UUID] | None:
    # PostgreSQL tenant context is transaction-local. Every worker commit
    # clears it, so each claim starts by re-establishing the scope.
    await set_workspace_context(db, workspace_id)
    row = await db.scalar(
        select(MeetingNotificationOutbox)
        .where(
            MeetingNotificationOutbox.workspace_id == workspace_id,
            _due_predicate(now),
        )
        .order_by(
            MeetingNotificationOutbox.created_at,
            MeetingNotificationOutbox.id,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row is None:
        return None
    token = uuid4()
    row.status = MeetingNotificationStatus.processing
    row.attempts += 1
    row.claimed_at = now
    row.claim_token = token
    row.last_error_code = None
    await db.commit()
    return row.id, token


def _next_retry_at(now: datetime, attempts: int) -> datetime:
    delay_seconds = min(300, 5 * (2 ** max(0, attempts - 1)))
    return now + timedelta(seconds=delay_seconds)


async def _active_alert_recipient(
    db,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
) -> bool:
    if user_id is None:
        return False
    return (
        await db.scalar(
            select(WorkspaceMembership.id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        is not None
    )


async def _record_exhausted_alert(
    db,
    *,
    row: MeetingNotificationOutbox,
    meeting: Meeting | None,
    previous_error_code: str | None,
) -> None:
    row.status = MeetingNotificationStatus.disabled
    row.last_error_code = "max_attempts_exhausted"
    row.claim_token = None
    row.claimed_at = None
    logger.error(
        "meeting_notification.max_attempts_exhausted",
        workspace_id=str(row.workspace_id),
        meeting_id=str(row.meeting_id) if row.meeting_id else None,
        outbox_id=str(row.id),
        attempts=row.attempts,
        previous_error_code=previous_error_code,
    )
    if meeting is None or not await _active_alert_recipient(
        db,
        workspace_id=row.workspace_id,
        user_id=meeting.responsible_user_id,
    ):
        return
    notification_id = uuid5(
        NAMESPACE_URL,
        f"klm:meeting-notification-exhausted:{row.id}",
    )
    if await db.get(Notification, notification_id) is not None:
        return
    notification = Notification(
        id=notification_id,
        user_id=meeting.responsible_user_id,
        workspace_id=row.workspace_id,
        matter_id=meeting.matter_id,
        title="Falha persistente ao notificar compromisso",
        message=(
            f"A notificação do compromisso '{meeting.title}' esgotou "
            "as tentativas automáticas e requer revisão."
        ),
        notification_type=NotificationType.urgent,
        is_read=False,
    )
    db.add(notification)
    await db.flush()
    queue_realtime_event(
        db,
        workspace_id=row.workspace_id,
        event_type="notification.meeting_delivery_exhausted.created",
        data={
            "notificationId": str(notification.id),
            "meetingId": str(meeting.id),
            "outboxId": str(row.id),
            "userId": str(meeting.responsible_user_id),
        },
    )


async def _settle_failed(
    db,
    *,
    row: MeetingNotificationOutbox,
    meeting: Meeting | None,
    now: datetime,
    error_code: str,
) -> None:
    await set_workspace_context(db, row.workspace_id)
    if row.attempts >= MEETING_NOTIFICATION_MAX_ATTEMPTS:
        await _record_exhausted_alert(
            db,
            row=row,
            meeting=meeting,
            previous_error_code=error_code,
        )
        await db.commit()
        return
    row.status = MeetingNotificationStatus.failed
    row.last_error_code = error_code[:80]
    row.available_at = _next_retry_at(now, row.attempts)
    row.claim_token = None
    row.claimed_at = None
    await db.commit()


async def _terminalize_exhausted(
    db,
    *,
    workspace_id: UUID,
    now: datetime,
) -> int:
    """Move historical max-attempt rows to an explicit terminal state.

    Identity is read without a row lock, then the aggregate is locked in the
    same Meeting -> outbox order used by domain mutations. The outbox predicate
    is revalidated after both locks are held.
    """

    terminalized = 0
    while True:
        await set_workspace_context(db, workspace_id)
        identity = (
            await db.execute(
                select(
                    MeetingNotificationOutbox.id,
                    MeetingNotificationOutbox.meeting_id,
                )
                .where(
                    MeetingNotificationOutbox.workspace_id == workspace_id,
                    _terminalizable_predicate(now),
                )
                .order_by(
                    MeetingNotificationOutbox.available_at,
                    MeetingNotificationOutbox.id,
                )
                .limit(1)
            )
        ).one_or_none()
        if identity is None:
            await db.rollback()
            break
        meeting = (
            await db.scalar(
                select(Meeting)
                .where(
                    Meeting.workspace_id == workspace_id,
                    Meeting.id == identity.meeting_id,
                )
                .with_for_update()
            )
            if identity.meeting_id is not None
            else None
        )
        row = await db.scalar(
            select(MeetingNotificationOutbox)
            .where(
                MeetingNotificationOutbox.workspace_id == workspace_id,
                MeetingNotificationOutbox.id == identity.id,
            )
            .with_for_update()
        )
        if row is None or not (
            row.attempts >= MEETING_NOTIFICATION_MAX_ATTEMPTS
            and (
                row.status == MeetingNotificationStatus.failed
                or (
                    row.status == MeetingNotificationStatus.processing
                    and (
                        row.claimed_at is None
                        or _aware_utc(row.claimed_at)
                        <= _aware_utc(now) - MEETING_NOTIFICATION_LEASE
                    )
                )
            )
        ):
            await db.rollback()
            # A concurrent command changed the candidate after discovery.
            # Leave it to the next recovery sweep instead of spinning on the
            # same identity in this transaction.
            break
        await _record_exhausted_alert(
            db,
            row=row,
            meeting=meeting,
            previous_error_code=row.last_error_code,
        )
        await db.commit()
        terminalized += 1
    return terminalized


async def _deliver_claim(
    db,
    *,
    workspace_id: UUID,
    outbox_id: UUID,
    claim_token: UUID,
    now: datetime,
) -> bool:
    # `_claim_next` committed the lease. Reapply RLS context before reading
    # the claimed row and the meeting in this new transaction.
    await set_workspace_context(db, workspace_id)
    identity = (
        await db.execute(
            select(
                MeetingNotificationOutbox.meeting_id,
            ).where(
                MeetingNotificationOutbox.workspace_id == workspace_id,
                MeetingNotificationOutbox.id == outbox_id,
            )
        )
    ).one_or_none()
    if identity is None:
        await db.rollback()
        return False

    # Domain mutations lock Meeting before touching its outbox. Delivery uses
    # the same order and revalidates the lease only after both locks are held,
    # eliminating the previous outbox -> Meeting deadlock cycle.
    meeting = (
        await db.scalar(
            select(Meeting)
            .where(
                Meeting.workspace_id == workspace_id,
                Meeting.id == identity.meeting_id,
            )
            .with_for_update()
        )
        if identity.meeting_id
        else None
    )
    # Keep the claimed row locked for the provider attempt. A slow provider may
    # outlive the lease, but a competing recovery cannot reclaim this row.
    row = await db.scalar(
        select(MeetingNotificationOutbox)
        .where(
            MeetingNotificationOutbox.workspace_id == workspace_id,
            MeetingNotificationOutbox.id == outbox_id,
        )
        .with_for_update()
    )
    if (
        row is None
        or row.workspace_id != workspace_id
        or row.status != MeetingNotificationStatus.processing
        or row.claim_token != claim_token
        or row.meeting_id != identity.meeting_id
    ):
        await db.rollback()
        return False
    if meeting is None or meeting.status != MeetingStatus.scheduled:
        row.status = MeetingNotificationStatus.disabled
        row.last_error_code = (
            "meeting_cancelled"
            if meeting is not None and meeting.status == MeetingStatus.cancelled
            else "meeting_unavailable"
        )
        row.claim_token = None
        row.claimed_at = None
        await db.commit()
        return False
    if row.meeting_version != meeting.version:
        # The intent belongs to a snapshot that has since been edited.  Never
        # deliver stale time/participant information; the domain transaction
        # enqueues the current version when notification-relevant state changes.
        row.status = MeetingNotificationStatus.disabled
        row.last_error_code = "meeting_superseded"
        row.claim_token = None
        row.claimed_at = None
        await db.commit()
        return False

    try:
        delivered = await OperationalAlertService(db).notify_meeting_scheduled(
            meeting.id,
            commit_whatsapp=False,
        )
    except Exception as error:
        logger.warning(
            "meeting_notification.delivery_failed",
            outbox_id=str(row.id),
            error_type=safe_error_type(error),
        )
        await _settle_failed(
            db,
            row=row,
            meeting=meeting,
            now=now,
            error_code=safe_error_type(error),
        )
        return False

    # Keep the outbox finalization correct even if a future provider adapter
    # introduces an internal commit despite the explicit no-commit contract.
    await set_workspace_context(db, workspace_id)
    if not delivered:
        await _settle_failed(
            db,
            row=row,
            meeting=meeting,
            now=now,
            error_code="provider_or_policy_rejected",
        )
        return False

    row.status = MeetingNotificationStatus.delivered
    row.delivered_at = now
    row.last_error_code = None
    row.claim_token = None
    row.claimed_at = None
    await db.commit()
    return True


async def _async_process_workspace(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = MEETING_NOTIFICATION_BATCH_SIZE,
) -> dict[str, int]:
    resolved_workspace_id = _workspace_id(workspace_id)
    if not external_delivery_allowed():
        logger.info(
            "meeting_notification.delivery_blocked_by_klm_gate",
            workspace_id=str(resolved_workspace_id),
        )
        return {"processed": 0, "delivered": 0}

    reference_now = now or datetime.now(UTC)
    session_maker = session_factory or AsyncSessionLocal
    processed = 0
    delivered = 0
    async with session_maker() as db:
        await _terminalize_exhausted(
            db,
            workspace_id=resolved_workspace_id,
            now=reference_now,
        )
        for _ in range(bounded_batch_size(batch_size)):
            claimed = await _claim_next(
                db,
                workspace_id=resolved_workspace_id,
                now=reference_now,
            )
            if claimed is None:
                break
            processed += 1
            outbox_id, claim_token = claimed
            delivered += int(
                await _deliver_claim(
                    db,
                    workspace_id=resolved_workspace_id,
                    outbox_id=outbox_id,
                    claim_token=claim_token,
                    now=reference_now,
                )
            )
    return {"processed": processed, "delivered": delivered}


async def _async_dispatch_due(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = MEETING_NOTIFICATION_BATCH_SIZE,
    after_created_at: datetime | str | None = None,
    after_workspace_id: UUID | str | None = None,
) -> int:
    if not external_delivery_allowed():
        logger.info("meeting_notification.dispatch_blocked_by_klm_gate")
        return 0
    workspace_ids, next_cursor = await _scan_active_workspace_ids(
        session_factory=session_factory,
        now=now,
        batch_size=batch_size,
        after_created_at=after_created_at,
        after_workspace_id=after_workspace_id,
    )
    scheduled = 0
    for workspace_id in workspace_ids:
        try:
            process_meeting_notification_workspace.apply_async(
                args=[str(workspace_id)],
                countdown=0,
            )
            scheduled += 1
        except Exception as error:
            # The durable row remains visible to the next recovery sweep.
            logger.warning(
                "meeting_notification.recovery_wakeup_failed",
                workspace_id=str(workspace_id),
                error_type=safe_error_type(error),
            )
    if next_cursor is not None:
        recover_meeting_notifications.apply_async(
            kwargs={
                "cursor_created_at": next_cursor[0].isoformat(),
                "cursor_workspace_id": str(next_cursor[1]),
            },
            countdown=1,
        )
    return scheduled


@celery_app.task(name="meeting_notification_tasks.process_workspace")
def process_meeting_notification_workspace(workspace_id: str) -> dict[str, int]:
    return _run_async(_async_process_workspace(workspace_id))


@celery_app.task(name="meeting_notification_tasks.recover")
def recover_meeting_notifications(
    cursor_created_at: str | None = None,
    cursor_workspace_id: str | None = None,
) -> int:
    return _run_async(
        _async_dispatch_due(
            after_created_at=cursor_created_at,
            after_workspace_id=cursor_workspace_id,
        )
    )


__all__ = [
    "_async_dispatch_due",
    "_async_process_workspace",
    "process_meeting_notification_workspace",
    "recover_meeting_notifications",
]
