"""Central Blackbox listing of agent runs with uncertain provider billing.

Covers the read-only companion of
``POST /platform/workspaces/{workspace_id}/agent-runs/{run_id}/billing-reconciliation``:
the operator needs to *find* the pending runs before deciding on them.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app as fastapi_app
from app.core.deps import get_current_auth_context
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.matter import Matter
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.klm_agent_service import KlmAgentService


def _use_platform_identity(platform_auth_context) -> None:
    async def override_auth_context():
        return platform_auth_context

    fastapi_app.dependency_overrides[get_current_auth_context] = override_auth_context


def _billing_payload(status: str) -> dict:
    return {
        "status": status,
        "summary": "Execução encerrada sem confirmação de cobrança.",
        "billingReconciliation": {
            "status": status,
            "requestStarted": True,
            "reconciliationRequired": status == "reconciliation_required",
            "estimatedCostUsd": "0.0075",
            "provider": "openai",
            "model": "model-test",
            "providerRequestId": "provider-req-1",
            "reservationId": "reservation-1",
            "reservationDay": "2026-07-16",
            "reservedAmountUsd": "0.0075",
        },
    }


async def _matter_for(db_session: AsyncSession, workspace_id) -> Matter:
    client_row = Client(workspace_id=workspace_id, name="Cliente Conciliação")
    db_session.add(client_row)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_row.id,
        demand_type="Caso conciliação de cobrança",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _agent_run(
    db_session: AsyncSession,
    *,
    workspace_id,
    matter_id,
    agent_name: str,
    status: str,
) -> AgentRun:
    run = AgentRun(
        workspace_id=workspace_id,
        matter_id=matter_id,
        agent_name=agent_name,
        trigger_source="test",
        status=AgentRunStatus.failed,
        output_payload=_billing_payload(status),
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _second_workspace(db_session: AsyncSession) -> Workspace:
    workspace = Workspace(
        name="Workspace Vizinho",
        slug="workspace-vizinho",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


@pytest.mark.asyncio
async def test_listing_returns_only_runs_pending_reconciliation(
    client, db_session, auth_context, platform_auth_context
):
    workspace_id = auth_context.workspace_id
    matter = await _matter_for(db_session, workspace_id)
    pending = await _agent_run(
        db_session,
        workspace_id=workspace_id,
        matter_id=matter.id,
        agent_name="agente-pendente",
        status="reconciliation_required",
    )
    await _agent_run(
        db_session,
        workspace_id=workspace_id,
        matter_id=matter.id,
        agent_name="agente-ja-conciliado",
        status="reconciled_charged",
    )
    await _agent_run(
        db_session,
        workspace_id=workspace_id,
        matter_id=matter.id,
        agent_name="agente-sem-cobranca",
        status="not_started",
    )

    _use_platform_identity(platform_auth_context)
    response = await client.get(
        f"/api/v1/platform/workspaces/{workspace_id}/agent-runs/billing-reconciliation/pending"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [item["id"] for item in body["items"]] == [str(pending.id)]
    assert body["items"][0]["agentName"] == "agente-pendente"


@pytest.mark.asyncio
async def test_listing_is_isolated_between_workspaces(
    client, db_session, auth_context, platform_auth_context
):
    workspace_a = auth_context.workspace_id
    matter_a = await _matter_for(db_session, workspace_a)
    run_a = await _agent_run(
        db_session,
        workspace_id=workspace_a,
        matter_id=matter_a.id,
        agent_name="agente-workspace-a",
        status="reconciliation_required",
    )

    workspace_b = await _second_workspace(db_session)
    matter_b = await _matter_for(db_session, workspace_b.id)
    run_b = await _agent_run(
        db_session,
        workspace_id=workspace_b.id,
        matter_id=matter_b.id,
        agent_name="agente-workspace-b",
        status="reconciliation_required",
    )

    _use_platform_identity(platform_auth_context)

    response_a = await client.get(
        f"/api/v1/platform/workspaces/{workspace_a}/agent-runs/billing-reconciliation/pending"
    )

    assert response_a.status_code == 200, response_a.text
    ids_a = [item["id"] for item in response_a.json()["items"]]
    assert ids_a == [str(run_a.id)]
    assert str(run_b.id) not in ids_a
    assert response_a.json()["total"] == 1

    # The mirrored scope is asserted at the service layer because one ORM
    # session is forbidden from switching workspaces (app.db.tenant guard);
    # in production each request opens its own session.
    items_b, total_b = await KlmAgentService(
        db_session
    ).list_billing_reconciliation_pending(workspace_id=workspace_b.id, limit=50, offset=0)
    assert total_b == 1
    assert [item.id for item in items_b] == [run_b.id]


@pytest.mark.asyncio
async def test_listing_paginates_and_reports_empty_state(
    client, db_session, auth_context, platform_auth_context
):
    workspace_id = auth_context.workspace_id
    matter = await _matter_for(db_session, workspace_id)
    for index in range(3):
        await _agent_run(
            db_session,
            workspace_id=workspace_id,
            matter_id=matter.id,
            agent_name=f"agente-{index}",
            status="reconciliation_required",
        )

    _use_platform_identity(platform_auth_context)
    base = f"/api/v1/platform/workspaces/{workspace_id}/agent-runs/billing-reconciliation/pending"

    first_page = await client.get(f"{base}?limit=2&offset=0")
    second_page = await client.get(f"{base}?limit=2&offset=2")
    beyond = await client.get(f"{base}?limit=2&offset=50")

    assert first_page.status_code == 200, first_page.text
    assert first_page.json()["total"] == 3
    assert len(first_page.json()["items"]) == 2
    assert second_page.json()["total"] == 3
    assert len(second_page.json()["items"]) == 1
    # Empty page still reports the honest total instead of pretending zero.
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 3


@pytest.mark.asyncio
async def test_listing_requires_platform_root(client, db_session, auth_context):
    workspace_id = auth_context.workspace_id
    matter = await _matter_for(db_session, workspace_id)
    await _agent_run(
        db_session,
        workspace_id=workspace_id,
        matter_id=matter.id,
        agent_name="agente-protegido",
        status="reconciliation_required",
    )

    response = await client.get(
        f"/api/v1/platform/workspaces/{workspace_id}/agent-runs/billing-reconciliation/pending"
    )

    assert response.status_code == 403, response.text
