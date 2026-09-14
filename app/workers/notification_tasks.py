"""
Tasks periódicas para lembretes e digest diário.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.db.post_commit import queue_realtime_event
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.models.meeting import (
    Meeting,
    MeetingParticipant,
    MeetingParticipantRsvp,
    MeetingStatus,
)
from app.models.notification import Notification, NotificationType
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.operational_alert_service import OperationalAlertService
from app.services.workspace_policy_service import _normalize_notification_settings_shape
from app.workers.celery_app import celery_app
from app.workers.fanout import (
    FanoutMetrics,
    bounded_batch_size,
    unique_workspace_ids,
)

NOTIFICATION_WORKSPACE_BATCH_SIZE = 64
WORKSPACE_SCAN_PAGE_SIZE = 256
MEETING_REMINDER_SCAN_PAGE_SIZE = 200
MAX_PARTICIPANT_REMINDER_MINUTES = 10_080
DAILY_DIGEST_LEASE = timedelta(minutes=30)
WORKER_STATE_KEY = "_workerState"


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
    except ValueError as exc:
        raise ValueError("cursor_created_at must be a valid ISO timestamp") from exc
    return resolved_created_at, _workspace_id(workspace_id)


def _notification_rule(settings: dict, key: str) -> dict:
    notifications = _normalize_notification_settings_shape(
        (settings or {}).get("notifications", {})
    )
    return next(
        (rule for rule in notifications.get("rules", []) if rule.get("key") == key),
        {"key": key, "enabled": True},
    )


def _meeting_reminder_is_due(
    *,
    workspace_settings: dict,
    starts_at: datetime,
    now: datetime,
) -> bool:
    rule = _notification_rule(workspace_settings, "meetingReminder")
    if not rule.get("enabled", True):
        return False
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    reminder_minutes = _reminder_minutes(
        rule.get("reminderMinutesBefore"),
        default=120,
    )
    return starts_at - timedelta(minutes=reminder_minutes) <= now <= starts_at


def _reminder_minutes(value, *, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(parsed, MAX_PARTICIPANT_REMINDER_MINUTES))


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _participant_reminder_notification_id(
    *,
    meeting_id: UUID,
    user_id: UUID,
    starts_at: datetime,
    reminder_minutes: int,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        (
            f"klm:meeting-participant-reminder:{meeting_id}:{user_id}:"
            f"{_aware_utc(starts_at).isoformat()}:{reminder_minutes}"
        ),
    )


async def _individual_reminder_page(
    db,
    *,
    workspace_id: UUID,
    now: datetime,
    cursor: tuple[datetime, UUID, UUID] | None,
) -> list[tuple[UUID, datetime, UUID | None, str, UUID, UUID, int]]:
    predicates = [
        Meeting.workspace_id == workspace_id,
        Meeting.status == MeetingStatus.scheduled,
        Meeting.starts_at >= now,
        Meeting.starts_at <= now + timedelta(minutes=MAX_PARTICIPANT_REMINDER_MINUTES),
        MeetingParticipant.workspace_id == workspace_id,
        MeetingParticipant.active.is_(True),
        MeetingParticipant.reminder_minutes_before.is_not(None),
        MeetingParticipant.rsvp != MeetingParticipantRsvp.declined,
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.status == MembershipStatus.active,
        User.is_active.is_(True),
        User.deleted_at.is_(None),
    ]
    if cursor is not None:
        starts_at, meeting_id, participant_id = cursor
        predicates.append(
            or_(
                Meeting.starts_at > starts_at,
                and_(
                    Meeting.starts_at == starts_at,
                    Meeting.id > meeting_id,
                ),
                and_(
                    Meeting.starts_at == starts_at,
                    Meeting.id == meeting_id,
                    MeetingParticipant.id > participant_id,
                ),
            )
        )
    rows = (
        await db.execute(
            select(
                Meeting.id,
                Meeting.starts_at,
                Meeting.matter_id,
                Meeting.title,
                MeetingParticipant.id,
                MeetingParticipant.user_id,
                MeetingParticipant.reminder_minutes_before,
            )
            .join(
                MeetingParticipant,
                and_(
                    MeetingParticipant.meeting_id == Meeting.id,
                    MeetingParticipant.workspace_id == Meeting.workspace_id,
                ),
            )
            .join(
                WorkspaceMembership,
                and_(
                    WorkspaceMembership.id == MeetingParticipant.membership_id,
                    WorkspaceMembership.workspace_id == MeetingParticipant.workspace_id,
                    WorkspaceMembership.user_id == MeetingParticipant.user_id,
                ),
            )
            .join(User, User.id == MeetingParticipant.user_id)
            .where(*predicates)
            .order_by(
                Meeting.starts_at,
                Meeting.id,
                MeetingParticipant.id,
            )
            .limit(MEETING_REMINDER_SCAN_PAGE_SIZE)
        )
    ).all()
    return [
        (
            meeting_id,
            starts_at,
            matter_id,
            title,
            participant_id,
            user_id,
            int(reminder_minutes),
        )
        for (
            meeting_id,
            starts_at,
            matter_id,
            title,
            participant_id,
            user_id,
            reminder_minutes,
        ) in rows
    ]


async def _workspace_has_due_meeting_reminder(
    db,
    *,
    workspace_id: UUID,
    workspace_settings: dict,
    now: datetime,
    include_external: bool,
) -> bool:
    await set_workspace_context(db, workspace_id)
    rule = _notification_rule(workspace_settings, "meetingReminder")
    if not rule.get("enabled", True):
        await db.rollback()
        return False

    if include_external:
        reminder_minutes = _reminder_minutes(
            rule.get("reminderMinutesBefore"),
            default=120,
        )
        due_meeting_id = await db.scalar(
            select(Meeting.id)
            .where(
                Meeting.workspace_id == workspace_id,
                Meeting.status == MeetingStatus.scheduled,
                Meeting.reminder_sent_at.is_(None),
                Meeting.starts_at >= now,
                Meeting.starts_at <= now + timedelta(minutes=reminder_minutes),
            )
            .order_by(Meeting.starts_at, Meeting.id)
            .limit(1)
        )
        if due_meeting_id is not None:
            await db.rollback()
            return True

    cursor: tuple[datetime, UUID, UUID] | None = None
    while True:
        rows = await _individual_reminder_page(
            db,
            workspace_id=workspace_id,
            now=now,
            cursor=cursor,
        )
        if not rows:
            await db.rollback()
            return False
        for (
            meeting_id,
            starts_at,
            _matter_id,
            _title,
            participant_id,
            _user_id,
            raw_minutes,
        ) in rows:
            reminder_minutes = _reminder_minutes(raw_minutes, default=0)
            starts_at_utc = _aware_utc(starts_at)
            if not (
                starts_at_utc - timedelta(minutes=reminder_minutes)
                <= _aware_utc(now)
                <= starts_at_utc
            ):
                continue
            notification_id = _participant_reminder_notification_id(
                meeting_id=meeting_id,
                user_id=_user_id,
                starts_at=starts_at,
                reminder_minutes=reminder_minutes,
            )
            if await db.get(Notification, notification_id) is None:
                await db.rollback()
                return True
        last = rows[-1]
        cursor = last[1], last[0], last[4]
        if len(rows) < MEETING_REMINDER_SCAN_PAGE_SIZE:
            await db.rollback()
            return False


async def _deliver_individual_meeting_reminder(
    db,
    *,
    workspace_id: UUID,
    meeting_id: UUID,
    candidate_starts_at: datetime,
    participant_id: UUID,
    candidate_user_id: UUID,
    candidate_reminder_minutes: int,
    now: datetime,
) -> int:
    """Revalidate one participant intent under the Meeting aggregate lock."""

    # Meeting mutations lock the aggregate before replacing participants. The
    # worker uses the same order so a participant removed after discovery can
    # never receive a stale notification.
    await set_workspace_context(db, workspace_id)
    meeting = await db.scalar(
        select(Meeting)
        .where(
            Meeting.workspace_id == workspace_id,
            Meeting.id == meeting_id,
        )
        .with_for_update()
    )
    if (
        meeting is None
        or meeting.status != MeetingStatus.scheduled
        or _aware_utc(meeting.starts_at) != _aware_utc(candidate_starts_at)
    ):
        await db.rollback()
        return 0
    participant = await db.scalar(
        select(MeetingParticipant)
        .where(
            MeetingParticipant.workspace_id == workspace_id,
            MeetingParticipant.meeting_id == meeting.id,
            MeetingParticipant.id == participant_id,
        )
        .with_for_update()
    )
    if (
        participant is None
        or not participant.active
        or participant.user_id != candidate_user_id
        or participant.rsvp == MeetingParticipantRsvp.declined
        or participant.reminder_minutes_before is None
    ):
        await db.rollback()
        return 0
    recipient_is_active = await db.scalar(
        select(WorkspaceMembership.id)
        .join(User, User.id == WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.id == participant.membership_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == participant.user_id,
            WorkspaceMembership.status == MembershipStatus.active,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if recipient_is_active is None:
        await db.rollback()
        return 0
    reminder_minutes = _reminder_minutes(
        participant.reminder_minutes_before,
        default=candidate_reminder_minutes,
    )
    starts_at_utc = _aware_utc(meeting.starts_at)
    if not (
        starts_at_utc - timedelta(minutes=reminder_minutes) <= _aware_utc(now) <= starts_at_utc
    ):
        await db.rollback()
        return 0
    notification_id = _participant_reminder_notification_id(
        meeting_id=meeting.id,
        user_id=participant.user_id,
        starts_at=meeting.starts_at,
        reminder_minutes=reminder_minutes,
    )
    if await db.get(Notification, notification_id) is not None:
        await db.rollback()
        return 0
    notification = Notification(
        id=notification_id,
        user_id=participant.user_id,
        workspace_id=workspace_id,
        matter_id=meeting.matter_id,
        title="Lembrete de compromisso",
        message=(
            f"O compromisso '{meeting.title}' começa em "
            f"{starts_at_utc.strftime('%d/%m/%Y %H:%M UTC')}."
        ),
        notification_type=(
            NotificationType.urgent
            if starts_at_utc <= _aware_utc(now) + timedelta(minutes=15)
            else NotificationType.warning
        ),
        is_read=False,
    )
    try:
        async with db.begin_nested():
            db.add(notification)
            await db.flush()
    except IntegrityError:
        # A chave semântica independe da identidade transitória da linha de
        # participante e fenceia sweeps ou redistribuições concorrentes.
        await db.rollback()
        return 0
    queue_realtime_event(
        db,
        workspace_id=workspace_id,
        event_type="notification.meeting_participant_reminder.created",
        data={
            "notificationId": str(notification.id),
            "meetingId": str(meeting.id),
            "participantId": str(participant.id),
            "userId": str(participant.user_id),
        },
    )
    await db.commit()
    return 1


async def _deliver_individual_meeting_reminders(
    db,
    *,
    workspace_id: UUID,
    now: datetime,
) -> int:
    """Materialize one durable in-app delivery per semantic participant intent."""

    delivered = 0
    cursor: tuple[datetime, UUID, UUID] | None = None
    while True:
        await set_workspace_context(db, workspace_id)
        rows = await _individual_reminder_page(
            db,
            workspace_id=workspace_id,
            now=now,
            cursor=cursor,
        )
        # Discovery is only a candidate read. Release its transaction before
        # taking aggregate locks and revalidate every field used for delivery.
        await db.rollback()
        if not rows:
            break
        for (
            meeting_id,
            starts_at,
            _matter_id,
            _title,
            participant_id,
            user_id,
            raw_minutes,
        ) in rows:
            delivered += await _deliver_individual_meeting_reminder(
                db,
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                candidate_starts_at=starts_at,
                participant_id=participant_id,
                candidate_user_id=user_id,
                candidate_reminder_minutes=raw_minutes,
                now=now,
            )
        last = rows[-1]
        cursor = last[1], last[0], last[4]
        if len(rows) < MEETING_REMINDER_SCAN_PAGE_SIZE:
            break
    return delivered


def _digest_state(settings: dict) -> dict:
    worker_state = (settings or {}).get(WORKER_STATE_KEY)
    if not isinstance(worker_state, dict):
        return {}
    state = worker_state.get("dailyDigest")
    return dict(state) if isinstance(state, dict) else {}


def _digest_day(now: datetime) -> str:
    return now.astimezone().date().isoformat()


def _digest_lease_active(state: dict, now: datetime) -> bool:
    raw_expiry = state.get("leaseExpiresAt")
    if not raw_expiry:
        return False
    try:
        expires_at = datetime.fromisoformat(str(raw_expiry))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at.astimezone(UTC) > now.astimezone(UTC)


async def _scan_meeting_reminder_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = NOTIFICATION_WORKSPACE_BATCH_SIZE,
    include_external: bool = True,
    after_created_at: datetime | str | None = None,
    after_workspace_id: UUID | str | None = None,
) -> tuple[list[UUID], int, tuple[datetime, UUID] | None]:
    reference_now = now or datetime.now(UTC)
    limit = bounded_batch_size(batch_size)
    session_maker = session_factory or AsyncSessionLocal
    selected: list[UUID] = []
    scanned = 0
    cursor = _workspace_cursor(after_created_at, after_workspace_id)
    while len(selected) < limit:
        predicates = [
            Workspace.status == WorkspaceStatus.active,
            Workspace.deleted_at.is_(None),
            Workspace.is_active.is_(True),
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
                        select(
                            Workspace.id,
                            Workspace.created_at,
                            Workspace.settings,
                        )
                        .where(*predicates)
                        .order_by(Workspace.created_at, Workspace.id)
                        .limit(WORKSPACE_SCAN_PAGE_SIZE)
                    )
                ).all()
            )
        if not candidates:
            break
        scanned += len(candidates)
        for index, (workspace_id, created_at, settings) in enumerate(candidates):
            async with session_maker() as tenant_db:
                if await _workspace_has_due_meeting_reminder(
                    tenant_db,
                    workspace_id=workspace_id,
                    workspace_settings=settings or {},
                    now=reference_now,
                    include_external=include_external,
                ):
                    selected.append(workspace_id)
            cursor = created_at, workspace_id
            if len(selected) >= limit:
                has_more = (
                    index < len(candidates) - 1 or len(candidates) == WORKSPACE_SCAN_PAGE_SIZE
                )
                return (
                    unique_workspace_ids(selected, limit=limit),
                    scanned,
                    cursor if has_more else None,
                )
        if len(candidates) < WORKSPACE_SCAN_PAGE_SIZE:
            return unique_workspace_ids(selected, limit=limit), scanned, None
    return unique_workspace_ids(selected, limit=limit), scanned, None


async def _meeting_reminder_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = NOTIFICATION_WORKSPACE_BATCH_SIZE,
    include_external: bool = True,
) -> tuple[list[UUID], int]:
    selected, scanned, _next_cursor = await _scan_meeting_reminder_workspace_ids(
        session_factory=session_factory,
        now=now,
        batch_size=batch_size,
        include_external=include_external,
    )
    return selected, scanned


async def _daily_digest_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = NOTIFICATION_WORKSPACE_BATCH_SIZE,
    after_workspace_id: UUID | str | None = None,
) -> tuple[list[UUID], int, UUID | None]:
    reference_now = now or datetime.now(UTC)
    digest_day = _digest_day(reference_now)
    limit = bounded_batch_size(batch_size)
    last_processed = Workspace.settings[WORKER_STATE_KEY]["dailyDigest"]["processedDay"].as_string()
    predicates = [
        Workspace.deleted_at.is_(None),
        Workspace.is_active.is_(True),
        or_(
            last_processed.is_(None),
            last_processed != digest_day,
        ),
    ]
    if after_workspace_id is not None:
        predicates.append(Workspace.id > _workspace_id(after_workspace_id))
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as discovery_db:
        rows = list(
            (
                await discovery_db.execute(
                    select(Workspace.id, Workspace.settings)
                    .where(*predicates)
                    .order_by(Workspace.id)
                    .limit(limit)
                )
            ).all()
        )
    workspace_ids = [
        workspace_id
        for workspace_id, settings in rows
        if _notification_rule(settings or {}, "dailyDigest").get("enabled", True)
        and not _digest_lease_active(_digest_state(settings or {}), reference_now)
    ]
    return workspace_ids, len(rows), rows[-1][0] if rows else None


async def _dispatch_workspaces(
    task,
    workspace_ids: list[UUID],
    *,
    metrics: FanoutMetrics,
) -> int:
    metrics.discovered_workspaces = len(workspace_ids)
    for workspace_id in workspace_ids:
        try:
            task.apply_async(kwargs={"workspace_id": str(workspace_id)})
            metrics.enqueued_workspaces += 1
        except Exception:
            metrics.publish_failures += 1
            raise
    metrics.emit()
    return metrics.enqueued_workspaces


@celery_app.task(name="notification_tasks.send_meeting_reminders")
def send_meeting_reminders(
    workspace_id: str | None = None,
    cursor_created_at: str | None = None,
    cursor_workspace_id: str | None = None,
) -> int:
    if workspace_id is None:
        return _run_async(
            _async_dispatch_meeting_reminders(
                after_created_at=cursor_created_at,
                after_workspace_id=cursor_workspace_id,
            )
        )
    return _run_async(_async_send_meeting_reminders(workspace_id))


async def _async_dispatch_meeting_reminders(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = NOTIFICATION_WORKSPACE_BATCH_SIZE,
    after_created_at: datetime | str | None = None,
    after_workspace_id: UUID | str | None = None,
) -> int:
    external_allowed = external_delivery_allowed()
    if not external_allowed:
        logger.info("notification_tasks.external_meeting_reminders.blocked_by_klm_gate")
    limit = bounded_batch_size(batch_size)
    workspace_ids, discovery_rows, next_cursor = await _scan_meeting_reminder_workspace_ids(
        session_factory=session_factory,
        now=now,
        batch_size=limit,
        include_external=external_allowed,
        after_created_at=after_created_at,
        after_workspace_id=after_workspace_id,
    )
    metrics = FanoutMetrics("notification.meeting_reminders", limit)
    metrics.discovery_rows = discovery_rows
    enqueued = await _dispatch_workspaces(
        send_meeting_reminders,
        workspace_ids,
        metrics=metrics,
    )
    if next_cursor is not None:
        send_meeting_reminders.apply_async(
            kwargs={
                "cursor_created_at": next_cursor[0].isoformat(),
                "cursor_workspace_id": str(next_cursor[1]),
            },
            countdown=1,
        )
    return enqueued


async def _async_send_meeting_reminders(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    now: datetime | None = None,
) -> int:
    resolved_workspace_id = _workspace_id(workspace_id)
    reference_now = now or datetime.now(UTC)
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        individual_sent = await _deliver_individual_meeting_reminders(
            db,
            workspace_id=resolved_workspace_id,
            now=reference_now,
        )
        external_sent = 0
        if external_delivery_allowed():
            # The individual delivery committed (or rolled back) its own
            # transaction. PostgreSQL SET LOCAL RLS context must be installed
            # again before the legacy provider-backed reminder path.
            await set_workspace_context(db, resolved_workspace_id)
            external_sent = await OperationalAlertService(db).send_due_meeting_reminders(
                reference_now
            )
        else:
            logger.info(
                "notification_tasks.meeting_reminders.external_blocked_by_klm_gate",
                workspace_id=str(resolved_workspace_id),
            )
        sent = individual_sent + external_sent
        if sent:
            logger.info(
                "notification_tasks.meeting_reminders_sent",
                workspace_id=str(resolved_workspace_id),
                count=sent,
                individual_count=individual_sent,
                external_count=external_sent,
            )
        return sent


@celery_app.task(name="notification_tasks.send_daily_digest")
def send_daily_digest(
    workspace_id: str | None = None,
    cursor: str | None = None,
) -> int:
    if workspace_id is None:
        return _run_async(_async_dispatch_daily_digest(after_workspace_id=cursor))
    return _run_async(_async_send_daily_digest(workspace_id))


async def _async_dispatch_daily_digest(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = NOTIFICATION_WORKSPACE_BATCH_SIZE,
    after_workspace_id: UUID | str | None = None,
) -> int:
    if not external_delivery_allowed():
        logger.info("notification_tasks.dispatch.blocked_by_klm_gate")
        return 0
    limit = bounded_batch_size(batch_size)
    workspace_ids, discovery_rows, next_cursor = await _daily_digest_workspace_ids(
        session_factory=session_factory,
        now=now,
        batch_size=limit,
        after_workspace_id=after_workspace_id,
    )
    metrics = FanoutMetrics("notification.daily_digest", limit)
    metrics.discovery_rows = discovery_rows
    enqueued = await _dispatch_workspaces(
        send_daily_digest,
        workspace_ids,
        metrics=metrics,
    )
    if discovery_rows >= limit and next_cursor is not None:
        send_daily_digest.apply_async(
            kwargs={"cursor": str(next_cursor)},
            countdown=1,
        )
    return enqueued


def _set_digest_state(workspace: Workspace, state: dict) -> None:
    settings = dict(workspace.settings or {})
    worker_state = dict(settings.get(WORKER_STATE_KEY) or {})
    worker_state["dailyDigest"] = state
    settings[WORKER_STATE_KEY] = worker_state
    workspace.settings = settings


async def _claim_daily_digest(
    db,
    *,
    workspace_id: UUID,
    now: datetime,
) -> str | None:
    workspace = await db.scalar(
        select(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
            Workspace.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
        .with_for_update(skip_locked=True)
    )
    if not workspace:
        await db.rollback()
        return None
    state = _digest_state(workspace.settings or {})
    day = _digest_day(now)
    if state.get("processedDay") == day or _digest_lease_active(state, now):
        await db.rollback()
        return None
    token = uuid4().hex
    _set_digest_state(
        workspace,
        {
            **state,
            "claimToken": token,
            "claimedAt": now.astimezone(UTC).isoformat(),
            "leaseExpiresAt": (now + DAILY_DIGEST_LEASE).astimezone(UTC).isoformat(),
        },
    )
    await db.commit()
    return token


async def _finalize_daily_digest(
    db,
    *,
    workspace_id: UUID,
    claim_token: str,
    now: datetime,
    sent: bool,
    uncertain: bool = False,
) -> bool:
    workspace = await db.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not workspace:
        await db.rollback()
        return False
    state = _digest_state(workspace.settings or {})
    if state.get("claimToken") != claim_token:
        await db.rollback()
        return False
    _set_digest_state(
        workspace,
        {
            "processedDay": _digest_day(now),
            "processedAt": datetime.now(UTC).isoformat(),
            "sent": sent,
            "deliveryUncertain": uncertain,
        },
    )
    await db.commit()
    return True


async def _async_send_daily_digest(
    workspace_id: UUID | str,
    *,
    session_factory=None,
) -> int:
    resolved_workspace_id = _workspace_id(workspace_id)
    if not external_delivery_allowed():
        logger.info(
            "notification_tasks.daily_digest.blocked_by_klm_gate",
            workspace_id=str(resolved_workspace_id),
        )
        return 0

    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        now = datetime.now(UTC)
        claim_token = await _claim_daily_digest(
            db,
            workspace_id=resolved_workspace_id,
            now=now,
        )
        if not claim_token:
            return 0
        try:
            sent = await OperationalAlertService(db).send_daily_digest_for_workspace(
                resolved_workspace_id,
                now=now,
            )
        except Exception:
            await _finalize_daily_digest(
                db,
                workspace_id=resolved_workspace_id,
                claim_token=claim_token,
                now=now,
                sent=False,
                uncertain=True,
            )
            raise
        await _finalize_daily_digest(
            db,
            workspace_id=resolved_workspace_id,
            claim_token=claim_token,
            now=now,
            sent=bool(sent),
        )
        if sent:
            logger.info(
                "notification_tasks.daily_digest_sent",
                workspace_id=str(resolved_workspace_id),
                count=1,
            )
        return int(sent)
