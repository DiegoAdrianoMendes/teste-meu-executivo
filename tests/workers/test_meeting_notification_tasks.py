from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.meeting import (
    Meeting,
    MeetingNotificationOutbox,
    MeetingNotificationStatus,
    MeetingStatus,
)
from app.models.notification import Notification
from app.schemas.control_plane import MeetingCreate
from app.services.meeting_service import MeetingService
from app.workers import meeting_notification_tasks


def _same_session_factory(db_session):
    @asynccontextmanager
    async def session_context():
        yield db_session

    return session_context


@pytest.mark.asyncio
async def test_meeting_notification_worker_delivers_committed_outbox(
    db_session,
    auth_context,
    monkeypatch,
):
    starts_at = datetime.now(UTC) + timedelta(days=1)
    meeting = await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Reunião confirmada após commit",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            location="Online",
        ),
    )
    await db_session.commit()
    delivered_meeting_ids = []

    async def deliver(_service, meeting_id, *, commit_whatsapp):
        assert commit_whatsapp is False
        delivered_meeting_ids.append(meeting_id)
        return True

    monkeypatch.setattr(
        meeting_notification_tasks,
        "external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        meeting_notification_tasks.OperationalAlertService,
        "notify_meeting_scheduled",
        deliver,
    )
    processed_at = datetime.now(UTC) + timedelta(seconds=1)

    result = await meeting_notification_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=_same_session_factory(db_session),
        now=processed_at,
    )

    assert result == {"processed": 1, "delivered": 1}
    assert delivered_meeting_ids == [meeting.id]
    outbox = await db_session.scalar(
        select(MeetingNotificationOutbox).where(MeetingNotificationOutbox.meeting_id == meeting.id)
    )
    assert outbox is not None
    assert outbox.status == MeetingNotificationStatus.delivered
    assert outbox.attempts == 1
    assert outbox.delivered_at is not None
    assert outbox.delivered_at.replace(tzinfo=UTC) == processed_at
    assert outbox.claim_token is None
    assert outbox.claimed_at is None


@pytest.mark.asyncio
async def test_meeting_notification_worker_retries_rejected_delivery(
    db_session,
    auth_context,
    monkeypatch,
):
    starts_at = datetime.now(UTC) + timedelta(days=2)
    meeting = await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Reunião com provider indisponível",
            starts_at=starts_at,
            location="Online",
        ),
    )
    await db_session.commit()

    async def reject(_service, _meeting_id, *, commit_whatsapp):
        assert commit_whatsapp is False
        return False

    monkeypatch.setattr(
        meeting_notification_tasks,
        "external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        meeting_notification_tasks.OperationalAlertService,
        "notify_meeting_scheduled",
        reject,
    )
    processed_at = datetime.now(UTC) + timedelta(seconds=1)

    result = await meeting_notification_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=_same_session_factory(db_session),
        now=processed_at,
    )

    assert result == {"processed": 1, "delivered": 0}
    outbox = await db_session.scalar(
        select(MeetingNotificationOutbox).where(MeetingNotificationOutbox.meeting_id == meeting.id)
    )
    assert outbox is not None
    assert outbox.status == MeetingNotificationStatus.failed
    assert outbox.attempts == 1
    assert outbox.last_error_code == "provider_or_policy_rejected"
    assert outbox.available_at.replace(tzinfo=UTC) > processed_at
    assert outbox.claim_token is None
    assert outbox.claimed_at is None


