from __future__ import annotations

from uuid import uuid4

import pytest

from app.api.v1.endpoints.contract_billing import get_contract_billing_extractor
from app.main import app as fastapi_app
from app.models.approval import ApprovalQueueItem
from app.models.client import Client
from app.models.matter import Matter, PracticeArea

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_matter(db_session, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente Contrato F11 (API)")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Contrato de honorários F11 (API)",
    )
    db_session.add(matter)
    await db_session.flush()
    await db_session.commit()
    return matter


def _complete_items() -> list[dict]:
    return [
        {"kind": "fixo", "valor": 4000.0, "vencimento": "2026-08-01", "confidence": 0.95},
        {"kind": "mensalidade", "valor": 700.0, "vencimento": "2026-08-05", "confidence": 0.9},
        {"kind": "exito", "percentual": 20.0, "confidence": 0.9},
    ]


@pytest.fixture(autouse=True)
def _override_extractor():
    async def fake_extractor(contract_text: str) -> dict:
        return {"model": "fake-extractor-v1", "items": _complete_items()}

    async def override():
        return fake_extractor

    fastapi_app.dependency_overrides[get_contract_billing_extractor] = override
    yield
    fastapi_app.dependency_overrides.pop(get_contract_billing_extractor, None)


@pytest.mark.asyncio
async def test_extract_submit_approve_flow(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)

    extract_response = await client.post(
        "/api/v1/finance/contract-billing-proposals/extract",
        json={"matterId": str(matter.id), "contractText": "texto do contrato de honorários"},
    )
    assert extract_response.status_code == 201, extract_response.text
    proposal = extract_response.json()
    assert proposal["status"] == "draft"
    assert len(proposal["items"]) == 3

    get_response = await client.get(
        f"/api/v1/finance/contract-billing-proposals/{proposal['id']}"
    )
    assert get_response.status_code == 200
    assert get_response.json()["id"] == proposal["id"]

    submit_response = await client.post(
        f"/api/v1/finance/contract-billing-proposals/{proposal['id']}/submit", json={}
    )
    assert submit_response.status_code == 200, submit_response.text
    approval_id = submit_response.json()["approvalId"]
    assert submit_response.json()["status"] == "pending"

    approve_response = await client.post(
        f"/api/v1/finance/contract-billing-proposals/approvals/{approval_id}/approve", json={}
    )
    assert approve_response.status_code == 200, approve_response.text
    materialized = approve_response.json()
    assert len(materialized["fixedEntries"]) == 1
    assert len(materialized["recurrences"]) == 1
    assert len(materialized["arrangements"]) == 1

    # Risco 2 (CRÍTICO): o autor da aprovação é sempre o usuário autenticado.
    approval = await db_session.get(ApprovalQueueItem, approval_id)
    assert approval is not None
    assert approval.resolved_by == auth_context.user_id

    # dupla aprovação nunca duplica lançamentos.
    approve_again_response = await client.post(
        f"/api/v1/finance/contract-billing-proposals/approvals/{approval_id}/approve", json={}
    )
    assert approve_again_response.status_code == 409


@pytest.mark.asyncio
async def test_approve_billing_proposal_ignores_forged_resolved_by_in_payload(
    client, db_session, auth_context
):
    """Um payload tentando forjar outro autor deve ser ignorado — o backend
    deriva sempre do contexto autenticado (auth.user_id)."""
    matter = await _make_matter(db_session, auth_context.workspace_id)

    extract_response = await client.post(
        "/api/v1/finance/contract-billing-proposals/extract",
        json={"matterId": str(matter.id), "contractText": "texto do contrato de honorários"},
    )
    proposal_id = extract_response.json()["id"]

    submit_response = await client.post(
        f"/api/v1/finance/contract-billing-proposals/{proposal_id}/submit", json={}
    )
    approval_id = submit_response.json()["approvalId"]

    forged_author = str(uuid4())
    approve_response = await client.post(
        f"/api/v1/finance/contract-billing-proposals/approvals/{approval_id}/approve",
        json={"resolvedBy": forged_author},
    )
    assert approve_response.status_code == 200, approve_response.text

    approval = await db_session.get(ApprovalQueueItem, approval_id)
    assert approval is not None
    assert str(approval.resolved_by) == str(auth_context.user_id)
    assert str(approval.resolved_by) != forged_author


@pytest.mark.asyncio
async def test_reject_proposal_endpoint_materializes_nothing(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)

    extract_response = await client.post(
        "/api/v1/finance/contract-billing-proposals/extract",
        json={"matterId": str(matter.id), "contractText": "texto do contrato de honorários"},
    )
    proposal_id = extract_response.json()["id"]

    submit_response = await client.post(
        f"/api/v1/finance/contract-billing-proposals/{proposal_id}/submit", json={}
    )
    approval_id = submit_response.json()["approvalId"]

    reject_response = await client.post(
        f"/api/v1/finance/contract-billing-proposals/approvals/{approval_id}/reject",
        json={"resolutionReason": "Valores desalinhados com o combinado"},
    )
    assert reject_response.status_code == 200, reject_response.text
    assert reject_response.json()["status"] == "rejected"

    approve_after_reject_response = await client.post(
        f"/api/v1/finance/contract-billing-proposals/approvals/{approval_id}/approve", json={}
    )
    assert approve_after_reject_response.status_code == 409
