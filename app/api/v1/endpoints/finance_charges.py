"""
Endpoints do serviço de cobrança (F2) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F2).
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.models.finance_charge import FinanceChargeStatus
from app.schemas.finance_charge import (
    FinanceChargeCreate,
    FinanceChargePixQrRead,
    FinanceChargeRead,
)
from app.services.finance_charge_service import DRAFT_NOTICE, FinanceChargeService

router = APIRouter(prefix="/finance", tags=["Financeiro — Cobranças"])


def _to_read(charge) -> FinanceChargeRead:
    data = FinanceChargeRead.model_validate(charge)
    if charge.status == FinanceChargeStatus.rascunho:
        data.draft_notice = DRAFT_NOTICE
    return data


@router.post(
    "/entries/{entry_id}/charge",
    response_model=FinanceChargeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar cobrança para um lançamento financeiro",
)
async def create_finance_charge(
    entry_id: UUID,
    payload: FinanceChargeCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    charge = await FinanceChargeService(db).create_charge_for_entry(workspace_id, entry_id, payload)
    await db.commit()
    return _to_read(charge)


@router.get(
    "/charges",
    response_model=List[FinanceChargeRead],
    summary="Listar cobranças por status/vencimento",
)
async def list_finance_charges(
    status_filter: Optional[FinanceChargeStatus] = Query(None, alias="status"),
    due_before: Optional[date] = Query(None),
    due_after: Optional[date] = Query(None),
    limit: int = Query(100, ge=1, le=300),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    charges = await FinanceChargeService(db).list_charges(
        workspace_id, status_filter=status_filter, due_before=due_before, due_after=due_after, limit=limit
    )
    return [_to_read(charge) for charge in charges]


@router.get(
    "/charges/{charge_id}",
    response_model=FinanceChargeRead,
    summary="Detalhe de uma cobrança (com QR PIX opcional)",
)
async def get_finance_charge(
    charge_id: UUID,
    with_pix: bool = Query(False, alias="withPix"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    charge, _pix = await FinanceChargeService(db).get_charge(workspace_id, charge_id, with_pix=with_pix)
    await db.commit()
    data = _to_read(charge)
    return data


@router.get(
    "/charges/{charge_id}/pix",
    response_model=FinanceChargePixQrRead,
    summary="QR Code PIX de uma cobrança (sob demanda, cacheado)",
)
async def get_finance_charge_pix(
    charge_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    charge, pix = await FinanceChargeService(db).get_charge(workspace_id, charge_id, with_pix=True)
    await db.commit()
    if pix is None:
        return FinanceChargePixQrRead()
    return FinanceChargePixQrRead(
        encoded_image=pix.encoded_image, payload=pix.payload, expiration_date=pix.expiration_date
    )


@router.delete(
    "/charges/{charge_id}",
    response_model=FinanceChargeRead,
    summary="Cancelar uma cobrança",
)
async def cancel_finance_charge(
    charge_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    charge = await FinanceChargeService(db).cancel_charge(workspace_id, charge_id)
    await db.commit()
    await db.refresh(charge)
    return _to_read(charge)
