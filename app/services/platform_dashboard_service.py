"""Agregador operacional, autorizado e tolerante a falhas da home Blackbox.

O servico consulta apenas metadados operacionais. Corpos de mensagens, descricoes
de tickets, documentos e outros conteudos privados nunca entram na resposta.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_rbac import (
    PLATFORM_ROLE_PACKAGES,
    effective_platform_permissions,
    has_platform_permission,
    is_platform_root,
)
from app.models.blog_post import BlogPost, BlogPostStatus
from app.models.error_log import ErrorLog, ErrorSeverity, ErrorStatus
from app.models.growth_crm import OPEN_STAGES, GrowthFunnelStage, GrowthProspect
from app.models.platform_inbox import (
    PlatformInboxConversation,
    PlatformInboxMessage,
    PlatformInboxQueue,
    PlatformInboxStatus,
)
from app.models.platform_incident import PlatformIncident
from app.models.platform_organization import (
    PlatformDashboardPreference,
    PlatformOperationalAssignment,
    PlatformTeam,
    PlatformTeamMembership,
    PlatformWorkspaceAssignment,
)
from app.models.platform_work_item import PlatformWorkItem
from app.models.subscription_commercial import (
    WorkspaceSubscription,
    WorkspaceSubscriptionStatus,
)
from app.models.super_admin_notice import SuperAdminNotice, SuperAdminNoticeStatus
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.schemas.platform_dashboard import (
    DashboardScope,
    PlatformDashboardAction,
    PlatformDashboardAlert,
    PlatformDashboardHome,
    PlatformDashboardLayout,
    PlatformDashboardMetric,
    PlatformDashboardSourceHealth,
    PlatformDashboardWorkItem,
)
from app.services.platform_work_item_service import blackbox_source_href

OPEN_TICKET_STATUSES = (
    SupportTicketStatus.new,
    SupportTicketStatus.triage,
    SupportTicketStatus.in_progress,
    SupportTicketStatus.waiting_customer,
    SupportTicketStatus.waiting_internal,
    SupportTicketStatus.validating,
)


@dataclass(frozen=True)
class _MetricSpec:
    key: str
    label: str
    unit: str
    source: str
    href: str
    description: str


_SPECS: dict[str, _MetricSpec] = {
    "accounts_active": _MetricSpec(
        "accounts_active",
        "Contas ativas",
        "count",
        "workspaces",
        "/blackbox/workspaces",
        "Contas ativas e nao arquivadas.",
    ),
    "onboarding_pending": _MetricSpec(
        "onboarding_pending",
        "Onboardings pendentes",
        "count",
        "workspaces",
        "/blackbox/workspaces",
        "Contas ainda sem conclusao de onboarding.",
    ),
    "accounts_suspended": _MetricSpec(
        "accounts_suspended",
        "Contas suspensas",
        "count",
        "workspaces",
        "/blackbox/workspaces",
        "Contas com operacao suspensa.",
    ),
    "staff_active": _MetricSpec(
        "staff_active",
        "Equipe ativa",
        "count",
        "staff",
        "/blackbox/staff",
        "Funcionarios internos com acesso ativo.",
    ),
    "prospects_open": _MetricSpec(
        "prospects_open",
        "Oportunidades abertas",
        "count",
        "growth_crm",
        "/blackbox/growth-crm",
        "Prospects em etapas nao terminais.",
    ),
    "prospects_unassigned": _MetricSpec(
        "prospects_unassigned",
        "Leads sem responsavel",
        "count",
        "growth_crm",
        "/blackbox/growth-crm",
        "Prospects abertos sem owner.",
    ),
    "prospects_proposal": _MetricSpec(
        "prospects_proposal",
        "Propostas em aberto",
        "count",
        "growth_crm",
        "/blackbox/growth-crm",
        "Prospects atualmente em proposta.",
    ),
    "prospects_won": _MetricSpec(
        "prospects_won",
        "Conversoes no periodo",
        "count",
        "growth_crm",
        "/blackbox/growth-crm",
        "Prospects convertidos dentro do periodo.",
    ),
    "pipeline_value": _MetricSpec(
        "pipeline_value",
        "Pipeline estimado",
        "currency_brl",
        "growth_crm",
        "/blackbox/growth-crm",
        "Soma dos valores estimados em etapas abertas.",
    ),
    "tickets_open": _MetricSpec(
        "tickets_open",
        "Tickets abertos",
        "count",
        "support",
        "/blackbox/support-tickets",
        "Tickets ainda nao encerrados.",
    ),
    "tickets_critical": _MetricSpec(
        "tickets_critical",
        "Tickets criticos",
        "count",
        "support",
        "/blackbox/support-tickets",
        "Tickets abertos de severidade critica.",
    ),
    "tickets_overdue": _MetricSpec(
        "tickets_overdue",
        "SLA vencido",
        "count",
        "support",
        "/blackbox/support-tickets",
        "Tickets abertos com algum prazo de SLA vencido.",
    ),
    "tickets_unassigned": _MetricSpec(
        "tickets_unassigned",
        "Tickets sem responsavel",
        "count",
        "support",
        "/blackbox/support-tickets",
        "Tickets abertos ainda nao atribuidos.",
    ),
    "tickets_suggestions": _MetricSpec(
        "tickets_suggestions",
        "Sugestoes abertas",
        "count",
        "support",
        "/blackbox/support-tickets",
        "Sugestoes de produto ainda abertas.",
    ),
    "inbox_open": _MetricSpec(
        "inbox_open",
        "Conversas aguardando",
        "count",
        "platform_inbox",
        "/blackbox/inbox",
        "Conversas oficiais abertas ou pendentes.",
    ),
    "errors_open": _MetricSpec(
        "errors_open",
        "Erros abertos",
        "count",
        "errors",
        "/blackbox/errors",
        "Erros ainda nao resolvidos.",
    ),
    "errors_critical": _MetricSpec(
        "errors_critical",
        "Erros criticos",
        "count",
        "errors",
        "/blackbox/errors",
        "Erros abertos de severidade critica.",
    ),
    "incidents_open": _MetricSpec(
        "incidents_open",
        "Incidentes ativos",
        "count",
        "incidents",
        "/blackbox/infrastructure",
        "Incidentes operacionais ainda nao resolvidos.",
    ),
    "mrr": _MetricSpec(
        "mrr",
        "MRR",
        "currency_brl",
        "billing",
        "/blackbox/billing",
        "Receita mensal recorrente comercial local.",
    ),
    "subscriptions_active": _MetricSpec(
        "subscriptions_active",
        "Assinaturas ativas",
        "count",
        "billing",
        "/blackbox/billing",
        "Assinaturas comerciais ativas.",
    ),
    "subscriptions_overdue": _MetricSpec(
        "subscriptions_overdue",
        "Inadimplentes",
        "count",
        "billing",
        "/blackbox/billing",
        "Assinaturas em estado inadimplente.",
    ),
    "trials_active": _MetricSpec(
        "trials_active",
        "Trials ativos",
        "count",
        "billing",
        "/blackbox/billing",
        "Assinaturas em trial.",
    ),
    "trials_expiring": _MetricSpec(
        "trials_expiring",
        "Trials vencendo",
        "count",
        "billing",
        "/blackbox/billing",
        "Trials com vencimento nos proximos sete dias.",
    ),
    "content_drafts": _MetricSpec(
        "content_drafts",
        "Conteudos em rascunho",
        "count",
        "content",
        "/blackbox/site",
        "Posts editoriais ainda em rascunho.",
    ),
    "content_scheduled": _MetricSpec(
        "content_scheduled",
        "Conteudos agendados",
        "count",
        "content",
        "/blackbox/site",
        "Posts editoriais agendados.",
    ),
    "content_published": _MetricSpec(
        "content_published",
        "Publicados no periodo",
        "count",
        "content",
        "/blackbox/site",
        "Posts publicados dentro do periodo.",
    ),
    "notices_draft": _MetricSpec(
        "notices_draft",
        "Comunicados em rascunho",
        "count",
        "announcements",
        "/blackbox/announcements",
        "Comunicados ainda em preparacao.",
    ),
    "notices_scheduled": _MetricSpec(
        "notices_scheduled",
        "Comunicados agendados",
        "count",
        "announcements",
        "/blackbox/announcements",
        "Comunicados aguardando publicacao.",
    ),
    "notices_published": _MetricSpec(
        "notices_published",
        "Comunicados publicados",
        "count",
        "announcements",
        "/blackbox/announcements",
        "Comunicados publicados dentro do periodo.",
    ),
    "inbox_failed": _MetricSpec(
        "inbox_failed",
        "Entregas com falha",
        "count",
        "platform_inbox",
        "/blackbox/inbox",
        "Mensagens oficiais com falha de entrega.",
    ),
}


ROLE_METRICS: dict[str, tuple[str, ...]] = {
    "platform_owner": (
        "mrr",
        "accounts_active",
        "prospects_open",
        "tickets_open",
        "incidents_open",
        "errors_critical",
    ),
    "platform_admin": (
        "accounts_active",
        "onboarding_pending",
        "tickets_open",
        "trials_active",
        "staff_active",
        "incidents_open",
    ),
    "sales_sdr": ("prospects_open", "prospects_unassigned", "prospects_proposal", "prospects_won"),
    "commercial_manager": (
        "prospects_open",
        "pipeline_value",
        "prospects_proposal",
        "prospects_won",
        "prospects_unassigned",
    ),
    "onboarding": ("onboarding_pending", "accounts_active", "trials_active", "tickets_open"),
    "customer_success": (
        "accounts_active",
        "accounts_suspended",
        "onboarding_pending",
        "tickets_open",
    ),
    "support": (
        "tickets_open",
        "tickets_critical",
        "tickets_overdue",
        "tickets_unassigned",
        "inbox_open",
    ),
    "technical_support": (
        "tickets_open",
        "tickets_critical",
        "errors_open",
        "incidents_open",
        "tickets_unassigned",
    ),
    "finance_billing": (
        "mrr",
        "subscriptions_active",
        "subscriptions_overdue",
        "trials_active",
        "trials_expiring",
    ),
    "marketing_content": (
        "content_drafts",
        "content_scheduled",
        "content_published",
        "prospects_open",
    ),
    "community_manager": ("notices_draft", "notices_scheduled", "notices_published", "inbox_open"),
    "compliance_security": (
        "incidents_open",
        "errors_critical",
        "tickets_critical",
        "staff_active",
    ),
    "product_operations": ("tickets_suggestions", "errors_open", "incidents_open", "tickets_open"),
    "engineering": (
        "errors_open",
        "errors_critical",
        "incidents_open",
        "tickets_critical",
        "inbox_failed",
    ),
}


ROLE_ACTIONS: dict[str, tuple[tuple[str, str, str, str, str], ...]] = {
    "platform_owner": (
        ("accounts", "Abrir contas", "/blackbox/workspaces", "customers", "view"),
        (
            "incident",
            "Registrar incidente",
            "/blackbox/infrastructure",
            "security",
            "manage",
        ),
        ("staff", "Administrar equipe", "/blackbox/staff", "staff", "manage"),
    ),
    "platform_admin": (
        ("accounts", "Operar contas", "/blackbox/workspaces", "customers", "manage"),
        ("staff", "Ver equipe", "/blackbox/staff", "staff", "view"),
    ),
    "sales_sdr": (("lead", "Criar lead", "/blackbox/growth-crm", "sales", "manage"),),
    "commercial_manager": (
        ("pipeline", "Distribuir pipeline", "/blackbox/growth-crm", "sales", "manage"),
    ),
    "onboarding": (
        ("onboarding", "Abrir implantacoes", "/blackbox/workspaces", "onboarding", "manage"),
    ),
    "customer_success": (
        ("portfolio", "Revisar carteira", "/blackbox/workspaces", "success", "manage"),
    ),
    "support": (
        ("tickets", "Atender tickets", "/blackbox/support-tickets", "support", "respond"),
        ("access", "Solicitar acesso", "/blackbox/support-access", "support_access", "request"),
    ),
    "technical_support": (
        ("tickets", "Triar fila tecnica", "/blackbox/support-tickets", "support", "triage"),
        ("errors", "Investigar erros", "/blackbox/errors", "errors", "view"),
    ),
    "finance_billing": (
        ("billing", "Operar assinaturas", "/blackbox/billing", "billing", "manage"),
    ),
    "marketing_content": (("content", "Criar conteudo", "/blackbox/site", "content", "manage"),),
    "community_manager": (
        ("notice", "Criar comunicado", "/blackbox/announcements", "announcements", "manage"),
    ),
    "compliance_security": (
        (
            "security",
            "Investigar alertas",
            "/blackbox/governance/security",
            "security",
            "view",
        ),
    ),
    "product_operations": (
        (
            "flags",
            "Revisar feature flags",
            "/blackbox/governance/feature_flags",
            "feature_flags",
            "view",
        ),
    ),
    "engineering": (
        (
            "incident",
            "Assumir incidente",
            "/blackbox/infrastructure",
            "security",
            "view",
        ),
        ("errors", "Investigar erros", "/blackbox/errors", "errors", "manage"),
    ),
}


class PlatformDashboardService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._source_state: dict[str, tuple[str, str | None]] = {}

    async def home(
        self,
        *,
        user: User,
        scope: DashboardScope,
        period_days: int,
    ) -> PlatformDashboardHome:
        now = datetime.now(UTC)
        role = str(
            user.platform_role_package or ("platform_admin" if user.is_platform_admin else "")
        )
        is_root = is_platform_root(user)
        if is_root:
            role = "platform_owner"
        role_package = PLATFORM_ROLE_PACKAGES.get(role)
        role_label = role_package.label if role_package else "Equipe Meu Executivo"
        available_scopes: list[DashboardScope] = ["mine"]
        if role in {"platform_owner", "platform_admin"}:
            available_scopes.append("platform")
        managed_team = await self.db.scalar(
            select(PlatformTeam)
            .where(
                PlatformTeam.manager_user_id == user.id,
                PlatformTeam.active.is_(True),
            )
            .order_by(PlatformTeam.name, PlatformTeam.id)
            .limit(1)
        )
        if managed_team and has_platform_permission(user, "dashboard", "team_view"):
            available_scopes.append("team")
        team_user_ids: list[Any] = []
        if managed_team:
            team_user_ids = list(
                (
                    await self.db.scalars(
                        select(PlatformTeamMembership.user_id).where(
                            PlatformTeamMembership.team_id == managed_team.id,
                            PlatformTeamMembership.active.is_(True),
                            or_(
                                PlatformTeamMembership.ends_at.is_(None),
                                PlatformTeamMembership.ends_at > now,
                            ),
                        )
                    )
                ).all()
            )

        metric_keys = ROLE_METRICS.get(role, ("tickets_open", "accounts_active", "errors_open"))
        metrics: list[PlatformDashboardMetric] = []
        if scope == "team" and "team" not in available_scopes:
            self._source_state["team_assignments"] = (
                "unavailable",
                "Equipes e responsabilidades ainda nao foram publicadas para este usuario.",
            )
            metrics = [
                self._unavailable_metric(key, now, "Escopo de equipe nao configurado.")
                for key in metric_keys
            ]
        elif scope == "platform" and "platform" not in available_scopes:
            self._source_state["scope"] = (
                "unavailable",
                "O perfil nao possui visao integral da plataforma.",
            )
            metrics = [
                self._unavailable_metric(key, now, "Escopo de plataforma nao autorizado.")
                for key in metric_keys
            ]
        else:
            for key in metric_keys:
                metrics.append(
                    await self._metric(
                        key,
                        user=user,
                        scope=scope,
                        period_days=period_days,
                        now=now,
                        is_root=is_root,
                        team_id=managed_team.id if scope == "team" and managed_team else None,
                        team_user_ids=team_user_ids if scope == "team" else [],
                    )
                )

        work_items = await self._work_items(
            user=user,
            scope=scope,
            team_id=managed_team.id if managed_team else None,
            now=now,
        )
        alerts = self._alerts(metrics, scope=scope)
        actions = [
            PlatformDashboardAction(key=key, label=label, href=href, module=module, action=action)
            for key, label, href, module, action in ROLE_ACTIONS.get(role, ())
            if is_root or has_platform_permission(user, module, action)
        ]
        source_health = [
            PlatformDashboardSourceHealth(
                key=key,
                label=key.replace("_", " ").title(),
                status=status,  # type: ignore[arg-type]
                updated_at=now,
                detail=detail,
            )
            for key, (status, detail) in sorted(self._source_state.items())
        ]
        template_key = f"blackbox-home:{role}:v1"
        mandatory_widgets = ["alerts", "priority_queue", "metrics"]
        optional_widgets = ["quick_actions", "source_health", "authorized_modules"]
        preference = await self.db.scalar(
            select(PlatformDashboardPreference).where(
                PlatformDashboardPreference.user_id == user.id
            )
        )
        delta = (
            dict(preference.preference_delta or {})
            if preference and preference.template_key == template_key
            else {}
        )
        hidden_widgets = [key for key in delta.get("hiddenWidgets", []) if key in optional_widgets]
        requested_order = [
            key
            for key in delta.get("widgetOrder", [])
            if key in mandatory_widgets or key in optional_widgets
        ]
        widget_order = requested_order + [
            key for key in mandatory_widgets + optional_widgets if key not in requested_order
        ]
        return PlatformDashboardHome(
            role=role,
            role_label=role_label,
            is_platform_root=is_root,
            scope=scope,
            available_scopes=available_scopes,
            team_id=managed_team.id if scope == "team" and managed_team else None,
            team_name=managed_team.name if scope == "team" and managed_team else None,
            period_days=period_days,
            generated_at=now,
            layout=PlatformDashboardLayout(
                template_key=template_key,
                template_version=1,
                mandatory_widgets=mandatory_widgets,
                optional_widgets=optional_widgets,
                hidden_widgets=hidden_widgets,
                widget_order=widget_order,
                preference_etag=preference.etag if preference else None,
            ),
            metrics=metrics,
            work_items=work_items,
            alerts=alerts,
            actions=actions,
            source_health=source_health,
        )

    async def _safe_value(
        self, source: str, operation: Callable[[], Awaitable[int | float]]
    ) -> tuple[int | float | None, bool]:
        try:
            async with self.db.begin_nested():
                value = await operation()
            self._source_state.setdefault(source, ("available", None))
            return value, True
        except Exception:
            self._source_state[source] = ("unavailable", "Fonte indisponivel nesta atualizacao.")
            return None, False

    async def _count(self, statement: Any) -> int:
        return int((await self.db.scalar(statement)) or 0)

    def _unavailable_metric(
        self, key: str, now: datetime, description: str
    ) -> PlatformDashboardMetric:
        spec = _SPECS[key]
        return PlatformDashboardMetric(
            key=key,
            label=spec.label,
            value=None,
            unit=spec.unit,
            quality="unavailable",
            source=spec.source,
            updated_at=now,
            href=spec.href,
            description=description,
        )

    async def _metric(
        self,
        key: str,
        *,
        user: User,
        scope: DashboardScope,
        period_days: int,
        now: datetime,
        is_root: bool,
        team_id: Any | None,
        team_user_ids: list[Any],
    ) -> PlatformDashboardMetric:
        spec = _SPECS[key]
        cutoff = now - timedelta(days=period_days)
        owned = (
            scope == "mine"
            and not is_root
            and str(user.platform_role_package or "") not in {"platform_admin"}
        )
        team_scoped = scope == "team" and team_id is not None
        workspace_team_ids = select(PlatformWorkspaceAssignment.workspace_id).where(
            or_(
                PlatformWorkspaceAssignment.assigned_team_id == team_id,
                PlatformWorkspaceAssignment.assigned_user_id.in_(team_user_ids),
            ),
            or_(
                PlatformWorkspaceAssignment.ends_at.is_(None),
                PlatformWorkspaceAssignment.ends_at > now,
            ),
        )

        async def value() -> int | float:
            if key == "accounts_active":
                filters = [
                    Workspace.status == WorkspaceStatus.active,
                    Workspace.deleted_at.is_(None),
                ]
                if team_scoped:
                    filters.append(Workspace.id.in_(workspace_team_ids))
                return await self._count(select(func.count(Workspace.id)).where(*filters))
            if key == "onboarding_pending":
                filters = [
                    Workspace.onboarding_completed.is_(False),
                    Workspace.deleted_at.is_(None),
                ]
                if team_scoped:
                    filters.append(Workspace.id.in_(workspace_team_ids))
                return await self._count(select(func.count(Workspace.id)).where(*filters))
            if key == "accounts_suspended":
                filters = [
                    Workspace.status == WorkspaceStatus.suspended,
                    Workspace.deleted_at.is_(None),
                ]
                if team_scoped:
                    filters.append(Workspace.id.in_(workspace_team_ids))
                return await self._count(select(func.count(Workspace.id)).where(*filters))
            if key == "staff_active":
                filters = [
                    User.is_internal_staff.is_(True),
                    User.platform_access_active.is_(True),
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                ]
                if team_scoped:
                    filters.append(User.id.in_(team_user_ids))
                return await self._count(select(func.count(User.id)).where(*filters))
            if key.startswith("prospects_") or key == "pipeline_value":
                filters: list[Any] = []
                if owned:
                    filters.append(GrowthProspect.owner_user_id == user.id)
                elif team_scoped:
                    filters.append(GrowthProspect.owner_user_id.in_(team_user_ids))
                if key == "prospects_open":
                    filters.append(GrowthProspect.estagio.in_(OPEN_STAGES))
                    return await self._count(select(func.count(GrowthProspect.id)).where(*filters))
                if key == "prospects_unassigned":
                    filters.extend(
                        (
                            GrowthProspect.estagio.in_(OPEN_STAGES),
                            GrowthProspect.owner_user_id.is_(None),
                        )
                    )
                    return await self._count(select(func.count(GrowthProspect.id)).where(*filters))
                if key == "prospects_proposal":
                    filters.append(GrowthProspect.estagio == GrowthFunnelStage.proposta)
                    return await self._count(select(func.count(GrowthProspect.id)).where(*filters))
                if key == "prospects_won":
                    filters.extend(
                        (
                            GrowthProspect.estagio == GrowthFunnelStage.pagante,
                            GrowthProspect.won_at >= cutoff,
                        )
                    )
                    return await self._count(select(func.count(GrowthProspect.id)).where(*filters))
                filters.append(GrowthProspect.estagio.in_(OPEN_STAGES))
                cents = int(
                    (
                        await self.db.scalar(
                            select(
                                func.coalesce(func.sum(GrowthProspect.valor_estimado_cents), 0)
                            ).where(*filters)
                        )
                    )
                    or 0
                )
                return round(cents / 100, 2)
            if key.startswith("tickets_"):
                filters = [SupportTicket.status.in_(OPEN_TICKET_STATUSES)]
                role = str(user.platform_role_package or "")
                if role == "support":
                    filters.append(SupportTicket.queue == SupportTicketQueue.support)
                elif role in {"technical_support", "engineering"}:
                    filters.append(SupportTicket.queue == SupportTicketQueue.technical)
                if owned:
                    filters.append(SupportTicket.assigned_to_user_id == user.id)
                elif team_scoped:
                    filters.append(SupportTicket.assigned_to_user_id.in_(team_user_ids))
                if key == "tickets_critical":
                    filters.append(SupportTicket.severity == SupportTicketSeverity.critical)
                elif key == "tickets_overdue":
                    filters.append(
                        (SupportTicket.first_response_due_at < now)
                        | (SupportTicket.resolution_due_at < now)
                    )
                elif key == "tickets_unassigned":
                    filters.append(SupportTicket.assigned_to_user_id.is_(None))
                elif key == "tickets_suggestions":
                    filters.append(SupportTicket.ticket_type == SupportTicketType.suggestion)
                return await self._count(select(func.count(SupportTicket.id)).where(*filters))
            if key == "inbox_open":
                filters = [
                    PlatformInboxConversation.status.in_(
                        (PlatformInboxStatus.open, PlatformInboxStatus.pending)
                    )
                ]
                role = str(user.platform_role_package or "")
                if role in {"sales_sdr", "commercial_manager", "marketing_content"}:
                    filters.append(PlatformInboxConversation.queue == PlatformInboxQueue.sales)
                elif role in {"support", "technical_support"}:
                    filters.append(PlatformInboxConversation.queue == PlatformInboxQueue.support)
                if owned:
                    filters.append(PlatformInboxConversation.assigned_to_user_id == user.id)
                elif team_scoped:
                    filters.append(PlatformInboxConversation.assigned_to_user_id.in_(team_user_ids))
                return await self._count(
                    select(func.count(PlatformInboxConversation.id)).where(*filters)
                )
            if key == "inbox_failed":
                if team_scoped:
                    raise RuntimeError("Entregas não possuem responsável de equipe.")
                return await self._count(
                    select(func.count(PlatformInboxMessage.id)).where(
                        PlatformInboxMessage.delivery_status == "failed"
                    )
                )
            if key == "errors_open":
                if team_scoped:
                    raise RuntimeError("Erros ainda não possuem responsável de equipe.")
                return await self._count(
                    select(func.count(ErrorLog.id)).where(ErrorLog.status != ErrorStatus.resolved)
                )
            if key == "errors_critical":
                if team_scoped:
                    raise RuntimeError("Erros ainda não possuem responsável de equipe.")
                return await self._count(
                    select(func.count(ErrorLog.id)).where(
                        ErrorLog.status != ErrorStatus.resolved,
                        ErrorLog.severity == ErrorSeverity.critical,
                    )
                )
            if key == "incidents_open":
                filters = [PlatformIncident.status != "resolved"]
                if owned:
                    filters.append(PlatformIncident.owner_user_id == user.id)
                elif team_scoped:
                    filters.append(PlatformIncident.owner_user_id.in_(team_user_ids))
                return await self._count(select(func.count(PlatformIncident.id)).where(*filters))
            if key == "mrr":
                statement = select(WorkspaceSubscription)
                if team_scoped:
                    statement = statement.where(
                        WorkspaceSubscription.workspace_id.in_(workspace_team_ids)
                    )
                subscriptions = (await self.db.scalars(statement)).all()
                return round(sum(item.mrr_cents for item in subscriptions) / 100, 2)
            if key == "subscriptions_active":
                filters = [WorkspaceSubscription.status == WorkspaceSubscriptionStatus.ativa]
                if team_scoped:
                    filters.append(WorkspaceSubscription.workspace_id.in_(workspace_team_ids))
                return await self._count(
                    select(func.count(WorkspaceSubscription.id)).where(*filters)
                )
            if key == "subscriptions_overdue":
                filters = [WorkspaceSubscription.status == WorkspaceSubscriptionStatus.inadimplente]
                if team_scoped:
                    assigned_billing_ids = select(PlatformOperationalAssignment.source_id).where(
                        PlatformOperationalAssignment.source_type == "billing",
                        or_(
                            PlatformOperationalAssignment.team_id == team_id,
                            PlatformOperationalAssignment.owner_user_id.in_(team_user_ids),
                        ),
                    )
                    filters.append(WorkspaceSubscription.id.in_(assigned_billing_ids))
                return await self._count(
                    select(func.count(WorkspaceSubscription.id)).where(*filters)
                )
            if key == "trials_active":
                filters = [WorkspaceSubscription.status == WorkspaceSubscriptionStatus.trial]
                if team_scoped:
                    filters.append(WorkspaceSubscription.workspace_id.in_(workspace_team_ids))
                return await self._count(
                    select(func.count(WorkspaceSubscription.id)).where(*filters)
                )
            if key == "trials_expiring":
                filters = [
                    WorkspaceSubscription.status == WorkspaceSubscriptionStatus.trial,
                    WorkspaceSubscription.trial_ends_at >= now,
                    WorkspaceSubscription.trial_ends_at <= now + timedelta(days=7),
                ]
                if team_scoped:
                    filters.append(WorkspaceSubscription.workspace_id.in_(workspace_team_ids))
                return await self._count(
                    select(func.count(WorkspaceSubscription.id)).where(*filters)
                )
            if key.startswith("content_"):
                if key == "content_drafts":
                    clause = BlogPost.status == BlogPostStatus.draft
                elif key == "content_scheduled":
                    clause = BlogPost.status == BlogPostStatus.scheduled
                else:
                    clause = (BlogPost.status == BlogPostStatus.published) & (
                        BlogPost.published_at >= cutoff
                    )
                filters = [clause]
                if team_scoped:
                    filters.append(BlogPost.created_by_user_id.in_(team_user_ids))
                return await self._count(select(func.count(BlogPost.id)).where(*filters))
            if key.startswith("notices_"):
                if key == "notices_draft":
                    clause = SuperAdminNotice.status == SuperAdminNoticeStatus.draft
                elif key == "notices_scheduled":
                    clause = SuperAdminNotice.status == SuperAdminNoticeStatus.scheduled
                else:
                    clause = (SuperAdminNotice.status == SuperAdminNoticeStatus.published) & (
                        SuperAdminNotice.published_at >= cutoff
                    )
                filters = [clause]
                if team_scoped:
                    filters.append(SuperAdminNotice.created_by_user_id.in_(team_user_ids))
                return await self._count(select(func.count(SuperAdminNotice.id)).where(*filters))
            raise KeyError(key)

        metric_value, ok = await self._safe_value(spec.source, value)
        # Custos, margens e tarifas nao fazem parte deste contrato. MRR e receita
        # comercial sao permitidos somente aos papeis que ja possuem finance:view.
        if key == "mrr" and not (is_root or has_platform_permission(user, "finance", "view")):
            metric_value, ok = None, False
        return PlatformDashboardMetric(
            key=key,
            label=spec.label,
            value=metric_value,
            unit=spec.unit,
            quality="updated" if ok else "unavailable",
            source=spec.source,
            updated_at=now,
            href=spec.href,
            description=spec.description
            if ok
            else "Fonte indisponivel; nenhum zero substitutivo foi usado.",
        )

    async def _work_items(
        self,
        *,
        user: User,
        scope: DashboardScope,
        team_id: Any | None,
        now: datetime,
    ) -> list[PlatformDashboardWorkItem]:
        if not has_platform_permission(user, "platform_tasks", "view"):
            self._source_state["priority_queue"] = (
                "available",
                "O perfil não possui acesso à Central de Tarefas.",
            )
            return []
        permissions = effective_platform_permissions(user)
        if not permissions:
            self._source_state["priority_queue"] = (
                "available",
                "O perfil não possui itens autorizados nesta fila.",
            )
            return []
        permission_clause = or_(
            *(
                and_(
                    PlatformWorkItem.required_module == permission.module,
                    PlatformWorkItem.required_action == permission.action,
                )
                for permission in permissions
            )
        )
        clauses: list[Any] = [
            permission_clause,
            PlatformWorkItem.state.in_(
                {
                    "open",
                    "in_progress",
                    "waiting_external",
                    "waiting_customer",
                    "blocked",
                    "stale",
                }
            ),
            or_(
                PlatformWorkItem.snoozed_until.is_(None),
                PlatformWorkItem.snoozed_until <= now,
            ),
        ]
        if scope == "mine":
            clauses.append(PlatformWorkItem.assigned_user_id == user.id)
        elif scope == "team":
            if team_id is None:
                self._source_state["priority_queue"] = (
                    "unavailable",
                    "Equipe gerenciada não configurada para este usuário.",
                )
                return []
            member_ids = select(PlatformTeamMembership.user_id).where(
                PlatformTeamMembership.team_id == team_id,
                PlatformTeamMembership.active.is_(True),
                or_(
                    PlatformTeamMembership.ends_at.is_(None),
                    PlatformTeamMembership.ends_at > now,
                ),
            )
            clauses.append(
                or_(
                    PlatformWorkItem.assigned_team_id == team_id,
                    PlatformWorkItem.assigned_user_id.in_(member_ids),
                )
            )
        try:
            async with self.db.begin_nested():
                rows = list(
                    (
                        await self.db.scalars(
                            select(PlatformWorkItem)
                            .where(*clauses)
                            .order_by(
                                case(
                                    (PlatformWorkItem.priority == "P0", 0),
                                    (PlatformWorkItem.priority == "P1", 1),
                                    (PlatformWorkItem.priority == "P2", 2),
                                    else_=3,
                                ),
                                PlatformWorkItem.due_at.asc().nulls_last(),
                                PlatformWorkItem.source_updated_at.desc(),
                                PlatformWorkItem.id,
                            )
                            .limit(5)
                        )
                    ).all()
                )
            self._source_state.setdefault("priority_queue", ("available", None))
        except Exception:
            self._source_state["priority_queue"] = (
                "unavailable",
                "Fila prioritária indisponível nesta atualização.",
            )
            return []
        return [
            PlatformDashboardWorkItem(
                id=str(row.id),
                source_type=row.source_type,
                priority=row.priority,
                safe_title=row.safe_title,
                safe_summary=row.safe_summary,
                # The projection may predate the canonical Blackbox route map.
                # Rebuild the link on read so the dashboard never leaks a
                # removed or external persisted href.
                href=blackbox_source_href(
                    row.source_type,
                    row.source_id,
                    workspace_id=row.workspace_id,
                    extra_context=(
                        {"namespace": (row.payload_safe or {}).get("namespace")}
                        if row.source_type == "configuration"
                        else None
                    ),
                    actor=user,
                ),
                due_at=row.due_at,
            )
            for row in rows
        ]

    async def _legacy_work_items(
        self, *, user: User, role: str, scope: DashboardScope, now: datetime
    ) -> list[PlatformDashboardWorkItem]:
        if scope == "team":
            return []
        owned = scope == "mine" and role not in {"platform_owner", "platform_admin"}
        items: list[PlatformDashboardWorkItem] = []
        try:
            async with self.db.begin_nested():
                if role in {
                    "support",
                    "technical_support",
                    "engineering",
                    "product_operations",
                    "platform_owner",
                    "platform_admin",
                }:
                    filters: list[Any] = [SupportTicket.status.in_(OPEN_TICKET_STATUSES)]
                    if role == "support":
                        filters.append(SupportTicket.queue == SupportTicketQueue.support)
                    elif role in {"technical_support", "engineering"}:
                        filters.append(SupportTicket.queue == SupportTicketQueue.technical)
                    if owned:
                        filters.append(SupportTicket.assigned_to_user_id == user.id)
                    rows = (
                        await self.db.scalars(
                            select(SupportTicket)
                            .where(*filters)
                            .order_by(SupportTicket.severity.desc(), SupportTicket.created_at)
                            .limit(5)
                        )
                    ).all()
                    for row in rows:
                        priority = (
                            "P0"
                            if row.severity == SupportTicketSeverity.critical
                            else "P1"
                            if row.severity == SupportTicketSeverity.high
                            else "P2"
                        )
                        items.append(
                            PlatformDashboardWorkItem(
                                id=str(row.id),
                                source_type="support_ticket",
                                priority=priority,
                                safe_title=f"Ticket {row.public_number}",
                                safe_summary=(
                                    f"Fila {row.queue.value} · severidade {row.severity.value}"
                                ),
                                href=f"/blackbox/support-tickets?ticket={row.id}",
                                due_at=min(row.first_response_due_at, row.resolution_due_at),
                            )
                        )
                elif role in {"sales_sdr", "commercial_manager"}:
                    filters = [GrowthProspect.estagio.in_(OPEN_STAGES)]
                    if owned:
                        filters.append(GrowthProspect.owner_user_id == user.id)
                    rows = (
                        await self.db.scalars(
                            select(GrowthProspect)
                            .where(*filters)
                            .order_by(GrowthProspect.stage_entered_at)
                            .limit(5)
                        )
                    ).all()
                    for row in rows:
                        items.append(
                            PlatformDashboardWorkItem(
                                id=str(row.id),
                                source_type="growth_prospect",
                                priority="P2",
                                safe_title=row.nome_escritorio,
                                safe_summary=f"Etapa {row.estagio.value}",
                                href=f"/blackbox/growth-crm?prospect={row.id}",
                            )
                        )
                elif role in {"onboarding", "customer_success"}:
                    rows = (
                        await self.db.scalars(
                            select(Workspace)
                            .where(
                                Workspace.onboarding_completed.is_(False),
                                Workspace.deleted_at.is_(None),
                            )
                            .order_by(Workspace.created_at)
                            .limit(5)
                        )
                    ).all()
                    for row in rows:
                        items.append(
                            PlatformDashboardWorkItem(
                                id=str(row.id),
                                source_type="workspace_onboarding",
                                priority="P2",
                                safe_title=row.name,
                                safe_summary="Onboarding pendente",
                                href=f"/blackbox/workspaces?workspace={row.id}",
                            )
                        )
                elif role == "finance_billing":
                    rows = (
                        await self.db.scalars(
                            select(WorkspaceSubscription)
                            .where(
                                WorkspaceSubscription.status
                                == WorkspaceSubscriptionStatus.inadimplente
                            )
                            .order_by(WorkspaceSubscription.updated_at)
                            .limit(5)
                        )
                    ).all()
                    for row in rows:
                        items.append(
                            PlatformDashboardWorkItem(
                                id=str(row.id),
                                source_type="billing",
                                priority="P1",
                                safe_title="Cobranca inadimplente",
                                safe_summary="Assinatura requer acompanhamento",
                                href=f"/blackbox/billing?workspace={row.workspace_id}",
                            )
                        )
                elif role == "marketing_content":
                    rows = (
                        await self.db.scalars(
                            select(BlogPost)
                            .where(
                                BlogPost.status.in_(
                                    (BlogPostStatus.draft, BlogPostStatus.scheduled)
                                )
                            )
                            .order_by(BlogPost.updated_at)
                            .limit(5)
                        )
                    ).all()
                    for row in rows:
                        items.append(
                            PlatformDashboardWorkItem(
                                id=str(row.id),
                                source_type="publication",
                                priority="P2",
                                safe_title=row.title,
                                safe_summary=f"Estado {row.status.value}",
                                href="/blackbox/site",
                            )
                        )
                elif role == "community_manager":
                    rows = (
                        await self.db.scalars(
                            select(SuperAdminNotice)
                            .where(
                                SuperAdminNotice.status.in_(
                                    (SuperAdminNoticeStatus.draft, SuperAdminNoticeStatus.scheduled)
                                )
                            )
                            .order_by(SuperAdminNotice.updated_at)
                            .limit(5)
                        )
                    ).all()
                    for row in rows:
                        items.append(
                            PlatformDashboardWorkItem(
                                id=str(row.id),
                                source_type="publication",
                                priority="P2",
                                safe_title=row.title,
                                safe_summary=f"Estado {row.status.value}",
                                href="/blackbox/announcements",
                            )
                        )
                elif role == "compliance_security":
                    rows = (
                        await self.db.scalars(
                            select(PlatformIncident)
                            .where(PlatformIncident.status != "resolved")
                            .order_by(PlatformIncident.started_at)
                            .limit(5)
                        )
                    ).all()
                    for row in rows:
                        items.append(
                            PlatformDashboardWorkItem(
                                id=str(row.id),
                                source_type="incident",
                                priority="P0" if row.severity == "SEV1" else "P1",
                                safe_title=row.title,
                                safe_summary=f"Severidade {row.severity} · {row.status}",
                                href=f"/blackbox/infrastructure?incident={row.id}",
                            )
                        )
            self._source_state.setdefault("priority_queue", ("available", None))
        except Exception:
            self._source_state["priority_queue"] = (
                "unavailable",
                "Fila prioritaria indisponivel nesta atualizacao.",
            )
            return []
        return items[:5]

    def _alerts(
        self, metrics: list[PlatformDashboardMetric], *, scope: DashboardScope
    ) -> list[PlatformDashboardAlert]:
        alerts: list[PlatformDashboardAlert] = []
        if scope == "team" and any(metric.value is None for metric in metrics):
            alerts.append(
                PlatformDashboardAlert(
                    id="team-scope-unavailable",
                    severity="info",
                    title="Escopo de equipe nao configurado",
                    description=(
                        "A home nao inventou dados coletivos; use o escopo pessoal disponivel."
                    ),
                )
            )
        by_key = {metric.key: metric for metric in metrics}
        for key, title, href in (
            (
                "incidents_open",
                "Incidentes ativos exigem acompanhamento",
                "/blackbox/infrastructure",
            ),
            ("tickets_critical", "Tickets criticos na fila", "/blackbox/support-tickets"),
            ("subscriptions_overdue", "Assinaturas inadimplentes", "/blackbox/billing"),
            ("errors_critical", "Erros criticos abertos", "/blackbox/errors"),
        ):
            metric = by_key.get(key)
            if metric and isinstance(metric.value, int | float) and metric.value > 0:
                alerts.append(
                    PlatformDashboardAlert(
                        id=f"metric:{key}",
                        severity="critical"
                        if key in {"incidents_open", "errors_critical"}
                        else "warning",
                        title=title,
                        description=(
                            f"{metric.value:g} item(ns) confirmado(s) pela fonte {metric.source}."
                        ),
                        href=href,
                    )
                )
        unavailable = sum(1 for metric in metrics if metric.value is None)
        if unavailable:
            alerts.append(
                PlatformDashboardAlert(
                    id="partial-sources",
                    severity="info",
                    title="Leitura parcial",
                    description=(
                        f"{unavailable} indicador(es) ficaram indisponiveis; "
                        "nenhum foi convertido em zero."
                    ),
                )
            )
        return alerts[:5]
