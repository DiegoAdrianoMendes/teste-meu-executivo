from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.tenant import get_workspace_context
from app.models.meeting import Meeting, MeetingParticipant, MeetingParticipantRole
from app.models.notification import Notification
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.control_plane import MeetingUpdate
from app.services.meeting_service import MeetingService
from app.workers import notification_tasks


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def _workspace(name: str, *, is_active: bool = True, deleted: bool = False) -> Workspace:
    return Workspace(
        name=name,
        slug=f"notification-worker-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=is_active,
        deleted_at=datetime.now(UTC) if deleted else None,
        settings={},
    )


async def _workspace_member(
    db_session: AsyncSession,
    *,
    workspace_id,
    name: str,
) -> tuple[User, WorkspaceMembership]:
    suffix = uuid4().hex
    user = User(
        name=name,
        email=f"{suffix}@notification-worker.local",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials=name[:2].upper(),
        color="#0e3d5c",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user.id,
        role=WorkspaceRole.operator,
        status=MembershipStatus.active,
        is_default=False,
    )
    db_session.add(membership)
    await db_session.flush()
    return user, membership


@pytest.mark.asyncio
async def test_notification_dispatchers_enqueue_only_active_workspace_ids(
    db_session: AsyncSession,
    monkeypatch,
):
    active_a = _workspace("Notification A")
    active_b = _workspace("Notification B")
    inactive = _workspace("Notification inactive", is_active=False)
    deleted = _workspace("Notification deleted", deleted=True)
    db_session.add_all((active_a, active_b, inactive, deleted))
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all(
        Meeting(
            workspace_id=workspace.id,
            title=f"Reunião {workspace.name}",
            starts_at=now + timedelta(minutes=30),
        )
        for workspace in (active_a, active_b, inactive, deleted)
    )
    await db_session.commit()

    reminder_messages: list[tuple[tuple, dict]] = []
    digest_messages: list[tuple[tuple, dict]] = []

    def record_reminder(*args, **options):
        reminder_messages.append((args, options))

    def record_digest(*args, **options):
        digest_messages.append((args, options))

    monkeypatch.setattr(notification_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        notification_tasks.send_meeting_reminders,
        "apply_async",
        record_reminder,
    )
    monkeypatch.setattr(notification_tasks.send_daily_digest, "apply_async", record_digest)

    session_factory = _session_factory(db_session)
    reminder_count = await notification_tasks._async_dispatch_meeting_reminders(
        session_factory=session_factory,
        now=now,
    )
    digest_count = await notification_tasks._async_dispatch_daily_digest(
        session_factory=session_factory
    )

    expected = {
        ((), str(active_a.id)),
        ((), str(active_b.id)),
    }
    assert reminder_count == digest_count == 2
    assert {
        (args, options["kwargs"]["workspace_id"]) for args, options in reminder_messages
    } == expected
    assert {
        (args, options["kwargs"]["workspace_id"]) for args, options in digest_messages
    } == expected
    assert all(set(options) == {"kwargs"} for _, options in reminder_messages)
    assert all(set(options) == {"kwargs"} for _, options in digest_messages)


