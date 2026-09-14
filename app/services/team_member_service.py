"""Administração transacional da equipe e das permissões por workspace."""
from __future__ import annotations

import builtins
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_delete, key_workspace_membership
from app.core.deps import AuthContext
from app.core.public_api_policy import has_sensitive_public_api_scope
from app.core.rbac import (
    UNRESTRICTED_PERMISSION_SCOPES,
    Permission,
    get_effective_permissions,
    get_permissions_for_role,
    known_permission_keys,
    normalize_permissions,
    permission_key,
)
from app.core.workspace_access_packages import (
    WorkspaceAccessPackage,
    get_workspace_access_package,
)
from app.models.identity import IdentityOutboxMessage, InvitationStatus, WorkspaceInvitation
from app.models.klm_operations import KlmAuditEvent
from app.models.public_api import WorkspacePublicApiToken
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.workspace import (
    PermissionRead,
    PermissionWrite,
    TeamMemberCreate,
    TeamMemberRead,
    TeamMemberUpdate,
)
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.identity_lifecycle_service import IdentityLifecycleService
from app.services.workspace_service import WorkspaceService
from app.websockets.manager import ws_manager


class TeamMemberService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.workspace_service = WorkspaceService(db)

    async def create(
        self,
        workspace_id: UUID,
        actor: AuthContext,
        payload: TeamMemberCreate,
        *,
        idempotency_key: str,
    ) -> TeamMemberRead:
        actor_membership = await self._actor_membership(workspace_id, actor)
        access_package = self._validated_access_package(payload.access_package)
        if (
            access_package
            and "workspace_role" in payload.model_fields_set
            and payload.workspace_role != access_package.workspace_role
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "TEAM-PACKAGE-ROLE-CONFLICT",
                    "message": "O papel informado não corresponde ao pacote de acesso selecionado.",
                },
            )
        target_role = access_package.workspace_role if access_package else payload.workspace_role
        self._assert_role_assignment(actor_membership, target_role)
        grants_input = payload.permission_grants
        denials_input = payload.permission_denials
        if access_package and "permission_grants" not in payload.model_fields_set:
            grants_input = list(access_package.permission_grants)
        if access_package and "permission_denials" not in payload.model_fields_set:
            denials_input = list(access_package.permission_denials)
        grants, denials = self._validated_overrides(
            grants_input,
            denials_input,
            actor,
            actor_membership,
            target_role,
        )

        invitation = await IdentityLifecycleService(self.db).create_invitation(
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            payload=payload.model_copy(update={"workspace_role": target_role}),
            idempotency_key=idempotency_key,
            permission_grants=self._permission_dicts(grants),
            permission_denials=self._permission_dicts(denials),
            access_package=access_package.key if access_package else None,
        )
        membership = await self._load_membership(workspace_id, invitation.membership_id)
        await self._write_audit(
            workspace_id=workspace_id,
            actor=actor,
            membership=membership,
            event_type="team.invitation.created",
            before=None,
            after=self._audit_snapshot(membership),
        )
        return self.to_read(
            membership,
            invitation=invitation,
            delivery=await self._invitation_delivery(invitation),
        )

    async def list(self, workspace_id: UUID) -> list[TeamMemberRead]:
        memberships = await self.workspace_service.list_memberships(workspace_id)
        invitations = await IdentityLifecycleService(self.db).latest_invitations(workspace_id)
        delivery_ids = [
            invitation.last_delivery_id
            for invitation in invitations.values()
            if invitation.last_delivery_id is not None
        ]
        deliveries = {
            delivery.id: delivery
            for delivery in (
                list(
                    (
                        await self.db.scalars(
                            select(IdentityOutboxMessage).where(
                                IdentityOutboxMessage.id.in_(delivery_ids)
                            )
                        )
                    ).all()
                )
                if delivery_ids
                else []
            )
        }
        return [
            self.to_read(
                item,
                invitation=invitations.get(item.id),
                delivery=(
                    deliveries.get(invitations[item.id].last_delivery_id)
                    if item.id in invitations and invitations[item.id].last_delivery_id
                    else None
                ),
            )
            for item in memberships
            if item.user and item.user.agent_type.value == "human"
        ]

    async def resend_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        actor: AuthContext,
        *,
        idempotency_key: str,
    ) -> TeamMemberRead:
        await self._actor_membership(workspace_id, actor)
        lifecycle = IdentityLifecycleService(self.db)
        invitation = await lifecycle.resend_invitation(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            idempotency_key=idempotency_key,
        )
        membership = await self._load_membership(workspace_id, invitation.membership_id)
        await self._write_audit(
            workspace_id=workspace_id,
            actor=actor,
            membership=membership,
            event_type="team.invitation.resent",
            before=None,
            after=self._audit_snapshot(membership),
        )
        return self.to_read(
            membership,
            invitation=invitation,
            delivery=await self._invitation_delivery(invitation),
        )

    async def revoke_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        actor: AuthContext,
    ) -> TeamMemberRead:
        await self._actor_membership(workspace_id, actor)
        lifecycle = IdentityLifecycleService(self.db)
        invitation = await lifecycle.revoke_invitation(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )
        membership = await self._load_membership(workspace_id, invitation.membership_id)
        await self._write_audit(
            workspace_id=workspace_id,
            actor=actor,
            membership=membership,
            event_type="team.invitation.revoked",
            before=None,
            after=self._audit_snapshot(membership),
        )
        return self.to_read(membership, invitation=invitation)

    async def update(
        self,
        workspace_id: UUID,
        membership_id: UUID,
        actor: AuthContext,
        payload: TeamMemberUpdate,
    ) -> TeamMemberRead:
        membership = await self._load_membership(workspace_id, membership_id)
        actor_membership = await self._actor_membership(workspace_id, actor)
        self._assert_target_manageable(actor, actor_membership, membership)
        before_permissions = get_effective_permissions(
            membership.role,
            membership.permission_grants,
            membership.permission_denials,
        )

        update_data = payload.model_dump(exclude_unset=True)
        if actor.is_support_access:
            unsupported = set(update_data) - {"status"}
            if unsupported:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "SUPPORT-TEAM-SCOPE-LIMIT",
                        "message": "A sessão assistida só pode alterar o status do vínculo.",
                    },
                )
            if membership.role == WorkspaceRole.workspace_owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "SUPPORT-OWNER-PROTECTED",
                        "message": "A sessão assistida não pode alterar o titular da conta.",
                    },
                )
        next_package_key = update_data.get("access_package", membership.access_package)
        access_package = self._validated_access_package(next_package_key)
        explicit_role = update_data.get("workspace_role")
        if access_package and explicit_role is not None and explicit_role != access_package.workspace_role:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "TEAM-PACKAGE-ROLE-CONFLICT",
                    "message": "O papel informado não corresponde ao pacote de acesso selecionado.",
                },
            )
        next_role = access_package.workspace_role if access_package else (explicit_role or membership.role)
        next_status = update_data.get("status", membership.status)
        if not actor.is_support_access:
            self._assert_role_assignment(actor_membership, next_role)
        if "status" in update_data and membership.status == MembershipStatus.invited:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TEAM-INVITATION-ACTION-REQUIRED",
                    "message": "Use as operacoes de reenviar ou revogar para um convite pendente.",
                },
            )
        if next_status == MembershipStatus.invited and membership.status != MembershipStatus.invited:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TEAM-INVITATION-REQUIRED",
                    "message": "Um novo convite deve ser criado para restaurar este acesso.",
                },
            )
        if (
            membership.status == MembershipStatus.suspended
            and next_status == MembershipStatus.active
        ):
            latest_invitation = await self.db.scalar(
                select(WorkspaceInvitation)
                .where(
                    WorkspaceInvitation.workspace_id == workspace_id,
                    WorkspaceInvitation.membership_id == membership.id,
                )
                .order_by(WorkspaceInvitation.created_at.desc())
                .limit(1)
            )
            if (
                latest_invitation is not None
                and latest_invitation.status != InvitationStatus.accepted
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "TEAM-INVITATION-REQUIRED",
                        "message": "Um novo convite precisa ser aceito antes da reativacao.",
                    },
                )
        if actor.user_id == membership.user_id and next_status != MembershipStatus.active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "TEAM-004", "message": "Você não pode suspender o próprio acesso."},
            )
        if membership.role == WorkspaceRole.workspace_owner and (
            next_role != WorkspaceRole.workspace_owner or next_status != MembershipStatus.active
        ):
            await self._assert_another_active_owner(workspace_id, membership.id)
        if (
            membership.status == MembershipStatus.suspended
            and next_status in {MembershipStatus.active, MembershipStatus.invited}
        ):
            await self.workspace_service._assert_membership_limit(
                workspace_id,
                exclude_membership_id=membership.id,
                agent_type=membership.user.agent_type,
            )

        package_was_selected = "access_package" in update_data and access_package is not None
        grants_input = update_data.get(
            "permission_grants",
            list(access_package.permission_grants)
            if package_was_selected
            else membership.permission_grants or [],
        )
        denials_input = update_data.get(
            "permission_denials",
            list(access_package.permission_denials)
            if package_was_selected
            else membership.permission_denials or [],
        )
        grants, denials = self._validated_overrides(
            grants_input,
            denials_input,
            actor,
            actor_membership,
            next_role,
        )

        before = self._audit_snapshot(membership)
        membership.role = next_role
        membership.access_package = next_package_key
        membership.status = next_status
        membership.permission_grants = self._permission_dicts(grants)
        membership.permission_denials = self._permission_dicts(denials)
        after_permissions = get_effective_permissions(next_role, grants, denials)
        await self._revoke_invalid_public_api_tokens(membership, after_permissions)
        await self.db.flush()
        await cache_delete(key_workspace_membership(workspace_id, membership.user_id))
        await self._write_audit(
            workspace_id=workspace_id,
            actor=actor,
            membership=membership,
            event_type="team.member.updated",
            before=before,
            after=self._audit_snapshot(membership),
        )
        if (
            membership.status != MembershipStatus.active
            or self._permission_keys(before_permissions) != self._permission_keys(after_permissions)
        ):
            await ws_manager.disconnect_user(workspace_id, membership.user_id)
        invitation = (
            await IdentityLifecycleService(self.db).latest_invitations(workspace_id)
        ).get(membership.id)
        return self.to_read(
            await self._load_membership(workspace_id, membership.id),
            invitation=invitation,
        )

    def to_read(
        self,
        membership: WorkspaceMembership,
        *,
        invitation: WorkspaceInvitation | None = None,
        delivery: IdentityOutboxMessage | None = None,
    ) -> TeamMemberRead:
        role_permissions = get_permissions_for_role(membership.role)
        grants = normalize_permissions(membership.permission_grants)
        denials = normalize_permissions(membership.permission_denials)
        effective = get_effective_permissions(membership.role, grants, denials)
        return TeamMemberRead(
            membership_id=membership.id,
            user_id=membership.user_id,
            name=membership.user.name,
            email=membership.user.email or "",
            phone=membership.user.phone,
            initials=membership.user.initials,
            color=membership.user.color,
            avatar=membership.user.avatar_url,
            workspace_role=membership.role,
            access_package=membership.access_package,
            status=membership.status,
            is_active=membership.status == MembershipStatus.active and membership.user.is_active,
            role_permissions=self._permission_reads(role_permissions),
            permission_grants=self._permission_reads(grants),
            permission_denials=self._permission_reads(denials),
            permissions=self._permission_reads(effective),
            can_view_sensitive_lead_data=any(
                permission_key(permission) == ("lead_data", "view") for permission in effective
            ),
            can_edit_sensitive_lead_data=any(
                permission_key(permission) == ("lead_data", "edit") for permission in effective
            ),
            created_at=membership.created_at,
            invitation_id=invitation.id if invitation else None,
            invitation_status=invitation.status if invitation else None,
            invitation_expires_at=invitation.expires_at if invitation else None,
            invitation_delivery_id=invitation.last_delivery_id if invitation else None,
            invitation_delivery_status=(delivery.delivery_status.value if delivery else None),
            invitation_delivery_error_code=(
                delivery.last_delivery_error_code if delivery else None
            ),
        )

    async def _invitation_delivery(
        self,
        invitation: WorkspaceInvitation | None,
    ) -> IdentityOutboxMessage | None:
        if invitation is None or invitation.last_delivery_id is None:
            return None
        return await self.db.get(IdentityOutboxMessage, invitation.last_delivery_id)

    async def _revoke_invalid_public_api_tokens(
        self,
        membership: WorkspaceMembership,
        permissions: builtins.list[Permission],
    ) -> None:
        permission_keys = self._permission_keys(permissions)
        can_manage_integrations = ("integrations", "manage") in permission_keys
        can_view_leads = ("lead_data", "view") in permission_keys
        result = await self.db.execute(
            select(WorkspacePublicApiToken).where(
                WorkspacePublicApiToken.workspace_id == membership.workspace_id,
                WorkspacePublicApiToken.created_by_id == membership.user_id,
                WorkspacePublicApiToken.is_active.is_(True),
                WorkspacePublicApiToken.revoked_at.is_(None),
            )
        )
        now = datetime.now(timezone.utc)
        for token in result.scalars().all():
            should_revoke = membership.status != MembershipStatus.active or not can_manage_integrations
            if has_sensitive_public_api_scope(token.scopes) and not can_view_leads:
                should_revoke = True
            if should_revoke:
                token.is_active = False
                token.revoked_at = now

    @staticmethod
    def _permission_keys(permissions: Iterable[Permission]) -> set[tuple[str, str]]:
        return {permission_key(permission) for permission in permissions}

    async def _load_membership(self, workspace_id: UUID, membership_id: UUID) -> WorkspaceMembership:
        result = await self.db.execute(
            select(WorkspaceMembership)
            .options(
                selectinload(WorkspaceMembership.workspace),
                selectinload(WorkspaceMembership.user),
            )
            .where(
                WorkspaceMembership.id == membership_id,
                WorkspaceMembership.workspace_id == workspace_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "TEAM-002", "message": "Usuário não encontrado neste workspace."},
            )
        return membership

    async def _actor_membership(
        self,
        workspace_id: UUID,
        actor: AuthContext,
    ) -> WorkspaceMembership | None:
        if actor.is_support_access:
            return None
        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == actor.user_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TEAM-003", "message": "Acesso administrativo não está mais ativo."},
            )
        return membership

    @staticmethod
    def _assert_role_assignment(
        actor_membership: WorkspaceMembership | None,
        target_role: WorkspaceRole,
    ) -> None:
        if actor_membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TEAM-003", "message": "Acesso administrativo não está mais ativo."},
            )
        if actor_membership.role == WorkspaceRole.workspace_owner:
            return
        if target_role == WorkspaceRole.workspace_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "TEAM-005",
                    "message": "Somente o titular pode atribuir o papel de titular.",
                },
            )
        assignable_roles = {
            WorkspaceRole.workspace_admin: {
                WorkspaceRole.workspace_admin,
                WorkspaceRole.legal_manager,
                WorkspaceRole.finance_manager,
                WorkspaceRole.operator,
                WorkspaceRole.viewer,
            },
            WorkspaceRole.legal_manager: {
                WorkspaceRole.legal_manager,
                WorkspaceRole.operator,
                WorkspaceRole.viewer,
            },
            WorkspaceRole.finance_manager: {
                WorkspaceRole.finance_manager,
                WorkspaceRole.viewer,
            },
            WorkspaceRole.operator: {WorkspaceRole.operator, WorkspaceRole.viewer},
            WorkspaceRole.viewer: {WorkspaceRole.viewer},
        }
        if target_role not in assignable_roles.get(actor_membership.role, set()):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "TEAM-ROLE-ESCALATION",
                    "message": "Você não pode atribuir um papel superior ao seu nível de gestão.",
                },
            )

    @staticmethod
    def _assert_target_manageable(
        actor: AuthContext,
        actor_membership: WorkspaceMembership | None,
        target: WorkspaceMembership,
    ) -> None:
        if actor_membership is None:
            return
        if target.role == WorkspaceRole.workspace_owner and actor_membership.role != WorkspaceRole.workspace_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TEAM-006", "message": "Administradores não podem alterar o titular do workspace."},
            )

    def _validated_overrides(
        self,
        grants_input: Iterable[Permission | PermissionWrite | dict] | None,
        denials_input: Iterable[Permission | PermissionWrite | dict] | None,
        actor: AuthContext,
        actor_membership: WorkspaceMembership | None,
        target_role: WorkspaceRole,
    ) -> tuple[builtins.list[Permission], builtins.list[Permission]]:
        grants = normalize_permissions(grants_input)
        denials = normalize_permissions(denials_input)
        known = known_permission_keys()
        invalid = [permission_key(item) for item in [*grants, *denials] if permission_key(item) not in known]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "TEAM-007", "message": "A permissão informada não existe no catálogo."},
            )
        unsupported_scopes = sorted(
            {
                item.scope
                for item in [*grants, *denials]
                if item.scope not in UNRESTRICTED_PERMISSION_SCOPES
            }
        )
        if unsupported_scopes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "TEAM-011",
                    "message": "O escopo informado não possui filtro por registro implementado.",
                },
            )
        overlap = {permission_key(item) for item in grants} & {permission_key(item) for item in denials}
        if overlap:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "TEAM-008", "message": "Uma permissão não pode ser concedida e bloqueada ao mesmo tempo."},
            )
        if actor_membership is None or actor_membership.role == WorkspaceRole.workspace_owner:
            return grants, denials
        actor_keys = {
            permission_key(item)
            for item in get_effective_permissions(
                actor_membership.role,
                actor_membership.permission_grants,
                actor_membership.permission_denials,
            )
        }
        if any(permission_key(item) not in actor_keys for item in grants):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TEAM-009", "message": "Você não pode conceder uma permissão que não possui."},
            )
        return grants, denials

    @staticmethod
    def _validated_access_package(key: str | None) -> WorkspaceAccessPackage | None:
        if key is None:
            return None
        package = get_workspace_access_package(key)
        if package is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "TEAM-PACKAGE-UNKNOWN",
                    "message": "O pacote de acesso informado não existe.",
                },
            )
        return package

    async def _assert_another_active_owner(self, workspace_id: UUID, membership_id: UUID) -> None:
        count = await self.db.scalar(
            select(func.count(WorkspaceMembership.id)).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.id != membership_id,
                WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        if not count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "TEAM-010", "message": "O workspace precisa manter ao menos um titular ativo."},
            )

    async def _write_audit(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
        membership: WorkspaceMembership,
        event_type: str,
        before: dict | None,
        after: dict,
    ) -> None:
        actor_label = await resolve_human_actor_label(self.db, actor.user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor.user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type=event_type,
                entity_type="workspace_membership",
                entity_id=membership.id,
                summary="Permissões e acesso da equipe atualizados.",
                before_data=before,
                after_data=after,
            )
        )
        await self.db.flush()

    @staticmethod
    def _audit_snapshot(membership: WorkspaceMembership) -> dict:
        return {
            "userId": str(membership.user_id),
            "role": membership.role.value,
            "accessPackage": membership.access_package,
            "status": membership.status.value,
            "permissionGrants": membership.permission_grants or [],
            "permissionDenials": membership.permission_denials or [],
        }

    @staticmethod
    def _permission_dicts(
        permissions: Iterable[Permission],
    ) -> builtins.list[dict[str, str]]:
        return [
            {"module": item.module, "action": item.action, "scope": item.scope}
            for item in permissions
        ]

    @staticmethod
    def _permission_reads(
        permissions: Iterable[Permission],
    ) -> builtins.list[PermissionRead]:
        return [
            PermissionRead(module=item.module, action=item.action, scope=item.scope)
            for item in permissions
        ]
