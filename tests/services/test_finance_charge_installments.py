from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import app.services.finance_charge_service as finance_charge_service_module
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge, FinanceChargeStatus
from app.payments.fake_adapter import FakeGatewayAdapter
from app.services.finance_charge_service import FinanceChargeService


async def _make_entry(db_session, workspace_id, *, amount=100, entry_metadata=None):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorários parcelados",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.pending,
        amount=amount,
        due_date=datetime.now(UTC) + timedelta(days=5),
        entry_metadata=entry_metadata or {},
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


@pytest.mark.asyncio
async def test_installment_series_splits_cents_last_installment_absorbs_rounding(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id, amount=100)
    service = FinanceChargeService(db_session)

    installment_ref, children = await service.create_installment_series(
        auth_context.workspace_id, entry.id, 3
    )

    assert len(children) == 3
    amounts = [Decimal(str(child.amount)) for child in children]
    assert amounts[0] == Decimal("33.33")
    assert amounts[1] == Decimal("33.33")
    assert amounts[2] == Decimal("33.34")
    assert sum(amounts) == Decimal("100.00")

    for index, child in enumerate(children, start=1):
        assert child.entry_metadata["installmentRef"] == installment_ref
        assert child.entry_metadata["installmentIndex"] == index
        assert child.entry_metadata["installmentCount"] == 3


@pytest.mark.asyncio
async def test_installment_series_gate_closed_creates_only_local_draft_charges(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: False)
    entry = await _make_entry(db_session, auth_context.workspace_id, amount=300)
    service = FinanceChargeService(db_session)

    installment_ref, children = await service.create_installment_series(
        auth_context.workspace_id, entry.id, 3
    )

    from sqlalchemy import select

    charges = (
        await db_session.execute(
            select(FinanceCharge).where(FinanceCharge.entry_id.in_([c.id for c in children]))
        )
    ).scalars().all()
    assert len(charges) == 3
    assert all(charge.status == FinanceChargeStatus.rascunho for charge in charges)
    assert all(charge.external_id is None for charge in charges)
    assert all(charge.installment_ref == installment_ref for charge in charges)


@pytest.mark.asyncio
async def test_installment_series_gate_open_calls_gateway_once_with_installment_count(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(finance_charge_service_module, "external_delivery_allowed", lambda: True)
    fake_gateway = FakeGatewayAdapter()

    async def _fake_get_gateway(db, workspace_id):
        return fake_gateway

    monkeypatch.setattr(finance_charge_service_module, "get_gateway_for_workspace", _fake_get_gateway)

    entry = await _make_entry(
        db_session,
        auth_context.workspace_id,
        amount=100,
        entry_metadata={"gatewayCustomerId": "cus_fake_000001"},
    )
    service = FinanceChargeService(db_session)

    installment_ref, children = await service.create_installment_series(
        auth_context.workspace_id, entry.id, 3
    )

    assert len(fake_gateway.created_charges) == 1
    assert fake_gateway.created_charges[0].installment_count == 3
    assert fake_gateway.created_charges[0].total_value == Decimal("100")
    assert len(children) == 3
