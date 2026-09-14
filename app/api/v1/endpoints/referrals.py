"""
Endpoints: Indicações com comissão (F13) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F13.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.schemas.referral import (
    MatterReferralCreate,
    MatterReferralRead,
    ReferralCommissionRead,
    ReferralPartnerCreate,
    ReferralPartnerRead,
    ReferralPartnerUpdate,
)
from app.services.referral_service import ReferralService

router = APIRouter(prefix="/referrals", tags=["Financeiro — Indicações"])


@router.post(
    "/partners",
    response_model=ReferralPartnerRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastrar parceiro de indicação",
)
async def create_partner(
    payload: ReferralPartnerCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    partner = await ReferralService(db).create_partner(workspace_id, payload)
    await db.commit()
    return ReferralPartnerRead.model_validate(partner)


@router.get(
    "/partners",
    response_model=List[ReferralPartnerRead],
    summary="Listar parceiros de indicação",
)
async def list_partners(
    ativo: Optional[bool] = Query(None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    partners = await ReferralService(db).list_partners(workspace_id, ativo=ativo)
    return [ReferralPartnerRead.model_validate(partner) for partner in partners]


@router.patch(
    "/partners/{partner_id}",
    response_model=ReferralPartnerRead,
    summary="Editar parceiro de indicação",
)
async def update_partner(
    partner_id: UUID,
    payload: ReferralPartnerUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    partner = await ReferralService(db).update_partner(workspace_id, partner_id, payload)
    await db.commit()
    return ReferralPartnerRead.model_validate(partner)


@router.post(
    "/matters/{matter_id}",
    response_model=MatterReferralRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar (ou editar) a origem de indicação de um caso",
)
async def register_matter_referral(
    matter_id: UUID,
    payload: MatterReferralCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    referral = await ReferralService(db).register_matter_referral(
        workspace_id, matter_id, context.user_id, payload
    )
    await db.commit()
    return MatterReferralRead.model_validate(referral)


@router.get(
    "/matters/{matter_id}",
    response_model=Optional[MatterReferralRead],
    summary="Consultar a origem de indicação de um caso",
)
async def get_matter_referral(
    matter_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    referral = await ReferralService(db).get_matter_referral(workspace_id, matter_id)
    return MatterReferralRead.model_validate(referral) if referral else None


@router.get(
    "/commissions",
    response_model=List[ReferralCommissionRead],
    summary="Listar comissões acumuladas (despesas pending_approval/approved/paid) por parceiro",
)
async def list_commissions(
    partner_id: Optional[UUID] = Query(None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    commissions = await ReferralService(db).list_commissions(workspace_id, partner_id=partner_id)
    return [ReferralCommissionRead.model_validate(c) for c in commissions]
