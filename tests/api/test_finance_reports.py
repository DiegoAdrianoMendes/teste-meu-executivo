from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_receivable(db_session, workspace_id, *, amount, status, due_date=None, paid_at=None):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorários",
        entry_type=FinanceEntryType.receivable,
        status=status,
        amount=amount,
        due_date=due_date,
        paid_at=paid_at,
    )
    db_session.add(entry)
    await db_session.flush()
    await db_session.commit()
    return entry


@pytest.mark.asyncio
async def test_kpis_endpoint_returns_totals(client, db_session, auth_context):
    today = datetime.now(UTC)
    await _make_receivable(
        db_session,
        auth_context.workspace_id,
        amount=1000,
        status=FinanceEntryStatus.paid,
        due_date=today,
        paid_at=today,
    )

    response = await client.get("/api/v1/finance/reports/kpis")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["recebido"] == "1000.00"
    assert payload["vencido"] == {
        "dias1A15": "0.00",
        "dias16A30": "0.00",
        "dias31A60": "0.00",
        "dias61Mais": "0.00",
        "total": "0.00",
    }


@pytest.mark.asyncio
async def test_dre_endpoint_returns_mensal_detail(client, db_session, auth_context):
    today = datetime.now(UTC)
    await _make_receivable(
        db_session,
        auth_context.workspace_id,
        amount=500,
        status=FinanceEntryStatus.paid,
        due_date=today,
        paid_at=today,
    )

    response = await client.get("/api/v1/finance/reports/dre")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["receitaTotal"] == "500.00"
    assert len(payload["mensal"]) == 12


@pytest.mark.asyncio
async def test_cash_flow_endpoint_returns_series(client, db_session, auth_context):
    due = datetime.now(UTC) + timedelta(days=10)
    await _make_receivable(
        db_session,
        auth_context.workspace_id,
        amount=250,
        status=FinanceEntryStatus.pending,
        due_date=due,
    )

    response = await client.get("/api/v1/finance/reports/cash-flow", params={"dias": 30})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["horizonteDias"] == 30
    assert len(payload["dias"]) == 31
    total_entradas = sum(float(day["entradas"]) for day in payload["dias"])
    assert total_entradas == pytest.approx(250.0)


@pytest.mark.asyncio
async def test_reports_workspace_isolation(client, db_session, auth_context):
    other_workspace_id = uuid4()
    await _make_receivable(
        db_session,
        other_workspace_id,
        amount=999999,
        status=FinanceEntryStatus.paid,
        due_date=datetime.now(UTC),
        paid_at=datetime.now(UTC),
    )

    response = await client.get("/api/v1/finance/reports/kpis")

    assert response.status_code == 200
    assert response.json()["recebido"] == "0.00"


@pytest.mark.asyncio
async def test_rbac_denies_viewer_from_reports(client, db_session, auth_context):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.viewer
    await db_session.commit()
    auth_context.role = WorkspaceRole.viewer

    response = await client.get("/api/v1/finance/reports/kpis")
    assert response.status_code == 403
