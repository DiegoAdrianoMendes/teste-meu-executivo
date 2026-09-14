"""Platform-admin side of the local support break-glass workflow."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_root
from app.core.platform_audit import record_canonical_platform_mutation
from app.core.platform_rbac import has_platform_permission
from app.models.support_access import SupportAccessGrant, SupportAccessState
from app.models.user import User
from app.schemas.support_access import (
    RootDirectAccessRequest,
    SupportAccessEventPageRead,
    SupportAccessEventRead,
    SupportAccessGrantRead,
    SupportAccessRequestCreate,
    SupportAccessTokenRead,
)
from app.services.support_access_service import SupportAccessGrantService

router = APIRouter(prefix="/platform/support-access", tags=["Platform Support Access"])


def _grant_snapshot(grant: SupportAccessGrant | SupportAccessGrantRead) -> tuple[str, str, dict]:
    def value(name: str):
        return getattr(grant, name)

    state = value("state")
    requested_scopes = value("requested_scopes")
    approved_scopes = value("approved_scopes")
    return (
        "support_access_grant",
        str(value("id")),
        {
            "workspaceId": str(value("workspace_id")),
            "requesterUserId": str(value("requester_user_id")),
            "ticketReference": value("ticket_reference"),
            "reasonPresent": bool(value("reason")),
            "requestedScopes": [getattr(scope, "value", scope) for scope in requested_scopes],
            "requestedDurationMinutes": value("requested_duration_minutes"),
            "state": getattr(state, "value", state),
            "approverUserId": (
                str(value("approver_user_id")) if value("approver_user_id") else None
            ),
            "approvedScopes": (
                [getattr(scope, "value", scope) for scope in approved_scopes]
                if approved_scopes
                else None
            ),
            "approvedDurationMinutes": value("approved_duration_minutes"),
            "activatedAt": value("activated_at"),
            "expiresAt": value("expires_at"),
            "version": value("version"),
        },
    )


@router.post(
    "/root-direct/{workspace_id}",
    response_model=SupportAccessTokenRead,
    summary="Entrar diretamente em uma conta como Super Admin",
)
async def activate_root_direct_access(
    workspace_id: UUID,
    request: Request,
    body: RootDirectAccessRequest = Body(...),
    administrative_session_token: Annotated[
        str | None,
        Header(alias="X-Administrative-Session"),
    ] = None,
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupportAccessTokenRead:
    result = await SupportAccessGrantService(db).activate_root_direct(
        workspace_id,
        actor.id,
        administrative_session_token,
        reason=body.reason,
        request=request,
    )
    activated_grant = await db.get(SupportAccessGrant, result.grant.id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=_grant_snapshot(result.grant),
        module="support_access",
        action="support_access.root_direct",
        reason=body.reason,
        reason_source="user",
        session_type="root_direct",
        administrative_session_id=(
            activated_grant.activation_admin_session_id if activated_grant else None
        ),
    )
    return result


async def require_platform_support_requester(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    user = await db.get(User, context.user_id)
    if user is None or (not user.is_platform_admin and not user.is_internal_staff):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPPORT-PLATFORM-ADMIN-REQUIRED",
                "message": "A operação exige uma identidade interna da plataforma.",
            },
        )
    if not has_platform_permission(user, "support_access", "request"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PLATFORM-PERMISSION-DENIED",
                "message": "Seu pacote de acesso não autoriza esta operação.",
                "module": "support_access",
                "action": "request",
            },
        )
    if context.token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPPORT-NORMAL-SESSION-REQUIRED",
                "message": "Use a sessão normal da equipe do SaaS.",
            },
        )
    return context


@router.post(
    "/requests",
    response_model=SupportAccessGrantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar acesso temporário de suporte",
)
async def request_support_access(
    payload: SupportAccessRequestCreate,
    request: Request,
    context: AuthContext = Depends(require_platform_support_requester),
    db: AsyncSession = Depends(get_db),
) -> SupportAccessGrantRead:
    service = SupportAccessGrantService(db)
    grant = await service.request_access(context.user_id, payload)
    if not service.operation_was_replayed:
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=None,
            after_snapshot=_grant_snapshot(grant),
            module="support_access",
            action="support_access.request",
            reason=payload.reason,
        )
    else:
        request.state.canonical_platform_audit_recorded = True
    return grant


@router.get(
    "/requests",
    response_model=list[SupportAccessGrantRead],
    summary="Listar solicitações de suporte",
)
async def list_platform_support_access(
    workspace_id: UUID | None = Query(default=None),
    state_filter: SupportAccessState | None = Query(default=None, alias="state"),
    limit: int = Query(default=100, ge=1, le=200),
    context: AuthContext = Depends(require_platform_support_requester),
    db: AsyncSession = Depends(get_db),
) -> list[SupportAccessGrantRead]:
    return await SupportAccessGrantService(db).list_for_platform(
        context.user_id,
        workspace_id=workspace_id,
        state=state_filter,
        limit=limit,
    )


@router.get(
    "/requests/page",
    summary="Paginar solicitações de suporte",
)
async def page_platform_support_access(
    workspace_id: UUID | None = Query(default=None),
    state_filter: SupportAccessState | None = Query(default=None, alias="state"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_platform_support_requester),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows, total = await SupportAccessGrantService(db).page_for_platform(
        context.user_id,
        workspace_id=workspace_id,
        state=state_filter,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [
            SupportAccessGrantRead.model_validate(row).model_dump(by_alias=True)
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(rows) < total,
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
async def list_platform_support_access_events(
    grant_id: UUID,
    context: AuthContext = Depends(require_platform_support_requester),
    db: AsyncSession = Depends(get_db),
) -> list[SupportAccessEventRead]:
    return await SupportAccessGrantService(db).list_events_for_platform(
        grant_id,
        context.user_id,
    )


@router.get(
    "/requests/{grant_id}/events/page",
    response_model=SupportAccessEventPageRead,
    summary="Paginar trilha imutável de uma solicitação",
)
async def page_platform_support_access_events(
    grant_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_platform_support_requester),
    db: AsyncSession = Depends(get_db),
) -> SupportAccessEventPageRead:
    return await SupportAccessGrantService(db).page_events_for_platform(
        grant_id,
        context.user_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/requests/{grant_id}/activate",
    response_model=SupportAccessTokenRead,
    summary="Ativar grant aprovado após step-up MFA",
)
async def activate_support_access(
    grant_id: UUID,
    request: Request,
    administrative_session_token: Annotated[
        str,
        Header(alias="X-Administrative-Session", min_length=1),
    ],
    context: AuthContext = Depends(require_platform_support_requester),
    db: AsyncSession = Depends(get_db),
) -> SupportAccessTokenRead:
    before_grant = await db.get(SupportAccessGrant, grant_id)
    before = _grant_snapshot(before_grant) if before_grant is not None else None
    result = await SupportAccessGrantService(db).activate(
        grant_id,
        context.user_id,
        administrative_session_token,
        request=request,
    )
    activated_grant = await db.get(SupportAccessGrant, grant_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=_grant_snapshot(result.grant),
        module="support_access",
        action="support_access.activate",
        reason="Acesso assistido aprovado ativado após step-up MFA.",
        reason_source="system",
        session_type="support_approved",
        administrative_session_id=(
            activated_grant.activation_admin_session_id if activated_grant else None
        ),
    )
    return result
