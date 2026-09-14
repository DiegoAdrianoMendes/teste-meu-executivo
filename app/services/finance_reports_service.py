"""
Service: FinanceReportsService
KPIs, DRE gerencial e fluxo de caixa projetado (F10) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F10.

Sem tabela nova: tudo é derivado de `FinanceEntry` (F1), `FinanceCharge` (F2)
e `FinanceRecurrence` (F3) já existentes. Agregações pesadas (somas, aging,
detalhe mensal) são feitas via SQL (`func.sum`/`case`) para não trazer linha
a linha para o Python; a única exceção é a expansão de recorrências futuras
no fluxo de caixa projetado, que é aritmética de datas em memória sobre um
conjunto pequeno (recorrências ativas do workspace), não uma query por
linha — não há N+1.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_charge import FinanceCharge
from app.models.finance_recurrence import (
    FinanceRecurrence,
    FinanceRecurrenceCycle,
    FinanceRecurrenceStatus,
)
from app.payments.port import BillingType
from app.workers.finance_tasks import _advance_date  # reaproveita o cálculo de ciclo do worker (F3)

_ZERO = Decimal("0")
_CENTS = Decimal("0.01")

# Prazo de liquidação do cartão de crédito no Asaas (CREATED → CONFIRMED →
# RECEIVED em D+32 após a confirmação — plano §3.3).
CREDIT_CARD_SETTLEMENT_DAYS = 32

# Status de FinanceEntry que ainda estão "em aberto" (nem pagos, nem
# cancelados) — usados tanto para "a receber"/"vencido" quanto para saídas
# projetadas no fluxo de caixa.
_OPEN_ENTRY_STATUSES = (
    FinanceEntryStatus.pending,
    FinanceEntryStatus.scheduled,
    FinanceEntryStatus.overdue,
)

# Fator de normalização mensal por ciclo de recorrência (para MRR).
_MRR_MONTH_FACTOR: dict[FinanceRecurrenceCycle, Decimal] = {
    FinanceRecurrenceCycle.semanal: Decimal("52") / Decimal("12"),
    FinanceRecurrenceCycle.quinzenal: Decimal("26") / Decimal("12"),
    FinanceRecurrenceCycle.mensal: Decimal("1"),
    FinanceRecurrenceCycle.bimestral: Decimal("1") / Decimal("2"),
    FinanceRecurrenceCycle.trimestral: Decimal("1") / Decimal("3"),
    FinanceRecurrenceCycle.semestral: Decimal("1") / Decimal("6"),
    FinanceRecurrenceCycle.anual: Decimal("1") / Decimal("12"),
}


def _dec(value) -> Decimal:
    """Normaliza um valor numérico vindo do banco (Decimal/float/int/None) para Decimal."""
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_CENTS)


def _day_start(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


def _day_end(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time(), tzinfo=UTC)


def _add_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    import calendar

    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass
class AgingBuckets:
    dias_1_a_15: Decimal = _ZERO
    dias_16_a_30: Decimal = _ZERO
    dias_31_a_60: Decimal = _ZERO
    dias_61_mais: Decimal = _ZERO

    @property
    def total(self) -> Decimal:
        return self.dias_1_a_15 + self.dias_16_a_30 + self.dias_31_a_60 + self.dias_61_mais


@dataclass
class FinanceKpis:
    period_start: date
    period_end: date
    as_of: date
    recebido: Decimal
    a_receber: Decimal
    vencido: AgingBuckets
    ticket_medio: Decimal
    inadimplencia_pct: Decimal
    mrr: Decimal
    despesas: Decimal
    resultado: Decimal


@dataclass
class DreCategoriaValor:
    categoria: str
    valor: Decimal


@dataclass
class DreMensal:
    competencia: str
    receitas: Decimal
    despesas: Decimal
    resultado: Decimal


@dataclass
class FinanceDre:
    competencia: str
    periodo_inicio: date
    periodo_fim: date
    receitas_por_categoria: list[DreCategoriaValor]
    despesas_por_categoria: list[DreCategoriaValor]
    receita_total: Decimal
    despesa_total: Decimal
    resultado: Decimal
    mensal: list[DreMensal]
    basis: str = "cash"
    budget_variance: Decimal | None = None


@dataclass
class CashFlowDay:
    data: date
    entradas: Decimal
    saidas: Decimal
    saldo_dia: Decimal
    saldo_acumulado: Decimal


@dataclass
class CashFlowProjection:
    as_of: date
    horizonte_dias: int
    dias: list[CashFlowDay] = field(default_factory=list)


class FinanceReportsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── KPIs ──────────────────────────────────────────────────────────────

    async def kpis(
        self,
        workspace_id: UUID,
        *,
        start_date: date,
        end_date: date,
        as_of: date | None = None,
    ) -> FinanceKpis:
        as_of = as_of or datetime.now(UTC).date()
        start_dt = _day_start(start_date)
        end_dt = _day_end(end_date)
        as_of_dt = _day_start(as_of)

        recebido, count_recebido = await self._sum_and_count(
            FinanceEntry.entry_type == FinanceEntryType.receivable,
            FinanceEntry.status == FinanceEntryStatus.paid,
            FinanceEntry.paid_at >= start_dt,
            FinanceEntry.paid_at <= end_dt,
            workspace_id=workspace_id,
        )
        despesas, _ = await self._sum_and_count(
            FinanceEntry.entry_type == FinanceEntryType.expense,
            FinanceEntry.status == FinanceEntryStatus.paid,
            FinanceEntry.paid_at >= start_dt,
            FinanceEntry.paid_at <= end_dt,
            workspace_id=workspace_id,
        )

        a_receber, _ = await self._sum_and_count(
            FinanceEntry.entry_type == FinanceEntryType.receivable,
            FinanceEntry.status.in_(_OPEN_ENTRY_STATUSES),
            or_(FinanceEntry.due_date.is_(None), FinanceEntry.due_date >= as_of_dt),
            workspace_id=workspace_id,
        )

        aging = await self._aging_buckets(workspace_id, as_of_dt)

        # "Faturado" no período = total de receivables não cancelados com
        # vencimento no período (proxy de faturamento para % de inadimplência).
        faturado, _ = await self._sum_and_count(
            FinanceEntry.entry_type == FinanceEntryType.receivable,
            FinanceEntry.status != FinanceEntryStatus.cancelled,
            FinanceEntry.due_date >= start_dt,
            FinanceEntry.due_date <= end_dt,
            workspace_id=workspace_id,
        )
        inadimplencia_pct = (
            _q2((aging.total / faturado) * Decimal("100")) if faturado > _ZERO else _ZERO
        )

        ticket_medio = _q2(recebido / count_recebido) if count_recebido else _ZERO

        mrr = await self._mrr(workspace_id)

        return FinanceKpis(
            period_start=start_date,
            period_end=end_date,
            as_of=as_of,
            recebido=_q2(recebido),
            a_receber=_q2(a_receber),
            vencido=aging,
            ticket_medio=ticket_medio,
            inadimplencia_pct=inadimplencia_pct,
            mrr=_q2(mrr),
            despesas=_q2(despesas),
            resultado=_q2(recebido - despesas),
        )

    async def _sum_and_count(self, *conditions, workspace_id: UUID) -> tuple[Decimal, int]:
        result = await self.db.execute(
            select(
                func.coalesce(func.sum(FinanceEntry.amount), 0),
                func.count(FinanceEntry.id),
            ).where(FinanceEntry.workspace_id == workspace_id, *conditions)
        )
        total, count = result.one()
        return _dec(total), int(count or 0)

    async def _aging_buckets(self, workspace_id: UUID, as_of_dt: datetime) -> AgingBuckets:
        b15 = as_of_dt - timedelta(days=15)
        b30 = as_of_dt - timedelta(days=30)
        b60 = as_of_dt - timedelta(days=60)

        result = await self.db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    FinanceEntry.due_date < as_of_dt, FinanceEntry.due_date >= b15
                                ),
                                FinanceEntry.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("d1_15"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(FinanceEntry.due_date < b15, FinanceEntry.due_date >= b30),
                                FinanceEntry.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("d16_30"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(FinanceEntry.due_date < b30, FinanceEntry.due_date >= b60),
                                FinanceEntry.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("d31_60"),
                func.coalesce(
                    func.sum(case((FinanceEntry.due_date < b60, FinanceEntry.amount), else_=0)),
                    0,
                ).label("d61_mais"),
            ).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.entry_type == FinanceEntryType.receivable,
                FinanceEntry.status.notin_((FinanceEntryStatus.paid, FinanceEntryStatus.cancelled)),
                FinanceEntry.due_date.is_not(None),
                FinanceEntry.due_date < as_of_dt,
            )
        )
        d1_15, d16_30, d31_60, d61_mais = result.one()
        return AgingBuckets(
            dias_1_a_15=_q2(_dec(d1_15)),
            dias_16_a_30=_q2(_dec(d16_30)),
            dias_31_a_60=_q2(_dec(d31_60)),
            dias_61_mais=_q2(_dec(d61_mais)),
        )

    async def _mrr(self, workspace_id: UUID) -> Decimal:
        """MRR = soma das recorrências ativas (receivable) normalizada para
        um mês, via `case` por ciclo — uma única query agregada."""
        cycle_factor_case = case(
            *[
                (FinanceRecurrence.cycle == cycle, FinanceRecurrence.entry_value * factor)
                for cycle, factor in _MRR_MONTH_FACTOR.items()
            ],
            else_=FinanceRecurrence.entry_value,
        )
        result = await self.db.execute(
            select(func.coalesce(func.sum(cycle_factor_case), 0)).where(
                FinanceRecurrence.workspace_id == workspace_id,
                FinanceRecurrence.status == FinanceRecurrenceStatus.ativa,
                FinanceRecurrence.entry_type == FinanceEntryType.receivable,
            )
        )
        return _dec(result.scalar())

    # ── DRE ───────────────────────────────────────────────────────────────

    async def dre(
        self,
        workspace_id: UUID,
        *,
        competencia: date | None = None,
        basis: str = "cash",
        cost_center_id: UUID | None = None,
        client_id: UUID | None = None,
        contract_id: UUID | None = None,
        matter_id: UUID | None = None,
        compare_budget: bool = False,
    ) -> FinanceDre:
        if basis not in {"cash", "accrual"}:
            raise ValueError("basis must be cash or accrual")
        if basis == "accrual" or any(
            value is not None for value in (cost_center_id, client_id, contract_id, matter_id)
        ):
            from app.services.finance_dimensional_service import FinanceDimensionalService

            reference = competencia or datetime.now(UTC).date()
            period_start = date(reference.year, 1, 1)
            raw = await FinanceDimensionalService(self.db).dre(
                workspace_id,
                mode=basis,
                period_start=period_start,
                period_end=reference,
                cost_center_id=cost_center_id,
                client_id=client_id,
                contract_id=contract_id,
                matter_id=matter_id,
            )
            line_values = {item["key"]: _dec(item["amount"]) for item in raw["lines"]}
            return FinanceDre(
                competencia=reference.strftime("%Y-%m"),
                periodo_inicio=period_start,
                periodo_fim=reference,
                receitas_por_categoria=[
                    DreCategoriaValor(
                        categoria="receita_alocada",
                        valor=_q2(line_values.get("gross_revenue", _ZERO)),
                    )
                ],
                despesas_por_categoria=[
                    DreCategoriaValor(
                        categoria="despesa_alocada",
                        valor=_q2(abs(line_values.get("expenses", _ZERO))),
                    )
                ],
                receita_total=_q2(line_values.get("net_revenue", _ZERO)),
                despesa_total=_q2(abs(line_values.get("expenses", _ZERO))),
                resultado=_q2(line_values.get("result", _ZERO)),
                mensal=[],
                basis=basis,
                budget_variance=(
                    _q2(_dec(raw["budget"]["variance"]))
                    if compare_budget and raw.get("budget") is not None
                    else None
                ),
            )
        reference = competencia or datetime.now(UTC).date()
        ref_month_start = date(reference.year, reference.month, 1)
        start_month = _add_months(ref_month_start, -11)
        period_end_month = _add_months(ref_month_start, 1)  # limite superior exclusivo

        period_start_dt = _day_start(start_month)
        period_end_dt = _day_start(period_end_month)

        # 1) Totais por categoria (últimos 12 meses), realizados (status=paid).
        # Reutilize a mesma expressão também no GROUP BY. Criar duas
        # expressões coalesce equivalentes faz o dialeto asyncpg emitir dois
        # bind parameters ($1 e $6); o PostgreSQL então não considera as
        # expressões sintaticamente idênticas e rejeita a consulta com
        # GroupingError. SQLite aceita a forma antiga e, por isso, não expunha
        # a divergência nos testes unitários.
        category_expression = func.coalesce(FinanceEntry.category, "sem_categoria")
        rows = await self.db.execute(
            select(
                FinanceEntry.entry_type,
                category_expression.label("categoria"),
                func.sum(FinanceEntry.amount).label("valor"),
            )
            .where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.entry_type.in_(
                    (FinanceEntryType.receivable, FinanceEntryType.expense)
                ),
                FinanceEntry.status == FinanceEntryStatus.paid,
                FinanceEntry.paid_at >= period_start_dt,
                FinanceEntry.paid_at < period_end_dt,
            )
            .group_by(
                FinanceEntry.entry_type,
                category_expression,
            )
        )
        receitas_por_categoria: list[DreCategoriaValor] = []
        despesas_por_categoria: list[DreCategoriaValor] = []
        receita_total = _ZERO
        despesa_total = _ZERO
        for entry_type, categoria, valor in rows.all():
            valor_dec = _q2(_dec(valor))
            if entry_type == FinanceEntryType.receivable:
                receitas_por_categoria.append(
                    DreCategoriaValor(categoria=categoria, valor=valor_dec)
                )
                receita_total += valor_dec
            else:
                despesas_por_categoria.append(
                    DreCategoriaValor(categoria=categoria, valor=valor_dec)
                )
                despesa_total += valor_dec

        # 2) Detalhe mensal — uma única query com SUM(CASE) por mês (limites
        # de data calculados em Python, comparação portável entre dialetos).
        month_starts = [_add_months(start_month, i) for i in range(13)]
        month_starts_dt = [_day_start(m) for m in month_starts]

        receita_cols = []
        despesa_cols = []
        for i in range(12):
            lo, hi = month_starts_dt[i], month_starts_dt[i + 1]
            receita_cols.append(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    FinanceEntry.entry_type == FinanceEntryType.receivable,
                                    FinanceEntry.paid_at >= lo,
                                    FinanceEntry.paid_at < hi,
                                ),
                                FinanceEntry.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label(f"receita_{i}")
            )
            despesa_cols.append(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    FinanceEntry.entry_type == FinanceEntryType.expense,
                                    FinanceEntry.paid_at >= lo,
                                    FinanceEntry.paid_at < hi,
                                ),
                                FinanceEntry.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label(f"despesa_{i}")
            )

        monthly_row = await self.db.execute(
            select(*receita_cols, *despesa_cols).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.status == FinanceEntryStatus.paid,
                FinanceEntry.paid_at >= month_starts_dt[0],
                FinanceEntry.paid_at < month_starts_dt[12],
            )
        )
        values = monthly_row.one()
        receitas_mes = values[:12]
        despesas_mes = values[12:]

        mensal: list[DreMensal] = []
        for i in range(12):
            receita_i = _q2(_dec(receitas_mes[i]))
            despesa_i = _q2(_dec(despesas_mes[i]))
            mensal.append(
                DreMensal(
                    competencia=month_starts[i].strftime("%Y-%m"),
                    receitas=receita_i,
                    despesas=despesa_i,
                    resultado=_q2(receita_i - despesa_i),
                )
            )

        budget_variance = None
        if compare_budget:
            from app.services.finance_dimensional_service import FinanceDimensionalService

            budget_lines = await FinanceDimensionalService(self.db).list_budget_lines(
                workspace_id,
                period_start=start_month,
                period_end=period_end_month - timedelta(days=1),
            )
            if budget_lines:
                budget_revenue = sum(
                    (
                        _dec(item.amount)
                        if item.entry_type == "receivable"
                        else -_dec(item.amount)
                        if item.entry_type == "refund"
                        else _ZERO
                    )
                    for item in budget_lines
                )
                budget_expenses = sum(
                    (_dec(item.amount) for item in budget_lines if item.entry_type == "expense"),
                    _ZERO,
                )
                budget_variance = _q2(
                    (receita_total - despesa_total) - (budget_revenue - budget_expenses)
                )

        return FinanceDre(
            competencia=ref_month_start.strftime("%Y-%m"),
            periodo_inicio=start_month,
            periodo_fim=period_end_month - timedelta(days=1),
            receitas_por_categoria=receitas_por_categoria,
            despesas_por_categoria=despesas_por_categoria,
            receita_total=_q2(receita_total),
            despesa_total=_q2(despesa_total),
            resultado=_q2(receita_total - despesa_total),
            mensal=mensal,
            basis="cash",
            budget_variance=budget_variance,
        )

    # ── Fluxo de caixa projetado ─────────────────────────────────────────

    async def cash_flow_projection(
        self,
        workspace_id: UUID,
        *,
        dias: int = 90,
        as_of: date | None = None,
    ) -> CashFlowProjection:
        as_of = as_of or datetime.now(UTC).date()
        horizon = as_of + timedelta(days=dias)

        entradas_por_dia: dict[date, Decimal] = defaultdict(lambda: _ZERO)
        saidas_por_dia: dict[date, Decimal] = defaultdict(lambda: _ZERO)

        await self._project_entradas(workspace_id, as_of, horizon, entradas_por_dia)
        await self._project_recorrencias(workspace_id, as_of, horizon, entradas_por_dia)
        await self._project_saidas(workspace_id, as_of, horizon, saidas_por_dia)

        dias_serie: list[CashFlowDay] = []
        acumulado = _ZERO
        cursor = as_of
        while cursor <= horizon:
            entradas = _q2(entradas_por_dia.get(cursor, _ZERO))
            saidas = _q2(saidas_por_dia.get(cursor, _ZERO))
            saldo_dia = _q2(entradas - saidas)
            acumulado = _q2(acumulado + saldo_dia)
            dias_serie.append(
                CashFlowDay(
                    data=cursor,
                    entradas=entradas,
                    saidas=saidas,
                    saldo_dia=saldo_dia,
                    saldo_acumulado=acumulado,
                )
            )
            cursor += timedelta(days=1)

        return CashFlowProjection(as_of=as_of, horizonte_dias=dias, dias=dias_serie)

    async def _project_entradas(
        self,
        workspace_id: UUID,
        as_of: date,
        horizon: date,
        bucket: dict[date, Decimal],
    ) -> None:
        """Entradas projetadas por vencimento das charges/entries abertas.

        Charges com `billing_type=CREDIT_CARD` liquidam em D+32 a partir de
        `paid_at` (quando já confirmada) ou, na falta desse dado, do próprio
        vencimento — plano §3.3. Uma única query (LEFT JOIN 1:1 com
        `FinanceCharge`), sem N+1.
        """
        rows = await self.db.execute(
            select(
                FinanceEntry.due_date,
                FinanceEntry.amount,
                FinanceCharge.billing_type,
                FinanceCharge.due_date,
                FinanceCharge.paid_at,
            )
            .outerjoin(FinanceCharge, FinanceCharge.entry_id == FinanceEntry.id)
            .where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.entry_type == FinanceEntryType.receivable,
                FinanceEntry.status.in_(_OPEN_ENTRY_STATUSES),
                FinanceEntry.deleted_at.is_(None),
            )
        )
        for entry_due_date, amount, billing_type, charge_due_date, charge_paid_at in rows.all():
            reference = charge_due_date or entry_due_date
            if billing_type == BillingType.CREDIT_CARD.value:
                base_ref = charge_paid_at or reference
                if base_ref is None:
                    continue
                projected_date = base_ref.date() + timedelta(days=CREDIT_CARD_SETTLEMENT_DAYS)
            else:
                if reference is None:
                    continue
                projected_date = reference.date()
            if as_of <= projected_date <= horizon:
                bucket[projected_date] += _dec(amount)

    async def _project_recorrencias(
        self,
        workspace_id: UUID,
        as_of: date,
        horizon: date,
        bucket: dict[date, Decimal],
    ) -> None:
        """Expande recorrências ativas (receivable) até o horizonte. Aritmética
        de datas em memória sobre o conjunto (pequeno) de recorrências ativas
        do workspace — não é uma query por ocorrência."""
        result = await self.db.execute(
            select(FinanceRecurrence).where(
                FinanceRecurrence.workspace_id == workspace_id,
                FinanceRecurrence.status == FinanceRecurrenceStatus.ativa,
                FinanceRecurrence.entry_type == FinanceEntryType.receivable,
                FinanceRecurrence.next_run <= horizon,
            )
        )
        for recurrence in result.scalars().all():
            occurrence = recurrence.next_run
            generated = recurrence.generated_count
            guard = 0
            while occurrence <= horizon and guard < 500:
                guard += 1
                if recurrence.end_date is not None and occurrence > recurrence.end_date:
                    break
                if recurrence.max_count is not None and generated >= recurrence.max_count:
                    break
                if occurrence >= as_of:
                    bucket[occurrence] += _dec(recurrence.entry_value)
                generated += 1
                occurrence = _advance_date(occurrence, recurrence.cycle)

    async def _project_saidas(
        self,
        workspace_id: UUID,
        as_of: date,
        horizon: date,
        bucket: dict[date, Decimal],
    ) -> None:
        rows = await self.db.execute(
            select(FinanceEntry.due_date, FinanceEntry.amount).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.entry_type == FinanceEntryType.expense,
                FinanceEntry.status.in_(_OPEN_ENTRY_STATUSES),
                FinanceEntry.deleted_at.is_(None),
                FinanceEntry.due_date.is_not(None),
            )
        )
        for due_date, amount in rows.all():
            projected_date = due_date.date()
            if as_of <= projected_date <= horizon:
                bucket[projected_date] += _dec(amount)
