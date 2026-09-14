"""Realtime notifications must never escape before their transaction commits."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import post_commit
from app.db.post_commit import (
    queue_agent_run_wakeup,
    queue_native_outbox_wakeup,
    queue_operational_alert,
    queue_realtime_event,
)
from app.db.session import KlmAsyncSession
from app.websockets.manager import ws_manager


@pytest.mark.asyncio
async def test_realtime_event_is_published_only_after_successful_commit(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)
    published: list[dict] = []

    async def capture(event_type, data, *, workspace_id):
        published.append(
            {
                "event_type": event_type,
                "data": data,
                "workspace_id": workspace_id,
            }
        )

    monkeypatch.setattr(ws_manager, "broadcast_board_update", capture)
    workspace_id = uuid4()
    async with sessions() as session:
        queue_realtime_event(
            session,
            workspace_id=workspace_id,
            event_type="AGENT_RUN_COMPLETED",
            data={"agent_run_id": "run-1"},
        )
        assert published == []
        await session.commit()

    assert published == [
        {
            "event_type": "AGENT_RUN_COMPLETED",
            "data": {"agent_run_id": "run-1"},
            "workspace_id": workspace_id,
        }
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_rollback_discards_realtime_event(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)
    published: list[str] = []

    async def capture(event_type, data, *, workspace_id):
        published.append(event_type)

    monkeypatch.setattr(ws_manager, "broadcast_board_update", capture)
    async with sessions() as session:
        queue_realtime_event(
            session,
            workspace_id=uuid4(),
            event_type="AGENT_RUN_FAILED",
            data={"agent_run_id": "run-2"},
        )
        await session.rollback()
        await session.commit()

    assert published == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_realtime_failure_does_not_turn_committed_transaction_into_failure(
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)

    async def fail(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(ws_manager, "broadcast_board_update", fail)
    async with sessions() as session:
        queue_realtime_event(
            session,
            workspace_id=uuid4(),
            event_type="AGENT_RUN_STARTED",
            data={"agent_run_id": "run-3"},
        )
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_durable_wakeup_precedes_realtime_and_hung_transport_is_bounded(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)
    calls: list[str] = []
    never_returns = asyncio.Event()

    def capture_wakeup(workspace_id: str) -> None:
        calls.append(f"wakeup:{workspace_id}")

    async def hang(*args, **kwargs) -> None:
        calls.append("realtime")
        await never_returns.wait()

    monkeypatch.setattr(
        "app.workers.native_outbox_tasks.process_native_outbox_workspace.delay",
        capture_wakeup,
    )
    monkeypatch.setattr(ws_manager, "broadcast_board_update", hang)
    monkeypatch.setattr(post_commit, "_ADVISORY_DISPATCH_TIMEOUT_SECONDS", 0.01)
    workspace_id = uuid4()

    async with sessions() as session:
        queue_realtime_event(
            session,
            workspace_id=workspace_id,
            event_type="AGENT_RUN_STARTED",
            data={"agent_run_id": "bounded"},
        )
        queue_native_outbox_wakeup(session, workspace_id=workspace_id)
        await asyncio.wait_for(session.commit(), timeout=0.25)

    assert calls == [f"wakeup:{workspace_id}", "realtime"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_operational_alert_is_not_called_before_commit(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)
    calls: list[str] = []

    async def capture(self, matter_id):
        calls.append(str(matter_id))

    monkeypatch.setattr(
        "app.services.operational_alert_service.OperationalAlertService.notify_contract_signed",
        capture,
    )
    workspace_id = uuid4()
    matter_id = uuid4()
    async with sessions() as session:
        queue_operational_alert(
            session,
            workspace_id=workspace_id,
            alert_type="contract_signed",
            entity_id=matter_id,
        )
        assert calls == []
        await session.commit()

    assert calls == [str(matter_id)]
    await engine.dispose()


@pytest.mark.asyncio
async def test_native_outbox_wakeup_is_deduplicated_and_published_after_commit(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)
    queued: list[str] = []
    monkeypatch.setattr(
        "app.workers.native_outbox_tasks.process_native_outbox_workspace.delay",
        lambda workspace_id: queued.append(workspace_id),
    )
    workspace_id = uuid4()

    async with sessions() as session:
        queue_native_outbox_wakeup(session, workspace_id=workspace_id)
        queue_native_outbox_wakeup(session, workspace_id=workspace_id)
        assert queued == []
        await session.commit()

    assert queued == [str(workspace_id)]
    await engine.dispose()


@pytest.mark.asyncio
async def test_native_outbox_wakeup_is_discarded_on_rollback(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)
    queued: list[str] = []
    monkeypatch.setattr(
        "app.workers.native_outbox_tasks.process_native_outbox_workspace.delay",
        lambda workspace_id: queued.append(workspace_id),
    )

    async with sessions() as session:
        queue_native_outbox_wakeup(session, workspace_id=uuid4())
        await session.rollback()
        await session.commit()

    assert queued == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_run_wakeup_is_deduplicated_and_published_after_commit(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)
    queued: list[dict] = []

    def capture(*, args, task_id):
        queued.append({"args": args, "task_id": task_id})

    monkeypatch.setattr(
        "app.workers.agent_tasks.execute_agent_run.apply_async",
        capture,
    )
    workspace_id = uuid4()
    agent_run_id = uuid4()
    task_id = "approval-task-1"

    async with sessions() as session:
        queue_agent_run_wakeup(
            session,
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            task_id=task_id,
        )
        queue_agent_run_wakeup(
            session,
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            task_id=task_id,
        )
        assert queued == []
        await session.commit()

    assert queued == [
        {
            "args": [str(agent_run_id), str(workspace_id)],
            "task_id": task_id,
        }
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_run_wakeup_is_discarded_on_rollback(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)
    queued: list[str] = []
    monkeypatch.setattr(
        "app.workers.agent_tasks.execute_agent_run.apply_async",
        lambda **kwargs: queued.append(kwargs["task_id"]),
    )

    async with sessions() as session:
        queue_agent_run_wakeup(
            session,
            workspace_id=uuid4(),
            agent_run_id=uuid4(),
            task_id="approval-task-rollback",
        )
        await session.rollback()
        await session.commit()

    assert queued == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_run_broker_failure_does_not_fail_committed_transaction(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=KlmAsyncSession)

    def fail(*args, **kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "app.workers.agent_tasks.execute_agent_run.apply_async",
        fail,
    )
    async with sessions() as session:
        queue_agent_run_wakeup(
            session,
            workspace_id=uuid4(),
            agent_run_id=uuid4(),
            task_id="approval-task-broker-failure",
        )
        await session.commit()

    await engine.dispose()
