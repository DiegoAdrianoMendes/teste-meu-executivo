"""
Schemas: KPIs, DRE e fluxo de caixa projetado (F10) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F10.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.schemas.base import CamelCaseSchema


class FinanceAgingBucketsRead(CamelCaseSchema):
    dias_1_a_15: Decimal
    dias_16_a_30: Decimal
    dias_31_a_60: Decimal
    dias_61_mais: Decimal
    total: Decimal


class FinanceKpisRead(CamelCaseSchema):
    period_start: date
    period_end: date
    as_of: date
    recebido: Decimal
    a_receber: Decimal
    vencido: FinanceAgingBucketsRead
    ticket_medio: Decimal
    inadimplencia_pct: Decimal
    mrr: Decimal
    despesas: Decimal
    resultado: Decimal


class FinanceDreCategoriaRead(CamelCaseSchema):
    categoria: str
    valor: Decimal


class FinanceDreMensalRead(CamelCaseSchema):
    competencia: str
    receitas: Decimal
    despesas: Decimal
    resultado: Decimal


class FinanceDreRead(CamelCaseSchema):
    competencia: str
    periodo_inicio: date
    periodo_fim: date
    receitas_por_categoria: list[FinanceDreCategoriaRead]
    despesas_por_categoria: list[FinanceDreCategoriaRead]
    receita_total: Decimal
    despesa_total: Decimal
    resultado: Decimal
    mensal: list[FinanceDreMensalRead]
    basis: str = "cash"
    budget_variance: Decimal | None = None


class FinanceCashFlowDayRead(CamelCaseSchema):
    data: date
    entradas: Decimal
    saidas: Decimal
    saldo_dia: Decimal
    saldo_acumulado: Decimal


class FinanceCashFlowProjectionRead(CamelCaseSchema):
    as_of: date
    horizonte_dias: int
    dias: list[FinanceCashFlowDayRead]
