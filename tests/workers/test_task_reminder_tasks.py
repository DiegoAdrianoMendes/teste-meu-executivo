from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.deps import AuthContext
from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.models.notification import Notification
from app.models.task_reminder import (
    TaskReminderChannel,
    TaskReminderDeliveryStatus,
    TaskReminderOutbox,
    TaskReminderRecipient,
    TaskReminderRelativeTo,
)
from app.schemas.task import TaskCreate
from app.schemas.task_reminder import TaskReminderRuleCreate
from app.services.task_reminder_service import (
    TaskReminderMutationContext,
    TaskReminderService,
)
from app.services.task_service import TaskMutationContext, TaskService
from app.workers import task_reminder_tasks


def _same_session_factory(db_session):
    @asynccontextmanager
    async def session_context():
        yield db_session

    return session_context


async def _task(db, auth: AuthContext, *, due_at: datetime):
    client = Client(workspace_id=auth.workspace_id, name=f"Worker {uuid4().hex[:8]}")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=auth.workspace_id,
        client_id=client.id,
        demand_type="Lembrete worker",
        practice_area=PracticeArea.civel,
    )
    db.add(matter)
    await db.flush()
    return await TaskService(db).create(
        matter.id,
        TaskCreate(title="Entregar recurso", due_at=due_at),
        auth.workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth.user_id,
            correlation_id="test:task-reminder-worker:task",
        ),
    )


async def _rule(db, auth: AuthContext, task, *, channel: TaskReminderChannel):
    return await TaskReminderService(db).create_rule(
        workspace_id=auth.workspace_id,
        task_id=task.id,
        data=TaskReminderRuleCreate(
            expected_version=task.version,
            relative_to=TaskReminderRelativeTo.general_due,
            offset_minutes_before=60,
            channel=channel,
            recipient=TaskReminderRecipient.owner,
        ),
        idempotency_key=f"worker-rule-{channel.value}-{uuid4()}",
        context=TaskReminderMutationContext(
            actor_user_id=auth.user_id,
            correlation_id=f"test:task-reminder-worker:{channel.value}",
        ),
    )


@pytest.mark.asyncio
async def test_worker_delivers_in_app_exactly_once_without_external_gate(
    db_session, auth_context: AuthContext
) -> None:
    processed_at = datetime.now(UTC)
    task = await _task(
        db_session,
        auth_context,
        due_at=processed_at + timedelta(minutes=30),
    )
    created, _ = await _rule(db_session, auth_context, task, channel=TaskReminderChannel.in_app)
    await db_session.commit()

    first = await task_reminder_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=_same_session_factory(db_session),
        now=processed_at,
    )
    second = await task_reminder_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=_same_session_factory(db_session),
        now=processed_at + timedelta(seconds=1),
    )

    assert first == {
        "processed": 1,
        "delivered": 1,
        "cancelled": 0,
        "unavailable": 0,
    }
    assert second == {
        "processed": 0,
        "delivered": 0,
        "cancelled": 0,
        "unavailable": 0,
    }
    assert int(await db_session.scalar(select(func.count(Notification.id))) or 0) == 1
    delivery = await db_session.get(TaskReminderOutbox, created["deliveries"][0]["id"])
    assert delivery is not None
    assert delivery.status == TaskReminderDeliveryStatus.delivered
    assert delivery.notification_id is not None
    assert delivery.attempts == 1


@pytest.mark.asyncio
async def test_external_rule_is_unavailable_and_never_enters_delivery_worker(
    db_session, auth_context: AuthContext
) -> None:
    processed_at = datetime.now(UTC)
    task = await _task(
        db_session,
        auth_context,
        due_at=processed_at + timedelta(minutes=30),
    )
    created, _ = await _rule(db_session, auth_context, task, channel=TaskReminderChannel.whatsapp)
    await db_session.commit()

    result = await task_reminder_tasks._async_process_workspace(
        auth_context.workspace_id,
        session_factory=_same_session_factory(db_session),
        now=processed_at,
    )

    assert result["processed"] == 0
    assert int(await db_session.scalar(select(func.count(Notification.id))) or 0) == 0
    delivery = await db_session.get(TaskReminderOutbox, created["deliveries"][0]["id"])
    assert delivery is not None
    assert delivery.status == TaskReminderDeliveryStatus.unavailable
    assert delivery.last_error_code == "channel_not_configured"


@pytest.mark.asyncio
async def test_recovery_fans_out_only_workspaces_with_due_internal_rows(monkeypatch) -> None:
    workspace_ids = [uuid4(), uuid4()]
    scheduled: list[str] = []

    async def active_workspaces(**_kwargs):
        return workspace_ids, None

    def schedule(*, args):
        scheduled.append(args[0])

    monkeypatch.setattr(
        task_reminder_tasks,
        "_scan_active_workspace_ids",
        active_workspaces,
    )
    monkeypatch.setattr(
        task_reminder_tasks.process_task_reminder_workspace, "apply_async", schedule
    )

    assert await task_reminder_tasks._async_dispatch_due() == 2
    assert scheduled == [str(item) for item in workspace_ids]


@pytest.mark.asyncio
async def test_recovery_persists_next_cursor_in_a_chained_task(monkeypatch) -> None:
    workspace_id = uuid4()
    cursor = datetime.now(UTC), uuid4()
    continuations: list[dict] = []

    async def scan(**_kwargs):
        return [workspace_id], cursor

    monkeypatch.setattr(task_reminder_tasks, "_scan_active_workspace_ids", scan)
    monkeypatch.setattr(
        task_reminder_tasks.process_task_reminder_workspace,
        "apply_async",
        lambda **_options: None,
    )
    monkeypatch.setattr(
        task_reminder_tasks.recover_task_reminders,
        "apply_async",
        lambda **options: continuations.append(options),
    )

    assert await task_reminder_tasks._async_dispatch_due() == 1
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
async def test_recovery_keysets_past_legacy_512_workspace_cap(monkeypatch) -> None:
    created_at = datetime.now(UTC)
    candidates = [(uuid4(), created_at + timedelta(microseconds=index)) for index in range(700)]
    pages = [
        candidates[index : index + task_reminder_tasks.WORKSPACE_SCAN_PAGE_SIZE]
        for index in range(
            0,
            len(candidates),
            task_reminder_tasks.WORKSPACE_SCAN_PAGE_SIZE,
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

    monkeypatch.setattr(task_reminder_tasks, "set_workspace_context", bind_workspace)

    selected = await task_reminder_tasks._active_workspace_ids(
        session_factory=Session,
        batch_size=1,
    )

    assert selected == [target_id]
    assert len(visited) == 700


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

    monkeypatch.setattr(task_reminder_tasks, "set_workspace_context", bind_workspace)

    first, cursor = await task_reminder_tasks._scan_active_workspace_ids(
        session_factory=Session,
        batch_size=2,
    )
    assert first == [item[0] for item in candidates[:2]]
    assert cursor == (candidates[1][1], candidates[1][0])

    second, final_cursor = await task_reminder_tasks._scan_active_workspace_ids(
        session_factory=Session,
        batch_size=2,
        after_created_at=cursor[0],
        after_workspace_id=cursor[1],
    )
    assert second == [item[0] for item in candidates[2:]]
    assert final_cursor is None
