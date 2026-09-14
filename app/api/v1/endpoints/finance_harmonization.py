"""
Endpoints de Harmonização de Documentos Financeiros × Lançamentos Manuais
(F14) — ver `app/services/finance_harmonization_service.py`.
"""

from __future__ import annotations

from datetime import date
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.core.request_body import read_bounded_upload
from app.schemas.finance_harmonization import (
    FinanceHarmonizationBonusGrantWrite,
    FinanceHarmonizationItemDecisionWrite,
    FinanceHarmonizationItemRead,
    FinanceHarmonizationQuotaRead,
    FinanceHarmonizationRunDetailRead,
    FinanceHarmonizationRunRead,
)
from app.services.finance_harmonization_service import FinanceHarmonizationService

router = APIRouter(
    prefix="/finance/harmonization", tags=["Financeiro — Harmonização de Documentos"]
)


@router.get(
    "/quota",
    response_model=FinanceHarmonizationQuotaRead,
    summary="Cota mensal de verificação completa (1 base + 2 bônus + bônus extra do admin)",
)
async def get_harmonization_quota(
    workspace_id=Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    status_payload = await FinanceHarmonizationService(db).get_quota_status(workspace_id)
    await db.commit()
    return FinanceHarmonizationQuotaRead(**status_payload)


@router.get(
    "/runs",
    response_model=List[FinanceHarmonizationRunRead],
    summary="Listar verificações completas de harmonização",
)
async def list_harmonization_runs(
    workspace_id=Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    runs = await FinanceHarmonizationService(db).list_runs(workspace_id)
    return [FinanceHarmonizationRunRead.model_validate(run) for run in runs]


@router.post(
    "/runs",
    response_model=FinanceHarmonizationRunDetailRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar documento (PDF/XML/CSV) e rodar a verificação completa do mês",
)
async def create_harmonization_run(
    period_start: date = Form(...),
    period_end: date = Form(...),
    file: UploadFile = File(...),
    workspace_id=Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    content = await read_bounded_upload(
        file,
        max_bytes=settings.FINANCE_HARMONIZATION_UPLOAD_MAX_BYTES,
    )
    run = await FinanceHarmonizationService(db).create_run(
        workspace_id,
        filename=file.filename or "documento",
        content_type=file.content_type,
        raw_bytes=content,
        period_start=period_start,
        period_end=period_end,
        created_by=context.user_id,
    )
    await db.commit()
    return FinanceHarmonizationRunDetailRead.model_validate(run)


@router.get(
    "/runs/{run_id}",
    response_model=FinanceHarmonizationRunDetailRead,
    summary="Detalhe de uma verificação com a tabela comparativa completa",
)
async def get_harmonization_run(
    run_id: UUID,
    workspace_id=Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    run = await FinanceHarmonizationService(db).get_run(workspace_id, run_id)
    return FinanceHarmonizationRunDetailRead.model_validate(run)


@router.post(
    "/runs/{run_id}/items/{item_id}/decision",
    response_model=FinanceHarmonizationItemRead,
    summary="Decidir uma linha comparativa (confirmar/rejeitar/ajustar) — exactly-once",
)
async def decide_harmonization_item(
    run_id: UUID,
    item_id: UUID,
    payload: FinanceHarmonizationItemDecisionWrite,
    workspace_id=Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    item = await FinanceHarmonizationService(db).decide_item(
        workspace_id,
        run_id,
        item_id,
        decision=payload.decision,
        decided_by=context.user_id,
    )
    await db.commit()
    return FinanceHarmonizationItemRead.model_validate(item)


@router.post(
    "/runs/{run_id}/validate",
    response_model=FinanceHarmonizationRunRead,
    summary="Validar a verificação completa (encerra o run — nada é aplicado automaticamente)",
)
async def validate_harmonization_run(
    run_id: UUID,
    workspace_id=Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    run = await FinanceHarmonizationService(db).validate_run(
        workspace_id, run_id, decided_by=context.user_id
    )
    await db.commit()
    return FinanceHarmonizationRunRead.model_validate(run)


@router.post(
    "/runs/{run_id}/reject",
    response_model=FinanceHarmonizationRunRead,
    summary="Rejeitar a verificação completa (encerra o run)",
)
async def reject_harmonization_run(
    run_id: UUID,
    workspace_id=Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    run = await FinanceHarmonizationService(db).reject_run(
        workspace_id, run_id, decided_by=context.user_id
    )
    await db.commit()
    return FinanceHarmonizationRunRead.model_validate(run)
