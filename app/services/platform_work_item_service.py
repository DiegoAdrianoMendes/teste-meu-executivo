"""Projection and command service for the Blackbox task center.

Reconciliation reads authoritative domain tables and writes only safe routing metadata.
Commands are an explicit allowlist and always re-check both RBAC and source version.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_privacy import safe_error_type
from app.core.platform_rbac import (
    effective_platform_permissions,
    has_platform_permission,
    is_platform_root,
)
from app.db.advisory import acquire_transaction_advisory_lock
from app.models.configuration import PlatformConfigDocument
from app.models.identity import InvitationStatus, WorkspaceInvitation
from app.models.platform_incident import PlatformIncident
from app.models.platform_organization import (
    PlatformOnboardingMilestone,
    PlatformOperationalAssignment,
    PlatformReviewRequest,
    PlatformStaffInvitation,
)
from app.models.platform_work_item import PlatformWorkItem
from app.models.subscription_commercial import WorkspaceSubscription
from app.models.super_admin_notice import SuperAdminNotice
from app.models.support_access import SupportAccessGrant
from app.models.support_ticket import SupportTicket
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.platform_audit_event_service import PlatformAuditEventService

OPEN_STATES = frozenset(
    {"open", "in_progress", "waiting_external", "waiting_customer", "blocked", "stale"}
)
TERMINAL_STATES = frozenset({"done", "cancelled"})
KNOWN_SOURCE_TYPES = (
    "support_ticket",
    "incident",
    "support_access",
    "workspace_onboarding",
    "billing",
    "workspace_invitation",
    "staff_invitation",
    "publication",
    "configuration",
)
RECONCILIATION_BATCH_SIZE = 500
MAX_RECONCILIATION_BATCH_SIZE = 1000
RECONCILIATION_LOCK_NAMESPACE = "platform-work-item-reconciliation"

logger = logging.getLogger(__name__)


class PlatformWorkItemError(RuntimeError):
    code = "WORK-ITEM-ERROR"
    status_code = 422


class PlatformWorkItemNotFoundError(PlatformWorkItemError):
    code = "WORK-ITEM-NOT-FOUND"
    status_code = 404


class PlatformWorkItemForbiddenError(PlatformWorkItemError):
    code = "WORK-ITEM-FORBIDDEN"
    status_code = 403


class PlatformWorkItemConflictError(PlatformWorkItemError):
    code = "WORK-ITEM-CONFLICT"
    status_code = 409


class PlatformWorkItemValidationError(PlatformWorkItemError):
    code = "WORK-ITEM-INVALID"
    status_code = 422


@dataclass(frozen=True)
class CommandDefinition:
    source_type: str
    command: str
    module: str
    action: str
    handler: str


@dataclass(frozen=True)
class AdapterResult:
    source_type: str
    status: str
    projected: int = 0
    unchanged: int = 0
    reason_code: str | None = None


@dataclass(frozen=True)
class BlackboxSourceLink:
    path: str
    tab: str
    focus: str = "context"
    selector_param: str | None = None
    route_module: str = "platform_tasks"
    route_action: str = "view"


BLACKBOX_SOURCE_LINKS: dict[str, BlackboxSourceLink] = {
    "support_ticket": BlackboxSourceLink(
        "/blackbox/support-tickets",
        "tickets",
        route_module="support",
    ),
    "incident": BlackboxSourceLink(
        "/blackbox/governance/security",
        "incidents",
        route_module="security",
    ),
    "support_access": BlackboxSourceLink(
        "/blackbox/support-access",
        "requests",
        route_module="support_access",
    ),
    "workspace_onboarding": BlackboxSourceLink(
        "/blackbox/workspaces",
        "onboarding",
        focus="workspace",
        route_module="customers",
    ),
    "billing": BlackboxSourceLink(
        "/blackbox/billing",
        "subscriptions",
        route_module="finance",
    ),
    "workspace_invitation": BlackboxSourceLink(
        "/blackbox/workspaces",
        "invitations",
        route_module="customers",
    ),
    "staff_invitation": BlackboxSourceLink(
        "/blackbox/team",
        "invitations",
        route_module="staff",
    ),
    "publication": BlackboxSourceLink(
        "/blackbox/announcements",
        "publications",
        route_module="announcements",
    ),
    "configuration": BlackboxSourceLink(
        "/blackbox/settings",
        "configuration",
        route_module="settings",
    ),
    "commercial_request": BlackboxSourceLink(
        "/blackbox/workspaces",
        "commercial",
        focus="workspace",
        route_module="commercial_contracts",
    ),
    "commercial_checkout_reconcile": BlackboxSourceLink(
        "/blackbox/workspaces",
        "commercial",
        focus="workspace",
        route_module="commercial_contracts",
        route_action="manage",
    ),
}


def blackbox_source_href(
    source_type: str,
    source_id: UUID | str,
    *,
    workspace_id: UUID | str | None = None,
    extra_context: dict[str, str | int | None] | None = None,
    actor: User | None = None,
) -> str:
    """Build a same-origin link only to a registered Blackbox surface.

    ``focus=context`` is deliberate: the destination receives safe routing
    metadata but must not imply that it can select a record it does not yet
    know how to open. Record/workspace focus is declared only for surfaces
    that already implement it.
    """
    link = BLACKBOX_SOURCE_LINKS.get(
        source_type,
        BlackboxSourceLink("/blackbox/tasks", "work-items"),
    )
    if actor is not None and not has_platform_permission(
        actor,
        link.route_module,
        link.route_action,
    ):
        link = BlackboxSourceLink("/blackbox/tasks", "work-items")
    params: dict[str, str] = {
        "sourceType": source_type,
        "sourceId": str(source_id),
        "tab": link.tab,
        "focus": link.focus,
    }
    if workspace_id is not None:
        params["workspaceId"] = str(workspace_id)
    if link.selector_param is not None:
        params[link.selector_param] = str(source_id)
    for key, value in (extra_context or {}).items():
        if value is not None:
            params[key] = str(value)
    return f"{link.path}?{urlencode(params)}"


COMMANDS: dict[tuple[str, str], CommandDefinition] = {
    ("support_ticket", "assign_to_me"): CommandDefinition(
        "support_ticket", "assign_to_me", "support", "assign", "_command_ticket_assign_to_me"
    ),
    ("incident", "assign_to_me"): CommandDefinition(
        "incident",
        "assign_to_me",
        "security",
        "manage",
        "_command_incident_assign_to_me",
    ),
    ("workspace_onboarding", "mark_complete"): CommandDefinition(
        "workspace_onboarding",
        "mark_complete",
        "onboarding",
        "manage",
        "_command_onboarding_complete",
    ),
    ("billing", "assign_to_me"): CommandDefinition(
        "billing", "assign_to_me", "billing", "manage", "_command_billing_assign_to_me"
    ),
    ("workspace_invitation", "revoke"): CommandDefinition(
        "workspace_invitation",
        "revoke",
        "customers",
        "manage",
        "_command_workspace_invitation_revoke",
    ),
    ("staff_invitation", "revoke"): CommandDefinition(
        "staff_invitation", "revoke", "staff", "manage", "_command_staff_invitation_revoke"
    ),
    ("publication", "submit_review"): CommandDefinition(
        "publication", "submit_review", "announcements", "manage", "_command_review_submit"
    ),
    ("publication", "approve"): CommandDefinition(
        "publication", "approve", "announcements", "manage", "_command_review_approve"
    ),
    ("configuration", "submit_review"): CommandDefinition(
        "configuration",
        "submit_review",
        "settings",
        "manage",
        "_command_review_submit",
    ),
    ("configuration", "approve"): CommandDefinition(
        "configuration", "approve", "settings", "manage", "_command_review_approve"
    ),
}


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _version_timestamp(value: datetime | None) -> str:
    return (_aware(value) or datetime(1970, 1, 1, tzinfo=UTC)).isoformat()


def _source_timestamp(updated_at: datetime | None, created_at: datetime | None) -> datetime:
    return _aware(updated_at) or _aware(created_at) or datetime(1970, 1, 1, tzinfo=UTC)


def _source_version(updated_at: datetime | None, created_at: datetime | None) -> str:
    return _source_timestamp(updated_at, created_at).isoformat()


def _projection_values_equal(current: Any, candidate: Any) -> bool:
    if isinstance(current, datetime) and isinstance(candidate, datetime):
        return _aware(current) == _aware(candidate)
    return current == candidate


def _composite_version(*parts: Any) -> str:
    return ":".join(str(part) for part in parts)


def _encode_cursor(timestamp: datetime, item_id: UUID) -> str:
    raw = json.dumps(
        {"t": _aware(timestamp).isoformat(), "id": str(item_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(payload["t"]), UUID(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise PlatformWorkItemValidationError("Cursor inválido.") from error


class PlatformWorkItemService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.reconciliation_batch_size = RECONCILIATION_BATCH_SIZE

    @staticmethod
    def _permission_clause(actor: User):
        permissions = effective_platform_permissions(actor)
        if not permissions:
            return None
        return or_(
            *(
                and_(
                    PlatformWorkItem.required_module == permission.module,
                    PlatformWorkItem.required_action == permission.action,
                )
                for permission in permissions
            )
        )

    @staticmethod
    def _can_view(actor: User, item: PlatformWorkItem) -> bool:
        return has_platform_permission(actor, item.required_module, item.required_action)

    @staticmethod
    def _visible_commands(actor: User, item: PlatformWorkItem) -> list[str]:
        visible: list[str] = []
        for command in item.allowed_commands or []:
            definition = COMMANDS.get((item.source_type, command))
            if definition and has_platform_permission(actor, definition.module, definition.action):
                visible.append(command)
        return visible

    def present(self, actor: User, item: PlatformWorkItem) -> dict[str, Any]:
        extra_context = (
            {"namespace": (item.payload_safe or {}).get("namespace")}
            if item.source_type == "configuration"
            else None
        )
        return {
            "id": item.id,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "source_version": item.source_version,
            "kind": item.kind,
            "state": item.state,
            "priority": item.priority,
            "workspace_id": item.workspace_id,
            "assigned_user_id": item.assigned_user_id,
            "assigned_team_id": item.assigned_team_id,
            "requester_user_id": item.requester_user_id,
            "reviewer_user_id": item.reviewer_user_id,
            "safe_title": item.safe_title,
            "safe_summary": item.safe_summary,
            # Recompute at read time so legacy projections cannot keep sending
            # operators to routes removed before the canonical map existed.
            "href": blackbox_source_href(
                item.source_type,
                item.source_id,
                workspace_id=item.workspace_id,
                extra_context=extra_context,
                actor=actor,
            ),
            "required_module": item.required_module,
            "required_action": item.required_action,
            "allowed_commands": self._visible_commands(actor, item),
            "opened_at": item.opened_at,
            "due_at": item.due_at,
            "snoozed_until": item.snoozed_until,
            "closed_at": item.closed_at,
            "source_updated_at": item.source_updated_at,
            "projected_at": item.projected_at,
            "projection_version": item.projection_version,
            "payload_safe": dict(item.payload_safe or {}),
        }

    async def list(
        self,
        *,
        actor: User,
        limit: int,
        cursor: str | None = None,
        scope: str = "platform",
        team_id: UUID | None = None,
        states: list[str] | None = None,
        priorities: list[str] | None = None,
        source_types: list[str] | None = None,
        workspace_id: UUID | None = None,
        assigned_user_id: UUID | None = None,
        unassigned: bool | None = None,
        search: str | None = None,
        overdue: bool | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not has_platform_permission(actor, "platform_tasks", "view"):
            raise PlatformWorkItemForbiddenError(
                "Seu pacote não autoriza consultar tarefas operacionais."
            )
        permission_clause = self._permission_clause(actor)
        if permission_clause is None:
            return [], None
        clauses = [permission_clause]
        if scope == "mine":
            clauses.append(PlatformWorkItem.assigned_user_id == actor.id)
        elif scope == "team":
            if team_id is None:
                raise PlatformWorkItemValidationError("teamId é obrigatório para o escopo team.")
            clauses.append(PlatformWorkItem.assigned_team_id == team_id)
        elif scope != "platform":
            raise PlatformWorkItemValidationError("Escopo inválido.")
        if states:
            clauses.append(PlatformWorkItem.state.in_(states))
        if priorities:
            clauses.append(PlatformWorkItem.priority.in_(priorities))
        if source_types:
            clauses.append(PlatformWorkItem.source_type.in_(source_types))
        if workspace_id:
            clauses.append(PlatformWorkItem.workspace_id == workspace_id)
        if assigned_user_id:
            clauses.append(PlatformWorkItem.assigned_user_id == assigned_user_id)
        if unassigned is True:
            clauses.append(PlatformWorkItem.assigned_user_id.is_(None))
        elif unassigned is False:
            clauses.append(PlatformWorkItem.assigned_user_id.is_not(None))
        if search and search.strip():
            term = f"%{search.strip()}%"
            clauses.append(
                or_(
                    PlatformWorkItem.safe_title.ilike(term),
                    PlatformWorkItem.safe_summary.ilike(term),
                )
            )
        if overdue is True:
            clauses.extend(
                [
                    PlatformWorkItem.state.in_(OPEN_STATES),
                    PlatformWorkItem.due_at.is_not(None),
                    PlatformWorkItem.due_at < _now(),
                ]
            )
        parsed_cursor = _decode_cursor(cursor)
        if parsed_cursor:
            timestamp, item_id = parsed_cursor
            clauses.append(
                or_(
                    PlatformWorkItem.source_updated_at < timestamp,
                    and_(
                        PlatformWorkItem.source_updated_at == timestamp,
                        PlatformWorkItem.id < item_id,
                    ),
                )
            )
        rows = list(
            (
                await self.db.scalars(
                    select(PlatformWorkItem)
                    .where(*clauses)
                    .order_by(
                        PlatformWorkItem.source_updated_at.desc(),
                        PlatformWorkItem.id.desc(),
                    )
                    .limit(limit + 1)
                )
            ).all()
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            _encode_cursor(rows[-1].source_updated_at, rows[-1].id) if has_more and rows else None
        )
        return [self.present(actor, row) for row in rows], next_cursor

    async def counts(self, *, actor: User) -> dict[str, Any]:
        if not has_platform_permission(actor, "platform_tasks", "view"):
            raise PlatformWorkItemForbiddenError(
                "Seu pacote não autoriza consultar tarefas operacionais."
            )
        permission_clause = self._permission_clause(actor)
        if permission_clause is None:
            return {
                "total": 0,
                "open": 0,
                "overdue": 0,
                "unassigned": 0,
                "by_priority": {},
                "by_source": {},
                "generated_at": _now(),
            }
        items = list(
            (await self.db.scalars(select(PlatformWorkItem).where(permission_clause))).all()
        )
        now = _now()
        by_priority: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for item in items:
            by_priority[item.priority] = by_priority.get(item.priority, 0) + 1
            by_source[item.source_type] = by_source.get(item.source_type, 0) + 1
        open_items = [item for item in items if item.state in OPEN_STATES]
        return {
            "total": len(items),
            "open": len(open_items),
            "overdue": sum(
                1 for item in open_items if _aware(item.due_at) and _aware(item.due_at) < now
            ),
            "unassigned": sum(1 for item in open_items if item.assigned_user_id is None),
            "by_priority": by_priority,
            "by_source": by_source,
            "generated_at": now,
        }

    async def get(self, *, actor: User, item_id: UUID, lock: bool = False) -> PlatformWorkItem:
        if not has_platform_permission(actor, "platform_tasks", "view"):
            raise PlatformWorkItemForbiddenError(
                "Seu pacote não autoriza consultar tarefas operacionais."
            )
        query = select(PlatformWorkItem).where(PlatformWorkItem.id == item_id)
        if lock:
            query = query.with_for_update()
        item = await self.db.scalar(query)
        if item is None or not self._can_view(actor, item):
            raise PlatformWorkItemNotFoundError("Tarefa não encontrada.")
        return item

    async def assign(
        self,
        *,
        actor: User,
        item_id: UUID,
        assigned_user_id: UUID | None,
        expected_projection_version: int,
    ) -> PlatformWorkItem:
        if not has_platform_permission(actor, "platform_tasks", "assign"):
            raise PlatformWorkItemForbiddenError(
                "Seu pacote não autoriza atribuir tarefas operacionais."
            )
        item = await self.get(actor=actor, item_id=item_id, lock=True)
        if item.projection_version != expected_projection_version:
            raise PlatformWorkItemConflictError(
                "A tarefa foi atualizada; recarregue antes de atribuir."
            )
        if assigned_user_id is not None:
            assignee = await self.db.get(User, assigned_user_id)
            if (
                assignee is None
                or not assignee.is_active
                or assignee.deleted_at is not None
                or not assignee.is_internal_staff
                or not assignee.platform_access_active
                or not has_platform_permission(assignee, item.required_module, item.required_action)
            ):
                raise PlatformWorkItemValidationError("Responsável inativo ou sem acesso à tarefa.")
        item.assigned_user_id = assigned_user_id
        item.projection_version += 1
        item.projected_at = _now()
        await self.db.flush()
        return item

    async def snooze(
        self,
        *,
        actor: User,
        item_id: UUID,
        snoozed_until: datetime,
        expected_projection_version: int,
    ) -> PlatformWorkItem:
        if not has_platform_permission(actor, "platform_tasks", "edit"):
            raise PlatformWorkItemForbiddenError(
                "Seu pacote não autoriza adiar tarefas operacionais."
            )
        item = await self.get(actor=actor, item_id=item_id, lock=True)
        if item.projection_version != expected_projection_version:
            raise PlatformWorkItemConflictError(
                "A tarefa foi atualizada; recarregue antes de adiar."
            )
        due_at = _aware(item.due_at)
        if item.priority in {"P0", "P1"} or (due_at is not None and due_at <= _now()):
            raise PlatformWorkItemValidationError(
                "Tarefas P0/P1 ou vencidas não podem ser adiadas."
            )
        target = _aware(snoozed_until)
        if target is None or target <= _now() or target > _now() + timedelta(days=30):
            raise PlatformWorkItemValidationError("O adiamento deve estar entre agora e 30 dias.")
        item.snoozed_until = target
        item.projection_version += 1
        item.projected_at = _now()
        await self.db.flush()
        return item

    async def dispatch_command(
        self,
        *,
        actor: User,
        item_id: UUID,
        command: str,
        expected_source_version: str,
        reason: str,
        correlation_id: str | None = None,
        administrative_session_id: UUID | None = None,
        session_type: str = "normal",
        route: str = "/api/v1/platform/work-items/{item_id}/commands/{command}",
    ) -> tuple[PlatformWorkItem, str]:
        item = await self.get(actor=actor, item_id=item_id, lock=True)
        definition = COMMANDS.get((item.source_type, command))
        if definition is None or command not in (item.allowed_commands or []):
            raise PlatformWorkItemValidationError(
                "Comando desconhecido ou indisponível para esta fonte."
            )
        if not has_platform_permission(actor, definition.module, definition.action):
            raise PlatformWorkItemForbiddenError("Seu pacote não autoriza este comando.")
        if item.source_version != expected_source_version:
            raise PlatformWorkItemConflictError(
                "A fonte mudou; reconcilie a tarefa antes de executar."
            )
        handler: Callable[..., Awaitable[str]] = getattr(self, definition.handler)
        previous_version = item.source_version
        before = {
            "sourceVersion": item.source_version,
            "state": item.state,
            "assignedUserId": str(item.assigned_user_id) if item.assigned_user_id else None,
            "reviewerUserId": str(item.reviewer_user_id) if item.reviewer_user_id else None,
        }
        new_version = await handler(actor=actor, item=item)
        item.source_version = new_version
        item.source_updated_at = _now()
        item.projected_at = _now()
        item.projection_version += 1
        await self.db.flush()
        after = {
            "sourceVersion": item.source_version,
            "state": item.state,
            "assignedUserId": str(item.assigned_user_id) if item.assigned_user_id else None,
            "reviewerUserId": str(item.reviewer_user_id) if item.reviewer_user_id else None,
        }
        operation_id = correlation_id or str(uuid4())
        await PlatformAuditEventService(self.db).record(
            PlatformAuditEventCreate(
                actor_id=actor.id,
                actor_role_snapshot=actor.platform_role_package or "unknown",
                is_platform_root=is_platform_root(actor),
                workspace_id=item.workspace_id,
                administrative_session_id=administrative_session_id,
                session_type=session_type,
                module=definition.module,
                action=f"work_item.{command}",
                resource_type=item.source_type,
                resource_id=str(item.source_id),
                resource_label_snapshot=item.safe_title,
                result="success",
                before=before,
                after=after,
                reason=reason,
                reason_source="user",
                correlation_id=operation_id,
                operation_id=operation_id,
                route=route,
                method="POST",
            )
        )
        return item, previous_version

    async def _command_ticket_assign_to_me(self, *, actor: User, item: PlatformWorkItem) -> str:
        ticket = await self.db.scalar(
            select(SupportTicket).where(SupportTicket.id == item.source_id).with_for_update()
        )
        if ticket is None:
            raise PlatformWorkItemNotFoundError("Ticket de origem não encontrado.")
        if str(ticket.version) != item.source_version:
            raise PlatformWorkItemConflictError("O ticket mudou desde a projeção.")
        ticket.assigned_to_user_id = actor.id
        ticket.version += 1
        item.assigned_user_id = actor.id
        return str(ticket.version)

    async def _command_incident_assign_to_me(self, *, actor: User, item: PlatformWorkItem) -> str:
        incident = await self.db.scalar(
            select(PlatformIncident).where(PlatformIncident.id == item.source_id).with_for_update()
        )
        if incident is None:
            raise PlatformWorkItemNotFoundError("Incidente de origem não encontrado.")
        if _source_version(incident.updated_at, incident.created_at) != item.source_version:
            raise PlatformWorkItemConflictError("O incidente mudou desde a projeção.")
        incident.owner_user_id = actor.id
        incident.updated_at = _now()
        item.assigned_user_id = actor.id
        return _source_version(incident.updated_at, incident.created_at)

    async def _command_onboarding_complete(self, *, actor: User, item: PlatformWorkItem) -> str:
        if (item.payload_safe or {}).get("sourceKind") == "milestone":
            milestone = await self.db.scalar(
                select(PlatformOnboardingMilestone)
                .where(PlatformOnboardingMilestone.id == item.source_id)
                .with_for_update()
            )
            if milestone is None:
                raise PlatformWorkItemNotFoundError("Milestone de origem não encontrado.")
            if _composite_version("milestone", milestone.version) != item.source_version:
                raise PlatformWorkItemConflictError("O milestone mudou desde a projeção.")
            milestone.status = "completed"
            milestone.completed_at = _now()
            milestone.version += 1
            item.state = "done"
            item.closed_at = milestone.completed_at
            return _composite_version("milestone", milestone.version)
        workspace = await self.db.scalar(
            select(Workspace).where(Workspace.id == item.source_id).with_for_update()
        )
        if workspace is None:
            raise PlatformWorkItemNotFoundError("Conta de origem não encontrada.")
        if _source_version(workspace.updated_at, workspace.created_at) != item.source_version:
            raise PlatformWorkItemConflictError("A conta mudou desde a projeção.")
        workspace.onboarding_completed = True
        workspace.updated_at = _now()
        item.state = "done"
        item.closed_at = _now()
        return _source_version(workspace.updated_at, workspace.created_at)

    async def _command_billing_assign_to_me(self, *, actor: User, item: PlatformWorkItem) -> str:
        subscription = await self.db.scalar(
            select(WorkspaceSubscription)
            .where(WorkspaceSubscription.id == item.source_id)
            .with_for_update()
        )
        if subscription is None:
            raise PlatformWorkItemNotFoundError("Assinatura de origem não encontrada.")
        assignment = await self.db.scalar(
            select(PlatformOperationalAssignment)
            .where(
                PlatformOperationalAssignment.source_type == "billing",
                PlatformOperationalAssignment.source_id == subscription.id,
            )
            .with_for_update()
        )
        assignment_version = assignment.version if assignment else 0
        current = _composite_version(
            _source_version(subscription.updated_at, subscription.created_at), assignment_version
        )
        if current != item.source_version:
            raise PlatformWorkItemConflictError("A cobrança mudou desde a projeção.")
        if assignment is None:
            assignment = PlatformOperationalAssignment(
                source_type="billing",
                source_id=subscription.id,
                owner_user_id=actor.id,
                state="in_progress",
                version=1,
            )
            self.db.add(assignment)
        else:
            assignment.owner_user_id = actor.id
            assignment.state = "in_progress"
            assignment.version += 1
        await self.db.flush()
        item.assigned_user_id = actor.id
        item.state = "in_progress"
        return _composite_version(
            _source_version(subscription.updated_at, subscription.created_at),
            assignment.version,
        )

    async def _command_workspace_invitation_revoke(
        self, *, actor: User, item: PlatformWorkItem
    ) -> str:
        invitation = await self.db.scalar(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.id == item.source_id)
            .with_for_update()
        )
        if invitation is None:
            raise PlatformWorkItemNotFoundError("Convite de conta não encontrado.")
        current = _composite_version(
            _value(invitation.status), _version_timestamp(invitation.last_sent_at)
        )
        if current != item.source_version:
            raise PlatformWorkItemConflictError("O convite mudou desde a projeção.")
        if _value(invitation.status) != "pending":
            raise PlatformWorkItemConflictError("Somente convites pendentes podem ser revogados.")
        invitation.status = InvitationStatus.revoked
        invitation.revoked_at = _now()
        item.state = "cancelled"
        item.closed_at = invitation.revoked_at
        return _composite_version("revoked", _version_timestamp(invitation.last_sent_at))

    async def _command_staff_invitation_revoke(self, *, actor: User, item: PlatformWorkItem) -> str:
        invitation = await self.db.scalar(
            select(PlatformStaffInvitation)
            .where(PlatformStaffInvitation.id == item.source_id)
            .with_for_update()
        )
        if invitation is None:
            raise PlatformWorkItemNotFoundError("Convite de funcionário não encontrado.")
        if invitation.role_package == "platform_admin" and not is_platform_root(actor):
            raise PlatformWorkItemForbiddenError(
                "Somente o titular revoga convite de administrador operacional."
            )
        if str(invitation.version) != item.source_version:
            raise PlatformWorkItemConflictError("O convite mudou desde a projeção.")
        if invitation.status != "pending":
            raise PlatformWorkItemConflictError("Somente convites pendentes podem ser revogados.")
        invitation.status = "revoked"
        invitation.revoked_at = _now()
        invitation.version += 1
        item.state = "cancelled"
        item.closed_at = invitation.revoked_at
        return str(invitation.version)

    async def _review_source_version(
        self, item: PlatformWorkItem
    ) -> tuple[Any, PlatformReviewRequest | None, str]:
        model = SuperAdminNotice if item.source_type == "publication" else PlatformConfigDocument
        source = await self.db.scalar(
            select(model).where(model.id == item.source_id).with_for_update()
        )
        if source is None:
            raise PlatformWorkItemNotFoundError("Registro de revisão não encontrado.")
        review = await self.db.scalar(
            select(PlatformReviewRequest)
            .where(
                PlatformReviewRequest.source_type == item.source_type,
                PlatformReviewRequest.source_id == item.source_id,
            )
            .with_for_update()
        )
        source_version = (
            source.version if item.source_type == "publication" else source.current_version
        )
        current = _composite_version(source_version, review.version if review else 0)
        return source, review, current

    async def _command_review_submit(self, *, actor: User, item: PlatformWorkItem) -> str:
        source, review, current = await self._review_source_version(item)
        if current != item.source_version:
            raise PlatformWorkItemConflictError("O conteúdo ou a revisão mudou desde a projeção.")
        if review is None:
            review = PlatformReviewRequest(
                source_type=item.source_type,
                source_id=item.source_id,
                state="in_review",
                author_user_id=actor.id,
                review_due_at=_now() + timedelta(days=1),
                version=1,
            )
            self.db.add(review)
        else:
            if review.state not in {"draft", "changes_requested"}:
                raise PlatformWorkItemConflictError(
                    "A revisão não pode ser submetida neste estado."
                )
            review.state = "in_review"
            review.author_user_id = actor.id
            review.review_due_at = _now() + timedelta(days=1)
            review.reviewed_at = None
            review.version += 1
        await self.db.flush()
        item.state = "open"
        item.reviewer_user_id = review.reviewer_user_id
        source_version = (
            source.version if item.source_type == "publication" else source.current_version
        )
        return _composite_version(source_version, review.version)

    async def _command_review_approve(self, *, actor: User, item: PlatformWorkItem) -> str:
        source, review, current = await self._review_source_version(item)
        if current != item.source_version:
            raise PlatformWorkItemConflictError("O conteúdo ou a revisão mudou desde a projeção.")
        if review is None or review.state != "in_review":
            raise PlatformWorkItemConflictError("Não existe revisão pendente para aprovação.")
        if review.author_user_id == actor.id and not is_platform_root(actor):
            raise PlatformWorkItemForbiddenError("O autor não pode aprovar o próprio conteúdo.")
        review.state = "approved"
        review.reviewer_user_id = actor.id
        review.reviewed_at = _now()
        review.version += 1
        await self.db.flush()
        item.state = "done"
        item.reviewer_user_id = actor.id
        item.closed_at = review.reviewed_at
        source_version = (
            source.version if item.source_type == "publication" else source.current_version
        )
        return _composite_version(source_version, review.version)

    async def reconcile(self, *, source_types: list[str] | None = None) -> list[AdapterResult]:
        requested = source_types or list(KNOWN_SOURCE_TYPES)
        unknown = sorted(set(requested) - set(KNOWN_SOURCE_TYPES))
        if unknown:
            raise PlatformWorkItemValidationError(f"Fontes desconhecidas: {', '.join(unknown)}")
        # Acquire every requested source lock in one canonical order. PostgreSQL
        # holds these transaction-scoped locks until the endpoint commits, so
        # concurrent workers cannot project or retire the same source at once.
        # The shared helper is intentionally a no-op under SQLite tests.
        requested_set = set(requested)
        for source_type in KNOWN_SOURCE_TYPES:
            if source_type in requested_set:
                await acquire_transaction_advisory_lock(
                    self.db,
                    namespace=RECONCILIATION_LOCK_NAMESPACE,
                    value=source_type,
                )
        results: list[AdapterResult] = []
        for source_type in requested:
            adapter = getattr(self, f"_reconcile_{source_type}")
            try:
                # Cada fonte usa um SAVEPOINT. Se uma adaptação falhar, as
                # projeções saudáveis feitas pelas fontes anteriores continuam
                # na transação externa e podem ser confirmadas pelo endpoint.
                async with self.db.begin_nested():
                    result = await adapter()
                results.append(result)
            except Exception as exc:
                # A falha fica restrita ao SAVEPOINT. A resposta preserva o
                # estado honesto da fonte sem descartar projeções anteriores.
                logger.error(
                    "platform_work_item.reconcile_source_failed source_type=%s error_type=%s",
                    source_type,
                    safe_error_type(exc),
                )
                results.append(
                    AdapterResult(
                        source_type,
                        "failed",
                        reason_code="source_reconciliation_failed",
                    )
                )
        return results

    async def _keyset_batches(
        self,
        model: Any,
        *,
        clauses: tuple[Any, ...] = (),
        batch_size: int | None = None,
        for_update: bool = False,
    ) -> AsyncIterator[list[Any]]:
        """Read an authoritative source completely with bounded memory.

        ``id`` is immutable and unique on every reconciled model.  A fixed
        high-watermark makes the scan finite while keyset pagination avoids
        offset drift when rows are concurrently inserted or removed.  Rows
        inserted behind the cursor are intentionally picked up by the next
        reconciliation run.
        """

        requested_size = self.reconciliation_batch_size if batch_size is None else batch_size
        if requested_size < 1:
            raise PlatformWorkItemValidationError(
                "O tamanho do lote de reconciliação deve ser positivo."
            )
        size = min(requested_size, MAX_RECONCILIATION_BATCH_SIZE)
        high_watermark = await self.db.scalar(
            select(model.id).where(*clauses).order_by(model.id.desc()).limit(1)
        )
        if high_watermark is None:
            return

        cursor: UUID | None = None
        while True:
            page_clauses = [*clauses, model.id <= high_watermark]
            if cursor is not None:
                page_clauses.append(model.id > cursor)
            query = select(model).where(*page_clauses).order_by(model.id.asc()).limit(size)
            if for_update:
                query = query.with_for_update()
            rows = list((await self.db.scalars(query)).all())
            if not rows:
                return
            yield rows
            cursor = rows[-1].id
            if len(rows) < size:
                return

    async def _upsert(
        self,
        payload: dict[str, Any],
        *,
        projected_at: datetime | None = None,
    ) -> bool:
        existing = await self.db.scalar(
            select(PlatformWorkItem)
            .where(PlatformWorkItem.dedupe_key == payload["dedupe_key"])
            .with_for_update()
        )
        projection_time = projected_at or _now()
        if existing is None:
            self.db.add(
                PlatformWorkItem(
                    **payload,
                    projected_at=projection_time,
                    projection_version=1,
                )
            )
            await self.db.flush()
            return True
        changed = False
        preserved = {"snoozed_until"}
        for key, value in payload.items():
            if key in preserved:
                continue
            if not _projection_values_equal(getattr(existing, key), value):
                setattr(existing, key, value)
                changed = True
        current_projection = _aware(existing.projected_at)
        if current_projection is None or current_projection < _aware(projection_time):
            existing.projected_at = projection_time
        if changed:
            existing.projection_version += 1
        await self.db.flush()
        return changed

    async def _project_rows(
        self,
        source_type: str,
        rows: list[Any],
        builder: Callable[[Any], dict[str, Any]],
        *,
        status: str = "available",
        reason_code: str | None = None,
        projected_at: datetime | None = None,
    ) -> AdapterResult:
        projected = unchanged = 0
        for row in rows:
            if await self._upsert(builder(row), projected_at=projected_at):
                projected += 1
            else:
                unchanged += 1
        return AdapterResult(source_type, status, projected, unchanged, reason_code)

    async def _project_keyset(
        self,
        source_type: str,
        model: Any,
        builder: Callable[[Any], dict[str, Any]],
        *,
        clauses: tuple[Any, ...] = (),
        projected_at: datetime | None = None,
    ) -> AdapterResult:
        projected = unchanged = 0
        async for rows in self._keyset_batches(model, clauses=clauses):
            batch_result = await self._project_rows(
                source_type,
                rows,
                builder,
                projected_at=projected_at,
            )
            projected += batch_result.projected
            unchanged += batch_result.unchanged
        return AdapterResult(source_type, "available", projected, unchanged)

    async def _retire_unseen(
        self,
        source_type: str,
        *,
        scan_started_at: datetime,
    ) -> None:
        """Retire only items not observed by a *completed* source scan.

        Callers invoke this method only after every source page was projected.
        The surrounding SAVEPOINT rolls back both projections and retirements
        if any page, upsert, or flush fails.
        """

        now = _now()
        clauses = (
            PlatformWorkItem.source_type == source_type,
            PlatformWorkItem.state.in_(OPEN_STATES - {"stale"}),
            PlatformWorkItem.projected_at < scan_started_at,
        )
        async for rows in self._keyset_batches(
            PlatformWorkItem,
            clauses=clauses,
            for_update=True,
        ):
            for row in rows:
                row.state = "stale"
                row.allowed_commands = []
                row.safe_summary = (
                    "A fonte deixou de pertencer a este recorte; "
                    "reconcilie ou abra o registro original."
                )
                row.projected_at = now
                row.projection_version += 1
            await self.db.flush()

    async def _workspace_names(self, workspace_ids: set[UUID]) -> dict[UUID, str]:
        if not workspace_ids:
            return {}
        rows = (
            await self.db.execute(
                select(Workspace.id, Workspace.name).where(Workspace.id.in_(workspace_ids))
            )
        ).all()
        return {row.id: row.name for row in rows}

    async def _reconcile_support_ticket(self) -> AdapterResult:
        def build(row: SupportTicket) -> dict[str, Any]:
            status = _value(row.status)
            if status in {"resolved", "closed"}:
                state, closed = "done", row.closed_at or row.resolved_at or row.updated_at
            elif status in {"waiting_customer"}:
                state, closed = "waiting_customer", None
            elif status in {"in_progress", "triage"}:
                state, closed = "in_progress", None
            else:
                state, closed = "open", None
            severity = _value(row.severity)
            priority = (
                "P0"
                if severity == "critical"
                else "P1"
                if severity == "high"
                else "P2"
                if severity == "medium"
                else "P3"
            )
            due = (
                row.first_response_due_at
                if row.first_responded_at is None
                else row.resolution_due_at
            )
            return {
                "source_type": "support_ticket",
                "source_id": row.id,
                "source_version": str(row.version),
                "dedupe_key": f"support_ticket:{row.id}",
                "kind": "task",
                "state": state,
                "priority": priority,
                "workspace_id": row.workspace_id,
                "assigned_user_id": row.assigned_to_user_id,
                "assigned_team_id": None,
                "requester_user_id": row.reporter_user_id,
                "reviewer_user_id": None,
                "safe_title": f"Ticket {row.public_number}",
                "safe_summary": (
                    f"Fila {_value(row.queue)} · severidade {severity} · estado {status}"
                ),
                "href": blackbox_source_href(
                    "support_ticket",
                    row.id,
                    workspace_id=row.workspace_id,
                ),
                "required_module": "support",
                "required_action": "view",
                "allowed_commands": ["assign_to_me"],
                "opened_at": row.created_at,
                "due_at": due,
                "snoozed_until": None,
                "closed_at": closed,
                "source_updated_at": _source_timestamp(row.updated_at, row.created_at),
                "payload_safe": {
                    "publicNumber": row.public_number,
                    "queue": _value(row.queue),
                    "severity": severity,
                    "status": status,
                },
            }

        return await self._project_keyset("support_ticket", SupportTicket, build)

    async def _reconcile_incident(self) -> AdapterResult:
        def build(row: PlatformIncident) -> dict[str, Any]:
            resolved = row.status == "resolved"
            priority = {"SEV1": "P0", "SEV2": "P1", "SEV3": "P2", "SEV4": "P3"}.get(
                row.severity.upper(), "P2"
            )
            return {
                "source_type": "incident",
                "source_id": row.id,
                "source_version": _source_version(row.updated_at, row.created_at),
                "dedupe_key": f"incident:{row.id}",
                "kind": "alert",
                "state": "done" if resolved else "in_progress",
                "priority": priority,
                "workspace_id": None,
                "assigned_user_id": row.owner_user_id,
                "assigned_team_id": None,
                "requester_user_id": row.created_by_user_id,
                "reviewer_user_id": None,
                "safe_title": row.title,
                "safe_summary": f"{row.severity} · {row.status}",
                "href": blackbox_source_href("incident", row.id),
                "required_module": "security",
                "required_action": "view",
                "allowed_commands": ["assign_to_me"],
                "opened_at": row.started_at,
                "due_at": None,
                "snoozed_until": None,
                "closed_at": row.resolved_at,
                "source_updated_at": _source_timestamp(row.updated_at, row.created_at),
                "payload_safe": {
                    "severity": row.severity,
                    "status": row.status,
                    "componentCount": len(row.components or []),
                },
            }

        return await self._project_keyset("incident", PlatformIncident, build)

    async def _reconcile_support_access(self) -> AdapterResult:
        def build(row: SupportAccessGrant) -> dict[str, Any]:
            state = _value(row.state)
            mapped = {
                "requested": "open",
                "approved": "open",
                "active": "in_progress",
                "expired": "done",
                "revoked": "cancelled",
                "rejected": "cancelled",
            }.get(state, "stale")
            due = (
                row.expires_at
                if state == "active"
                else row.requested_at + timedelta(hours=4)
                if state == "requested"
                else None
            )
            return {
                "source_type": "support_access",
                "source_id": row.id,
                "source_version": str(row.version),
                "dedupe_key": f"support_access:{row.id}",
                "kind": "approval",
                "state": mapped,
                "priority": "P1" if state == "requested" else "P2",
                "workspace_id": row.workspace_id,
                "assigned_user_id": row.requester_user_id,
                "assigned_team_id": None,
                "requester_user_id": row.requester_user_id,
                "reviewer_user_id": row.approver_user_id,
                "safe_title": "Acesso assistido",
                "safe_summary": (
                    f"Estado {state} · {len(row.requested_scopes or [])} escopos · "
                    f"{row.requested_duration_minutes} min"
                ),
                "href": blackbox_source_href(
                    "support_access",
                    row.id,
                    workspace_id=row.workspace_id,
                ),
                "required_module": "support_access",
                "required_action": "view",
                "allowed_commands": [],
                "opened_at": row.requested_at,
                "due_at": due,
                "snoozed_until": None,
                "closed_at": row.revoked_at or row.rejected_at or row.expired_at,
                "source_updated_at": _source_timestamp(row.updated_at, row.created_at),
                "payload_safe": {
                    "state": state,
                    "scopeCount": len(row.requested_scopes or []),
                    "durationMinutes": row.requested_duration_minutes,
                },
            }

        return await self._project_keyset("support_access", SupportAccessGrant, build)

    async def _reconcile_workspace_onboarding(self) -> AdapterResult:
        scan_started_at = _now()
        now = scan_started_at
        projected = unchanged = 0

        async for workspaces in self._keyset_batches(
            Workspace,
            clauses=(Workspace.deleted_at.is_(None),),
        ):
            workspace_ids = {row.id for row in workspaces}
            workspace_names = {row.id: row.name for row in workspaces}
            configured_workspace_ids: set[UUID] = set()

            async for milestones in self._keyset_batches(
                PlatformOnboardingMilestone,
                clauses=(PlatformOnboardingMilestone.workspace_id.in_(workspace_ids),),
            ):
                configured_workspace_ids.update(milestone.workspace_id for milestone in milestones)
                for milestone in milestones:
                    terminal = milestone.status in {"completed", "cancelled"}
                    payload = {
                        "source_type": "workspace_onboarding",
                        "source_id": milestone.id,
                        "source_version": _composite_version(
                            "milestone",
                            milestone.version,
                        ),
                        "dedupe_key": f"workspace_onboarding_milestone:{milestone.id}",
                        "kind": "task",
                        "state": "done"
                        if milestone.status == "completed"
                        else "cancelled"
                        if milestone.status == "cancelled"
                        else "blocked"
                        if milestone.status == "blocked"
                        else "in_progress"
                        if milestone.status == "in_progress"
                        else "open",
                        "priority": "P1"
                        if milestone.due_at and _aware(milestone.due_at) < now
                        else "P2",
                        "workspace_id": milestone.workspace_id,
                        "assigned_user_id": milestone.owner_user_id,
                        "assigned_team_id": None,
                        "requester_user_id": None,
                        "reviewer_user_id": None,
                        "safe_title": (
                            "Onboarding · "
                            f"{workspace_names.get(milestone.workspace_id, 'Conta')} · "
                            f"{milestone.label}"
                        ),
                        "safe_summary": f"Milestone {milestone.status}",
                        "href": blackbox_source_href(
                            "workspace_onboarding",
                            milestone.id,
                            workspace_id=milestone.workspace_id,
                        ),
                        "required_module": "onboarding",
                        "required_action": "view",
                        "allowed_commands": [] if terminal else ["mark_complete"],
                        "opened_at": milestone.created_at,
                        "due_at": milestone.due_at,
                        "snoozed_until": None,
                        "closed_at": milestone.completed_at if terminal else None,
                        "source_updated_at": _source_timestamp(
                            milestone.updated_at,
                            milestone.created_at,
                        ),
                        "payload_safe": {
                            "milestoneKey": milestone.key,
                            "status": milestone.status,
                            "sourceKind": "milestone",
                        },
                    }
                    changed = await self._upsert(
                        payload,
                        projected_at=scan_started_at,
                    )
                    projected += int(changed)
                    unchanged += int(not changed)

            for workspace in workspaces:
                if workspace.id in configured_workspace_ids:
                    continue
                completed = bool(workspace.onboarding_completed)
                payload = {
                    "source_type": "workspace_onboarding",
                    "source_id": workspace.id,
                    "source_version": _source_version(workspace.updated_at, workspace.created_at),
                    "dedupe_key": f"workspace_onboarding:{workspace.id}",
                    "kind": "task",
                    "state": "done" if completed else "open",
                    "priority": "P2",
                    "workspace_id": workspace.id,
                    "assigned_user_id": None,
                    "assigned_team_id": None,
                    "requester_user_id": None,
                    "reviewer_user_id": None,
                    "safe_title": f"Onboarding · {workspace.name}",
                    "safe_summary": (
                        f"Modo {_value(workspace.setup_mode)} · "
                        f"{'concluído' if completed else 'pendente'} · Prazo não configurado"
                    ),
                    "href": blackbox_source_href(
                        "workspace_onboarding",
                        workspace.id,
                        workspace_id=workspace.id,
                    ),
                    "required_module": "onboarding",
                    "required_action": "view",
                    "allowed_commands": [] if completed else ["mark_complete"],
                    "opened_at": workspace.created_at or now,
                    "due_at": None,
                    "snoozed_until": None,
                    "closed_at": workspace.updated_at if completed else None,
                    "source_updated_at": _source_timestamp(
                        workspace.updated_at, workspace.created_at
                    ),
                    "payload_safe": {
                        "setupMode": _value(workspace.setup_mode),
                        "completed": completed,
                        "deadlinePolicy": "not_configured",
                        "sourceKind": "workspace",
                    },
                }
                changed = await self._upsert(
                    payload,
                    projected_at=scan_started_at,
                )
                projected += int(changed)
                unchanged += int(not changed)

        # Only a fully completed source traversal may retire older projections.
        await self._retire_unseen(
            "workspace_onboarding",
            scan_started_at=scan_started_at,
        )
        return AdapterResult("workspace_onboarding", "available", projected, unchanged)

    async def _reconcile_billing(self) -> AdapterResult:
        now = _now()
        projected = unchanged = 0
        async for rows in self._keyset_batches(WorkspaceSubscription):
            source_ids = {row.id for row in rows}
            assignments = list(
                (
                    await self.db.scalars(
                        select(PlatformOperationalAssignment).where(
                            PlatformOperationalAssignment.source_type == "billing",
                            PlatformOperationalAssignment.source_id.in_(source_ids),
                        )
                    )
                ).all()
            )
            assignments_by_source = {row.source_id: row for row in assignments}
            names = await self._workspace_names({row.workspace_id for row in rows})

            def build(row: WorkspaceSubscription) -> dict[str, Any]:
                assignment = assignments_by_source.get(row.id)
                status = _value(row.status)
                trial_due = row.trial_ends_at if status == "trial" else None
                actionable = status == "inadimplente" or (
                    trial_due is not None and _aware(trial_due) <= now + timedelta(days=7)
                )
                state = "open" if actionable else "done"
                return {
                    "source_type": "billing",
                    "source_id": row.id,
                    "source_version": _composite_version(
                        _source_version(row.updated_at, row.created_at),
                        assignment.version if assignment else 0,
                    ),
                    "dedupe_key": f"billing:{row.id}",
                    "kind": "task",
                    "state": state,
                    "priority": "P1" if status == "inadimplente" else "P2",
                    "workspace_id": row.workspace_id,
                    "assigned_user_id": assignment.owner_user_id if assignment else None,
                    "assigned_team_id": assignment.team_id if assignment else None,
                    "requester_user_id": None,
                    "reviewer_user_id": None,
                    "safe_title": f"Cobrança · {names.get(row.workspace_id, 'Conta')}",
                    "safe_summary": (
                        f"Plano {_value(row.plano)} · estado comercial local {status}"
                    ),
                    "href": blackbox_source_href(
                        "billing",
                        row.id,
                        workspace_id=row.workspace_id,
                    ),
                    "required_module": "billing",
                    "required_action": "view",
                    "allowed_commands": ["assign_to_me"] if actionable else [],
                    "opened_at": row.created_at,
                    "due_at": assignment.due_at
                    if assignment and assignment.due_at
                    else row.updated_at + timedelta(hours=4)
                    if status == "inadimplente"
                    else trial_due,
                    "snoozed_until": None,
                    "closed_at": row.updated_at if not actionable else None,
                    "source_updated_at": _source_timestamp(
                        row.updated_at,
                        row.created_at,
                    ),
                    "payload_safe": {
                        "plan": _value(row.plano),
                        "status": status,
                        "evidenceLevel": "local_commercial",
                    },
                }

            batch_result = await self._project_rows("billing", rows, build)
            projected += batch_result.projected
            unchanged += batch_result.unchanged
        return AdapterResult("billing", "available", projected, unchanged)

    async def _reconcile_workspace_invitation(self) -> AdapterResult:
        now = _now()
        projected = unchanged = 0
        async for rows in self._keyset_batches(WorkspaceInvitation):
            names = await self._workspace_names({row.workspace_id for row in rows})

            def build(row: WorkspaceInvitation) -> dict[str, Any]:
                status = _value(row.status)
                terminal = status in {"accepted", "revoked", "expired"}
                return {
                    "source_type": "workspace_invitation",
                    "source_id": row.id,
                    "source_version": (f"{status}:{_version_timestamp(row.last_sent_at)}"),
                    "dedupe_key": f"workspace_invitation:{row.id}",
                    "kind": "task",
                    "state": "cancelled"
                    if status in {"revoked", "expired"}
                    else "done"
                    if terminal
                    else "open",
                    "priority": "P3",
                    "workspace_id": row.workspace_id,
                    "assigned_user_id": None,
                    "assigned_team_id": None,
                    "requester_user_id": row.invited_by_user_id,
                    "reviewer_user_id": None,
                    "safe_title": f"Convite · {names.get(row.workspace_id, 'Conta')}",
                    "safe_summary": f"Estado {status} · {row.send_count} envio(s)",
                    "href": blackbox_source_href(
                        "workspace_invitation",
                        row.id,
                        workspace_id=row.workspace_id,
                    ),
                    "required_module": "customers",
                    "required_action": "view",
                    "allowed_commands": ["revoke"] if status == "pending" else [],
                    "opened_at": row.last_sent_at,
                    "due_at": row.expires_at - timedelta(hours=24) if status == "pending" else None,
                    "snoozed_until": None,
                    "closed_at": row.accepted_at or row.revoked_at,
                    "source_updated_at": row.last_sent_at or now,
                    "payload_safe": {
                        "status": status,
                        "sendCount": row.send_count,
                        "invitationKind": "workspace",
                    },
                }

            batch_result = await self._project_rows(
                "workspace_invitation",
                rows,
                build,
            )
            projected += batch_result.projected
            unchanged += batch_result.unchanged
        return AdapterResult(
            "workspace_invitation",
            "available",
            projected,
            unchanged,
        )

    async def _reconcile_staff_invitation(self) -> AdapterResult:
        def build(row: PlatformStaffInvitation) -> dict[str, Any]:
            terminal = row.status in {"accepted", "revoked", "expired", "failed"}
            return {
                "source_type": "staff_invitation",
                "source_id": row.id,
                "source_version": str(row.version),
                "dedupe_key": f"staff_invitation:{row.id}",
                "kind": "task",
                "state": "done"
                if row.status == "accepted"
                else "cancelled"
                if terminal
                else "open",
                "priority": "P3",
                "workspace_id": None,
                "assigned_user_id": None,
                "assigned_team_id": None,
                "requester_user_id": row.invited_by_user_id,
                "reviewer_user_id": None,
                "safe_title": f"Convite de funcionário · {row.name}",
                "safe_summary": (
                    f"Função {row.role_package} · estado {row.status} · entrega externa não tentada"
                ),
                "href": blackbox_source_href("staff_invitation", row.id),
                "required_module": "staff",
                "required_action": "view",
                "allowed_commands": ["revoke"] if row.status == "pending" else [],
                "opened_at": row.created_at,
                "due_at": row.expires_at - timedelta(hours=24) if row.status == "pending" else None,
                "snoozed_until": None,
                "closed_at": row.accepted_at or row.revoked_at,
                "source_updated_at": row.updated_at,
                "payload_safe": {
                    "status": row.status,
                    "rolePackage": row.role_package,
                    "invitationKind": "staff",
                    "externalDelivery": "not_attempted",
                },
            }

        return await self._project_keyset(
            "staff_invitation",
            PlatformStaffInvitation,
            build,
        )

    async def _reconcile_publication(self) -> AdapterResult:
        projected = unchanged = 0
        async for rows in self._keyset_batches(SuperAdminNotice):
            source_ids = {row.id for row in rows}
            reviews = list(
                (
                    await self.db.scalars(
                        select(PlatformReviewRequest).where(
                            PlatformReviewRequest.source_type == "publication",
                            PlatformReviewRequest.source_id.in_(source_ids),
                        )
                    )
                ).all()
            )
            reviews_by_source = {row.source_id: row for row in reviews}

            def build(row: SuperAdminNotice) -> dict[str, Any]:
                status = _value(row.status)
                review = reviews_by_source.get(row.id)
                review_state = review.state if review else "draft"
                commands = (
                    ["submit_review"]
                    if status == "draft" and review_state in {"draft", "changes_requested"}
                    else ["approve"]
                    if status == "draft" and review_state == "in_review"
                    else []
                )
                return {
                    "source_type": "publication",
                    "source_id": row.id,
                    "source_version": _composite_version(
                        row.version,
                        review.version if review else 0,
                    ),
                    "dedupe_key": f"publication:{row.id}",
                    "kind": "approval" if status == "draft" else "task",
                    "state": "done"
                    if status == "published" or review_state == "approved"
                    else "cancelled"
                    if status == "cancelled"
                    else "open",
                    "priority": "P2",
                    "workspace_id": None,
                    "assigned_user_id": None,
                    "assigned_team_id": None,
                    "requester_user_id": row.created_by_user_id,
                    "reviewer_user_id": (review.reviewer_user_id if review else None),
                    "safe_title": f"Publicação {str(row.id)[-8:]}",
                    "safe_summary": (
                        f"Estado {status} · revisão {review_state} · "
                        f"audiência {_value(row.audience_kind)}"
                    ),
                    "href": blackbox_source_href("publication", row.id),
                    "required_module": "announcements",
                    "required_action": "view",
                    "allowed_commands": commands,
                    "opened_at": row.created_at,
                    "due_at": review.review_due_at
                    if review and review_state == "in_review"
                    else row.scheduled_at
                    if status == "scheduled"
                    else None,
                    "snoozed_until": None,
                    "closed_at": row.published_at or row.cancelled_at,
                    "source_updated_at": _source_timestamp(
                        row.updated_at,
                        row.created_at,
                    ),
                    "payload_safe": {
                        "status": status,
                        "reviewState": review_state,
                        "audienceKind": _value(row.audience_kind),
                        "channelCount": len(row.channels or []),
                    },
                }

            batch_result = await self._project_rows("publication", rows, build)
            projected += batch_result.projected
            unchanged += batch_result.unchanged
        return AdapterResult("publication", "available", projected, unchanged)

    async def _reconcile_configuration(self) -> AdapterResult:
        projected = unchanged = 0
        async for rows in self._keyset_batches(PlatformConfigDocument):
            source_ids = {row.id for row in rows}
            reviews = list(
                (
                    await self.db.scalars(
                        select(PlatformReviewRequest).where(
                            PlatformReviewRequest.source_type == "configuration",
                            PlatformReviewRequest.source_id.in_(source_ids),
                        )
                    )
                ).all()
            )
            reviews_by_source = {row.source_id: row for row in reviews}

            def build(row: PlatformConfigDocument) -> dict[str, Any]:
                review = reviews_by_source.get(row.id)
                review_state = review.state if review else "draft"
                commands = (
                    ["submit_review"]
                    if review_state in {"draft", "changes_requested"}
                    else ["approve"]
                    if review_state == "in_review"
                    else []
                )
                return {
                    "source_type": "configuration",
                    "source_id": row.id,
                    "source_version": _composite_version(
                        row.current_version,
                        review.version if review else 0,
                    ),
                    "dedupe_key": f"configuration:{row.id}",
                    "kind": "approval",
                    "state": "done"
                    if review_state in {"approved", "published"}
                    else "blocked"
                    if review_state == "publish_failed"
                    else "open",
                    "priority": "P2",
                    "workspace_id": None,
                    "assigned_user_id": None,
                    "assigned_team_id": None,
                    "requester_user_id": (
                        review.author_user_id if review else row.updated_by_user_id
                    ),
                    "reviewer_user_id": (review.reviewer_user_id if review else None),
                    "safe_title": f"Configuração · {row.namespace}",
                    "safe_summary": (f"Versão {row.current_version} · revisão {review_state}"),
                    "href": blackbox_source_href(
                        "configuration",
                        row.id,
                        extra_context={"namespace": row.namespace},
                    ),
                    "required_module": "settings",
                    "required_action": "view",
                    "allowed_commands": commands,
                    "opened_at": row.created_at,
                    "due_at": review.review_due_at
                    if review and review_state == "in_review"
                    else None,
                    "snoozed_until": None,
                    "closed_at": review.reviewed_at
                    if review_state in {"approved", "published"} and review
                    else None,
                    "source_updated_at": _source_timestamp(
                        row.updated_at,
                        row.created_at,
                    ),
                    "payload_safe": {
                        "namespace": row.namespace,
                        "currentVersion": row.current_version,
                        "reviewState": review_state,
                        "projectionAvailability": "available",
                    },
                }

            batch_result = await self._project_rows(
                "configuration",
                rows,
                build,
            )
            projected += batch_result.projected
            unchanged += batch_result.unchanged
        return AdapterResult("configuration", "available", projected, unchanged)
