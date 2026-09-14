"""Workspace-owner/admin side of the local support break-glass workflow."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_active_membership,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
)
from app.models.support_access import SupportAccessState
from app.models.workspace_membership import WorkspaceRole
from app.schemas.support_access import (
    SupportAccessApproval,
    SupportAccessDecision,
    SupportAccessEventPageRead,
    SupportAccessEventRead,
    SupportAccessGrantRead,
)
from app.services.support_access_service import SupportAccessGrantService

router = APIRouter(prefix="/workspaces/support-access", tags=["Workspace Support Access"])


async def require_workspace_support_approver(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if context.token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPPORT-NORMAL-SESSION-REQUIRED",
                "message": "A aprovação exige uma sessão normal do workspace.",
            },
        )
    membership = await get_active_membership(context, db)
    if membership.role not in {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPPORT-WORKSPACE-APPROVER-REQUIRED",
                "message": "A operação exige owner ou admin ativo deste workspace.",
            },
        )
    return context


@router.get(
    "/requests",
    response_model=list[SupportAccessGrantRead],
    summary="Listar solicitações ligadas ao workspace ativo",
    deprecated=True,
)
async def list_workspace_support_access(
    state_filter: SupportAccessState | None = Query(default=None, alias="state"),
    limit: int = Query(default=100, ge=1, le=200),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_support_approver),
    db: AsyncSession = Depends(get_db),
) -> list[SupportAccessGrantRead]:
    return await SupportAccessGrantService(db).list_for_workspace(
        context.user_id,
        workspace_id,
        state=state_filter,
        limit=limit,
    )


@router.get(
    "/requests/page",
    summary="Paginar solicitações ligadas ao workspace ativo",
)
async def page_workspace_support_access(
    state_filter: SupportAccessState | None = Query(default=None, alias="state"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_support_approver),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows, total = await SupportAccessGrantService(db).page_for_workspace(
        context.user_id,
        workspace_id,
        state=state_filter,
        limit=limit,
        offset=offset,
    )
    items = [SupportAccessGrantRead.model_validate(row).model_dump(by_alias=True) for row in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(items) < total,
        "sort": [
            {"field": "requestedAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    }


@router.get(
    "/requests/{grant_id}/events",
    response_model=list[SupportAccessEventRead],
    summary="Listar trilha imutável de uma solicitação",
    deprecated=True,
)
async def list_workspace_support_access_events(
    grant_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_support_approver),
    db: AsyncSession = Depends(get_db),
) -> list[SupportAccessEventRead]:
    return await SupportAccessGrantService(db).list_events_for_workspace(
        grant_id,
        workspace_id,
        context.user_id,
    )


@router.get(
    "/requests/{grant_id}/events/page",
    response_model=SupportAccessEventPageRead,
    summary="Paginar trilha imutável de uma solicitação",
)
async def page_workspace_support_access_events(
    grant_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_support_approver),
    db: AsyncSession = Depends(get_db),
) -> SupportAccessEventPageRead:
    return await SupportAccessGrantService(db).page_events_for_workspace(
        grant_id,
        workspace_id,
        context.user_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/requests/{grant_id}/approve",
    response_model=SupportAccessGrantRead,
    summary="Aprovar e opcionalmente reduzir escopo e duração",
)
async def approve_support_access(
    grant_id: UUID,
    payload: SupportAccessApproval,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_support_approver),
    db: AsyncSession = Depends(get_db),
) -> SupportAccessGrantRead:
    return await SupportAccessGrantService(db).approve(
        grant_id,
        workspace_id,
        context.user_id,
        payload,
    )


@router.post(
    "/requests/{grant_id}/reject",
    response_model=SupportAccessGrantRead,
    summary="Rejeitar solicitação pendente",
)
async def reject_support_access(
    grant_id: UUID,
    payload: SupportAccessDecision,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_support_approver),
    db: AsyncSession = Depends(get_db),
) -> SupportAccessGrantRead:
    return await SupportAccessGrantService(db).reject(
        grant_id,
        workspace_id,
        context.user_id,
        payload,
    )


@router.post(
    "/requests/{grant_id}/revoke",
    response_model=SupportAccessGrantRead,
    summary="Revogar grant aprovado ou ativo",
)
async def revoke_support_access(
    grant_id: UUID,
    payload: SupportAccessDecision,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(require_workspace_support_approver),
    db: AsyncSession = Depends(get_db),
) -> SupportAccessGrantRead:
    return await SupportAccessGrantService(db).revoke(
        grant_id,
        workspace_id,
        context.user_id,
        payload,
    )
