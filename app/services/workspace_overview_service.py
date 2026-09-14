"""Read-model that aggregates one workspace's account state for the platform.

``GET /platform/workspaces/{id}/overview`` — the "AGREGADO 360" — powers the
Blackbox account drawer with a single call: identification, subscription with
resolved entitlements (effective value + whether it came from the plan or a
platform override), this month's metered usage, feature toggles with their
resolution reason, integration connections, WhatsApp numbers, active AI
agents and their configured autonomy, open ticket count, summarized finance
KPIs, and the last 10 audit entries. Every query is scoped by
``workspace_id`` — nothing here can leak another workspace's data.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.billing import Subscription
from app.models.entitlement import WorkspaceEntitlementOverride
from app.models.finance import IntegrationConnection
from app.models.identity import UserSession
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.support_ticket import SupportTicket, SupportTicketStatus
from app.models.task import Task, TaskReviewState, TaskStatus
from app.models.user import AgentType, User
from app.models.whatsapp import WhatsAppConnection
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.entitlement_service import (
    ENTITLEMENT_CATALOG,
    METERED_ENTITLEMENTS,
    QUANTITATIVE_ENTITLEMENTS,
    EntitlementError,
    EntitlementLimitSource,
    EntitlementResolver,
    MonthlyUsageService,
)
from app.services.finance_reports_service import FinanceReportsService
from app.services.playbook_service import PlaybookService

_CLOSED_TICKET_STATUSES = {
    SupportTicketStatus.resolved,
    SupportTicketStatus.closed,
    SupportTicketStatus.cancelled,
}
_OPEN_TICKET_STATUSES = tuple(
    status for status in SupportTicketStatus if status not in _CLOSED_TICKET_STATUSES
)


class WorkspaceOverviewWorkspaceNotFoundError(RuntimeError):
    pass


class WorkspaceOverviewService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_overview(
        self,
        workspace_id: UUID,
        *,
        include_private: bool = False,
    ) -> dict[str, Any]:
        workspace = await self.db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id, Workspace.deleted_at.is_(None)
            )
        )
        if workspace is None:
            raise WorkspaceOverviewWorkspaceNotFoundError(
                f"Workspace {workspace_id} não encontrado."
            )

        identification = {
            "id": str(workspace.id),
            "name": workspace.name,
            "slug": workspace.slug,
            "status": workspace.status.value,
            "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
        }

        subscription = await self.db.scalar(
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.workspace_id == workspace_id)
        )
        entitlements = await self._resolve_entitlements(workspace_id)
        overrides = await self._list_overrides(workspace_id)

        return {
            "workspace": identification,
            "subscription": self._subscription_summary(subscription),
            "entitlements": entitlements,
            "entitlement_overrides": overrides,
            "integrations": await self._integration_connections(workspace_id),
            "whatsapp_numbers": await self._whatsapp_numbers(workspace_id),
            "agents": await self._agents_and_autonomy(workspace_id),
            "members": await self._members(workspace_id),
            "support": {"open_tickets": await self._open_ticket_count(workspace_id)},
            # Somente contagens e carimbo de atualização. Título, descrição,
            # participantes, comentários, casos e clientes permanecem nas
            # rotas tenant e exigem uma sessão root_direct/support_approved.
            "tasks": await self._task_summary(workspace_id),
            "finance_kpis": await self._finance_kpis(workspace_id) if include_private else None,
            "recent_audit_events": (
                await self._recent_audit_events(workspace_id) if include_private else []
            ),
            "private_data_included": include_private,
        }

    @staticmethod
    def _subscription_summary(subscription: Subscription | None) -> dict[str, Any] | None:
        if subscription is None:
            return None
        plan = subscription.plan
        return {
            "status": subscription.status.value,
            "interval": subscription.interval.value,
            "amount_cents": subscription.amount_cents,
            "currency": subscription.currency,
            "current_period_end": (
                subscription.current_period_end.isoformat()
                if subscription.current_period_end
                else None
            ),
            "trial_end": subscription.trial_end.isoformat() if subscription.trial_end else None,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "plan": (
                {"id": str(plan.id), "name": plan.name, "tier": plan.tier.value}
                if plan is not None
                else None
            ),
        }

    async def _resolve_entitlements(self, workspace_id: UUID) -> list[dict[str, Any]]:
        resolver = EntitlementResolver(self.db)
        monthly_usage = MonthlyUsageService(self.db)
        rows: list[dict[str, Any]] = []
        for key in ENTITLEMENT_CATALOG:
            entry: dict[str, Any] = {
                "entitlement": key.value,
                "unit": ENTITLEMENT_CATALOG[key].unit.value,
                "quantitative": key in QUANTITATIVE_ENTITLEMENTS,
                "metered": key in METERED_ENTITLEMENTS,
            }
            try:
                resolved = await resolver.resolve(workspace_id, key)
            except EntitlementError as error:
                entry.update(
                    {
                        "enabled": False,
                        "limit_quantity": None,
                        "hard_limit": None,
                        "origin": "not_configured",
                        "error": str(error),
                    }
                )
                rows.append(entry)
                continue

            origin = (
                "override"
                if resolved.source == EntitlementLimitSource.workspace_override
                else "plan"
            )
            entry.update(
                {
                    "enabled": resolved.enabled,
                    "limit_quantity": resolved.limit_quantity,
                    "hard_limit": resolved.hard_limit,
                    "origin": origin,
                    "source": resolved.source.value,
                }
            )
            if key in METERED_ENTITLEMENTS:
                usage = await monthly_usage.get_usage(workspace_id=workspace_id, entitlement=key)
                entry["usage_this_month"] = {
                    "consumed_quantity": usage.consumed_quantity,
                    "reserved_quantity": usage.reserved_quantity,
                    "remaining_quantity": usage.remaining_quantity,
                }
            rows.append(entry)
        return rows

    async def _list_overrides(self, workspace_id: UUID) -> list[dict[str, Any]]:
        result = await self.db.scalars(
            select(WorkspaceEntitlementOverride)
            .where(WorkspaceEntitlementOverride.workspace_id == workspace_id)
            .order_by(WorkspaceEntitlementOverride.entitlement)
        )
        return [
            {
                "entitlement": row.entitlement.value,
                "mode": row.mode.value,
                "quantity": row.quantity,
                "reason": row.reason,
                "created_by_user_id": (
                    str(row.created_by_user_id) if row.created_by_user_id else None
                ),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in result.all()
        ]

    async def _integration_connections(self, workspace_id: UUID) -> list[dict[str, Any]]:
        result = await self.db.scalars(
            select(IntegrationConnection)
            .where(IntegrationConnection.workspace_id == workspace_id)
            .order_by(IntegrationConnection.provider)
        )
        return [
            {
                "provider": row.provider.value,
                "label": row.label,
                "status": row.status.value,
                "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
            }
            for row in result.all()
        ]

    async def _whatsapp_numbers(self, workspace_id: UUID) -> list[dict[str, Any]]:
        result = await self.db.scalars(
            select(WhatsAppConnection)
            .where(WhatsAppConnection.workspace_id == workspace_id)
            .order_by(WhatsAppConnection.created_at)
        )
        return [
            {
                "display_phone_number": row.display_phone_number or row.phone_number,
                "display_name": row.display_name,
                "status": row.status.value,
                "is_default": row.is_default,
            }
            for row in result.all()
        ]

    async def _agents_and_autonomy(self, workspace_id: UUID) -> dict[str, Any]:
        active_agents = int(
            await self.db.scalar(
                select(func.count(WorkspaceMembership.id))
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.status == MembershipStatus.active,
                    User.agent_type == AgentType.ai,
                    User.deleted_at.is_(None),
                )
            )
            or 0
        )
        summaries = await PlaybookService(self.db).list_summaries(workspace_id)
        published = [summary for summary in summaries if summary.state.value == "published"]
        autonomy_by_playbook = [
            {"name": summary.name, "autonomy_mode": summary.autonomy_mode}
            for summary in published
        ]
        return {
            "active_ai_agents": active_agents,
            "published_playbooks": len(published),
            "autonomy_by_playbook": autonomy_by_playbook,
        }

    async def _members(self, workspace_id: UUID) -> list[dict[str, Any]]:
        rows = (await self.db.execute(
            select(User, WorkspaceMembership)
            .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                User.agent_type == AgentType.human,
                User.deleted_at.is_(None),
            )
            .order_by(User.name.asc())
        )).all()
        members: list[dict[str, Any]] = []
        for user, membership in rows:
            active_sessions = int((await self.db.scalar(
                select(func.count(UserSession.id)).where(
                    UserSession.user_id == user.id,
                    UserSession.last_workspace_id == workspace_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > datetime.now(UTC),
                )
            )) or 0)
            members.append({
                "user_id": str(user.id),
                "name": user.name,
                "email": user.email,
                "role": membership.role.value,
                "status": membership.status.value,
                "access_package": membership.access_package,
                "active_session_count": active_sessions,
            })
        return members

    async def _open_ticket_count(self, workspace_id: UUID) -> int:
        return int(
            await self.db.scalar(
                select(func.count(SupportTicket.id)).where(
                    SupportTicket.workspace_id == workspace_id,
                    SupportTicket.status.in_(_OPEN_TICKET_STATUSES),
                )
            )
            or 0
        )

    async def _task_summary(self, workspace_id: UUID) -> dict[str, Any]:
        now = datetime.now(UTC)
        filters = (
            Matter.workspace_id == workspace_id,
            Matter.deleted_at.is_(None),
        )

        aggregate = (
            await self.db.execute(
                select(
                    func.count(Task.id).label("total"),
                    func.count(Task.id)
                    .filter(Task.cancelled_at.is_(None), Task.status != TaskStatus.done)
                    .label("open_count"),
                    func.count(Task.id)
                    .filter(
                        Task.cancelled_at.is_(None),
                        Task.status != TaskStatus.done,
                        Task.due_at.is_not(None),
                        Task.due_at < now,
                    )
                    .label("overdue"),
                    func.count(Task.id)
                    .filter(
                        Task.cancelled_at.is_(None),
                        Task.status == TaskStatus.blocked,
                    )
                    .label("blocked"),
                    func.count(Task.id)
                    .filter(
                        Task.cancelled_at.is_(None),
                        Task.review_state == TaskReviewState.pending,
                    )
                    .label("pending_review"),
                    func.count(Task.id)
                    .filter(
                        Task.cancelled_at.is_(None),
                        Task.status == TaskStatus.done,
                    )
                    .label("completed"),
                    func.count(Task.id)
                    .filter(Task.cancelled_at.is_not(None))
                    .label("cancelled"),
                    func.max(Task.updated_at).label("last_updated"),
                )
                .join(Matter, Matter.id == Task.matter_id)
                .where(*filters)
            )
        ).one()
        last_updated = aggregate.last_updated
        return {
            "total": int(aggregate.total or 0),
            "open": int(aggregate.open_count or 0),
            "overdue": int(aggregate.overdue or 0),
            "blocked": int(aggregate.blocked or 0),
            "pending_review": int(aggregate.pending_review or 0),
            "completed": int(aggregate.completed or 0),
            "cancelled": int(aggregate.cancelled or 0),
            "last_updated_at": last_updated.isoformat() if last_updated else None,
            "content_included": False,
        }

    async def _finance_kpis(self, workspace_id: UUID) -> dict[str, Any] | None:
        today = date.today()
        start_date = today - timedelta(days=30)
        try:
            kpis = await FinanceReportsService(self.db).kpis(
                workspace_id, start_date=start_date, end_date=today
            )
        except Exception:  # noqa: BLE001 — the overview degrades, it never breaks.
            return None
        return {
            "period_start": kpis.period_start.isoformat(),
            "period_end": kpis.period_end.isoformat(),
            "recebido": str(kpis.recebido),
            "a_receber": str(kpis.a_receber),
            "despesas": str(kpis.despesas),
            "resultado": str(kpis.resultado),
            "mrr": str(kpis.mrr),
            "inadimplencia_pct": str(kpis.inadimplencia_pct),
        }

    async def _recent_audit_events(self, workspace_id: UUID) -> list[dict[str, Any]]:
        result = await self.db.scalars(
            select(KlmAuditEvent)
            .where(KlmAuditEvent.workspace_id == workspace_id)
            .order_by(KlmAuditEvent.occurred_at.desc())
            .limit(10)
        )
        return [
            {
                "event_type": row.event_type,
                "entity_type": row.entity_type,
                "actor_label": row.actor_label,
                "summary": row.summary,
                "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
            }
            for row in result.all()
        ]
