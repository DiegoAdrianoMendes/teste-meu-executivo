from __future__ import annotations

import pytest

from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.matter import Matter, PracticeArea


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente API Estratégia")
    db_session.add(client)
    await db_session.flush()
    await db_session.commit()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso API estratégia",
    )
    db_session.add(matter)
    await db_session.flush()
    await db_session.commit()
    return matter


async def _make_document(db_session, matter_id) -> Document:
    document = Document(
        matter_id=matter_id,
        name="Contrato.pdf",
        doc_type=DocumentType.contrato,
        status=DocumentStatus.completo,
    )
    db_session.add(document)
    await db_session.flush()
    await db_session.commit()
    return document


async def _make_demand(client, demand_client_id: str, matter_id: str | None = None) -> dict:
    payload = {
        "nature": "judicial",
        "clientId": demand_client_id,
        "area": "civel",
    }
    if matter_id:
        payload["matterId"] = matter_id
    response = await client.post("/api/v1/legal/demands", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_strategy_matrix_flow_fact_proof_pleading_and_gaps(
    client, db_session, auth_context
):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, demand_client.id)
    document = await _make_document(db_session, matter.id)
    demand = await _make_demand(client, str(demand_client.id), str(matter.id))

    fact_response = await client.post(
        f"/api/v1/legal/demands/{demand['id']}/strategy/facts",
        json={"description": "O contrato foi assinado em 05/01/2026.", "isControverted": True},
    )
    assert fact_response.status_code == 201, fact_response.text
    fact = fact_response.json()
    assert fact["isControverted"] is True

    matrix_with_gap = await client.get(f"/api/v1/legal/demands/{demand['id']}/strategy")
    assert matrix_with_gap.status_code == 200
    gap_kinds = {gap["kind"] for gap in matrix_with_gap.json()["gaps"]}
    assert "fato_sem_prova" in gap_kinds

    proof_response = await client.post(
        f"/api/v1/legal/demands/{demand['id']}/strategy/facts/{fact['id']}/proofs",
        json={"documentId": str(document.id), "force": "robusta"},
    )
    assert proof_response.status_code == 201, proof_response.text
    proof = proof_response.json()
    assert proof["status"] == "tem"

    pleading_response = await client.post(
        f"/api/v1/legal/demands/{demand['id']}/strategy/pleadings",
        json={
            "requestText": "Condenação ao cumprimento do contrato.",
            "legalBasis": "Art. 389 do Código Civil.",
            "supportingFactIds": [fact["id"]],
        },
    )
    assert pleading_response.status_code == 201, pleading_response.text

    matrix_after = await client.get(f"/api/v1/legal/demands/{demand['id']}/strategy")
    assert matrix_after.status_code == 200
    payload = matrix_after.json()
    assert len(payload["facts"]) == 1
    assert len(payload["proofLinks"]) == 1
    assert len(payload["pleadings"]) == 1
    assert not any(gap["kind"] == "fato_sem_prova" for gap in payload["gaps"])


async def test_pleading_without_facts_is_flagged_via_api(client, db_session, auth_context):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    demand = await _make_demand(client, str(demand_client.id))

    pleading_response = await client.post(
        f"/api/v1/legal/demands/{demand['id']}/strategy/pleadings",
        json={"requestText": "Pedido genérico sem fato de sustentação."},
    )
    assert pleading_response.status_code == 201, pleading_response.text

    matrix = await client.get(f"/api/v1/legal/demands/{demand['id']}/strategy")
    assert matrix.status_code == 200
    gap_kinds = {gap["kind"] for gap in matrix.json()["gaps"]}
    assert "pedido_sem_fato" in gap_kinds


async def test_strategy_endpoints_require_authentication(unauthenticated_client):
    from uuid import uuid4

    response = await unauthenticated_client.get(
        f"/api/v1/legal/demands/{uuid4()}/strategy"
    )
    assert response.status_code in (401, 403)
