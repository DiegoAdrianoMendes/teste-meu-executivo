"""Transactional organization and workflow commands for the Blackbox."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.core.platform_rbac import PLATFORM_ROLE_PACKAGES, is_platform_root
from app.models.configuration import PlatformConfigDocument
from app.models.growth_crm import GrowthProspect
from app.models.platform_organization import (
    PlatformDashboardPreference,
    PlatformOnboardingMilestone,
    PlatformOperationalAssignment,
    PlatformReviewRequest,
    PlatformStaffInvitation,
    PlatformTeam,
    PlatformTeamMembership,
    PlatformWorkspaceAssignment,
)
from app.models.subscription_commercial import WorkspaceSubscription
from app.models.super_admin_notice import SuperAdminNotice
from app.models.support_ticket import SupportTicket
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.platform_audit_event_service import PlatformAuditEventService


class PlatformOrganizationError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _email_hint(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    visible = local[:1] if local else ""
    return f"{visible}***@{domain.lower()}"


class PlatformOrganizationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        request: Request | None = None,
        context: AuthContext | None = None,
    ):
        self.db = db
        self.request = request
        self.context = context

    async def _audit(
        self,
        *,
        actor: User,
        module: str,
        action: str,
        resource_type: str,
        resource_id: UUID,
        before: dict | None,
        after: dict | None,
        reason: str,
        reason_source: str = "user",
    ) -> None:
        correlation_id = (
            self.request.headers.get("X-Correlation-ID", "").strip()
            if self.request is not None
            else ""
        ) or str(uuid4())
        route = (
            str(
                getattr(
                    self.request.scope.get("route"),
                    "path",
                    self.request.url.path,
                )
            )
            if self.request is not None
            else "platform_organization"
        )
        await PlatformAuditEventService(self.db).record(
            PlatformAuditEventCreate(
                actor_id=actor.id,
                actor_role_snapshot=actor.platform_role_package or "unknown",
                is_platform_root=is_platform_root(actor),
                administrative_session_id=(
                    self.context.administrative_session_id
                    if self.context is not None
                    else None
                ),
                session_type=(
                    self.context.administrative_session_type
                    if self.context is not None
                    else "normal"
                ),
                module=module,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                resource_label_snapshot=(after or before or {}).get("name") or str(resource_id),
                result="success",
                before=before,
                after=after,
                reason=reason,
                reason_source=reason_source,
                correlation_id=correlation_id,
                operation_id=correlation_id,
                route=route,
                method=(self.request.method if self.request is not None else "COMMAND"),
            )
        )

    @staticmethod
    def _team_snapshot(row: PlatformTeam) -> dict:
        return {
            "name": row.name,
            "slug": row.slug,
            "description": row.description,
            "managerUserId": str(row.manager_user_id) if row.manager_user_id else None,
            "active": row.active,
            "version": row.version,
        }

    async def list_teams(self) -> list[PlatformTeam]:
        return list(
            (
                await self.db.scalars(
                    select(PlatformTeam).order_by(PlatformTeam.name, PlatformTeam.id)
                )
            ).all()
        )

    async def save_team(
        self, *, actor: User, team_id: UUID | None, values: dict, reason: str
    ) -> PlatformTeam:
        row = await self.db.get(PlatformTeam, team_id, with_for_update=True) if team_id else None
        before = self._team_snapshot(row) if row else None
        expected = values.pop("expected_version", None)
        if row and expected is not None and row.version != expected:
            raise PlatformOrganizationError(
                "PLATFORM_TEAM_CONFLICT", "A equipe mudou desde a leitura.", status_code=409
            )
        manager_id = values.get("manager_user_id")
        if manager_id:
            manager = await self.db.get(User, manager_id)
            if not manager or not manager.is_internal_staff or not manager.platform_access_active:
                raise PlatformOrganizationError(
                    "PLATFORM_TEAM_MANAGER_INVALID", "Gestor interno inválido."
                )
        if row is None:
            row = PlatformTeam(**values, version=1)
            self.db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
        await self.db.flush()
        await self._audit(
            actor=actor,
            module="staff",
            action="team.saved",
            resource_type="platform_team",
            resource_id=row.id,
            before=before,
            after=self._team_snapshot(row),
            reason=reason,
        )
        await self.db.refresh(row)
        return row

    async def add_team_member(
        self, *, actor: User, team_id: UUID, values: dict, reason: str
    ) -> PlatformTeamMembership:
        team = await self.db.get(PlatformTeam, team_id)
        user = await self.db.get(User, values["user_id"])
        if not team or not team.active:
            raise PlatformOrganizationError(
                "PLATFORM_TEAM_NOT_FOUND", "Equipe não encontrada.", status_code=404
            )
        if not user or not user.is_internal_staff or not user.platform_access_active:
            raise PlatformOrganizationError(
                "PLATFORM_TEAM_MEMBER_INVALID", "Funcionário ativo obrigatório."
            )
        row = await self.db.scalar(
            select(PlatformTeamMembership)
            .where(
                PlatformTeamMembership.team_id == team_id,
                PlatformTeamMembership.user_id == user.id,
            )
            .with_for_update()
        )
        before = (
            None
            if row is None
            else {"active": row.active, "isPrimary": row.is_primary, "version": row.version}
        )
        if values.get("is_primary"):
            existing_primary = list(
                (
                    await self.db.scalars(
                        select(PlatformTeamMembership).where(
                            PlatformTeamMembership.user_id == user.id,
                            PlatformTeamMembership.is_primary.is_(True),
                            PlatformTeamMembership.active.is_(True),
                        )
                    )
                ).all()
            )
            for membership in existing_primary:
                membership.is_primary = False
                membership.version += 1
        if row is None:
            row = PlatformTeamMembership(
                team_id=team_id,
                user_id=user.id,
                is_primary=values.get("is_primary", False),
                active=True,
                starts_at=values.get("starts_at") or _now(),
                ends_at=values.get("ends_at"),
                version=1,
            )
            self.db.add(row)
        else:
            row.is_primary = values.get("is_primary", row.is_primary)
            row.active = True
            row.starts_at = values.get("starts_at") or row.starts_at
            row.ends_at = values.get("ends_at")
            row.version += 1
        await self.db.flush()
        after = {
            "teamId": str(team_id),
            "userId": str(user.id),
            "active": row.active,
            "isPrimary": row.is_primary,
            "version": row.version,
        }
        await self._audit(
            actor=actor,
            module="staff",
            action="team.member_saved",
            resource_type="platform_team_membership",
            resource_id=row.id,
            before=before,
            after=after,
            reason=reason,
        )
        await self.db.refresh(row)
        return row

    async def save_workspace_assignment(
        self, *, actor: User, workspace_id: UUID, values: dict, reason: str
    ) -> PlatformWorkspaceAssignment:
        if await self.db.get(Workspace, workspace_id) is None:
            raise PlatformOrganizationError(
                "PLATFORM_WORKSPACE_NOT_FOUND", "Conta não encontrada.", status_code=404
            )
        if values.get("assigned_user_id") and values.get("assigned_team_id"):
            raise PlatformOrganizationError(
                "PLATFORM_ASSIGNMENT_TARGET_AMBIGUOUS",
                "Informe uma pessoa ou uma equipe, não ambas.",
            )
        if values.get("assigned_user_id"):
            user = await self.db.get(User, values["assigned_user_id"])
            if not user or not user.is_internal_staff or not user.platform_access_active:
                raise PlatformOrganizationError(
                    "PLATFORM_ASSIGNMENT_USER_INVALID", "Responsável inválido."
                )
        if values.get("assigned_team_id"):
            team = await self.db.get(PlatformTeam, values["assigned_team_id"])
            if not team or not team.active:
                raise PlatformOrganizationError(
                    "PLATFORM_ASSIGNMENT_TEAM_INVALID", "Equipe ativa obrigatória."
                )
        row = await self.db.scalar(
            select(PlatformWorkspaceAssignment)
            .where(
                PlatformWorkspaceAssignment.workspace_id == workspace_id,
                PlatformWorkspaceAssignment.assignment_type == values["assignment_type"],
            )
            .with_for_update()
        )
        before = None if row is None else self.assignment_snapshot(row)
        expected = values.pop("expected_version", None)
        if row and expected is not None and row.version != expected:
            raise PlatformOrganizationError(
                "PLATFORM_ASSIGNMENT_CONFLICT", "A atribuição mudou.", status_code=409
            )
        if row is None:
            row = PlatformWorkspaceAssignment(
                workspace_id=workspace_id,
                version=1,
                starts_at=values.pop("starts_at", None) or _now(),
                **values,
            )
            self.db.add(row)
        else:
            for key, value in values.items():
                if key != "starts_at" or value is not None:
                    setattr(row, key, value)
            row.version += 1
        await self.db.flush()
        await self._audit(
            actor=actor,
            module="customers",
            action="assignment.saved",
            resource_type="workspace_assignment",
            resource_id=row.id,
            before=before,
            after=self.assignment_snapshot(row),
            reason=reason,
        )
        await self.db.refresh(row)
        return row

    @staticmethod
    def assignment_snapshot(row: PlatformWorkspaceAssignment) -> dict:
        return {
            "workspaceId": str(row.workspace_id),
            "assignmentType": row.assignment_type,
            "assignedUserId": str(row.assigned_user_id) if row.assigned_user_id else None,
            "assignedTeamId": str(row.assigned_team_id) if row.assigned_team_id else None,
            "startsAt": row.starts_at,
            "endsAt": row.ends_at,
            "version": row.version,
        }

    async def get_preference(self, user_id: UUID) -> PlatformDashboardPreference | None:
        return await self.db.scalar(
            select(PlatformDashboardPreference).where(
                PlatformDashboardPreference.user_id == user_id
            )
        )

    async def save_preference(self, *, actor: User, values: dict) -> PlatformDashboardPreference:
        row = await self.db.scalar(
            select(PlatformDashboardPreference)
            .where(PlatformDashboardPreference.user_id == actor.id)
            .with_for_update()
        )
        provided_etag = values.pop("etag", None)
        before = (
            {
                "templateKey": row.template_key,
                "templateVersion": row.template_version,
                "preferenceDelta": row.preference_delta,
                "etag": row.etag,
            }
            if row
            else None
        )
        if row and provided_etag and provided_etag != row.etag:
            raise PlatformOrganizationError(
                "PLATFORM_DASHBOARD_PREFERENCE_CONFLICT", "O layout mudou.", status_code=409
            )
        canonical = {
            "templateKey": values["template_key"],
            "templateVersion": values["template_version"],
            "preferenceDelta": values["preference_delta"],
        }
        etag = _hash(canonical)
        if row is None:
            row = PlatformDashboardPreference(user_id=actor.id, etag=etag, **values)
            self.db.add(row)
        else:
            row.template_key = values["template_key"]
            row.template_version = values["template_version"]
            row.preference_delta = values["preference_delta"]
            row.etag = etag
        await self.db.flush()
        await self._audit(
            actor=actor,
            module="dashboard",
            action="preference.saved",
            resource_type="dashboard_preference",
            resource_id=row.id,
            before=before,
            after={**canonical, "etag": row.etag},
            reason="Preferência individual de painel atualizada.",
            reason_source="system",
        )
        await self.db.refresh(row)
        return row

    async def create_staff_invitation(
        self, *, actor: User, values: dict, reason: str
    ) -> PlatformStaffInvitation:
        role = values["role_package"]
        package = PLATFORM_ROLE_PACKAGES.get(role)
        if not package or not package.assignable or role == "platform_owner":
            raise PlatformOrganizationError("PLATFORM_STAFF_ROLE_INVALID", "Função não atribuível.")
        if role == "platform_admin" and not is_platform_root(actor):
            raise PlatformOrganizationError(
                "PLATFORM_STAFF_ADMIN_FORBIDDEN",
                "Somente o titular convida platform_admin.",
                status_code=403,
            )
        active = await self.db.scalar(
            select(PlatformStaffInvitation).where(
                PlatformStaffInvitation.email == values["email"].strip().lower(),
                PlatformStaffInvitation.status == "pending",
            )
        )
        if active:
            raise PlatformOrganizationError(
                "PLATFORM_STAFF_INVITATION_EXISTS", "Já existe convite pendente.", status_code=409
            )
        row = PlatformStaffInvitation(
            email=values["email"].strip().lower(),
            name=values["name"].strip(),
            role_package=role,
            invited_by_user_id=actor.id,
            status="pending",
            expires_at=_now() + timedelta(days=values["expires_in_days"]),
            last_sent_at=None,
            send_count=0,
            version=1,
        )
        self.db.add(row)
        await self.db.flush()
        await self._audit(
            actor=actor,
            module="staff",
            action="invitation.created",
            resource_type="staff_invitation",
            resource_id=row.id,
            before=None,
            after={
                "name": row.name,
                "emailHint": _email_hint(row.email),
                "rolePackage": row.role_package,
                "status": row.status,
                "delivery": "not_attempted",
            },
            reason=reason,
        )
        await self.db.refresh(row)
        return row

    async def revoke_staff_invitation(
        self, *, actor: User, invitation_id: UUID, reason: str
    ) -> PlatformStaffInvitation:
        row = await self.db.get(PlatformStaffInvitation, invitation_id, with_for_update=True)
        if row is None:
            raise PlatformOrganizationError(
                "PLATFORM_STAFF_INVITATION_NOT_FOUND", "Convite não encontrado.", status_code=404
            )
        if row.role_package == "platform_admin" and not is_platform_root(actor):
            raise PlatformOrganizationError(
                "PLATFORM_STAFF_ADMIN_FORBIDDEN",
                "Somente o titular revoga convite de platform_admin.",
                status_code=403,
            )
        before = {"status": row.status, "version": row.version}
        if row.status == "pending":
            row.status = "revoked"
            row.revoked_at = _now()
            row.version += 1
        await self.db.flush()
        await self._audit(
            actor=actor,
            module="staff",
            action="invitation.revoked",
            resource_type="staff_invitation",
            resource_id=row.id,
            before=before,
            after={"status": row.status, "version": row.version},
            reason=reason,
        )
        await self.db.refresh(row)
        return row

    async def save_milestone(
        self, *, actor: User, workspace_id: UUID, values: dict, reason: str
    ) -> PlatformOnboardingMilestone:
        if await self.db.get(Workspace, workspace_id) is None:
            raise PlatformOrganizationError(
                "PLATFORM_WORKSPACE_NOT_FOUND", "Conta não encontrada.", status_code=404
            )
        owner_id = values.get("owner_user_id")
        if owner_id:
            owner = await self.db.get(User, owner_id)
            if not owner or not owner.is_internal_staff or not owner.platform_access_active:
                raise PlatformOrganizationError(
                    "PLATFORM_MILESTONE_OWNER_INVALID", "Responsável interno inválido."
                )
        row = await self.db.scalar(
            select(PlatformOnboardingMilestone)
            .where(
                PlatformOnboardingMilestone.workspace_id == workspace_id,
                PlatformOnboardingMilestone.key == values["key"],
            )
            .with_for_update()
        )
        expected = values.pop("expected_version", None)
        before = None if row is None else self.milestone_snapshot(row)
        if row and expected is not None and row.version != expected:
            raise PlatformOrganizationError(
                "PLATFORM_MILESTONE_CONFLICT", "O milestone mudou.", status_code=409
            )
        if row is None:
            row = PlatformOnboardingMilestone(workspace_id=workspace_id, version=1, **values)
            self.db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
        row.completed_at = _now() if row.status == "completed" else None
        await self.db.flush()
        await self._audit(
            actor=actor,
            module="onboarding",
            action="milestone.saved",
            resource_type="onboarding_milestone",
            resource_id=row.id,
            before=before,
            after=self.milestone_snapshot(row),
            reason=reason,
        )
        await self.db.refresh(row)
        return row

    @staticmethod
    def milestone_snapshot(row: PlatformOnboardingMilestone) -> dict:
        return {
            "workspaceId": str(row.workspace_id),
            "key": row.key,
            "label": row.label,
            "status": row.status,
            "ownerUserId": str(row.owner_user_id) if row.owner_user_id else None,
            "dueAt": row.due_at,
            "completedAt": row.completed_at,
            "version": row.version,
        }

    async def save_operational_assignment(
        self, *, actor: User, values: dict, reason: str
    ) -> PlatformOperationalAssignment:
        if values.get("owner_user_id") and values.get("team_id"):
            raise PlatformOrganizationError(
                "PLATFORM_OPERATIONAL_TARGET_AMBIGUOUS",
                "Informe uma pessoa ou uma equipe, não ambas.",
            )
        source_models = {
            "billing": WorkspaceSubscription,
            "lead": GrowthProspect,
            "ticket": SupportTicket,
            "publication": SuperAdminNotice,
            "configuration": PlatformConfigDocument,
        }
        model = source_models.get(values["source_type"])
        if model is None or await self.db.get(model, values["source_id"]) is None:
            raise PlatformOrganizationError(
                "PLATFORM_OPERATIONAL_SOURCE_NOT_FOUND",
                "Registro operacional de origem não encontrado.",
                status_code=404,
            )
        owner_id = values.get("owner_user_id")
        if owner_id:
            owner = await self.db.get(User, owner_id)
            if not owner or not owner.is_internal_staff or not owner.platform_access_active:
                raise PlatformOrganizationError(
                    "PLATFORM_OPERATIONAL_OWNER_INVALID", "Responsável interno inválido."
                )
        team_id = values.get("team_id")
        if team_id:
            team = await self.db.get(PlatformTeam, team_id)
            if not team or not team.active:
                raise PlatformOrganizationError(
                    "PLATFORM_OPERATIONAL_TEAM_INVALID", "Equipe ativa obrigatória."
                )
        row = await self.db.scalar(
            select(PlatformOperationalAssignment)
            .where(
                PlatformOperationalAssignment.source_type == values["source_type"],
                PlatformOperationalAssignment.source_id == values["source_id"],
            )
            .with_for_update()
        )
        expected = values.pop("expected_version", None)
        before = None if row is None else self.operational_snapshot(row)
        if row and expected is not None and row.version != expected:
            raise PlatformOrganizationError(
                "PLATFORM_OPERATIONAL_ASSIGNMENT_CONFLICT", "A atribuição mudou.", status_code=409
            )
        if row is None:
            row = PlatformOperationalAssignment(version=1, **values)
            self.db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
        await self.db.flush()
        await self._audit(
            actor=actor,
            module=values["source_type"],
            action="operational_assignment.saved",
            resource_type="operational_assignment",
            resource_id=row.id,
            before=before,
            after=self.operational_snapshot(row),
            reason=reason,
        )
        await self.db.refresh(row)
        return row

    @staticmethod
    def operational_snapshot(row: PlatformOperationalAssignment) -> dict:
        return {
            "sourceType": row.source_type,
            "sourceId": str(row.source_id),
            "ownerUserId": str(row.owner_user_id) if row.owner_user_id else None,
            "teamId": str(row.team_id) if row.team_id else None,
            "nextAction": row.next_action,
            "nextActionAt": row.next_action_at,
            "dueAt": row.due_at,
            "state": row.state,
            "version": row.version,
        }

    async def save_review(self, *, actor: User, values: dict, reason: str) -> PlatformReviewRequest:
        source_models = {
            "publication": SuperAdminNotice,
            "configuration": PlatformConfigDocument,
        }
        model = source_models.get(values["source_type"])
        if model is None or await self.db.get(model, values["source_id"]) is None:
            raise PlatformOrganizationError(
                "PLATFORM_REVIEW_SOURCE_NOT_FOUND",
                "Conteúdo de origem da revisão não encontrado.",
                status_code=404,
            )
        reviewer_id = values.get("reviewer_user_id")
        if reviewer_id:
            reviewer = await self.db.get(User, reviewer_id)
            if (
                not reviewer
                or not reviewer.is_internal_staff
                or not reviewer.platform_access_active
            ):
                raise PlatformOrganizationError(
                    "PLATFORM_REVIEWER_INVALID", "Revisor interno inválido."
                )
        row = await self.db.scalar(
            select(PlatformReviewRequest)
            .where(
                PlatformReviewRequest.source_type == values["source_type"],
                PlatformReviewRequest.source_id == values["source_id"],
            )
            .with_for_update()
        )
        expected = values.pop("expected_version", None)
        before = None if row is None else self.review_snapshot(row)
        if row and expected is not None and row.version != expected:
            raise PlatformOrganizationError(
                "PLATFORM_REVIEW_CONFLICT", "A revisão mudou.", status_code=409
            )
        target_state = values["state"]
        if (
            row
            and target_state in {"approved", "published"}
            and row.author_user_id == actor.id
            and not is_platform_root(actor)
        ):
            raise PlatformOrganizationError(
                "PLATFORM_REVIEW_SELF_APPROVAL",
                "Autor não aprova o próprio conteúdo.",
                status_code=403,
            )
        if (
            target_state == "published"
            and not is_platform_root(actor)
            and (row is None or row.state != "approved")
        ):
            raise PlatformOrganizationError(
                "PLATFORM_REVIEW_APPROVAL_REQUIRED",
                "Publicação exige aprovação anterior.",
                status_code=409,
            )
        if row is None:
            row = PlatformReviewRequest(author_user_id=actor.id, version=1, **values)
            self.db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
        row.reviewed_at = (
            _now() if target_state in {"approved", "changes_requested", "published"} else None
        )
        row.reason = reason
        await self.db.flush()
        await self._audit(
            actor=actor,
            module=values["source_type"],
            action="review.saved",
            resource_type="review_request",
            resource_id=row.id,
            before=before,
            after=self.review_snapshot(row),
            reason=reason,
        )
        await self.db.refresh(row)
        return row

    @staticmethod
    def review_snapshot(row: PlatformReviewRequest) -> dict:
        return {
            "sourceType": row.source_type,
            "sourceId": str(row.source_id),
            "state": row.state,
            "authorUserId": str(row.author_user_id) if row.author_user_id else None,
            "reviewerUserId": str(row.reviewer_user_id) if row.reviewer_user_id else None,
            "reviewDueAt": row.review_due_at,
            "reviewedAt": row.reviewed_at,
            "reason": row.reason,
            "version": row.version,
        }
