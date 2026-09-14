from __future__ import annotations

import pytest

from app.models.client import Client


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente API Demanda Jurídica")
    db_session.add(client)
    await db_session.flush()
    await db_session.commit()
    return client


async def test_create_get_update_transition_and_list_flow(client, db_session, auth_context):
    demand_client = await _make_client(db_session, auth_context.workspace_id)

    create_response = await client.post(
        "/api/v1/legal/demands",
        json={
            "nature": "consultivo",
            "clientId": str(demand_client.id),
            "area": "tributario",
        },
    )
    assert create_response.status_code == 201, create_response.text
    demand = create_response.json()
    assert demand["nature"] == "consultivo"
    assert demand["phase"] == "triagem"

    get_response = await client.get(f"/api/v1/legal/demands/{demand['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == demand["id"]

    update_response = await client.patch(
        f"/api/v1/legal/demands/{demand['id']}",
        json={"strategy": "Parecer preliminar entregue."},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["strategy"] == "Parecer preliminar entregue."

    transition_response = await client.post(
        f"/api/v1/legal/demands/{demand['id']}/transition",
        json={"phase": "em_andamento"},
    )
    assert transition_response.status_code == 200, transition_response.text
    assert transition_response.json()["phase"] == "em_andamento"

    # Idempotente por valor — repetir a mesma fase não quebra.
    repeat_transition = await client.post(
        f"/api/v1/legal/demands/{demand['id']}/transition",
        json={"phase": "em_andamento"},
    )
    assert repeat_transition.status_code == 200
    assert repeat_transition.json()["phase"] == "em_andamento"

    list_response = await client.get("/api/v1/legal/demands", params={"nature": "consultivo"})
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] >= 1
    assert any(item["id"] == demand["id"] for item in payload["items"])


async def test_create_missing_required_field_returns_422(client, db_session, auth_context):
    demand_client = await _make_client(db_session, auth_context.workspace_id)

    response = await client.post(
        "/api/v1/legal/demands",
        json={"nature": "acordo", "clientId": str(demand_client.id)},
    )
    assert response.status_code == 422, response.text


async def test_create_is_idempotent_via_api(client, db_session, auth_context):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    payload = {
        "nature": "massificado",
        "clientId": str(demand_client.id),
        "documentsRequired": ["procuracao"],
        "externalRef": "lote-2026-07-api",
    }

    first = await client.post("/api/v1/legal/demands", json=payload)
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/legal/demands", json=payload)
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]

    list_response = await client.get(
        "/api/v1/legal/demands", params={"nature": "massificado"}
    )
    matching = [
        item
        for item in list_response.json()["items"]
        if item["externalRef"] == "lote-2026-07-api"
    ]
    assert len(matching) == 1


async def test_get_unknown_demand_returns_404(client):
    from uuid import uuid4

    response = await client.get(f"/api/v1/legal/demands/{uuid4()}")
    assert response.status_code == 404


async def test_legal_demand_endpoints_require_authentication(unauthenticated_client):
    response = await unauthenticated_client.get("/api/v1/legal/demands")
    assert response.status_code in (401, 403)
