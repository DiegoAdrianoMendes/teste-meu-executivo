from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.bank_reconciliation import (
    ReconciliationMatch,
    ReconciliationMethod,
    ReconciliationStatus,
    ReconciliationTargetKind,
)
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge, FinanceChargeGateway, FinanceChargeStatus
from app.services.reconciliation_service import ReconciliationService

OFX_SAMPLE = """
OFXHEADER:100
DATA:OFXSGML

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260701120000
<TRNAMT>1500.00
<FITID>fit-0001
<MEMO>Recebimento cliente
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTRNRS>
</BANKMSGSRSV1>
</OFX>
"""

CSV_SAMPLE = "date,value,description\n2026-07-02,250.00,Aluguel escritorio\n"


async def _make_entry_and_charge(db_session, workspace_id, *, value=Decimal("1500.00"), due_date=None):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorários",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.pending,
        amount=value,
        due_date=due_date or (datetime.now(UTC) - timedelta(days=1)),
    )
    db_session.add(entry)
    await db_session.flush()

    charge = FinanceCharge(
        workspace_id=workspace_id,
        entry_id=entry.id,
        gateway=FinanceChargeGateway.asaas,
        external_id="pay_fake_000001",
        status=FinanceChargeStatus.confirmada,
        value=value,
        due_date=due_date or (datetime.now(UTC) - timedelta(days=1)),
    )
    db_session.add(charge)
    await db_session.flush()
    return entry, charge


@pytest.mark.asyncio
async def test_ingest_ofx_is_idempotent_on_reimport(db_session, auth_context):
    service = ReconciliationService(db_session)

    first = await service.ingest_ofx(auth_context.workspace_id, OFX_SAMPLE.encode("utf-8"))
    assert first.imported == 1
    assert first.duplicates == 0

    second = await service.ingest_ofx(auth_context.workspace_id, OFX_SAMPLE.encode("utf-8"))
    assert second.imported == 0
    assert second.duplicates == 1


@pytest.mark.asyncio
async def test_ingest_ofx_matches_known_charge_deterministically(db_session, auth_context):
    due_date = datetime(2026, 7, 1, tzinfo=UTC)
    entry, charge = await _make_entry_and_charge(
        db_session, auth_context.workspace_id, value=Decimal("1500.00"), due_date=due_date
    )
    service = ReconciliationService(db_session)

    result = await service.ingest_ofx(auth_context.workspace_id, OFX_SAMPLE.encode("utf-8"))
    assert result.matched_deterministic == 1
    assert result.suggested == 0

    await db_session.refresh(charge)
    assert charge.status == FinanceChargeStatus.recebida

    await db_session.refresh(entry)
    assert entry.status == FinanceEntryStatus.paid


@pytest.mark.asyncio
async def test_ingest_csv_creates_suggestion_never_applies_alone(db_session, auth_context):
    service = ReconciliationService(db_session)
    result = await service.ingest_csv(auth_context.workspace_id, CSV_SAMPLE.encode("utf-8"))

    assert result.imported == 1
    assert result.suggested == 1

    pending = await service.list_pending(auth_context.workspace_id)
    assert len(pending) == 1
    match = pending[0]
    assert match.status == ReconciliationStatus.sugerido
    assert match.target_id is None
    # Sugestão de IA nunca aplica nada sozinha.
    assert match.method in (ReconciliationMethod.deterministico, ReconciliationMethod.ia)


@pytest.mark.asyncio
async def test_confirm_match_applies_once_idempotently(db_session, auth_context):
    entry, charge = await _make_entry_and_charge(db_session, auth_context.workspace_id)
    service = ReconciliationService(db_session)

    result = await service.ingest_csv(auth_context.workspace_id, CSV_SAMPLE.encode("utf-8"))
    assert result.suggested == 1
    pending = await service.list_pending(auth_context.workspace_id)
    match = pending[0]

    confirmed = await service.confirm_match(
        auth_context.workspace_id,
        match.id,
        target_kind=ReconciliationTargetKind.entry,
        target_id=entry.id,
        confirmed_by=auth_context.user_id,
        category="aluguel",
    )
    assert confirmed.status == ReconciliationStatus.confirmado

    await db_session.refresh(entry)
    assert entry.status == FinanceEntryStatus.paid
    assert entry.category == "aluguel"

    # Confirmar de novo é no-op (idempotência sagrada): não relança exceção
    # nem reaplica a baixa/categorização.
    confirmed_again = await service.confirm_match(
        auth_context.workspace_id,
        match.id,
        target_kind=ReconciliationTargetKind.entry,
        target_id=entry.id,
        confirmed_by=auth_context.user_id,
    )
    assert confirmed_again.status == ReconciliationStatus.confirmado


@pytest.mark.asyncio
async def test_asaas_payment_fee_becomes_expense_automatically(db_session, auth_context, monkeypatch):
    import app.services.reconciliation_service as reconciliation_module

    class _FakeGateway:
        async def get_statement(self, start, finish):
            from app.payments.port import StatementEntry, StatementPage

            entry = StatementEntry(
                transaction_type="PAYMENT_FEE",
                value=Decimal("-4.99"),
                date=start,
                description="Taxa Asaas",
                raw={},
            )
            return StatementPage(entries=(entry,), total_count=1, has_more=False)

    monkeypatch.setattr(reconciliation_module, "external_delivery_allowed", lambda: True)

    async def _fake_get_gateway(db, workspace_id):
        return _FakeGateway()

    monkeypatch.setattr(reconciliation_module, "get_gateway_for_workspace", _fake_get_gateway)

    service = ReconciliationService(db_session)
    result = await service.pull_asaas_statement(
        auth_context.workspace_id, datetime(2026, 7, 1).date(), datetime(2026, 7, 31).date()
    )

    assert result.imported == 1
    assert result.matched_deterministic == 1
    assert result.suggested == 0

    from sqlalchemy import select

    expense = (
        await db_session.execute(
            select(FinanceEntry).where(
                FinanceEntry.workspace_id == auth_context.workspace_id,
                FinanceEntry.entry_type == FinanceEntryType.expense,
            )
        )
    ).scalar_one()
    assert expense.category == "taxa_gateway"
    assert expense.status == FinanceEntryStatus.paid

    match = (
        await db_session.execute(
            select(ReconciliationMatch).where(ReconciliationMatch.workspace_id == auth_context.workspace_id)
        )
    ).scalar_one()
    assert match.status == ReconciliationStatus.confirmado
    assert match.method == ReconciliationMethod.deterministico
