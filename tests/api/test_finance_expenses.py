from __future__ import annotations

import pytest

from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryType
from app.models.matter import Matter
from app.models.workspace import Workspace
from app.services.finance_expense_service import DEFAULT_EXPENSE_APPROVAL_LIMIT


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_matter(db_session, workspace_id):
    contact = Client(
        workspace_id=workspace_id,
        name="Cliente despesa",
        cpf="11122233344",
        phone="+5511988887777",
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=contact.id,
        demand_type="Caso com despesa",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


@pytest.mark.asyncio
async def test_create_expense_endpoint_below_limit_needs_no_approval(client, db_session, auth_context):
    response = await client.post(
        "/api/v1/finance/expenses",
        json={"title": "Custas processuais", "amount": 250.0, "category": "custas"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["approvalStatus"] == "not_required"
    assert payload["entryType"] == "expense"
    assert payload["category"] == "custas"


@pytest.mark.asyncio
async def test_create_expense_endpoint_above_limit_requires_approval(client, db_session, auth_context):
    amount = DEFAULT_EXPENSE_APPROVAL_LIMIT + 500
    response = await client.post(
        "/api/v1/finance/expenses",
        json={"title": "Perícia técnica", "amount": amount, "category": "pericia"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["approvalStatus"] == "pending"


@pytest.mark.asyncio
async def test_expense_categories_seed_present(client, db_session, auth_context):
    response = await client.get("/api/v1/finance/expenses/categories")
    assert response.status_code == 200, response.text
    categories = response.json()["categories"]
    for expected in (
        "custas",
        "diligencia",
        "pericia",
        "honorario_repassado",
        "pro_labore",
        "aluguel",
        "software",
        "salarios",
    ):
        assert expected in categories


@pytest.mark.asyncio
async def test_reimbursement_generated_exactly_once(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    entry = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        title="Diligência externa",
        entry_type=FinanceEntryType.expense,
        amount=180.0,
        reimbursable=True,
    )
    db_session.add(entry)
    await db_session.commit()

    first = await client.post(f"/api/v1/finance/expenses/{entry.id}/reimbursement")
    assert first.status_code == 200, first.text
    first_mirror_id = first.json()["id"]

    second = await client.post(f"/api/v1/finance/expenses/{entry.id}/reimbursement")
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first_mirror_id

    # Confere no banco que só existe UMA entry receivable-espelho.
    from sqlalchemy import select

    result = await db_session.execute(
        select(FinanceEntry).where(
            FinanceEntry.workspace_id == auth_context.workspace_id,
            FinanceEntry.entry_type == FinanceEntryType.receivable,
        )
    )
    mirrors = result.scalars().all()
    assert len(mirrors) == 1


@pytest.mark.asyncio
async def test_reimbursement_requires_reimbursable_flag(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    entry = FinanceEntry(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        title="Despesa não reembolsável",
        entry_type=FinanceEntryType.expense,
        amount=90.0,
        reimbursable=False,
    )
    db_session.add(entry)
    await db_session.commit()

    response = await client.post(f"/api/v1/finance/expenses/{entry.id}/reimbursement")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_approve_expense_endpoint_unblocks_reimbursement(client, db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    amount = DEFAULT_EXPENSE_APPROVAL_LIMIT + 100
    create_response = await client.post(
        "/api/v1/finance/expenses",
        json={
            "title": "Diligência cara",
            "amount": amount,
            "reimbursable": True,
            "matterId": str(matter.id),
        },
    )
    entry_id = create_response.json()["id"]

    blocked = await client.post(f"/api/v1/finance/expenses/{entry_id}/reimbursement")
    assert blocked.status_code == 409

    approve = await client.post(
        f"/api/v1/finance/expenses/{entry_id}/approve", json={"decision": "approved"}
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["approvalStatus"] == "approved"

    unblocked = await client.post(f"/api/v1/finance/expenses/{entry_id}/reimbursement")
    assert unblocked.status_code == 200, unblocked.text

    # Idempotência mantida mesmo após aprovação + reembolso já gerado.
    again = await client.post(f"/api/v1/finance/expenses/{entry_id}/reimbursement")
    assert again.status_code == 200
    assert again.json()["id"] == unblocked.json()["id"]
