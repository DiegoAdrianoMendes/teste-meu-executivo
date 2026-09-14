"""Plano Mestre §14 — políticas de autonomia por evento (CRUD admin).

Distinto do modo HIL simples (`tests/api/test_agent_autonomy_settings.py`):
aqui cada política decide o nível A0-A4 para UM evento concreto (área,
cliente/carteira, agente, ação), com capability/risco/confiança mínima/
orçamento/prazo/idempotency/rollback/aprovação/escalonamento.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.agent_autonomy_policy import AgentAutonomyPolicy
from app.models.klm_operations import KlmAuditEvent
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

pytestmark = pytest.mark.usefixtures("active_subscription")


def _payload(**overrides):
    values = {
        "name": "Preparar minuta de resposta padrão",
        "eventType": "lead_replied",
        "action": "draft_response",
        "autonomyLevel": "A2",
        "riskLevel": "baixo",
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_create_list_and_read_autonomy_policy(client, db_session, auth_context):
    created = await client.post("/api/v1/klm/agents/autonomy/policies", json=_payload())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Preparar minuta de resposta padrão"
    assert body["eventType"] == "lead_replied"
    assert body["autonomyLevel"] == "A2"
    assert body["riskLevel"] == "baixo"
    assert body["approvalRequired"] is True
    assert body["isActive"] is True
    assert body["budgetCurrency"] == "BRL"

    listed = await client.get("/api/v1/klm/agents/autonomy/policies")
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == body["id"] for item in listed.json())

    row = await db_session.scalar(
        select(AgentAutonomyPolicy).where(AgentAutonomyPolicy.id == body["id"])
    )
    assert row is not None
    assert row.workspace_id == auth_context.workspace_id

    event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "agent_autonomy_policy_created",
        )
    )
    assert event is not None
    assert event.after_data["eventType"] == "lead_replied"


@pytest.mark.asyncio
async def test_create_with_confidence_budget_and_sla_round_trips(client):
    created = await client.post(
        "/api/v1/klm/agents/autonomy/policies",
        json=_payload(
            name="Executar lembrete interno de follow-up",
            eventType="follow_up_due",
            action="send_internal_reminder",
            autonomyLevel="A3",
            minConfidence=0.85,
            budgetLimit="1000.50",
            slaMinutes=30,
            idempotencyKeyTemplate="autonomy:{eventType}:{action}",
            rollbackStrategy="Cancelar o lembrete agendado.",
            escalationTargetAgentKey="orquestrador",
        ),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["minConfidence"] == pytest.approx(0.85)
    assert body["budgetLimit"] == "1000.50"
    assert body["slaMinutes"] == 30
    assert body["idempotencyKeyTemplate"] == "autonomy:{eventType}:{action}"
    assert body["rollbackStrategy"] == "Cancelar o lembrete agendado."
    assert body["escalationTargetAgentKey"] == "orquestrador"


@pytest.mark.asyncio
async def test_create_rejects_unknown_autonomy_level(client):
    response = await client.post(
        "/api/v1/klm/agents/autonomy/policies",
        json=_payload(autonomyLevel="A9"),
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_update_autonomy_policy_is_audited(client, db_session, auth_context):
    created = await client.post("/api/v1/klm/agents/autonomy/policies", json=_payload())
    policy_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/klm/agents/autonomy/policies/{policy_id}",
        json={"autonomyLevel": "A3", "approvalRequired": False, "reason": "Banda validada."},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["autonomyLevel"] == "A3"
    assert body["approvalRequired"] is False

    event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "agent_autonomy_policy_updated",
        )
    )
    assert event is not None
    assert event.after_data["autonomyLevel"] == "A3"
    assert event.before_data["autonomyLevel"] == "A2"


@pytest.mark.asyncio
async def test_delete_autonomy_policy_removes_row_and_audits(client, db_session, auth_context):
    created = await client.post("/api/v1/klm/agents/autonomy/policies", json=_payload())
    policy_id = created.json()["id"]

    deleted = await client.delete(f"/api/v1/klm/agents/autonomy/policies/{policy_id}")
    assert deleted.status_code == 204, deleted.text

    row = await db_session.scalar(
        select(AgentAutonomyPolicy).where(AgentAutonomyPolicy.id == policy_id)
    )
    assert row is None

    event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "agent_autonomy_policy_deleted",
        )
    )
    assert event is not None


@pytest.mark.asyncio
async def test_delete_unknown_policy_returns_404(client):
    response = await client.delete(f"/api/v1/klm/agents/autonomy/policies/{uuid4()}")
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_autonomy_policies_are_isolated_per_workspace(client, db_session, auth_context):
    other_workspace = Workspace(
        name="Workspace Autonomia Externa (policies)",
        slug=f"autonomy-policy-other-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    created = await client.post("/api/v1/klm/agents/autonomy/policies", json=_payload())
    policy_id = created.json()["id"]

    from app.services.autonomy_policy_service import AutonomyPolicyService

    with pytest.raises(LookupError):
        await AutonomyPolicyService(db_session).get_policy(other_workspace.id, policy_id)
