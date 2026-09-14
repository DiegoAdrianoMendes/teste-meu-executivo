"""
Endpoints de Conciliação Bancária (F7) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F7.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_finance_access
from app.core.request_body import read_bounded_upload
from app.schemas.bank_reconciliation import (
    BankTransactionRead,
    ConfirmMatchesBatchWrite,
    ConfirmMatchWrite,
    IngestResultRead,
    PendingReconciliationRead,
    PullAsaasStatementWrite,
    ReconciliationMatchRead,
    RejectMatchesBatchWrite,
    RejectMatchWrite,
)
from app.services.reconciliation_service import IngestResult, ReconciliationService

router = APIRouter(prefix="/finance/reconciliation", tags=["Financeiro — Conciliação Bancária"])


def _to_ingest_read(result: IngestResult) -> IngestResultRead:
    return IngestResultRead(
        imported=result.imported,
        duplicates=result.duplicates,
        matched_deterministic=result.matched_deterministic,
        suggested=result.suggested,
    )


@router.post(
    "/import/ofx",
    response_model=IngestResultRead,
    status_code=status.HTTP_201_CREATED,
    summary="Importar extrato OFX",
)
async def import_ofx(
    file: UploadFile = File(...),
    workspace_id=Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    content = await read_bounded_upload(
        file,
        max_bytes=settings.BANK_RECONCILIATION_UPLOAD_MAX_BYTES,
    )
    result = await ReconciliationService(db).ingest_ofx(workspace_id, content)
    await db.commit()
    return _to_ingest_read(result)


@router.post(
    "/import/csv",
    response_model=IngestResultRead,
    status_code=status.HTTP_201_CREATED,
    summary="Importar extrato CSV",
)
async def import_csv(
    file: UploadFile = File(...),
    workspace_id=Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    content = await read_bounded_upload(
        file,
        max_bytes=settings.BANK_RECONCILIATION_UPLOAD_MAX_BYTES,
    )
    result = await ReconciliationService(db).ingest_csv(workspace_id, content)
    await db.commit()
    return _to_ingest_read(result)


@router.post(
    "/import/asaas",
    response_model=IngestResultRead,
    status_code=status.HTTP_201_CREATED,
    summary="Puxar extrato financeiro Asaas",
)
async def pull_asaas_statement(
    payload: PullAsaasStatementWrite,
    workspace_id=Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    result = await ReconciliationService(db).pull_asaas_statement(
        workspace_id, payload.start, payload.finish
    )
    await db.commit()
    return _to_ingest_read(result)


@router.get(
    "/pending",
    response_model=List[PendingReconciliationRead],
    summary="Listar sugestões de conciliação pendentes de conferência (com a transação de origem)",
)
async def list_pending(
    workspace_id=Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    matches = await ReconciliationService(db).list_pending(workspace_id)
    return [
        PendingReconciliationRead(
            transaction=BankTransactionRead.model_validate(match.bank_transaction),
            match=ReconciliationMatchRead.model_validate(match),
        )
        for match in matches
    ]


@router.post(
    "/matches/{match_id}/confirm",
    response_model=ReconciliationMatchRead,
    summary="Confirmar uma sugestão de conciliação (aplica a baixa/categorização)",
)
async def confirm_match(
    match_id,
    payload: ConfirmMatchWrite,
    workspace_id=Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    match = await ReconciliationService(db).confirm_match(
        workspace_id,
        match_id,
        target_kind=payload.target_kind,
        target_id=payload.target_id,
        confirmed_by=context.user_id,
        category=payload.category,
        tags=payload.tags or None,
    )
    await db.commit()
    return ReconciliationMatchRead.model_validate(match)


@router.post(
    "/matches/confirm-batch",
    response_model=List[ReconciliationMatchRead],
    summary="Confirmar várias sugestões de conciliação em lote",
)
async def confirm_matches_batch(
    payload: ConfirmMatchesBatchWrite,
    workspace_id=Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    items = [
        {
            "match_id": item.match_id,
            "target_kind": item.target_kind,
            "target_id": item.target_id,
            "category": item.category,
            "tags": item.tags or None,
        }
        for item in payload.items
    ]
    matches = await ReconciliationService(db).confirm_matches_batch(
        workspace_id, items=items, confirmed_by=context.user_id
    )
    await db.commit()
    return [ReconciliationMatchRead.model_validate(match) for match in matches]


@router.post(
    "/matches/{match_id}/reject",
    response_model=ReconciliationMatchRead,
    summary="Rejeitar uma sugestão de conciliação",
)
async def reject_match(
    match_id,
    payload: RejectMatchWrite,
    workspace_id=Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    match = await ReconciliationService(db).reject_match(
        workspace_id, match_id, confirmed_by=context.user_id, reason=payload.reason
    )
    await db.commit()
    return ReconciliationMatchRead.model_validate(match)


@router.post(
    "/matches/reject-batch",
    response_model=List[ReconciliationMatchRead],
    summary="Rejeitar várias sugestões de conciliação em lote",
)
async def reject_matches_batch(
    payload: RejectMatchesBatchWrite,
    workspace_id=Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    matches = await ReconciliationService(db).reject_matches_batch(
        workspace_id,
        match_ids=payload.match_ids,
        confirmed_by=context.user_id,
        reason=payload.reason,
    )
    await db.commit()
    return [ReconciliationMatchRead.model_validate(match) for match in matches]
