"""
Schemas: Contrato → Nós Financeiros com Aprovação (F11) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção F11.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import Field

from app.models.contract_billing import ContractBillingProposalStatus
from app.schemas.base import CamelCaseSchema, WriteSchema


class ContractBillingItemKind(str, Enum):
    fixo = "fixo"
    entrada = "entrada"
    parcela = "parcela"
    mensalidade = "mensalidade"
    exito = "exito"
    por_hora = "por_hora"


class ContractBillingItem(CamelCaseSchema):
    """Item de proposta de cobrança — sempre PROPOSTA até aprovação."""

    kind: ContractBillingItemKind
    valor: Decimal | None = None
    percentual: Decimal | None = Field(default=None, ge=0, le=100)
    vencimento: date | None = None
    ciclo: str | None = None
    descricao: str | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    needs_review: bool = False


class ExtractBillingPlanRequest(WriteSchema):
    matter_id: UUID
    contract_text: str = Field(..., min_length=1)
    source_document_name: str | None = None
    requested_by: UUID | None = None


class SubmitProposalRequest(WriteSchema):
    requested_by: UUID | None = None


class ContractBillingProposalUpdate(WriteSchema):
    source_document_name: str | None = None
    items: list[ContractBillingItem] | None = None
    notes: str | None = None
    expected_updated_at: datetime | None = None


class RejectProposalRequest(WriteSchema):
    resolution_reason: str | None = None


class ContractBillingProposalRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    source_document_name: str | None = None
    items: list[ContractBillingItem]
    status: ContractBillingProposalStatus
    extraction_model: str | None = None
    approval_id: UUID | None = None
    decided_at: datetime | None = None
    decided_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
