"""
Endpoint: Auth
Autenticação de usuários via email/senha + JWT.

Códigos de erro:
  AUTH-001  Token ausente ou inválido
  AUTH-002  Token expirado
  AUTH-003  Credenciais incorretas (email ou senha)
  AUTH-004  Usuário inativo — contate o administrador
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    get_optional_access_auth_context,
    get_refresh_auth_context,
)
from app.core.security import create_websocket_ticket, verify_token
from app.core.token_store import (
    consume_token,
    is_user_session_revoked,
    revoke_token,
)
from app.models.auth_security import AuthRateLimitScope
from app.models.user import normalize_email
from app.schemas.auth_security import (
    AdministrativeSessionRead,
    AdministrativeSessionStatusRead,
    AdministrativeSessionTokenRead,
    AuthSecurityEventRead,
    MfaChallengeRead,
    MfaEnrollmentCompleteRead,
    MfaLoginChallengeRead,
    MfaStatusRead,
    MfaVerificationRequest,
)
from app.schemas.identity import (
    ForgotPasswordRequest,
    IdentityRequestAcceptedRead,
    InvitationAcceptedRead,
    InvitationAcceptRequest,
    InvitationPreviewRead,
    LocalOutboxMessageRead,
    MessageRead,
    PasswordChangeRequest,
    PublicIdentityRequestAcceptedRead,
    ResetPasswordRequest,
    SessionRevocationRead,
    SignupResendRequest,
    SignupVerificationRead,
    SignupVerificationRequest,
    UserSessionRead,
)
from app.schemas.user import LoginRequest, RegisterRequest, TokenResponse
from app.schemas.workspace import SessionContextRead, WebSocketTicketRead, WorkspaceSelectRequest
from app.services.auth_rate_limit_service import (
    REGISTER_ENUMERATION_FLOOR_SECONDS,
    PersistentAuthRateLimiter,
    enforce_uniform_timing,
    opaque_auth_digest,
    request_network_key,
    start_uniform_timing,
)
from app.services.auth_service import AuthService
from app.services.auth_session_service import AuthSessionService
from app.services.identity_lifecycle_service import IdentityLifecycleService
from app.services.identity_outbox_service import LocalIdentityOutboxService
from app.services.mfa_service import LocalMfaService
from app.websockets.manager import ws_manager

router = APIRouter(prefix="/auth", tags=["Autenticação"])
admin_bearer_scheme = HTTPBearer(auto_error=False)


def _set_refresh_cookie(
    response: Response,
    token_response: TokenResponse,
    *,
    expose_refresh_token: bool = False,
) -> TokenResponse:
    refresh_token = token_response.refresh_token
    if not refresh_token:
        return token_response
    cookie_name = (
        settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME
        if token_response.session.surface == "platform"
        else settings.AUTH_REFRESH_COOKIE_NAME
    )
    response.set_cookie(
        key=cookie_name,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        domain=settings.AUTH_REFRESH_COOKIE_DOMAIN,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.AUTH_REFRESH_COOKIE_SAMESITE,
    )
    if expose_refresh_token:
        return token_response
    return token_response.model_copy(update={"refresh_token": None})


def _clear_refresh_cookie(
    response: Response,
    *,
    audience: str = "workspace",
) -> None:
    response.delete_cookie(
        key=(
            settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME
            if audience == "platform"
            else settings.AUTH_REFRESH_COOKIE_NAME
        ),
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        domain=settings.AUTH_REFRESH_COOKIE_DOMAIN,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.AUTH_REFRESH_COOKIE_SAMESITE,
    )


def _auth_error_code(error: HTTPException) -> str | None:
    if not isinstance(error.detail, dict):
        return None
    code = error.detail.get("code")
    return code if isinstance(code, str) else None


def _invalid_login() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "AUTH-003", "message": "Email ou senha incorretos."},
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post(
    "/register",
    response_model=IdentityRequestAcceptedRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Solicitar cadastro local pendente de verificacao",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    started_at = start_uniform_timing()
    normalized_email = normalize_email(str(payload.email))
    try:
        limiter = PersistentAuthRateLimiter(db)
        decision = await limiter.check(
            AuthRateLimitScope.register,
            identifier=normalized_email,
            network=request_network_key(request),
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "AUTH-RATE-LIMITED",
                    "message": "Solicitacao temporariamente limitada.",
                },
                headers={"Retry-After": str(decision.retry_after)},
            )
        return await IdentityLifecycleService(db).request_signup(
            payload,
            idempotency_key=idempotency_key,
        )
    finally:
        await enforce_uniform_timing(
            started_at,
            AuthRateLimitScope.register,
            security_floor_seconds=REGISTER_ENUMERATION_FLOOR_SECONDS,
        )


@router.post(
    "/register/resend",
    response_model=PublicIdentityRequestAcceptedRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reenviar verificacao de cadastro sem enumerar identidades",
)
async def resend_registration_verification(
    payload: SignupResendRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    started_at = start_uniform_timing()
    normalized_email = normalize_email(str(payload.email))
    try:
        decision = await PersistentAuthRateLimiter(db).check(
            AuthRateLimitScope.register,
            identifier=normalized_email,
            network=request_network_key(request),
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "AUTH-RATE-LIMITED",
                    "message": "Solicitacao temporariamente limitada.",
                },
                headers={"Retry-After": str(decision.retry_after)},
            )
        return await IdentityLifecycleService(db).resend_signup_verification(
            payload,
            idempotency_key=idempotency_key,
        )
    finally:
        await enforce_uniform_timing(
            started_at,
            AuthRateLimitScope.register,
            security_floor_seconds=REGISTER_ENUMERATION_FLOOR_SECONDS,
        )


@router.post(
    "/register/verify",
    response_model=SignupVerificationRead,
    summary="Verificar cadastro e ativar workspace/trial",
)
async def verify_registration(
    payload: SignupVerificationRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    identifier = str(payload.delivery_id or payload.token or payload.code)
    decision = await PersistentAuthRateLimiter(db).check(
        AuthRateLimitScope.verify_signup,
        identifier=identifier,
        network=request_network_key(request),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "AUTH-RATE-LIMITED",
                "message": "Verificacao temporariamente limitada.",
            },
            headers={"Retry-After": str(decision.retry_after)},
        )
    result = await IdentityLifecycleService(db).verify_signup(payload, request=request)
    return _set_refresh_cookie(response, result)


@router.get(
    "/local-outbox/requests/{request_id}",
    response_model=LocalOutboxMessageRead,
    summary="Ler entrega de reset por request local privilegiado",
)
async def read_local_identity_outbox_request(
    request_id: UUID,
    request: Request,
    administrative_session: str | None = Header(
        default=None,
        alias="X-Administrative-Session",
    ),
    db: AsyncSession = Depends(get_db),
):
    return await LocalIdentityOutboxService(db).read_request_privileged(
        request_id,
        administrative_session_token=administrative_session or "",
        request=request,
    )


@router.get(
    "/local-outbox/{delivery_id}",
    response_model=LocalOutboxMessageRead,
    summary="Ler entrega simulada por capacidade local",
)
async def read_local_identity_outbox(
    delivery_id: UUID,
    request: Request,
    administrative_session: str | None = Header(
        default=None,
        alias="X-Administrative-Session",
    ),
    db: AsyncSession = Depends(get_db),
):
    return await LocalIdentityOutboxService(db).read_privileged(
        delivery_id,
        administrative_session_token=administrative_session or "",
        request=request,
    )


@router.post(
    "/login",
    response_model=TokenResponse | MfaLoginChallengeRead,
    summary="Login de usuário",
)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Autentica um usuário humano e retorna JWT + dados do usuário.

    O access token deve permanecer em memória/sessionStorage. O refresh token
    é entregue em cookie HttpOnly e não é exposto no corpo da resposta.

    Falhas de credencial, conta inexistente e conta inativa usam a mesma resposta.
    """
    started_at = start_uniform_timing()
    normalized_email = normalize_email(str(credentials.email))
    network = request_network_key(request)
    limiter = PersistentAuthRateLimiter(db)
    try:
        decision = await limiter.check(
            AuthRateLimitScope.login,
            identifier=normalized_email,
            network=network,
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "AUTH-014",
                    "message": "Não foi possível autenticar agora. Tente novamente mais tarde.",
                },
                headers={"Retry-After": str(decision.retry_after)},
            )

        service = AuthService(db)
        normalized_credentials = LoginRequest(
            email=normalized_email,
            password=credentials.password,
            audience=credentials.audience,
        )
        try:
            user = await service.authenticate(normalized_credentials)
        except HTTPException as error:
            if _auth_error_code(error) in {"AUTH-003", "AUTH-004"}:
                raise _invalid_login() from None
            raise

        platform_identity = service.is_platform_identity(user)
        if (
            credentials.audience == "platform" and not platform_identity
        ) or (
            credentials.audience == "workspace" and platform_identity
        ):
            raise _invalid_login()
        if platform_identity and not service.has_active_platform_access(user):
            raise _invalid_login()
        await limiter.clear(
            AuthRateLimitScope.login,
            identifier=normalized_email,
        )
        session_context = service._build_session(user)
        if user.password_change_required:
            token_response = await service.issue_new_session(user, request=request)
            await db.commit()
            return _set_refresh_cookie(response, token_response)
        mfa_service = LocalMfaService(db)
        mfa_enabled = await mfa_service.is_enabled(user.id)
        if platform_identity and settings.is_production and not mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTH-BLACKBOX-MFA-ENROLLMENT-REQUIRED",
                    "message": (
                        "A equipe da Blackbox deve habilitar MFA antes do acesso em produção."
                    ),
                },
            )
        if mfa_enabled:
            workspace_id = (
                session_context.active_workspace.id
                if session_context.active_workspace
                else None
            )
            return await mfa_service.begin_login(
                user.id,
                workspace_id=workspace_id,
                request=request,
            )
        token_response = await service.issue_new_session(user, request=request)
        await db.commit()
        return _set_refresh_cookie(response, token_response)
    finally:
        await enforce_uniform_timing(started_at, AuthRateLimitScope.login)


