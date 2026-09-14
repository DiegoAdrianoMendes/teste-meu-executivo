"""
Schemas: Despesas completas (F8) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §C.3/F8.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field

from app.models.finance import FinanceExpenseApprovalStatus
from app.models.finance_recurrence import FinanceRecurrenceCycle
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.finance import FinanceEntryRead


class FinanceExpenseRecurrenceWrite(WriteSchema):
    """Molde de recorrência reutilizando `finance_recurrences` (F3)."""

    client_id: UUID
    cycle: FinanceRecurrenceCycle = FinanceRecurrenceCycle.mensal
    next_run: date
    end_date: Optional[date] = None
    max_count: Optional[int] = None


class FinanceExpenseCreate(WriteSchema):
    matter_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    amount: float
    currency: str = "BRL"
    due_date: Optional[datetime] = None
    category: Optional[str] = None
    cost_center: Optional[str] = None
    reimbursable: bool = False
    receipt_document_ref: Optional[UUID] = None
    tags: List[str] = Field(default_factory=list)
    entry_metadata: dict = Field(default_factory=dict)
    recurrence: Optional[FinanceExpenseRecurrenceWrite] = None


class FinanceExpenseApprove(WriteSchema):
    decision: FinanceExpenseApprovalStatus = FinanceExpenseApprovalStatus.approved
    reason: Optional[str] = None


class FinanceExpenseRead(FinanceEntryRead):
    pass


class FinanceExpenseCategorySeed(CamelCaseSchema):
    categories: List[str]