@pytest.mark.asyncio
async def test_meeting_notification_exhaustion_is_terminal_and_alerted(
    db_session,
    auth_context,
    monkeypatch,
):
    starts_at = datetime.now(UTC) + timedelta(days=2)
    meeting = await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Reunião com falha persistente",
            starts_at=starts_at,
            location="Online",
            responsible_user_id=auth_context.user_id,
        ),
    )
    await db_session.flush()
    outbox = await db_session.scalar(
        select(MeetingNotificationOutbox).where(MeetingNotificationOutbox.meeting_id == meeting.id)
    )
    assert outbox is not None
    outbox.attempts = meeting_notification_tasks.MEETING_NOTIFICATION_MAX_ATTEMPTS - 1
    await db_session.commit()

    async def reject(_service, _meeting_id, *, commit_whatsapp):
        assert commit_whatsapp is False
        return False

    monkeypatch.setattr(
        meeting_notification_tasks,
        "external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        meeting_notification_tasks.OperationalAlertService,
        "notify_meeting_scheduled",
        reject,
    )

    result = await meeting_notification_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=_same_session_factory(db_session),
        now=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert result == {"processed": 1, "delivered": 0}
    await db_session.refresh(outbox)
    assert outbox.status == MeetingNotificationStatus.disabled
    assert outbox.attempts == meeting_notification_tasks.MEETING_NOTIFICATION_MAX_ATTEMPTS
    assert outbox.last_error_code == "max_attempts_exhausted"
    alert = await db_session.scalar(
        select(Notification).where(
            Notification.workspace_id == auth_context.workspace_id,
            Notification.user_id == auth_context.user_id,
            Notification.title == "Falha persistente ao notificar compromisso",
        )
    )
    assert alert is not None
    assert meeting.title in alert.message


@pytest.mark.asyncio
async def test_meeting_notification_gate_leaves_outbox_pending(
    db_session,
    auth_context,
    monkeypatch,
):
    starts_at = datetime.now(UTC) + timedelta(days=3)
    meeting = await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Reunião bloqueada pelo gate externo",
            starts_at=starts_at,
            location="Online",
        ),
    )
    await db_session.commit()
    monkeypatch.setattr(
        meeting_notification_tasks,
        "external_delivery_allowed",
        lambda: False,
    )

    result = await meeting_notification_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=_same_session_factory(db_session),
    )

    assert result == {"processed": 0, "delivered": 0}
    outbox = await db_session.scalar(
        select(MeetingNotificationOutbox).where(MeetingNotificationOutbox.meeting_id == meeting.id)
    )
    assert outbox is not None
    assert outbox.status == MeetingNotificationStatus.pending
    assert outbox.attempts == 0


@pytest.mark.asyncio
async def test_meeting_notification_worker_disables_a_stale_meeting_version(
    db_session,
    auth_context,
    monkeypatch,
):
    starts_at = datetime.now(UTC) + timedelta(days=4)
    meeting = await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Versão antiga não pode ser entregue",
            starts_at=starts_at,
            location="Online",
        ),
    )
    await db_session.commit()
    persisted = await db_session.get(Meeting, meeting.id)
    assert persisted is not None
    persisted.version += 1
    await db_session.commit()

    async def should_not_deliver(*_args, **_kwargs):
        raise AssertionError("an obsolete meeting snapshot reached the provider")

    monkeypatch.setattr(
        meeting_notification_tasks,
        "external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        meeting_notification_tasks.OperationalAlertService,
        "notify_meeting_scheduled",
        should_not_deliver,
    )

    result = await meeting_notification_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=_same_session_factory(db_session),
        now=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert result == {"processed": 1, "delivered": 0}
    outbox = await db_session.scalar(
        select(MeetingNotificationOutbox).where(MeetingNotificationOutbox.meeting_id == meeting.id)
    )
    assert outbox is not None
    assert outbox.status == MeetingNotificationStatus.disabled
    assert outbox.last_error_code == "meeting_superseded"


