"""Typed, tenant-scoped API for local-ready Meta Cloud API groups."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.schemas.meta_groups import (
    MetaGroupCapabilityConfigure,
    MetaGroupCapabilityGateRead,
    MetaGroupCapabilityRead,
    MetaGroupCollectionRead,
    MetaGroupCreate,
    MetaGroupCreateRead,
    MetaGroupInviteLinkRotateRead,
    MetaGroupLifecycleEventRead,
    MetaGroupLifecycleIngest,
    MetaGroupLifecycleIngestRead,
    MetaGroupMessageCollectionRead,
    MetaGroupMessagePreparationRead,
    MetaGroupMessagePrepare,
    MetaGroupOfficialCreationPreparationRead,
    MetaGroupOfficialCreationPrepare,
    MetaGroupParticipantCollectionRead,
    MetaGroupParticipantRemoveRead,
    MetaGroupPinnedMessageSet,
    MetaGroupRead,
)
from app.services.meta_groups_service import (
    MetaGroupConflictError,
    MetaGroupDomainError,
    MetaGroupNotFoundError,
    MetaGroupService,
    MetaGroupValidationError,
)

router = APIRouter(
    prefix="/klm/omnichannel/meta-groups",
    tags=["KLM Meta Cloud Groups"],
)


def _service(db: AsyncSession = Depends(get_db)) -> MetaGroupService:
    return MetaGroupService(db)


def _raise_http(error: MetaGroupDomainError) -> None:
    if isinstance(error, MetaGroupNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
        code = "META-GROUPS-NOT-FOUND"
    elif isinstance(error, MetaGroupConflictError):
        status_code = status.HTTP_409_CONFLICT
        code = "META-GROUPS-CONFLICT"
    elif isinstance(error, MetaGroupValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        code = "META-GROUPS-INVALID"
    else:  # pragma: no cover - public domain errors are mapped above
        status_code = status.HTTP_400_BAD_REQUEST
        code = "META-GROUPS-ERROR"
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(error)},
    ) from error


@router.get(
    "/connections/{connection_id}/capability",
    response_model=MetaGroupCapabilityRead,
    summary="Read developer-app and external OBA Groups API gates",
)
async def get_meta_group_capability(
    connection_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> MetaGroupCapabilityRead:
    try:
        return await service.get_capability(workspace_id, connection_id)
    except MetaGroupDomainError as error:
        _raise_http(error)


@router.get(
    "/connections/{connection_id}/gate",
    response_model=MetaGroupCapabilityGateRead,
    summary="Consume the service-level Meta Groups capability decision",
)
async def get_meta_group_gate(
    connection_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> MetaGroupCapabilityGateRead:
    try:
        return await service.capability_gate(workspace_id, connection_id)
    except MetaGroupDomainError as error:
        _raise_http(error)


@router.put(
    "/connections/{connection_id}/capability",
    response_model=MetaGroupCapabilityRead,
    summary="Record evidence without claiming OBA access from app readiness",
)
async def configure_meta_group_capability(
    connection_id: UUID,
    payload: MetaGroupCapabilityConfigure,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("users", "manage")),
) -> MetaGroupCapabilityRead:
    try:
        result = await service.configure_capability(
            workspace_id=workspace_id,
            connection_id=connection_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    response.headers["X-KLM-External-Dispatch"] = "false"
    return result


@router.post(
    "/connections/{connection_id}/groups",
    response_model=MetaGroupCreateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an audited local internal group without Meta dispatch",
)
async def create_local_meta_group(
    connection_id: UUID,
    payload: MetaGroupCreate,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> MetaGroupCreateRead:
    try:
        result = await service.create_local_group(
            workspace_id=workspace_id,
            connection_id=connection_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    response.headers["X-KLM-External-Dispatch"] = "false"
    return result


@router.post(
    "/connections/{connection_id}/official-groups/prepare",
    response_model=MetaGroupOfficialCreationPreparationRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Prepare a gated official group creation without dispatch or projection",
)
async def prepare_official_meta_group_creation(
    connection_id: UUID,
    payload: MetaGroupOfficialCreationPrepare,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> MetaGroupOfficialCreationPreparationRead:
    try:
        result = await service.prepare_official_group_creation(
            workspace_id=workspace_id,
            connection_id=connection_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    response.headers["X-KLM-External-Dispatch"] = "false"
    return result


@router.get(
    "/groups",
    response_model=MetaGroupCollectionRead,
    summary="List local internal and official Meta group projections",
)
async def list_meta_groups(
    connection_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> MetaGroupCollectionRead:
    try:
        return await service.list_groups(
            workspace_id=workspace_id,
            connection_id=connection_id,
            limit=limit,
            offset=offset,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)


@router.get(
    "/groups/{group_id}",
    response_model=MetaGroupRead,
    summary="Read one local or official Meta group projection",
)
async def get_meta_group(
    group_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> MetaGroupRead:
    try:
        return await service.get_group(workspace_id, group_id)
    except MetaGroupDomainError as error:
        _raise_http(error)


@router.post(
    "/groups/{group_id}/messages/prepare",
    response_model=MetaGroupMessagePreparationRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Prepare and audit a strict group payload without dispatch",
)
async def prepare_meta_group_message(
    group_id: UUID,
    payload: MetaGroupMessagePrepare,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> MetaGroupMessagePreparationRead:
    try:
        result = await service.prepare_group_message(
            workspace_id=workspace_id,
            group_id=group_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    response.headers["X-KLM-External-Dispatch"] = "false"
    return result


@router.post(
    "/connections/{connection_id}/lifecycle-events",
    response_model=MetaGroupLifecycleIngestRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record a normalized, evidenced lifecycle update (not a public webhook boundary)",
)
async def ingest_meta_group_lifecycle(
    connection_id: UUID,
    payload: MetaGroupLifecycleIngest,
    response: Response,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("users", "manage")),
) -> MetaGroupLifecycleIngestRead:
    try:
        result = await service.ingest_lifecycle_event(
            workspace_id=workspace_id,
            connection_id=connection_id,
            actor_user_id=auth.user_id,
            payload=payload,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    response.headers["X-KLM-External-Dispatch"] = "false"
    return result


@router.get(
    "/groups/{group_id}/lifecycle-events",
    response_model=list[MetaGroupLifecycleEventRead],
    summary="List append-only group lifecycle evidence",
)
async def list_meta_group_lifecycle_events(
    group_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> list[MetaGroupLifecycleEventRead]:
    try:
        return await service.list_lifecycle_events(
            workspace_id=workspace_id,
            group_id=group_id,
            limit=limit,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)


@router.get(
    "/groups/{group_id}/messages",
    response_model=MetaGroupMessageCollectionRead,
    summary="List locally prepared group messages (the group thread)",
)
async def list_meta_group_messages(
    group_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> MetaGroupMessageCollectionRead:
    try:
        return await service.list_group_messages(
            workspace_id=workspace_id,
            group_id=group_id,
            limit=limit,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)


@router.post(
    "/groups/{group_id}/invite-link/rotate",
    response_model=MetaGroupInviteLinkRotateRead,
    summary="Rotate the group invite link (local-simulated for internal groups, gated for official)",
)
async def rotate_meta_group_invite_link(
    group_id: UUID,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> MetaGroupInviteLinkRotateRead:
    try:
        result = await service.rotate_invite_link(
            workspace_id=workspace_id,
            group_id=group_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    response.headers["X-KLM-External-Dispatch"] = "false"
    return result


@router.get(
    "/groups/{group_id}/participants",
    response_model=MetaGroupParticipantCollectionRead,
    summary="List group participants (cap of 8 for official Meta groups)",
)
async def list_meta_group_participants(
    group_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> MetaGroupParticipantCollectionRead:
    try:
        return await service.list_participants(
            workspace_id=workspace_id,
            group_id=group_id,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)


@router.delete(
    "/groups/{group_id}/participants/{participant_id}",
    response_model=MetaGroupParticipantRemoveRead,
    summary="Remove a participant (executed locally; gated/prepared for official groups)",
)
async def remove_meta_group_participant(
    group_id: UUID,
    participant_id: UUID,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> MetaGroupParticipantRemoveRead:
    try:
        result = await service.remove_participant(
            workspace_id=workspace_id,
            group_id=group_id,
            participant_id=participant_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)
    response.headers["X-KLM-Deduplicated"] = str(result.deduplicated).lower()
    response.headers["X-KLM-External-Dispatch"] = "false"
    return result


@router.put(
    "/groups/{group_id}/pinned-message",
    response_model=MetaGroupRead,
    summary="Pin a message in the group (local audited action)",
)
async def set_meta_group_pinned_message(
    group_id: UUID,
    payload: MetaGroupPinnedMessageSet,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> MetaGroupRead:
    try:
        return await service.set_pinned_message(
            workspace_id=workspace_id,
            group_id=group_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)


@router.delete(
    "/groups/{group_id}/pinned-message",
    response_model=MetaGroupRead,
    summary="Clear the group's pinned message (local audited action)",
)
async def clear_meta_group_pinned_message(
    group_id: UUID,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=180,
    ),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    service: MetaGroupService = Depends(_service),
    _: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> MetaGroupRead:
    try:
        return await service.clear_pinned_message(
            workspace_id=workspace_id,
            group_id=group_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
        )
    except MetaGroupDomainError as error:
        _raise_http(error)


__all__ = ["router"]
