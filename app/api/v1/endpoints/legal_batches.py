"""
Endpoints: Operação Massificada / Lotes (Onda 8, Plano Mestre §7).
Ver app/models/legal_batch.py e app/services/legal_batch_service.py.

REGRA DURA aplicada em `/approve-all`: itens de decisão crítica (tese,
prazo fatal, texto final, protocolo) NUNCA são aprovados em lote — o
serviço sempre os devolve em `skippedProtectedItemIds`, exigindo
`/items/{item_id}/approve` (item a item, sempre humano).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.schemas.legal_batch import (
    LegalBatchApproveAllResult,
    LegalBatchCreate,
    LegalBatchCreateFromContext,
    LegalBatchItemRead,
    LegalBatchPanel,
    LegalBatchProcessResult,
    LegalBatchRead,
    LegalBatchRollbackRequest,
)
from app.services.legal_batch_service import LegalBatchService

router = APIRouter(prefix="/legal/batches", tags=["Jurídico — Operação Massificada (Onda 8)"])


def _panel_to_schema(panel) -> LegalBatchPanel:
    return LegalBatchPanel(
        batch_id=panel.batch_id,
        status=panel.status,
        total_items=panel.total_items,
        pendente=panel.counts["pendente"],
        classificado=panel.counts["classificado"],
        vinculado=panel.counts["vinculado"],
        rascunho=panel.counts["rascunho"],
        revisado=panel.counts["revisado"],
        bloqueado=panel.counts["bloqueado"],
        baixa_confianca=panel.counts["baixa_confianca"],
        divergente=panel.counts["divergente"],
        aguardando_humano=panel.counts["aguardando_humano"],
        concluido=panel.counts["concluido"],
        estimated_cost_total=panel.estimated_cost_total,
        estimated_time_saved_minutes_total=panel.estimated_time_saved_minutes_total,
    )


@router.post(
    "",
    response_model=LegalBatchRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar lote por critério — idempotente por external_ref",
)
async def create_batch(
    payload: LegalBatchCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    batch = await LegalBatchService(db).create_batch(
        workspace_id=workspace_id, user_id=auth.user_id, payload=payload
    )
    await db.commit()
    await db.refresh(batch)
    return LegalBatchRead.model_validate(batch)


@router.post(
    "/from-context",
    response_model=LegalBatchRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar lote a partir de um cliente ou de uma demanda — recorte por contexto",
)
async def create_batch_from_context(
    payload: LegalBatchCreateFromContext,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    batch = await LegalBatchService(db).create_batch_from_context(
        workspace_id=workspace_id, user_id=auth.user_id, payload=payload
    )
    await db.commit()
    await db.refresh(batch)
    return LegalBatchRead.model_validate(batch)


@router.get(
    "",
    response_model=list[LegalBatchRead],
    summary="Listar lotes do workspace",
)
async def list_batches(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    batches = await LegalBatchService(db).list_batches(workspace_id)
    return [LegalBatchRead.model_validate(batch) for batch in batches]


@router.get(
    "/{batch_id}",
    response_model=LegalBatchRead,
    summary="Detalhe do lote",
)
async def get_batch(
    batch_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    batch = await LegalBatchService(db).get_batch(workspace_id, batch_id)
    return LegalBatchRead.model_validate(batch)


@router.get(
    "/{batch_id}/items",
    response_model=list[LegalBatchItemRead],
    summary="Listar itens do lote — cada um com ESTADO INDIVIDUAL",
)
async def list_batch_items(
    batch_id: UUID,
    state: Optional[str] = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    items = await LegalBatchService(db).list_items(workspace_id, batch_id, state=state)
    return [LegalBatchItemRead.model_validate(item) for item in items]


@router.get(
    "/{batch_id}/panel",
    response_model=LegalBatchPanel,
    summary=(
        "Painel do lote — total/concluídos/bloqueados/baixa confiança/"
        "divergentes/aguardando humano/custo/tempo economizado"
    ),
)
async def get_batch_panel(
    batch_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
):
    panel = await LegalBatchService(db).get_panel(workspace_id, batch_id)
    return _panel_to_schema(panel)


@router.post(
    "/{batch_id}/process",
    response_model=LegalBatchProcessResult,
    summary="Processar itens pendentes do lote — falha parcial NUNCA escondida",
)
async def process_batch(
    batch_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    result = await LegalBatchService(db).process_all(
        workspace_id=workspace_id, batch_id=batch_id, user_id=auth.user_id
    )
    await db.commit()
    return LegalBatchProcessResult(
        batch_id=result.batch_id,
        processed_item_ids=result.processed_item_ids,
        succeeded_item_ids=result.succeeded_item_ids,
        failed_item_ids=result.failed_item_ids,
        panel=_panel_to_schema(result.panel),
    )


@router.post(
    "/{batch_id}/items/{item_id}/approve",
    response_model=LegalBatchItemRead,
    summary="Aprovar UM item — único caminho para tese/prazo fatal/texto final/protocolo",
)
async def approve_batch_item(
    batch_id: UUID,
    item_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    item = await LegalBatchService(db).approve_item(
        workspace_id=workspace_id, batch_id=batch_id, item_id=item_id, user_id=auth.user_id
    )
    await db.commit()
    await db.refresh(item)
    return LegalBatchItemRead.model_validate(item)


@router.post(
    "/{batch_id}/approve-all",
    response_model=LegalBatchApproveAllResult,
    summary="Aprovar em lote — NUNCA cobre tese/prazo fatal/texto final/protocolo",
)
async def approve_all_batch_items(
    batch_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    result = await LegalBatchService(db).approve_all(
        workspace_id=workspace_id, batch_id=batch_id, user_id=auth.user_id
    )
    await db.commit()
    return LegalBatchApproveAllResult(
        batch_id=result.batch_id,
        approved_item_ids=result.approved_item_ids,
        skipped_protected_item_ids=result.skipped_protected_item_ids,
        skipped_not_pending_item_ids=result.skipped_not_pending_item_ids,
    )


@router.post(
    "/{batch_id}/items/{item_id}/rollback",
    response_model=LegalBatchItemRead,
    summary="Reverter item ao estado anterior — nunca destrutivo (trilha em append)",
)
async def rollback_batch_item(
    batch_id: UUID,
    item_id: UUID,
    payload: LegalBatchRollbackRequest = LegalBatchRollbackRequest(),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
    db: AsyncSession = Depends(get_db),
):
    item = await LegalBatchService(db).rollback_item(
        workspace_id=workspace_id,
        batch_id=batch_id,
        item_id=item_id,
        user_id=auth.user_id,
        reason=payload.reason,
    )
    await db.commit()
    await db.refresh(item)
    return LegalBatchItemRead.model_validate(item)
