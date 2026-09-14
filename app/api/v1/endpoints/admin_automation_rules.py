from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace_id, get_db, require_permission
from app.schemas.whatsapp_automation import WhatsAppAutomationSettingsRead, WhatsAppAutomationSettingsUpdate
from app.services.whatsapp_service import WhatsAppService

router = APIRouter(prefix="/admin", tags=["Admin WhatsApp Automation"])


def _get_service(db: AsyncSession = Depends(get_db)) -> WhatsAppService:
    return WhatsAppService(db)


@router.get("/automation-rules", response_model=WhatsAppAutomationSettingsRead, summary="Configurações de automação simples do inbox")
async def get_automation_rules(
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("configuracoes", "view")),
):
    return await svc.get_automation_settings(workspace_id)


@router.put("/automation-rules", response_model=WhatsAppAutomationSettingsRead, summary="Salvar automações simples do inbox")
async def update_automation_rules(
    payload: WhatsAppAutomationSettingsUpdate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("configuracoes", "edit")),
):
    return await svc.update_automation_settings(workspace_id, payload)
