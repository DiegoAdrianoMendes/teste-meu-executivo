"""
Endpoints: KPIs, DRE e fluxo de caixa projetado (F10) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F10.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.schemas.finance_reports import (
    FinanceCashFlowProjectionRead,
    FinanceDreRead,
    FinanceKpisRead,
)
from app.services.finance_reports_service import FinanceReportsService

router = APIRouter(prefix="/finance/reports", tags=["Financeiro — Relatórios"])


@router.get(
    "/kpis",
    response_model=FinanceKpisRead,
    summary="KPIs financeiros do período (recebido, a receber, aging, MRR, inadimplência)",
)
async def get_finance_kpis(
    start_date: date | None = Query(None, alias="startDate"),
    end_date: date | None = Query(None, alias="endDate"),
    as_of: date | None = Query(None, alias="asOf"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    today = datetime.now(UTC).date()
    resolved_as_of = as_of or today
    resolved_start = start_date or date(resolved_as_of.year, resolved_as_of.month, 1)
    resolved_end = end_date or resolved_as_of
    kpis = await FinanceReportsService(db).kpis(
        workspace_id, start_date=resolved_start, end_date=resolved_end, as_of=resolved_as_of
    )
    return FinanceKpisRead.model_validate(kpis)


@router.get(
    "/dre",
    response_model=FinanceDreRead,
    summary="DRE gerencial por caixa ou competência, com detalhe mensal",
)
async def get_finance_dre(
    competencia: date | None = Query(None),
    basis: str = Query("cash", pattern="^(cash|accrual)$"),
    cost_center_id: UUID | None = Query(None),
    client_id: UUID | None = Query(None),
    contract_id: UUID | None = Query(None),
    matter_id: UUID | None = Query(None),
    compare_budget: bool = Query(False),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    dre = await FinanceReportsService(db).dre(
        workspace_id,
        competencia=competencia,
        basis=basis,
        cost_center_id=cost_center_id,
        client_id=client_id,
        contract_id=contract_id,
        matter_id=matter_id,
        compare_budget=compare_budget,
    )
    return FinanceDreRead.model_validate(dre)


@router.get(
    "/cash-flow",
    response_model=FinanceCashFlowProjectionRead,
    summary="Fluxo de caixa projetado (entradas D+32 cartão + recorrências + saídas)",
)
async def get_finance_cash_flow(
    dias: int = Query(90, ge=1, le=365),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    projection = await FinanceReportsService(db).cash_flow_projection(workspace_id, dias=dias)
    return FinanceCashFlowProjectionRead.model_validate(projection)
