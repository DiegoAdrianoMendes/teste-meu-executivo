"""
Schemas: Matriz Estratégia Fato–Prova–Pedido (Onda 5 — Caso 360).
Ver app/models/case_strategy.py e app/services/case_strategy_service.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.case_strategy import ProofForce, ProofStatus
from app.schemas.base import CamelCaseSchema, WriteSchema


class CaseFactCreate(WriteSchema):
    description: str
    occurred_at: Optional[datetime] = None
    is_controverted: bool = False
    origin: Optional[str] = None


class CaseFactRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    legal_demand_id: UUID
    description: str
    occurred_at: Optional[datetime] = None
    is_controverted: bool
    origin: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CaseProofLinkCreate(WriteSchema):
    # Prova já anexada (Document existente) — opcional: quando ausente, o
    # vínculo nasce declarativo ("falta"/"solicitada") e é resolvido depois
    # por uma nova chamada com document_id preenchido.
    document_id: Optional[UUID] = None
    force: ProofForce
    # Quando omitido, o service deriva: document_id presente => 'tem',
    # ausente => 'falta'. Nunca fabrica 'tem' sem document_id.
    status: Optional[ProofStatus] = None
    notes: Optional[str] = None


class CaseProofLinkRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    legal_demand_id: UUID
    fact_id: UUID
    document_id: Optional[UUID] = None
    force: ProofForce
    status: ProofStatus
    notes: Optional[str] = None
    requested_at: Optional[datetime] = None
    obtained_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CasePleadingCreate(WriteSchema):
    request_text: str
    legal_basis: Optional[str] = None
    supporting_fact_ids: list[UUID] = Field(default_factory=list)


class CasePleadingRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    legal_demand_id: UUID
    request_text: str
    legal_basis: Optional[str] = None
    supporting_fact_ids: list[UUID]
    created_at: datetime
    updated_at: datetime


class CaseStrategyGap(CamelCaseSchema):
    """Uma lacuna detectada na matriz — nunca fabrica prova/fato, só aponta
    a ausência para decisão humana (ver CaseStrategyService._compute_gaps)."""

    kind: str  # "fato_sem_prova" | "pedido_sem_fato"
    fact_id: Optional[UUID] = None
    pleading_id: Optional[UUID] = None
    description: str
    exception_id: Optional[UUID] = None


class CaseStrategyMatrixRead(CamelCaseSchema):
    legal_demand_id: UUID
    facts: list[CaseFactRead]
    proof_links: list[CaseProofLinkRead]
    pleadings: list[CasePleadingRead]
    gaps: list[CaseStrategyGap]
