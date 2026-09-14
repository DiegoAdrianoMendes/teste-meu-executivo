"""
Schemas: LegalDemand (Demanda jurídica canônica) — Onda 2, Fundação jurídica.
Ver app/models/legal_demand.py e app/services/legal_demand_service.py.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.legal_demand import LegalDemandNature, LegalDemandPhase, LegalDemandRisk
from app.schemas.base import CamelCaseSchema, WriteSchema


class LegalDemandParty(CamelCaseSchema):
    """Parte envolvida na demanda além do cliente titular."""

    name: str
    role: Optional[str] = None
    doc_number: Optional[str] = None


class LegalDemandCreate(WriteSchema):
    # Chave de idempotência opcional — uma segunda criação com o mesmo
    # external_ref no mesmo workspace devolve a demanda já existente em vez
    # de duplicar (ver LegalDemandService.create).
    external_ref: Optional[str] = None
    nature: LegalDemandNature
    area: Optional[str] = None
    subarea: Optional[str] = None
    client_id: UUID
    parties: list[LegalDemandParty] = Field(default_factory=list)
    unit: Optional[str] = None
    team: Optional[str] = None
    responsible_user_id: Optional[UUID] = None
    strategy: Optional[str] = None
    risk: LegalDemandRisk = LegalDemandRisk.medio
    value: Optional[Decimal] = None
    sla: dict = Field(default_factory=dict)
    playbook_ref: Optional[str] = None
    documents_required: list[str] = Field(default_factory=list)
    # Não duplica: referencia o que já existe.
    matter_id: Optional[UUID] = None
    contract_ref: Optional[UUID] = None
    finance_ref: Optional[UUID] = None


class LegalDemandUpdate(WriteSchema):
    """Atualização de campos mutáveis. `nature` é imutável (define os campos
    obrigatórios na criação) e `phase` só muda via /transition."""

    area: Optional[str] = None
    subarea: Optional[str] = None
    parties: Optional[list[LegalDemandParty]] = None
    unit: Optional[str] = None
    team: Optional[str] = None
    responsible_user_id: Optional[UUID] = None
    strategy: Optional[str] = None
    risk: Optional[LegalDemandRisk] = None
    value: Optional[Decimal] = None
    sla: Optional[dict] = None
    playbook_ref: Optional[str] = None
    documents_required: Optional[list[str]] = None
    matter_id: Optional[UUID] = None
    contract_ref: Optional[UUID] = None
    finance_ref: Optional[UUID] = None


class LegalDemandPhaseTransitionRequest(WriteSchema):
    phase: LegalDemandPhase
    reason: Optional[str] = None


class LegalDemandRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    external_ref: Optional[str] = None
    nature: LegalDemandNature
    area: Optional[str] = None
    subarea: Optional[str] = None
    client_id: UUID
    parties: list[LegalDemandParty]
    unit: Optional[str] = None
    team: Optional[str] = None
    responsible_user_id: Optional[UUID] = None
    strategy: Optional[str] = None
    risk: LegalDemandRisk
    value: Optional[Decimal] = None
    phase: LegalDemandPhase
    sla: dict
    playbook_ref: Optional[str] = None
    documents_required: list[str]
    matter_id: Optional[UUID] = None
    contract_ref: Optional[UUID] = None
    finance_ref: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class LegalDemandListRead(CamelCaseSchema):
    items: list[LegalDemandRead]
    total: int
