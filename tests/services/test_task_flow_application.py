from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.klm_operations import KlmAuditEvent
from app.services.flow_service import (
    FlowService,
    TaskFlowApplicationError,
    TaskFlowIdempotencyConflict,
)


@pytest.mark.asyncio
async def test_task_flow_application_replays_workspace_idempotency_key():
    workspace_id, task_id, flow_id = uuid4(), uuid4(), uuid4()
    existing = SimpleNamespace(task_id=task_id, flow_id=flow_id)
    db = SimpleNamespace(scalar=AsyncMock(return_value=existing))

    result = await FlowService(db).start_task_flow_run(
        task_id=task_id,
        flow_id=flow_id,
        workspace_id=workspace_id,
        actor_user_id=uuid4(),
        idempotency_key="same-request",
    )

    assert result is existing

    with pytest.raises(TaskFlowIdempotencyConflict):
        await FlowService(db).start_task_flow_run(
            task_id=uuid4(),
            flow_id=flow_id,
            workspace_id=workspace_id,
            actor_user_id=uuid4(),
            idempotency_key="same-request",
        )


@pytest.mark.asyncio
async def test_task_flow_application_rejects_revision_that_creates_task(monkeypatch):
    workspace_id, task_id, flow_id, matter_id = uuid4(), uuid4(), uuid4(), uuid4()
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[None, SimpleNamespace(matter_id=matter_id)])
    )
    service = FlowService(db)
    service.get_flow = AsyncMock(return_value=SimpleNamespace(id=flow_id))
    service._current_revision_for_flow = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(
        "app.services.flow_service.verified_flow_revision_snapshot",
        lambda _revision: {"nodes": [{"id": "duplicate", "type": "create_task"}]},
    )

    with pytest.raises(TaskFlowApplicationError, match="cria tarefas"):
        await service.start_task_flow_run(
            task_id=task_id,
            flow_id=flow_id,
            workspace_id=workspace_id,
            actor_user_id=uuid4(),
            idempotency_key="new-request",
        )


@pytest.mark.asyncio
async def test_task_flow_application_audits_human_authorization_once(monkeypatch):
    workspace_id, task_id, flow_id, matter_id, actor_id, run_id, revision_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[None, SimpleNamespace(id=task_id, matter_id=matter_id)]),
        add=MagicMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = FlowService(db)
    flow = SimpleNamespace(id=flow_id, name="Fluxo jurídico")
    revision = SimpleNamespace(id=revision_id, revision_number=3)
    run = SimpleNamespace(id=run_id, task_id=task_id, flow_id=flow_id)
    service.get_flow = AsyncMock(return_value=flow)
    service._current_revision_for_flow = AsyncMock(return_value=revision)
    service.start_flow_run = AsyncMock(return_value=run)
    service.publish_initial_flow_run = lambda *_args: True
    monkeypatch.setattr(
        "app.services.flow_service.verified_flow_revision_snapshot",
        lambda _revision: {"name": "Fluxo jurídico", "nodes": []},
    )
    monkeypatch.setattr(
        "app.services.flow_service.resolve_human_actor_label",
        AsyncMock(return_value="Responsável jurídico"),
    )

    result = await service.start_task_flow_run(
        task_id=task_id,
        flow_id=flow_id,
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        idempotency_key="human-approved-workflow",
    )

    assert result is run
    audit_events = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], KlmAuditEvent)
    ]
    assert len(audit_events) == 1
    assert audit_events[0].event_type == "task_workflow_applied"
    assert audit_events[0].after_data["taskId"] == str(task_id)
    assert audit_events[0].after_data["flowRevisionId"] == str(revision_id)
