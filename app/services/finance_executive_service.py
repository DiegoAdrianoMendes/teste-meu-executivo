"""Executive financial metrics with explicit sources and quality."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select

from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_dimensional import FinanceAllocation
from app.schemas.finance_executive import ExecutiveAnalyticsRead, ExecutiveMetric
from app.services.finance_reports_service import FinanceReportsService


class FinanceExecutiveService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _metric(value, *, quality: str, source: str, definition: str, unit: str = "BRL"):
        return ExecutiveMetric(
            value=value,
            quality=quality,
            source=source,
            definition=definition,
            unit=unit,
        )

    @classmethod
    def _missing(cls, definition: str, source: str, *, unit: str = "BRL"):
        return cls._metric(
            None,
            quality="insufficient_data",
            source=source,
            definition=definition,
            unit=unit,
        )

    async def analytics(self, workspace_id: UUID, *, as_of: date | None = None):
        as_of = as_of or datetime.now(UTC).date()
        start = date(as_of.year, as_of.month, 1)
        kpis = await FinanceReportsService(self.db).kpis(
            workspace_id, start_date=start, end_date=as_of, as_of=as_of
        )
        arr = kpis.mrr * Decimal("12")
        contracted = kpis.recebido + kpis.a_receber + kpis.vencido.total
        margin_amount = kpis.recebido - kpis.despesas
        gross_margin = (
            (margin_amount / kpis.recebido * Decimal("100")) if kpis.recebido > 0 else None
        )
        burn = abs(margin_amount) if margin_amount < 0 else Decimal("0")

        paid_balance = await self.db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                FinanceEntry.entry_type == FinanceEntryType.receivable,
                                FinanceEntry.amount,
                            ),
                            else_=-FinanceEntry.amount,
                        )
                    ),
                    0,
                )
            ).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.status == FinanceEntryStatus.paid,
            )
        )
        paid_balance = Decimal(str(paid_balance or 0))
        runway = (paid_balance / burn) if burn > 0 and paid_balance > 0 else None

        allocation_rows = (
            await self.db.execute(
                select(
                    FinanceAllocation.client_id,
                    FinanceEntry.entry_type,
                    func.coalesce(func.sum(FinanceAllocation.amount), 0),
                )
                .join(FinanceEntry, FinanceEntry.id == FinanceAllocation.finance_entry_id)
                .where(FinanceAllocation.workspace_id == workspace_id)
                .group_by(FinanceAllocation.client_id, FinanceEntry.entry_type)
            )
        ).all()
        client_revenue: dict[UUID, Decimal] = {}
        allocated_revenue = Decimal("0")
        allocated_expense = Decimal("0")
        variable_expense = Decimal("0")
        classified_expense = 0
        total_expense = 0
        for client_id, entry_type, amount in allocation_rows:
            amount = Decimal(str(amount or 0))
            if entry_type == FinanceEntryType.receivable:
                allocated_revenue += amount
                if client_id is not None:
                    client_revenue[client_id] = client_revenue.get(client_id, Decimal("0")) + amount
            elif entry_type == FinanceEntryType.expense:
                allocated_expense += amount
        concentration = (
            max(client_revenue.values()) / sum(client_revenue.values()) * Decimal("100")
            if client_revenue and sum(client_revenue.values()) > 0
            else None
        )
        dimensional_profitability = (
            allocated_revenue - allocated_expense if allocation_rows else None
        )

        # Contribution margin is only emitted when expense rows carry an
        # explicit variable/fixed classification.  This avoids presenting a
        # gross-margin proxy as contribution margin.  The classification may
        # come from entry_metadata.cost_behavior or a conservative category
        # vocabulary used by the finance UI.
        expense_rows = (
            await self.db.execute(
                select(FinanceEntry.amount, FinanceEntry.category, FinanceEntry.entry_metadata)
                .where(
                    FinanceEntry.workspace_id == workspace_id,
                    FinanceEntry.entry_type == FinanceEntryType.expense,
                    FinanceEntry.status == FinanceEntryStatus.paid,
                )
            )
        ).all()
        variable_categories = {"custas", "diligencia", "pericia", "honorario_repassado"}
        for amount, category, metadata in expense_rows:
            total_expense += 1
            metadata = metadata if isinstance(metadata, dict) else {}
            behavior = str(metadata.get("cost_behavior", "")).lower()
            if behavior in {"variable", "fixed"}:
                classified_expense += 1
                if behavior == "variable":
                    variable_expense += Decimal(str(amount or 0))
            elif str(category or "").lower() in variable_categories:
                classified_expense += 1
                variable_expense += Decimal(str(amount or 0))
        contribution_margin = None
        contribution_quality = "insufficient_data"
        contribution_source = "FinanceEntry classificação de custo"
        if kpis.recebido > 0 and total_expense > 0 and classified_expense == total_expense:
            contribution_margin = (
                (kpis.recebido - variable_expense) / kpis.recebido * Decimal("100")
            )
            contribution_quality = "derived"
            contribution_source = "FinanceEntry pagos com cost_behavior/category"

        return ExecutiveAnalyticsRead(
            generated_at=datetime.now(UTC),
            period_start=start,
            period_end=as_of,
            arr=self._metric(
                arr,
                quality="derived",
                source="FinanceReportsService.kpis.mrr",
                definition="MRR ativo normalizado multiplicado por 12.",
            ),
            contracted_revenue=self._metric(
                contracted,
                quality="proxy",
                source="FinanceEntry recebido + aberto + vencido",
                definition=(
                    "Receita faturada/contratada observável no período; "
                    "não inclui contratos sem lançamento."
                ),
            ),
            gross_margin=(
                self._metric(
                    gross_margin,
                    quality="proxy",
                    source="FinanceEntry pagos",
                    definition="Recebido menos despesas pagas, dividido pelo recebido no período.",
                    unit="percent",
                )
                if gross_margin is not None
                else self._missing(
                    "Margem depende de receita recebida no período.",
                    "FinanceEntry pagos",
                    unit="percent",
                )
            ),
            contribution_margin=(
                self._metric(
                    contribution_margin,
                    quality=contribution_quality,
                    source=contribution_source,
                    definition=(
                        "Receita recebida menos custos variáveis classificados, "
                        "dividida pela receita recebida no período."
                    ),
                    unit="percent",
                )
                if contribution_margin is not None
                else self._missing(
                    "Margem de contribuição exige classificação explícita "
                    "de todos os custos pagos.",
                    contribution_source,
                    unit="percent",
                )
            ),
            ebitda=self._metric(
                margin_amount,
                quality="proxy",
                source="FinanceEntry pagos",
                definition=(
                    "Proxy gerencial antes de classificação validada de juros, "
                    "impostos, depreciação e amortização."
                ),
            ),
            burn=self._metric(
                burn,
                quality="derived",
                source="Resultado caixa mensal",
                definition=(
                    "Déficit mensal observado; zero somente quando a consulta válida "
                    "não identifica déficit."
                ),
            ),
            runway=(
                self._metric(
                    runway,
                    quality="proxy",
                    source="Saldo líquido histórico / burn mensal",
                    definition=(
                        "Meses estimados de fôlego; não substitui saldo bancário "
                        "conciliado."
                    ),
                    unit="months",
                )
                if runway is not None
                else self._missing(
                    "Runway exige saldo positivo e burn mensal maior que zero.",
                    "FinanceEntry pagos",
                    unit="months",
                )
            ),
            break_even=self._metric(
                kpis.despesas,
                quality="proxy",
                source="Despesas pagas no mês",
                definition="Receita mensal necessária para cobrir as despesas pagas observadas.",
            ),
            concentration=(
                self._metric(
                    concentration,
                    quality="partial",
                    source="FinanceAllocation por cliente",
                    definition="Participação do maior cliente na receita alocada.",
                    unit="percent",
                )
                if concentration is not None
                else self._missing(
                    "Concentração exige receitas alocadas a clientes.",
                    "FinanceAllocation",
                    unit="percent",
                )
            ),
            dimensional_profitability=(
                self._metric(
                    dimensional_profitability,
                    quality="partial",
                    source="FinanceAllocation",
                    definition="Receitas menos despesas alocadas no recorte dimensional.",
                )
                if dimensional_profitability is not None
                else self._missing(
                    "Rentabilidade dimensional exige alocações.", "FinanceAllocation"
                )
            ),
            cac=self._missing(
                "CAC exige custo de aquisição e novos clientes identificados no período.",
                "Fonte de aquisição não configurada",
            ),
            ltv=self._missing(
                "LTV exige coortes de clientes, retenção e receita recorrente confiáveis.",
                "Coortes de clientes não configuradas",
            ),
            payback=self._missing(
                "Payback exige CAC e margem de contribuição válidos.",
                "CAC/LTV insuficientes",
                unit="months",
            ),
        )
