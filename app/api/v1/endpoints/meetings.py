"""
Agenda interna do workspace.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_meeting
from app.schemas.control_plane import (
    MeetingCreate,
    MeetingParticipantsUpdate,
    MeetingRead,
    MeetingUpdate,
)
from app.services.meeting_service import MeetingService

router = APIRouter(prefix="/meetings", tags=["Agenda"])


@router.get("", response_model=list[MeetingRead], summary="Listar reuniões do workspace")
async def list_meetings(
    matter_id: UUID | None = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("lista", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    meetings = await MeetingService(db).list_for_workspace(workspace_id, matter_id=matter_id)
    return meetings if can_view_sensitive else [mask_meeting(item) for item in meetings]


@router.post(
    "",
    response_model=MeetingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar reunião",
)
async def create_meeting(
    payload: MeetingCreate,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=120,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    meeting = await MeetingService(db).create_for_workspace(
        workspace_id,
        payload,
        actor_user_id=auth_context.user_id,
        idempotency_key=idempotency_key,
        correlation_id=request.headers.get("X-Correlation-ID"),
    )
    await db.commit()
    response = MeetingRead.model_validate(meeting)
    return response if can_view_sensitive else mask_meeting(response)


@router.patch("/{meeting_id}", response_model=MeetingRead, summary="Atualizar reunião")
async def update_meeting(
    meeting_id: UUID,
    payload: MeetingUpdate,
    request: Request,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    service = MeetingService(db)
    meeting = await service.update_for_workspace(
        workspace_id,
        meeting_id,
        payload,
        actor_user_id=auth_context.user_id,
        correlation_id=request.headers.get("X-Correlation-ID"),
    )
    await db.commit()
    response = MeetingRead.model_validate(meeting)
    return response if can_view_sensitive else mask_meeting(response)


@router.put(
    "/{meeting_id}/participants",
    response_model=MeetingRead,
    summary="Substituir participantes internos do compromisso",
)
async def replace_meeting_participants(
    meeting_id: UUID,
    payload: MeetingParticipantsUpdate,
    request: Request,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    service = MeetingService(db)
    meeting = await service.replace_participants_for_workspace(
        workspace_id,
        meeting_id,
        participants=payload.participants,
        expected_version=payload.expected_version,
        confirm_participant_conflicts=payload.confirm_participant_conflicts,
        conflict_reason=payload.conflict_reason,
        actor_user_id=auth_context.user_id,
        correlation_id=request.headers.get("X-Correlation-ID"),
    )
    await db.commit()
    response = MeetingRead.model_validate(meeting)
    return response if can_view_sensitive else mask_meeting(response)


@router.delete(
    "/{meeting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancelar reunião preservando o histórico",
)
async def delete_meeting(
    meeting_id: UUID,
    request: Request,
    expected_version: int = Query(alias="expectedVersion", ge=1),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth_context: AuthContext = Depends(require_permission("kanban", "edit")),
    db: AsyncSession = Depends(get_db),
):
    await MeetingService(db).delete_for_workspace(
        workspace_id,
        meeting_id,
        expected_version=expected_version,
        actor_user_id=auth_context.user_id,
        correlation_id=request.headers.get("X-Correlation-ID"),
    )
    await db.commit()
