"""
Endpoints de recorrência e parcelamento (F3) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F3).
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.models.finance_recurrence import FinanceRecurrenceStatus
from app.schemas.finance_recurrence import (
    FinanceInstallmentEntryRead,
    FinanceInstallmentSeriesCreate,
    FinanceInstallmentSeriesRead,
    FinanceRecurrenceCreate,
    FinanceRecurrenceRead,
    FinanceRecurrenceUpdate,
)
from app.services.finance_charge_service import FinanceChargeService
from app.services.finance_recurrence_service import FinanceRecurrenceService

router = APIRouter(prefix="/finance", tags=["Financeiro — Recorrência e parcelamento"])


@router.post(
    "/recurrences",
    response_model=FinanceRecurrenceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar recorrência (assinatura no gateway ou geração agendada local)",
)
async def create_finance_recurrence(
    payload: FinanceRecurrenceCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    recurrence = await FinanceRecurrenceService(db).create_recurrence(workspace_id, payload)
    await db.commit()
    await db.refresh(recurrence)
    return FinanceRecurrenceRead.model_validate(recurrence)


@router.get(
    "/recurrences",
    response_model=List[FinanceRecurrenceRead],
    summary="Listar recorrências",
)
async def list_finance_recurrences(
    status_filter: Optional[FinanceRecurrenceStatus] = Query(None, alias="status"),
    client_id: Optional[UUID] = Query(None),
    limit: int = Query(100, ge=1, le=300),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    recurrences = await FinanceRecurrenceService(db).list_recurrences(
        workspace_id, status_filter=status_filter, client_id=client_id, limit=limit
    )
    return [FinanceRecurrenceRead.model_validate(recurrence) for recurrence in recurrences]


@router.get(
    "/recurrences/{recurrence_id}",
    response_model=FinanceRecurrenceRead,
    summary="Detalhe de uma recorrência",
)
async def get_finance_recurrence(
    recurrence_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    recurrence = await FinanceRecurrenceService(db).get_recurrence(workspace_id, recurrence_id)
    return FinanceRecurrenceRead.model_validate(recurrence)


@router.patch(
    "/recurrences/{recurrence_id}",
    response_model=FinanceRecurrenceRead,
    summary="Atualizar o molde de lançamento de uma recorrência",
)
async def update_finance_recurrence(
    recurrence_id: UUID,
    payload: FinanceRecurrenceUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    recurrence = await FinanceRecurrenceService(db).update_recurrence(
        workspace_id, recurrence_id, payload
    )
    await db.commit()
    await db.refresh(recurrence)
    return FinanceRecurrenceRead.model_validate(recurrence)


@router.post(
    "/recurrences/{recurrence_id}/pause",
    response_model=FinanceRecurrenceRead,
    summary="Pausar uma recorrência (o tick para de gerar lançamentos)",
)
async def pause_finance_recurrence(
    recurrence_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    recurrence = await FinanceRecurrenceService(db).pause_recurrence(workspace_id, recurrence_id)
    await db.commit()
    await db.refresh(recurrence)
    return FinanceRecurrenceRead.model_validate(recurrence)


@router.post(
    "/recurrences/{recurrence_id}/resume",
    response_model=FinanceRecurrenceRead,
    summary="Retomar uma recorrência pausada",
)
async def resume_finance_recurrence(
    recurrence_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    recurrence = await FinanceRecurrenceService(db).resume_recurrence(workspace_id, recurrence_id)
    await db.commit()
    await db.refresh(recurrence)
    return FinanceRecurrenceRead.model_validate(recurrence)


@router.post(
    "/recurrences/{recurrence_id}/end",
    response_model=FinanceRecurrenceRead,
    summary="Encerrar definitivamente uma recorrência",
)
async def end_finance_recurrence(
    recurrence_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    recurrence = await FinanceRecurrenceService(db).end_recurrence(workspace_id, recurrence_id)
    await db.commit()
    await db.refresh(recurrence)
    return FinanceRecurrenceRead.model_validate(recurrence)


@router.post(
    "/entries/{entry_id}/installments",
    response_model=FinanceInstallmentSeriesRead,
    status_code=status.HTTP_201_CREATED,
    summary="Parcelar um lançamento em N FinanceEntries filhas",
)
async def create_finance_installment_series(
    entry_id: UUID,
    payload: FinanceInstallmentSeriesCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    installment_ref, child_entries = await FinanceChargeService(db).create_installment_series(
        workspace_id,
        entry_id,
        payload.installment_count,
        total_value=payload.total_value,
        first_due_date=payload.first_due_date,
        billing_type=payload.billing_type,
        customer_external_id=payload.customer_external_id,
    )
    await db.commit()
    entries_read = []
    for entry in child_entries:
        await db.refresh(entry)
        metadata = entry.entry_metadata or {}
        entries_read.append(
            FinanceInstallmentEntryRead(
                id=entry.id,
                installment_index=metadata.get("installmentIndex"),
                installment_count=metadata.get("installmentCount"),
                amount=entry.amount,
                due_date=entry.due_date,
            )
        )
    return FinanceInstallmentSeriesRead(
        installment_ref=installment_ref,
        parent_entry_id=entry_id,
        entries=entries_read,
    )
