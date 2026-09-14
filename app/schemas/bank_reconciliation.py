"""
Schemas: Conciliação Bancária (F7) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F7 / §3.8.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from app.models.bank_reconciliation import (
    BankTransactionSource,
    ReconciliationMethod,
    ReconciliationStatus,
    ReconciliationTargetKind,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


class BankTransactionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    source: BankTransactionSource
    external_fingerprint: str
    date: datetime
    value: Decimal
    description: Optional[str] = None
    created_at: datetime


class ReconciliationMatchRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    bank_transaction_id: UUID
    target_kind: Optional[ReconciliationTargetKind] = None
    target_id: Optional[UUID] = None
    method: ReconciliationMethod
    confidence: Optional[Decimal] = None
    suggested_category: Optional[str] = None
    suggested_tags: List[str] = []
    status: ReconciliationStatus
    confirmed_by: Optional[UUID] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime


class PendingReconciliationRead(CamelCaseSchema):
    transaction: BankTransactionRead
    match: ReconciliationMatchRead


class IngestResultRead(CamelCaseSchema):
    imported: int
    duplicates: int
    matched_deterministic: int
    suggested: int


class ConfirmMatchWrite(WriteSchema):
    target_kind: ReconciliationTargetKind
    target_id: UUID
    category: Optional[str] = None
    tags: List[str] = []


class ConfirmMatchBatchItem(ConfirmMatchWrite):
    match_id: UUID


class ConfirmMatchesBatchWrite(WriteSchema):
    items: List[ConfirmMatchBatchItem]


class RejectMatchWrite(WriteSchema):
    reason: Optional[str] = None


class RejectMatchesBatchWrite(WriteSchema):
    match_ids: List[UUID]
    reason: Optional[str] = None


class PullAsaasStatementWrite(WriteSchema):
    start: date
    finish: date
