from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import app.services.finance_charge_service as finance_charge_service_module
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.payments.fake_adapter import FakeGatewayAdapter


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_entry(db_session, workspace_id, *, entry_metadata=None):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorários iniciais",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.pending,
        amount=2500,
        due_date=datetime.now(UTC) + timedelta(days=15),
        entry_metadata=entry_metadata or {},
    )
    db_session.add(entry)
    await db_session.flush()
    await db_session.commit()
    return entry


@pytest.mark.asyncio
async def test_create_charge_endpoint_returns_local_draft_when_gate_closed(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id)

    response = await client.post(f"/api/v1/finance/entries/{entry.id}/charge", json={})

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "rascunho"
    assert payload["draftNotice"]
    assert payload["externalId"] is None


@pytest.mark.asyncio
async def test_create_charge_endpoint_is_idempotent(client, db_session, auth_context, monkeypatch):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id)

    first = await client.post(f"/api/v1/finance/entries/{entry.id}/charge", json={})
    assert first.status_code == 201

    second = await client.post(f"/api/v1/finance/entries/{entry.id}/charge", json={})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_create_charge_endpoint_accepts_undefined_billing_type_with_gate_open(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: True)
    fake_gateway = FakeGatewayAdapter()

    async def _fake_get_gateway(db, workspace_id):
        return fake_gateway

    monkeypatch.setattr(finance_charge_service_module, "get_gateway_for_workspace", _fake_get_gateway)

    entry = await _make_entry(
        db_session, auth_context.workspace_id, entry_metadata={"gatewayCustomerId": "cus_fake_000001"}
    )

    response = await client.post(
        f"/api/v1/finance/entries/{entry.id}/charge",
        json={"billingType": "UNDEFINED"},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "criada"
    assert payload["externalId"]
    assert payload["invoiceUrl"]


@pytest.mark.asyncio
async def test_list_and_get_charges(client, db_session, auth_context, monkeypatch):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id)
    create_response = await client.post(f"/api/v1/finance/entries/{entry.id}/charge", json={})
    charge_id = create_response.json()["id"]

    list_response = await client.get("/api/v1/finance/charges", params={"status": "rascunho"})
    assert list_response.status_code == 200
    assert any(item["id"] == charge_id for item in list_response.json())

    get_response = await client.get(f"/api/v1/finance/charges/{charge_id}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == charge_id


@pytest.mark.asyncio
async def test_cancel_charge_endpoint(client, db_session, auth_context, monkeypatch):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id)
    create_response = await client.post(f"/api/v1/finance/entries/{entry.id}/charge", json={})
    charge_id = create_response.json()["id"]

    cancel_response = await client.delete(f"/api/v1/finance/charges/{charge_id}")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelada"

    second_cancel = await client.delete(f"/api/v1/finance/charges/{charge_id}")
    assert second_cancel.status_code == 409


@pytest.mark.asyncio
async def test_rbac_denies_viewer_from_creating_or_cancelling_charges(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id)

    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.viewer
    await db_session.commit()
    auth_context.role = WorkspaceRole.viewer

    create_response = await client.post(f"/api/v1/finance/entries/{entry.id}/charge", json={})
    assert create_response.status_code == 403

    list_response = await client.get("/api/v1/finance/charges")
    assert list_response.status_code == 403
