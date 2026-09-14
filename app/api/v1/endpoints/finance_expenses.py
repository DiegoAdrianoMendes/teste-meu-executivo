"""
Endpoints: Despesas completas (F8) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §C.3/F8.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.schemas.finance_expense import (
    FinanceExpenseApprove,
    FinanceExpenseCategorySeed,
    FinanceExpenseCreate,
    FinanceExpenseRead,
)
from app.services.finance_expense_service import FinanceExpenseService

router = APIRouter(prefix="/finance/expenses", tags=["Financeiro — Despesas"])


@router.get(
    "/categories",
    response_model=FinanceExpenseCategorySeed,
    summary="Categorias padrão sugeridas para despesas jurídicas",
)
async def list_expense_categories(
    _: AuthContext = Depends(require_finance_access),
):
    return FinanceExpenseCategorySeed(categories=FinanceExpenseService.default_categories())


@router.post(
    "",
    response_model=FinanceExpenseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar despesa (avulsa ou recorrente)",
)
async def create_expense(
    payload: FinanceExpenseCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    entry = await FinanceExpenseService(db).create_expense(workspace_id, payload)
    await db.commit()
    return FinanceExpenseRead.model_validate(entry)


@router.get(
    "",
    response_model=List[FinanceExpenseRead],
    summary="Listar despesas por categoria/centro de custo",
)
async def list_expenses(
    category: Optional[str] = Query(None),
    cost_center: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=300),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    entries = await FinanceExpenseService(db).list_expenses(
        workspace_id, category=category, cost_center=cost_center, limit=limit
    )
    return [FinanceExpenseRead.model_validate(entry) for entry in entries]


@router.post(
    "/{entry_id}/approve",
    response_model=FinanceExpenseRead,
    summary="Aprovar ou rejeitar despesa acima do limite do workspace",
)
async def approve_expense(
    entry_id: UUID,
    payload: FinanceExpenseApprove,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    entry = await FinanceExpenseService(db).approve_expense(
        workspace_id, entry_id, context.user_id, payload
    )
    await db.commit()
    return FinanceExpenseRead.model_validate(entry)


@router.post(
    "/{entry_id}/reimbursement",
    response_model=FinanceExpenseRead,
    summary="Gerar (ou retornar) a cobrança-espelho de reembolso — idempotente",
)
async def generate_reimbursement(
    entry_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    mirror = await FinanceExpenseService(db).generate_reimbursement_charge(workspace_id, entry_id)
    await db.commit()
    return FinanceExpenseRead.model_validate(mirror)
