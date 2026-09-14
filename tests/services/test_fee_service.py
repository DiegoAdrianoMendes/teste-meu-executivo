from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.approval import ApprovalDecision, ApprovalStatus
from app.models.client import Client
from app.models.fee_arrangement import FeeArrangement, FeeArrangementKind, FeeArrangementStatus, TimeEntry
from app.models.finance import FinanceEntry
from app.models.finance_charge import FinanceCharge, FinanceChargeStatus
from app.models.matter import Matter, PracticeArea
from app.schemas.approval import ApprovalResolveRequest
from app.schemas.fee_arrangement import FeeArrangementCreate
from app.services.approval_service import ApprovalService
from app.services.fee_service import FeeArrangementConflictError, FeeService
from fastapi import HTTPException


async def _make_matter(db_session, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente Honorários")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Cobrança de honorários",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


# ── fixo ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fixo_arrangement_generates_entry_and_charge(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(matter_id=matter.id, kind=FeeArrangementKind.fixo, fixed_value=Decimal("5000.00")),
    )

    entry, charge = await service.generate_charge_from_arrangement(
        auth_context.workspace_id, arrangement.id
    )

    assert isinstance(entry, FinanceEntry)
    assert isinstance(charge, FinanceCharge)
    assert entry.amount == Decimal("5000.00")
    assert charge.entry_id == entry.id
    assert charge.status == FinanceChargeStatus.rascunho


@pytest.mark.asyncio
async def test_fixo_arrangement_without_fixed_value_is_rejected(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    with pytest.raises(HTTPException) as exc:
        await service.create_arrangement(
            auth_context.workspace_id,
            FeeArrangementCreate(matter_id=matter.id, kind=FeeArrangementKind.fixo),
        )
    assert exc.value.status_code == 422


# ── entrada + parcelas ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entrada_exito_arrangement_generates_installment_series(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(
            matter_id=matter.id, kind=FeeArrangementKind.entrada_exito, fixed_value=Decimal("9000.00")
        ),
    )

    installment_ref, entries = await service.generate_charge_from_arrangement(
        auth_context.workspace_id, arrangement.id, installment_count=3
    )

    assert isinstance(installment_ref, str)
    assert len(entries) == 3
    total = sum((Decimal(str(e.amount)) for e in entries), Decimal("0"))
    assert total == Decimal("9000.00")


# ── mensalidade ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mensalidade_arrangement_generates_recurrence(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(
            matter_id=matter.id, kind=FeeArrangementKind.mensalidade, fixed_value=Decimal("1200.00")
        ),
    )

    recurrence = await service.generate_charge_from_arrangement(auth_context.workspace_id, arrangement.id)

    assert recurrence.entry_value == Decimal("1200.00")
    assert recurrence.matter_id == matter.id


# ── por_hora ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_por_hora_close_monthly_invoice_twice_yields_single_invoice(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(
            matter_id=matter.id, kind=FeeArrangementKind.por_hora, hourly_rate=Decimal("300.00")
        ),
    )

    await service.log_time(
        auth_context.workspace_id,
        arrangement.id,
        user_id=auth_context.user_id,
        entry_date=date(2026, 6, 10),
        minutes=120,
        description="Audiência",
    )
    await service.log_time(
        auth_context.workspace_id,
        arrangement.id,
        user_id=auth_context.user_id,
        entry_date=date(2026, 6, 20),
        minutes=60,
        description="Petição",
    )

    competence = date(2026, 6, 1)
    first_invoice = await service.close_monthly_invoice(auth_context.workspace_id, arrangement.id, competence)
    # 120+60 minutos = 3h * 300 = 900.00
    assert first_invoice.amount == Decimal("900.00")

    second_invoice = await service.close_monthly_invoice(auth_context.workspace_id, arrangement.id, competence)
    assert second_invoice.id == first_invoice.id

    charges = (
        await db_session.execute(
            FinanceCharge.__table__.select().where(FinanceCharge.entry_id == first_invoice.id)
        )
    ).fetchall()
    assert len(charges) == 1

    time_entries = (
        await db_session.execute(TimeEntry.__table__.select().where(TimeEntry.arrangement_id == arrangement.id))
    ).fetchall()
    assert all(row.billed for row in time_entries)
    assert all(row.invoice_entry_id == first_invoice.id for row in time_entries)


