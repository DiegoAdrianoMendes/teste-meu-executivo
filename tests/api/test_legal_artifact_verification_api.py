from __future__ import annotations

import pytest

from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.schemas.klm_operations import LegalArtifactVersionCreate
from app.services.klm_operations_service import KlmOperationsService


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente API Verificação")
    db_session.add(client)
    await db_session.flush()
    await db_session.commit()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso API verificação",
    )
    db_session.add(matter)
    await db_session.flush()
    await db_session.commit()
    return matter


async def _make_demand(client, db_session, workspace_id, demand_client_id, matter_id) -> str:
    response = await client.post(
        "/api/v1/legal/demands",
        json={
            "nature": "judicial",
            "clientId": str(demand_client_id),
            "area": "civel",
            "matterId": str(matter_id),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _make_artifact(db_session, workspace_id, matter, user_id, *, content):
    artifact = await KlmOperationsService(db_session).create_legal_artifact_version(
        workspace_id,
        user_id,
        LegalArtifactVersionCreate(
            matter_id=matter.id,
            kind="petition",
            title="Petição — API verificação",
            content=content,
        ),
    )
    await db_session.commit()
    return artifact


async def test_verify_citations_endpoint_honestly_marks_unverified_by_default(
    client, db_session, auth_context
):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, demand_client.id)
    demand_id = await _make_demand(
        client, db_session, auth_context.workspace_id, demand_client.id, matter.id
    )
    artifact = await _make_artifact(
        db_session,
        auth_context.workspace_id,
        matter,
        auth_context.user_id,
        content={"sections": [{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}]},
    )

    response = await client.post(
        f"/api/v1/legal/demands/{demand_id}/artifacts/{artifact.id}/verify-citations"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allVerified"] is False
    assert body["gateOpen"] is False
    assert len(body["verifications"]) == 1
    assert body["verifications"][0]["status"] == "nao_verificada"
    assert body["verifications"][0]["sourceReference"] is None


async def test_review_endpoint_flags_placeholder_and_blocks_protocolability(
    client, db_session, auth_context
):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, demand_client.id)
    demand_id = await _make_demand(
        client, db_session, auth_context.workspace_id, demand_client.id, matter.id
    )
    artifact = await _make_artifact(
        db_session,
        auth_context.workspace_id,
        matter,
        auth_context.user_id,
        content={"sections": [{"title": "Qualificação", "body": "Autor: [NOME DO AUTOR]."}]},
    )

    response = await client.post(
        f"/api/v1/legal/demands/{demand_id}/artifacts/{artifact.id}/review"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["isProtocolable"] is False
    kinds = {finding["kind"] for finding in body["findings"]}
    assert "placeholder" in kinds


async def test_verify_citations_returns_404_for_artifact_of_another_demand(
    client, db_session, auth_context
):
    demand_client = await _make_client(db_session, auth_context.workspace_id)
    matter_a = await _make_matter(db_session, auth_context.workspace_id, demand_client.id)
    matter_b = await _make_matter(db_session, auth_context.workspace_id, demand_client.id)
    demand_id = await _make_demand(
        client, db_session, auth_context.workspace_id, demand_client.id, matter_a.id
    )
    artifact_b = await _make_artifact(
        db_session,
        auth_context.workspace_id,
        matter_b,
        auth_context.user_id,
        content={"sections": [{"title": "X", "body": "Sem citação."}]},
    )

    response = await client.post(
        f"/api/v1/legal/demands/{demand_id}/artifacts/{artifact_b.id}/verify-citations"
    )
    assert response.status_code == 422, response.text
