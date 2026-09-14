"""Native collaboration endpoints for canonical KLM conversations."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.models.conversation_collaboration import (
    ConversationCollaborationGroup,
    ConversationGroupParticipant,
    ConversationMention,
    ConversationMessageReference,
)
from app.schemas.conversation_collaboration import (
    ConversationCapabilitiesRead,
    ConversationGroupRead,
    ConversationMentionRead,
    ConversationMessageMentionsCreate,
    ConversationParticipantCollectionRead,
    ConversationParticipantCreate,
    ConversationParticipantRead,
    ConversationReferenceCreate,
    ConversationReferenceRead,
)
from app.services.conversation_collaboration_service import (
    ConversationCollaborationConflictError,
    ConversationCollaborationService,
)

router = APIRouter(
    prefix="/klm/omnichannel/collaboration",
    tags=["KLM Conversation Collaboration"],
)


def _service(db: AsyncSession = Depends(get_db)) -> ConversationCollaborationService:
    return ConversationCollaborationService(db)


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


def _mention_read(mention: ConversationMention) -> ConversationMentionRead:
    return ConversationMentionRead.model_validate(
        {
            "id": mention.id,
            "workspace_id": mention.workspace_id,
            "thread_id": mention.thread_id,
            "operation_id": mention.operation_id,
            "reference_id": mention.reference_id,
            "source_message_id": mention.source_message_id,
            "mention_type": mention.mention_type,
            "target_id": mention.mentioned_user_id or mention.mentioned_team_id,
            "resolved_label": mention.resolved_label,
            "resolution_source": mention.resolution_source,
            "resolved_at": mention.resolved_at,
            "created_by_user_id": mention.created_by_user_id,
            "created_at": mention.created_at,
        }
    )


def _reference_read(
    reference: ConversationMessageReference,
    mentions: tuple[ConversationMention, ...],
) -> ConversationReferenceRead:
    return ConversationReferenceRead.model_validate(
        {
            "id": reference.id,
            "workspace_id": reference.workspace_id,
            "thread_id": reference.thread_id,
            "source_message_id": reference.source_message_id,
            "target_message_id": reference.target_message_id,
            "reference_type": reference.reference_type,
            "idempotency_key": reference.idempotency_key,
            "created_by_user_id": reference.created_by_user_id,
            "created_at": reference.created_at,
            "mentions": [_mention_read(mention) for mention in mentions],
        }
    )


def _group_read(group: ConversationCollaborationGroup) -> ConversationGroupRead:
    return ConversationGroupRead.model_validate(group)


def _participant_read(participant: ConversationGroupParticipant) -> ConversationParticipantRead:
    return ConversationParticipantRead.model_validate(
        {
            "id": participant.id,
            "workspace_id": participant.workspace_id,
            "thread_id": participant.thread_id,
            "group_id": participant.group_id,
            "participant_type": participant.participant_type,
            "participant_id": participant.user_id or participant.team_id,
            "role": participant.role,
            "resolved_label": participant.resolved_label,
            "resolution_source": participant.resolution_source,
            "resolved_at": participant.resolved_at,
            "idempotency_key": participant.idempotency_key,
            "created_by_user_id": participant.created_by_user_id,
            "created_at": participant.created_at,
        }
    )


@router.post(
    "/threads/{thread_id}/references",
    response_model=ConversationReferenceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a structured message reference",
)
async def create_reference(
    thread_id: UUID,
    payload: ConversationReferenceCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: ConversationCollaborationService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> ConversationReferenceRead:
    try:
        result = await service.create_reference(
            workspace_id,
            thread_id,
            auth.user_id,
            payload,
        )
    except (ConversationCollaborationConflictError, LookupError, ValueError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    return _reference_read(result.reference, result.mentions)


@router.get(
    "/threads/{thread_id}/references",
    response_model=list[ConversationReferenceRead],
    summary="List structured message references",
)
async def list_references(
    thread_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: ConversationCollaborationService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[ConversationReferenceRead]:
    try:
        rows = await service.list_references(
            workspace_id,
            thread_id,
            auth.user_id,
            limit=limit,
        )
    except LookupError as error:
        raise _http_error(error) from error
    return [_reference_read(reference, mentions) for reference, mentions in rows]


@router.post(
    "/threads/{thread_id}/mentions",
    response_model=list[ConversationMentionRead],
    status_code=status.HTTP_201_CREATED,
    summary="Attach standalone mentions to a canonical message",
)
async def attach_mentions(
    thread_id: UUID,
    payload: ConversationMessageMentionsCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: ConversationCollaborationService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> list[ConversationMentionRead]:
    try:
        result = await service.attach_mentions(
            workspace_id,
            thread_id,
            auth.user_id,
            payload,
        )
    except (ConversationCollaborationConflictError, LookupError, ValueError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    return [_mention_read(mention) for mention in result.mentions]


@router.get(
    "/threads/{thread_id}/messages/{source_message_id}/mentions",
    response_model=list[ConversationMentionRead],
    summary="List all resolved mentions attached to a canonical message",
)
async def list_message_mentions(
    thread_id: UUID,
    source_message_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: ConversationCollaborationService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[ConversationMentionRead]:
    try:
        mentions = await service.list_message_mentions(
            workspace_id,
            thread_id,
            source_message_id,
            auth.user_id,
        )
    except LookupError as error:
        raise _http_error(error) from error
    return [_mention_read(mention) for mention in mentions]


@router.post(
    "/threads/{thread_id}/participants",
    response_model=ConversationParticipantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a resolved participant to the native thread group",
)
async def create_participant(
    thread_id: UUID,
    payload: ConversationParticipantCreate,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: ConversationCollaborationService = Depends(_service),
    _: AuthContext = Depends(require_permission("users", "manage")),
) -> ConversationParticipantRead:
    try:
        result = await service.create_participant(
            workspace_id,
            thread_id,
            auth.user_id,
            payload,
        )
    except (ConversationCollaborationConflictError, LookupError, ValueError) as error:
        raise _http_error(error) from error
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    return _participant_read(result.participant)


@router.get(
    "/threads/{thread_id}/participants",
    response_model=ConversationParticipantCollectionRead,
    summary="List the native thread group and its participants",
)
async def list_participants(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: ConversationCollaborationService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> ConversationParticipantCollectionRead:
    try:
        group, participants = await service.list_participants(
            workspace_id,
            thread_id,
            auth.user_id,
        )
    except LookupError as error:
        raise _http_error(error) from error
    return ConversationParticipantCollectionRead(
        group=_group_read(group) if group else None,
        participants=[_participant_read(item) for item in participants],
    )


@router.get(
    "/threads/{thread_id}/capabilities",
    response_model=ConversationCapabilitiesRead,
    summary="Get the gated native and provider collaboration matrix",
)
async def get_capabilities(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: ConversationCollaborationService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> ConversationCapabilitiesRead:
    try:
        result: dict[str, Any] = await service.capabilities(
            workspace_id,
            thread_id,
            auth.user_id,
        )
    except LookupError as error:
        raise _http_error(error) from error
    return ConversationCapabilitiesRead.model_validate(result)


__all__ = ["router"]