@pytest.mark.asyncio
async def test_por_hora_close_monthly_invoice_without_hours_raises(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(
            matter_id=matter.id, kind=FeeArrangementKind.por_hora, hourly_rate=Decimal("300.00")
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await service.close_monthly_invoice(auth_context.workspace_id, arrangement.id, date(2026, 6, 1))
    assert exc.value.status_code == 422


# ── êxito ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_event_without_approval_does_not_create_entry_or_charge(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(matter_id=matter.id, kind=FeeArrangementKind.exito, percent=Decimal("10")),
    )

    approval = await service.register_success_event(
        auth_context.workspace_id,
        arrangement.id,
        Decimal("100000.00"),
        requested_by=auth_context.user_id,
    )

    assert approval.status == ApprovalStatus.pending

    entries = (
        await db_session.execute(FinanceEntry.__table__.select().where(FinanceEntry.matter_id == matter.id))
    ).fetchall()
    assert entries == []


@pytest.mark.asyncio
async def test_success_event_approved_creates_entry_and_charge_with_percent_value(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(matter_id=matter.id, kind=FeeArrangementKind.exito, percent=Decimal("10")),
    )

    approval = await service.register_success_event(
        auth_context.workspace_id,
        arrangement.id,
        Decimal("100000.00"),
        requested_by=auth_context.user_id,
    )

    entry, charge = await service.approve_success_fee(
        auth_context.workspace_id, approval.id, resolved_by=auth_context.user_id
    )

    assert entry.amount == Decimal("10000.00")
    assert charge.entry_id == entry.id
    assert charge.value == Decimal("10000.00")


@pytest.mark.asyncio
async def test_success_event_rejected_creates_nothing(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(matter_id=matter.id, kind=FeeArrangementKind.exito, percent=Decimal("10")),
    )
    approval = await service.register_success_event(
        auth_context.workspace_id,
        arrangement.id,
        Decimal("100000.00"),
        requested_by=auth_context.user_id,
    )

    await ApprovalService(db_session, auth_context.workspace_id).resolve(
        approval.id,
        ApprovalResolveRequest(status=ApprovalDecision.rejected, resolved_by=auth_context.user_id),
    )

    entries = (
        await db_session.execute(FinanceEntry.__table__.select().where(FinanceEntry.matter_id == matter.id))
    ).fetchall()
    assert entries == []


@pytest.mark.asyncio
async def test_approve_success_fee_twice_is_rejected_by_approval_cas(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(matter_id=matter.id, kind=FeeArrangementKind.exito, percent=Decimal("10")),
    )
    approval = await service.register_success_event(
        auth_context.workspace_id,
        arrangement.id,
        Decimal("100000.00"),
        requested_by=auth_context.user_id,
    )
    await service.approve_success_fee(auth_context.workspace_id, approval.id, resolved_by=auth_context.user_id)

    with pytest.raises(HTTPException) as exc:
        await service.approve_success_fee(auth_context.workspace_id, approval.id, resolved_by=auth_context.user_id)
    assert exc.value.status_code == 409

    entries = (
        await db_session.execute(FinanceEntry.__table__.select().where(FinanceEntry.matter_id == matter.id))
    ).fetchall()
    assert len(entries) == 1


# ── renegociação ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_renegotiate_overdue_cancels_charge_and_creates_new_series(db_session, auth_context):
    matter = await _make_matter(db_session, auth_context.workspace_id)
    service = FeeService(db_session)
    arrangement = await service.create_arrangement(
        auth_context.workspace_id,
        FeeArrangementCreate(matter_id=matter.id, kind=FeeArrangementKind.fixo, fixed_value=Decimal("3000.00")),
    )
    entry, charge = await service.generate_charge_from_arrangement(auth_context.workspace_id, arrangement.id)
    original_charge_id = charge.id

    installment_ref, new_entries = await service.renegotiate_overdue(
        auth_context.workspace_id,
        entry.id,
        installment_count=3,
        agreed_value=Decimal("3300.00"),
        first_due_date=date.today() + timedelta(days=5),
        requested_by=auth_context.user_id,
    )

    await db_session.refresh(charge)
    assert charge.status == FinanceChargeStatus.cancelada
    assert len(new_entries) == 3
    total = sum((Decimal(str(e.amount)) for e in new_entries), Decimal("0"))
    assert total == Decimal("3300.00")

    from app.models.klm_operations import KlmAuditEvent

    audit = (
        await db_session.execute(
            KlmAuditEvent.__table__.select().where(
                KlmAuditEvent.event_type == "fee_overdue_renegotiated",
                KlmAuditEvent.entity_id == entry.id,
            )
        )
    ).fetchone()
    assert audit is not None
    assert audit.before_data["chargeId"] == str(original_charge_id)
