from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect

from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge, FinanceChargeKind, FinanceChargeStatus
from app.models.finance_recurrence import (
    FinanceRecurrence,
    FinanceRecurrenceCycle,
    FinanceRecurrenceKind,
    FinanceRecurrenceStatus,
)
from app.services.finance_reports_service import (
    CREDIT_CARD_SETTLEMENT_DAYS,
    FinanceReportsService,
    _add_months,
)

AS_OF = date(2026, 7, 25)


@pytest.mark.asyncio
async def test_dre_category_expression_reuses_one_postgresql_bind_parameter():
    """Protege a consulta DRE contra GroupingError específico do PostgreSQL."""
    class _Result:
        def all(self):
            return []

        def one(self):
            return (0,) * 24

    class _CapturingSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return _Result()

    session = _CapturingSession()
    await FinanceReportsService(session).dre(uuid4(), competencia=AS_OF)

    compiled = session.statements[0].compile(
        dialect=asyncpg_dialect(paramstyle="numeric_dollar")
    )

    assert str(compiled).count("$1::VARCHAR") == 2
    assert [value for value in compiled.params.values() if value == "sem_categoria"] == [
        "sem_categoria"
    ]


def _dt(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


async def _make_receivable(
    db_session,
    workspace_id,
    *,
    amount,
    status=FinanceEntryStatus.pending,
    due_date=None,
    paid_at=None,
    category=None,
):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Honorários",
        entry_type=FinanceEntryType.receivable,
        status=status,
        amount=amount,
        due_date=due_date,
        paid_at=paid_at,
        category=category,
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


async def _make_expense(
    db_session,
    workspace_id,
    *,
    amount,
    status=FinanceEntryStatus.pending,
    due_date=None,
    paid_at=None,
    category=None,
):
    entry = FinanceEntry(
        workspace_id=workspace_id,
        title="Despesa",
        entry_type=FinanceEntryType.expense,
        status=status,
        amount=amount,
        due_date=due_date,
        paid_at=paid_at,
        category=category,
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


async def _make_charge(
    db_session,
    workspace_id,
    entry_id,
    *,
    billing_type=None,
    due_date=None,
    paid_at=None,
    status=FinanceChargeStatus.criada,
):
    charge = FinanceCharge(
        workspace_id=workspace_id,
        entry_id=entry_id,
        billing_type=billing_type,
        charge_kind=FinanceChargeKind.avulsa,
        status=status,
        value=0,
        due_date=due_date,
        paid_at=paid_at,
    )
    db_session.add(charge)
    await db_session.flush()
    return charge


async def _make_recurrence(
    db_session,
    workspace_id,
    client_id,
    *,
    entry_value,
    next_run,
    cycle=FinanceRecurrenceCycle.mensal,
    end_date=None,
    max_count=None,
    entry_type=FinanceEntryType.receivable,
    status=FinanceRecurrenceStatus.ativa,
):
    recurrence = FinanceRecurrence(
        workspace_id=workspace_id,
        client_id=client_id,
        entry_title="Assinatura",
        entry_value=entry_value,
        entry_type=entry_type,
        kind=FinanceRecurrenceKind.geracao_programada,
        cycle=cycle,
        next_run=next_run,
        end_date=end_date,
        max_count=max_count,
        status=status,
    )
    db_session.add(recurrence)
    await db_session.flush()
    return recurrence


@pytest.mark.asyncio
async def test_kpis_recebido_a_receber_aging_faturado_e_resultado(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    start_date = AS_OF - timedelta(days=60)
    end_date = AS_OF

    # Recebido no período: 1 entry paga, due_date dentro do período (conta em "faturado" também).
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("1000"),
        status=FinanceEntryStatus.paid,
        due_date=_dt(AS_OF - timedelta(days=3)),
        paid_at=_dt(AS_OF - timedelta(days=1)),
    )
    # Fora do período (não deve contar no recebido).
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("500"),
        status=FinanceEntryStatus.paid,
        due_date=_dt(AS_OF - timedelta(days=200)),
        paid_at=_dt(AS_OF - timedelta(days=200)),
    )
    # A receber (aberto, não vencido) — fora do período de faturamento (due_date > end_date).
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("300"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF + timedelta(days=10)),
    )
    # Vencidos — aging buckets.
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("100"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF - timedelta(days=5)),
    )
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("200"),
        status=FinanceEntryStatus.overdue,
        due_date=_dt(AS_OF - timedelta(days=20)),
    )
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("300"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF - timedelta(days=45)),
    )
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("400"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF - timedelta(days=90)),
    )
    # Vencido cancelado — não deve entrar em nenhum bucket nem no faturado.
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("999"),
        status=FinanceEntryStatus.cancelled,
        due_date=_dt(AS_OF - timedelta(days=10)),
    )
    # Despesa paga no período.
    await _make_expense(
        db_session,
        workspace_id,
        amount=Decimal("200"),
        status=FinanceEntryStatus.paid,
        due_date=_dt(AS_OF - timedelta(days=2)),
        paid_at=_dt(AS_OF - timedelta(days=1)),
    )

    client = Client(workspace_id=workspace_id, name="Cliente KPI")
    db_session.add(client)
    await db_session.flush()
    await _make_recurrence(
        db_session, workspace_id, client.id, entry_value=Decimal("500"), next_run=AS_OF + timedelta(days=5)
    )

    kpis = await FinanceReportsService(db_session).kpis(
        workspace_id, start_date=start_date, end_date=end_date, as_of=AS_OF
    )

    assert kpis.recebido == Decimal("1000.00")
    assert kpis.despesas == Decimal("200.00")
    assert kpis.resultado == Decimal("800.00")
    assert kpis.a_receber == Decimal("300.00")
    assert kpis.ticket_medio == Decimal("1000.00")
    assert kpis.mrr == Decimal("500.00")

    assert kpis.vencido.dias_1_a_15 == Decimal("100.00")
    assert kpis.vencido.dias_16_a_30 == Decimal("200.00")
    assert kpis.vencido.dias_31_a_60 == Decimal("300.00")
    assert kpis.vencido.dias_61_mais == Decimal("400.00")
    assert kpis.vencido.total == Decimal("1000.00")

    # faturado = pago (1000) + vencidos dentro do período (100+200+300=600) = 1600
    # inadimplência = vencido_total(1000) / faturado(1600) * 100 = 62.5
    assert kpis.inadimplencia_pct == Decimal("62.50")