@pytest.mark.asyncio
async def test_notification_workers_bind_one_session_to_each_workspace(
    db_session: AsyncSession,
    monkeypatch,
):
    persisted_a = _workspace("Notification session A")
    persisted_b = _workspace("Notification session B")
    db_session.add_all((persisted_a, persisted_b))
    await db_session.commit()
    workspace_a = persisted_a.id
    workspace_b = persisted_b.id
    observations: list[tuple[str, AsyncSession, object, object | None]] = []

    class RecordingOperationalAlertService:
        def __init__(self, db: AsyncSession) -> None:
            self.db = db
            observations.append(("init", db, get_workspace_context(db), None))

        async def send_due_meeting_reminders(self, now=None) -> int:
            observations.append(("reminder", self.db, get_workspace_context(self.db), None))
            return 1

        async def send_daily_digest_for_workspace(self, workspace_id, *, now=None) -> bool:
            observations.append(("digest", self.db, get_workspace_context(self.db), workspace_id))
            return True

    monkeypatch.setattr(notification_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        notification_tasks,
        "OperationalAlertService",
        RecordingOperationalAlertService,
    )
    session_factory = _session_factory(db_session)

    assert (
        await notification_tasks._async_send_meeting_reminders(
            workspace_a,
            session_factory=session_factory,
        )
        == 1
    )
    assert (
        await notification_tasks._async_send_meeting_reminders(
            workspace_b,
            session_factory=session_factory,
        )
        == 1
    )
    assert (
        await notification_tasks._async_send_daily_digest(
            workspace_b,
            session_factory=session_factory,
        )
        == 1
    )

    service_calls = [item for item in observations if item[0] != "init"]
    assert [(kind, context, argument) for kind, _, context, argument in service_calls] == [
        ("reminder", workspace_a, None),
        ("reminder", workspace_b, None),
        ("digest", workspace_b, workspace_b),
    ]
    assert len({id(db) for _, db, _, _ in service_calls}) == 3
    assert all(context is not None for _, _, context, _ in observations)


@pytest.mark.asyncio
async def test_notification_zero_work_does_not_enter_any_tenant_session(
    db_session: AsyncSession,
    monkeypatch,
):
    now = datetime.now(UTC)
    workspaces = [_workspace(f"Notification idle {index}") for index in range(12)]
    for workspace in workspaces:
        workspace.settings = {
            notification_tasks.WORKER_STATE_KEY: {
                "dailyDigest": {
                    "processedDay": notification_tasks._digest_day(now),
                }
            }
        }
    db_session.add_all(workspaces)
    await db_session.commit()

    def forbidden_enqueue(*args, **kwargs):
        raise AssertionError("idle tenant was enqueued")

    monkeypatch.setattr(notification_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        notification_tasks.send_meeting_reminders,
        "apply_async",
        forbidden_enqueue,
    )
    monkeypatch.setattr(
        notification_tasks.send_daily_digest,
        "apply_async",
        forbidden_enqueue,
    )
    factory = _session_factory(db_session)

    assert (
        await notification_tasks._async_dispatch_meeting_reminders(
            session_factory=factory,
            now=now,
        )
        == 0
    )
    assert (
        await notification_tasks._async_dispatch_daily_digest(
            session_factory=factory,
            now=now,
        )
        == 0
    )


@pytest.mark.asyncio
async def test_notification_fanout_respects_workspace_batch_cap(
    db_session: AsyncSession,
    monkeypatch,
):
    now = datetime.now(UTC)
    workspaces = [_workspace(f"Notification cap {index}") for index in range(5)]
    db_session.add_all(workspaces)
    await db_session.flush()
    db_session.add_all(
        Meeting(
            workspace_id=workspace.id,
            title=f"Reunião cap {index}",
            starts_at=now + timedelta(minutes=10),
        )
        for index, workspace in enumerate(workspaces)
    )
    await db_session.commit()
    enqueued: list[str] = []

    def record_workspace_or_continuation(**options):
        workspace_id = options["kwargs"].get("workspace_id")
        if workspace_id is not None:
            enqueued.append(workspace_id)

    monkeypatch.setattr(notification_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        notification_tasks.send_meeting_reminders,
        "apply_async",
        record_workspace_or_continuation,
    )

    count = await notification_tasks._async_dispatch_meeting_reminders(
        session_factory=_session_factory(db_session),
        now=now,
        batch_size=2,
    )

    assert count == 2
    assert len(set(enqueued)) == 2


