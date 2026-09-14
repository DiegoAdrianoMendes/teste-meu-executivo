from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.endpoints import agent_runtime
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.matter import Matter

pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_retry_response_returns_persisted_queued_status_without_phantom_human(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    contact = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente retry",
        cpf=uuid4().hex[:11],
        phone="+5511999999999",
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=contact.id,
        demand_type="Retry de agente",
    )
    db_session.add(matter)
    await db_session.flush()
    original = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name="klm_sdr",
        trigger_source="manual",
        status=AgentRunStatus.failed,
        input_payload={},
        attempt_count=1,
    )
    db_session.add(original)
    await db_session.commit()

    retried_id = uuid4()

    async def fake_retry_agent_run(**kwargs):
        assert kwargs == {
            "run_id": original.id,
            "workspace_id": auth_context.workspace_id,
            "triggered_by_user_id": auth_context.user_id,
        }
        return SimpleNamespace(
            id=retried_id,
            status=AgentRunStatus.queued,
        )

    monkeypatch.setattr(agent_runtime, "retry_agent_run", fake_retry_agent_run)

    response = await client.post(
        f"/api/v1/agent-runs/matters/{matter.id}/{original.id}/retry"
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "matterId": str(matter.id),
        "agentRunId": str(retried_id),
        "status": "queued",
        "message": "Retry registrado com sucesso.",
    }
