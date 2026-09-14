from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import app.services.finance_charge_service as finance_charge_service_module
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge, FinanceChargeStatus
from app.payments.fake_adapter import FakeGatewayAdapter
from app.payments.port import BillingType
from app.schemas.finance_charge import FinanceChargeCreate
from app.services.finance_charge_service import (
    DRAFT_NOTICE,
    FinanceChargeConflictError,
    FinanceChargeService,
)


async def _make_entry(db_session, workspace_id, *, entry_metadata=None):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorários iniciais",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.pending,
        amount=1500,
        due_date=datetime.now(UTC) + timedelta(days=10),
        entry_metadata=entry_metadata or {},
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


@pytest.mark.asyncio
async def test_create_charge_gate_closed_yields_local_draft_without_gateway_call(
    db_session, auth_context, monkeypatch
):
    """Gate externo fechado (default de teste) => rascunho local, sem chamar o gateway."""
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)

    called = False

    async def _fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("get_gateway_for_workspace não deveria ser chamado com o gate fechado")

    monkeypatch.setattr(finance_charge_service_module, "get_gateway_for_workspace", _fail_if_called)

    entry = await _make_entry(db_session, auth_context.workspace_id)
    service = FinanceChargeService(db_session)

    charge = await service.create_charge_for_entry(
        auth_context.workspace_id, entry.id, FinanceChargeCreate()
    )

    assert called is False
    assert charge.status == FinanceChargeStatus.rascunho
    assert charge.external_id is None
    assert charge.payload_snapshot["aviso"] == DRAFT_NOTICE
    assert charge.value == Decimal("1500")


@pytest.mark.asyncio
async def test_create_charge_is_idempotent_per_entry(db_session, auth_context, monkeypatch):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id)
    service = FinanceChargeService(db_session)

    await service.create_charge_for_entry(auth_context.workspace_id, entry.id, FinanceChargeCreate())

    with pytest.raises(FinanceChargeConflictError):
        await service.create_charge_for_entry(
            auth_context.workspace_id, entry.id, FinanceChargeCreate()
        )


@pytest.mark.asyncio
async def test_create_charge_accepts_undefined_billing_type_with_gate_open(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: True)
    fake_gateway = FakeGatewayAdapter()

    async def _fake_get_gateway(db, workspace_id):
        return fake_gateway

    monkeypatch.setattr(finance_charge_service_module, "get_gateway_for_workspace", _fake_get_gateway)

    entry = await _make_entry(
        db_session, auth_context.workspace_id, entry_metadata={"gatewayCustomerId": "cus_fake_000001"}
    )
    service = FinanceChargeService(db_session)

    charge = await service.create_charge_for_entry(
        auth_context.workspace_id, entry.id, FinanceChargeCreate(billing_type=BillingType.UNDEFINED)
    )

    assert charge.status == FinanceChargeStatus.criada
    assert charge.external_id is not None
    assert charge.invoice_url is not None
    assert len(fake_gateway.created_charges) == 1
    assert fake_gateway.created_charges[0].billing_type == BillingType.UNDEFINED


@pytest.mark.asyncio
async def test_create_charge_gate_open_without_customer_id_fails_clearly(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: True)
    entry = await _make_entry(db_session, auth_context.workspace_id)
    service = FinanceChargeService(db_session)

    with pytest.raises(Exception) as exc_info:
        await service.create_charge_for_entry(
            auth_context.workspace_id, entry.id, FinanceChargeCreate()
        )
    assert "422" in str(exc_info.value) or "sincronizado" in str(exc_info.value)


@pytest.mark.asyncio
async def test_cancel_charge_only_allowed_in_valid_states(db_session, auth_context, monkeypatch):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id)
    service = FinanceChargeService(db_session)
    charge = await service.create_charge_for_entry(
        auth_context.workspace_id, entry.id, FinanceChargeCreate()
    )

    cancelled = await service.cancel_charge(auth_context.workspace_id, charge.id)
    assert cancelled.status == FinanceChargeStatus.cancelada

    with pytest.raises(Exception):
        await service.cancel_charge(auth_context.workspace_id, charge.id)


@pytest.mark.asyncio
async def test_cancel_charge_with_gateway_calls_adapter_when_gate_open(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: True)
    fake_gateway = FakeGatewayAdapter()

    async def _fake_get_gateway(db, workspace_id):
        return fake_gateway

    monkeypatch.setattr(finance_charge_service_module, "get_gateway_for_workspace", _fake_get_gateway)

    entry = await _make_entry(
        db_session, auth_context.workspace_id, entry_metadata={"gatewayCustomerId": "cus_fake_000001"}
    )
    service = FinanceChargeService(db_session)
    charge = await service.create_charge_for_entry(
        auth_context.workspace_id, entry.id, FinanceChargeCreate()
    )

    cancelled = await service.cancel_charge(auth_context.workspace_id, charge.id)

    assert cancelled.status == FinanceChargeStatus.cancelada
    assert fake_gateway.cancelled_charge_ids == [charge.external_id]
