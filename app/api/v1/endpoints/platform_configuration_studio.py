"""Estúdio assistido exclusivo da equipe do SaaS."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_permission
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_studio_draft,
)
from app.models.support_access import SupportAccessScope
from app.models.user import User
from app.schemas.platform_configuration_studio import (
    StudioAssistRequest,
    StudioDraftCreate,
    StudioDraftPageRead,
    StudioDraftRead,
    StudioDraftUpdate,
    StudioProposalRead,
    StudioPublishRequest,
    StudioPublishResult,
    StudioRestoreRequest,
    StudioVersionPageRead,
    StudioVersionRead,
    StudioWorkspaceOptionRead,
)
from app.services.configuration_service import ConfigConflictError, ConfigValidationError
from app.services.platform_configuration_studio_service import (
    PlatformConfigurationStudioService,
)
from app.services.support_access_service import SupportAccessGrantService

router = APIRouter(
    prefix="/platform/configuration-studio",
    tags=["Blackbox Configuration Studio"],
)

_view = require_platform_permission("workspace_configuration", "view")
_manage = require_platform_permission("workspace_configuration", "manage")
_publish = require_platform_permission("workspace_configuration", "approve")


def _raise_studio_error(error: Exception) -> None:
    if isinstance(error, LookupError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "STUDIO-NOT-FOUND", "message": str(error)},
        ) from error
    if isinstance(error, ConfigConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "STUDIO-CONFLICT", "message": str(error)},
        ) from error
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "STUDIO-INVALID", "message": str(error)},
    ) from error


@router.get(
    "/workspaces",
    response_model=list[StudioWorkspaceOptionRead],
    deprecated=True,
)
async def list_configuration_workspaces(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> list[StudioWorkspaceOptionRead]:
    rows = await SupportAccessGrantService(db).list_configuration_workspaces(
        actor_user_id=actor.id,
    )
    return [StudioWorkspaceOptionRead.model_validate(row) for row in rows]


@router.get("/workspaces/page")
async def page_configuration_workspaces(
    search: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> dict:
    rows, total = await SupportAccessGrantService(db).page_configuration_workspaces(
        actor_user_id=actor.id,
        search=search,
        limit=limit,
        offset=offset,
    )
    items = [StudioWorkspaceOptionRead.model_validate(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(items) < total,
    }


@router.get(
    "/workspaces/{workspace_id}/access",
    response_model=StudioWorkspaceOptionRead,
)
async def get_configuration_workspace_access(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> StudioWorkspaceOptionRead:
    row = await SupportAccessGrantService(db).get_configuration_workspace(
        actor_user_id=actor.id,
        workspace_id=workspace_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "STUDIO-WORKSPACE-ACCESS-NOT-FOUND",
                "message": "A conta não está disponível no escopo atual do Estúdio.",
            },
        )
    return StudioWorkspaceOptionRead.model_validate(row)


@router.post(
    "/workspaces/{workspace_id}/assist",
    response_model=StudioProposalRead,
)
async def assist_configuration(
    workspace_id: UUID,
    data: StudioAssistRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_manage),
) -> StudioProposalRead:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_write,
            request=request,
        )
        return await PlatformConfigurationStudioService(db).assist(
            workspace_id=workspace_id,
            request=data,
        )
    except (LookupError, ConfigValidationError, ValueError) as error:
        _raise_studio_error(error)


@router.get(
    "/workspaces/{workspace_id}/drafts",
    response_model=list[StudioDraftRead],
    deprecated=True,
)
async def list_configuration_drafts(
    workspace_id: UUID,
    request: Request,
    kind: Literal["agent", "agent_package", "flow", "experience"] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> list[StudioDraftRead]:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_read,
            request=request,
        )
        return await PlatformConfigurationStudioService(db).list_drafts(
            workspace_id=workspace_id,
            kind=kind,
        )
    except (LookupError, ConfigValidationError) as error:
        _raise_studio_error(error)


@router.get(
    "/workspaces/{workspace_id}/drafts/page",
    response_model=StudioDraftPageRead,
)
async def page_configuration_drafts(
    workspace_id: UUID,
    request: Request,
    kind: Literal["agent", "agent_package", "flow", "experience"] | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> StudioDraftPageRead:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_read,
            request=request,
        )
        return await PlatformConfigurationStudioService(db).page_drafts(
            workspace_id=workspace_id,
            kind=kind,
            limit=limit,
            offset=offset,
        )
    except (LookupError, ConfigValidationError) as error:
        _raise_studio_error(error)


@router.post(
    "/workspaces/{workspace_id}/drafts",
    response_model=StudioDraftRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_configuration_draft(
    workspace_id: UUID,
    data: StudioDraftCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> StudioDraftRead:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_write,
            request=request,
            administrative_session_type=context.administrative_session_type,
            administrative_session_id=context.administrative_session_id,
            require_root_direct=True,
        )
        result = await PlatformConfigurationStudioService(db).create_draft(
            workspace_id=workspace_id,
            actor_user_id=actor.id,
            data=data,
        )
        after = await snapshot_platform_studio_draft(db, workspace_id, result.id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=None,
            after_snapshot=after,
            module="workspace_configuration",
            action="create_configuration_draft",
            reason=data.reason or "Rascunho criado no Estúdio Blackbox.",
            reason_source="user" if data.reason else "system",
        )
        return result
    except (LookupError, ConfigValidationError, ValueError) as error:
        await db.rollback()
        _raise_studio_error(error)


@router.put(
    "/workspaces/{workspace_id}/drafts/{document_id}",
    response_model=StudioDraftRead,
)
async def update_configuration_draft(
    workspace_id: UUID,
    document_id: UUID,
    data: StudioDraftUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> StudioDraftRead:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_write,
            request=request,
            administrative_session_type=context.administrative_session_type,
            administrative_session_id=context.administrative_session_id,
            require_root_direct=True,
        )
        before = await snapshot_platform_studio_draft(
            db, workspace_id, document_id, for_update=True
        )
        result = await PlatformConfigurationStudioService(db).update_draft(
            workspace_id=workspace_id,
            document_id=document_id,
            actor_user_id=actor.id,
            data=data,
        )
        after = await snapshot_platform_studio_draft(db, workspace_id, document_id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="workspace_configuration",
            action="update_configuration_draft",
            reason=data.reason or "Rascunho atualizado no Estúdio Blackbox.",
            reason_source="user" if data.reason else "system",
        )
        return result
    except (LookupError, ConfigConflictError, ConfigValidationError, ValueError) as error:
        await db.rollback()
        _raise_studio_error(error)


@router.get(
    "/workspaces/{workspace_id}/drafts/{document_id}/versions",
    response_model=list[StudioVersionRead],
    deprecated=True,
)
async def list_configuration_draft_versions(
    workspace_id: UUID,
    document_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> list[StudioVersionRead]:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_read,
            request=request,
        )
        return await PlatformConfigurationStudioService(db).list_versions(
            workspace_id=workspace_id,
            document_id=document_id,
        )
    except (LookupError, ConfigValidationError) as error:
        _raise_studio_error(error)


@router.get(
    "/workspaces/{workspace_id}/drafts/{document_id}/versions/page",
    response_model=StudioVersionPageRead,
)
async def page_configuration_draft_versions(
    workspace_id: UUID,
    document_id: UUID,
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_view),
) -> StudioVersionPageRead:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_read,
            request=request,
        )
        return await PlatformConfigurationStudioService(db).page_versions(
            workspace_id=workspace_id,
            document_id=document_id,
            limit=limit,
            offset=offset,
        )
    except (LookupError, ConfigValidationError) as error:
        _raise_studio_error(error)


@router.post(
    "/workspaces/{workspace_id}/drafts/{document_id}/restore",
    response_model=StudioDraftRead,
)
async def restore_configuration_draft(
    workspace_id: UUID,
    document_id: UUID,
    data: StudioRestoreRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_publish),
    context: AuthContext = Depends(get_current_auth_context),
) -> StudioDraftRead:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_write,
            request=request,
            administrative_session_type=context.administrative_session_type,
            administrative_session_id=context.administrative_session_id,
            require_root_direct=True,
        )
        before = await snapshot_platform_studio_draft(
            db, workspace_id, document_id, for_update=True
        )
        result = await PlatformConfigurationStudioService(db).restore_draft(
            workspace_id=workspace_id,
            document_id=document_id,
            actor_user_id=actor.id,
            target_version=data.target_version,
            expected_etag=data.expected_etag,
            reason=data.reason,
        )
        after = await snapshot_platform_studio_draft(db, workspace_id, document_id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="workspace_configuration",
            action="restore_configuration_draft",
            reason=data.reason,
        )
        return result
    except (LookupError, ConfigConflictError, ConfigValidationError, ValueError) as error:
        await db.rollback()
        _raise_studio_error(error)


@router.post(
    "/workspaces/{workspace_id}/drafts/{document_id}/publish",
    response_model=StudioPublishResult,
)
async def publish_configuration_draft(
    workspace_id: UUID,
    document_id: UUID,
    data: StudioPublishRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_publish),
    context: AuthContext = Depends(get_current_auth_context),
) -> StudioPublishResult:
    try:
        await SupportAccessGrantService(db).authorize_platform_workspace_operation(
            actor_user_id=actor.id,
            workspace_id=workspace_id,
            required_scope=SupportAccessScope.configurations_write,
            request=request,
            administrative_session_type=context.administrative_session_type,
            administrative_session_id=context.administrative_session_id,
            require_root_direct=True,
        )
        before = await snapshot_platform_studio_draft(
            db, workspace_id, document_id, for_update=True
        )
        result = await PlatformConfigurationStudioService(db).publish(
            workspace_id=workspace_id,
            document_id=document_id,
            actor_user_id=actor.id,
            expected_etag=data.expected_etag,
            reason=data.reason,
        )
        after = await snapshot_platform_studio_draft(db, workspace_id, document_id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="workspace_configuration",
            action="publish_configuration_draft",
            reason=data.reason or "Configuração publicada pelo Estúdio Blackbox.",
            reason_source="user" if data.reason else "system",
        )
        return result
    except (LookupError, ConfigConflictError, ConfigValidationError, ValueError) as error:
        await db.rollback()
        _raise_studio_error(error)