@pytest.mark.asyncio
async def test_claimed_notification_preserves_explicit_cancellation_evidence(
    db_session,
    auth_context,
    monkeypatch,
):
    meeting = await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Reunião cancelada durante a entrega",
            starts_at=datetime.now(UTC) + timedelta(days=8),
            location="Online",
        ),
    )
    await db_session.commit()
    claimed = await meeting_notification_tasks._claim_next(
        db_session,
        workspace_id=auth_context.workspace_id,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    assert claimed is not None
    outbox_id, claim_token = claimed
    persisted = await db_session.get(Meeting, meeting.id)
    assert persisted is not None
    persisted.status = MeetingStatus.cancelled
    persisted.version += 1
    await db_session.commit()

    async def should_not_deliver(*_args, **_kwargs):
        raise AssertionError("a cancelled meeting reached the provider")

    monkeypatch.setattr(
        meeting_notification_tasks.OperationalAlertService,
        "notify_meeting_scheduled",
        should_not_deliver,
    )
    delivered = await meeting_notification_tasks._deliver_claim(
        db_session,
        workspace_id=auth_context.workspace_id,
        outbox_id=outbox_id,
        claim_token=claim_token,
        now=datetime.now(UTC) + timedelta(seconds=2),
    )

    assert delivered is False
    outbox = await db_session.get(MeetingNotificationOutbox, outbox_id)
    assert outbox is not None
    assert outbox.status == MeetingNotificationStatus.disabled
    assert outbox.last_error_code == "meeting_cancelled"
    assert outbox.claim_token is None


@pytest.mark.asyncio
async def test_delivery_locks_meeting_before_outbox_and_revalidates_claim(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    meeting_id = uuid4()
    outbox_id = uuid4()
    claim_token = uuid4()
    lock_order: list[object] = []
    meeting = SimpleNamespace(
        id=meeting_id,
        workspace_id=workspace_id,
        status=MeetingStatus.scheduled,
        version=3,
    )
    outbox = SimpleNamespace(
        id=outbox_id,
        workspace_id=workspace_id,
        meeting_id=meeting_id,
        meeting_version=3,
        status=MeetingNotificationStatus.processing,
        claim_token=claim_token,
        claimed_at=datetime.now(UTC),
        attempts=1,
        delivered_at=None,
        last_error_code=None,
    )

    class IdentityResult:
        def one_or_none(self):
            return SimpleNamespace(meeting_id=meeting_id)

    class RecordingDb:
        async def execute(self, _statement):
            return IdentityResult()

        async def scalar(self, statement):
            entity = statement.column_descriptions[0].get("entity")
            lock_order.append(entity)
            if entity is Meeting:
                return meeting
            if entity is MeetingNotificationOutbox:
                return outbox
            raise AssertionError(f"unexpected lock target: {entity}")

        async def commit(self):
            return None

        async def rollback(self):
            return None

    async def bind_workspace(_db, bound_workspace_id):
        assert bound_workspace_id == workspace_id

    class DeliveredService:
        def __init__(self, _db):
            pass

        async def notify_meeting_scheduled(self, requested_meeting_id, *, commit_whatsapp):
            assert requested_meeting_id == meeting_id
            assert commit_whatsapp is False
            return True

    monkeypatch.setattr(
        meeting_notification_tasks,
        "set_workspace_context",
        bind_workspace,
    )
    monkeypatch.setattr(
        meeting_notification_tasks,
        "OperationalAlertService",
        DeliveredService,
    )

    delivered = await meeting_notification_tasks._deliver_claim(
        RecordingDb(),
        workspace_id=workspace_id,
        outbox_id=outbox_id,
        claim_token=claim_token,
        now=datetime.now(UTC),
    )

    assert delivered is True
    assert lock_order == [Meeting, MeetingNotificationOutbox]
    assert outbox.status == MeetingNotificationStatus.delivered


@pytest.mark.asyncio
async def test_meeting_notification_recovery_fans_out_active_workspaces(
    monkeypatch,
):
    workspace_ids = [uuid4(), uuid4()]
    scheduled: list[str] = []

    async def active_workspaces(**_kwargs):
        return workspace_ids, None

    def schedule(*, args, countdown):
        assert countdown == 0
        scheduled.append(args[0])

    monkeypatch.setattr(
        meeting_notification_tasks,
        "external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        meeting_notification_tasks,
        "_scan_active_workspace_ids",
        active_workspaces,
    )
    monkeypatch.setattr(
        meeting_notification_tasks.process_meeting_notification_workspace,
        "apply_async",
        schedule,
    )

    count = await meeting_notification_tasks._async_dispatch_due()

    assert count == 2
    assert scheduled == [str(item) for item in workspace_ids]


@pytest.mark.asyncio
async def test_recovery_persists_next_cursor_in_a_chained_task(monkeypatch) -> None:
    workspace_id = uuid4()
    cursor = datetime.now(UTC), uuid4()
    continuations: list[dict] = []

    async def scan(**_kwargs):
        return [workspace_id], cursor

    monkeypatch.setattr(
        meeting_notification_tasks,
        "external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        meeting_notification_tasks,
        "_scan_active_workspace_ids",
        scan,
    )
    monkeypatch.setattr(
        meeting_notification_tasks.process_meeting_notification_workspace,
        "apply_async",
        lambda **_options: None,
    )
    monkeypatch.setattr(
        meeting_notification_tasks.recover_meeting_notifications,
        "apply_async",
        lambda **options: continuations.append(options),
    )

    assert await meeting_notification_tasks._async_dispatch_due() == 1
    assert continuations == [
        {
            "kwargs": {
                "cursor_created_at": cursor[0].isoformat(),
                "cursor_workspace_id": str(cursor[1]),
            },
            "countdown": 1,
        }
    ]


@pytest.mark.asyncio
async def test_recovery_keysets_past_legacy_1000_workspace_cap(monkeypatch) -> None:
    created_at = datetime.now(UTC)
    candidates = [(uuid4(), created_at + timedelta(microseconds=index)) for index in range(1_100)]
    pages = [
        candidates[index : index + meeting_notification_tasks.WORKSPACE_SCAN_PAGE_SIZE]
        for index in range(
            0,
            len(candidates),
            meeting_notification_tasks.WORKSPACE_SCAN_PAGE_SIZE,
        )
    ]
    target_id = candidates[-1][0]
    visited: list[object] = []

    class Rows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Session:
        workspace_id = None

        async def execute(self, _statement):
            return Rows(pages.pop(0) if pages else [])

        async def scalar(self, _statement):
            return object() if self.workspace_id == target_id else None

        async def rollback(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def bind_workspace(db, workspace_id):
        db.workspace_id = workspace_id
        visited.append(workspace_id)

    monkeypatch.setattr(
        meeting_notification_tasks,
        "set_workspace_context",
        bind_workspace,
    )

    selected = await meeting_notification_tasks._active_workspace_ids(
        session_factory=Session,
        batch_size=1,
    )

    assert selected == [target_id]
    assert len(visited) == 1_100


@pytest.mark.asyncio
async def test_recovery_cursor_chains_past_a_permanently_due_front_batch(
    monkeypatch,
) -> None:
    created_at = datetime.now(UTC)
    candidates = [(uuid4(), created_at + timedelta(microseconds=index)) for index in range(4)]
    pages = [candidates, candidates[2:]]

    class Rows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Session:
        workspace_id = None

        async def execute(self, _statement):
            return Rows(pages.pop(0) if pages else [])

        async def scalar(self, _statement):
            return object()

        async def rollback(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def bind_workspace(db, workspace_id):
        db.workspace_id = workspace_id

    monkeypatch.setattr(
        meeting_notification_tasks,
        "set_workspace_context",
        bind_workspace,
    )

    first, cursor = await meeting_notification_tasks._scan_active_workspace_ids(
        session_factory=Session,
        batch_size=2,
    )
    assert first == [item[0] for item in candidates[:2]]
    assert cursor == (candidates[1][1], candidates[1][0])

    second, final_cursor = await meeting_notification_tasks._scan_active_workspace_ids(
        session_factory=Session,
        batch_size=2,
        after_created_at=cursor[0],
        after_workspace_id=cursor[1],
    )
    assert second == [item[0] for item in candidates[2:]]
    assert final_cursor is None