@router.post("/refresh", response_model=TokenResponse, summary="Renovar token JWT")
async def refresh_token(
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_refresh_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Renova o token JWT do usuário autenticado.
    O frontend deve chamar este endpoint quando o token estiver próximo de expirar.
    """
    if not context.token_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-002", "message": "Refresh token inválido ou expirado."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if context.session_id is None and not await consume_token(context.token_id, context.expires_at):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-009", "message": "Token revogado. Faça login novamente."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    service = AuthService(db)
    user = await service.get_user_for_refresh(
        context.user_id,
        workspace_id=context.workspace_id,
        membership_id=context.membership_id,
    )
    if context.session_id is None and await is_user_session_revoked(context.token_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-009", "message": "Token revogado. Faça login novamente."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if context.session_id is not None:
        token_response = await service.rotate_session(
            user,
            session_id=context.session_id,
            presented_refresh_jti=context.token_id,
            preferred_workspace_id=context.workspace_id,
            session_issued_at_ns=context.session_issued_at_ns,
            request=request,
        )
    else:
        token_response = await service.issue_new_session(
            user,
            preferred_workspace_id=context.workspace_id,
            request=request,
            session_issued_at_ns=context.session_issued_at_ns,
        )
    await db.commit()
    return _set_refresh_cookie(
        response,
        token_response,
        expose_refresh_token=context.credential_source == "bearer",
    )


@router.post(
    "/select-workspace",
    response_model=TokenResponse,
    summary="Selecionar workspace ativo",
)
async def select_workspace(
    payload: WorkspaceSelectRequest,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    token_response = await service.select_workspace(
        context.user_id,
        payload.workspace_id,
        session_issued_at_ns=context.session_issued_at_ns,
        session_id=context.session_id,
    )
    await db.commit()
    return _set_refresh_cookie(response, token_response)


@router.get("/me", response_model=SessionContextRead, summary="Sessão e workspaces acessíveis")
async def get_me(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    user = await service.get_user_by_id(context.user_id)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-001", "message": "Usuário não encontrado."},
        )
    return service._build_session(user, preferred_workspace_id=context.workspace_id)


@router.post(
    "/ws-ticket",
    response_model=WebSocketTicketRead,
    summary="Emitir ticket curto para o canal em tempo real",
)
async def create_ws_ticket(
    context: AuthContext = Depends(get_current_auth_context),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    if not context.membership_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "WS-001", "message": "Sessão sem vínculo ativo para tempo real."},
        )
    if context.session_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH-009", "message": "Familia de sessao revogada ou ausente."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return WebSocketTicketRead(
        ticket=create_websocket_ticket(
            context.user_id,
            workspace_id=workspace_id,
            membership_id=context.membership_id,
            session_id=context.session_id,
            auth_epoch=context.auth_epoch,
            session_issued_at_ns=context.session_issued_at_ns,
        ),
        expires_in=settings.WEBSOCKET_TICKET_EXPIRE_SECONDS,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Logout")
async def logout(
    request: Request,
    response: Response,
    requested_audience: str | None = Header(default=None, alias="X-Auth-Audience"),
    context: AuthContext | None = Depends(get_optional_access_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoga credenciais apresentadas, apaga o cookie e retorna 204.
    """
    if context and context.token_id:
        await revoke_token(context.token_id, context.expires_at)
    audience = (
        context.surface
        if context and context.surface in {"workspace", "platform"}
        else "platform" if requested_audience == "platform" else "workspace"
    )
    refresh_cookie_name = (
        settings.AUTH_PLATFORM_REFRESH_COOKIE_NAME
        if audience == "platform"
        else settings.AUTH_REFRESH_COOKIE_NAME
    )
    refresh_token = request.cookies.get(refresh_cookie_name)
    refresh_payload = verify_token(refresh_token) if refresh_token else None
    logout_user_id = context.user_id if context is not None else None
    logout_session_id = context.session_id if context is not None else None
    if refresh_payload and refresh_payload.get("token_type") == "refresh":
        refresh_token_id = refresh_payload.get("jti")
        if refresh_token_id:
            await revoke_token(refresh_token_id, refresh_payload.get("exp"))
        if logout_user_id is None:
            try:
                logout_user_id = UUID(str(refresh_payload.get("sub")))
            except (TypeError, ValueError):
                pass
        if logout_session_id is None and refresh_payload.get("session_id"):
            try:
                logout_session_id = UUID(str(refresh_payload["session_id"]))
            except (TypeError, ValueError):
                pass
    if logout_user_id is not None:
        if logout_session_id is not None:
            await AuthSessionService(db).revoke(
                session_id=logout_session_id,
                user_id=logout_user_id,
                reason="logout",
                missing_ok=True,
            )
        await LocalMfaService(db).revoke_all_administrative_sessions(
            logout_user_id,
            actor_user_id=logout_user_id,
            reason="logout",
            request=request,
            commit=False,
        )
        await db.commit()
        if logout_session_id is not None:
            await ws_manager.disconnect_session(logout_session_id, code=4401)
        else:
            await ws_manager.disconnect_user_sessions(logout_user_id, code=4401)
    _clear_refresh_cookie(response, audience=audience)
    return None


# Password reset local

@router.post(
    "/forgot-password",
    response_model=PublicIdentityRequestAcceptedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    started_at = start_uniform_timing()
    normalized_email = normalize_email(str(body.email))
    try:
        decision = await PersistentAuthRateLimiter(db).check(
            AuthRateLimitScope.forgot_password,
            identifier=normalized_email,
            network=request_network_key(request),
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "AUTH-RATE-LIMITED",
                    "message": "Muitas solicitacoes. Tente novamente mais tarde.",
                },
                headers={"Retry-After": str(decision.retry_after)},
            )
        return await IdentityLifecycleService(db).request_password_reset(normalized_email)
    finally:
        await enforce_uniform_timing(started_at, AuthRateLimitScope.forgot_password)


@router.post("/reset-password", response_model=MessageRead, status_code=200)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Redefine a senha com segredo local de uso unico."""
    started_at = start_uniform_timing()
    network = request_network_key(request)
    limiter = PersistentAuthRateLimiter(db)
    try:
        decision = await limiter.check(
            AuthRateLimitScope.reset_password,
            identifier=opaque_auth_digest(
                "reset-attempt",
                str(body.delivery_id or body.token or body.code),
            ),
            network=network,
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "AUTH-VERIFICATION-INVALID",
                    "message": "Verificacao invalida, expirada ou ja utilizada.",
                },
            )
        result = await IdentityLifecycleService(db).reset_password(
            body,
            request=request,
        )
        _clear_refresh_cookie(response)
        return result
    finally:
        await enforce_uniform_timing(started_at, AuthRateLimitScope.reset_password)


@router.post("/password", response_model=MessageRead, summary="Alterar a propria senha")
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    result = await IdentityLifecycleService(db).change_password(
        user_id=context.user_id,
        current_password=body.current_password,
        new_password=body.new_password,
        request=request,
    )
    _clear_refresh_cookie(response)
    return result


@router.get(
    "/invitations/{invitation_id}",
    response_model=InvitationPreviewRead,
    summary="Visualizar convite local",
)
async def preview_invitation(
    invitation_id: UUID,
    token: str = Query(min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    return await IdentityLifecycleService(db).invitation_preview(invitation_id, token=token)


@router.post(
    "/invitations/{invitation_id}/accept",
    response_model=InvitationAcceptedRead,
    summary="Aceitar convite de workspace",
)
async def accept_invitation(
    invitation_id: UUID,
    body: InvitationAcceptRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    identifier = f"invitation:{invitation_id}:delivery:{body.delivery_id or 'token'}"
    decision = await PersistentAuthRateLimiter(db).check(
        AuthRateLimitScope.accept_invitation,
        identifier=identifier,
        network=request_network_key(request),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "AUTH-RATE-LIMITED", "message": "Aceite temporariamente limitado."},
            headers={"Retry-After": str(decision.retry_after)},
        )
    return await IdentityLifecycleService(db).accept_invitation(
        invitation_id,
        body,
        idempotency_key=idempotency_key,
    )


@router.get("/sessions", response_model=list[UserSessionRead], summary="Listar meus dispositivos")
async def list_user_sessions(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await AuthSessionService(db).list(
        context.user_id,
        current_session_id=context.session_id,
    )


@router.delete(
    "/sessions/others",
    response_model=SessionRevocationRead,
    summary="Revogar todas as outras sessoes",
)
async def revoke_other_user_sessions(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    revoked_session_ids = await AuthSessionService(db).revoke_all_session_ids(
        user_id=context.user_id,
        reason="user_requested_others",
        except_session_id=context.session_id,
    )
    await db.commit()
    await ws_manager.disconnect_sessions(revoked_session_ids, code=4401)
    return SessionRevocationRead(revoked_count=len(revoked_session_ids))


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revogar uma sessao normal",
)
async def revoke_user_session(
    session_id: UUID,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await AuthSessionService(db).revoke(
        session_id=session_id,
        user_id=context.user_id,
        reason="user_requested",
    )
    await db.commit()
    await ws_manager.disconnect_session(session_id, code=4401)
    return None


# ── MFA local e sessões administrativas ──────────────────────────────────────


@router.get("/mfa", response_model=MfaStatusRead, summary="Consultar MFA local")
async def get_mfa_status(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await LocalMfaService(db).status(context.user_id)


@router.post(
    "/mfa/enroll",
    response_model=MfaChallengeRead,
    summary="Iniciar habilitação de MFA local",
)
async def begin_mfa_enrollment(
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await LocalMfaService(db).begin_enrollment(
        context.user_id,
        token_id=context.token_id,
        request=request,
    )


@router.post(
    "/mfa/enroll/{challenge_id}/verify",
    response_model=MfaEnrollmentCompleteRead,
    summary="Concluir habilitação de MFA local",
)
async def complete_mfa_enrollment(
    challenge_id: UUID,
    body: MfaVerificationRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await LocalMfaService(db).complete_enrollment(
        challenge_id,
        context.user_id,
        body,
        token_id=context.token_id,
        request=request,
    )


@router.post(
    "/mfa/login/{challenge_id}/verify",
    response_model=TokenResponse,
    summary="Concluir login com MFA local",
)
async def complete_mfa_login(
    challenge_id: UUID,
    body: MfaVerificationRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    verified = await LocalMfaService(db).complete_login(
        challenge_id,
        body,
        request=request,
    )
    auth_service = AuthService(db)
    await auth_service.get_user_for_refresh(
        verified.user_id,
        workspace_id=verified.workspace_id,
        membership_id=None,
    )
    user = await auth_service.get_user_by_id(verified.user_id)
    if user is None:
        raise _invalid_login()
    token_response = await auth_service.issue_new_session(
        user,
        preferred_workspace_id=verified.workspace_id,
        request=request,
    )
    await db.commit()
    return _set_refresh_cookie(response, token_response)


@router.post(
    "/admin-sessions/challenge",
    response_model=MfaChallengeRead,
    summary="Criar challenge para sessão administrativa",
)
async def begin_administrative_session(
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await LocalMfaService(db).begin_administrative_session(
        context.user_id,
        workspace_id=context.workspace_id,
        token_id=context.token_id,
        request=request,
    )


@router.post(
    "/admin-sessions/challenge/{challenge_id}/verify",
    response_model=AdministrativeSessionTokenRead,
    summary="Emitir sessão administrativa após MFA",
)
async def complete_administrative_session(
    challenge_id: UUID,
    body: MfaVerificationRequest,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await LocalMfaService(db).complete_administrative_session(
        challenge_id,
        context.user_id,
        body,
        workspace_id=context.workspace_id,
        token_id=context.token_id,
        request=request,
    )


@router.get(
    "/admin-sessions",
    response_model=list[AdministrativeSessionRead],
    summary="Listar sessões administrativas",
)
async def list_administrative_sessions(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    service = LocalMfaService(db)
    await service.require_administrator(
        context.user_id,
        workspace_id=context.workspace_id,
    )
    sessions = await service.list_administrative_sessions(context.user_id)
    return [AdministrativeSessionRead.model_validate(item) for item in sessions]


@router.get(
    "/admin-sessions/current",
    response_model=AdministrativeSessionStatusRead,
    summary="Validar sessão administrativa",
)
async def get_current_administrative_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(admin_bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    if credentials is None:
        raise LocalMfaService._invalid_admin_session()
    session = await LocalMfaService(db).verify_administrative_session(
        credentials.credentials,
        request=request,
    )
    return AdministrativeSessionStatusRead(
        active=True,
        session=AdministrativeSessionRead.model_validate(session),
    )


@router.delete(
    "/admin-sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revogar todas as sessões administrativas",
)
async def revoke_all_administrative_sessions(
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    service = LocalMfaService(db)
    await service.require_administrator(
        context.user_id,
        workspace_id=context.workspace_id,
    )
    await service.revoke_all_administrative_sessions(
        context.user_id,
        actor_user_id=context.user_id,
        reason="user_requested_all",
        request=request,
    )
    return None


@router.delete(
    "/admin-sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revogar sessão administrativa",
)
async def revoke_administrative_session(
    session_id: UUID,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    service = LocalMfaService(db)
    await service.require_administrator(
        context.user_id,
        workspace_id=context.workspace_id,
    )
    await service.revoke_administrative_session(
        session_id,
        context.user_id,
        request=request,
    )
    return None


@router.get(
    "/security-events",
    response_model=list[AuthSecurityEventRead],
    summary="Consultar auditoria de identidade",
)
async def list_auth_security_events(
    limit: int = Query(default=50, ge=1, le=100),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    service = LocalMfaService(db)
    await service.require_administrator(
        context.user_id,
        workspace_id=context.workspace_id,
    )
    return await service.security_events(context.user_id, limit=limit)
