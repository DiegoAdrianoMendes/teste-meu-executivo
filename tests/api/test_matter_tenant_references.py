"""Adversarial tenant-boundary tests for Matter foreign references."""

from uuid import uuid4

import pytest

from app.models.client import Client
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _outside_records(db_session):
    suffix = uuid4().hex[:8]
    workspace = Workspace(
        name="Workspace Isolado",
        slug=f"workspace-isolado-{suffix}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    user = User(
        name="Usuário Isolado",
        email=f"isolado-{suffix}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="UI",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    outside_client = Client(
        workspace_id=workspace.id,
        name="Cliente Isolado",
        email=f"cliente-{suffix}@example.com",
    )
    db_session.add(outside_client)
    await db_session.flush()
    return outside_client, user


async def _own_client(client):
    response = await client.post(
        "/api/v1/clients",
        json={"name": "Cliente do Workspace", "email": f"own-{uuid4().hex[:8]}@example.com"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _matter_payload(client_id, **overrides):
    payload = {
        "clientId": str(client_id),
        "practiceArea": "saude_suplementar",
        "demandType": "Negativa de cobertura",
        "columnId": "novo_lead",
        "microStatus": "lead_novo",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_rejects_client_from_another_workspace(client, db_session):
    outside_client, _ = await _outside_records(db_session)
    response = await client.post("/api/v1/matters", json=_matter_payload(outside_client.id))
    assert response.status_code == 422
    assert response.json()["detail"] == "Cliente não pertence ao workspace ativo."


@pytest.mark.asyncio
async def test_create_rejects_responsible_user_from_another_workspace(client, db_session):
    own_client = await _own_client(client)
    _, outside_user = await _outside_records(db_session)
    response = await client.post(
        "/api/v1/matters",
        json=_matter_payload(own_client["id"], responsibleHumanId=str(outside_user.id)),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Responsável humano não pertence ao workspace ativo."


@pytest.mark.asyncio
async def test_update_rejects_agent_from_another_workspace(client, db_session):
    own_client = await _own_client(client)
    created = await client.post("/api/v1/matters", json=_matter_payload(own_client["id"]))
    assert created.status_code == 201, created.text
    _, outside_user = await _outside_records(db_session)
    response = await client.patch(
        f"/api/v1/matters/{created.json()['id']}",
        json={"currentAgentId": str(outside_user.id)},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Agente atual não pertence ao workspace ativo."


@pytest.mark.asyncio
async def test_create_accepts_active_workspace_member(client, auth_context):
    own_client = await _own_client(client)
    response = await client.post(
        "/api/v1/matters",
        json=_matter_payload(own_client["id"], responsibleHumanId=str(auth_context.user_id)),
    )
    assert response.status_code == 201, response.text
    assert response.json()["responsibleHumanId"] == str(auth_context.user_id)
