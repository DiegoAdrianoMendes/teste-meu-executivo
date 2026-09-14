from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_auth_context,
    get_current_permissions,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_internal_note, mask_lead_drawer
from app.core.rbac import Permission
from app.schemas.whatsapp_collaboration import (
    ConversationInternalNoteCreate,
    ConversationInternalNoteRead,
    LeadDrawerRead,
    MessageOptimizationRead,
    MessageOptimizationRequest,
    MoveConversationStageRequest,
)
from app.services.whatsapp_service import WhatsAppService

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Collaboration"])


def _get_service(db: AsyncSession = Depends(get_db)) -> WhatsAppService:
    return WhatsAppService(db)


@router.get("/conversations/{contact_id}/notes", response_model=List[ConversationInternalNoteRead], summary="Listar notas internas da conversa")
async def list_conversation_notes(
    contact_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("mensagens", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    notes = await svc.list_conversation_notes(contact_id, workspace_id)
    return notes if can_view_sensitive else [mask_internal_note(item) for item in notes]


@router.post("/conversations/{contact_id}/notes", response_model=ConversationInternalNoteRead, status_code=status.HTTP_201_CREATED, summary="Adicionar nota interna")
async def create_conversation_note(
    contact_id: UUID,
    payload: ConversationInternalNoteCreate,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_permission("mensagens", "send_message")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
):
    note = await svc.create_conversation_note(contact_id, workspace_id, context.user_id, payload)
    return note if can_view_sensitive else mask_internal_note(note)


@router.get("/conversations/{contact_id}/drawer", response_model=LeadDrawerRead, summary="Carregar drawer lateral do lead")
async def get_lead_drawer(
    contact_id: UUID,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    permissions: list[Permission] = Depends(get_current_permissions),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("mensagens", "view")),
):
    can_edit = any(p.module == "kanban" and p.action == "edit" for p in permissions)
    can_manage = any(
        p.module == "mensagens" and p.action == "send_message" for p in permissions
    )
    drawer = await svc.get_lead_drawer(
        contact_id,
        workspace_id,
        can_edit_matter=can_edit,
        can_manage_conversation=can_manage,
    )
    return drawer if can_view_sensitive else mask_lead_drawer(drawer)


@router.post("/conversations/{contact_id}/move-stage", response_model=LeadDrawerRead, summary="Mover o estágio do card a partir do inbox")
async def move_conversation_stage(
    contact_id: UUID,
    payload: MoveConversationStageRequest,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    permissions: list[Permission] = Depends(get_current_permissions),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "edit")),
):
    can_edit = any(p.module == "kanban" and p.action == "edit" for p in permissions)
    can_manage = any(
        p.module == "mensagens" and p.action == "send_message" for p in permissions
    )
    drawer = await svc.move_conversation_stage(
        contact_id,
        workspace_id,
        context.user_id,
        payload,
        can_edit_matter=can_edit,
        can_manage_conversation=can_manage,
    )
    return drawer if can_view_sensitive else mask_lead_drawer(drawer)


@router.post("/messages/optimize", response_model=MessageOptimizationRead, summary="Gerar sugestões de melhoria para a mensagem")
async def optimize_message(
    payload: MessageOptimizationRequest,
    svc: WhatsAppService = Depends(_get_service),
    _: object = Depends(require_permission("mensagens", "view")),
):
    return await svc.optimize_message(payload.body, payload.tone)
