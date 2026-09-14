"""
Dependências injetáveis do FastAPI.
Usadas nos endpoints via Depends().

Códigos de erro de autenticação:
  AUTH-001  Token ausente
  AUTH-002  Token inválido ou expirado
"""
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.identity_surface import (
    has_active_platform_access,
    is_platform_identity,
)
from app.core.rbac import (
    UNRESTRICTED_PERMISSION_SCOPES,
    Permission,
    get_effective_permissions,
)
from app.core.security import parse_session_token_id, verify_token
from app.core.token_store import is_token_revoked
from app.db.session import AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.models.user import AgentType, User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.subscription_access import (
    SubscriptionAccessDecision,
    SubscriptionAccessOperation,
)
from app.services.subscription_access_policy import (
    SubscriptionAccessDeniedError,
    SubscriptionAccessPolicy,
)
from app.services.support_access_service import (
    SupportAccessGrantService,
    required_support_scope_for_request,
)

bearer_scheme = HTTPBearer(auto_error=False)

SUBSCRIPTION_ACCESS_DENIED_CODE = "SUBSCRIPTION_ACCESS_DENIED"
SUBSCRIPTION_ACCESS_DENIED_MESSAGE = (
    "A assinatura do workspace não permite esta operação."
)
SUBSCRIPTION_ACCESS_LOCAL_COMPAT_REASON = "subscription_legacy_local_compatibility"

_SUBSCRIPTION_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SUBSCRIPTION_ACCESS_EXEMPT_ROUTES = {
    ("POST", "/api/v1/auth/select-workspace"): "workspace_selection",
    ("GET", "/api/v1/admin/workspaces"): "workspace_selection",
    ("POST", "/api/v1/klm/local/session"): "workspace_selection",
    ("GET", "/api/v1/klm/local/source-binding"): "workspace_selection",
    ("POST", "/api/v1/workspaces/setup"): "workspace_selection",
    ("GET", "/api/v1/workspaces/plan-summary"): "billing",
    ("GET", "/api/v1/workspaces/capability-manifest"): "billing",
    ("GET", "/api/v1/documents/{document_id}/download-url"): "export",
    ("GET", "/api/v1/local-storage/objects/{object_id}/export"): "export",
    (
        "POST",
        "/api/v1/local-storage/objects/{object_id}/download-ticket",
    ): "export",
    ("GET", "/api/v1/reports/export"): "export",
    ("POST", "/api/v1/reports/export"): "export",
    ("GET", "/api/v1/workspaces/export"): "export",
    ("POST", "/api/v1/workspaces/export"): "export",
    ("POST", "/api/v1/workspaces/closure"): "closure",
    ("DELETE", "/api/v1/workspaces/closure"): "closure",
}
_SUBSCRIPTION_ACCESS_EXEMPT_PREFIXES = (
    ("/api/v1/auth", "auth"),
    ("/api/v1/billing", "billing"),
    ("/api/v1/local-billing", "billing"),
    ("/api/v1/exports", "export"),
    ("/api/v1/workspaces/exports", "export"),
    ("/api/v1/workspaces/closure", "closure"),
)


def _allows_platform_first_access_mfa(request: Request, user: User) -> bool:
    """Keep provisional Blackbox sessions limited to MFA enrollment and rotation."""
    if not is_platform_identity(user):
        return False
    path = request.url.path
    if request.method == "GET" and path == "/api/v1/auth/mfa":
        return True
    if request.method != "POST":
        return False
    return path == "/api/v1/auth/mfa/enroll" or (
        path.startswith("/api/v1/auth/mfa/enroll/") and path.endswith("/verify")
    )


@dataclass
class AuthContext:
    user_id: UUID
    workspace_id: UUID | None
    membership_id: UUID | None
    role: WorkspaceRole | None
    is_internal_staff: bool
    surface: str = "workspace"
    token_id: str | None = None
    expires_at: int | None = None
    token_type: str = "access"
    credential_source: str = "bearer"
    session_issued_at_ns: int | None = None
    # ``session_id`` is always the normal UserSession family carried by an
    # access/refresh token.  Administrative audit foreign keys must never use
    # it: those point to the separate MFA-backed ``administrative_sessions``
    # table.
    session_id: UUID | None = None
    administrative_session_id: UUID | None = None
    administrative_session_type: Literal[
        "normal", "root_direct", "support_approved"
    ] = "normal"
    auth_epoch: int = 0
    support_grant_id: UUID | None = None
    support_scopes: frozenset[str] = field(default_factory=frozenset)
    support_request_scope: str | None = None

    @property
    def is_support_access(self) -> bool:
        return self.token_type == "support_access" and self.support_grant_id is not None


