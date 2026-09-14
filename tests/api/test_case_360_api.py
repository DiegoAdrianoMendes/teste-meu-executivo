from __future__ import annotations

from uuid import uuid4

from app.models.client import Client
from app.models.matter import Matter, PracticeArea


async def _make_client(db_session, workspace_id, *, name: str = "Cliente API Caso 360") -> Client:
    client = Client(workspace_id=workspace_id, name=name)
    db_session.add(client)
    await db_session.flush()
    await db_session.commit()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso 360 API — teste",
    )
    db_session.add(matter)
    await db_session.flush()
    await db_session.commit()
    return matter


async def test_case_360_consultivo_sem_matter_devolve_blocos_vazios_honestos(
    client, db_session, auth_context, active_subscription
):
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
    demand_id = create_response.json()["id"]

    response = await client.get(f"/api/v1/legal/demands/{demand_id}/case-360")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["demandId"] == demand_id
    assert body["demandaContratada"]["nature"] == "consultivo"
    assert body["oportunidade"] is None
    assert body["cliente"]["id"] == str(demand_client.id)
    assert body["processos"] == []
    assert body["documentos"] == []
    assert body["financeiro"]["entries"] == []
    assert float(body["financeiro"]["totals"]["totalReceivable"]) == 0
    assert body["comunicacoes"]["totalCount"] == 0
    assert body["excecoesAbertas"] == []
    assert body["timeline"] == []
    assert body["provas"]["legalDemandId"] == demand_id


async def test_case_360_judicial_com_matter_e_partes(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, demand_client.id)

    create_response = await client.post(
        "/api/v1/legal/demands",
        json={
            "nature": "judicial",
            "clientId": str(demand_client.id),
            "area": "civel",
        },
    )
    assert create_response.status_code == 201, create_response.text
    demand_id = create_response.json()["id"]

    update_response = await client.patch(
        f"/api/v1/legal/demands/{demand_id}",
        json={"matterId": str(matter.id)},
    )
    assert update_response.status_code == 200, update_response.text

    party_response = await client.post(
        f"/api/v1/legal/demands/{demand_id}/parties",
        json={"name": "Contraparte S.A.", "pole": "passivo", "role": "réu"},
    )
    assert party_response.status_code == 201, party_response.text
    assert party_response.json()["pole"] == "passivo"

    list_parties_response = await client.get(f"/api/v1/legal/demands/{demand_id}/parties")
    assert list_parties_response.status_code == 200
    assert list_parties_response.json()["items"][0]["name"] == "Contraparte S.A."

    case_360_response = await client.get(f"/api/v1/legal/demands/{demand_id}/case-360")
    assert case_360_response.status_code == 200, case_360_response.text
    body = case_360_response.json()

    assert body["oportunidade"]["matterId"] == str(matter.id)
    assert body["demandaContratada"]["matterId"] == str(matter.id)
    assert len(body["partes"]) == 1
    assert body["partes"][0]["pole"] == "passivo"


async def test_case_360_unknown_demand_returns_404(client, active_subscription):
    response = await client.get(f"/api/v1/legal/demands/{uuid4()}/case-360")
    assert response.status_code == 404


async def test_case_360_requires_authentication(unauthenticated_client):
    response = await unauthenticated_client.get(
        f"/api/v1/legal/demands/{uuid4()}/case-360"
    )
    assert response.status_code in (401, 403)
