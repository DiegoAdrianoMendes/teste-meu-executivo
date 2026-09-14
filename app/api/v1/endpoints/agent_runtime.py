"""
Endpoints de runtime de agentes e override humano.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    can_view_sensitive_lead_data,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_agent_run, mask_human_override
from app.schemas.agent_runtime import (
    AgentRunRead,
    AgentRunRetryResponse,
    HumanOverrideCreate,
    HumanOverrideRead,
)
from app.services.agent_runtime_service import AgentRuntimeService
from app.workers.event_bus import retry_agent_run

router = APIRouter(prefix="/agent-runs", tags=["Agent Runtime"])


@router.get(
    "/matters/{matter_id}",
    response_model=list[AgentRunRead],
    summary="Listar execuções de agentes de um card",
)
async def list_runs_for_matter(
    matter_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "view")),
):
    service = AgentRuntimeService(db)
    runs = await service.list_runs_for_matter(matter_id, workspace_id)
    return runs if can_view_sensitive else [mask_agent_run(item) for item in runs]


@router.post(
    "/matters/{matter_id}/{run_id}/retry",
    response_model=AgentRunRetryResponse,
    summary="Reenfileirar uma execução de agente",
)
async def retry_run(
    matter_id: UUID,
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    _: object = Depends(require_permission("kanban", "edit")),
):
    service = AgentRuntimeService(db)
    run = await service.get_run(run_id, workspace_id)
    if not run or run.matter_id != matter_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Execução não encontrada."
        )

    retried = await retry_agent_run(
        run_id=run_id, workspace_id=workspace_id, triggered_by_user_id=current_user_id
    )
    if not retried:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não foi possível reenfileirar esta execução.",
        )

    return AgentRunRetryResponse(
        matter_id=matter_id,
        agent_run_id=retried.id,
        status=str(getattr(retried.status, "value", retried.status)),
        message="Retry registrado com sucesso.",
    )


@router.post(
    "/matters/{matter_id}/{run_id}/override",
    response_model=HumanOverrideRead,
    summary="Registrar override humano em uma execução",
)
async def override_run(
    matter_id: UUID,
    run_id: UUID,
    payload: HumanOverrideCreate,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("aprovacoes", "approve")),
):
    service = AgentRuntimeService(db)
    run = await service.get_run(run_id, workspace_id)
    if not run or run.matter_id != matter_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Execução não encontrada."
        )

    override = await service.create_override(
        run=run,
        workspace_id=workspace_id,
        decided_by_user_id=current_user_id,
        decision=payload.decision,
        notes=payload.notes,
        corrected_payload=payload.corrected_payload,
    )
    return override if can_view_sensitive else mask_human_override(override)