@pytest.mark.asyncio
async def test_kpis_workspace_isolation(db_session, auth_context):
    other_workspace_id = uuid4()
    await _make_receivable(
        db_session,
        other_workspace_id,
        amount=Decimal("999999"),
        status=FinanceEntryStatus.paid,
        due_date=_dt(AS_OF),
        paid_at=_dt(AS_OF),
    )

    kpis = await FinanceReportsService(db_session).kpis(
        auth_context.workspace_id,
        start_date=AS_OF - timedelta(days=30),
        end_date=AS_OF,
        as_of=AS_OF,
    )

    assert kpis.recebido == Decimal("0.00")
    assert kpis.vencido.total == Decimal("0.00")


@pytest.mark.asyncio
async def test_dre_categorias_e_detalhe_mensal(db_session, auth_context):
    workspace_id = auth_context.workspace_id
    current_month_start = date(AS_OF.year, AS_OF.month, 1)
    three_months_back = _add_months(current_month_start, -3) + timedelta(days=10)

    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("1000"),
        status=FinanceEntryStatus.paid,
        paid_at=_dt(AS_OF),
        category="honorarios",
    )
    await _make_expense(
        db_session,
        workspace_id,
        amount=Decimal("300"),
        status=FinanceEntryStatus.paid,
        paid_at=_dt(AS_OF),
        category="aluguel",
    )
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("200"),
        status=FinanceEntryStatus.paid,
        paid_at=_dt(three_months_back),
        category="honorarios",
    )
    # Não realizado (pending) — não deve entrar no DRE (só considera pagos).
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("5000"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF),
        category="honorarios",
    )

    dre = await FinanceReportsService(db_session).dre(workspace_id, competencia=AS_OF)

    assert dre.receita_total == Decimal("1200.00")
    assert dre.despesa_total == Decimal("300.00")
    assert dre.resultado == Decimal("900.00")

    receitas_by_cat = {c.categoria: c.valor for c in dre.receitas_por_categoria}
    despesas_by_cat = {c.categoria: c.valor for c in dre.despesas_por_categoria}
    assert receitas_by_cat["honorarios"] == Decimal("1200.00")
    assert despesas_by_cat["aluguel"] == Decimal("300.00")

    mensal_by_competencia = {m.competencia: m for m in dre.mensal}
    assert len(dre.mensal) == 12
    current_key = current_month_start.strftime("%Y-%m")
    back_key = _add_months(current_month_start, -3).strftime("%Y-%m")
    assert mensal_by_competencia[current_key].receitas == Decimal("1000.00")
    assert mensal_by_competencia[current_key].despesas == Decimal("300.00")
    assert mensal_by_competencia[current_key].resultado == Decimal("700.00")
    assert mensal_by_competencia[back_key].receitas == Decimal("200.00")
    assert mensal_by_competencia[back_key].despesas == Decimal("0.00")