@pytest.mark.asyncio
async def test_daily_digest_claim_makes_second_execution_a_noop(
    db_session: AsyncSession,
    monkeypatch,
):
    workspace = _workspace("Notification digest idempotent")
    db_session.add(workspace)
    await db_session.commit()
    deliveries: list[object] = []

    class RecordingOperationalAlertService:
        def __init__(self, db: AsyncSession) -> None:
            self.db = db

        async def send_daily_digest_for_workspace(self, workspace_id, *, now=None) -> bool:
            deliveries.append(workspace_id)
            return True

    monkeypatch.setattr(notification_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        notification_tasks,
        "OperationalAlertService",
        RecordingOperationalAlertService,
    )
    factory = _session_factory(db_session)

    first = await notification_tasks._async_send_daily_digest(
        workspace.id,
        session_factory=factory,
    )
    second = await notification_tasks._async_send_daily_digest(
        workspace.id,
        session_factory=factory,
    )

    assert first == 1
    assert second == 0
    assert deliveries == [workspace.id]


@pytest.mark.asyncio
async def test_individual_meeting_reminder_is_internal_and_idempotent(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    starts_at = datetime.now(UTC) + timedelta(minutes=45)
    meeting = Meeting(
        workspace_id=auth_context.workspace_id,
        title="Compromisso com lembrete individual",
        starts_at=starts_at,
        participants=[
            MeetingParticipant(
                workspace_id=auth_context.workspace_id,
                membership_id=auth_context.membership_id,
                user_id=auth_context.user_id,
                role=MeetingParticipantRole.organizer,
                reminder_minutes_before=60,
                label_snapshot="Organizador",
                active=True,
            )
        ],
    )
    db_session.add(meeting)
    await db_session.commit()
    monkeypatch.setattr(
        notification_tasks,
        "external_delivery_allowed",
        lambda: False,
    )
    factory = _session_factory(db_session)

    first = await notification_tasks._async_send_meeting_reminders(
        auth_context.workspace_id,
        session_factory=factory,
        now=datetime.now(UTC),
    )
    second = await notification_tasks._async_send_meeting_reminders(
        auth_context.workspace_id,
        session_factory=factory,
        now=datetime.now(UTC),
    )

    assert first == 1
    assert second == 0
    notifications = list(
        (
            await db_session.scalars(
                select(Notification).where(
                    Notification.workspace_id == auth_context.workspace_id,
                    Notification.user_id == auth_context.user_id,
                    Notification.title == "Lembrete de compromisso",
                )
            )
        ).all()
    )
    assert len(notifications) == 1
    assert meeting.title in notifications[0].message


@pytest.mark.asyncio
async def test_individual_reminder_revalidates_removed_participant_before_insert(
    db_session: AsyncSession,
    auth_context,
):
    starts_at = datetime.now(UTC) + timedelta(minutes=45)
    participant = MeetingParticipant(
        workspace_id=auth_context.workspace_id,
        membership_id=auth_context.membership_id,
        user_id=auth_context.user_id,
        role=MeetingParticipantRole.organizer,
        reminder_minutes_before=60,
        label_snapshot="Organizador removido",
        active=True,
    )
    meeting = Meeting(
        workspace_id=auth_context.workspace_id,
        responsible_user_id=auth_context.user_id,
        title="Compromisso alterado após discovery",
        starts_at=starts_at,
        participants=[participant],
    )
    db_session.add(meeting)
    await db_session.commit()
    participant.active = False
    participant.removed_at = datetime.now(UTC)
    await db_session.commit()

    delivered = await notification_tasks._deliver_individual_meeting_reminder(
        db_session,
        workspace_id=auth_context.workspace_id,
        meeting_id=meeting.id,
        candidate_starts_at=starts_at,
        participant_id=participant.id,
        candidate_user_id=auth_context.user_id,
        candidate_reminder_minutes=60,
        now=datetime.now(UTC),
    )

    assert delivered == 0
    assert (
        await db_session.scalar(
            select(Notification.id).where(
                Notification.workspace_id == auth_context.workspace_id,
                Notification.user_id == auth_context.user_id,
                Notification.title == "Lembrete de compromisso",
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_organizer_reassignment_keeps_attendee_reminder_semantically_idempotent(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    attendee, attendee_membership = await _workspace_member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Participante preservado",
    )
    replacement, _replacement_membership = await _workspace_member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Novo organizador",
    )
    starts_at = datetime.now(UTC) + timedelta(minutes=45)
    meeting = Meeting(
        workspace_id=auth_context.workspace_id,
        responsible_user_id=auth_context.user_id,
        title="Compromisso redistribuído",
        starts_at=starts_at,
        participants=[
            MeetingParticipant(
                workspace_id=auth_context.workspace_id,
                membership_id=auth_context.membership_id,
                user_id=auth_context.user_id,
                role=MeetingParticipantRole.organizer,
                label_snapshot="Organizador original",
                active=True,
            ),
            MeetingParticipant(
                workspace_id=auth_context.workspace_id,
                membership_id=attendee_membership.id,
                user_id=attendee.id,
                role=MeetingParticipantRole.attendee,
                reminder_minutes_before=60,
                label_snapshot=attendee.name,
                active=True,
            ),
        ],
    )
    db_session.add(meeting)
    await db_session.commit()
    original_attendee_id = next(
        item.id for item in meeting.participants if item.user_id == attendee.id and item.active
    )
    monkeypatch.setattr(
        notification_tasks,
        "external_delivery_allowed",
        lambda: False,
    )
    factory = _session_factory(db_session)

    assert (
        await notification_tasks._async_send_meeting_reminders(
            auth_context.workspace_id,
            session_factory=factory,
            now=datetime.now(UTC),
        )
        == 1
    )
    updated = await MeetingService(db_session).update_for_workspace(
        auth_context.workspace_id,
        meeting.id,
        MeetingUpdate(
            expected_version=meeting.version,
            responsible_user_id=replacement.id,
        ),
        actor_user_id=auth_context.user_id,
        correlation_id="test:meeting-reminder:organizer-reassigned",
    )
    await db_session.commit()
    replacement_attendee = next(
        item for item in updated.participants if item.user_id == attendee.id and item.active
    )
    assert replacement_attendee.id != original_attendee_id

    assert (
        await notification_tasks._async_send_meeting_reminders(
            auth_context.workspace_id,
            session_factory=factory,
            now=datetime.now(UTC),
        )
        == 0
    )
    notifications = list(
        (
            await db_session.scalars(
                select(Notification).where(
                    Notification.workspace_id == auth_context.workspace_id,
                    Notification.user_id == attendee.id,
                    Notification.title == "Lembrete de compromisso",
                )
            )
        ).all()
    )
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_meeting_reminder_discovery_keysets_past_legacy_512_cap(
    monkeypatch,
):
    created_at = datetime.now(UTC)
    candidates = [(uuid4(), created_at + timedelta(microseconds=index), {}) for index in range(700)]
    pages = [
        candidates[index : index + notification_tasks.WORKSPACE_SCAN_PAGE_SIZE]
        for index in range(0, len(candidates), notification_tasks.WORKSPACE_SCAN_PAGE_SIZE)
    ]
    target_id = candidates[-1][0]
    sessions: list[object] = []

    class Rows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Session:
        async def execute(self, _statement):
            return Rows(pages.pop(0) if pages else [])

        async def __aenter__(self):
            sessions.append(self)
            return self

        async def __aexit__(self, *_args):
            return None

    async def has_due(_db, *, workspace_id, **_kwargs):
        return workspace_id == target_id

    monkeypatch.setattr(
        notification_tasks,
        "_workspace_has_due_meeting_reminder",
        has_due,
    )

    selected, scanned = await notification_tasks._meeting_reminder_workspace_ids(
        session_factory=Session,
        batch_size=1,
    )

    assert selected == [target_id]
    assert scanned == 700
    # One discovery session per page plus a distinct tenant session per
    # candidate; a single global FORCE-RLS query is never used.
    assert len(sessions) > 700


@pytest.mark.asyncio
async def test_meeting_reminder_cursor_chains_past_a_permanently_due_front_batch(
    monkeypatch,
):
    created_at = datetime.now(UTC)
    candidates = [(uuid4(), created_at + timedelta(microseconds=index), {}) for index in range(4)]
    pages = [candidates, candidates[2:]]

    class Rows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Session:
        async def execute(self, _statement):
            return Rows(pages.pop(0) if pages else [])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def has_due(_db, **_kwargs):
        return True

    monkeypatch.setattr(
        notification_tasks,
        "_workspace_has_due_meeting_reminder",
        has_due,
    )

    first, _scanned, cursor = await notification_tasks._scan_meeting_reminder_workspace_ids(
        session_factory=Session,
        batch_size=2,
    )
    assert first == [item[0] for item in candidates[:2]]
    assert cursor == (candidates[1][1], candidates[1][0])

    second, _scanned, final_cursor = await notification_tasks._scan_meeting_reminder_workspace_ids(
        session_factory=Session,
        batch_size=2,
        after_created_at=cursor[0],
        after_workspace_id=cursor[1],
    )
    assert second == [item[0] for item in candidates[2:]]
    assert final_cursor is None


@pytest.mark.asyncio
async def test_meeting_reminder_dispatch_persists_next_cursor_in_chained_task(
    monkeypatch,
):
    workspace_id = uuid4()
    cursor = datetime.now(UTC), uuid4()
    continuations: list[dict] = []

    async def scan(**_kwargs):
        return [workspace_id], 1, cursor

    monkeypatch.setattr(
        notification_tasks,
        "_scan_meeting_reminder_workspace_ids",
        scan,
    )
    monkeypatch.setattr(
        notification_tasks,
        "external_delivery_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        notification_tasks.send_meeting_reminders,
        "apply_async",
        lambda **options: continuations.append(options),
    )

    assert await notification_tasks._async_dispatch_meeting_reminders() == 1
    assert continuations == [
        {"kwargs": {"workspace_id": str(workspace_id)}},
        {
            "kwargs": {
                "cursor_created_at": cursor[0].isoformat(),
                "cursor_workspace_id": str(cursor[1]),
            },
            "countdown": 1,
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worker",
    [
        notification_tasks._async_send_daily_digest,
    ],
)
async def test_notification_workers_reject_invalid_workspace_before_database_access(worker):
    def forbidden_session_factory():
        raise AssertionError("invalid workspace reached the database")

    with pytest.raises(ValueError, match="workspace_id"):
        await worker("not-a-uuid", session_factory=forbidden_session_factory)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worker",
    [
        notification_tasks._async_send_daily_digest,
    ],
)
async def test_notification_workers_recheck_external_gate_before_database_access(
    worker,
    monkeypatch,
):
    def forbidden_session_factory():
        raise AssertionError("blocked delivery reached the database")

    monkeypatch.setattr(notification_tasks, "external_delivery_allowed", lambda: False)

    assert await worker(uuid4(), session_factory=forbidden_session_factory) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dispatcher",
    [
        notification_tasks._async_dispatch_daily_digest,
    ],
)
async def test_notification_dispatchers_do_not_enqueue_when_external_gate_is_closed(
    dispatcher,
    monkeypatch,
):
    def forbidden_session_factory():
        raise AssertionError("blocked dispatcher reached the database")

    def forbidden_apply_async(*args, **kwargs):
        raise AssertionError("blocked dispatcher enqueued a notification")

    monkeypatch.setattr(notification_tasks, "external_delivery_allowed", lambda: False)
    monkeypatch.setattr(
        notification_tasks.send_meeting_reminders,
        "apply_async",
        forbidden_apply_async,
    )
    monkeypatch.setattr(
        notification_tasks.send_daily_digest,
        "apply_async",
        forbidden_apply_async,
    )

    assert await dispatcher(session_factory=forbidden_session_factory) == 0
