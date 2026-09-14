"""
Schemas do serviço de cobrança (F2) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F2) e 4.2.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import Field, field_validator

from app.models.finance_charge import FinanceChargeGateway, FinanceChargeKind, FinanceChargeStatus
from app.payments.port import BillingType
from app.schemas.base import CamelCaseSchema, WriteSchema


class FinanceChargeDiscountWrite(WriteSchema):
    value: Optional[Decimal] = None
    due_date_limit_days: Optional[int] = None
    type: Optional[str] = None


class FinanceChargeFineWrite(WriteSchema):
    value: Optional[Decimal] = None
    type: Optional[str] = None


class FinanceChargeInterestWrite(WriteSchema):
    value: Optional[Decimal] = None


class FinanceChargeCreate(WriteSchema):
    """Payload para criar a cobrança de uma FinanceEntry.

    `customer_external_id` é tolerante: se ausente, o service tenta lê-lo de
    `entry.entry_metadata["gatewayCustomerId"]` (sincronização Cliente↔customer
    do gateway ainda não implementada nesta onda). Sem ele, só é possível
    criar cobrança em modo rascunho (gate externo fechado).
    """

    billing_type: BillingType = BillingType.UNDEFINED
    customer_external_id: Optional[str] = None
    due_date: Optional[date] = None
    description: Optional[str] = None
    discount: Optional[FinanceChargeDiscountWrite] = None
    fine: Optional[FinanceChargeFineWrite] = None
    interest: Optional[FinanceChargeInterestWrite] = None

    @field_validator("billing_type", mode="before")
    @classmethod
    def _normalize_billing_type(cls, value):
        if value is None:
            return BillingType.UNDEFINED
        return value


class FinanceChargeRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    entry_id: UUID
    gateway: FinanceChargeGateway
    external_id: Optional[str] = None
    billing_type: Optional[str] = None
    charge_kind: FinanceChargeKind
    status: FinanceChargeStatus
    value: Decimal
    net_value: Optional[Decimal] = None
    fee_value: Optional[Decimal] = None
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    invoice_url: Optional[str] = None
    bank_slip_url: Optional[str] = None
    pix_payload: Optional[str] = None
    external_reference: Optional[str] = None
    installment_ref: Optional[str] = None
    subscription_ref: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    draft_notice: Optional[str] = Field(default=None)


class FinanceChargePixQrRead(CamelCaseSchema):
    encoded_image: Optional[str] = None
    payload: Optional[str] = None
    expiration_date: Optional[str] = None
