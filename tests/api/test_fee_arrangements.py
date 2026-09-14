from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.approval import ApprovalQueueItem
from app.models.client import Client
from app.models.matter import Matter, PracticeArea


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_matter(db_session, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente Honorários API")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Cobrança de honorários (API)",
    )
    db_session.add(matter)
    await db_session.flush()
    await db_session.commit()
    return matter


@pytest.mark.asyncio
async def test_create_and_generate_fixo_arrangement(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)

    create_response = await client.post(
        "/api/v1/finance/fee-arrangements",
        json={"matterId": str(matter.id), "kind": "fixo", "fixedValue": "5000.00"},
    )
    assert create_response.status_code == 201, create_response.text
    arrangement = create_response.json()
    assert arrangement["kind"] == "fixo"
    assert arrangement["status"] == "ativo"

    generate_response = await client.post(
        f"/api/v1/finance/fee-arrangements/{arrangement['id']}/generate", json={}
    )
    assert generate_response.status_code == 200, generate_response.text
    payload = generate_response.json()
    assert payload["kind"] == "fixo"
    assert payload["entry"]["amount"] == "5000.00" or float(payload["entry"]["amount"]) == 5000.00
    assert payload["charge"]["status"] == "rascunho"


@pytest.mark.asyncio
async def test_create_arrangement_missing_required_field_is_rejected(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)

    response = await client.post(
        "/api/v1/finance/fee-arrangements",
        json={"matterId": str(matter.id), "kind": "fixo"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_close_fee_arrangement_endpoint(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/finance/fee-arrangements",
        json={"matterId": str(matter.id), "kind": "mensalidade", "fixedValue": "800.00"},
    )
    arrangement_id = create_response.json()["id"]

    close_response = await client.post(f"/api/v1/finance/fee-arrangements/{arrangement_id}/close")
    assert close_response.status_code == 200
    assert close_response.json()["status"] == "encerrado"


@pytest.mark.asyncio
async def test_por_hora_time_entry_and_close_invoice_flow(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/finance/fee-arrangements",
        json={"matterId": str(matter.id), "kind": "por_hora", "hourlyRate": "250.00"},
    )
    arrangement_id = create_response.json()["id"]

    time_response = await client.post(
        f"/api/v1/finance/fee-arrangements/{arrangement_id}/time-entries",
        json={
            "userId": str(auth_context.user_id),
            "date": "2026-06-15",
            "minutes": 90,
            "description": "Reunião com cliente",
        },
    )
    assert time_response.status_code == 201, time_response.text
    assert time_response.json()["billed"] is False

    close_response = await client.post(
        f"/api/v1/finance/fee-arrangements/{arrangement_id}/invoices/close",
        json={"competence": "2026-06-01"},
    )
    assert close_response.status_code == 200, close_response.text
    first_entry_id = close_response.json()["id"]

    close_again_response = await client.post(
        f"/api/v1/finance/fee-arrangements/{arrangement_id}/invoices/close",
        json={"competence": "2026-06-01"},
    )
    assert close_again_response.status_code == 200
    assert close_again_response.json()["id"] == first_entry_id


@pytest.mark.asyncio
async def test_success_event_requires_approval_before_billing(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/finance/fee-arrangements",
        json={"matterId": str(matter.id), "kind": "exito", "percent": "15"},
    )
    arrangement_id = create_response.json()["id"]

    register_response = await client.post(
        f"/api/v1/finance/fee-arrangements/{arrangement_id}/success-events",
        json={"baseValue": "50000.00", "requestedBy": str(auth_context.user_id)},
    )
    assert register_response.status_code == 200, register_response.text
    approval_id = register_response.json()["approvalId"]
    assert register_response.json()["status"] == "pending"

    approve_response = await client.post(
        f"/api/v1/finance/fee-arrangements/success-events/{approval_id}/approve"
    )
    assert approve_response.status_code == 200, approve_response.text
    body = approve_response.json()
    assert float(body["entry"]["amount"]) == 7500.00
    assert body["charge"]["status"] == "rascunho"

    # Risco 2 (CRÍTICO): o autor da aprovação é sempre o usuário autenticado —
    # o endpoint não aceita mais `resolvedBy` no corpo.
    approval = await db_session.get(ApprovalQueueItem, approval_id)
    assert approval is not None
    assert approval.resolved_by == auth_context.user_id


@pytest.mark.asyncio
async def test_approve_success_fee_ignores_forged_resolved_by_in_payload(
    client, db_session, auth_context
):
    """Um payload tentando forjar outro autor deve ser ignorado — o backend
    deriva sempre do contexto autenticado (auth.user_id)."""
    matter = await _make_matter(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/finance/fee-arrangements",
        json={"matterId": str(matter.id), "kind": "exito", "percent": "15"},
    )
    arrangement_id = create_response.json()["id"]

    register_response = await client.post(
        f"/api/v1/finance/fee-arrangements/{arrangement_id}/success-events",
        json={"baseValue": "50000.00", "requestedBy": str(auth_context.user_id)},
    )
    approval_id = register_response.json()["approvalId"]

    forged_author = str(uuid4())
    approve_response = await client.post(
        f"/api/v1/finance/fee-arrangements/success-events/{approval_id}/approve",
        json={"resolvedBy": forged_author},
    )
    assert approve_response.status_code == 200, approve_response.text

    approval = await db_session.get(ApprovalQueueItem, approval_id)
    assert approval is not None
    assert str(approval.resolved_by) == str(auth_context.user_id)
    assert str(approval.resolved_by) != forged_author


@pytest.mark.asyncio
async def test_renegotiate_overdue_endpoint(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/finance/fee-arrangements",
        json={"matterId": str(matter.id), "kind": "fixo", "fixedValue": "2000.00"},
    )
    arrangement_id = create_response.json()["id"]
    generate_response = await client.post(
        f"/api/v1/finance/fee-arrangements/{arrangement_id}/generate", json={}
    )
    entry_id = generate_response.json()["entry"]["id"]

    renegotiate_response = await client.post(
        f"/api/v1/finance/entries/{entry_id}/renegotiate",
        json={"installmentCount": 2, "agreedValue": "2200.00"},
    )
    assert renegotiate_response.status_code == 200, renegotiate_response.text
    body = renegotiate_response.json()
    assert len(body["entries"]) == 2
    total = sum(Decimal(str(e["amount"])) for e in body["entries"])
    assert total == Decimal("2200.00")
