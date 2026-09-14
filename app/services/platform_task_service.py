"""Transactional commands for employee-owned Blackbox tasks.

The service never commits.  Each successful mutation flushes the task and one
append-only ``PlatformTaskEvent`` in the same transaction; HTTP endpoints own
the commit boundary and add the canonical platform audit event.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import AuthContext
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.db.platform_support import set_platform_support_context
from app.models.growth_crm import GrowthProspect
from app.models.platform_incident import PlatformIncident
from app.models.platform_organization import PlatformTeam, PlatformTeamMembership
from app.models.platform_release import PlatformRelease
from app.models.platform_task import (
    PlatformTask,
    PlatformTaskEvent,
    PlatformTaskEventType,
    PlatformTaskParticipant,
    PlatformTaskParticipantRole,
    PlatformTaskParticipantState,
    PlatformTaskReviewRound,
    PlatformTaskReviewRoundState,
    PlatformTaskReviewState,
    PlatformTaskStatus,
)
from app.models.support_ticket import SupportTicket
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.platform_task import (
    PlatformTaskAssignmentsReplace,
    PlatformTaskCommand,
    PlatformTaskCommandRequest,
    PlatformTaskCommentCreate,
    PlatformTaskCreate,
    PlatformTaskParticipantStateUpdate,
    PlatformTaskParticipantWrite,
    PlatformTaskUpdate,
)
from app.services.platform_audit_event_service import (
    changed_paths,
    sanitize_audit_text,
    sanitize_audit_value,
)


class PlatformTaskError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class PlatformTaskMutationContext:
    actor: User
    correlation_id: str
    administrative_session_id: UUID | None = None
    session_type: str = "normal"

    @classmethod
    def for_actor(
        cls,
        actor: User,
        *,
        correlation_id: str | None = None,
        auth_context: AuthContext | None = None,
    ) -> PlatformTaskMutationContext:
        return cls(
            actor=actor,
            correlation_id=(correlation_id or "").strip() or f"platform-task:{uuid4()}",
            administrative_session_id=(
                auth_context.administrative_session_id if auth_context else None
            ),
            session_type=(
                auth_context.administrative_session_type if auth_context is not None else "normal"
            ),
        )


@dataclass(frozen=True)
class PlatformTaskMutationResult:
    """Task plus the snapshots captured inside its mutation lock.

    The HTTP layer must never pre-read the task to build the canonical audit
    diff.  Returning this envelope keeps the domain event and the platform
    audit event anchored to the same locked state.
    """

    task: PlatformTask
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    workspace_id: UUID | None

    def __getattr__(self, name: str) -> Any:
        """Temporary source compatibility for direct service consumers."""

        return getattr(self.task, name)


def has_platform_task_permission(actor: User | None, action: str) -> bool:
    """Use the canonical package/grant/denial resolver for this catalogued module.

    A raw grant is never an authority source on its own: the actor must have a
    valid non-root package (or be the real ``platform_owner``) and every custom
    grant/denial is normalized against ``PLATFORM_PERMISSION_CATALOG`` by the
    shared resolver.
    """

    return has_platform_permission(actor, "platform_tasks", action)


def required_platform_task_command_permission(command: PlatformTaskCommand) -> str:
    """Return the single RBAC action that authorizes one typed command.

    The HTTP dependency and the transactional service deliberately share this
    mapper so PostgreSQL receives the same least-privilege action that the
    application authorized.  Picking the first permission an actor happens to
    have would make RLS decisions depend on role ordering rather than on the
    requested command.
    """

    if command in {
        PlatformTaskCommand.approve,
        PlatformTaskCommand.request_changes,
        PlatformTaskCommand.reject,
    }:
        return "review"
    if command in {
        PlatformTaskCommand.complete,
        PlatformTaskCommand.reopen,
        PlatformTaskCommand.cancel,
    }:
        return "complete"
    return "edit"


def _now() -> datetime:
    return datetime.now(UTC)


class PlatformTaskService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._managed_members_cache: dict[UUID, set[UUID]] = {}

    @staticmethod
    def _options():
        return (
            selectinload(PlatformTask.participants),
            selectinload(PlatformTask.review_rounds),
        )

    @staticmethod
    def _require(actor: User, action: str) -> None:
        if not has_platform_task_permission(actor, action):
            raise PlatformTaskError(
                "PLATFORM-TASK-PERMISSION-DENIED",
                "Seu pacote de acesso não autoriza esta operação.",
                status_code=403,
            )

    @staticmethod
    def _require_version(task: PlatformTask, expected_version: int) -> None:
        if task.version != expected_version:
            raise PlatformTaskError(
                "PLATFORM-TASK-VERSION-CONFLICT",
                "A tarefa mudou desde a leitura. Atualize os dados e tente novamente.",
                status_code=409,
            )

    @staticmethod
    def _require_reason(value: str | None, *, action: str) -> str:
        reason = (value or "").strip()
        if len(reason) < 8:
            raise PlatformTaskError(
                "PLATFORM-TASK-REASON-REQUIRED",
                f"O comando {action} exige motivo com pelo menos 8 caracteres.",
            )
        return reason

    @staticmethod
    def _active_participants(task: PlatformTask) -> list[PlatformTaskParticipant]:
        return [item for item in task.participants if item.active]

    @staticmethod
    def _has_platform_scope(actor: User) -> bool:
        return is_platform_root(actor) or actor.platform_role_package == "platform_admin"

    async def _is_manager_for_task(self, actor: User, task: PlatformTask) -> bool:
        if actor.platform_role_package == "platform_admin":
            return True
        managed_members = await self._managed_member_ids(actor)
        # Team authority follows the task owner.  Managing an observer,
        # executor or reviewer must not grant edit/assignment/cancel powers
        # over work owned by somebody outside the manager's team.
        return task.owner_user_id in managed_members

    @staticmethod
    def _participant_role(
        actor: User,
        task: PlatformTask,
    ) -> PlatformTaskParticipantRole | None:
        return next(
            (
                item.role
                for item in PlatformTaskService._active_participants(task)
                if item.user_id == actor.id
            ),
            None,
        )

    async def _assert_operational_actor(self, actor: User, task: PlatformTask) -> None:
        """Only the owner or an executor may perform the task's work."""

        if is_platform_root(actor) or actor.id == task.owner_user_id:
            return
        if self._participant_role(actor, task) == PlatformTaskParticipantRole.assignee:
            return
        raise PlatformTaskError(
            "PLATFORM-TASK-EXECUTOR-ONLY",
            "Somente o responsável ou um executor ativo pode executar esta ação.",
            status_code=403,
        )

    async def _assert_owner_or_manager(self, actor: User, task: PlatformTask) -> None:
        if (
            is_platform_root(actor)
            or actor.id == task.owner_user_id
            or await self._is_manager_for_task(actor, task)
        ):
            return
        raise PlatformTaskError(
            "PLATFORM-TASK-OWNER-MANAGER-ONLY",
            "Somente o responsável, seu gestor ou o Super Admin pode executar esta ação.",
            status_code=403,
        )

    async def _assert_comment_actor(self, actor: User, task: PlatformTask) -> None:
        """Observers are read-only; reviewers may comment on their review."""

        if is_platform_root(actor) or actor.id in {
            task.owner_user_id,
            task.reviewer_user_id,
        }:
            return
        if self._participant_role(actor, task) == PlatformTaskParticipantRole.assignee:
            return
        if await self._is_manager_for_task(actor, task):
            return
        raise PlatformTaskError(
            "PLATFORM-TASK-COMMENT-DENIED",
            "Observadores possuem acesso somente para leitura.",
            status_code=403,
        )

    async def _assert_participant_state_actor(
        self,
        *,
        actor: User,
        task: PlatformTask,
        target_user_id: UUID,
    ) -> None:
        if actor.id == target_user_id:
            return
        await self._assert_owner_or_manager(actor, task)

    @staticmethod
    def _may_override_assigned_reviewer(actor: User) -> bool:
        """Keep review bypass exclusive to the platform root.

        ``platform_admin`` may have platform-wide visibility, but that does not
        make it equivalent to ``platform_owner`` for segregation-of-duties
        decisions.  The root override remains explicit, reasoned and audited.
        """

        return is_platform_root(actor)

    async def _managed_member_ids(self, actor: User) -> set[UUID]:
        cached = self._managed_members_cache.get(actor.id)
        if cached is not None:
            return cached
        if not has_platform_permission(actor, "dashboard", "team_view"):
            self._managed_members_cache[actor.id] = set()
            return set()
        now = _now()
        team_ids = select(PlatformTeam.id).where(
            PlatformTeam.manager_user_id == actor.id,
            PlatformTeam.active.is_(True),
        )
        managed = set(
            (
                await self.db.scalars(
                    select(PlatformTeamMembership.user_id).where(
                        PlatformTeamMembership.team_id.in_(team_ids),
                        PlatformTeamMembership.active.is_(True),
                        PlatformTeamMembership.starts_at <= now,
                        or_(
                            PlatformTeamMembership.ends_at.is_(None),
                            PlatformTeamMembership.ends_at > now,
                        ),
                    )
                )
            ).all()
        )
        self._managed_members_cache[actor.id] = managed
        return managed

    @staticmethod
    def _require_link_permission(
        actor: User,
        *,
        allowed: tuple[tuple[str, str], ...],
        code: str,
        message: str,
    ) -> None:
        if is_platform_root(actor) or any(
            has_platform_permission(actor, module, action) for module, action in allowed
        ):
            return
        raise PlatformTaskError(code, message, status_code=403)

    @asynccontextmanager
    async def _support_ticket_lookup_context(
        self,
        *,
        actor: User,
    ) -> AsyncIterator[None]:
        """Temporarily open the narrow support:view RLS context.

        Platform-task routes carry a ``platform_tasks:*`` database context,
        which intentionally cannot read tenant support rows protected by FORCE
        RLS.  A ticket link therefore requires the independently authorized
        support permission, switches only for the lookup, and restores all
        three original transaction-local settings even when validation fails.
        """

        if not (is_platform_root(actor) or has_platform_permission(actor, "support", "view")):
            raise PlatformTaskError(
                "PLATFORM-TASK-TICKET-PERMISSION-DENIED",
                "Vincular um ticket exige permissão de leitura no suporte.",
                status_code=403,
            )
        if self.db.get_bind().dialect.name != "postgresql":
            yield
            return

        original = (
            await self.db.execute(
                text(
                    "SELECT "
                    "current_setting('app.current_platform_scope', true), "
                    "current_setting('app.current_platform_actor_id', true), "
                    "current_setting('app.current_platform_action', true)"
                )
            )
        ).one()
        await set_platform_support_context(
            self.db,
            actor_user_id=actor.id,
            action="view",
        )
        try:
            yield
        finally:
            await self.db.execute(
                text(
                    "SELECT "
                    "set_config('app.current_platform_scope', :scope, true), "
                    "set_config('app.current_platform_actor_id', :actor, true), "
                    "set_config('app.current_platform_action', :action, true)"
                ),
                {
                    "scope": original[0] or "",
                    "actor": original[1] or "",
                    "action": original[2] or "",
                },
            )

    @staticmethod
    def _involved_user_ids(task: PlatformTask) -> set[UUID]:
        values = {task.owner_user_id}
        if task.reviewer_user_id is not None:
            values.add(task.reviewer_user_id)
        values.update(item.user_id for item in PlatformTaskService._active_participants(task))
        return values

    @staticmethod
    def _involved_clause(user_ids: set[UUID]):
        return or_(
            PlatformTask.owner_user_id.in_(user_ids),
            PlatformTask.reviewer_user_id.in_(user_ids),
            exists().where(
                PlatformTaskParticipant.task_id == PlatformTask.id,
                PlatformTaskParticipant.user_id.in_(user_ids),
                PlatformTaskParticipant.active.is_(True),
            ),
        )

    async def _assert_task_access(self, actor: User, task: PlatformTask) -> None:
        if self._has_platform_scope(actor) or actor.id in self._involved_user_ids(task):
            return
        managed_members = await self._managed_member_ids(actor)
        if managed_members.intersection(self._involved_user_ids(task)):
            return
        raise PlatformTaskError(
            "PLATFORM-TASK-ROW-DENIED",
            "Esta tarefa não pertence ao seu escopo pessoal ou de equipe.",
            status_code=403,
        )

    async def _refresh_generated_timestamps(self, task: PlatformTask) -> PlatformTask:
        """Load SQL-generated ``updated_at`` before leaving async command scope."""

        await self.db.refresh(task, attribute_names=["updated_at"])
        return task

    @classmethod
    def snapshot(cls, task: PlatformTask) -> dict[str, Any]:
        participants = sorted(
            cls._active_participants(task),
            key=lambda item: (item.role.value, item.label_snapshot.casefold(), str(item.user_id)),
        )
        return sanitize_audit_value(
            {
                "title": task.title,
                "description": task.description,
                "status": task.status.value,
                "priority": task.priority.value,
                "dueAt": task.due_at,
                "ownerDueAt": task.owner_due_at,
                "reviewDueAt": task.review_due_at,
                "reviewRequired": task.review_required,
                "reviewState": task.review_state.value,
                "reviewRoundNumber": task.review_round_number,
                "ownerUserId": task.owner_user_id,
                "reviewerUserId": task.reviewer_user_id,
                "workspaceId": task.workspace_id,
                "linkedTicketId": task.linked_ticket_id,
                "linkedIncidentId": task.linked_incident_id,
                "linkedLeadId": task.linked_lead_id,
                "linkedReleaseId": task.linked_release_id,
                "blockedReason": task.blocked_reason,
                "cancelledAt": task.cancelled_at,
                "participants": [
                    {
                        "userId": item.user_id,
                        "label": item.label_snapshot,
                        "role": item.role.value,
                        "personalDueAt": item.personal_due_at,
                        "state": item.state.value,
                        "requiredForCompletion": item.required_for_completion,
                    }
                    for item in participants
                ],
                "version": task.version,
            }
        )

    async def _event(
        self,
        task: PlatformTask,
        *,
        context: PlatformTaskMutationContext,
        event_type: PlatformTaskEventType,
        before: dict | None,
        after: dict | None,
        reason: str,
        comment: str | None = None,
    ) -> PlatformTaskEvent:
        safe_before = sanitize_audit_value(before)
        safe_after = sanitize_audit_value(after)
        event = PlatformTaskEvent(
            task_id=task.id,
            actor_user_id=context.actor.id,
            actor_label_snapshot=sanitize_audit_text(context.actor.name),
            actor_role_snapshot=context.actor.platform_role_package or "unknown",
            is_platform_root=is_platform_root(context.actor),
            event_type=event_type,
            before_data=safe_before,
            after_data=safe_after,
            changed_paths=changed_paths(safe_before, safe_after),
            comment=sanitize_audit_text(comment) if comment is not None else None,
            reason=sanitize_audit_text(reason),
            correlation_id=context.correlation_id,
            administrative_session_id=context.administrative_session_id,
            session_type=context.session_type,
            resource_version_before=(before or {}).get("version"),
            resource_version_after=(after or {}).get("version"),
            occurred_at=_now(),
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def get(
        self,
        *,
        actor: User,
        task_id: UUID,
        lock: bool = False,
    ) -> PlatformTask:
        self._require(actor, "view")
        task = await self._load(task_id=task_id, lock=lock)
        await self._assert_task_access(actor, task)
        return task

    async def _load(self, *, task_id: UUID, lock: bool = False) -> PlatformTask:
        query = select(PlatformTask).options(*self._options()).where(PlatformTask.id == task_id)
        if lock:
            query = query.with_for_update()
        task = (await self.db.execute(query)).scalars().unique().one_or_none()
        if task is None:
            raise PlatformTaskError(
                "PLATFORM-TASK-NOT-FOUND", "Tarefa interna não encontrada.", status_code=404
            )
        return await self._refresh_generated_timestamps(task)

    async def list(
        self,
        *,
        actor: User,
        limit: int = 50,
        offset: int = 0,
        scope: str = "mine",
        statuses: list[PlatformTaskStatus] | None = None,
        priorities: list[str] | None = None,
        workspace_id: UUID | None = None,
        search: str | None = None,
        overdue: bool | None = None,
        unassigned: bool | None = None,
    ) -> tuple[list[PlatformTask], int]:
        self._require(actor, "view")
        clauses: list[Any] = []
        if scope == "mine":
            clauses.append(
                or_(
                    PlatformTask.owner_user_id == actor.id,
                    PlatformTask.reviewer_user_id == actor.id,
                    exists().where(
                        PlatformTaskParticipant.task_id == PlatformTask.id,
                        PlatformTaskParticipant.user_id == actor.id,
                        PlatformTaskParticipant.active.is_(True),
                    ),
                )
            )
        elif scope == "reviews":
            clauses.extend(
                [
                    PlatformTask.reviewer_user_id == actor.id,
                    PlatformTask.review_state == PlatformTaskReviewState.pending,
                ]
            )
        elif scope == "team":
            managed_members = await self._managed_member_ids(actor)
            if not managed_members:
                raise PlatformTaskError(
                    "PLATFORM-TASK-TEAM-SCOPE-DENIED",
                    "Você não gerencia uma equipe autorizada.",
                    status_code=403,
                )
            clauses.append(self._involved_clause(managed_members))
        elif scope == "platform":
            if not self._has_platform_scope(actor):
                raise PlatformTaskError(
                    "PLATFORM-TASK-PLATFORM-SCOPE-DENIED",
                    "A visão integral é reservada ao root e ao administrador operacional.",
                    status_code=403,
                )
        else:
            raise PlatformTaskError("PLATFORM-TASK-SCOPE-INVALID", "Escopo inválido.")
        if statuses:
            clauses.append(PlatformTask.status.in_(statuses))
        if priorities:
            clauses.append(PlatformTask.priority.in_(priorities))
        if workspace_id:
            clauses.append(PlatformTask.workspace_id == workspace_id)
        if search and search.strip():
            escaped = search.strip().replace("%", r"\%").replace("_", r"\_")
            pattern = f"%{escaped}%"
            clauses.append(
                or_(
                    PlatformTask.title.ilike(pattern, escape="\\"),
                    PlatformTask.description.ilike(pattern, escape="\\"),
                )
            )
        if overdue is True:
            clauses.extend(
                [
                    PlatformTask.due_at.is_not(None),
                    PlatformTask.due_at < _now(),
                    PlatformTask.status.not_in(
                        [PlatformTaskStatus.done, PlatformTaskStatus.cancelled]
                    ),
                ]
            )
        if unassigned is True:
            clauses.append(
                ~exists().where(
                    PlatformTaskParticipant.task_id == PlatformTask.id,
                    PlatformTaskParticipant.role == PlatformTaskParticipantRole.assignee,
                    PlatformTaskParticipant.active.is_(True),
                )
            )
        total = int(await self.db.scalar(select(func.count(PlatformTask.id)).where(*clauses)) or 0)
        items = list(
            (
                await self.db.scalars(
                    select(PlatformTask)
                    .options(*self._options())
                    .where(*clauses)
                    .order_by(
                        PlatformTask.priority.asc(),
                        PlatformTask.due_at.asc().nulls_last(),
                        PlatformTask.id.asc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            )
            .unique()
            .all()
        )
        return items, total

    async def _require_internal_users(self, user_ids: set[UUID]) -> dict[UUID, User]:
        if not user_ids:
            return {}
        users = {
            user.id: user
            for user in (await self.db.scalars(select(User).where(User.id.in_(user_ids)))).all()
        }
        invalid = [
            user_id
            for user_id in user_ids
            if (
                user_id not in users
                or not users[user_id].is_active
                or users[user_id].deleted_at is not None
                or not users[user_id].is_internal_staff
                or not users[user_id].platform_access_active
            )
        ]
        if invalid:
            raise PlatformTaskError(
                "PLATFORM-TASK-STAFF-INVALID",
                "Owner, executores, reviewer e observadores devem ser funcionários ativos.",
            )
        return users

    @staticmethod
    def _validate_deadlines(
        *,
        due_at: datetime | None,
        owner_due_at: datetime | None,
        review_due_at: datetime | None,
        participants: list[PlatformTaskParticipantWrite],
    ) -> None:
        if due_at is None:
            return
        deadlines = [owner_due_at, review_due_at]
        deadlines.extend(item.personal_due_at for item in participants)
        if any(item is not None and item > due_at for item in deadlines):
            raise PlatformTaskError(
                "PLATFORM-TASK-DEADLINE-INVALID",
                "Prazo individual ou de revisão não pode superar o prazo geral.",
            )

    async def _validate_links(
        self,
        *,
        actor: User,
        workspace_id: UUID | None,
        linked_ticket_id: UUID | None,
        linked_incident_id: UUID | None,
        linked_lead_id: UUID | None,
        linked_release_id: UUID | None,
    ) -> UUID | None:
        resolved_workspace_id = workspace_id
        if workspace_id is not None:
            self._require_link_permission(
                actor,
                allowed=(("customers", "view"),),
                code="PLATFORM-TASK-WORKSPACE-PERMISSION-DENIED",
                message="Vincular uma conta exige permissão de leitura de clientes.",
            )
            if await self.db.get(Workspace, workspace_id) is None:
                raise PlatformTaskError(
                    "PLATFORM-TASK-WORKSPACE-NOT-FOUND", "Conta não encontrada."
                )
        if linked_ticket_id is not None:
            async with self._support_ticket_lookup_context(actor=actor):
                ticket = await self.db.scalar(
                    select(SupportTicket)
                    .where(SupportTicket.id == linked_ticket_id)
                    .execution_options(populate_existing=True)
                )
            if ticket is None:
                raise PlatformTaskError("PLATFORM-TASK-TICKET-NOT-FOUND", "Ticket não encontrado.")
            if resolved_workspace_id is not None and ticket.workspace_id != resolved_workspace_id:
                raise PlatformTaskError(
                    "PLATFORM-TASK-LINK-WORKSPACE-MISMATCH",
                    "Ticket e conta vinculada pertencem a workspaces diferentes.",
                )
            resolved_workspace_id = resolved_workspace_id or ticket.workspace_id
        if linked_incident_id is not None:
            self._require_link_permission(
                actor,
                allowed=(("security", "view"), ("support", "view")),
                code="PLATFORM-TASK-INCIDENT-PERMISSION-DENIED",
                message="Vincular um incidente exige permissão de segurança ou suporte.",
            )
            if await self.db.get(PlatformIncident, linked_incident_id) is None:
                raise PlatformTaskError(
                    "PLATFORM-TASK-INCIDENT-NOT-FOUND", "Incidente não encontrado."
                )
        if linked_lead_id is not None:
            self._require_link_permission(
                actor,
                allowed=(("sales", "view"),),
                code="PLATFORM-TASK-LEAD-PERMISSION-DENIED",
                message="Vincular um lead exige permissão de leitura comercial.",
            )
            # Prospect conversion locks the same row. Sharing that lock keeps
            # ``workspace_id`` stable until this task mutation commits.
            lead = await self.db.scalar(
                select(GrowthProspect).where(GrowthProspect.id == linked_lead_id).with_for_update()
            )
            if lead is None:
                raise PlatformTaskError("PLATFORM-TASK-LEAD-NOT-FOUND", "Lead não encontrado.")
            if lead.workspace_id is None and resolved_workspace_id is not None:
                raise PlatformTaskError(
                    "PLATFORM-TASK-LINK-WORKSPACE-MISMATCH",
                    "Lead ainda não convertido não pode ser vinculado simultaneamente a uma conta.",
                )
            if resolved_workspace_id is not None and lead.workspace_id != resolved_workspace_id:
                raise PlatformTaskError(
                    "PLATFORM-TASK-LINK-WORKSPACE-MISMATCH",
                    "Lead e conta vinculada pertencem a workspaces diferentes.",
                )
            resolved_workspace_id = resolved_workspace_id or lead.workspace_id
        if linked_release_id is not None:
            self._require_link_permission(
                actor,
                allowed=(("releases", "view"),),
                code="PLATFORM-TASK-RELEASE-PERMISSION-DENIED",
                message="Vincular uma release exige permissão de leitura de releases.",
            )
            if await self.db.get(PlatformRelease, linked_release_id) is None:
                raise PlatformTaskError(
                    "PLATFORM-TASK-RELEASE-NOT-FOUND", "Release não encontrada."
                )
        return resolved_workspace_id

    async def _apply_participants(
        self,
        *,
        task: PlatformTask,
        specs: list[PlatformTaskParticipantWrite],
        users: dict[UUID, User],
        actor_id: UUID,
    ) -> None:
        now = _now()
        for participant in self._active_participants(task):
            participant.active = False
            participant.state = PlatformTaskParticipantState.removed
            participant.removed_at = now
        await self.db.flush()
        for spec in specs:
            task.participants.append(
                PlatformTaskParticipant(
                    task_id=task.id,
                    user_id=spec.user_id,
                    role=spec.role,
                    personal_due_at=spec.personal_due_at,
                    state=PlatformTaskParticipantState.assigned,
                    required_for_completion=(
                        spec.required_for_completion
                        if spec.role == PlatformTaskParticipantRole.assignee
                        else False
                    ),
                    label_snapshot=users[spec.user_id].name,
                    active=True,
                    created_by_user_id=actor_id,
                )
            )
        await self.db.flush()
        # The relationship is intentionally filtered to active rows. Reload it
        # after logical replacement so API responses never mix removed and
        # current assignees while the immutable events retain the old snapshot.
        self.db.expire(task, ["participants"])
        await self.db.refresh(task, attribute_names=["participants"])

    async def create(
        self,
        *,
        data: PlatformTaskCreate,
        context: PlatformTaskMutationContext,
    ) -> PlatformTaskMutationResult:
        self._require(context.actor, "create")
        assigns_other_staff = (
            data.owner_user_id != context.actor.id
            or data.reviewer_user_id is not None
            or any(item.user_id != context.actor.id for item in data.participants)
        )
        if assigns_other_staff:
            self._require(context.actor, "assign")
        user_ids = {data.owner_user_id, *(item.user_id for item in data.participants)}
        if data.reviewer_user_id is not None:
            user_ids.add(data.reviewer_user_id)
        users = await self._require_internal_users(user_ids)
        self._validate_deadlines(
            due_at=data.due_at,
            owner_due_at=data.owner_due_at,
            review_due_at=data.review_due_at,
            participants=data.participants,
        )
        workspace_id = await self._validate_links(
            actor=context.actor,
            workspace_id=data.workspace_id,
            linked_ticket_id=data.linked_ticket_id,
            linked_incident_id=data.linked_incident_id,
            linked_lead_id=data.linked_lead_id,
            linked_release_id=data.linked_release_id,
        )
        review_required = bool(data.review_required or data.reviewer_user_id)
        task = PlatformTask(
            title=data.title,
            description=data.description,
            priority=data.priority,
            due_at=data.due_at,
            owner_due_at=data.owner_due_at,
            review_due_at=data.review_due_at,
            review_required=review_required,
            review_state=(
                PlatformTaskReviewState.draft
                if review_required
                else PlatformTaskReviewState.not_required
            ),
            owner_user_id=data.owner_user_id,
            reviewer_user_id=data.reviewer_user_id,
            created_by_user_id=context.actor.id,
            workspace_id=workspace_id,
            linked_ticket_id=data.linked_ticket_id,
            linked_incident_id=data.linked_incident_id,
            linked_lead_id=data.linked_lead_id,
            linked_release_id=data.linked_release_id,
            version=1,
            participants=[],
            review_rounds=[],
        )
        self.db.add(task)
        await self.db.flush()
        await self._apply_participants(
            task=task,
            specs=data.participants,
            users=users,
            actor_id=context.actor.id,
        )
        after = self.snapshot(task)
        await self._event(
            task,
            context=context,
            event_type=PlatformTaskEventType.created,
            before=None,
            after=after,
            reason=data.reason,
        )
        task = await self._load(task_id=task.id)
        return PlatformTaskMutationResult(
            task=task,
            before=None,
            after=after,
            workspace_id=task.workspace_id,
        )

    async def update(
        self,
        *,
        task_id: UUID,
        data: PlatformTaskUpdate,
        context: PlatformTaskMutationContext,
    ) -> PlatformTaskMutationResult:
        self._require(context.actor, "edit")
        task = await self._load(task_id=task_id, lock=True)
        await self._assert_task_access(context.actor, task)
        await self._assert_owner_or_manager(context.actor, task)
        self._require_version(task, data.expected_version)
        if task.status in {PlatformTaskStatus.done, PlatformTaskStatus.cancelled}:
            raise PlatformTaskError(
                "PLATFORM-TASK-COMMAND-REQUIRED",
                "Reabra a tarefa antes de editar um registro encerrado.",
                status_code=409,
            )
        if task.review_state == PlatformTaskReviewState.pending:
            raise PlatformTaskError(
                "PLATFORM-TASK-REVIEW-PENDING",
                "Não altere a tarefa durante uma revisão pendente.",
                status_code=409,
            )
        before = self.snapshot(task)
        fields = data.model_fields_set - {"expected_version", "reason"}
        relation_values = {
            "workspace_id": data.workspace_id if "workspace_id" in fields else task.workspace_id,
            "linked_ticket_id": (
                data.linked_ticket_id if "linked_ticket_id" in fields else task.linked_ticket_id
            ),
            "linked_incident_id": (
                data.linked_incident_id
                if "linked_incident_id" in fields
                else task.linked_incident_id
            ),
            "linked_lead_id": (
                data.linked_lead_id if "linked_lead_id" in fields else task.linked_lead_id
            ),
            "linked_release_id": (
                data.linked_release_id if "linked_release_id" in fields else task.linked_release_id
            ),
        }
        relation_fields = set(relation_values)
        resolved_workspace = task.workspace_id
        if fields.intersection(relation_fields):
            resolved_workspace = await self._validate_links(
                actor=context.actor,
                **relation_values,
            )
        if fields.intersection({"workspace_id", "linked_ticket_id", "linked_lead_id"}):
            relation_values["workspace_id"] = resolved_workspace
        if "due_at" in fields:
            self._validate_deadlines(
                due_at=data.due_at,
                owner_due_at=task.owner_due_at,
                review_due_at=task.review_due_at,
                participants=[
                    PlatformTaskParticipantWrite(
                        user_id=item.user_id,
                        role=item.role,
                        personal_due_at=item.personal_due_at,
                        required_for_completion=item.required_for_completion,
                    )
                    for item in self._active_participants(task)
                ],
            )
        for field in fields:
            if field in relation_values:
                setattr(task, field, relation_values[field])
            else:
                setattr(task, field, getattr(data, field))
        task.version += 1
        await self.db.flush()
        after = self.snapshot(task)
        await self._event(
            task,
            context=context,
            event_type=PlatformTaskEventType.updated,
            before=before,
            after=after,
            reason=data.reason,
        )
        task = await self._refresh_generated_timestamps(task)
        return PlatformTaskMutationResult(
            task=task,
            before=before,
            after=after,
            workspace_id=task.workspace_id,
        )

    async def replace_assignments(
        self,
        *,
        task_id: UUID,
        data: PlatformTaskAssignmentsReplace,
        context: PlatformTaskMutationContext,
    ) -> PlatformTaskMutationResult:
        self._require(context.actor, "assign")
        task = await self._load(task_id=task_id, lock=True)
        await self._assert_task_access(context.actor, task)
        await self._assert_owner_or_manager(context.actor, task)
        self._require_version(task, data.expected_version)
        if task.status in {PlatformTaskStatus.done, PlatformTaskStatus.cancelled}:
            raise PlatformTaskError(
                "PLATFORM-TASK-COMMAND-REQUIRED",
                "Reabra a tarefa antes de alterar seus responsáveis.",
                status_code=409,
            )
        if task.review_state == PlatformTaskReviewState.pending:
            raise PlatformTaskError(
                "PLATFORM-TASK-REVIEW-PENDING",
                "Não altere responsáveis durante uma revisão pendente.",
                status_code=409,
            )
        self._validate_deadlines(
            due_at=task.due_at,
            owner_due_at=data.owner_due_at,
            review_due_at=data.review_due_at,
            participants=data.participants,
        )
        user_ids = {data.owner_user_id, *(item.user_id for item in data.participants)}
        if data.reviewer_user_id is not None:
            user_ids.add(data.reviewer_user_id)
        users = await self._require_internal_users(user_ids)
        before = self.snapshot(task)
        task.owner_user_id = data.owner_user_id
        task.owner_due_at = data.owner_due_at
        task.reviewer_user_id = data.reviewer_user_id
        task.review_due_at = data.review_due_at
        task.review_required = data.reviewer_user_id is not None
        task.review_state = (
            PlatformTaskReviewState.draft
            if task.review_required
            else PlatformTaskReviewState.not_required
        )
        await self._apply_participants(
            task=task,
            specs=data.participants,
            users=users,
            actor_id=context.actor.id,
        )
        task.version += 1
        await self.db.flush()
        after = self.snapshot(task)
        await self._event(
            task,
            context=context,
            event_type=PlatformTaskEventType.participants_changed,
            before=before,
            after=after,
            reason=data.reason,
        )
        task = await self._load(task_id=task.id)
        return PlatformTaskMutationResult(
            task=task,
            before=before,
            after=after,
            workspace_id=task.workspace_id,
        )

    async def set_participant_state(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        data: PlatformTaskParticipantStateUpdate,
        context: PlatformTaskMutationContext,
    ) -> PlatformTaskMutationResult:
        self._require(context.actor, "edit")
        task = await self._load(task_id=task_id, lock=True)
        await self._assert_task_access(context.actor, task)
        await self._assert_participant_state_actor(
            actor=context.actor,
            task=task,
            target_user_id=user_id,
        )
        if user_id != context.actor.id and not is_platform_root(context.actor):
            self._require(context.actor, "assign")
        self._require_version(task, data.expected_version)
        if task.status in {PlatformTaskStatus.done, PlatformTaskStatus.cancelled}:
            raise PlatformTaskError(
                "PLATFORM-TASK-COMMAND-REQUIRED",
                "Reabra a tarefa antes de alterar o progresso individual.",
                status_code=409,
            )
        if task.review_state == PlatformTaskReviewState.pending:
            raise PlatformTaskError(
                "PLATFORM-TASK-REVIEW-PENDING",
                "O progresso não pode mudar durante uma revisão pendente.",
                status_code=409,
            )
        participant = next(
            (item for item in self._active_participants(task) if item.user_id == user_id), None
        )
        if participant is None or participant.role != PlatformTaskParticipantRole.assignee:
            raise PlatformTaskError(
                "PLATFORM-TASK-ASSIGNEE-NOT-FOUND",
                "Executor ativo não encontrado.",
                status_code=404,
            )
        if data.state == PlatformTaskParticipantState.removed:
            raise PlatformTaskError(
                "PLATFORM-TASK-PARTICIPANT-STATE-INVALID",
                "Remova participantes pelo comando de atribuição.",
            )
        before = self.snapshot(task)
        participant.state = data.state
        task.version += 1
        await self.db.flush()
        after = self.snapshot(task)
        await self._event(
            task,
            context=context,
            event_type=PlatformTaskEventType.updated,
            before=before,
            after=after,
            reason=data.reason,
        )
        task = await self._refresh_generated_timestamps(task)
        return PlatformTaskMutationResult(
            task=task,
            before=before,
            after=after,
            workspace_id=task.workspace_id,
        )

    @staticmethod
    def _required_assignees_complete(task: PlatformTask) -> bool:
        return all(
            item.state == PlatformTaskParticipantState.completed
            for item in PlatformTaskService._active_participants(task)
            if item.role == PlatformTaskParticipantRole.assignee and item.required_for_completion
        )

    @staticmethod
    def _current_round(task: PlatformTask) -> PlatformTaskReviewRound | None:
        return next(
            (
                item
                for item in reversed(task.review_rounds)
                if item.round_number == task.review_round_number
            ),
            None,
        )

    async def allowed_commands(
        self,
        *,
        actor: User,
        task: PlatformTask,
    ) -> list[PlatformTaskCommand]:
        """Return only commands that the current state and row authority allow.

        This manifest is intentionally calculated server-side so the Blackbox
        never guesses team-manager authority and never renders an action that
        the same service will predictably reject.
        """

        commands: list[PlatformTaskCommand] = []
        is_operational = (
            is_platform_root(actor)
            or actor.id == task.owner_user_id
            or self._participant_role(actor, task) == PlatformTaskParticipantRole.assignee
        )
        is_owner_or_manager = (
            is_platform_root(actor)
            or actor.id == task.owner_user_id
            or await self._is_manager_for_task(actor, task)
        )
        required_assignees_complete = self._required_assignees_complete(task)

        if has_platform_task_permission(actor, "edit") and is_operational:
            if task.status in {PlatformTaskStatus.pending, PlatformTaskStatus.blocked}:
                commands.append(PlatformTaskCommand.start)
            if task.status in {PlatformTaskStatus.pending, PlatformTaskStatus.in_progress}:
                commands.append(PlatformTaskCommand.block)
            if (
                task.status == PlatformTaskStatus.in_progress
                and task.review_required
                and task.reviewer_user_id is not None
                and task.review_state != PlatformTaskReviewState.pending
                and required_assignees_complete
            ):
                commands.append(PlatformTaskCommand.submit_for_review)

        current_round = self._current_round(task)
        can_decide_review = (
            has_platform_task_permission(actor, "review")
            and task.review_state == PlatformTaskReviewState.pending
            and current_round is not None
            and current_round.state == PlatformTaskReviewRoundState.pending
            and current_round.task_version_reviewed == task.version
            and current_round.submitted_by_user_id != actor.id
            and (
                actor.id == task.reviewer_user_id
                or self._may_override_assigned_reviewer(actor)
            )
        )
        if can_decide_review:
            commands.extend(
                (
                    PlatformTaskCommand.approve,
                    PlatformTaskCommand.request_changes,
                    PlatformTaskCommand.reject,
                )
            )

        if (
            has_platform_task_permission(actor, "complete")
            and is_operational
            and task.status in {PlatformTaskStatus.pending, PlatformTaskStatus.in_progress}
            and not task.review_required
            and required_assignees_complete
        ):
            commands.append(PlatformTaskCommand.complete)

        if has_platform_task_permission(actor, "complete") and is_owner_or_manager:
            if task.status in {PlatformTaskStatus.done, PlatformTaskStatus.cancelled}:
                commands.append(PlatformTaskCommand.reopen)
            else:
                commands.append(PlatformTaskCommand.cancel)
        return commands

    async def execute_command(
        self,
        *,
        task_id: UUID,
        command: PlatformTaskCommand,
        data: PlatformTaskCommandRequest,
        context: PlatformTaskMutationContext,
    ) -> PlatformTaskMutationResult:
        required_permission = required_platform_task_command_permission(command)
        self._require(context.actor, required_permission)
        task = await self._load(task_id=task_id, lock=True)
        await self._assert_task_access(context.actor, task)
        self._require_version(task, data.expected_version)
        if command in {
            PlatformTaskCommand.start,
            PlatformTaskCommand.block,
            PlatformTaskCommand.submit_for_review,
            PlatformTaskCommand.complete,
        }:
            await self._assert_operational_actor(context.actor, task)
        elif command in {
            PlatformTaskCommand.reopen,
            PlatformTaskCommand.cancel,
        }:
            await self._assert_owner_or_manager(context.actor, task)
        before = self.snapshot(task)
        now = _now()
        reason = (data.reason or "").strip() or f"Comando {command.value} executado."

        if command == PlatformTaskCommand.start:
            if task.status not in {PlatformTaskStatus.pending, PlatformTaskStatus.blocked}:
                raise PlatformTaskError(
                    "PLATFORM-TASK-TRANSITION-INVALID",
                    "Somente tarefa pendente ou bloqueada pode ser iniciada.",
                    status_code=409,
                )
            task.status = PlatformTaskStatus.in_progress
            task.started_at = task.started_at or now
            task.blocked_reason = None
            event_type = PlatformTaskEventType.started
        elif command == PlatformTaskCommand.block:
            reason = self._require_reason(data.reason, action=command.value)
            if task.status not in {PlatformTaskStatus.pending, PlatformTaskStatus.in_progress}:
                raise PlatformTaskError(
                    "PLATFORM-TASK-TRANSITION-INVALID",
                    "Somente tarefa aberta pode ser bloqueada.",
                    status_code=409,
                )
            task.status = PlatformTaskStatus.blocked
            task.blocked_reason = reason
            event_type = PlatformTaskEventType.blocked
        elif command == PlatformTaskCommand.submit_for_review:
            if task.status != PlatformTaskStatus.in_progress:
                raise PlatformTaskError(
                    "PLATFORM-TASK-NOT-IN-PROGRESS",
                    "Inicie a tarefa antes de submetê-la à revisão.",
                    status_code=409,
                )
            if not task.review_required or task.reviewer_user_id is None:
                raise PlatformTaskError(
                    "PLATFORM-TASK-REVIEWER-REQUIRED",
                    "A tarefa não possui reviewer ativo.",
                    status_code=409,
                )
            if not self._required_assignees_complete(task):
                raise PlatformTaskError(
                    "PLATFORM-TASK-ASSIGNEES-INCOMPLETE",
                    "Todos os executores obrigatórios devem concluir sua parte.",
                    status_code=409,
                )
            if task.review_state == PlatformTaskReviewState.pending:
                raise PlatformTaskError(
                    "PLATFORM-TASK-REVIEW-PENDING",
                    "A tarefa já aguarda revisão.",
                    status_code=409,
                )
            task.review_round_number += 1
            task.review_state = PlatformTaskReviewState.pending
            # The round reviews the exact version produced by this command.
            task.version += 1
            task.review_rounds.append(
                PlatformTaskReviewRound(
                    task_id=task.id,
                    round_number=task.review_round_number,
                    reviewer_user_id=task.reviewer_user_id,
                    state=PlatformTaskReviewRoundState.pending,
                    review_due_at=task.review_due_at,
                    submitted_by_user_id=context.actor.id,
                    submitted_at=now,
                    task_version_reviewed=task.version,
                )
            )
            await self.db.flush()
            after = self.snapshot(task)
            await self._event(
                task,
                context=context,
                event_type=PlatformTaskEventType.review_submitted,
                before=before,
                after=after,
                reason=reason,
                comment=data.comment,
            )
            task = await self._refresh_generated_timestamps(task)
            return PlatformTaskMutationResult(
                task=task,
                before=before,
                after=after,
                workspace_id=task.workspace_id,
            )
        elif command in {
            PlatformTaskCommand.approve,
            PlatformTaskCommand.request_changes,
            PlatformTaskCommand.reject,
        }:
            if task.review_state != PlatformTaskReviewState.pending:
                raise PlatformTaskError(
                    "PLATFORM-TASK-REVIEW-NOT-PENDING",
                    "Não existe revisão pendente.",
                    status_code=409,
                )
            if (
                context.actor.id != task.reviewer_user_id
                and not self._may_override_assigned_reviewer(context.actor)
            ):
                raise PlatformTaskError(
                    "PLATFORM-TASK-REVIEWER-ONLY",
                    "Somente o reviewer ativo pode decidir.",
                    status_code=403,
                )
            if context.actor.id != task.reviewer_user_id:
                reason = self._require_reason(data.reason, action=command.value)
            round_item = self._current_round(task)
            if round_item is None or round_item.state != PlatformTaskReviewRoundState.pending:
                raise PlatformTaskError(
                    "PLATFORM-TASK-REVIEW-ROUND-MISSING",
                    "A rodada de revisão não foi encontrada.",
                    status_code=409,
                )
            if round_item.submitted_by_user_id == context.actor.id:
                raise PlatformTaskError(
                    "PLATFORM-TASK-SELF-REVIEW-DENIED",
                    "Quem submeteu a entrega não pode decidir a própria revisão.",
                    status_code=403,
                )
            if round_item.task_version_reviewed != task.version:
                raise PlatformTaskError(
                    "PLATFORM-TASK-REVIEW-STALE",
                    "A tarefa mudou após a submissão; uma nova rodada é necessária.",
                    status_code=409,
                )
            round_item.resolved_at = now
            round_item.resolved_by_user_id = context.actor.id
            round_item.decision_reason = sanitize_audit_text(data.comment or reason)
            if command == PlatformTaskCommand.approve:
                round_item.state = PlatformTaskReviewRoundState.approved
                task.review_state = PlatformTaskReviewState.approved
                task.status = PlatformTaskStatus.done
                task.completed_at = now
                event_type = PlatformTaskEventType.review_approved
            elif command == PlatformTaskCommand.request_changes:
                reason = self._require_reason(data.reason or data.comment, action=command.value)
                round_item.state = PlatformTaskReviewRoundState.changes_requested
                task.review_state = PlatformTaskReviewState.changes_requested
                task.status = PlatformTaskStatus.in_progress
                event_type = PlatformTaskEventType.review_changes_requested
            else:
                reason = self._require_reason(data.reason or data.comment, action=command.value)
                round_item.state = PlatformTaskReviewRoundState.rejected
                task.review_state = PlatformTaskReviewState.rejected
                task.status = PlatformTaskStatus.in_progress
                event_type = PlatformTaskEventType.review_rejected
        elif command == PlatformTaskCommand.complete:
            if task.status not in {PlatformTaskStatus.pending, PlatformTaskStatus.in_progress}:
                raise PlatformTaskError(
                    "PLATFORM-TASK-TRANSITION-INVALID",
                    "Somente tarefa aberta pode ser concluída.",
                    status_code=409,
                )
            if task.review_required:
                raise PlatformTaskError(
                    "PLATFORM-TASK-REVIEW-REQUIRED",
                    "Tarefa com reviewer deve ser concluída pela aprovação.",
                    status_code=409,
                )
            if not self._required_assignees_complete(task):
                raise PlatformTaskError(
                    "PLATFORM-TASK-ASSIGNEES-INCOMPLETE",
                    "Todos os executores obrigatórios devem concluir sua parte.",
                    status_code=409,
                )
            task.status = PlatformTaskStatus.done
            task.completed_at = now
            event_type = PlatformTaskEventType.completed
        elif command == PlatformTaskCommand.reopen:
            reason = self._require_reason(data.reason, action=command.value)
            if task.status not in {PlatformTaskStatus.done, PlatformTaskStatus.cancelled}:
                raise PlatformTaskError(
                    "PLATFORM-TASK-NOT-TERMINAL",
                    "Somente tarefa encerrada pode ser reaberta.",
                    status_code=409,
                )
            task.status = PlatformTaskStatus.pending
            task.completed_at = None
            task.cancelled_at = None
            task.cancellation_reason = None
            task.blocked_reason = None
            task.review_state = (
                PlatformTaskReviewState.draft
                if task.review_required
                else PlatformTaskReviewState.not_required
            )
            event_type = PlatformTaskEventType.reopened
        elif command == PlatformTaskCommand.cancel:
            reason = self._require_reason(data.reason, action=command.value)
            if task.status in {PlatformTaskStatus.done, PlatformTaskStatus.cancelled}:
                raise PlatformTaskError(
                    "PLATFORM-TASK-ALREADY-TERMINAL",
                    "A tarefa já está encerrada.",
                    status_code=409,
                )
            current_round = self._current_round(task)
            if current_round and current_round.state == PlatformTaskReviewRoundState.pending:
                current_round.state = PlatformTaskReviewRoundState.cancelled
                current_round.resolved_at = now
                current_round.resolved_by_user_id = context.actor.id
                current_round.decision_reason = reason
            task.status = PlatformTaskStatus.cancelled
            task.cancelled_at = now
            task.completed_at = None
            task.cancellation_reason = reason
            event_type = PlatformTaskEventType.cancelled
        else:  # pragma: no cover - enum keeps this exhaustive
            raise PlatformTaskError("PLATFORM-TASK-COMMAND-UNKNOWN", "Comando desconhecido.")

        task.version += 1
        await self.db.flush()
        after = self.snapshot(task)
        await self._event(
            task,
            context=context,
            event_type=event_type,
            before=before,
            after=after,
            reason=reason,
            comment=data.comment,
        )
        task = await self._refresh_generated_timestamps(task)
        return PlatformTaskMutationResult(
            task=task,
            before=before,
            after=after,
            workspace_id=task.workspace_id,
        )

    async def add_comment(
        self,
        *,
        task_id: UUID,
        data: PlatformTaskCommentCreate,
        context: PlatformTaskMutationContext,
    ) -> PlatformTaskMutationResult:
        self._require(context.actor, "comment")
        task = await self._load(task_id=task_id, lock=True)
        await self._assert_task_access(context.actor, task)
        await self._assert_comment_actor(context.actor, task)
        self._require_version(task, data.expected_version)
        # Comments are append-only and do not invalidate an in-flight review or
        # consume the optimistic version of the mutable task itself.
        snapshot = self.snapshot(task)
        await self._event(
            task,
            context=context,
            event_type=PlatformTaskEventType.comment_added,
            before=snapshot,
            after=snapshot,
            reason="Comentário interno adicionado.",
            comment=data.comment,
        )
        return PlatformTaskMutationResult(
            task=task,
            before=snapshot,
            after=snapshot,
            workspace_id=task.workspace_id,
        )

    async def list_events(
        self,
        *,
        actor: User,
        task_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[PlatformTaskEvent], int]:
        await self.get(actor=actor, task_id=task_id)
        total = int(
            await self.db.scalar(
                select(func.count(PlatformTaskEvent.id)).where(PlatformTaskEvent.task_id == task_id)
            )
            or 0
        )
        items = list(
            (
                await self.db.scalars(
                    select(PlatformTaskEvent)
                    .where(PlatformTaskEvent.task_id == task_id)
                    .order_by(PlatformTaskEvent.occurred_at, PlatformTaskEvent.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return items, total


__all__ = [
    "PlatformTaskError",
    "PlatformTaskMutationContext",
    "PlatformTaskMutationResult",
    "PlatformTaskService",
    "has_platform_task_permission",
    "required_platform_task_command_permission",
]