@pytest.mark.asyncio
async def test_dre_workspace_isolation(db_session, auth_context):
    other_workspace_id = uuid4()
    await _make_receivable(
        db_session,
        other_workspace_id,
        amount=Decimal("777"),
        status=FinanceEntryStatus.paid,
        paid_at=_dt(AS_OF),
        category="honorarios",
    )

    dre = await FinanceReportsService(db_session).dre(auth_context.workspace_id, competencia=AS_OF)

    assert dre.receita_total == Decimal("0.00")
    assert dre.receitas_por_categoria == []


@pytest.mark.asyncio
async def test_cash_flow_projection_models_card_d32_and_recurrence_and_expenses(
    db_session, auth_context
):
    workspace_id = auth_context.workspace_id

    # Entry receivable ligada a charge de cartão: deve liquidar em paid_at + 32 dias,
    # não no vencimento original.
    card_entry = await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("1000"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF + timedelta(days=5)),
    )
    await _make_charge(
        db_session,
        workspace_id,
        card_entry.id,
        billing_type="CREDIT_CARD",
        due_date=_dt(AS_OF + timedelta(days=5)),
        paid_at=_dt(AS_OF + timedelta(days=5)),
        status=FinanceChargeStatus.confirmada,
    )

    # Entry receivable sem charge (boleto/pix "cru"): projeta no próprio vencimento.
    await _make_receivable(
        db_session,
        workspace_id,
        amount=Decimal("500"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF + timedelta(days=10)),
    )

    # Recorrência ativa com 1 única ocorrência dentro do horizonte (end_date logo após next_run).
    client = Client(workspace_id=workspace_id, name="Cliente Fluxo")
    db_session.add(client)
    await db_session.flush()
    await _make_recurrence(
        db_session,
        workspace_id,
        client.id,
        entry_value=Decimal("200"),
        next_run=AS_OF + timedelta(days=3),
        end_date=AS_OF + timedelta(days=10),
    )

    # Despesa a pagar dentro do horizonte.
    await _make_expense(
        db_session,
        workspace_id,
        amount=Decimal("300"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF + timedelta(days=5)),
    )

    projection = await FinanceReportsService(db_session).cash_flow_projection(
        workspace_id, dias=90, as_of=AS_OF
    )

    by_date = {day.data: day for day in projection.dias}

    card_settlement_day = AS_OF + timedelta(days=5 + CREDIT_CARD_SETTLEMENT_DAYS)
    assert by_date[card_settlement_day].entradas == Decimal("1000.00")
    # No dia do vencimento original do cartão não deve haver entrada (só saída da despesa).
    assert by_date[AS_OF + timedelta(days=5)].entradas == Decimal("0.00")
    assert by_date[AS_OF + timedelta(days=5)].saidas == Decimal("300.00")

    assert by_date[AS_OF + timedelta(days=10)].entradas == Decimal("500.00")
    assert by_date[AS_OF + timedelta(days=3)].entradas == Decimal("200.00")

    # Saldo acumulado no último dia = soma de todas as entradas menos todas as saídas.
    total_entradas = Decimal("1000.00") + Decimal("500.00") + Decimal("200.00")
    total_saidas = Decimal("300.00")
    assert projection.dias[-1].saldo_acumulado == total_entradas - total_saidas


@pytest.mark.asyncio
async def test_cash_flow_projection_workspace_isolation(db_session, auth_context):
    other_workspace_id = uuid4()
    await _make_receivable(
        db_session,
        other_workspace_id,
        amount=Decimal("123456"),
        status=FinanceEntryStatus.pending,
        due_date=_dt(AS_OF + timedelta(days=5)),
    )

    projection = await FinanceReportsService(db_session).cash_flow_projection(
        auth_context.workspace_id, dias=30, as_of=AS_OF
    )

    assert all(day.entradas == Decimal("0.00") for day in projection.dias)
    assert all(day.saidas == Decimal("0.00") for day in projection.dias)
