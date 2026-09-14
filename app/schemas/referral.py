"""
Schemas: Indicações com comissão (F13) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F13.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from app.models.referral import ReferralCommissionType, ReferralPartnerType
from app.schemas.base import CamelCaseSchema, WriteSchema


class ReferralPartnerCreate(WriteSchema):
    nome: str
    tipo: ReferralPartnerType = ReferralPartnerType.contato
    contato_ref: Optional[str] = None
    comissao_tipo: ReferralCommissionType = ReferralCommissionType.percentual
    comissao_valor: float
    vigencia_inicio: date
    vigencia_fim: Optional[date] = None
    ativo: bool = True
    notas: Optional[str] = None


class ReferralPartnerUpdate(WriteSchema):
    nome: Optional[str] = None
    tipo: Optional[ReferralPartnerType] = None
    contato_ref: Optional[str] = None
    comissao_tipo: Optional[ReferralCommissionType] = None
    comissao_valor: Optional[float] = None
    vigencia_inicio: Optional[date] = None
    vigencia_fim: Optional[date] = None
    ativo: Optional[bool] = None
    notas: Optional[str] = None


class ReferralPartnerRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    nome: str
    tipo: ReferralPartnerType
    contato_ref: Optional[str] = None
    comissao_tipo: ReferralCommissionType
    comissao_valor: float
    vigencia_inicio: date
    vigencia_fim: Optional[date] = None
    ativo: bool
    notas: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MatterReferralCreate(WriteSchema):
    partner_id: UUID
    data: Optional[datetime] = None


class MatterReferralRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    partner_id: UUID
    registrado_por: Optional[UUID] = None
    data: datetime
    created_at: datetime


class ReferralCommissionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    partner_id: UUID
    charge_id: UUID
    expense_entry_id: UUID
    valor_base: float
    comissao_tipo: ReferralCommissionType
    comissao_valor_calculado: float
    created_at: datetime
