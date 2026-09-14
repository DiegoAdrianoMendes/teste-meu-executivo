"""Durable, preview-first workspace bulk execution for the Blackbox control plane."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.platform_rbac import (
    PLATFORM_ROLE_PACKAGES,
    has_platform_permission,
    is_platform_root,
)
from app.models.billing import Plan, Subscription
from app.models.commercial_catalog import (
    CommercialContractStatus,
    WorkspaceCommercialContract,
)
from app.models.growth_crm import GrowthFunnelStage, GrowthProspect
from app.models.identity import InvitationStatus, WorkspaceInvitation
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_operations import (
    PlatformBulkJob,
    PlatformBulkJobItem,
    PlatformBulkPreview,
    PlatformSelectionItem,
    PlatformSelectionSet,
)
from app.models.platform_organization import PlatformStaffInvitation
from app.models.subscription_commercial import (
    WorkspacePlanCode,
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketStatus,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.commercial_legacy_bridge_service import CanonicalContractCommand
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.platform_service import PlatformService


class PlatformBulkError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class PlatformBulkService:
    SELECTION_TTL = timedelta(minutes=30)
    PREVIEW_TTL = timedelta(minutes=15)
    MAX_TARGETS = 50_000
    RESOURCE_ACTIONS = {
        "workspace": {
            "suspend",
            "reactivate",
            "complete_onboarding",
            "reopen_onboarding",
            "set_plan",
        },
        "customer_user": {"block", "unblock", "revoke_sessions"},
        "staff": {"suspend", "reactivate", "archive", "set_role", "revoke_sessions"},
        "lead": {"assign_owner", "set_stage", "apply_tag", "set_next_action"},
        "ticket": {"assign", "set_queue", "set_priority", "set_status"},
        "subscription": {"set_plan", "extend_trial", "suspend", "reactivate"},
        "workspace_invitation": {"revoke"},
        "staff_invitation": {"revoke"},
    }
    TICKET_TRANSITIONS = {
        "new": {"triage", "in_progress", "cancelled"},
        "triage": {"in_progress", "waiting_customer", "waiting_internal", "cancelled"},
        "in_progress": {
            "waiting_customer",
            "waiting_internal",
            "validating",
            "resolved",
            "cancelled",
        },
        "waiting_customer": {"in_progress", "validating", "resolved", "cancelled"},
        "waiting_internal": {"in_progress", "validating", "resolved", "cancelled"},
        "validating": {"in_progress", "resolved", "cancelled"},
        "resolved": {"closed", "in_progress"},
        "closed": {"in_progress"},
        "cancelled": {"in_progress"},
    }
    TERMINAL_JOB_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
    IRREVERSIBLE_ACTIONS = {
        ("customer_user", "revoke_sessions"),
        ("staff", "revoke_sessions"),
        ("workspace_invitation", "revoke"),
        ("staff_invitation", "revoke"),
    }

    @staticmethod
    def _requires_commercial_contract_management(
        resource_type: str,
        action: str,
    ) -> bool:
        return (
            resource_type in {"workspace", "subscription"}
            and action in {"set_plan", "suspend", "reactivate", "restore"}
        )

    def __init__(self, db: AsyncSession):
        self.db = db
        # S22: workspaces suspensos neste run, invalidados pelo worker após o commit.
        self.workspaces_to_invalidate: set[UUID] = set()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _canonical_hash(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _workspace_snapshot(cls, workspace: Workspace | None) -> dict[str, Any] | None:
        if workspace is None:
            return None
        subscription = workspace.subscription
        return {
            "id": str(workspace.id),
            "name": workspace.name,
            "status": workspace.status.value,
            "isActive": workspace.is_active,
            "onboardingCompleted": workspace.onboarding_completed,
            "planId": str(subscription.plan_id) if subscription else None,
        }

    @classmethod
    def _workspace_version(cls, workspace: Workspace | None) -> str:
        snapshot = cls._workspace_snapshot(workspace)
        return "missing" if snapshot is None else cls._canonical_hash(snapshot)

    @classmethod
    def _resource_snapshot(cls, resource_type: str, row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        if resource_type == "workspace":
            return cls._workspace_snapshot(row)
        if resource_type in {"customer_user", "staff"}:
            return {
                "id": str(row.id),
                "name": row.name,
                "active": row.is_active,
                "platformAccessActive": row.platform_access_active,
                "rolePackage": row.platform_role_package,
                "authEpoch": row.auth_epoch,
                "archived": row.deleted_at is not None,
            }
        if resource_type == "lead":
            return {
                "id": str(row.id),
                "name": row.nome_escritorio,
                "stage": row.estagio.value,
                "ownerUserId": str(row.owner_user_id) if row.owner_user_id else None,
                "tags": list((row.metadata_json or {}).get("tags", [])),
                "nextAction": (row.metadata_json or {}).get("nextAction"),
                "nextActionAt": (row.metadata_json or {}).get("nextActionAt"),
            }
        if resource_type == "ticket":
            return {
                "id": str(row.id),
                "publicNumber": row.public_number,
                "status": row.status.value,
                "queue": row.queue.value,
                "priority": row.priority.value,
                "assignedUserId": str(row.assigned_to_user_id) if row.assigned_to_user_id else None,
                "version": row.version,
            }
        if resource_type == "subscription":
            return {
                "id": str(row.id),
                "workspaceId": str(row.workspace_id),
                "plan": row.plano.value,
                "status": row.status.value,
                "trialEndsAt": row.trial_ends_at,
            }
        if resource_type == "workspace_invitation":
            return {
                "id": str(row.id),
                "workspaceId": str(row.workspace_id),
                "status": row.status.value,
                "sendCount": row.send_count,
                "lastSentAt": row.last_sent_at,
            }
        if resource_type == "staff_invitation":
            return {
                "id": str(row.id),
                "name": row.name,
                "rolePackage": row.role_package,
                "status": row.status,
                "version": row.version,
            }
        return None

    @classmethod
    def _resource_version(cls, resource_type: str, row: Any | None) -> str:
        snapshot = cls._resource_snapshot(resource_type, row)
        return "missing" if snapshot is None else cls._canonical_hash(snapshot)

    @staticmethod
    def _resource_label(resource_type: str, row: Any | None) -> str:
        if row is None:
            return "Registro indisponível"
        if resource_type == "workspace":
            return row.name
        if resource_type in {"customer_user", "staff", "staff_invitation"}:
            return row.name
        if resource_type == "lead":
            return row.nome_escritorio
        if resource_type == "ticket":
            return row.public_number
        if resource_type == "subscription":
            return f"Assinatura {str(row.workspace_id)[-8:]}"
        if resource_type == "workspace_invitation":
            return f"Convite {str(row.workspace_id)[-8:]}"
        return str(row.id)

    async def _load_resources(
        self, resource_type: str, resource_ids: Iterable[UUID], *, lock: bool = False
    ) -> dict[UUID, Any]:
        if resource_type == "workspace":
            if lock:
                rows = list(
                    (
                        await self.db.scalars(
                            select(Workspace)
                            .options(
                                selectinload(Workspace.subscription).selectinload(Subscription.plan)
                            )
                            .where(
                                Workspace.id.in_(list(dict.fromkeys(resource_ids))),
                                Workspace.deleted_at.is_(None),
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                return {row.id: row for row in rows}
            return await self._load_workspaces(resource_ids)
        model = {
            "customer_user": User,
            "staff": User,
            "lead": GrowthProspect,
            "ticket": SupportTicket,
            "subscription": WorkspaceSubscription,
            "workspace_invitation": WorkspaceInvitation,
            "staff_invitation": PlatformStaffInvitation,
        }.get(resource_type)
        if model is None:
            raise PlatformBulkError("BULK_RESOURCE_UNSUPPORTED", "Tipo de recurso não suportado.")
        ids = list(dict.fromkeys(resource_ids))
        result: dict[UUID, Any] = {}
        for start in range(0, len(ids), 500):
            statement = select(model).where(model.id.in_(ids[start : start + 500]))
            if resource_type == "customer_user":
                statement = statement.where(
                    User.is_internal_staff.is_(False), User.deleted_at.is_(None)
                )
            elif resource_type == "staff":
                statement = statement.where(User.is_internal_staff.is_(True))
            if lock:
                statement = statement.with_for_update()
            rows = list((await self.db.scalars(statement)).all())
            result.update({row.id: row for row in rows})
        return result

    async def _load_workspaces(self, resource_ids: Iterable[UUID]) -> dict[UUID, Workspace]:
        ids = list(dict.fromkeys(resource_ids))
        result: dict[UUID, Workspace] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            rows = list(
                (
                    await self.db.scalars(
                        select(Workspace)
                        .options(
                            selectinload(Workspace.subscription).selectinload(Subscription.plan)
                        )
                        .where(Workspace.id.in_(chunk), Workspace.deleted_at.is_(None))
                    )
                ).all()
            )
            result.update({row.id: row for row in rows})
        return result

    @staticmethod
    def _normalize_filters(resource_type: str, filters: dict[str, Any]) -> dict[str, Any]:
        common = {"status", "isActive", "search"}
        allowed_by_resource = {
            "workspace": common | {"onboardingCompleted", "setupMode"},
            "customer_user": common,
            "staff": common | {"rolePackage"},
            "lead": {"status", "search"},
            "ticket": {"status", "search"},
            "subscription": {"status", "search"},
            "workspace_invitation": {"status"},
            "staff_invitation": {"status", "rolePackage", "search"},
        }
        allowed = allowed_by_resource.get(resource_type)
        if allowed is None:
            raise PlatformBulkError("BULK_RESOURCE_UNSUPPORTED", "Tipo de recurso não suportado.")
        unknown = sorted(set(filters) - allowed)
        if unknown:
            raise PlatformBulkError(
                "BULK_FILTER_UNSUPPORTED",
                f"Unsupported workspace filters: {', '.join(unknown)}.",
            )
        normalized: dict[str, Any] = {}
        if "status" in filters and resource_type == "workspace":
            try:
                normalized["status"] = WorkspaceStatus(str(filters["status"])).value
            except ValueError as exc:
                raise PlatformBulkError("BULK_FILTER_INVALID", "Invalid workspace status.") from exc
        elif "status" in filters:
            normalized["status"] = str(filters["status"]).strip()
        for key in ("isActive", "onboardingCompleted"):
            if key in filters:
                if not isinstance(filters[key], bool):
                    raise PlatformBulkError("BULK_FILTER_INVALID", f"{key} must be boolean.")
                normalized[key] = filters[key]
        if "setupMode" in filters:
            try:
                normalized["setupMode"] = WorkspaceSetupMode(str(filters["setupMode"])).value
            except ValueError as exc:
                raise PlatformBulkError(
                    "BULK_FILTER_INVALID", "Invalid workspace setup mode."
                ) from exc
        if "rolePackage" in filters:
            normalized["rolePackage"] = str(filters["rolePackage"]).strip()
        if "search" in filters:
            search = str(filters["search"]).strip()
            if len(search) > 120:
                raise PlatformBulkError(
                    "BULK_FILTER_INVALID", "Search is limited to 120 characters."
                )
            if search:
                normalized["search"] = search
        return normalized

    async def create_selection(
        self,
        *,
        actor_user_id: UUID,
        resource_type: str,
        mode: str,
        ids: list[UUID],
        filters: dict[str, Any],
        sort: list[dict[str, str]],
        excluded_ids: list[UUID],
    ) -> PlatformSelectionSet:
        if resource_type not in self.RESOURCE_ACTIONS:
            raise PlatformBulkError("BULK_RESOURCE_UNSUPPORTED", "Tipo de recurso não suportado.")
        if mode not in {"explicit", "filter"}:
            raise PlatformBulkError("BULK_SELECTION_MODE_INVALID", "Invalid selection mode.")

        normalized_filters = self._normalize_filters(resource_type, filters)
        normalized_sort = sort or [{"field": "createdAt", "direction": "desc"}]
        for item in normalized_sort:
            if item.get("field") not in {"createdAt", "name", "id"}:
                raise PlatformBulkError(
                    "BULK_SORT_UNSUPPORTED", "Unsupported workspace sort field."
                )
            if item.get("direction") not in {"asc", "desc"}:
                raise PlatformBulkError("BULK_SORT_INVALID", "Invalid sort direction.")

        excluded = set(excluded_ids)
        if mode == "explicit":
            resource_ids = [item for item in dict.fromkeys(ids) if item not in excluded]
        else:
            model = {
                "workspace": Workspace,
                "customer_user": User,
                "staff": User,
                "lead": GrowthProspect,
                "ticket": SupportTicket,
                "subscription": WorkspaceSubscription,
                "workspace_invitation": WorkspaceInvitation,
                "staff_invitation": PlatformStaffInvitation,
            }[resource_type]
            statement = select(model.id)
            if excluded:
                statement = statement.where(model.id.not_in(excluded))
            if resource_type == "workspace":
                statement = statement.where(Workspace.deleted_at.is_(None))
            elif resource_type == "customer_user":
                statement = statement.where(
                    User.is_internal_staff.is_(False), User.deleted_at.is_(None)
                )
            elif resource_type == "staff":
                statement = statement.where(User.is_internal_staff.is_(True))
            if "status" in normalized_filters:
                status = normalized_filters["status"]
                column = {
                    "workspace": Workspace.status,
                    "customer_user": User.is_active,
                    "staff": User.platform_access_active,
                    "lead": GrowthProspect.estagio,
                    "ticket": SupportTicket.status,
                    "subscription": WorkspaceSubscription.status,
                    "workspace_invitation": WorkspaceInvitation.status,
                    "staff_invitation": PlatformStaffInvitation.status,
                }[resource_type]
                if resource_type in {"customer_user", "staff"}:
                    statement = statement.where(column.is_(status in {"active", "true", "1"}))
                else:
                    statement = statement.where(column == status)
            if "isActive" in normalized_filters:
                column = Workspace.is_active if resource_type == "workspace" else User.is_active
                statement = statement.where(column.is_(normalized_filters["isActive"]))
            if "onboardingCompleted" in normalized_filters:
                statement = statement.where(
                    Workspace.onboarding_completed.is_(normalized_filters["onboardingCompleted"])
                )
            if "setupMode" in normalized_filters:
                statement = statement.where(
                    Workspace.setup_mode == WorkspaceSetupMode(normalized_filters["setupMode"])
                )
            if "rolePackage" in normalized_filters:
                column = (
                    User.platform_role_package
                    if resource_type == "staff"
                    else PlatformStaffInvitation.role_package
                )
                statement = statement.where(column == normalized_filters["rolePackage"])
            if normalized_filters.get("search"):
                term = f"%{normalized_filters['search']}%"
                search_columns = {
                    "workspace": (Workspace.name, Workspace.slug),
                    "customer_user": (User.name, User.email),
                    "staff": (User.name, User.email),
                    "lead": (GrowthProspect.nome_escritorio, GrowthProspect.contato_nome),
                    "ticket": (SupportTicket.public_number,),
                    "subscription": (),
                    "staff_invitation": (PlatformStaffInvitation.name,),
                }.get(resource_type, ())
                if not search_columns:
                    raise PlatformBulkError(
                        "BULK_FILTER_UNSUPPORTED", "Busca não disponível para este recurso."
                    )
                statement = statement.where(or_(*(column.ilike(term) for column in search_columns)))
            created_column = {
                "workspace": Workspace.created_at,
                "customer_user": User.created_at,
                "staff": User.created_at,
                "lead": GrowthProspect.created_at,
                "ticket": SupportTicket.created_at,
                "subscription": WorkspaceSubscription.created_at,
                "workspace_invitation": WorkspaceInvitation.last_sent_at,
                "staff_invitation": PlatformStaffInvitation.created_at,
            }[resource_type]
            name_column = {
                "workspace": Workspace.name,
                "customer_user": User.name,
                "staff": User.name,
                "lead": GrowthProspect.nome_escritorio,
                "ticket": SupportTicket.public_number,
                "subscription": WorkspaceSubscription.id,
                "workspace_invitation": WorkspaceInvitation.id,
                "staff_invitation": PlatformStaffInvitation.name,
            }[resource_type]
            field_map = {"createdAt": created_column, "name": name_column, "id": model.id}
            for item in normalized_sort:
                column = field_map[item["field"]]
                statement = statement.order_by(
                    column.asc() if item["direction"] == "asc" else column.desc()
                )
            statement = statement.order_by(model.id.asc()).limit(self.MAX_TARGETS + 1)
            resource_ids = list((await self.db.scalars(statement)).all())

        if len(resource_ids) > self.MAX_TARGETS:
            raise PlatformBulkError(
                "BULK_SELECTION_TOO_LARGE",
                f"A selection may contain at most {self.MAX_TARGETS} targets.",
            )
        if not resource_ids:
            raise PlatformBulkError("BULK_SELECTION_EMPTY", "The selection has no targets.")

        resources = await self._load_resources(resource_type, resource_ids)
        canonical = {
            "resourceType": resource_type,
            "mode": mode,
            "ids": [str(item) for item in resource_ids] if mode == "explicit" else [],
            "filters": normalized_filters,
            "sort": normalized_sort,
            "excludedIds": sorted(str(item) for item in excluded),
        }
        selection = PlatformSelectionSet(
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            mode=mode,
            filters=normalized_filters,
            sort=normalized_sort,
            excluded_ids=[str(item) for item in sorted(excluded, key=str)],
            filter_hash=self._canonical_hash(canonical),
            target_count=len(resource_ids),
            status="active",
            expires_at=self._now() + self.SELECTION_TTL,
        )
        self.db.add(selection)
        await self.db.flush()
        self.db.add_all(
            [
                PlatformSelectionItem(
                    selection_set_id=selection.id,
                    resource_id=resource_id,
                    resource_version=self._resource_version(
                        resource_type, resources.get(resource_id)
                    ),
                    ordinal=ordinal,
                    initially_eligible=resource_id in resources,
                )
                for ordinal, resource_id in enumerate(resource_ids)
            ]
        )
        await self.db.flush()
        return selection

    async def get_selection(self, selection_id: UUID) -> PlatformSelectionSet:
        selection = await self.db.get(PlatformSelectionSet, selection_id)
        if selection is None:
            raise PlatformBulkError(
                "BULK_SELECTION_NOT_FOUND", "Selection not found.", status_code=404
            )
        if self._as_utc(selection.expires_at) <= self._now() and selection.status == "active":
            selection.status = "expired"
            await self.db.flush()
        return selection

    @classmethod
    def _validate_action(cls, resource_type: str, action: str, parameters: dict[str, Any]) -> None:
        if action not in cls.RESOURCE_ACTIONS.get(resource_type, set()):
            raise PlatformBulkError("BULK_ACTION_UNSUPPORTED", "Ação não suportada para o recurso.")
        if resource_type == "workspace" and action == "set_plan":
            plan_key = parameters.get("planKey") or parameters.get("plan_key")
            if plan_key not in PlatformService.PLAN_KEY_TO_TIER:
                raise PlatformBulkError(
                    "BULK_PLAN_INVALID", "A valid destination plan is required."
                )
        required_uuid = {
            ("lead", "assign_owner"): "ownerUserId",
            ("ticket", "assign"): "assignedUserId",
        }.get((resource_type, action))
        if required_uuid:
            try:
                UUID(str(parameters.get(required_uuid)))
            except (TypeError, ValueError) as exc:
                raise PlatformBulkError(
                    "BULK_PARAMETER_INVALID", f"{required_uuid} inválido."
                ) from exc
        enum_values = {
            ("lead", "set_stage"): ("stage", {item.value for item in GrowthFunnelStage}),
            ("ticket", "set_queue"): ("queue", {item.value for item in SupportTicketQueue}),
            ("ticket", "set_priority"): (
                "priority",
                {item.value for item in SupportTicketPriority},
            ),
            ("ticket", "set_status"): ("status", {item.value for item in SupportTicketStatus}),
            ("subscription", "set_plan"): ("plan", {item.value for item in WorkspacePlanCode}),
            ("staff", "set_role"): ("rolePackage", set()),
        }.get((resource_type, action))
        if enum_values:
            key, allowed = enum_values
            value = str(parameters.get(key) or "")
            if not value or (allowed and value not in allowed) or value == "platform_owner":
                raise PlatformBulkError("BULK_PARAMETER_INVALID", f"{key} inválido.")
            if resource_type == "staff" and action == "set_role":
                package = PLATFORM_ROLE_PACKAGES.get(value)
                if package is None or not package.assignable:
                    raise PlatformBulkError("BULK_PARAMETER_INVALID", "Pacote de função inválido.")
        if action == "extend_trial":
            days = parameters.get("days")
            if not isinstance(days, int) or not 1 <= days <= 90:
                raise PlatformBulkError("BULK_PARAMETER_INVALID", "days deve estar entre 1 e 90.")
        if resource_type == "lead" and action == "apply_tag":
            tag = str(parameters.get("tag") or "").strip()
            if not 1 <= len(tag) <= 80:
                raise PlatformBulkError("BULK_PARAMETER_INVALID", "Tag inválida.")
        if resource_type == "lead" and action == "set_next_action":
            text = str(parameters.get("nextAction") or "").strip()
            if not 2 <= len(text) <= 240:
                raise PlatformBulkError("BULK_PARAMETER_INVALID", "Próxima ação inválida.")
            raw_due_at = parameters.get("nextActionAt")
            if raw_due_at is not None:
                try:
                    datetime.fromisoformat(str(raw_due_at).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise PlatformBulkError(
                        "BULK_PARAMETER_INVALID", "Data da próxima ação inválida."
                    ) from exc

    @classmethod
    def _is_no_op(
        cls, resource_type: str, row: Any, action: str, parameters: dict[str, Any]
    ) -> bool:
        if resource_type == "workspace" and action == "suspend":
            return row.status == WorkspaceStatus.suspended and not row.is_active
        if resource_type == "workspace" and action == "reactivate":
            return row.status == WorkspaceStatus.active and row.is_active
        if resource_type == "workspace" and action == "complete_onboarding":
            return row.onboarding_completed
        if resource_type == "workspace" and action == "reopen_onboarding":
            return not row.onboarding_completed
        if resource_type == "workspace" and action == "set_plan":
            plan_key = parameters.get("planKey") or parameters.get("plan_key")
            expected = PlatformService.PLAN_KEY_TO_TIER[plan_key]
            return bool(row.subscription and row.subscription.plan.tier == expected)
        snapshot = cls._resource_snapshot(resource_type, row) or {}
        targets = {
            ("customer_user", "block"): snapshot.get("active") is False,
            ("customer_user", "unblock"): snapshot.get("active") is True,
            ("staff", "suspend"): snapshot.get("platformAccessActive") is False,
            ("staff", "reactivate"): snapshot.get("platformAccessActive") is True,
            ("staff", "archive"): snapshot.get("archived") is True,
            ("staff", "set_role"): snapshot.get("rolePackage") == parameters.get("rolePackage"),
            ("lead", "assign_owner"): snapshot.get("ownerUserId") == parameters.get("ownerUserId"),
            ("lead", "set_stage"): snapshot.get("stage") == parameters.get("stage"),
            ("ticket", "assign"): snapshot.get("assignedUserId")
            == parameters.get("assignedUserId"),
            ("ticket", "set_queue"): snapshot.get("queue") == parameters.get("queue"),
            ("ticket", "set_priority"): snapshot.get("priority") == parameters.get("priority"),
            ("ticket", "set_status"): snapshot.get("status") == parameters.get("status"),
            ("subscription", "set_plan"): snapshot.get("plan") == parameters.get("plan"),
            ("subscription", "suspend"): snapshot.get("status") == "pausada",
            ("subscription", "reactivate"): snapshot.get("status") == "ativa",
            ("workspace_invitation", "revoke"): snapshot.get("status") != "pending",
            ("staff_invitation", "revoke"): snapshot.get("status") != "pending",
        }
        return targets.get((resource_type, action), False)

    @classmethod
    def _invariant_block_reason(
        cls, resource_type: str, row: Any, action: str, parameters: dict[str, Any]
    ) -> str | None:
        if resource_type == "ticket" and action == "set_status":
            target = str(parameters.get("status") or "")
            if target not in cls.TICKET_TRANSITIONS.get(row.status.value, set()):
                return "ticket_transition_not_allowed"
        if resource_type == "subscription" and action == "reactivate":
            if row.status == WorkspaceSubscriptionStatus.cancelada:
                return "cancelled_subscription_requires_reconciliation"
        return None
        return False

    async def create_preview(
        self,
        *,
        actor_user_id: UUID,
        selection_set_id: UUID,
        action: str,
        parameters: dict[str, Any],
    ) -> PlatformBulkPreview:
        selection = await self.get_selection(selection_set_id)
        self._validate_action(selection.resource_type, action, parameters)
        if selection.actor_user_id != actor_user_id:
            raise PlatformBulkError(
                "BULK_SELECTION_FORBIDDEN", "Selection belongs to another actor.", status_code=403
            )
        if selection.status != "active":
            raise PlatformBulkError(
                "BULK_SELECTION_EXPIRED", "Selection has expired.", status_code=409
            )

        selection_items = list(
            (
                await self.db.scalars(
                    select(PlatformSelectionItem)
                    .where(PlatformSelectionItem.selection_set_id == selection.id)
                    .order_by(PlatformSelectionItem.ordinal.asc())
                )
            ).all()
        )
        resources = await self._load_resources(
            selection.resource_type, (item.resource_id for item in selection_items)
        )
        protected_customer_ids: set[UUID] = set()
        if selection.resource_type == "customer_user" and action == "block":
            owner_memberships = list(
                (
                    await self.db.execute(
                        select(WorkspaceMembership.user_id, WorkspaceMembership.workspace_id).where(
                            WorkspaceMembership.user_id.in_(resources),
                            WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                            WorkspaceMembership.status == MembershipStatus.active,
                        )
                    )
                ).all()
            )
            workspace_ids = {workspace_id for _user_id, workspace_id in owner_memberships}
            if workspace_ids:
                owner_counts = dict(
                    (
                        await self.db.execute(
                            select(WorkspaceMembership.workspace_id, func.count())
                            .where(
                                WorkspaceMembership.workspace_id.in_(workspace_ids),
                                WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                                WorkspaceMembership.status == MembershipStatus.active,
                                User.is_active.is_(True),
                                User.deleted_at.is_(None),
                            )
                            .join(User, User.id == WorkspaceMembership.user_id)
                            .group_by(WorkspaceMembership.workspace_id)
                        )
                    ).all()
                )
                protected_customer_ids = {
                    user_id
                    for user_id, workspace_id in owner_memberships
                    if owner_counts.get(workspace_id, 0) <= 1
                }
        actor = await self.db.get(User, actor_user_id)
        if actor is None:
            raise PlatformBulkError(
                "BULK_ACTOR_NOT_FOUND", "Operador não encontrado.", status_code=403
            )
        if self._requires_commercial_contract_management(
            selection.resource_type,
            action,
        ) and not has_platform_permission(actor, "commercial_contracts", "manage"):
            raise PlatformBulkError(
                "BULK_COMMERCIAL_PERMISSION_REQUIRED",
                "Alterar plano em lote exige commercial_contracts:manage.",
                status_code=403,
            )
        target_key = {
            ("lead", "assign_owner"): "ownerUserId",
            ("ticket", "assign"): "assignedUserId",
        }.get((selection.resource_type, action))
        if target_key:
            target = await self.db.get(User, UUID(str(parameters[target_key])))
            required_module = "sales" if selection.resource_type == "lead" else "support"
            if (
                target is None
                or not target.is_internal_staff
                or not target.platform_access_active
                or not has_platform_permission(target, required_module, "view")
            ):
                raise PlatformBulkError(
                    "BULK_ASSIGNEE_INVALID", "Responsável ativo e autorizado obrigatório."
                )
        if (
            selection.resource_type == "staff"
            and action == "set_role"
            and parameters.get("rolePackage") == "platform_admin"
            and not is_platform_root(actor)
        ):
            raise PlatformBulkError(
                "BULK_PROTECTED_STAFF", "Somente root atribui platform_admin.", status_code=403
            )
        eligible_targets: list[dict[str, str]] = []
        blockers: list[dict[str, Any]] = []
        sample: list[dict[str, Any]] = []
        no_op_count = 0
        changed_count = 0
        missing_count = 0
        protected_count = 0
        invariant_count = 0
        for item in selection_items:
            resource = resources.get(item.resource_id)
            state = "eligible"
            reason = None
            current_version = self._resource_version(selection.resource_type, resource)
            if resource is None:
                state = "blocked"
                reason = "resource_missing"
                missing_count += 1
            elif selection.resource_type == "staff" and (
                resource.platform_role_package == "platform_owner"
                or resource.id == actor_user_id
                or (
                    resource.platform_role_package == "platform_admin"
                    and not is_platform_root(actor)
                )
            ):
                state = "blocked"
                reason = "protected_staff_authority"
                protected_count += 1
            elif (
                selection.resource_type == "customer_user"
                and item.resource_id in protected_customer_ids
            ):
                state = "blocked"
                reason = "last_active_workspace_owner"
                protected_count += 1
            elif current_version != item.resource_version:
                state = "blocked"
                reason = "changed_since_selection"
                changed_count += 1
            elif self._is_no_op(selection.resource_type, resource, action, parameters):
                state = "no_op"
                reason = "already_in_target_state"
                no_op_count += 1
            elif invariant_reason := self._invariant_block_reason(
                selection.resource_type, resource, action, parameters
            ):
                state = "blocked"
                reason = invariant_reason
                invariant_count += 1
            else:
                eligible_targets.append(
                    {"resourceId": str(item.resource_id), "resourceVersion": current_version}
                )
            entry = {
                "resourceId": str(item.resource_id),
                "label": self._resource_label(selection.resource_type, resource),
                "state": state,
                "reason": reason,
            }
            if state == "blocked" and len(blockers) < 1_000:
                blockers.append(entry)
            if len(sample) < 50:
                sample.append(entry)

        risk = "critical" if action in {"suspend", "set_plan"} else "high"
        confirmation_phrase = None
        if len(eligible_targets) > 100 or risk == "critical":
            confirmation_phrase = f"CONFIRMAR {len(eligible_targets)} {action.upper()}"
        preview = PlatformBulkPreview(
            selection_set_id=selection.id,
            actor_user_id=actor_user_id,
            resource_type=selection.resource_type,
            action=action,
            parameters=parameters,
            status="ready",
            risk=risk,
            target_count=len(selection_items),
            eligible_count=len(eligible_targets),
            no_op_count=no_op_count,
            blocked_count=missing_count + changed_count + protected_count + invariant_count,
            warnings=[
                {
                    "code": "BULK_PARTIAL_EXECUTION",
                    "message": "Blocked and no-op targets will not be executed.",
                }
            ]
            if missing_count + changed_count + protected_count + invariant_count + no_op_count
            else [],
            blockers=blockers,
            impact_summary={
                "eligible": len(eligible_targets),
                "noOp": no_op_count,
                "missing": missing_count,
                "changedSinceSelection": changed_count,
                "protected": protected_count,
                "invariantBlocked": invariant_count,
            },
            sample=sample,
            eligible_targets=eligible_targets,
            confirmation_phrase=confirmation_phrase,
            expires_at=self._now() + self.PREVIEW_TTL,
        )
        self.db.add(preview)
        await self.db.flush()
        return preview

    async def create_deferred_preview(
        self,
        *,
        actor_user_id: UUID,
        selection_set_id: UUID,
        action: str,
        parameters: dict[str, Any],
    ) -> PlatformBulkPreview:
        """Persist a lightweight preview request for selections above the inline limit."""
        selection = await self.get_selection(selection_set_id)
        self._validate_action(selection.resource_type, action, parameters)
        if selection.actor_user_id != actor_user_id:
            raise PlatformBulkError(
                "BULK_SELECTION_FORBIDDEN", "Selection belongs to another actor.", status_code=403
            )
        if selection.status != "active":
            raise PlatformBulkError(
                "BULK_SELECTION_EXPIRED", "Selection has expired.", status_code=409
            )
        actor = await self.db.get(User, actor_user_id)
        if actor is None:
            raise PlatformBulkError(
                "BULK_ACTOR_NOT_FOUND", "Operador não encontrado.", status_code=403
            )
        preview = PlatformBulkPreview(
            selection_set_id=selection.id,
            actor_user_id=actor_user_id,
            resource_type=selection.resource_type,
            action=action,
            parameters=parameters,
            status="building",
            risk="critical" if action in {"suspend", "set_plan"} else "high",
            target_count=selection.target_count,
            eligible_count=0,
            no_op_count=0,
            blocked_count=0,
            warnings=[],
            blockers=[],
            impact_summary={"state": "building"},
            sample=[],
            eligible_targets=[],
            confirmation_phrase=None,
            expires_at=self._now() + self.PREVIEW_TTL,
        )
        self.db.add(preview)
        await self.db.flush()
        return preview

    async def build_deferred_preview(self, preview_id: UUID) -> PlatformBulkPreview:
        """Calculate and atomically replace the fields of a persisted preview request."""
        preview = await self.db.scalar(
            select(PlatformBulkPreview)
            .where(PlatformBulkPreview.id == preview_id)
            .with_for_update()
        )
        if preview is None:
            raise PlatformBulkError("BULK_PREVIEW_NOT_FOUND", "Preview not found.", status_code=404)
        if preview.status != "building":
            return preview
        try:
            calculated = await self.create_preview(
                actor_user_id=preview.actor_user_id,
                selection_set_id=preview.selection_set_id,
                action=preview.action,
                parameters=preview.parameters,
            )
        except PlatformBulkError as error:
            preview.status = "invalid"
            preview.blockers = [
                {"code": error.code, "message": error.message, "reason": error.code}
            ]
            preview.impact_summary = {"state": "invalid", "errorCode": error.code}
            await self.db.flush()
            return preview
        for field in (
            "resource_type",
            "action",
            "parameters",
            "status",
            "risk",
            "target_count",
            "eligible_count",
            "no_op_count",
            "blocked_count",
            "warnings",
            "blockers",
            "impact_summary",
            "sample",
            "eligible_targets",
            "confirmation_phrase",
            "expires_at",
        ):
            setattr(preview, field, getattr(calculated, field))
        await self.db.delete(calculated)
        await self.db.flush()
        return preview

    async def get_preview(self, preview_id: UUID) -> PlatformBulkPreview:
        preview = await self.db.get(PlatformBulkPreview, preview_id)
        if preview is None:
            raise PlatformBulkError("BULK_PREVIEW_NOT_FOUND", "Preview not found.", status_code=404)
        if self._as_utc(preview.expires_at) <= self._now() and preview.status == "ready":
            preview.status = "expired"
            await self.db.flush()
        return preview

    async def create_restoration_preview(
        self, *, actor_user_id: UUID, original_job_id: UUID
    ) -> PlatformBulkPreview:
        """Build a frozen preview for compatible successes of a finished bulk job."""
        original = await self.get_job(original_job_id)
        actor = await self.db.get(User, actor_user_id)
        if actor is None or (
            self._requires_commercial_contract_management(
                original.resource_type,
                "restore",
            )
            and not has_platform_permission(
                actor,
                "commercial_contracts",
                "manage",
            )
        ):
            raise PlatformBulkError(
                "BULK_COMMERCIAL_PERMISSION_REQUIRED",
                "Restaurar conta ou assinatura exige commercial_contracts:manage.",
                status_code=403,
            )
        if original.status not in self.TERMINAL_JOB_STATUSES:
            raise PlatformBulkError(
                "BULK_RESTORE_JOB_NOT_TERMINAL",
                "Aguarde a conclusão do lote antes de preparar a restauração.",
                status_code=409,
            )
        if (original.resource_type, original.action) in self.IRREVERSIBLE_ACTIONS:
            raise PlatformBulkError(
                "BULK_RESTORE_IRREVERSIBLE",
                "Este lote produziu efeito irreversível e não possui restauração automática.",
                status_code=409,
            )
        items = list(
            (
                await self.db.scalars(
                    select(PlatformBulkJobItem)
                    .where(
                        PlatformBulkJobItem.job_id == original.id,
                        PlatformBulkJobItem.status == "succeeded",
                    )
                    .order_by(PlatformBulkJobItem.ordinal.asc())
                )
            ).all()
        )
        resources = await self._load_resources(
            original.resource_type, (item.resource_id for item in items)
        )
        eligible: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        sample: list[dict[str, Any]] = []
        for item in items:
            resource = resources.get(item.resource_id)
            current_version = self._resource_version(original.resource_type, resource)
            expected_after = self._canonical_hash(item.after) if item.after else "missing"
            if item.before is None or item.after is None:
                reason = "snapshot_incomplete"
            elif resource is None:
                reason = "resource_missing"
            elif current_version != expected_after:
                reason = "changed_after_bulk"
            else:
                reason = None
            if reason:
                blockers.append({"resourceId": str(item.resource_id), "reason": reason})
            else:
                eligible.append(
                    {
                        "resourceId": str(item.resource_id),
                        "resourceVersion": current_version,
                        "targetSnapshot": item.before,
                    }
                )
            if len(sample) < 25:
                sample.append(
                    {
                        "resourceId": str(item.resource_id),
                        "label": self._resource_label(original.resource_type, resource),
                        "status": "blocked" if reason else "eligible",
                        "reason": reason,
                    }
                )
        if not items:
            raise PlatformBulkError(
                "BULK_RESTORE_NO_SUCCESSES", "O lote não possui sucessos restauráveis."
            )
        preview = PlatformBulkPreview(
            selection_set_id=original.selection_set_id,
            actor_user_id=actor_user_id,
            resource_type=original.resource_type,
            action="restore",
            parameters={
                "originalJobId": str(original.id),
                "originalAction": original.action,
            },
            status="ready",
            risk="critical",
            target_count=len(items),
            eligible_count=len(eligible),
            no_op_count=0,
            blocked_count=len(blockers),
            warnings=[
                {
                    "code": "BULK_RESTORE_CREATES_NEW_MUTATIONS",
                    "message": "A restauração cria novas mutações; não apaga o histórico original.",
                }
            ],
            blockers=blockers,
            impact_summary={
                "eligible": len(eligible),
                "changedAfterBulk": sum(
                    blocker["reason"] == "changed_after_bulk" for blocker in blockers
                ),
                "blocked": len(blockers),
                "originalJobId": str(original.id),
            },
            sample=sample,
            eligible_targets=eligible,
            confirmation_phrase=f"RESTAURAR {len(eligible)}",
            expires_at=self._now() + self.PREVIEW_TTL,
        )
        self.db.add(preview)
        await self.db.flush()
        return preview

    async def create_retry_preview(
        self, *, actor_user_id: UUID, original_job_id: UUID
    ) -> PlatformBulkPreview:
        """Revalidate only failed/conflicted/skipped targets through a fresh preview."""
        original = await self.get_job(original_job_id)
        if original.status not in self.TERMINAL_JOB_STATUSES:
            raise PlatformBulkError(
                "BULK_RETRY_JOB_NOT_TERMINAL",
                "Aguarde a conclusão do lote antes de preparar uma nova tentativa.",
                status_code=409,
            )
        if original.action == "restore":
            raise PlatformBulkError(
                "BULK_RETRY_RESTORE_REQUIRES_NEW_PREVIEW",
                "Revalide a restauração a partir do lote original.",
                status_code=409,
            )
        retry_ids = list(
            (
                await self.db.scalars(
                    select(PlatformBulkJobItem.resource_id)
                    .where(
                        PlatformBulkJobItem.job_id == original.id,
                        PlatformBulkJobItem.status.in_({"failed", "conflict", "skipped"}),
                    )
                    .order_by(PlatformBulkJobItem.ordinal.asc())
                )
            ).all()
        )
        if not retry_ids:
            raise PlatformBulkError(
                "BULK_RETRY_NO_FAILURES",
                "O lote não possui falhas, conflitos ou itens ignorados para tentar novamente.",
            )
        selection = await self.create_selection(
            actor_user_id=actor_user_id,
            resource_type=original.resource_type,
            mode="explicit",
            ids=retry_ids,
            filters={},
            sort=[],
            excluded_ids=[],
        )
        preview = await self.create_preview(
            actor_user_id=actor_user_id,
            selection_set_id=selection.id,
            action=original.action,
            parameters=original.parameters,
        )
        preview.parameters = {
            **preview.parameters,
            "retryOfJobId": str(original.id),
        }
        preview.warnings = [
            *preview.warnings,
            {
                "code": "BULK_RETRY_REVALIDATED",
                "message": (
                    "A nova tentativa usa uma seleção e uma prévia novas; "
                    "sucessos anteriores não serão repetidos."
                ),
            },
        ]
        preview.impact_summary = {
            **preview.impact_summary,
            "retryOfJobId": str(original.id),
            "retriedTargets": len(retry_ids),
        }
        await self.db.flush()
        return preview

    async def create_job(
        self,
        *,
        actor_user_id: UUID,
        preview_id: UUID,
        reason: str,
        confirmation_phrase: str | None,
        idempotency_key: str,
    ) -> PlatformBulkJob:
        existing = await self.db.scalar(
            select(PlatformBulkJob).where(
                PlatformBulkJob.actor_user_id == actor_user_id,
                PlatformBulkJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.preview_id != preview_id or existing.reason != reason:
                raise PlatformBulkError(
                    "BULK_IDEMPOTENCY_CONFLICT",
                    "A chave de idempotência já foi usada com outro payload.",
                    status_code=409,
                )
            return existing
        preview = await self.db.scalar(
            select(PlatformBulkPreview)
            .where(PlatformBulkPreview.id == preview_id)
            .with_for_update()
        )
        if preview is None:
            raise PlatformBulkError("BULK_PREVIEW_NOT_FOUND", "Preview not found.", status_code=404)
        if self._as_utc(preview.expires_at) <= self._now() and preview.status == "ready":
            preview.status = "expired"
        if preview.actor_user_id != actor_user_id:
            raise PlatformBulkError(
                "BULK_PREVIEW_FORBIDDEN", "Preview belongs to another actor.", status_code=403
            )
        actor = await self.db.get(User, actor_user_id)
        if actor is None or (
            self._requires_commercial_contract_management(
                preview.resource_type,
                preview.action,
            )
            and not has_platform_permission(actor, "commercial_contracts", "manage")
        ):
            raise PlatformBulkError(
                "BULK_COMMERCIAL_PERMISSION_REQUIRED",
                "A permissão comercial foi revogada antes da execução.",
                status_code=403,
            )
        if preview.status != "ready" or preview.consumed_at is not None:
            raise PlatformBulkError(
                "BULK_PREVIEW_UNAVAILABLE",
                "Preview is expired or already consumed.",
                status_code=409,
            )
        if preview.eligible_count == 0:
            raise PlatformBulkError("BULK_NO_ELIGIBLE_TARGETS", "Preview has no eligible targets.")
        if preview.confirmation_phrase and confirmation_phrase != preview.confirmation_phrase:
            raise PlatformBulkError(
                "BULK_CONFIRMATION_MISMATCH", "Confirmation phrase does not match."
            )

        now = self._now()
        job = PlatformBulkJob(
            preview_id=preview.id,
            selection_set_id=preview.selection_set_id,
            actor_user_id=actor_user_id,
            resource_type=preview.resource_type,
            idempotency_key=idempotency_key,
            action=preview.action,
            parameters=preview.parameters,
            reason=reason,
            status="queued",
            risk=preview.risk,
            requested_count=preview.target_count,
            eligible_count=preview.eligible_count,
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
        )
        self.db.add(job)
        await self.db.flush()
        self.db.add_all(
            [
                PlatformBulkJobItem(
                    job_id=job.id,
                    resource_id=UUID(target["resourceId"]),
                    resource_version=target["resourceVersion"],
                    ordinal=ordinal,
                    status="pending",
                    target=target.get("targetSnapshot"),
                )
                for ordinal, target in enumerate(preview.eligible_targets)
            ]
        )
        preview.consumed_at = now
        await self.db.flush()
        return job

    async def get_job(self, job_id: UUID, *, include_items: bool = False) -> PlatformBulkJob:
        job = await self.db.get(PlatformBulkJob, job_id)
        if job is None:
            raise PlatformBulkError("BULK_JOB_NOT_FOUND", "Bulk job not found.", status_code=404)
        return job

    async def list_jobs(
        self, *, limit: int, offset: int, resource_types: set[str] | None = None
    ) -> tuple[list[PlatformBulkJob], int]:
        count_statement = select(func.count(PlatformBulkJob.id))
        list_statement = select(PlatformBulkJob)
        if resource_types is not None:
            count_statement = count_statement.where(
                PlatformBulkJob.resource_type.in_(resource_types)
            )
            list_statement = list_statement.where(PlatformBulkJob.resource_type.in_(resource_types))
        total = int((await self.db.scalar(count_statement)) or 0)
        jobs = list(
            (
                await self.db.scalars(
                    list_statement.order_by(
                        PlatformBulkJob.created_at.desc(), PlatformBulkJob.id.desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return jobs, total

    async def list_job_items(
        self, job_id: UUID, *, limit: int = 200, offset: int = 0
    ) -> list[PlatformBulkJobItem]:
        return list(
            (
                await self.db.scalars(
                    select(PlatformBulkJobItem)
                    .where(PlatformBulkJobItem.job_id == job_id)
                    .order_by(PlatformBulkJobItem.ordinal.asc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )

    async def request_cancel(self, job_id: UUID) -> PlatformBulkJob:
        job = await self.db.scalar(
            select(PlatformBulkJob).where(PlatformBulkJob.id == job_id).with_for_update()
        )
        if job is None:
            raise PlatformBulkError("BULK_JOB_NOT_FOUND", "Bulk job not found.", status_code=404)
        if job.status in self.TERMINAL_JOB_STATUSES:
            return job
        now = self._now()
        job.cancel_requested_at = job.cancel_requested_at or now
        if job.status == "queued":
            pending = list(
                (
                    await self.db.scalars(
                        select(PlatformBulkJobItem).where(
                            PlatformBulkJobItem.job_id == job.id,
                            PlatformBulkJobItem.status == "pending",
                        )
                    )
                ).all()
            )
            for item in pending:
                item.status = "cancelled"
                item.processed_at = now
            job.cancelled_count = len(pending)
            job.status = "cancelled"
            job.completed_at = now
        else:
            job.status = "cancelling"
        await self.db.flush()
        return job

    async def _apply_workspace_action(
        self,
        workspace: Workspace,
        action: str,
        parameters: dict[str, Any],
        *,
        actor: User | None,
        idempotency_key: str,
        reason: str,
    ) -> None:
        if action == "suspend":
            workspace.status = WorkspaceStatus.suspended
            workspace.is_active = False
            self.workspaces_to_invalidate.add(workspace.id)
        elif action == "reactivate":
            workspace.status = WorkspaceStatus.active
            workspace.is_active = True
        elif action == "complete_onboarding":
            workspace.onboarding_completed = True
        elif action == "reopen_onboarding":
            workspace.onboarding_completed = False
        elif action == "set_plan":
            plan_key = parameters.get("planKey") or parameters.get("plan_key")
            await PlatformService(self.db)._ensure_workspace_plan(
                workspace.id,
                plan_key,
                actor_user_id=actor.id if actor is not None else None,
                idempotency_key=idempotency_key,
                reason=reason,
            )
        else:  # protected by preview validation; retained as a fail-closed worker guard
            raise PlatformBulkError("BULK_ACTION_UNSUPPORTED", "Unsupported workspace action.")

    async def _apply_resource_action(
        self,
        resource_type: str,
        row: Any,
        action: str,
        parameters: dict[str, Any],
        *,
        actor: User | None,
        idempotency_key: str,
        reason: str,
    ) -> None:
        if resource_type == "workspace":
            await self._apply_workspace_action(
                row,
                action,
                parameters,
                actor=actor,
                idempotency_key=idempotency_key,
                reason=reason,
            )
            return
        if resource_type == "customer_user":
            if action == "block":
                owned_workspace_ids = list(
                    (
                        await self.db.scalars(
                            select(WorkspaceMembership.workspace_id).where(
                                WorkspaceMembership.user_id == row.id,
                                WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                                WorkspaceMembership.status == MembershipStatus.active,
                            )
                        )
                    ).all()
                )
                for workspace_id in owned_workspace_ids:
                    active_owner_count = int(
                        (
                            await self.db.scalar(
                                select(func.count(WorkspaceMembership.id))
                                .join(User, User.id == WorkspaceMembership.user_id)
                                .where(
                                    WorkspaceMembership.workspace_id == workspace_id,
                                    WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                                    WorkspaceMembership.status == MembershipStatus.active,
                                    User.is_active.is_(True),
                                    User.deleted_at.is_(None),
                                )
                            )
                        )
                        or 0
                    )
                    if active_owner_count <= 1:
                        raise PlatformBulkError(
                            "BULK_LAST_WORKSPACE_OWNER",
                            "O último titular ativo da conta não pode ser bloqueado em lote.",
                            status_code=409,
                        )
                row.is_active = False
                row.auth_epoch += 1
            elif action == "unblock":
                row.is_active = True
            elif action == "revoke_sessions":
                row.auth_epoch += 1
            return
        if resource_type == "staff":
            if row.platform_role_package == "platform_owner" or row.id == getattr(
                actor, "id", None
            ):
                raise PlatformBulkError(
                    "BULK_PROTECTED_STAFF", "Autoridade protegida.", status_code=403
                )
            if row.platform_role_package == "platform_admin" and not is_platform_root(actor):
                raise PlatformBulkError(
                    "BULK_PROTECTED_STAFF", "Somente root opera platform_admin.", status_code=403
                )
            if action == "suspend":
                row.platform_access_active = False
                row.auth_epoch += 1
            elif action == "reactivate":
                row.platform_access_active = True
            elif action == "archive":
                row.deleted_at = self._now()
                row.platform_access_active = False
                row.is_active = False
                row.auth_epoch += 1
            elif action == "set_role":
                row.platform_role_package = parameters["rolePackage"]
                row.auth_epoch += 1
            elif action == "revoke_sessions":
                row.auth_epoch += 1
            return
        if resource_type == "lead":
            metadata = dict(row.metadata_json or {})
            if action == "assign_owner":
                row.owner_user_id = UUID(parameters["ownerUserId"])
            elif action == "set_stage":
                row.estagio = GrowthFunnelStage(parameters["stage"])
                row.stage_entered_at = self._now()
            elif action == "apply_tag":
                metadata["tags"] = sorted(
                    set(metadata.get("tags", [])) | {str(parameters.get("tag", "")).strip()}
                )
            elif action == "set_next_action":
                metadata.update(
                    {
                        "nextAction": parameters.get("nextAction"),
                        "nextActionAt": parameters.get("nextActionAt"),
                    }
                )
            row.metadata_json = metadata
            return
        if resource_type == "ticket":
            if action == "assign":
                row.assigned_to_user_id = UUID(parameters["assignedUserId"])
            elif action == "set_queue":
                row.queue = SupportTicketQueue(parameters["queue"])
            elif action == "set_priority":
                row.priority = SupportTicketPriority(parameters["priority"])
            elif action == "set_status":
                if parameters["status"] not in self.TICKET_TRANSITIONS.get(row.status.value, set()):
                    raise PlatformBulkError(
                        "BULK_TICKET_TRANSITION_INVALID",
                        "Transição de ticket não autorizada.",
                        status_code=409,
                    )
                row.status = SupportTicketStatus(parameters["status"])
            row.version += 1
            return
        if resource_type == "subscription":
            if action == "set_plan":
                contract_id = await self.db.scalar(
                    select(WorkspaceCommercialContract.id).where(
                        WorkspaceCommercialContract.workspace_id == row.workspace_id
                    )
                )
                if contract_id is not None:
                    await CanonicalContractCommand(
                        self.db,
                        control_plane=True,
                    ).ensure_core_plan(
                        workspace_id=row.workspace_id,
                        plan=parameters["plan"],
                        actor_user_id=actor.id if actor is not None else None,
                        idempotency_key=idempotency_key,
                        reason=reason,
                        project_legacy=True,
                    )
                else:
                    row.plano = WorkspacePlanCode(parameters["plan"])
            elif action == "extend_trial":
                row.trial_ends_at = (row.trial_ends_at or self._now()) + timedelta(
                    days=parameters["days"]
                )
            elif action == "suspend":
                contract_id = await self.db.scalar(
                    select(WorkspaceCommercialContract.id).where(
                        WorkspaceCommercialContract.workspace_id == row.workspace_id
                    )
                )
                if contract_id is not None:
                    await CanonicalContractCommand(
                        self.db,
                        control_plane=True,
                    ).change_status(
                        workspace_id=row.workspace_id,
                        target_status=CommercialContractStatus.paused,
                        actor_user_id=actor.id if actor is not None else None,
                        idempotency_key=idempotency_key,
                        reason=reason,
                        project_legacy=True,
                    )
                else:
                    row.status = WorkspaceSubscriptionStatus.pausada
            elif action == "reactivate":
                if row.status == WorkspaceSubscriptionStatus.cancelada:
                    raise PlatformBulkError(
                        "BULK_SUBSCRIPTION_RECONCILIATION_REQUIRED",
                        "Assinatura cancelada exige reconciliação individual.",
                        status_code=409,
                    )
                contract_id = await self.db.scalar(
                    select(WorkspaceCommercialContract.id).where(
                        WorkspaceCommercialContract.workspace_id == row.workspace_id
                    )
                )
                if contract_id is not None:
                    await CanonicalContractCommand(
                        self.db,
                        control_plane=True,
                    ).change_status(
                        workspace_id=row.workspace_id,
                        target_status=CommercialContractStatus.active,
                        actor_user_id=actor.id if actor is not None else None,
                        idempotency_key=idempotency_key,
                        reason=reason,
                        project_legacy=True,
                    )
                else:
                    row.status = WorkspaceSubscriptionStatus.ativa
            return
        if resource_type == "workspace_invitation" and action == "revoke":
            row.status = InvitationStatus.revoked
            row.revoked_at = self._now()
            return
        if resource_type == "staff_invitation" and action == "revoke":
            if row.role_package == "platform_admin" and not is_platform_root(actor):
                raise PlatformBulkError(
                    "BULK_PROTECTED_STAFF", "Somente root revoga este convite.", status_code=403
                )
            row.status = "revoked"
            row.revoked_at = self._now()
            row.version += 1
            return
        raise PlatformBulkError("BULK_ACTION_UNSUPPORTED", "Ação não suportada para o recurso.")

    async def _apply_restoration_snapshot(
        self,
        resource_type: str,
        row: Any,
        target: dict[str, Any] | None,
        *,
        actor: User,
        idempotency_key: str,
        reason: str,
    ) -> None:
        if not target:
            raise PlatformBulkError("BULK_RESTORE_TARGET_MISSING", "Snapshot alvo ausente.")
        restoration_at = self._now()
        if resource_type == "workspace":
            row.status = WorkspaceStatus(target["status"])
            row.is_active = bool(target["isActive"])
            row.onboarding_completed = bool(target["onboardingCompleted"])
            target_plan = target.get("planId")
            if target_plan:
                if row.subscription is None:
                    raise PlatformBulkError(
                        "BULK_RESTORE_PLAN_MISSING", "Assinatura da conta não existe."
                    )
                canonical_contract = await self.db.scalar(
                    select(WorkspaceCommercialContract.id).where(
                        WorkspaceCommercialContract.workspace_id == row.id
                    )
                )
                if canonical_contract is not None:
                    plan = await self.db.get(Plan, UUID(target_plan))
                    if plan is None:
                        raise PlatformBulkError(
                            "BULK_RESTORE_PLAN_MISSING",
                            "Plano alvo da restauração não existe.",
                        )
                    await CanonicalContractCommand(
                        self.db,
                        control_plane=True,
                    ).ensure_core_plan(
                        workspace_id=row.id,
                        plan=plan.tier,
                        actor_user_id=actor.id,
                        idempotency_key=idempotency_key,
                        reason=reason,
                        project_legacy=True,
                    )
                else:
                    row.subscription.plan_id = UUID(target_plan)
            return
        if resource_type == "customer_user":
            row.is_active = bool(target["active"])
            row.auth_epoch += 1
            return
        if resource_type == "staff":
            if row.platform_role_package == "platform_owner" or row.id == actor.id:
                raise PlatformBulkError(
                    "BULK_PROTECTED_STAFF", "Autoridade protegida.", status_code=403
                )
            target_role = target.get("rolePackage")
            if target_role == "platform_owner":
                raise PlatformBulkError(
                    "BULK_PROTECTED_STAFF", "Restauração nunca concede root.", status_code=403
                )
            if (
                (row.platform_role_package == "platform_admin" or target_role == "platform_admin")
                and not is_platform_root(actor)
            ):
                raise PlatformBulkError(
                    "BULK_PROTECTED_STAFF", "Somente root opera platform_admin.", status_code=403
                )
            row.platform_role_package = target_role
            row.platform_access_active = bool(target.get("platformAccessActive"))
            row.is_active = bool(target.get("active"))
            row.deleted_at = None if not target.get("archived") else (row.deleted_at or self._now())
            row.auth_epoch += 1
            return
        if resource_type == "lead":
            row.estagio = GrowthFunnelStage(target["stage"])
            row.owner_user_id = UUID(target["ownerUserId"]) if target.get("ownerUserId") else None
            metadata = dict(row.metadata_json or {})
            metadata["tags"] = list(target.get("tags") or [])
            metadata["nextAction"] = target.get("nextAction")
            metadata["nextActionAt"] = target.get("nextActionAt")
            row.metadata_json = metadata
            row.stage_entered_at = self._now()
            return
        if resource_type == "ticket":
            row.status = SupportTicketStatus(target["status"])
            row.queue = SupportTicketQueue(target["queue"])
            row.priority = SupportTicketPriority(target["priority"])
            row.assigned_to_user_id = (
                UUID(target["assignedUserId"]) if target.get("assignedUserId") else None
            )
            row.version += 1
            return
        if resource_type == "subscription":
            canonical_contract = await self.db.scalar(
                select(WorkspaceCommercialContract).where(
                    WorkspaceCommercialContract.workspace_id == row.workspace_id
                )
            )
            if canonical_contract is not None:
                command = CanonicalContractCommand(
                    self.db,
                    control_plane=True,
                )
                await command.ensure_core_plan(
                    workspace_id=row.workspace_id,
                    plan=target["plan"],
                    actor_user_id=actor.id,
                    idempotency_key=idempotency_key,
                    reason=reason,
                    project_legacy=True,
                )
                target_status = {
                    WorkspaceSubscriptionStatus.trial: CommercialContractStatus.trialing,
                    WorkspaceSubscriptionStatus.ativa: CommercialContractStatus.active,
                    WorkspaceSubscriptionStatus.inadimplente: CommercialContractStatus.past_due,
                    WorkspaceSubscriptionStatus.pausada: CommercialContractStatus.paused,
                    WorkspaceSubscriptionStatus.cancelada: CommercialContractStatus.cancelled,
                }[WorkspaceSubscriptionStatus(target["status"])]
                if canonical_contract.status is not target_status:
                    await command.change_status(
                        workspace_id=row.workspace_id,
                        target_status=target_status,
                        actor_user_id=actor.id,
                        idempotency_key=f"{idempotency_key}:status",
                        reason=reason,
                        project_legacy=True,
                        trial_ends_at=(
                            datetime.fromisoformat(
                                str(target["trialEndsAt"]).replace("Z", "+00:00")
                            )
                            if target.get("trialEndsAt")
                            else None
                        ),
                        effective_at=restoration_at,
                    )
                return
            else:
                row.plano = WorkspacePlanCode(target["plan"])
            row.status = WorkspaceSubscriptionStatus(target["status"])
            raw_trial = target.get("trialEndsAt")
            row.trial_ends_at = (
                datetime.fromisoformat(str(raw_trial).replace("Z", "+00:00"))
                if raw_trial
                else None
            )
            return
        raise PlatformBulkError(
            "BULK_RESTORE_UNSUPPORTED", "Este tipo de recurso não possui restauração."
        )

    async def _finish_cancel(self, job: PlatformBulkJob) -> PlatformBulkJob:
        now = self._now()
        pending = list(
            (
                await self.db.scalars(
                    select(PlatformBulkJobItem).where(
                        PlatformBulkJobItem.job_id == job.id,
                        PlatformBulkJobItem.status == "pending",
                    )
                )
            ).all()
        )
        for item in pending:
            item.status = "cancelled"
            item.processed_at = now
        job.cancelled_count += len(pending)
        job.status = "partial" if job.processed_count else "cancelled"
        job.completed_at = now
        job.heartbeat_at = now
        await self.db.commit()
        return job

    async def _bulk_parent_event(self, job: PlatformBulkJob) -> PlatformAuditEvent:
        existing = await self.db.scalar(
            select(PlatformAuditEvent).where(
                PlatformAuditEvent.bulk_job_id == job.id,
                PlatformAuditEvent.resource_type == "bulk_job",
                PlatformAuditEvent.action == f"{job.action}.execute",
            )
        )
        if existing is not None:
            return existing
        actor = await self.db.get(User, job.actor_user_id)
        role = actor.platform_role_package if actor else None
        return await PlatformAuditEventService(self.db).record(
            PlatformAuditEventCreate(
                actor_id=job.actor_user_id,
                actor_role_snapshot=role or "unknown",
                is_platform_root=is_platform_root(actor),
                session_type="normal",
                module="bulk_operations",
                action=f"{job.action}.execute",
                resource_type="bulk_job",
                resource_id=str(job.id),
                resource_label_snapshot=f"Lote {job.action}",
                result="success",
                before={"status": "queued", "eligibleCount": job.eligible_count},
                after={"status": "running", "eligibleCount": job.eligible_count},
                reason=job.reason,
                reason_source="user",
                correlation_id=job.correlation_id,
                operation_id=job.operation_id,
                bulk_job_id=job.id,
                route="platform_bulk_tasks.run_job",
                method="WORKER",
            )
        )

    async def _audit_bulk_item(
        self,
        *,
        job: PlatformBulkJob,
        item: PlatformBulkJobItem,
        parent_event: PlatformAuditEvent,
        result: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        error_code: str | None = None,
        resource_type: str,
    ) -> None:
        actor = await self.db.get(User, job.actor_user_id)
        role = actor.platform_role_package if actor else None
        await PlatformAuditEventService(self.db).record(
            PlatformAuditEventCreate(
                actor_id=job.actor_user_id,
                actor_role_snapshot=role or "unknown",
                is_platform_root=is_platform_root(actor),
                workspace_id=(
                    item.resource_id
                    if resource_type == "workspace"
                    else UUID(str((after or before or {}).get("workspaceId")))
                    if (after or before or {}).get("workspaceId")
                    else None
                ),
                workspace_label_snapshot=(after or before or {}).get("name"),
                session_type="normal",
                module={
                    "workspace": "customers",
                    "customer_user": "customers",
                    "staff": "staff",
                    "lead": "sales",
                    "ticket": "support",
                    "subscription": "billing",
                    "workspace_invitation": "customers",
                    "staff_invitation": "staff",
                }[resource_type],
                action=f"bulk.{job.action}",
                resource_type=resource_type,
                resource_id=str(item.resource_id),
                resource_label_snapshot=(after or before or {}).get("name"),
                result=result,
                before=before,
                after=after,
                reason=job.reason,
                reason_source="user",
                correlation_id=job.correlation_id,
                operation_id=job.operation_id,
                parent_event_id=parent_event.id,
                bulk_job_id=job.id,
                error_code=error_code,
                route="platform_bulk_tasks.run_job",
                method="WORKER",
            )
        )

    async def run_job(self, job_id: UUID, *, max_items: int | None = None) -> PlatformBulkJob:
        """Execute a queued job. Intended entrypoint for a Celery worker."""
        job = await self.get_job(job_id)
        if job.status in self.TERMINAL_JOB_STATUSES:
            return job
        resource_type = job.resource_type
        actor = await self.db.get(User, job.actor_user_id)
        module = {
            "workspace": "customers",
            "customer_user": "customers",
            "staff": "staff",
            "lead": "sales",
            "ticket": "support",
            "subscription": "billing",
            "workspace_invitation": "customers",
            "staff_invitation": "staff",
        }[resource_type]
        if (
            actor is None
            or not has_platform_permission(actor, "bulk_operations", "manage")
            or not has_platform_permission(actor, module, "manage")
            or (
                self._requires_commercial_contract_management(
                    resource_type,
                    job.action,
                )
                and not has_platform_permission(
                    actor,
                    "commercial_contracts",
                    "manage",
                )
            )
        ):
            previous_status = job.status
            pending = list(
                (
                    await self.db.scalars(
                        select(PlatformBulkJobItem).where(
                            PlatformBulkJobItem.job_id == job.id,
                            PlatformBulkJobItem.status == "pending",
                        )
                    )
                ).all()
            )
            for pending_item in pending:
                pending_item.status = "skipped"
                pending_item.error_code = "BULK_PERMISSION_REVOKED"
                pending_item.error_detail = "Permissão revalidada e indisponível no worker."
                pending_item.processed_at = self._now()
            job.skipped_count += len(pending)
            job.processed_count += len(pending)
            job.status = "partial" if job.succeeded_count else "failed"
            job.completed_at = self._now()
            job.heartbeat_at = job.completed_at
            await PlatformAuditEventService(self.db).record(
                PlatformAuditEventCreate(
                    actor_id=job.actor_user_id,
                    actor_role_snapshot=(actor.platform_role_package if actor else "unknown"),
                    is_platform_root=is_platform_root(actor),
                    session_type="normal",
                    module="bulk_operations",
                    action=f"{job.action}.execute",
                    resource_type="bulk_job",
                    resource_id=str(job.id),
                    resource_label_snapshot=f"Lote {job.action}",
                    result="denied",
                    before={"status": previous_status, "resourceType": resource_type},
                    after={"status": job.status, "skippedCount": len(pending)},
                    reason=job.reason,
                    reason_source="user",
                    correlation_id=job.correlation_id,
                    operation_id=job.operation_id,
                    bulk_job_id=job.id,
                    error_code="BULK_PERMISSION_REVOKED",
                    route="platform_bulk_tasks.run_job",
                    method="WORKER",
                )
            )
            await self.db.commit()
            return job
        if job.status == "cancelling" or job.cancel_requested_at is not None:
            return await self._finish_cancel(job)
        if job.status == "queued":
            job.status = "running"
            job.started_at = self._now()
            job.heartbeat_at = job.started_at
            await self._bulk_parent_event(job)
            await self.db.commit()
        elif job.status != "running":
            raise PlatformBulkError(
                "BULK_JOB_STATE_INVALID",
                "Job cannot be executed in its current state.",
                status_code=409,
            )

        parent_event = await self._bulk_parent_event(job)
        await self.db.commit()
        executed = 0
        while max_items is None or executed < max_items:
            job = await self.get_job(job_id)
            if job.cancel_requested_at is not None or job.status == "cancelling":
                return await self._finish_cancel(job)
            item = await self.db.scalar(
                select(PlatformBulkJobItem)
                .where(
                    PlatformBulkJobItem.job_id == job.id,
                    PlatformBulkJobItem.status == "pending",
                )
                .order_by(PlatformBulkJobItem.ordinal.asc())
                .limit(1)
                .with_for_update()
            )
            if item is None:
                break
            now = self._now()
            item.started_at = now
            item.attempts += 1
            resource = (
                await self._load_resources(resource_type, [item.resource_id], lock=True)
            ).get(item.resource_id)
            if (
                resource is None
                or self._resource_version(resource_type, resource) != item.resource_version
            ):
                item.status = "conflict"
                item.error_code = "BULK_RESOURCE_VERSION_CONFLICT"
                item.error_detail = "Resource changed after preview or is no longer available."
                item.processed_at = now
                job.conflicted_count += 1
                job.processed_count += 1
                job.heartbeat_at = now
                await self._audit_bulk_item(
                    job=job,
                    item=item,
                    parent_event=parent_event,
                    result="failed",
                    before=self._resource_snapshot(resource_type, resource),
                    after=None,
                    error_code=item.error_code,
                    resource_type=resource_type,
                )
                await self.db.commit()
                executed += 1
                continue

            before = self._resource_snapshot(resource_type, resource)
            try:
                if job.action == "restore":
                    await self._apply_restoration_snapshot(
                        resource_type,
                        resource,
                        item.target,
                        actor=actor,
                        idempotency_key=f"bulk:{job.id}:{item.id}:restore",
                        reason=job.reason,
                    )
                else:
                    await self._apply_resource_action(
                        resource_type,
                        resource,
                        job.action,
                        job.parameters,
                        actor=actor,
                        idempotency_key=f"bulk:{job.id}:{item.id}",
                        reason=job.reason,
                    )
                await self.db.flush()
                refreshed = (await self._load_resources(resource_type, [resource.id])).get(
                    resource.id
                )
                item.before = before
                item.after = self._resource_snapshot(resource_type, refreshed or resource)
                item.status = "succeeded"
                item.processed_at = self._now()
                job.succeeded_count += 1
                job.processed_count += 1
                job.heartbeat_at = item.processed_at
                await self._audit_bulk_item(
                    job=job,
                    item=item,
                    parent_event=parent_event,
                    result="success",
                    before=item.before,
                    after=item.after,
                    resource_type=resource_type,
                )
                await self.db.commit()
            except Exception as exc:
                await self.db.rollback()
                job = await self.get_job(job_id)
                item = await self.db.get(PlatformBulkJobItem, item.id)
                if item is None:
                    raise
                item.status = "failed"
                item.error_code = (
                    exc.code if isinstance(exc, PlatformBulkError) else "BULK_ITEM_EXECUTION_FAILED"
                )
                item.error_detail = f"Operation failed ({type(exc).__name__})."
                item.processed_at = self._now()
                job.failed_count += 1
                job.processed_count += 1
                job.heartbeat_at = item.processed_at
                await self._audit_bulk_item(
                    job=job,
                    item=item,
                    parent_event=parent_event,
                    result="failed",
                    before=before,
                    after=None,
                    error_code=item.error_code,
                    resource_type=resource_type,
                )
                await self.db.commit()
            executed += 1

        job = await self.get_job(job_id)
        remaining = int(
            (
                await self.db.scalar(
                    select(func.count(PlatformBulkJobItem.id)).where(
                        PlatformBulkJobItem.job_id == job.id,
                        PlatformBulkJobItem.status == "pending",
                    )
                )
            )
            or 0
        )
        if remaining == 0:
            if job.failed_count or job.conflicted_count or job.cancelled_count:
                job.status = "partial" if job.succeeded_count else "failed"
            else:
                job.status = "succeeded"
            job.completed_at = self._now()
        job.heartbeat_at = self._now()
        await self.db.commit()
        return job

    @staticmethod
    def selection_payload(selection: PlatformSelectionSet) -> dict[str, Any]:
        return {
            "id": selection.id,
            "actor_user_id": selection.actor_user_id,
            "resource_type": selection.resource_type,
            "mode": selection.mode,
            "filters": selection.filters,
            "sort": selection.sort,
            "excluded_ids": selection.excluded_ids,
            "filter_hash": selection.filter_hash,
            "target_count": selection.target_count,
            "status": selection.status,
            "created_at": selection.created_at,
            "expires_at": selection.expires_at,
        }

    @staticmethod
    def preview_payload(preview: PlatformBulkPreview) -> dict[str, Any]:
        return {
            "id": preview.id,
            "selection_set_id": preview.selection_set_id,
            "resource_type": preview.resource_type,
            "action": preview.action,
            "status": preview.status,
            "risk": preview.risk,
            "target_count": preview.target_count,
            "eligible_count": preview.eligible_count,
            "no_op_count": preview.no_op_count,
            "blocked_count": preview.blocked_count,
            "warnings": preview.warnings,
            "blockers": preview.blockers,
            "impact_summary": preview.impact_summary,
            "sample": preview.sample,
            "confirmation_phrase": preview.confirmation_phrase,
            "created_at": preview.created_at,
            "expires_at": preview.expires_at,
        }

    @staticmethod
    def item_payload(item: PlatformBulkJobItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "resource_id": item.resource_id,
            "ordinal": item.ordinal,
            "status": item.status,
            "attempts": item.attempts,
            "before": item.before,
            "after": item.after,
            "target": item.target,
            "error_code": item.error_code,
            "error_detail": item.error_detail,
            "started_at": item.started_at,
            "processed_at": item.processed_at,
        }

    @classmethod
    def job_payload(
        cls, job: PlatformBulkJob, *, items: list[PlatformBulkJobItem] | None = None
    ) -> dict[str, Any]:
        completed = job.processed_count + job.cancelled_count
        progress = round((completed / max(job.eligible_count, 1)) * 100, 2)
        return {
            "id": job.id,
            "preview_id": job.preview_id,
            "selection_set_id": job.selection_set_id,
            "resource_type": job.resource_type,
            "action": job.action,
            "parameters": job.parameters,
            "reason": job.reason,
            "status": job.status,
            "risk": job.risk,
            "requested_count": job.requested_count,
            "eligible_count": job.eligible_count,
            "processed_count": job.processed_count,
            "succeeded_count": job.succeeded_count,
            "failed_count": job.failed_count,
            "conflicted_count": job.conflicted_count,
            "skipped_count": job.skipped_count,
            "cancelled_count": job.cancelled_count,
            "progress_percent": min(progress, 100.0),
            "correlation_id": job.correlation_id,
            "operation_id": job.operation_id,
            "cancel_requested_at": job.cancel_requested_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "heartbeat_at": job.heartbeat_at,
            "created_at": job.created_at,
            "items": [cls.item_payload(item) for item in (items or [])],
        }
