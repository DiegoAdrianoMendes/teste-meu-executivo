"""Governed planning endpoint for contextual operational copilots."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_permissions, get_current_workspace_id, get_db
from app.core.rbac import Permission
from app.schemas.operational_copilot import (
    OperationalCopilotPlanRead,
    OperationalCopilotPlanRequest,
)
from app.services.operational_copilot_service import OperationalCopilotService

router = APIRouter(prefix="/operational-copilot", tags=["operational-copilot"])

_DOMAIN_READ_PERMISSION = {
    "playbooks": ("configuracoes", "view"),
    "tracking": ("relatorios", "view"),
    "connections": ("integrations", "view"),
    "social_comments": ("mensagens", "view"),
    "agenda": ("lista", "view"),
}


@router.post(
    "/plan",
    response_model=OperationalCopilotPlanRead,
    summary="Propor ações locais e governadas para uma página operacional",
)
async def plan_with_operational_copilot(
    body: OperationalCopilotPlanRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    permissions: list[Permission] = Depends(get_current_permissions),
    db: AsyncSession = Depends(get_db),
):
    required = _DOMAIN_READ_PERMISSION[body.domain]
    if not any(
        permission.module == required[0] and permission.action == required[1]
        for permission in permissions
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "COPILOT_PERMISSION_DENIED",
                "message": "Sua permissão atual não permite usar o copiloto nesta página.",
            },
        )
    return await OperationalCopilotService(db).plan(workspace_id, body)
