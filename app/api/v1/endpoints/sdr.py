"""
Endpoints internos do SDR Jurídico Trabalhista v1.

Permitem consultar o status da qualificação e reprocessar o SDR de um Matter.
Acesso restrito a usuários autenticados.
"""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    can_view_sensitive_lead_data,
    get_async_db,
    get_current_user_id,
    get_current_workspace_id,
    require_permission,
)
from app.core.lead_privacy import mask_sdr_status
from app.core.logging import logger
from app.models.matter import Matter
from app.schemas.sdr import SDRReprocessRequest, SDRReprocessResponse, SDRStatusResponse, SDRStep

router = APIRouter(prefix="/sdr", tags=["SDR"])


@router.get(
    "/matters/{matter_id}",
    response_model=SDRStatusResponse,
    summary="Status do SDR de um Matter",
)
async def get_sdr_status(
    matter_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    _: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    __: object = Depends(require_permission("kanban", "view")),
) -> SDRStatusResponse:
    """
    Retorna o status atual da qualificação SDR de um Matter.

    Inclui a qualificação estruturada completa se disponível.
    """
    result = await db.execute(select(Matter).where(Matter.id == matter_id, Matter.workspace_id == workspace_id))
    matter = result.scalars().first()
    if not matter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SDR-001", "message": "Matter não encontrado."},
        )

    from app.schemas.sdr import SDRQualification

    sdr_qual_data = matter.sdr_qualification or {}
    qualification = SDRQualification(**sdr_qual_data) if sdr_qual_data else None
    current_step = qualification.current_step if qualification else SDRStep.inicio
    sdr_completed = qualification.completed if qualification else False

    response = SDRStatusResponse(
        matter_id=matter_id,
        sdr_step=current_step,
        sdr_completed=sdr_completed,
        qualification=qualification,
    )
    return response if can_view_sensitive else mask_sdr_status(response)


@router.post(
    "/matters/{matter_id}/reprocess",
    response_model=SDRReprocessResponse,
    summary="Reprocessar SDR de um Matter",
)
async def reprocess_sdr(
    matter_id: UUID,
    payload: SDRReprocessRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("kanban", "edit")),
) -> SDRReprocessResponse:
    """
    Força o reprocessamento do SDR para um Matter existente.

    Útil quando:
    - A qualificação automática falhou ou ficou incompleta.
    - O cliente enviou novas informações após a qualificação inicial.
    - Um operador quer resetar e requalificar o lead.

    O reprocessamento ocorre em background — a resposta é imediata.
    """
    result = await db.execute(select(Matter).where(Matter.id == matter_id, Matter.workspace_id == workspace_id))
    matter = result.scalars().first()
    if not matter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SDR-001", "message": "Matter não encontrado."},
        )

    # Resetar qualificação para permitir reprocessamento
    if matter.sdr_qualification:
        matter.sdr_qualification = None
        await db.commit()

    logger.info(
        "sdr.reprocess_requested",
        matter_id=str(matter_id),
        reason_provided=bool(payload.reason),
    )

    # Disparar SDR em background
    from app.workers.event_bus import dispatch_new_lead
    background_tasks.add_task(
        dispatch_new_lead,
        matter_id,
        current_user_id,
        workspace_id=workspace_id,
    )

    return SDRReprocessResponse(
        matter_id=matter_id,
        status="queued",
        message="Reprocessamento do SDR iniciado em background.",
    )
