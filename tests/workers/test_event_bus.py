from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.db.tenant import get_workspace_context
from app.models.agent_platform import AgentDeployment
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.matter import Matter
from app.models.task import Task
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.agent_runtime_service import AgentRuntimeService
from app.workers import event_bus
from app.workers.agent_tasks import (
    AGENT_DISPATCH_LEASE_TIMEOUT,
    _recover_agent_runs_async,
)
from tests.agent_deployment_helpers import seed_verified_agent_deployments


@pytest.fixture(autouse=True)
async def _explicit_agent_deployment(db_session, auth_context, monkeypatch):
    await seed_verified_agent_deployments(
        db_session, auth_context.workspace_id, {"klm_sdr"}, monkeypatch
    )


def _session_factory(db_session):
    @asynccontextmanager
    async def factory():
        yield db_session

    return factory


async def _matter(db_session, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente EventBus")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type="Demanda EventBus",
    )
    db_session.add(matter)
    await db_session.commit()
    return matter


async def _disable_broadcasts(monkeypatch) -> None:
    async def no_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr(AgentRuntimeService, "_broadcast_run_event", no_broadcast)


@pytest.mark.asyncio
async def test_event_bus_adopts_workspace_before_tenant_work(
    db_session,
    auth_context,
    monkeypatch,
):
    matter = await _matter(db_session, auth_context.workspace_id)
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(event_bus, "model_api_execution_allowed", lambda: False)

    run = await event_bus._enqueue_agent_run(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source="column:entrada",
        session_factory=_session_factory(db_session),
    )

    assert run is not None
    assert run.workspace_id == auth_context.workspace_id
    assert run.status == AgentRunStatus.waiting_human
    assert get_workspace_context(db_session) == auth_context.workspace_id


