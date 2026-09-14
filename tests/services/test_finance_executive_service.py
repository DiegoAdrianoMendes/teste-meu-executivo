from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_dimensional import FinanceAllocation, FinanceCostCenter
from app.models.finance_recurrence import (
    FinanceRecurrence,
    FinanceRecurrenceCycle,
    FinanceRecurrenceKind,
    FinanceRecurrenceStatus,
)
from app.services.finance_executive_service import FinanceExecutiveService

AS_OF = date(2026, 8, 2)


def _dt(day: int) -> datetime:
    return datetime(2026, 8, day, tzinfo=UTC)


@pytest.mark.asyncio
async def test_executive_metrics_are_derived_from_real_finance_data(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    client_a = Client(workspace_id=workspace_id, name="Cliente A")
    client_b = Client(workspace_id=workspace_id, name="Cliente B")
    center = FinanceCostCenter(workspace_id=workspace_id, code="OPER", name="Operacao")
    db_session.add_all([client_a, client_b, center])
    await db_session.flush()

    receivable = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorarios recebidos",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.paid,
        amount=Decimal("1000"),
        due_date=_dt(1),
        paid_at=_dt(2),
    )
    expense = FinanceEntry(
        workspace_id=workspace_id,
        title="Despesa paga",
        entry_type=FinanceEntryType.expense,
        status=FinanceEntryStatus.paid,
        amount=Decimal("400"),
        due_date=_dt(1),
        paid_at=_dt(2),
    )
    open_receivable = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorarios em aberto",
        entry_type=FinanceEntryType.receivable,
        status=FinanceEntryStatus.pending,
        amount=Decimal("500"),
        due_date=_dt(2),
    )
    db_session.add_all([receivable, expense, open_receivable])
    await db_session.flush()

    recurrence = FinanceRecurrence(
        workspace_id=workspace_id,
        client_id=client_a.id,
        entry_title="Mensalidade",
        entry_value=Decimal("200"),
        entry_type=FinanceEntryType.receivable,
        kind=FinanceRecurrenceKind.geracao_programada,
        cycle=FinanceRecurrenceCycle.mensal,
        next_run=_dt(15),
        status=FinanceRecurrenceStatus.ativa,
    )
    db_session.add(recurrence)
    await db_session.flush()

    db_session.add_all(
        [
            FinanceAllocation(
                workspace_id=workspace_id,
                finance_entry_id=receivable.id,
                cost_center_id=center.id,
                client_id=client_a.id,
                percentage=Decimal("80"),
                amount=Decimal("800"),
            ),
            FinanceAllocation(
                workspace_id=workspace_id,
                finance_entry_id=receivable.id,
                cost_center_id=center.id,
                client_id=client_b.id,
                percentage=Decimal("20"),
                amount=Decimal("200"),
            ),
            FinanceAllocation(
                workspace_id=workspace_id,
                finance_entry_id=expense.id,
                cost_center_id=center.id,
                percentage=Decimal("100"),
                amount=Decimal("400"),
            ),
        ]
    )
    await db_session.flush()

    result = await FinanceExecutiveService(db_session).analytics(workspace_id, as_of=AS_OF)

    assert result.arr.value == Decimal("2400")
    assert result.contracted_revenue.value == Decimal("1500")
    assert result.gross_margin.value == Decimal("60")
    assert result.ebitda.value == Decimal("600")
    assert result.burn.value == Decimal("0")
    assert result.break_even.value == Decimal("400")
    assert result.concentration.value == Decimal("80")
    assert result.dimensional_profitability.value == Decimal("600")
    assert result.contribution_margin.value is None
    assert result.contribution_margin.quality == "insufficient_data"
    assert result.runway.value is None


@pytest.mark.asyncio
async def test_executive_metrics_report_missing_data_instead_of_fake_zero(db_session, auth_context):
    result = await FinanceExecutiveService(db_session).analytics(
        auth_context.workspace_id, as_of=AS_OF
    )

    assert result.gross_margin.value is None
    assert result.gross_margin.quality == "insufficient_data"
    assert result.concentration.value is None
    assert result.dimensional_profitability.value is None
    assert result.runway.value is None


@pytest.mark.asyncio
async def test_contribution_margin_uses_only_explicit_cost_behavior(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    db_session.add_all(
        [
            FinanceEntry(
                workspace_id=workspace_id,
                title="Receita recebida",
                entry_type=FinanceEntryType.receivable,
                status=FinanceEntryStatus.paid,
                amount=Decimal("1000"),
                due_date=_dt(1),
                paid_at=_dt(2),
            ),
            FinanceEntry(
                workspace_id=workspace_id,
                title="Custo variável",
                entry_type=FinanceEntryType.expense,
                status=FinanceEntryStatus.paid,
                amount=Decimal("250"),
                due_date=_dt(1),
                paid_at=_dt(2),
                entry_metadata={"cost_behavior": "variable"},
            ),
            FinanceEntry(
                workspace_id=workspace_id,
                title="Custo fixo",
                entry_type=FinanceEntryType.expense,
                status=FinanceEntryStatus.paid,
                amount=Decimal("100"),
                due_date=_dt(1),
                paid_at=_dt(2),
                entry_metadata={"cost_behavior": "fixed"},
            ),
        ]
    )
    await db_session.flush()

    result = await FinanceExecutiveService(db_session).analytics(workspace_id, as_of=AS_OF)

    assert result.contribution_margin.value == Decimal("75")
    assert result.contribution_margin.quality == "derived"
    assert "cost_behavior" in result.contribution_margin.source
