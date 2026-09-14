"""Transferência auditável de carteiras sem apagar a responsabilidade anterior."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.core.platform_rbac import is_platform_root
from app.db.advisory import acquire_transaction_advisory_lock
from app.models.booking_link import BookingLink
from app.models.growth_crm import GrowthProspect
from app.models.klm_operations import OmnichannelThread
from app.models.matter import Matter
from app.models.meeting import Meeting, MeetingStatus
from app.models.meta_channels import MetaSocialConversation
from app.models.staff_reassignment import (
    PlatformStaffReassignmentEvent,
    PlatformStaffReassignmentItem,
    PlatformStaffReassignmentPlan,
    StaffReassignmentItemStatus,
    StaffReassignmentMode,
    StaffReassignmentStatus,
    WorkspaceStaffReassignmentEvent,
    WorkspaceStaffReassignmentItem,
    WorkspaceStaffReassignmentPlan,
)
from app.models.support_ticket import SupportTicket
from app.models.task import Task, TaskParticipant, TaskStatus
from app.models.user import AgentType, User
from app.models.whatsapp import WhatsAppContact
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.control_plane import MeetingUpdate
from app.schemas.staff_reassignment import (
    StaffReassignmentAuditEventRead,
    StaffReassignmentCreate,
    StaffReassignmentPlanRead,
    StaffReassignmentPreviewRead,
    StaffReassignmentPreviewRequest,
)
from app.schemas.task import TaskUpdate
from app.services.meeting_service import MeetingService
from app.services.task_service import TaskMutationContext, TaskService

WORKSPACE_SCOPES = frozenset(
    {
        "matters",
        "tasks",
        "meetings",
        "booking_links",
        "omnichannel_threads",
        "whatsapp_contacts",
        "social_conversations",
    }
)
PLATFORM_SCOPES = frozenset({"support_tickets", "growth_prospects"})
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{7,179}$")


class StaffReassignmentError(RuntimeError):
    pass


class StaffReassignmentAuthorizationError(StaffReassignmentError):
    pass


class StaffReassignmentConflictError(StaffReassignmentError):
    pass


class StaffReassignmentNotFoundError(StaffReassignmentError):
    pass


class StaffReassignmentValidationError(StaffReassignmentError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _fingerprint(payload: StaffReassignmentCreate) -> str:
    serialized = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalize_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not _IDEMPOTENCY_KEY.fullmatch(normalized):
        raise StaffReassignmentValidationError(
            "Idempotency-Key deve conter de 8 a 180 caracteres seguros."
        )
    return normalized


class StaffReassignmentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.operation_was_replayed = False

    async def preview_workspace(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
        payload: StaffReassignmentPreviewRequest,
    ) -> StaffReassignmentPreviewRead:
        actor_membership, source, target = await self._workspace_authorization(
            workspace_id=workspace_id,
            actor=actor,
            source_user_id=payload.source_user_id,
            target_user_id=payload.target_user_id,
        )
        self._assert_workspace_manager(actor_membership, source)
        scopes = self._validate_scopes(payload.scopes, WORKSPACE_SCOPES)
        counts = {
            scope: await self._workspace_count(
                workspace_id=workspace_id,
                source_user_id=source.user_id,
                scope=scope,
            )
            for scope in scopes
        }
        source_user = await self.db.get(User, source.user_id)
        target_user = await self.db.get(User, target.user_id)
        return StaffReassignmentPreviewRead(
            source_user_id=source.user_id,
            source_name=source_user.name if source_user else "Usuário indisponível",
            target_user_id=target.user_id,
            target_name=target_user.name if target_user else "Usuário indisponível",
            counts=counts,
            total=sum(counts.values()),
        )

    async def apply_workspace(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
        payload: StaffReassignmentCreate,
        idempotency_key: str,
    ) -> StaffReassignmentPlanRead:
        actor_membership, source, target = await self._workspace_authorization(
            workspace_id=workspace_id,
            actor=actor,
            source_user_id=payload.source_user_id,
            target_user_id=payload.target_user_id,
        )
        self._assert_workspace_manager(actor_membership, source)
        scopes = self._validate_scopes(payload.scopes, WORKSPACE_SCOPES)
        self._validate_apply_window(payload)
        idempotency_key = _normalize_idempotency_key(idempotency_key)
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="workspace_staff_reassignment_idempotency",
            value=f"{workspace_id}:{actor.user_id}:{idempotency_key}",
        )
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="workspace_staff_reassignment_source",
            value=f"{workspace_id}:{source.user_id}",
        )
        request_fingerprint = _fingerprint(payload)
        existing = await self.db.scalar(
            select(WorkspaceStaffReassignmentPlan).where(
                WorkspaceStaffReassignmentPlan.workspace_id == workspace_id,
                WorkspaceStaffReassignmentPlan.requested_by_user_id == actor.user_id,
                WorkspaceStaffReassignmentPlan.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise StaffReassignmentConflictError(
                    "A chave de idempotência já foi usada com outra solicitação."
                )
            return await self._workspace_read(existing)

        now = _utcnow()
        plan_id = uuid4()
        tag = f"continuidade:{plan_id}"
        plan = WorkspaceStaffReassignmentPlan(
            id=plan_id,
            workspace_id=workspace_id,
            source_user_id=source.user_id,
            target_user_id=target.user_id,
            mode=payload.mode.value,
            status=StaffReassignmentStatus.applied.value,
            reason=payload.reason.strip(),
            tag_label=tag,
            scopes=scopes,
            assignment_summary={},
            conflict_count=0,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            applied_at=now,
            requested_by_user_id=actor.user_id,
            approved_by_user_id=actor.user_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            version=1,
        )
        self.db.add(plan)
        summary: dict[str, int] = {}
        for scope in scopes:
            rows, assignment_field = await self._workspace_rows(
                workspace_id=workspace_id,
                source_user_id=source.user_id,
                scope=scope,
            )
            summary[scope] = len(rows)
            for row in rows:
                snapshot = self._entity_snapshot(row)
                row = await self._assign_workspace_entity(
                    entity=row,
                    workspace_id=workspace_id,
                    scope=scope,
                    target_user_id=target.user_id,
                    actor_user_id=actor.user_id,
                    correlation_id=(f"staff-reassignment:{plan_id}:apply:{scope}:{row.id}"),
                )
                resource_version = getattr(row, "version", None)
                if resource_version is not None:
                    snapshot["resourceVersionAfter"] = resource_version
                self._tag_entity(
                    row,
                    tag=tag,
                    plan_id=plan_id,
                    source_user_id=source.user_id,
                    target_user_id=target.user_id,
                    applied_at=now,
                )
                self.db.add(
                    WorkspaceStaffReassignmentItem(
                        workspace_id=workspace_id,
                        plan_id=plan_id,
                        entity_type=scope,
                        entity_id=row.id,
                        assignment_field=assignment_field,
                        previous_user_id=source.user_id,
                        assigned_user_id=target.user_id,
                        status=StaffReassignmentItemStatus.applied.value,
                        tag_label=tag,
                        snapshot=snapshot,
                        created_at=now,
                    )
                )
        plan.assignment_summary = summary
        self.db.add(
            WorkspaceStaffReassignmentEvent(
                workspace_id=workspace_id,
                plan_id=plan_id,
                actor_user_id=actor.user_id,
                action="applied",
                details={
                    "mode": payload.mode.value,
                    "tagLabel": tag,
                    "scopes": scopes,
                    "assignmentSummary": summary,
                },
                occurred_at=now,
            )
        )
        await self.db.flush()
        await self.db.refresh(plan)
        return await self._workspace_read(plan)

    async def list_workspace(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
    ) -> list[StaffReassignmentPlanRead]:
        items, _ = await self.page_workspace(
            workspace_id=workspace_id,
            actor=actor,
            limit=200,
            offset=0,
        )
        return items

    async def page_workspace(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
        limit: int,
        offset: int,
    ) -> tuple[list[StaffReassignmentPlanRead], int]:
        actor_membership = await self._actor_membership(workspace_id, actor)
        if actor_membership.role not in {
            WorkspaceRole.workspace_owner,
            WorkspaceRole.workspace_admin,
            WorkspaceRole.legal_manager,
        }:
            raise StaffReassignmentAuthorizationError(
                "Somente administradores e gestores autorizados veem a continuidade."
            )
        filters = [WorkspaceStaffReassignmentPlan.workspace_id == workspace_id]
        total = int(
            await self.db.scalar(
                select(func.count(WorkspaceStaffReassignmentPlan.id)).where(*filters)
            )
            or 0
        )
        plans = (
            await self.db.scalars(
                select(WorkspaceStaffReassignmentPlan)
                .where(*filters)
                .order_by(
                    WorkspaceStaffReassignmentPlan.created_at.desc(),
                    WorkspaceStaffReassignmentPlan.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [await self._workspace_read(plan) for plan in plans], total

    async def get_workspace(
        self,
        *,
        workspace_id: UUID,
        plan_id: UUID,
        actor: AuthContext,
    ) -> StaffReassignmentPlanRead:
        actor_membership = await self._actor_membership(workspace_id, actor)
        if actor_membership.role not in {
            WorkspaceRole.workspace_owner,
            WorkspaceRole.workspace_admin,
            WorkspaceRole.legal_manager,
        }:
            raise StaffReassignmentAuthorizationError(
                "Somente administradores e gestores autorizados veem a continuidade."
            )
        plan = await self.db.scalar(
            select(WorkspaceStaffReassignmentPlan).where(
                WorkspaceStaffReassignmentPlan.workspace_id == workspace_id,
                WorkspaceStaffReassignmentPlan.id == plan_id,
            )
        )
        if plan is None:
            raise StaffReassignmentNotFoundError("Transferência não encontrada.")
        return await self._workspace_read(plan)

    async def restore_workspace(
        self,
        *,
        workspace_id: UUID,
        plan_id: UUID,
        actor: AuthContext,
        expected_version: int,
        reason: str,
    ) -> StaffReassignmentPlanRead:
        actor_membership = await self._actor_membership(workspace_id, actor)
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="workspace_staff_reassignment_plan",
            value=f"{workspace_id}:{plan_id}",
        )
        plan = await self.db.scalar(
            select(WorkspaceStaffReassignmentPlan)
            .where(
                WorkspaceStaffReassignmentPlan.workspace_id == workspace_id,
                WorkspaceStaffReassignmentPlan.id == plan_id,
            )
            .with_for_update()
        )
        if plan is None:
            raise StaffReassignmentNotFoundError("Transferência não encontrada.")
        source = await self._source_membership(workspace_id, plan.source_user_id)
        self._assert_workspace_manager(actor_membership, source)
        await self._restore_workspace_plan(
            plan,
            expected_version=expected_version,
            reason=reason,
            actor_user_id=actor.user_id,
        )
        return await self._workspace_read(plan)

    async def preview_platform(
        self,
        *,
        actor: User,
        payload: StaffReassignmentPreviewRequest,
    ) -> StaffReassignmentPreviewRead:
        source, target = await self._platform_users(payload.source_user_id, payload.target_user_id)
        scopes = self._validate_scopes(payload.scopes, PLATFORM_SCOPES)
        self._assert_platform_scope_manager(actor, scopes)
        counts = {
            scope: await self._platform_count(source_user_id=source.id, scope=scope)
            for scope in scopes
        }
        return StaffReassignmentPreviewRead(
            source_user_id=source.id,
            source_name=source.name,
            target_user_id=target.id,
            target_name=target.name,
            counts=counts,
            total=sum(counts.values()),
        )

    async def apply_platform(
        self,
        *,
        actor: User,
        payload: StaffReassignmentCreate,
        idempotency_key: str,
    ) -> StaffReassignmentPlanRead:
        source, target = await self._platform_users(payload.source_user_id, payload.target_user_id)
        scopes = self._validate_scopes(payload.scopes, PLATFORM_SCOPES)
        self._assert_platform_scope_manager(actor, scopes)
        self._validate_apply_window(payload)
        idempotency_key = _normalize_idempotency_key(idempotency_key)
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="platform_staff_reassignment_idempotency",
            value=f"{actor.id}:{idempotency_key}",
        )
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="platform_staff_reassignment_source",
            value=str(source.id),
        )
        request_fingerprint = _fingerprint(payload)
        existing = await self.db.scalar(
            select(PlatformStaffReassignmentPlan).where(
                PlatformStaffReassignmentPlan.requested_by_user_id == actor.id,
                PlatformStaffReassignmentPlan.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise StaffReassignmentConflictError(
                    "A chave de idempotência já foi usada com outra solicitação."
                )
            self.operation_was_replayed = True
            return await self._platform_read(existing)

        now = _utcnow()
        plan_id = uuid4()
        tag = f"continuidade:{plan_id}"
        plan = PlatformStaffReassignmentPlan(
            id=plan_id,
            source_user_id=source.id,
            target_user_id=target.id,
            mode=payload.mode.value,
            status=StaffReassignmentStatus.applied.value,
            reason=payload.reason.strip(),
            tag_label=tag,
            scopes=scopes,
            assignment_summary={},
            conflict_count=0,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            applied_at=now,
            requested_by_user_id=actor.id,
            approved_by_user_id=actor.id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            version=1,
        )
        self.db.add(plan)
        summary: dict[str, int] = {}
        for scope in scopes:
            rows, assignment_field = await self._platform_rows(
                source_user_id=source.id,
                scope=scope,
            )
            summary[scope] = len(rows)
            for row in rows:
                snapshot = self._entity_snapshot(row)
                setattr(row, assignment_field, target.id)
                self._tag_entity(
                    row,
                    tag=tag,
                    plan_id=plan_id,
                    source_user_id=source.id,
                    target_user_id=target.id,
                    applied_at=now,
                )
                self.db.add(
                    PlatformStaffReassignmentItem(
                        plan_id=plan_id,
                        entity_type=scope,
                        entity_id=row.id,
                        assignment_field=assignment_field,
                        previous_user_id=source.id,
                        assigned_user_id=target.id,
                        status=StaffReassignmentItemStatus.applied.value,
                        tag_label=tag,
                        snapshot=snapshot,
                        created_at=now,
                    )
                )
        plan.assignment_summary = summary
        self.db.add(
            PlatformStaffReassignmentEvent(
                plan_id=plan_id,
                actor_user_id=actor.id,
                action="applied",
                details={
                    "mode": payload.mode.value,
                    "tagLabel": tag,
                    "scopes": scopes,
                    "assignmentSummary": summary,
                },
                occurred_at=now,
            )
        )
        await self.db.flush()
        await self.db.refresh(plan)
        return await self._platform_read(plan)

    async def list_platform(self, *, actor: User) -> list[StaffReassignmentPlanRead]:
        items, _ = await self.page_platform(actor=actor, limit=200, offset=0)
        return items

    async def page_platform(
        self,
        *,
        actor: User,
        limit: int,
        offset: int,
    ) -> tuple[list[StaffReassignmentPlanRead], int]:
        filters = []
        if not is_platform_root(actor):
            visible_scopes = self._platform_visible_scopes(actor)
            if len(visible_scopes) != 1:
                return [], 0
            filters.append(PlatformStaffReassignmentPlan.scopes == [next(iter(visible_scopes))])
        total = int(
            await self.db.scalar(
                select(func.count(PlatformStaffReassignmentPlan.id)).where(*filters)
            )
            or 0
        )
        plans = (
            await self.db.scalars(
                select(PlatformStaffReassignmentPlan)
                .where(*filters)
                .order_by(
                    PlatformStaffReassignmentPlan.created_at.desc(),
                    PlatformStaffReassignmentPlan.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [await self._platform_read(plan) for plan in plans], total

    async def get_platform(
        self,
        *,
        plan_id: UUID,
        actor: User,
    ) -> StaffReassignmentPlanRead:
        plan = await self.db.scalar(
            select(PlatformStaffReassignmentPlan).where(PlatformStaffReassignmentPlan.id == plan_id)
        )
        if plan is None:
            raise StaffReassignmentNotFoundError("Transferência não encontrada.")
        self._assert_platform_scope_manager(actor, list(plan.scopes or []))
        return await self._platform_read(plan)

    async def restore_platform(
        self,
        *,
        plan_id: UUID,
        actor: User,
        expected_version: int,
        reason: str,
    ) -> StaffReassignmentPlanRead:
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="platform_staff_reassignment_plan",
            value=str(plan_id),
        )
        plan = await self.db.scalar(
            select(PlatformStaffReassignmentPlan)
            .where(PlatformStaffReassignmentPlan.id == plan_id)
            .with_for_update()
        )
        if plan is None:
            raise StaffReassignmentNotFoundError("Transferência não encontrada.")
        self._assert_platform_scope_manager(actor, list(plan.scopes or []))
        await self._restore_platform_plan(
            plan,
            expected_version=expected_version,
            reason=reason,
            actor_user_id=actor.id,
        )
        return await self._platform_read(plan)

    async def restore_due_workspace(
        self,
        *,
        workspace_id: UUID,
        limit: int = 100,
    ) -> int:
        """Reverte planos vencidos de uma única conta com contexto RLS já aberto."""
        now = _utcnow()
        workspace_plans = (
            await self.db.scalars(
                select(WorkspaceStaffReassignmentPlan)
                .where(
                    WorkspaceStaffReassignmentPlan.workspace_id == workspace_id,
                    WorkspaceStaffReassignmentPlan.mode == StaffReassignmentMode.temporary.value,
                    WorkspaceStaffReassignmentPlan.status == StaffReassignmentStatus.applied.value,
                    WorkspaceStaffReassignmentPlan.ends_at <= now,
                )
                .order_by(
                    WorkspaceStaffReassignmentPlan.ends_at,
                    WorkspaceStaffReassignmentPlan.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        restored = 0
        for plan in workspace_plans:
            await self._restore_workspace_plan(
                plan,
                expected_version=plan.version,
                reason="Restauração automática ao término do afastamento.",
                actor_user_id=None,
            )
            restored += 1
        return restored

    async def restore_due_platform(self, *, limit: int = 100) -> int:
        """Reverte planos Blackbox vencidos sob contexto platform fail-closed."""
        now = _utcnow()
        platform_plans = (
            await self.db.scalars(
                select(PlatformStaffReassignmentPlan)
                .where(
                    PlatformStaffReassignmentPlan.mode == StaffReassignmentMode.temporary.value,
                    PlatformStaffReassignmentPlan.status == StaffReassignmentStatus.applied.value,
                    PlatformStaffReassignmentPlan.ends_at <= now,
                )
                .order_by(
                    PlatformStaffReassignmentPlan.ends_at,
                    PlatformStaffReassignmentPlan.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        restored = 0
        for plan in platform_plans:
            await self._restore_platform_plan(
                plan,
                expected_version=plan.version,
                reason="Restauração automática ao término do afastamento.",
                actor_user_id=None,
            )
            restored += 1
        return restored

    async def _restore_workspace_plan(
        self,
        plan: WorkspaceStaffReassignmentPlan,
        *,
        expected_version: int,
        reason: str,
        actor_user_id: UUID | None,
    ) -> None:
        self._assert_restorable(plan, expected_version)
        items = (
            await self.db.scalars(
                select(WorkspaceStaffReassignmentItem).where(
                    WorkspaceStaffReassignmentItem.plan_id == plan.id,
                    WorkspaceStaffReassignmentItem.workspace_id == plan.workspace_id,
                    WorkspaceStaffReassignmentItem.status
                    == StaffReassignmentItemStatus.applied.value,
                )
            )
        ).all()
        source_is_eligible = await self._workspace_restore_source_is_eligible(
            plan.workspace_id,
            plan.source_user_id,
        )
        conflicts = 0
        now = _utcnow()
        for item in items:
            entity = await self._workspace_entity(
                workspace_id=plan.workspace_id,
                scope=item.entity_type,
                entity_id=item.entity_id,
            )
            if (
                not source_is_eligible
                or entity is None
                or getattr(entity, item.assignment_field) != item.assigned_user_id
            ):
                item.status = StaffReassignmentItemStatus.conflict.value
                conflicts += 1
                continue
            try:
                async with self.db.begin_nested():
                    await self._assign_workspace_entity(
                        entity=entity,
                        workspace_id=plan.workspace_id,
                        scope=item.entity_type,
                        target_user_id=item.previous_user_id,
                        actor_user_id=actor_user_id,
                        correlation_id=(
                            f"staff-reassignment:{plan.id}:restore:"
                            f"{item.entity_type}:{item.entity_id}"
                        ),
                    )
            except (
                HTTPException,
                IntegrityError,
                StaffReassignmentConflictError,
            ):
                item.status = StaffReassignmentItemStatus.conflict.value
                conflicts += 1
                continue
            item.status = StaffReassignmentItemStatus.restored.value
            item.restored_at = now
        plan.conflict_count = conflicts
        plan.status = (
            StaffReassignmentStatus.partially_restored.value
            if conflicts
            else StaffReassignmentStatus.restored.value
        )
        plan.restored_at = now
        plan.restore_reason = reason.strip()
        plan.version += 1
        self.db.add(
            WorkspaceStaffReassignmentEvent(
                workspace_id=plan.workspace_id,
                plan_id=plan.id,
                actor_user_id=actor_user_id,
                action=("partially_restored" if conflicts else "restored"),
                details={
                    "automatic": actor_user_id is None,
                    "reason": plan.restore_reason,
                    "sourceEligible": source_is_eligible,
                    "conflictCount": conflicts,
                    "restoredCount": len(items) - conflicts,
                },
                occurred_at=now,
            )
        )
        await self.db.flush()
        await self.db.refresh(plan)

    async def _restore_platform_plan(
        self,
        plan: PlatformStaffReassignmentPlan,
        *,
        expected_version: int,
        reason: str,
        actor_user_id: UUID | None,
    ) -> None:
        self._assert_restorable(plan, expected_version)
        items = (
            await self.db.scalars(
                select(PlatformStaffReassignmentItem).where(
                    PlatformStaffReassignmentItem.plan_id == plan.id,
                    PlatformStaffReassignmentItem.status
                    == StaffReassignmentItemStatus.applied.value,
                )
            )
        ).all()
        source_is_eligible = await self._platform_restore_source_is_eligible(plan.source_user_id)
        conflicts = 0
        now = _utcnow()
        for item in items:
            entity = await self._platform_entity(
                scope=item.entity_type,
                entity_id=item.entity_id,
            )
            if (
                not source_is_eligible
                or entity is None
                or getattr(entity, item.assignment_field) != item.assigned_user_id
            ):
                item.status = StaffReassignmentItemStatus.conflict.value
                conflicts += 1
                continue
            setattr(entity, item.assignment_field, item.previous_user_id)
            item.status = StaffReassignmentItemStatus.restored.value
            item.restored_at = now
        plan.conflict_count = conflicts
        plan.status = (
            StaffReassignmentStatus.partially_restored.value
            if conflicts
            else StaffReassignmentStatus.restored.value
        )
        plan.restored_at = now
        plan.restore_reason = reason.strip()
        plan.version += 1
        self.db.add(
            PlatformStaffReassignmentEvent(
                plan_id=plan.id,
                actor_user_id=actor_user_id,
                action=("partially_restored" if conflicts else "restored"),
                details={
                    "automatic": actor_user_id is None,
                    "reason": plan.restore_reason,
                    "sourceEligible": source_is_eligible,
                    "conflictCount": conflicts,
                    "restoredCount": len(items) - conflicts,
                },
                occurred_at=now,
            )
        )
        await self.db.flush()
        await self.db.refresh(plan)

    @staticmethod
    def _assert_restorable(plan: Any, expected_version: int) -> None:
        if plan.version != expected_version:
            raise StaffReassignmentConflictError("A transferência foi alterada em outra sessão.")
        if plan.mode != StaffReassignmentMode.temporary.value:
            raise StaffReassignmentValidationError(
                "Somente uma transferência temporária pode ser restaurada."
            )
        if plan.status != StaffReassignmentStatus.applied.value:
            raise StaffReassignmentConflictError("A transferência não está mais ativa.")

    async def _workspace_authorization(
        self,
        *,
        workspace_id: UUID,
        actor: AuthContext,
        source_user_id: UUID,
        target_user_id: UUID,
    ) -> tuple[WorkspaceMembership, WorkspaceMembership, WorkspaceMembership]:
        actor_membership = await self._actor_membership(workspace_id, actor)
        source = await self._source_membership(workspace_id, source_user_id)
        target = await self._active_membership(workspace_id, target_user_id)
        if source.user_id == target.user_id:
            raise StaffReassignmentValidationError("A origem e o destino devem ser diferentes.")
        return actor_membership, source, target

    async def _actor_membership(
        self, workspace_id: UUID, actor: AuthContext
    ) -> WorkspaceMembership:
        if actor.surface != "workspace" or actor.is_support_access:
            raise StaffReassignmentAuthorizationError(
                "A continuidade do workspace exige uma identidade normal da conta."
            )
        membership = await self._active_membership(workspace_id, actor.user_id)
        if actor.membership_id is not None and membership.id != actor.membership_id:
            raise StaffReassignmentAuthorizationError("A sessão usa uma associação desatualizada.")
        return membership

    async def _active_membership(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMembership:
        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.agent_type == AgentType.human,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if membership is None:
            raise StaffReassignmentNotFoundError("Funcionário ativo não encontrado nesta conta.")
        return membership

    async def _source_membership(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMembership:
        """Aceita a origem histórica suspensa, mas nunca um convite não aceito."""
        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status.in_(
                    {MembershipStatus.active, MembershipStatus.suspended}
                ),
                User.agent_type == AgentType.human,
            )
        )
        if membership is None:
            raise StaffReassignmentNotFoundError(
                "Funcionário de origem não encontrado nesta conta."
            )
        return membership

    async def _workspace_restore_source_is_eligible(
        self,
        workspace_id: UUID,
        user_id: UUID,
    ) -> bool:
        try:
            await self._active_membership(workspace_id, user_id)
        except StaffReassignmentNotFoundError:
            return False
        return True

    @staticmethod
    def _assert_workspace_manager(actor: WorkspaceMembership, source: WorkspaceMembership) -> None:
        if actor.role is WorkspaceRole.workspace_owner:
            return
        if (
            actor.role is WorkspaceRole.workspace_admin
            and source.role is not WorkspaceRole.workspace_owner
        ):
            return
        if actor.role is WorkspaceRole.legal_manager and source.role in {
            WorkspaceRole.operator,
            WorkspaceRole.viewer,
        }:
            return
        raise StaffReassignmentAuthorizationError(
            "Somente o administrador master ou um gestor autorizado pode transferir esta carteira."
        )

    async def _platform_users(
        self, source_user_id: UUID, target_user_id: UUID
    ) -> tuple[User, User]:
        source = await self.db.get(User, source_user_id)
        target = await self.db.get(User, target_user_id)
        if source is None or not (source.is_platform_admin or source.is_internal_staff):
            raise StaffReassignmentNotFoundError("Funcionário de origem do SaaS não encontrado.")
        if not self._platform_user_is_eligible(target):
            raise StaffReassignmentNotFoundError(
                "Funcionário ativo de destino do SaaS não encontrado."
            )
        if source.id == target.id:
            raise StaffReassignmentValidationError("A origem e o destino devem ser diferentes.")
        return source, target

    @staticmethod
    def _platform_user_is_eligible(user: User | None) -> bool:
        return bool(
            user is not None
            and user.is_active
            and user.deleted_at is None
            and (user.is_platform_admin or user.is_internal_staff)
            and (user.is_platform_admin or user.platform_access_active)
        )

    async def _platform_restore_source_is_eligible(self, user_id: UUID) -> bool:
        return self._platform_user_is_eligible(await self.db.get(User, user_id))

    @staticmethod
    def _assert_platform_scope_manager(actor: User, scopes: list[str]) -> None:
        allowed = StaffReassignmentService._platform_visible_scopes(actor)
        invalid = sorted(set(scopes) - allowed)
        if invalid or not scopes:
            raise StaffReassignmentAuthorizationError(
                "Este gestor somente pode redistribuir dados da própria área."
            )

    @staticmethod
    def _platform_visible_scopes(actor: User) -> set[str]:
        if is_platform_root(actor):
            return set(PLATFORM_SCOPES)
        package = (actor.platform_role_package or "").strip()
        return {
            "commercial_manager": {"growth_prospects"},
            "technical_support": {"support_tickets"},
            "engineering": {"support_tickets"},
        }.get(package, set())

    @staticmethod
    def _platform_scopes_are_visible(actor: User, scopes: list[str]) -> bool:
        return bool(scopes) and set(scopes).issubset(
            StaffReassignmentService._platform_visible_scopes(actor)
        )

    @staticmethod
    def _validate_scopes(scopes: list[str], allowed: frozenset[str]) -> list[str]:
        normalized = list(dict.fromkeys(scopes))
        invalid = sorted(set(normalized) - allowed)
        if invalid:
            raise StaffReassignmentValidationError(
                f"Escopos não autorizados: {', '.join(invalid)}."
            )
        if not normalized:
            raise StaffReassignmentValidationError("Selecione ao menos um conjunto de dados.")
        return normalized

    @staticmethod
    def _validate_apply_window(payload: StaffReassignmentCreate) -> None:
        now = _utcnow()
        if payload.starts_at > now + timedelta(minutes=5):
            raise StaffReassignmentValidationError(
                "A transferência imediata não pode começar no futuro."
            )

    async def _workspace_count(
        self, *, workspace_id: UUID, source_user_id: UUID, scope: str
    ) -> int:
        query = self._workspace_query(
            workspace_id=workspace_id,
            source_user_id=source_user_id,
            scope=scope,
            count=True,
        )
        return int((await self.db.scalar(query)) or 0)

    async def _workspace_rows(
        self, *, workspace_id: UUID, source_user_id: UUID, scope: str
    ) -> tuple[list[Any], str]:
        query, assignment_field = self._workspace_query(
            workspace_id=workspace_id,
            source_user_id=source_user_id,
            scope=scope,
            count=False,
        )
        query = query.with_for_update()
        return list((await self.db.scalars(query)).all()), assignment_field

    def _workspace_query(
        self,
        *,
        workspace_id: UUID,
        source_user_id: UUID,
        scope: str,
        count: bool,
    ) -> Select | tuple[Select, str]:
        specs = {
            "matters": (
                Matter,
                Matter.responsible_human_id,
                "responsible_human_id",
                Matter.workspace_id == workspace_id,
            ),
            "meetings": (
                Meeting,
                Meeting.responsible_user_id,
                "responsible_user_id",
                Meeting.workspace_id == workspace_id,
            ),
            "booking_links": (
                BookingLink,
                BookingLink.responsible_user_id,
                "responsible_user_id",
                BookingLink.workspace_id == workspace_id,
            ),
            "omnichannel_threads": (
                OmnichannelThread,
                OmnichannelThread.assigned_human_id,
                "assigned_human_id",
                OmnichannelThread.workspace_id == workspace_id,
            ),
            "whatsapp_contacts": (
                WhatsAppContact,
                WhatsAppContact.assigned_user_id,
                "assigned_user_id",
                WhatsAppContact.workspace_id == workspace_id,
            ),
            "social_conversations": (
                MetaSocialConversation,
                MetaSocialConversation.assigned_user_id,
                "assigned_user_id",
                MetaSocialConversation.workspace_id == workspace_id,
            ),
        }
        if scope == "tasks":
            base = select(func.count(Task.id)) if count else select(Task)
            query = base.join(Matter, Matter.id == Task.matter_id).where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
                Task.assigned_to == source_user_id,
                Task.status != TaskStatus.done,
                Task.cancelled_at.is_(None),
            )
            return query if count else (query, "assigned_to")
        model, assignment_column, assignment_field, tenant_clause = specs[scope]
        base = select(func.count(model.id)) if count else select(model)
        predicates = [tenant_clause, assignment_column == source_user_id]
        if scope == "meetings":
            predicates.append(Meeting.status == MeetingStatus.scheduled)
        query = base.where(*predicates)
        return query if count else (query, assignment_field)

    async def _assign_workspace_entity(
        self,
        *,
        entity: Any,
        workspace_id: UUID,
        scope: str,
        target_user_id: UUID,
        actor_user_id: UUID | None,
        correlation_id: str,
    ) -> Any:
        """Move an operational owner through the domain command boundary.

        Tasks and meetings have canonical participant, version, audit and
        notification/reminder invariants.  Updating their compatibility
        foreign keys directly would leave active participants and outbox rows
        stale.  Other legacy assignment surfaces do not yet expose an
        equivalent command and retain their narrow scalar update.
        """

        if scope == "tasks":
            existing_target_role = await self.db.scalar(
                select(TaskParticipant.role).where(
                    TaskParticipant.task_id == entity.id,
                    TaskParticipant.user_id == target_user_id,
                    TaskParticipant.active.is_(True),
                )
            )
            if existing_target_role is not None:
                raise StaffReassignmentConflictError(
                    "O novo owner já participa da tarefa em outro papel."
                )
            updated = await TaskService(self.db).update(
                entity.id,
                TaskUpdate(
                    expected_version=entity.version,
                    assigned_to=target_user_id,
                ),
                workspace_id,
                # This is an internal domain command reached only after
                # StaffReassignmentService has authorized the manager and
                # locked the selected workspace rows. It does not elevate the
                # caller's session or expose a public bypass; actor_user_id is
                # preserved so TaskEvent/KlmAuditEvent retain real authorship.
                context=TaskMutationContext(
                    actor_user_id=actor_user_id,
                    correlation_id=correlation_id[:120],
                    session_type="system",
                ),
            )
            if updated is None:  # pragma: no cover - locked row was just selected
                raise StaffReassignmentNotFoundError("Tarefa indisponível durante a transferência.")
            return updated
        if scope == "meetings":
            return await MeetingService(self.db).update_for_workspace(
                workspace_id,
                entity.id,
                MeetingUpdate(
                    expected_version=entity.version,
                    responsible_user_id=target_user_id,
                ),
                actor_user_id=actor_user_id,
                correlation_id=correlation_id[:120],
            )
        setattr(
            entity,
            {
                "matters": "responsible_human_id",
                "booking_links": "responsible_user_id",
                "omnichannel_threads": "assigned_human_id",
                "whatsapp_contacts": "assigned_user_id",
                "social_conversations": "assigned_user_id",
            }[scope],
            target_user_id,
        )
        return entity

    async def _platform_count(self, *, source_user_id: UUID, scope: str) -> int:
        query, _ = self._platform_query(source_user_id=source_user_id, scope=scope, count=True)
        return int((await self.db.scalar(query)) or 0)

    async def _platform_rows(self, *, source_user_id: UUID, scope: str) -> tuple[list[Any], str]:
        query, assignment_field = self._platform_query(
            source_user_id=source_user_id, scope=scope, count=False
        )
        query = query.with_for_update()
        return list((await self.db.scalars(query)).all()), assignment_field

    @staticmethod
    def _platform_query(*, source_user_id: UUID, scope: str, count: bool) -> tuple[Select, str]:
        specs = {
            "support_tickets": (
                SupportTicket,
                SupportTicket.assigned_to_user_id,
                "assigned_to_user_id",
            ),
            "growth_prospects": (
                GrowthProspect,
                GrowthProspect.owner_user_id,
                "owner_user_id",
            ),
        }
        model, assignment_column, assignment_field = specs[scope]
        base = select(func.count(model.id)) if count else select(model)
        return base.where(assignment_column == source_user_id), assignment_field

    async def _workspace_entity(
        self, *, workspace_id: UUID, scope: str, entity_id: UUID
    ) -> Any | None:
        model = {
            "matters": Matter,
            "tasks": Task,
            "meetings": Meeting,
            "booking_links": BookingLink,
            "omnichannel_threads": OmnichannelThread,
            "whatsapp_contacts": WhatsAppContact,
            "social_conversations": MetaSocialConversation,
        }[scope]
        entity = await self.db.get(model, entity_id)
        if entity is None:
            return None
        if scope == "tasks":
            matter_workspace_id = await self.db.scalar(
                select(Matter.workspace_id).where(Matter.id == entity.matter_id)
            )
            return entity if matter_workspace_id == workspace_id else None
        return entity if getattr(entity, "workspace_id", None) == workspace_id else None

    async def _platform_entity(self, *, scope: str, entity_id: UUID) -> Any | None:
        model = {
            "support_tickets": SupportTicket,
            "growth_prospects": GrowthProspect,
        }[scope]
        return await self.db.get(model, entity_id)

    @staticmethod
    def _tag_entity(
        entity: Any,
        *,
        tag: str,
        plan_id: UUID,
        source_user_id: UUID,
        target_user_id: UUID,
        applied_at: datetime,
    ) -> None:
        if isinstance(entity, Matter):
            entity.tags = list(dict.fromkeys([*(entity.tags or []), tag]))
            return
        event = {
            "tag": tag,
            "planId": str(plan_id),
            "sourceUserId": str(source_user_id),
            "targetUserId": str(target_user_id),
            "appliedAt": applied_at.isoformat(),
        }
        if isinstance(entity, OmnichannelThread):
            metadata = dict(entity.thread_metadata or {})
            metadata["continuityTransfers"] = [
                *(metadata.get("continuityTransfers") or []),
                event,
            ]
            entity.thread_metadata = metadata
        elif isinstance(entity, GrowthProspect):
            metadata = dict(entity.metadata_json or {})
            metadata["continuityTransfers"] = [
                *(metadata.get("continuityTransfers") or []),
                event,
            ]
            entity.metadata_json = metadata

    @staticmethod
    def _entity_snapshot(entity: Any) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        resource_version = getattr(entity, "version", None)
        if resource_version is not None:
            snapshot["resourceVersionBefore"] = resource_version
        for attribute in (
            "title",
            "public_number",
            "nome_escritorio",
            "participant_name",
            "name",
            "demand_type",
        ):
            value = getattr(entity, attribute, None)
            if value:
                snapshot["label"] = str(value)[:300]
                break
        return snapshot

    async def _workspace_read(
        self, plan: WorkspaceStaffReassignmentPlan
    ) -> StaffReassignmentPlanRead:
        source = await self.db.get(User, plan.source_user_id)
        target = await self.db.get(User, plan.target_user_id)
        events = (
            await self.db.scalars(
                select(WorkspaceStaffReassignmentEvent)
                .where(
                    WorkspaceStaffReassignmentEvent.workspace_id == plan.workspace_id,
                    WorkspaceStaffReassignmentEvent.plan_id == plan.id,
                )
                .order_by(
                    WorkspaceStaffReassignmentEvent.occurred_at,
                    WorkspaceStaffReassignmentEvent.id,
                )
            )
        ).all()
        return self._plan_read(
            plan,
            surface="workspace",
            workspace_id=plan.workspace_id,
            source_name=source.name if source else "Usuário indisponível",
            target_name=target.name if target else "Usuário indisponível",
            audit_events=[
                StaffReassignmentAuditEventRead.model_validate(event) for event in events
            ],
        )

    async def _platform_read(
        self, plan: PlatformStaffReassignmentPlan
    ) -> StaffReassignmentPlanRead:
        source = await self.db.get(User, plan.source_user_id)
        target = await self.db.get(User, plan.target_user_id)
        events = (
            await self.db.scalars(
                select(PlatformStaffReassignmentEvent)
                .where(PlatformStaffReassignmentEvent.plan_id == plan.id)
                .order_by(
                    PlatformStaffReassignmentEvent.occurred_at,
                    PlatformStaffReassignmentEvent.id,
                )
            )
        ).all()
        return self._plan_read(
            plan,
            surface="platform",
            workspace_id=None,
            source_name=source.name if source else "Usuário indisponível",
            target_name=target.name if target else "Usuário indisponível",
            audit_events=[
                StaffReassignmentAuditEventRead.model_validate(event) for event in events
            ],
        )

    @staticmethod
    def _plan_read(
        plan: Any,
        *,
        surface: str,
        workspace_id: UUID | None,
        source_name: str,
        target_name: str,
        audit_events: list[StaffReassignmentAuditEventRead],
    ) -> StaffReassignmentPlanRead:
        return StaffReassignmentPlanRead(
            id=plan.id,
            surface=surface,
            workspace_id=workspace_id,
            source_user_id=plan.source_user_id,
            source_name=source_name,
            target_user_id=plan.target_user_id,
            target_name=target_name,
            mode=plan.mode,
            status=plan.status,
            reason=plan.reason,
            tag_label=plan.tag_label,
            scopes=list(plan.scopes or []),
            assignment_summary=dict(plan.assignment_summary or {}),
            conflict_count=plan.conflict_count,
            starts_at=plan.starts_at,
            ends_at=plan.ends_at,
            applied_at=plan.applied_at,
            restored_at=plan.restored_at,
            restore_reason=plan.restore_reason,
            requested_by_user_id=plan.requested_by_user_id,
            approved_by_user_id=plan.approved_by_user_id,
            version=plan.version,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            audit_events=audit_events,
        )


__all__ = [
    "StaffReassignmentAuthorizationError",
    "StaffReassignmentConflictError",
    "StaffReassignmentError",
    "StaffReassignmentNotFoundError",
    "StaffReassignmentService",
    "StaffReassignmentValidationError",
]
