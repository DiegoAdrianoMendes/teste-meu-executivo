"""Celery recovery and delivery for the transactional task-reminder outbox."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, or_, select

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.post_commit import queue_realtime_event
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.models.notification import Notification, NotificationType
from app.models.task import Task, TaskStatus
from app.models.task_reminder import (
    TaskReminderChannel,
    TaskReminderDeliveryStatus,
    TaskReminderOutbox,
    TaskReminderRule,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.task_reminder_service import TaskReminderService
from app.workers.celery_app import celery_app
from app.workers.fanout import bounded_batch_size, unique_workspace_ids

TASK_REMINDER_BATCH_SIZE = 64
WORKSPACE_SCAN_PAGE_SIZE = 256
TASK_REMINDER_CLAIM_LEASE = timedelta(minutes=5)


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
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("workspace_id must be a valid UUID") from error


def _workspace_cursor(
    created_at: datetime | str | None,
    workspace_id: UUID | str | None,
) -> tuple[datetime, UUID] | None:
    if created_at is None and workspace_id is None:
        return None
    if created_at is None or workspace_id is None:
        raise ValueError("workspace cursor is incomplete")
    try:
        resolved_created_at = (
            created_at if isinstance(created_at, datetime) else datetime.fromisoformat(created_at)
        )
    except ValueError as error:
        raise ValueError("cursor_created_at must be a valid ISO timestamp") from error
    return resolved_created_at, _workspace_id(workspace_id)


def _due_predicate(now: datetime):
    stale_before = now - TASK_REMINDER_CLAIM_LEASE
    return or_(
        and_(
            TaskReminderOutbox.status.in_(
                [TaskReminderDeliveryStatus.pending, TaskReminderDeliveryStatus.failed]
            ),
            TaskReminderOutbox.scheduled_for <= now,
            TaskReminderOutbox.available_at <= now,
        ),
        and_(
            TaskReminderOutbox.status == TaskReminderDeliveryStatus.processing,
            or_(
                TaskReminderOutbox.claimed_at.is_(None),
                TaskReminderOutbox.claimed_at <= stale_before,
            ),
        ),
    )


async def _scan_active_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = TASK_REMINDER_BATCH_SIZE,
    after_created_at: datetime | str | None = None,
    after_workspace_id: UUID | str | None = None,
) -> tuple[list[UUID], tuple[datetime, UUID] | None]:
    reference_now = now or datetime.now(UTC)
    session_maker = session_factory or AsyncSessionLocal
    # The outbox is FORCE-RLS protected, so global discovery cannot safely join
    # it before a tenant is selected. Enumerate active workspace identities on
    # the control table, then inspect each outbox in a fresh tenant-bound
    # session. This remains fail-closed even when the worker DB role is not a
    # PostgreSQL BYPASSRLS role.
    selected: list[UUID] = []
    limit = bounded_batch_size(batch_size)
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
                    select(TaskReminderOutbox.id)
                    .where(
                        TaskReminderOutbox.workspace_id == workspace_id,
                        _due_predicate(reference_now),
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
                    return (
                        unique_workspace_ids(selected, limit=limit),
                        cursor if has_more else None,
                    )
        if len(candidates) < WORKSPACE_SCAN_PAGE_SIZE:
            return unique_workspace_ids(selected, limit=limit), None
    return unique_workspace_ids(selected, limit=limit), None


async def _active_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = TASK_REMINDER_BATCH_SIZE,
) -> list[UUID]:
    selected, _next_cursor = await _scan_active_workspace_ids(
        session_factory=session_factory,
        now=now,
        batch_size=batch_size,
    )
    return selected


async def _release_stale_claims(db, *, workspace_id: UUID, now: datetime) -> None:
    await set_workspace_context(db, workspace_id)
    stale_before = now - TASK_REMINDER_CLAIM_LEASE
    rows = await db.scalars(
        select(TaskReminderOutbox)
        .where(
            TaskReminderOutbox.workspace_id == workspace_id,
            TaskReminderOutbox.status == TaskReminderDeliveryStatus.processing,
            or_(
                TaskReminderOutbox.claimed_at.is_(None),
                TaskReminderOutbox.claimed_at <= stale_before,
            ),
        )
        .with_for_update(skip_locked=True)
    )
    changed = False
    for row in rows.all():
        row.status = TaskReminderDeliveryStatus.failed
        row.claim_token = None
        row.claimed_at = None
        row.available_at = now
        row.last_error_code = "claim_lease_expired"
        changed = True
    if changed:
        await db.commit()
    else:
        await db.rollback()


async def _claim_next(db, *, workspace_id: UUID, now: datetime) -> tuple[UUID, UUID] | None:
    await set_workspace_context(db, workspace_id)
    row = await db.scalar(
        select(TaskReminderOutbox)
        .where(
            TaskReminderOutbox.workspace_id == workspace_id,
            TaskReminderOutbox.status.in_(
                [TaskReminderDeliveryStatus.pending, TaskReminderDeliveryStatus.failed]
            ),
            TaskReminderOutbox.scheduled_for <= now,
            TaskReminderOutbox.available_at <= now,
        )
        .order_by(TaskReminderOutbox.scheduled_for, TaskReminderOutbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row is None:
        await db.rollback()
        return None
    token = uuid4()
    row.status = TaskReminderDeliveryStatus.processing
    row.claim_token = token
    row.claimed_at = now
    row.attempts += 1
    row.last_error_code = None
    await db.commit()
    return row.id, token


async def _active_recipient(db, *, workspace_id: UUID, user_id: UUID) -> bool:
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


async def _cancel_claim(db, row: TaskReminderOutbox, *, now: datetime, reason: str) -> str:
    row.status = TaskReminderDeliveryStatus.cancelled
    row.cancelled_at = now
    row.last_error_code = reason[:80]
    row.claim_token = None
    row.claimed_at = None
    await db.commit()
    return "cancelled"


async def _mark_unavailable(
    db,
    row: TaskReminderOutbox,
    rule: TaskReminderRule,
    *,
    reason: str,
) -> str:
    row.status = TaskReminderDeliveryStatus.unavailable
    row.last_error_code = reason[:80]
    row.claim_token = None
    row.claimed_at = None
    await TaskReminderService(db).record_delivery_event(rule, row, delivered=False, reason=reason)
    await db.commit()
    return "unavailable"


async def _deliver_claim(
    db,
    *,
    workspace_id: UUID,
    outbox_id: UUID,
    claim_token: UUID,
    now: datetime,
) -> str:
    await set_workspace_context(db, workspace_id)
    identity = (
        await db.execute(
            select(TaskReminderOutbox.task_id, TaskReminderOutbox.rule_id).where(
                TaskReminderOutbox.id == outbox_id,
                TaskReminderOutbox.workspace_id == workspace_id,
            )
        )
    ).one_or_none()
    if identity is None:
        await db.rollback()
        return "skipped"

    # Keep the same global order used by task/rule mutations. Locking the
    # outbox first would form task->outbox / outbox->task deadlock cycles when
    # a due date changes at the same instant as delivery.
    task = await db.scalar(select(Task).where(Task.id == identity.task_id).with_for_update())
    rule = await db.scalar(
        select(TaskReminderRule)
        .where(
            TaskReminderRule.id == identity.rule_id,
            TaskReminderRule.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    row = await db.scalar(
        select(TaskReminderOutbox)
        .where(
            TaskReminderOutbox.id == outbox_id,
            TaskReminderOutbox.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if (
        row is None
        or row.status != TaskReminderDeliveryStatus.processing
        or row.claim_token != claim_token
    ):
        await db.rollback()
        return "skipped"
    if task is None or rule is None:
        return await _cancel_claim(db, row, now=now, reason="task_or_rule_unavailable")
    if task.status == TaskStatus.done or task.cancelled_at is not None:
        return await _cancel_claim(db, row, now=now, reason="task_terminal")
    if not rule.active or row.rule_version != rule.version:
        return await _cancel_claim(db, row, now=now, reason="rule_superseded")
    if not await _active_recipient(db, workspace_id=workspace_id, user_id=row.recipient_user_id):
        return await _cancel_claim(db, row, now=now, reason="recipient_unavailable")
    if row.channel != TaskReminderChannel.in_app:
        # V1 intentionally has no external adapter. Never turn configured intent
        # into a simulated success and never call a provider inside this tx.
        return await _mark_unavailable(db, row, rule, reason="channel_not_configured")

    notification_id = uuid5(NAMESPACE_URL, f"klm:task-reminder:{row.id}")
    notification = await db.get(Notification, notification_id)
    if notification is None:
        notification = Notification(
            id=notification_id,
            user_id=row.recipient_user_id,
            workspace_id=workspace_id,
            matter_id=row.matter_id,
            title="Lembrete de tarefa",
            message=(
                f"A tarefa '{task.title}' tem prazo em "
                f"{row.deadline_at.astimezone(UTC).strftime('%d/%m/%Y %H:%M UTC')}."
            ),
            notification_type=(
                NotificationType.urgent if row.deadline_at <= now else NotificationType.warning
            ),
            is_read=False,
        )
        db.add(notification)
        await db.flush()
        queue_realtime_event(
            db,
            workspace_id=workspace_id,
            event_type="notification.task_reminder.created",
            data={
                "notificationId": str(notification.id),
                "taskId": str(task.id),
                "ruleId": str(rule.id),
                "userId": str(row.recipient_user_id),
            },
        )
    elif (
        notification.workspace_id != workspace_id
        or notification.user_id != row.recipient_user_id
        or notification.matter_id != row.matter_id
    ):
        raise RuntimeError("Identificador determinístico de notificação em conflito.")

    row.status = TaskReminderDeliveryStatus.delivered
    row.delivered_at = now
    row.notification_id = notification.id
    row.last_error_code = None
    row.claim_token = None
    row.claimed_at = None
    await TaskReminderService(db).record_delivery_event(
        rule, row, delivered=True, reason="Lembrete entregue no inbox interno."
    )
    await db.commit()
    return "delivered"


def _next_retry_at(now: datetime, attempts: int) -> datetime:
    return now + timedelta(seconds=min(300, 5 * (2 ** max(0, attempts - 1))))


async def _settle_failed(
    db,
    *,
    workspace_id: UUID,
    outbox_id: UUID,
    claim_token: UUID,
    now: datetime,
    error_code: str,
) -> None:
    await db.rollback()
    await set_workspace_context(db, workspace_id)
    row = await db.scalar(
        select(TaskReminderOutbox)
        .where(
            TaskReminderOutbox.id == outbox_id,
            TaskReminderOutbox.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if (
        row is None
        or row.status != TaskReminderDeliveryStatus.processing
        or row.claim_token != claim_token
    ):
        await db.rollback()
        return
    row.status = TaskReminderDeliveryStatus.failed
    row.available_at = _next_retry_at(now, row.attempts)
    row.last_error_code = error_code[:80]
    row.claim_token = None
    row.claimed_at = None
    await db.commit()


async def _async_process_workspace(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = TASK_REMINDER_BATCH_SIZE,
) -> dict[str, int]:
    resolved_workspace_id = _workspace_id(workspace_id)
    reference_now = now or datetime.now(UTC)
    session_maker = session_factory or AsyncSessionLocal
    counters = {"processed": 0, "delivered": 0, "cancelled": 0, "unavailable": 0}
    async with session_maker() as db:
        await _release_stale_claims(db, workspace_id=resolved_workspace_id, now=reference_now)
        for _ in range(bounded_batch_size(batch_size)):
            claimed = await _claim_next(db, workspace_id=resolved_workspace_id, now=reference_now)
            if claimed is None:
                break
            counters["processed"] += 1
            outbox_id, token = claimed
            try:
                outcome = await _deliver_claim(
                    db,
                    workspace_id=resolved_workspace_id,
                    outbox_id=outbox_id,
                    claim_token=token,
                    now=reference_now,
                )
            except Exception as error:
                logger.warning(
                    "task_reminder.delivery_failed",
                    workspace_id=str(resolved_workspace_id),
                    outbox_id=str(outbox_id),
                    error_type=safe_error_type(error),
                )
                await _settle_failed(
                    db,
                    workspace_id=resolved_workspace_id,
                    outbox_id=outbox_id,
                    claim_token=token,
                    now=reference_now,
                    error_code=safe_error_type(error),
                )
                continue
            if outcome in counters:
                counters[outcome] += 1
    return counters


async def _async_dispatch_due(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = TASK_REMINDER_BATCH_SIZE,
    after_created_at: datetime | str | None = None,
    after_workspace_id: UUID | str | None = None,
) -> int:
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
            process_task_reminder_workspace.apply_async(args=[str(workspace_id)])
            scheduled += 1
        except Exception as error:
            logger.warning(
                "task_reminder.recovery_wakeup_failed",
                workspace_id=str(workspace_id),
                error_type=safe_error_type(error),
            )
    if next_cursor is not None:
        recover_task_reminders.apply_async(
            kwargs={
                "cursor_created_at": next_cursor[0].isoformat(),
                "cursor_workspace_id": str(next_cursor[1]),
            },
            countdown=1,
        )
    return scheduled


@celery_app.task(name="task_reminder_tasks.process_workspace")
def process_task_reminder_workspace(workspace_id: str) -> dict[str, int]:
    return _run_async(_async_process_workspace(workspace_id))


@celery_app.task(name="task_reminder_tasks.recover")
def recover_task_reminders(
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
    "process_task_reminder_workspace",
    "recover_task_reminders",
]
