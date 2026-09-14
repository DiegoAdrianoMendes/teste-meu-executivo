"""
Schemas de recorrência e parcelamento (F3) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F3) e 4.2.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.finance import FinanceEntryType
from app.models.finance_recurrence import (
    FinanceRecurrenceCycle,
    FinanceRecurrenceKind,
    FinanceRecurrenceStatus,
)
from app.payments.port import BillingType
from app.schemas.base import CamelCaseSchema, WriteSchema


class FinanceRecurrenceCreate(WriteSchema):
    client_id: UUID
    matter_id: Optional[UUID] = None
    entry_title: str = Field(..., max_length=200)
    entry_description: Optional[str] = None
    entry_value: Decimal
    entry_type: FinanceEntryType = FinanceEntryType.receivable
    kind: FinanceRecurrenceKind = FinanceRecurrenceKind.geracao_programada
    cycle: FinanceRecurrenceCycle = FinanceRecurrenceCycle.mensal
    billing_type: BillingType = BillingType.UNDEFINED
    next_run: date
    end_date: Optional[date] = None
    max_count: Optional[int] = Field(default=None, ge=1)
    customer_external_id: Optional[str] = None


class FinanceRecurrenceUpdate(WriteSchema):
    entry_title: Optional[str] = Field(default=None, max_length=200)
    entry_description: Optional[str] = None
    entry_value: Optional[Decimal] = None
    end_date: Optional[date] = None
    max_count: Optional[int] = Field(default=None, ge=1)


class FinanceRecurrenceRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    client_id: UUID
    matter_id: Optional[UUID] = None
    entry_title: str
    entry_description: Optional[str] = None
    entry_value: Decimal
    entry_type: FinanceEntryType
    kind: FinanceRecurrenceKind
    cycle: FinanceRecurrenceCycle
    billing_type: Optional[str] = None
    next_run: date
    end_date: Optional[date] = None
    max_count: Optional[int] = None
    generated_count: int
    status: FinanceRecurrenceStatus
    gateway_subscription_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FinanceInstallmentSeriesCreate(WriteSchema):
    """Payload para `POST /finance/entries/{entry_id}/installments`."""

    installment_count: int = Field(..., ge=2, le=60)
    total_value: Optional[Decimal] = None
    first_due_date: Optional[date] = None
    billing_type: BillingType = BillingType.UNDEFINED
    customer_external_id: Optional[str] = None


class FinanceInstallmentEntryRead(CamelCaseSchema):
    id: UUID
    installment_index: int
    installment_count: int
    amount: Decimal
    due_date: Optional[datetime] = None
    charge_id: Optional[UUID] = None
    charge_status: Optional[str] = None


class FinanceInstallmentSeriesRead(CamelCaseSchema):
    installment_ref: str
    parent_entry_id: UUID
    entries: list[FinanceInstallmentEntryRead]