def _session_issued_at_ns(payload: dict) -> int | None:
    parsed = parse_session_token_id(payload.get("jti"))
    return parsed[1] if parsed else None


def _revoked_credential() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "AUTH-009", "message": "Credencial revogada. Faca login novamente."},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _require_current_auth_epoch(
    db: AsyncSession,
    payload: dict,
    *,
    require_verified_email: bool,
) -> tuple[UUID, int]:
    try:
        user_id = UUID(str(payload["sub"]))
        token_auth_epoch = int(payload["auth_epoch"])
    except (KeyError, TypeError, ValueError) as error:
        raise _revoked_credential() from error
    user = await db.scalar(
        select(User).where(
            User.id == user_id,
            User.agent_type == AgentType.human,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if (
        user is None
        or token_auth_epoch != user.auth_epoch
        or (require_verified_email and user.email_verified_at is None)
    ):
        raise _revoked_credential()
    return user_id, token_auth_epoch


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Fornece uma sessão assíncrona do banco de dados por request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_current_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """
    Extrai e valida o JWT do header Authorization.
    Retorna o UUID do usuário autenticado.

    Erros:
      AUTH-001 — Token ausente
      AUTH-002 — Token inválido ou expirado
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-001", "message": "Token de autenticação não fornecido."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(credentials.credentials)
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH-002",
                "message": "Token inválido ou expirado. Faça login novamente.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_type = payload.get("token_type", "access")
    if token_type not in {"access", "support_access"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH-002",
                "message": "Token inválido ou expirado. Faça login novamente.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    if token_type == "access" and await is_token_revoked(payload.get("jti")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-009", "message": "Token revogado. Faça login novamente."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token_type == "support_access":
        required_scope = required_support_scope_for_request(request)
        authorization = await SupportAccessGrantService(db).authorize_token(
            payload,
            required_scope=required_scope,
            request=request,
        )
        grant = authorization.grant
        return AuthContext(
            user_id=grant.requester_user_id,
            workspace_id=grant.workspace_id,
            membership_id=None,
            role=None,
            is_internal_staff=False,
            surface="support",
            token_id=payload.get("jti"),
            expires_at=payload.get("exp"),
            token_type="support_access",
            support_grant_id=grant.id,
            support_scopes=frozenset(grant.approved_scopes or []),
            support_request_scope=required_scope,
            administrative_session_id=authorization.administrative_session_id,
            administrative_session_type=authorization.administrative_session_type,
        )

    user_id, auth_epoch = await _require_current_auth_epoch(
        db,
        payload,
        require_verified_email=True,
    )
    current_user = await db.get(User, user_id)
    if current_user is None:
        raise _revoked_credential()
    if (
        current_user.password_change_required
        and request.url.path != "/api/v1/auth/password"
        and not _allows_platform_first_access_mfa(request, current_user)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-PASSWORD-CHANGE-REQUIRED",
                "message": "Troque a senha provisoria antes de continuar.",
            },
        )
    platform_identity = is_platform_identity(current_user)
    token_has_workspace = bool(
        payload.get("workspace_id")
        or payload.get("membership_id")
        or payload.get("role")
    )
    if platform_identity and (
        not has_active_platform_access(current_user)
        or token_has_workspace
    ):
        raise _revoked_credential()

    session_id = None
    if token_type == "access" and payload.get("session_id"):
        try:
            session_id = UUID(str(payload["session_id"]))
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH-002", "message": "Token de sessao invalido."},
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
        from app.services.auth_session_service import AuthSessionService

        await AuthSessionService(db).require_active(
            session_id,
            user_id,
        )

    return AuthContext(
        user_id=user_id,
        workspace_id=UUID(payload["workspace_id"]) if payload.get("workspace_id") else None,
        membership_id=UUID(payload["membership_id"]) if payload.get("membership_id") else None,
        role=WorkspaceRole(payload["role"]) if payload.get("role") else None,
        is_internal_staff=bool(payload.get("is_internal_staff", False)),
        surface="platform" if platform_identity else "workspace",
        token_id=payload.get("jti"),
        expires_at=payload.get("exp"),
        token_type=token_type,
        session_issued_at_ns=_session_issued_at_ns(payload),
        session_id=session_id,
        auth_epoch=auth_epoch,
    )


async def get_refresh_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    requested_audience: Literal["workspace", "platform"] | None = Header(
        default=None,
        alias="X-Auth-Audience",
    ),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    workspace_cookie = request.cookies.get(settings.AUTH_REFRESH_COOKIE_NAME)
    platform_cookie = request.cookies.get(settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME)
    if requested_audience == "platform":
        cookie_token = platform_cookie
    elif requested_audience == "workspace":
        cookie_token = workspace_cookie
    else:
        cookie_token = workspace_cookie or platform_cookie
    token = cookie_token or (credentials.credentials if credentials else None)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-001", "message": "Refresh token não fornecido."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(token)
    if not payload or not payload.get("sub") or payload.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-002", "message": "Refresh token inválido ou expirado."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if await is_token_revoked(payload.get("jti")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-009", "message": "Token revogado. Faça login novamente."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id, auth_epoch = await _require_current_auth_epoch(
        db,
        payload,
        require_verified_email=True,
    )
    current_user = await db.get(User, user_id)
    if current_user is None:
        raise _revoked_credential()
    if current_user.password_change_required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-PASSWORD-CHANGE-REQUIRED",
                "message": "Troque a senha provisoria antes de continuar.",
            },
        )
    platform_identity = is_platform_identity(current_user)
    if (
        requested_audience == "platform" and not platform_identity
    ) or (
        requested_audience == "workspace" and platform_identity
    ):
        raise _revoked_credential()
    token_has_workspace = bool(
        payload.get("workspace_id")
        or payload.get("membership_id")
        or payload.get("role")
    )
    if platform_identity and (
        not has_active_platform_access(current_user)
        or token_has_workspace
    ):
        raise _revoked_credential()

    session_id = None
    if payload.get("session_id"):
        try:
            session_id = UUID(str(payload["session_id"]))
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH-002", "message": "Refresh token invalido ou expirado."},
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
        from app.services.auth_session_service import AuthSessionService

        await AuthSessionService(db).require_active(
            session_id,
            user_id,
            touch=False,
        )

    return AuthContext(
        user_id=user_id,
        workspace_id=UUID(payload["workspace_id"]) if payload.get("workspace_id") else None,
        membership_id=UUID(payload["membership_id"]) if payload.get("membership_id") else None,
        role=WorkspaceRole(payload["role"]) if payload.get("role") else None,
        is_internal_staff=bool(payload.get("is_internal_staff", False)),
        surface="platform" if platform_identity else "workspace",
        token_id=payload.get("jti"),
        expires_at=payload.get("exp"),
        token_type="refresh",
        credential_source="cookie" if cookie_token else "bearer",
        session_issued_at_ns=_session_issued_at_ns(payload),
        session_id=session_id,
        auth_epoch=auth_epoch,
    )


async def get_optional_access_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthContext | None:
    """Lê um access token válido sem bloquear operações idempotentes de limpeza."""
    if not credentials:
        return None
    payload = verify_token(credentials.credentials)
    if not payload or not payload.get("sub") or payload.get("token_type", "access") != "access":
        return None
    try:
        workspace_id = UUID(payload["workspace_id"]) if payload.get("workspace_id") else None
        membership_id = UUID(payload["membership_id"]) if payload.get("membership_id") else None
        is_internal_staff = bool(payload.get("is_internal_staff", False))
        return AuthContext(
            user_id=UUID(payload["sub"]),
            workspace_id=workspace_id,
            membership_id=membership_id,
            role=WorkspaceRole(payload["role"]) if payload.get("role") else None,
            is_internal_staff=is_internal_staff,
            surface=(
                "platform"
                if is_internal_staff and workspace_id is None and membership_id is None
                else "workspace"
            ),
            token_id=payload.get("jti"),
            expires_at=payload.get("exp"),
            token_type="access",
            session_issued_at_ns=_session_issued_at_ns(payload),
            session_id=UUID(str(payload["session_id"]))
            if payload.get("session_id")
            else None,
            auth_epoch=int(payload.get("auth_epoch", 0)),
        )
    except (TypeError, ValueError):
        return None


async def get_current_user_id(
    context: AuthContext = Depends(get_current_auth_context),
) -> UUID:
    return context.user_id


async def get_current_workspace_id(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    if context.surface == "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-SURFACE-MISMATCH",
                "message": "Identidades da Blackbox não acessam rotas de workspace.",
            },
        )
    if not context.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH-005", "message": "Selecione um workspace antes de continuar."},
        )

    if context.is_support_access:
        if (
            context.support_request_scope is None
            or context.support_request_scope not in context.support_scopes
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "SUPPORT-SCOPE-DENIED",
                    "message": "O grant de suporte não autoriza esta operação.",
                },
            )
        await set_workspace_context(db, context.workspace_id)
        return context.workspace_id

    # ── Cache: verificação de membership (lida em CADA request autenticado) ─
    # TTL 10min — invalidar quando membership for alterado
    from app.core.cache import cache_get, cache_set, key_workspace_membership
    cache_key = key_workspace_membership(context.workspace_id, context.user_id)
    cached = await cache_get(cache_key)
    if cached:
        await set_workspace_context(db, context.workspace_id)
        return context.workspace_id

    membership = await db.execute(
        select(WorkspaceMembership)
        .join(Workspace, WorkspaceMembership.workspace_id == Workspace.id)
        .where(
            WorkspaceMembership.workspace_id == context.workspace_id,
            WorkspaceMembership.user_id == context.user_id,
            WorkspaceMembership.status == MembershipStatus.active,
            Workspace.deleted_at.is_(None),
            Workspace.is_active.is_(True),
        )
    )
    if membership.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-010",
                "message": "O workspace ativo não está mais disponível para esta sessão.",
            },
        )

    # Salvar no cache por 10 minutos
    await cache_set(cache_key, {"valid": True}, ttl_seconds=600)
    await set_workspace_context(db, context.workspace_id)
    return context.workspace_id


def subscription_access_operation_for_method(
    method: str,
) -> SubscriptionAccessOperation:
    if method.upper() in _SUBSCRIPTION_READ_METHODS:
        return SubscriptionAccessOperation.read
    return SubscriptionAccessOperation.write


def _subscription_access_exemption(request: Request) -> str | None:
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    method = request.method.upper()
    exact_match = _SUBSCRIPTION_ACCESS_EXEMPT_ROUTES.get((method, route_path))
    if exact_match is not None:
        return exact_match
    for prefix, exemption in _SUBSCRIPTION_ACCESS_EXEMPT_PREFIXES:
        if route_path == prefix or route_path.startswith(f"{prefix}/"):
            return exemption
    return None


async def require_subscription_access(
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionAccessDecision | None:
    """Aplica a matriz read/write da assinatura às operações tenant."""
    if context.is_support_access or _subscription_access_exemption(request) is not None:
        return None

    membership = await get_active_membership(context, db)
    operation = subscription_access_operation_for_method(request.method)
    try:
        return await SubscriptionAccessPolicy(db).require(
            membership.workspace_id,
            operation,
        )
    except SubscriptionAccessDeniedError as error:
        decision = error.decision
        if (
            decision.reason_code == "subscription_missing"
            and settings.is_development
        ):
            return decision.model_copy(
                update={
                    "read_allowed": True,
                    "write_allowed": True,
                    "reason_code": SUBSCRIPTION_ACCESS_LOCAL_COMPAT_REASON,
                }
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": SUBSCRIPTION_ACCESS_DENIED_CODE,
                "message": SUBSCRIPTION_ACCESS_DENIED_MESSAGE,
                "operation": operation.value,
                "state": decision.state.value if decision.state is not None else None,
                "reasonCode": decision.reason_code,
            },
        ) from error


async def require_workspace_admin(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if context.is_support_access:
        if context.support_request_scope in context.support_scopes:
            return context
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPPORT-SCOPE-DENIED",
                "message": "O grant de suporte não autoriza esta operação.",
            },
        )
    membership = await get_active_membership(context, db)
    if membership.role not in {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-007",
                "message": "Permissão insuficiente para administrar este workspace.",
            },
        )
    return context


def require_finance_role_access(module: str = "finance") -> Callable[..., Any]:
    """Papel financeiro (owner/admin/finance_manager) E permissão efetiva de leitura
    do módulo. S23: só o papel era checado, então uma negação individual do
    módulo (`permission_denials`) continuava abrindo 96 rotas."""

    async def dependency(
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> AuthContext:
        if context.is_support_access:
            if context.support_request_scope in context.support_scopes:
                return context
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "SUPPORT-SCOPE-DENIED",
                    "message": "O grant de suporte não autoriza esta operação.",
                },
            )
        membership = await get_active_membership(context, db)
        if membership.role not in {
            WorkspaceRole.workspace_owner,
            WorkspaceRole.workspace_admin,
            WorkspaceRole.finance_manager,
        }:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTH-008",
                    "message": "Permissão insuficiente para operar o módulo financeiro.",
                },
            )
        permissions = get_effective_permissions(
            membership.role,
            membership.permission_grants,
            membership.permission_denials,
        )
        if not any(
            permission.module == module
            and permission.action == "view"
            and permission.scope in UNRESTRICTED_PERMISSION_SCOPES
            for permission in permissions
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTH-012",
                    "message": "Acesso a este módulo foi negado para o seu usuário.",
                },
            )
        return context

    return dependency


require_finance_access = require_finance_role_access("finance")
require_integrations_access = require_finance_role_access("integrations")


# Alias para consistência de nomenclatura nos endpoints
get_async_db = get_db


async def get_optional_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UUID | None:
    """Versão opcional — não lança erro se não houver token."""
    if not credentials:
        return None
    payload = verify_token(credentials.credentials)
    return (
        UUID(payload["sub"])
        if payload and payload.get("sub") and payload.get("token_type", "access") == "access"
        else None
    )


async def _authorize_effective_permissions(
    context: AuthContext,
    db: AsyncSession,
    candidates: tuple[tuple[str, str], ...],
) -> AuthContext:
    """Fator comum de `require_permission` e `require_any_permission`: ramo de
    suporte, sessão sem papel, permissões efetivas (papel + grants + denials da
    membership) e escopo irrestrito. Passa se QUALQUER candidato estiver
    concedido."""
    if context.is_support_access:
        if context.support_request_scope in context.support_scopes:
            return context
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPPORT-SCOPE-DENIED",
                "message": "O grant de suporte não autoriza esta operação.",
            },
        )
    if context.role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-011",
                "message": "Sessão sem papel válido para este workspace.",
            },
        )
    membership = await get_active_membership(context, db)
    permissions = get_effective_permissions(
        membership.role,
        membership.permission_grants,
        membership.permission_denials,
    )
    if not any(
        (permission.module, permission.action) in candidates
        and permission.scope in UNRESTRICTED_PERMISSION_SCOPES
        for permission in permissions
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH-012", "message": "Permissão insuficiente para esta ação."},
        )
    return context


async def has_effective_permission(
    context: AuthContext,
    db: AsyncSession,
    module: str,
    action: str,
) -> bool:
    """Versão booleana de `_authorize_effective_permissions`, para redigir campos
    de uma resposta que o chamador pode ler mas não inteira (V22: `verifyToken`
    do WhatsApp só para quem tem `integrations:view`)."""
    try:
        await _authorize_effective_permissions(context, db, ((module, action),))
    except HTTPException:
        return False
    return True


def require_permission(module: str, action: str = "view") -> Callable[[AuthContext], AuthContext]:
    async def dependency(
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> AuthContext:
        return await _authorize_effective_permissions(context, db, ((module, action),))
    return dependency


def require_any_permission(
    *candidates: tuple[str, str],
) -> Callable[[AuthContext], AuthContext]:
    """V22: leitura que serve a mais de um módulo (ex.: listar conexões do WhatsApp
    para quem envia mensagens) aceita qualquer uma das permissões, sem mudar o
    desenho dos papéis."""
    if not candidates:
        raise ValueError("require_any_permission exige ao menos um par (módulo, ação).")

    async def dependency(
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> AuthContext:
        return await _authorize_effective_permissions(context, db, tuple(candidates))
    return dependency


async def get_active_membership(
    context: AuthContext,
    db: AsyncSession,
) -> WorkspaceMembership:
    if context.surface == "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH-SURFACE-MISMATCH",
                "message": "Identidades da Blackbox não possuem membership operacional.",
            },
        )
    if context.is_support_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPPORT-MEMBERSHIP-REQUIRED",
                "message": "Esta operação exige membership normal do workspace.",
            },
        )
    if not context.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH-005", "message": "Selecione um workspace antes de continuar."},
        )
    conditions = [
        WorkspaceMembership.workspace_id == context.workspace_id,
        WorkspaceMembership.user_id == context.user_id,
        WorkspaceMembership.status == MembershipStatus.active,
    ]
    if context.membership_id:
        conditions.append(WorkspaceMembership.id == context.membership_id)
    membership = await db.scalar(select(WorkspaceMembership).where(*conditions))
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH-010", "message": "O acesso ao workspace não está mais ativo."},
        )
    await set_workspace_context(db, context.workspace_id)
    return membership


async def get_current_permissions(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[Permission]:
    if context.is_support_access:
        return []
    membership = await get_active_membership(context, db)
    return get_effective_permissions(
        membership.role,
        membership.permission_grants,
        membership.permission_denials,
    )


async def can_view_sensitive_lead_data(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> bool:
    if context.is_support_access:
        return "lead_data:read" in context.support_scopes
    permissions = await get_current_permissions(context, db)
    return any(
        permission.module == "lead_data" and permission.action == "view"
        for permission in permissions
    )
