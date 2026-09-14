from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.client import Client
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_client(db_session, workspace_id) -> Client:
    demand_client = Client(workspace_id=workspace_id, name="Cliente API Controladoria")
    db_session.add(demand_client)
    await db_session.flush()
    await db_session.commit()
    return demand_client


async def _make_member(db_session, workspace_id, *, name: str) -> User:
    user = User(
        name=name,
        email=f"controladoria-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="CA",
        color="#654321",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=False,
        )
    )
    await db_session.flush()
    await db_session.commit()
    return user


async def _create_demand(client, demand_client_id, *, area="trabalhista"):
    response = await client.post(
        "/api/v1/legal/demands",
        json={"nature": "judicial", "clientId": str(demand_client_id), "area": area},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_distribution_rule_crud_flow(client, db_session, auth_context):
    candidate = await _make_member(db_session, auth_context.workspace_id, name="Advogado API")

    create_response = await client.post(
        "/api/v1/legal/controladoria/distribution-rules",
        json={
            "name": "Regra API Trabalhista",
            "matchAreas": ["trabalhista"],
            "strategy": "fixed",
            "candidateUserIds": [str(candidate.id)],
            "respectAvailability": False,
        },
    )
    assert create_response.status_code == 201, create_response.text
    rule = create_response.json()
    assert rule["strategy"] == "fixed"
    assert rule["candidateUserIds"] == [str(candidate.id)]

    list_response = await client.get("/api/v1/legal/controladoria/distribution-rules")
    assert list_response.status_code == 200
    assert any(item["id"] == rule["id"] for item in list_response.json()["items"])

    update_response = await client.patch(
        f"/api/v1/legal/controladoria/distribution-rules/{rule['id']}",
        json={"priority": 5, "active": True},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["priority"] == 5

    delete_response = await client.delete(
        f"/api/v1/legal/controladoria/distribution-rules/{rule['id']}"
    )
    assert delete_response.status_code == 204

    list_after_delete = await client.get("/api/v1/legal/controladoria/distribution-rules")
    assert not any(item["id"] == rule["id"] for item in list_after_delete.json()["items"])


async def test_distribution_rule_duplicate_name_conflicts(client, db_session, auth_context):
    payload = {"name": "Regra Duplicada API", "candidateUserIds": []}
    first = await client.post("/api/v1/legal/controladoria/distribution-rules", json=payload)
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/legal/controladoria/distribution-rules", json=payload)
    assert second.status_code == 409


async def test_assign_via_rule_and_read_history(client, db_session, auth_context):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    candidate = await _make_member(db_session, auth_context.workspace_id, name="Advogado Assign API")
    demand = await _create_demand(client, demand_client.id, area="civel-api")

    rule_response = await client.post(
        "/api/v1/legal/controladoria/distribution-rules",
        json={
            "name": "Regra Assign API",
            "matchAreas": ["civel-api"],
            "strategy": "fixed",
            "candidateUserIds": [str(candidate.id)],
            "respectAvailability": False,
        },
    )
    assert rule_response.status_code == 201, rule_response.text

    assign_response = await client.post(
        f"/api/v1/legal/controladoria/demands/{demand['id']}/distribution/assign",
        json={},
    )
    assert assign_response.status_code == 200, assign_response.text
    decision = assign_response.json()
    assert decision["newUserId"] == str(candidate.id)
    assert decision["trigger"] == "regra_automatica"
    assert decision["reason"]

    history_response = await client.get(
        f"/api/v1/legal/controladoria/demands/{demand['id']}/distribution/history"
    )
    assert history_response.status_code == 200
    assert len(history_response.json()["items"]) == 1


async def test_assign_without_matching_rule_returns_409(client, db_session, auth_context):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _create_demand(client, demand_client.id, area="area-sem-regra-api")

    response = await client.post(
        f"/api/v1/legal/controladoria/demands/{demand['id']}/distribution/assign",
        json={},
    )
    assert response.status_code == 409


async def test_manual_redistribution_without_reason_returns_422(client, db_session, auth_context):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    candidate = await _make_member(db_session, auth_context.workspace_id, name="Advogado Manual API")
    demand = await _create_demand(client, demand_client.id, area="manual-api")

    response = await client.post(
        f"/api/v1/legal/controladoria/demands/{demand['id']}/distribution/assign",
        json={"forcedUserId": str(candidate.id)},
    )
    assert response.status_code == 422


async def test_sla_update_and_read_flow(client, db_session, auth_context):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _create_demand(client, demand_client.id, area="sla-api")

    update_response = await client.patch(
        f"/api/v1/legal/controladoria/demands/{demand['id']}/sla",
        json={
            "prazoFatalAt": "2026-08-10T00:00:00Z",
            "margemSegurancaDias": 2,
        },
    )
    assert update_response.status_code == 200, update_response.text
    sla = update_response.json()
    assert sla["prazoFatalAt"].startswith("2026-08-10")
    assert sla["slaEffectiveLayer"] == "prazo_fatal"

    get_response = await client.get(f"/api/v1/legal/controladoria/demands/{demand['id']}/sla")
    assert get_response.status_code == 200
    assert get_response.json()["margemSegurancaDias"] == 2


async def test_painel_endpoint_returns_aggregated_shape(client, db_session, auth_context):
    response = await client.get("/api/v1/legal/controladoria/painel")
    assert response.status_code == 200, response.text
    payload = response.json()
    for key in (
        "publicacoesNaoTratadas",
        "prazosSugeridos",
        "prazosFatais",
        "prazosPorJanela",
        "tarefasSemResponsavel",
        "processosSemMovimentacao",
        "audienciasProximas",
        "documentosPendentes",
        "minutasBloqueadas",
        "excecoesDeAgentes",
        "clientesSemAtualizacao",
    ):
        assert key in payload
    for window_key in ("d7", "d3", "d1", "d0", "atrasados"):
        assert window_key in payload["prazosPorJanela"]
