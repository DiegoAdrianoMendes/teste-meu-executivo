"""
Schemas de honorários jurídicos (F9) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F9).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.fee_arrangement import FeeArrangementKind, FeeArrangementStatus
from app.payments.port import BillingType
from app.schemas.base import CamelCaseSchema, WriteSchema


class FeeArrangementCreate(WriteSchema):
    matter_id: UUID
    kind: FeeArrangementKind
    # Nunca gerados por IA — decisão humana registrada no contrato.
    fixed_value: Optional[Decimal] = None
    percent: Optional[Decimal] = Field(default=None, ge=0, le=100)
    base_calculo_exito: Optional[str] = None
    hourly_rate: Optional[Decimal] = None
    notes: Optional[str] = None


class FeeArrangementRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    kind: FeeArrangementKind
    fixed_value: Optional[Decimal] = None
    percent: Optional[Decimal] = None
    base_calculo_exito: Optional[str] = None
    hourly_rate: Optional[Decimal] = None
    status: FeeArrangementStatus
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TimeEntryCreate(WriteSchema):
    user_id: UUID
    date: date
    minutes: int = Field(..., ge=1)
    description: Optional[str] = None


class TimeEntryRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    arrangement_id: UUID
    matter_id: UUID
    user_id: UUID
    date: date
    minutes: int
    description: Optional[str] = None
    hourly_rate: Decimal
    billed: bool
    invoice_entry_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class GenerateChargeRequest(WriteSchema):
    """Payload para gerar a cobrança de arranjos fixo/entrada_exito/mensalidade."""

    installment_count: Optional[int] = Field(default=None, ge=2, le=60)
    first_due_date: Optional[date] = None
    billing_type: BillingType = BillingType.UNDEFINED
    customer_external_id: Optional[str] = None


class CloseMonthlyInvoiceRequest(WriteSchema):
    competence: date


class RegisterSuccessEventRequest(WriteSchema):
    base_value: Decimal = Field(..., gt=0)
    requested_by: Optional[UUID] = None


class RenegotiateOverdueRequest(WriteSchema):
    installment_count: int = Field(..., ge=1, le=60)
    agreed_value: Decimal = Field(..., gt=0)
    first_due_date: Optional[date] = None
    billing_type: BillingType = BillingType.UNDEFINED
    requested_by: Optional[UUID] = None


class FinanceEntrySummaryRead(CamelCaseSchema):
    """Resumo leve de FinanceEntry — evita depender de relacionamentos
    lazy (`reminders`) que exigiriam recarregar o objeto após o flush."""

    id: UUID
    title: str
    amount: Decimal
    due_date: Optional[datetime] = None
    status: str
    external_ref: Optional[str] = None
