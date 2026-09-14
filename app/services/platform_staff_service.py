"""Gestão da equipe interna do SaaS, separada das memberships dos clientes."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_rbac import (
    PLATFORM_PERMISSION_CATALOG,
    PLATFORM_ROLE_PACKAGES,
    PlatformPermission,
    PlatformRolePackage,
    effective_platform_permissions,
    is_platform_root,
    normalize_platform_permissions,
    platform_permission_key,
)
from app.core.security import hash_password_async
from app.models.auth_security import AuthSecurityEvent
from app.models.identity import IdentityOutboxPurpose, UserSession
from app.models.user import AgentType, User, UserRole, normalize_email
from app.models.workspace_membership import WorkspaceMembership
from app.schemas.platform_admin import (
    PlatformPermissionRead,
    PlatformRolePackageRead,
    PlatformStaffCreate,
    PlatformStaffPageRead,
    PlatformStaffRead,
    PlatformStaffUpdate,
    PlatformTemporaryPasswordRead,
)
from app.services.auth_revocation_service import AuthRevocationService
from app.services.auth_session_service import AuthSessionService
from app.services.identity_lifecycle_service import IdentityLifecycleService
from app.services.identity_outbox_service import LocalIdentityOutboxService


class PlatformStaffService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def package_catalog() -> list[PlatformRolePackageRead]:
        return [
            PlatformRolePackageRead(
                key=package.key,
                label=package.label,
                description=package.description,
                permissions=PlatformStaffService._permission_reads(package.permissions),
                assignable=package.assignable,
                requires_mfa=package.requires_mfa,
            )
            for package in PLATFORM_ROLE_PACKAGES.values()
        ]

    async def list(self) -> list[PlatformStaffRead]:
        users = list(
            (
                await self.db.scalars(
                    select(User)
                    .where(
                        User.agent_type == AgentType.human,
                        User.deleted_at.is_(None),
                        or_(
                            User.is_platform_admin.is_(True),
                            User.is_internal_staff.is_(True),
                        ),
                    )
                    .order_by(User.is_platform_admin.desc(), User.name.asc())
                )
            ).all()
        )
        return [await self.to_read(user) for user in users]

    async def list_page(
        self,
        *,
        search: str | None,
        role_package: str | None,
        access_active: bool | None,
        limit: int,
        offset: int,
    ) -> PlatformStaffPageRead:
        filters = [
            User.agent_type == AgentType.human,
            User.deleted_at.is_(None),
            or_(User.is_platform_admin.is_(True), User.is_internal_staff.is_(True)),
        ]
        normalized_search = (search or "").strip()
        if normalized_search:
            pattern = f"%{normalized_search.replace('%', r'\%').replace('_', r'\_')}%"
            filters.append(
                or_(
                    User.name.ilike(pattern, escape="\\"),
                    User.email.ilike(pattern, escape="\\"),
                )
            )
        if role_package:
            if role_package == "platform_owner":
                filters.extend(
                    [
                        User.is_platform_admin.is_(True),
                        User.platform_role_package == "platform_owner",
                    ]
                )
            else:
                filters.append(User.platform_role_package == role_package)
        if access_active is not None:
            filters.append(User.platform_access_active.is_(access_active))

        total = int(
            await self.db.scalar(select(func.count(User.id)).where(*filters)) or 0
        )
        users = list(
            (
                await self.db.scalars(
                    select(User)
                    .where(*filters)
                    .order_by(
                        User.is_platform_admin.desc(),
                        User.name.asc(),
                        User.id.asc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        session_stats: dict[UUID, tuple[int, datetime | None]] = {}
        if users:
            now = datetime.now(UTC)
            rows = (
                await self.db.execute(
                    select(
                        UserSession.user_id,
                        func.count(UserSession.id),
                        func.max(UserSession.last_seen_at),
                    )
                    .where(
                        UserSession.user_id.in_([user.id for user in users]),
                        UserSession.revoked_at.is_(None),
                        UserSession.expires_at > now,
                    )
                    .group_by(UserSession.user_id)
                )
            ).all()
            session_stats = {
                user_id: (int(active_count), last_seen_at)
                for user_id, active_count, last_seen_at in rows
            }
        return PlatformStaffPageRead(
            items=[
                await self.to_read(user, session_stats=session_stats.get(user.id, (0, None)))
                for user in users
            ],
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(users) < total,
            sort=[
                {"field": "isPlatformAdmin", "direction": "desc"},
                {"field": "name", "direction": "asc"},
                {"field": "id", "direction": "asc"},
            ],
        )

    async def create(
        self,
        payload: PlatformStaffCreate,
        *,
        actor: User,
    ) -> PlatformStaffRead:
        package = self._assignable_package(payload.role_package)
        self._assert_package_assignable_by_actor(package, actor)
        grants, denials = self._validated_overrides(
            payload.permission_grants,
            payload.permission_denials,
            actor=actor,
        )
        email = normalize_email(str(payload.email))
        user = await self.db.scalar(
            select(User)
            .where(func.lower(User.email) == email, User.deleted_at.is_(None))
            .with_for_update()
        )
        created = user is None
        if user is not None and user.agent_type != AgentType.human:
            raise self._conflict("PLATFORM-STAFF-NON-HUMAN", "A identidade informada não é humana.")
        if user is not None and is_platform_root(user):
            raise self._conflict(
                "PLATFORM-STAFF-ROOT",
                "O titular da plataforma não pode ser convertido em funcionário.",
            )
        if user is not None and (user.is_internal_staff or user.platform_role_package):
            raise self._conflict(
                "PLATFORM-STAFF-EXISTS",
                "A identidade já pertence à equipe do SaaS.",
            )
        if user is not None:
            workspace_membership = await self.db.scalar(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.user_id == user.id,
                )
            )
            if workspace_membership is not None:
                raise self._conflict(
                    "PLATFORM-STAFF-WORKSPACE-IDENTITY",
                    "Use outra identidade: usuários de clientes não podem ser "
                    "convertidos em funcionários do SaaS.",
                )
        if user is not None and (not user.is_active or user.email_verified_at is None):
            raise self._conflict(
                "PLATFORM-STAFF-IDENTITY-PENDING",
                "A identidade precisa concluir o convite atual antes de receber acesso ao SaaS.",
            )

        if user is None:
            user = User(
                name=payload.name.strip(),
                email=email,
                hashed_password=(
                    await hash_password_async(payload.temporary_password)
                    if payload.temporary_password is not None
                    else None
                ),
                agent_type=AgentType.human,
                role=UserRole.admin,
                initials=self._initials(payload.name),
                color="hsl(42 72% 42%)",
                is_active=True,
                email_verified_at=datetime.now(UTC),
                password_change_required=payload.temporary_password is not None,
                temporary_password_issued_at=(
                    datetime.now(UTC) if payload.temporary_password is not None else None
                ),
            )
            self.db.add(user)
            await self.db.flush()
        else:
            user.name = payload.name.strip()
            user.initials = self._initials(payload.name)
            if payload.temporary_password is not None:
                user.hashed_password = await hash_password_async(payload.temporary_password)
                user.password_change_required = True
                user.temporary_password_issued_at = datetime.now(UTC)

        user.is_internal_staff = True
        user.platform_role_package = package.key
        user.platform_access_active = True
        user.platform_permission_grants = self._permission_dicts(grants)
        user.platform_permission_denials = self._permission_dicts(denials)
        self._audit(
            event_type="platform_staff_created",
            actor_user_id=actor.id,
            subject_user_id=user.id,
            details={"role_package": package.key, "identity_created": created},
        )
        await self.db.flush()

        delivery_id = None
        if user.hashed_password is None:
            await IdentityLifecycleService(self.db).request_password_reset(email)
            delivery_id = await LocalIdentityOutboxService(self.db).latest_related_delivery_id(
                purpose=IdentityOutboxPurpose.password_reset,
                recipient_email=email,
                related_type="password_reset",
            )
        return await self.to_read(user, password_setup_delivery_id=delivery_id)

    async def update(
        self,
        user_id: UUID,
        payload: PlatformStaffUpdate,
        *,
        actor: User,
    ) -> PlatformStaffRead:
        user = await self.db.scalar(
            select(User).where(User.id == user_id, User.deleted_at.is_(None)).with_for_update()
        )
        if user is None or (not user.is_internal_staff and not user.is_platform_admin):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "PLATFORM-STAFF-NOT-FOUND",
                    "message": "Funcionário do SaaS não encontrado.",
                },
            )
        if is_platform_root(user):
            raise self._conflict(
                "PLATFORM-STAFF-ROOT-IMMUTABLE",
                "O acesso raiz deve ser administrado pelo procedimento de titularidade.",
            )
        update_data = payload.model_dump(exclude_unset=True, exclude={"reason"})
        authority_fields = {
            "role_package",
            "access_active",
            "permission_grants",
            "permission_denials",
        }
        if actor.id == user.id and authority_fields.intersection(update_data):
            raise self._conflict(
                "PLATFORM-STAFF-SELF-AUTHORITY",
                "Funcionários não podem alterar a própria autoridade.",
            )
        if user.platform_role_package == "platform_admin" and not is_platform_root(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLATFORM-ADMIN-ROOT-ONLY",
                    "message": "Somente o Super Admin pode administrar administradores operacionais.",
                },
            )
        package_key = update_data.get("role_package", user.platform_role_package)
        package = self._assignable_package(package_key)
        self._assert_package_assignable_by_actor(package, actor)
        grants_input = update_data.get("permission_grants", user.platform_permission_grants or [])
        denials_input = update_data.get(
            "permission_denials",
            user.platform_permission_denials or [],
        )
        grants, denials = self._validated_overrides(grants_input, denials_input, actor=actor)
        access_active = update_data.get("access_active", user.platform_access_active)
        before = {
            "role_package": user.platform_role_package,
            "access_active": user.platform_access_active,
            "grants": user.platform_permission_grants or [],
            "denials": user.platform_permission_denials or [],
        }
        if "name" in update_data:
            user.name = str(update_data["name"]).strip()
            user.initials = self._initials(user.name)
        user.platform_role_package = package.key
        user.platform_access_active = bool(access_active)
        user.platform_permission_grants = self._permission_dicts(grants)
        user.platform_permission_denials = self._permission_dicts(denials)
        await AuthRevocationService(self.db).advance_epoch(user, reason="platform_access_changed")
        self._audit(
            event_type="platform_staff_updated",
            actor_user_id=actor.id,
            subject_user_id=user.id,
            details={
                "before": before,
                "after": {
                    "role_package": user.platform_role_package,
                    "access_active": user.platform_access_active,
                    "grants": user.platform_permission_grants,
                    "denials": user.platform_permission_denials,
                },
            },
        )
        await self.db.flush()
        return await self.to_read(user)

    async def to_read(
        self,
        user: User,
        *,
        password_setup_delivery_id: UUID | None = None,
        session_stats: tuple[int, datetime | None] | None = None,
    ) -> PlatformStaffRead:
        package_key = (
            "platform_owner"
            if is_platform_root(user)
            else str(user.platform_role_package or "")
        )
        package = PLATFORM_ROLE_PACKAGES.get(package_key)
        if session_stats is None:
            now = datetime.now(UTC)
            active_session_count = int(
                await self.db.scalar(
                    select(func.count(UserSession.id)).where(
                        UserSession.user_id == user.id,
                        UserSession.revoked_at.is_(None),
                        UserSession.expires_at > now,
                    )
                )
                or 0
            )
            last_login_at = await self.db.scalar(
                select(func.max(UserSession.last_seen_at)).where(UserSession.user_id == user.id)
            )
        else:
            active_session_count, last_login_at = session_stats
        return PlatformStaffRead(
            id=user.id,
            name=user.name,
            email=user.email or "",
            initials=user.initials,
            role_package=package_key,
            role_label=package.label if package else package_key,
            access_active=bool(is_platform_root(user) or user.platform_access_active),
            is_platform_root=is_platform_root(user),
            permission_grants=self._permission_reads(
                normalize_platform_permissions(user.platform_permission_grants)
            ),
            permission_denials=self._permission_reads(
                normalize_platform_permissions(user.platform_permission_denials)
            ),
            permissions=self._permission_reads(effective_platform_permissions(user)),
            requires_password_setup=user.hashed_password is None,
            requires_password_change=bool(user.password_change_required),
            password_setup_delivery_id=password_setup_delivery_id,
            active_session_count=active_session_count,
            last_login_at=last_login_at,
            created_at=user.created_at,
        )

    async def revoke_sessions(self, user_id: UUID, *, actor: User) -> int:
        user = await self.db.scalar(
            select(User).where(User.id == user_id, User.deleted_at.is_(None)).with_for_update()
        )
        if user is None or (not user.is_internal_staff and not user.is_platform_admin):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if is_platform_root(user) and not is_platform_root(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PLATFORM-STAFF-ROOT-IMMUTABLE", "message": "O titular não pode ser administrado por funcionários."},
            )
        if actor.id == user.id and not is_platform_root(actor):
            raise self._conflict(
                "PLATFORM-STAFF-SELF-AUTHORITY",
                "Funcionários não podem encerrar as próprias sessões por este painel.",
            )
        revoked = await AuthSessionService(self.db).revoke_all(
            user_id=user.id,
            reason="platform_staff_sessions_revoked",
        )
        await AuthRevocationService(self.db).advance_epoch(user, reason="platform_sessions_revoked")
        self._audit(
            event_type="platform_staff_sessions_revoked",
            actor_user_id=actor.id,
            subject_user_id=user.id,
            details={"revoked_count": revoked},
        )
        await self.db.flush()
        return revoked

    async def set_temporary_password(
        self,
        user_id: UUID,
        temporary_password: str,
        *,
        actor: User,
        workspace_id: UUID | None = None,
        staff_only: bool = False,
    ) -> PlatformTemporaryPasswordRead:
        """Issue a one-use provisional credential without exposing it afterwards."""
        if not is_platform_root(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLATFORM-ROOT-REQUIRED",
                    "message": "Somente o titular da plataforma pode definir senha provisória.",
                },
            )
        user = await self.db.scalar(
            select(User)
            .where(
                User.id == user_id,
                User.agent_type == AgentType.human,
                User.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "PLATFORM-IDENTITY-NOT-FOUND",
                    "message": "Usuário não encontrado.",
                },
            )
        if staff_only and not (user.is_internal_staff or user.is_platform_admin):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "PLATFORM-STAFF-NOT-FOUND",
                    "message": "Funcionário do SaaS não encontrado.",
                },
            )
        if workspace_id is not None:
            membership = await self.db.scalar(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == user.id,
                )
            )
            if membership is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "PLATFORM-PERSON-NOT-FOUND",
                        "message": "Usuário não pertence à conta informada.",
                    },
                )

        user.hashed_password = await hash_password_async(temporary_password)
        user.password_change_required = True
        user.temporary_password_issued_at = datetime.now(UTC)
        revoked = await AuthSessionService(self.db).revoke_all(
            user_id=user.id,
            reason="temporary_password_issued",
        )
        await AuthRevocationService(self.db).advance_epoch(
            user,
            reason="temporary_password_issued",
        )
        self._audit(
            event_type="temporary_password_issued",
            actor_user_id=actor.id,
            subject_user_id=user.id,
            details={
                "revoked_session_count": revoked,
                "subject_surface": "workspace" if workspace_id else "platform",
                **({"workspace_id": str(workspace_id)} if workspace_id else {}),
            },
        )
        await self.db.flush()
        return PlatformTemporaryPasswordRead(
            user_id=user.id,
            requires_password_change=True,
            revoked_session_count=revoked,
        )

    def _validated_overrides(
        self,
        grants_input,
        denials_input,
        *,
        actor: User,
    ) -> tuple[tuple[PlatformPermission, ...], tuple[PlatformPermission, ...]]:
        known = {platform_permission_key(item) for item in PLATFORM_PERMISSION_CATALOG}
        raw = [*list(grants_input or []), *list(denials_input or [])]
        invalid = sorted(
            {
                platform_permission_key(item)
                for item in raw
                if platform_permission_key(item) not in known
            }
        )
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "PLATFORM-PERMISSION-UNKNOWN",
                    "message": "Há permissões fora do catálogo.",
                },
            )
        grants = normalize_platform_permissions(grants_input)
        denials = normalize_platform_permissions(denials_input)
        overlap = {platform_permission_key(item) for item in grants} & {
            platform_permission_key(item) for item in denials
        }
        if overlap:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "PLATFORM-PERMISSION-CONFLICT",
                    "message": "Uma permissão não pode ser concedida e negada ao mesmo tempo.",
                },
            )
        if not is_platform_root(actor):
            actor_keys = {
                platform_permission_key(item)
                for item in effective_platform_permissions(actor)
            }
            if any(platform_permission_key(item) not in actor_keys for item in grants):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "PLATFORM-PERMISSION-ESCALATION",
                        "message": "Você não pode conceder uma permissão que não possui.",
                    },
                )
        return grants, denials

    @staticmethod
    def _assignable_package(key: str | None) -> PlatformRolePackage:
        package = PLATFORM_ROLE_PACKAGES.get(str(key or ""))
        if package is None or not package.assignable:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "PLATFORM-PACKAGE-UNKNOWN",
                    "message": "O pacote de acesso não existe ou não pode ser atribuído.",
                },
            )
        return package

    @staticmethod
    def _assert_package_assignable_by_actor(package: PlatformRolePackage, actor: User) -> None:
        if is_platform_root(actor):
            return
        if package.key == "platform_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLATFORM-ADMIN-ROOT-ONLY",
                    "message": "Somente o Super Admin pode administrar administradores operacionais.",
                },
            )
        actor_keys = {
            platform_permission_key(item)
            for item in effective_platform_permissions(actor)
        }
        package_keys = {platform_permission_key(item) for item in package.permissions}
        if not package_keys.issubset(actor_keys):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLATFORM-PACKAGE-ESCALATION",
                    "message": (
                        "Você não pode atribuir um pacote com permissões "
                        "superiores às suas."
                    ),
                },
            )

    def _audit(
        self,
        *,
        event_type: str,
        actor_user_id: UUID,
        subject_user_id: UUID,
        details: dict,
    ) -> None:
        self.db.add(
            AuthSecurityEvent(
                event_type=event_type,
                outcome="success",
                actor_user_id=actor_user_id,
                subject_user_id=subject_user_id,
                details=details,
            )
        )

    @staticmethod
    def _initials(name: str) -> str:
        parts = name.strip().split()
        return "".join(part[0].upper() for part in parts[:2])[:4] or "U"

    @staticmethod
    def _permission_reads(permissions) -> list[PlatformPermissionRead]:
        return [
            PlatformPermissionRead(module=item.module, action=item.action)
            for item in permissions
        ]

    @staticmethod
    def _permission_dicts(permissions) -> list[dict[str, str]]:
        return [{"module": item.module, "action": item.action} for item in permissions]

    @staticmethod
    def _conflict(code: str, message: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": code, "message": message},
        )