@pytest.mark.asyncio
async def test_event_bus_rejects_matter_from_another_workspace_on_sqlite(
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id)
    other_workspace = Workspace(
        name="Outro workspace EventBus",
        slug=f"event-bus-other-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.commit()

    run = await event_bus._enqueue_agent_run(
        workspace_id=other_workspace.id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source="column:entrada",
        session_factory=_session_factory(db_session),
    )

    assert run is None
    assert get_workspace_context(db_session) == other_workspace.id
    assert await db_session.scalar(select(func.count(AgentRun.id))) == 0


@pytest.mark.asyncio
async def test_missing_agent_deployment_routes_to_human_task(
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id)
    await db_session.execute(
        delete(AgentDeployment).where(
            AgentDeployment.workspace_id == auth_context.workspace_id,
            AgentDeployment.agent_key == "klm_sdr",
        )
    )
    await db_session.commit()

    run = await event_bus._enqueue_agent_run(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source="whatsapp:message",
        session_factory=_session_factory(db_session),
    )

    task = await db_session.scalar(
        select(Task).where(
            Task.matter_id == matter.id,
        )
    )
    assert run is None
    assert task is not None
    assert task.title == "Atuação humana necessária em entrada"
    assert await db_session.scalar(select(func.count(AgentRun.id))) == 0


@pytest.mark.asyncio
async def test_model_agent_is_enqueued_while_external_channels_remain_closed(
    db_session,
    auth_context,
    monkeypatch,
):
    matter = await _matter(db_session, auth_context.workspace_id)
    await _disable_broadcasts(monkeypatch)
    queued: list[tuple[str, str, str]] = []
    monkeypatch.setattr(event_bus, "model_api_execution_allowed", lambda: True)
    monkeypatch.setattr(
        event_bus.execute_agent_run,
        "apply_async",
        lambda *, args, task_id: queued.append((*args, task_id)),
    )

    run = await event_bus._enqueue_agent_run(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source="column:entrada",
        session_factory=_session_factory(db_session),
    )

    assert run is not None
    assert run.status == AgentRunStatus.queued
    assert queued == [
        (str(run.id), str(auth_context.workspace_id), run.celery_task_id)
    ]


@pytest.mark.asyncio
async def test_recovery_republishes_run_after_crash_between_commit_and_publish(
    db_session,
    auth_context,
    monkeypatch,
):
    class SimulatedProcessCrash(BaseException):
        pass

    matter = await _matter(db_session, auth_context.workspace_id)
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(event_bus, "model_api_execution_allowed", lambda: True)

    def crash_after_commit(**kwargs):
        raise SimulatedProcessCrash

    monkeypatch.setattr(event_bus, "_publish_agent_run", crash_after_commit)

    with pytest.raises(SimulatedProcessCrash):
        await event_bus._enqueue_agent_run(
            workspace_id=auth_context.workspace_id,
            matter_id=matter.id,
            agent_name="klm_sdr",
            trigger_source="column:entrada",
            session_factory=_session_factory(db_session),
        )

    run = await db_session.scalar(select(AgentRun).where(AgentRun.matter_id == matter.id))
    assert run is not None
    assert run.status == AgentRunStatus.queued
    assert run.celery_task_id

    published: list[dict] = []
    result = await _recover_agent_runs_async(
        session_factory=_session_factory(db_session),
        workspace_ids=[auth_context.workspace_id],
        now=datetime.now(timezone.utc) + AGENT_DISPATCH_LEASE_TIMEOUT + timedelta(seconds=1),
        publisher=lambda **intent: published.append(intent),
    )

    assert result == {
        "published": 1,
        "publish_failed": 0,
        "waiting_human": 0,
        "failed": 0,
    }
    assert published == [
        {
            "run_id": run.id,
            "workspace_id": auth_context.workspace_id,
            "task_id": run.celery_task_id,
        }
    ]


@pytest.mark.asyncio
async def test_duplicate_event_republishes_existing_queued_run_immediately(
    db_session,
    auth_context,
    monkeypatch,
):
    matter = await _matter(db_session, auth_context.workspace_id)
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(event_bus, "model_api_execution_allowed", lambda: True)
    published: list[dict] = []
    monkeypatch.setattr(
        event_bus,
        "_publish_agent_run",
        lambda **intent: published.append(intent),
    )

    first = await event_bus._enqueue_agent_run(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source="whatsapp:message",
        dedupe_key="whatsapp:wamid-immediate-republish",
        session_factory=_session_factory(db_session),
    )
    second = await event_bus._enqueue_agent_run(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source="whatsapp:message",
        dedupe_key="whatsapp:wamid-immediate-republish",
        session_factory=_session_factory(db_session),
    )

    assert first is not None
    assert second is not None
    assert second.id == first.id
    assert published == [
        {
            "run_id": first.id,
            "workspace_id": auth_context.workspace_id,
            "task_id": first.celery_task_id,
        },
        {
            "run_id": first.id,
            "workspace_id": auth_context.workspace_id,
            "task_id": first.celery_task_id,
        },
    ]


@pytest.mark.asyncio
async def test_agent_run_dedupe_is_workspace_scoped_and_rejects_same_tenant_rebinding(
    db_session,
    auth_context,
    monkeypatch,
):
    first_matter = await _matter(db_session, auth_context.workspace_id)
    second_matter = await _matter(db_session, auth_context.workspace_id)
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(event_bus, "model_api_execution_allowed", lambda: True)
    monkeypatch.setattr(event_bus, "_publish_agent_run", lambda **kwargs: None)

    first = await event_bus._enqueue_agent_run(
        workspace_id=auth_context.workspace_id,
        matter_id=first_matter.id,
        agent_name="klm_sdr",
        trigger_source="whatsapp:message",
        dedupe_key="whatsapp:shared-provider-id",
        session_factory=_session_factory(db_session),
    )
    assert first is not None

    with pytest.raises(ValueError, match="outra execução"):
        await event_bus._enqueue_agent_run(
            workspace_id=auth_context.workspace_id,
            matter_id=second_matter.id,
            agent_name="klm_sdr",
            trigger_source="whatsapp:message",
            dedupe_key="whatsapp:shared-provider-id",
            session_factory=_session_factory(db_session),
        )


def test_agent_run_dedupe_constraint_includes_workspace() -> None:
    constraint = next(
        item
        for item in AgentRun.__table__.constraints
        if item.name == "uq_agent_runs_workspace_dedupe_key"
    )
    assert [column.name for column in constraint.columns] == [
        "workspace_id",
        "dedupe_key",
    ]
