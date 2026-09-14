"""
Service: AuthService
Autenticação de usuários humanos via email/senha + JWT.

Códigos de erro:
  AUTH-001  Token ausente ou inválido
  AUTH-002  Token expirado
  AUTH-003  Credenciais incorretas (email ou senha)
  AUTH-004  Usuário inativo — contate o administrador
"""
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.identity_surface import (
    has_active_platform_access,
    is_platform_identity,
)
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref
from app.core.platform_rbac import is_platform_root, platform_permissions_as_dicts
from app.core.security import (
    AUTH_DUMMY_PASSWORD_HASH,
    verify_password_async,
)
from app.models.user import AgentType, User
from app.models.workspace import Workspace
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
)
from app.schemas.user import LoginRequest, RegisterRequest, TokenResponse, UserRead
from app.schemas.workspace import SessionContextRead
from app.services.auth_session_service import AuthSessionService
from app.services.workspace_service import WorkspaceService


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _user_read(user: User) -> UserRead:
        return UserRead.model_validate(user).model_copy(
            update={
                "is_platform_root": is_platform_root(user),
                "platform_permissions": platform_permissions_as_dicts(user),
            }
        )

    @staticmethod
    def is_platform_identity(user: User) -> bool:
        return is_platform_identity(user)

    @staticmethod
    def has_active_platform_access(user: User) -> bool:
        return has_active_platform_access(user)

    async def register(self, payload: RegisterRequest) -> TokenResponse:
        del payload
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "AUTH-VERIFICATION-REQUIRED",
                "message": "O cadastro deve ser solicitado e verificado pelo lifecycle publico.",
            },
        )

    async def authenticate(self, credentials: LoginRequest) -> User:
        """
        Autentica usuário e retorna JWT + dados do usuário.
        Lança HTTPException com código de erro padronizado em caso de falha.
        """
        # Buscar usuário pelo email
        normalized_email = str(credentials.email).strip().casefold()
        result = await self.db.execute(
            select(User)
            .options(
                selectinload(User.memberships).selectinload(WorkspaceMembership.workspace)
            )
            .where(
                func.lower(User.email) == normalized_email,
                User.agent_type == AgentType.human,
                User.deleted_at.is_(None),
            )
            .with_for_update()
        )
        user = result.scalar_one_or_none()

        encoded_password = credentials.password.encode("utf-8")
        password_too_long = len(encoded_password) > 72
        safe_password = (
            encoded_password[:72].decode(errors="ignore")
            if password_too_long
            else credentials.password
        )
        candidate_hash = (
            user.hashed_password
            if user is not None and user.hashed_password
            else AUTH_DUMMY_PASSWORD_HASH
        )
        try:
            password_matches = await verify_password_async(safe_password, candidate_hash)
        except (TypeError, ValueError):
            await verify_password_async(safe_password, AUTH_DUMMY_PASSWORD_HASH)
            password_matches = False

        if (
            user is None
            or not user.hashed_password
            or not password_matches
            or password_too_long
        ):
            logger.warning(
                "auth.login_failed",
                reason="invalid_credentials",
                email_ref=opaque_ref(str(getattr(credentials, "email", "") or ""), kind="email"),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH-003", "message": "Email ou senha incorretos."},
            )

        # Verificar se o usuário está ativo
        if not user.is_active:
            logger.warning(
                "auth.login_failed",
                reason="inactive",
                user_id=str(user.id),
                email_ref=opaque_ref(user.email, kind="email"),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH-004", "message": "Usuário inativo. Contate o administrador."},
            )
        if user.email_verified_at is None:
            logger.warning(
                "auth.login_failed",
                reason="email_unverified",
                user_id=str(user.id),
                email_ref=opaque_ref(user.email, kind="email"),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH-003", "message": "Email ou senha incorretos."},
            )

        logger.info(
            "user_login",
            user_id=str(user.id),
            email_ref=opaque_ref(user.email, kind="email"),
        )
        return user

    async def login(
        self,
        credentials: LoginRequest,
        *,
        request: Request | None = None,
    ) -> TokenResponse:
        user = await self.authenticate(credentials)
        return await self.issue_new_session(user, request=request)

    async def get_user_for_refresh(
        self,
        user_id: UUID,
        *,
        workspace_id: UUID | None,
        membership_id: UUID | None,
    ) -> User:
        """Recarrega e bloqueia o estado que autoriza uma rotação de refresh."""
        result = await self.db.execute(
            select(User)
            .options(
                selectinload(User.memberships).selectinload(WorkspaceMembership.workspace)
            )
            .where(
                User.id == user_id,
                User.agent_type == AgentType.human,
                User.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH-001", "message": "Usuário não encontrado."},
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH-004", "message": "Usuário inativo. Contate o administrador."},
            )
        if user.email_verified_at is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "AUTH-009",
                    "message": "Credencial revogada. Verifique o email e entre novamente.",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        if membership_id and not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTH-010",
                    "message": "O acesso ao workspace não está mais ativo.",
                },
            )

        if workspace_id:
            membership_conditions = [
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                Workspace.deleted_at.is_(None),
                Workspace.is_active.is_(True),
            ]
            if membership_id:
                membership_conditions.append(WorkspaceMembership.id == membership_id)

            membership_result = await self.db.execute(
                select(WorkspaceMembership)
                .options(selectinload(WorkspaceMembership.workspace))
                .join(Workspace, WorkspaceMembership.workspace_id == Workspace.id)
                .where(*membership_conditions)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if membership_result.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "AUTH-010",
                        "message": "O acesso ao workspace não está mais ativo.",
                    },
                )

        return user

    async def select_workspace(
        self,
        user_id: UUID,
        workspace_id: UUID,
        *,
        session_issued_at_ns: int | None = None,
        session_id: UUID | None = None,
    ) -> TokenResponse:
        result = await self.db.execute(
            select(User)
            .options(
                selectinload(User.memberships).selectinload(WorkspaceMembership.workspace)
            )
            .where(User.id == user_id, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH-001", "message": "Usuário não encontrado."},
            )

        membership = next(
            (
                membership
                for membership in user.memberships
                if membership.workspace_id == workspace_id
                and membership.status == MembershipStatus.active
            ),
            None,
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH-008", "message": "Você não tem acesso a este workspace."},
            )

        if session_id is not None:
            return await self.issue_workspace_switch(
                user,
                workspace_id=workspace_id,
                session_id=session_id,
                session_issued_at_ns=session_issued_at_ns,
            )
        return await self.issue_new_session(
            user,
            preferred_workspace_id=workspace_id,
            session_issued_at_ns=session_issued_at_ns,
        )

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        result = await self.db.execute(
            select(User)
            .options(selectinload(User.memberships).selectinload(WorkspaceMembership.workspace))
            .where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    def _build_session(
        self,
        user: User,
        preferred_workspace_id: UUID | None = None,
    ) -> SessionContextRead:
        if self.is_platform_identity(user):
            return SessionContextRead(
                surface="platform",
                active_workspace=None,
                available_workspaces=[],
                permissions=[],
                is_internal_staff=True,
                is_platform_root=is_platform_root(user),
                platform_role_package=user.platform_role_package,
                platform_permissions=platform_permissions_as_dicts(user),
                active_role=None,
            )

        workspace_service = WorkspaceService(self.db)
        active_memberships = [
            membership
            for membership in user.memberships
            if membership.status == MembershipStatus.active
            and membership.workspace
            and membership.workspace.deleted_at is None
        ]

        selected_membership = None
        if preferred_workspace_id:
            selected_membership = next(
                (
                    membership
                    for membership in active_memberships
                    if membership.workspace_id == preferred_workspace_id
                ),
                None,
            )
        if not selected_membership:
            selected_membership = next(
                (membership for membership in active_memberships if membership.is_default),
                None,
            )
        if not selected_membership and len(active_memberships) == 1:
            selected_membership = active_memberships[0]

        available = [
            workspace_service.to_membership_read(membership)
            for membership in active_memberships
        ]
        permissions = available[0].permissions if selected_membership and available else []
        for membership in available:
            if selected_membership and membership.id == selected_membership.id:
                permissions = membership.permissions
                break

        return SessionContextRead(
            surface="workspace",
            active_workspace=workspace_service.to_workspace_summary(selected_membership.workspace)
            if selected_membership else None,
            available_workspaces=available,
            permissions=permissions,
            is_internal_staff=user.is_internal_staff,
            active_role=selected_membership.role if selected_membership else None,
        )

    @staticmethod
    def _selected_membership(session: SessionContextRead):
        return next(
            (
                membership
                for membership in session.available_workspaces
                if session.active_workspace
                and membership.workspace.id == session.active_workspace.id
            ),
            None,
        )

    async def issue_new_session(
        self,
        user: User,
        *,
        preferred_workspace_id: UUID | None = None,
        request: Request | None = None,
        session_issued_at_ns: int | None = None,
    ) -> TokenResponse:
        session = self._build_session(user, preferred_workspace_id=preferred_workspace_id)
        active_membership = self._selected_membership(session)
        issued = await AuthSessionService(self.db).create(
            user_id=user.id,
            workspace_id=session.active_workspace.id if session.active_workspace else None,
            role=active_membership.role.value if active_membership else None,
            membership_id=active_membership.id if active_membership else None,
            is_internal_staff=user.is_internal_staff,
            auth_epoch=user.auth_epoch,
            request=request,
            session_issued_at_ns=session_issued_at_ns,
        )
        return TokenResponse(
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            user=self._user_read(user),
            session=session,
        )

    async def rotate_session(
        self,
        user: User,
        *,
        session_id: UUID,
        presented_refresh_jti: str,
        preferred_workspace_id: UUID | None,
        session_issued_at_ns: int | None,
        request: Request | None,
    ) -> TokenResponse:
        session = self._build_session(user, preferred_workspace_id=preferred_workspace_id)
        active_membership = self._selected_membership(session)
        issued = await AuthSessionService(self.db).rotate(
            session_id=session_id,
            user_id=user.id,
            presented_refresh_jti=presented_refresh_jti,
            workspace_id=session.active_workspace.id if session.active_workspace else None,
            role=active_membership.role.value if active_membership else None,
            membership_id=active_membership.id if active_membership else None,
            is_internal_staff=user.is_internal_staff,
            auth_epoch=user.auth_epoch,
            session_issued_at_ns=session_issued_at_ns,
            request=request,
        )
        return TokenResponse(
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            user=self._user_read(user),
            session=session,
        )

    async def issue_workspace_switch(
        self,
        user: User,
        *,
        workspace_id: UUID,
        session_id: UUID,
        session_issued_at_ns: int | None,
    ) -> TokenResponse:
        if self.is_platform_identity(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AUTH-SURFACE-MISMATCH",
                    "message": "A equipe do SaaS opera exclusivamente pela Central Blackbox.",
                },
            )
        session = self._build_session(user, preferred_workspace_id=workspace_id)
        active_membership = self._selected_membership(session)
        if active_membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH-008", "message": "Voce nao tem acesso a este workspace."},
            )
        issued = await AuthSessionService(self.db).switch_workspace(
            session_id=session_id,
            user_id=user.id,
            workspace_id=workspace_id,
            role=active_membership.role.value,
            membership_id=active_membership.id,
            is_internal_staff=user.is_internal_staff,
            auth_epoch=user.auth_epoch,
            session_issued_at_ns=session_issued_at_ns,
        )
        return TokenResponse(
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            user=self._user_read(user),
            session=session,
        )
