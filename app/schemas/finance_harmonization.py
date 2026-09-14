"""
Schemas: Harmonização de Documentos Financeiros × Lançamentos Manuais (F14).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import Field

from app.models.finance_harmonization import (
    FinanceHarmonizationDecision,
    FinanceHarmonizationMatchedSource,
    FinanceHarmonizationMatchKind,
    FinanceHarmonizationSourceType,
    FinanceHarmonizationStatus,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


class FinanceHarmonizationItemRead(CamelCaseSchema):
    id: UUID
    run_id: UUID
    doc_date: Optional[date] = None
    doc_value: Optional[Decimal] = None
    doc_description: Optional[str] = None
    matched_source: Optional[FinanceHarmonizationMatchedSource] = None
    matched_entry_id: Optional[UUID] = None
    match_kind: FinanceHarmonizationMatchKind
    delta_value: Optional[Decimal] = None
    delta_days: Optional[int] = None
    decision: FinanceHarmonizationDecision
    decided_by: Optional[UUID] = None
    decided_at: Optional[datetime] = None
    created_at: datetime


class FinanceHarmonizationRunRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    source_document_name: str
    source_type: FinanceHarmonizationSourceType
    period_start: date
    period_end: date
    competence: str
    status: FinanceHarmonizationStatus
    totals: dict
    parser_warning: Optional[str] = None
    created_by: Optional[UUID] = None
    decided_by: Optional[UUID] = None
    decided_at: Optional[datetime] = None
    created_at: datetime


class FinanceHarmonizationRunDetailRead(FinanceHarmonizationRunRead):
    items: List[FinanceHarmonizationItemRead] = []


class FinanceHarmonizationQuotaRead(CamelCaseSchema):
    competence: str
    used: int
    limit: int
    remaining: int
    renews_at: date


class FinanceHarmonizationItemDecisionWrite(WriteSchema):
    decision: FinanceHarmonizationDecision


class FinanceHarmonizationBonusGrantWrite(WriteSchema):
    competence: str
    extra: int
    reason: str = Field(min_length=8, max_length=500)
