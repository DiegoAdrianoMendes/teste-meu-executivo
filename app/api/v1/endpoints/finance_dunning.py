"""
Endpoints da régua de cobrança (dunning) — F4. Ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F4.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.models.finance_dunning import FinanceDunningLogStatus
from app.schemas.finance_dunning import (
    FinanceDunningLogRead,
    FinanceDunningSettingsRead,
    FinanceDunningSettingsUpdate,
)
from app.services.finance_dunning_service import FinanceDunningService
from app.workers.finance_tasks import approve_dunning_log

router = APIRouter(prefix="/finance", tags=["Financeiro — Régua de cobrança"])


@router.get(
    "/dunning/config",
    response_model=FinanceDunningSettingsRead,
    summary="Consultar configuração da régua de cobrança",
)
async def get_dunning_config(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    return await FinanceDunningService(db).get_config(workspace_id)


@router.patch(
    "/dunning/config",
    response_model=FinanceDunningSettingsRead,
    summary="Atualizar configuração da régua de cobrança",
)
async def update_dunning_config(
    payload: FinanceDunningSettingsUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    result = await FinanceDunningService(db).update_config(workspace_id, payload)
    await db.commit()
    return result


@router.get(
    "/dunning/log",
    response_model=List[FinanceDunningLogRead],
    summary="Listar disparos recentes da régua de cobrança do workspace",
)
async def list_recent_dunning_logs(
    status_filter: Optional[FinanceDunningLogStatus] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    """Alimenta a lista de "últimos disparos" e, com
    `status=pending_approval`, a fila de degraus aguardando aprovação
    humana (o D+15 formal sempre exige aprovação — ver
    DUNNING_DEFAULT_STEP_CONFIG)."""
    return await FinanceDunningService(db).list_recent_logs(
        workspace_id, status_filter=status_filter, limit=limit
    )


@router.get(
    "/dunning/charges/{charge_id}/log",
    response_model=List[FinanceDunningLogRead],
    summary="Listar o log da régua de cobrança de uma cobrança",
)
async def list_dunning_log(
    charge_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    entries = await FinanceDunningService(db).list_log_for_charge(workspace_id, charge_id)
    return [FinanceDunningLogRead.model_validate(entry) for entry in entries]


@router.post(
    "/dunning/log/{log_id}/approve",
    response_model=FinanceDunningLogRead,
    summary="Aprovar (humano) um degrau pendente da régua de cobrança",
)
async def approve_dunning_log_endpoint(
    log_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        log_entry = await approve_dunning_log(db, workspace_id, log_id, auth.user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FIN-DUN-001", "message": "Degrau da régua de cobrança não encontrado."},
        )
    await db.commit()
    await db.refresh(log_entry)
    return FinanceDunningLogRead.model_validate(log_entry)
