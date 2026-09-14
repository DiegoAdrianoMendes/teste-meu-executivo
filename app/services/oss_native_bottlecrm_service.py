"""Native, tenant-scoped BottleCRM capability adapter for KLM Hub."""

from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import get_effective_permissions
from app.models.approval import ApprovalQueueItem
from app.models.client import Client
from app.models.document import Document
from app.models.finance import FinanceEntry, IntegrationConnection
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.meeting import Meeting
from app.models.notification import Notification
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
    sanitize_oss_json,
)
from app.models.public_api import WorkspacePublicApiToken
from app.models.task import Task
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.bottlecrm_client import (
    BottleCRMClient,
    BottleCRMClientError,
    BottleCRMNotFoundError,
)
from app.services.bottlecrm_operations_service import BottleCRMOperationsService
from app.services.bottlecrm_sync_service import BottleCRMSyncService

Effect = Literal["state", "computed", "sync", "remote"]

_STATE_ROOT = "ossNativeState"
_PROVIDER = "bottlecrm"
_MAX_ROWS = 200
_MAX_DEPTH = 8
_MAX_STRING = 4_000
_STATE_MAX_BYTES = 100 * 1024
_REMOTE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TEMPLATE_VARIABLE = re.compile(r"{{\s*([A-Za-z0-9_.-]{1,80})\s*}}")
_SENSITIVE_OUTPUT_KEYS = {
    "address",
    "cpf",
    "email",
    "fullname",
    "firstname",
    "lastname",
    "name",
    "phone",
    "taxid",
    "telephone",
    "whatsapp",
}
_CREDENTIAL_KEY_FRAGMENTS = (
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "credential",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "signingkey",
    "webhooktoken",
)
_CONTROL_FIELDS = {
    "applyRemote",
    "confirmRemoteWrite",
    "expectedRecordVersion",
    "expectedRemoteDigest",
    "expectedVersion",
    "reason",
    "recordId",
    "remotePayload",
}


@dataclass(frozen=True)
class RemoteRead:
    name: str
    path: str
    params: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class CapabilitySpec:
    key: str
    title: str
    collection: str
    sections: tuple[str, ...]
    actions: tuple[str, ...]
    permission: tuple[str, str]
    canonical_resources: tuple[str, ...]
    remote_reads: tuple[RemoteRead, ...] = ()
    operations_view: str | None = None
    mode: str = "native_state"


@dataclass
class _StateBackendHandle:
    service: Any
    config_version: int
    configuration: dict[str, Any]


def _read(name: str, path: str, **params: Any) -> RemoteRead:
    return RemoteRead(name=name, path=path, params=tuple(sorted(params.items())))


def _spec(
    key: str,
    title: str,
    collection: str,
    sections: tuple[str, ...],
    actions: tuple[str, ...],
    permission: tuple[str, str],
    canonical_resources: tuple[str, ...],
    *remote_reads: RemoteRead,
    operations_view: str | None = None,
    mode: str = "native_state",
) -> CapabilitySpec:
    return CapabilitySpec(
        key=key,
        title=title,
        collection=collection,
        sections=sections,
        actions=actions,
        permission=permission,
        canonical_resources=canonical_resources,
        remote_reads=tuple(remote_reads),
        operations_view=operations_view,
        mode=mode,
    )


_CAPABILITIES = (
    _spec(
        "accounts-contacts-leads",
        "Contas, contatos e leads",
        "crm_identity",
        ("accounts", "contacts", "leads"),
        ("upsert_account", "upsert_contact", "upsert_lead", "archive_record", "sync_workspace"),
        ("kanban", "edit"),
        ("clients", "matters"),
        _read("accounts", "/api/accounts/", limit=50),
        _read("contacts", "/api/contacts/", limit=50),
        _read("leads", "/api/leads/", limit=50),
        operations_view="funnel",
    ),
    _spec(
        "opportunities-kanban",
        "Oportunidades e Kanban",
        "opportunities",
        ("pipeline", "cards", "probability", "ownership"),
        ("move_opportunity", "set_probability", "assign_owner", "sync_workspace"),
        ("kanban", "edit"),
        ("matters",),
        _read("kanban", "/api/opportunities/kanban/"),
        _read("opportunities", "/api/opportunities/", limit=50),
        operations_view="funnel",
    ),
    _spec(
        "cases-tickets",
        "Casos e tickets básicos",
        "cases",
        ("cases", "status", "priority", "ownership"),
        ("update_case", "assign_case", "change_case_status", "sync_workspace"),
        ("kanban", "edit"),
        ("matters",),
        _read("kanban", "/api/cases/kanban/"),
        _read("cases", "/api/cases/", limit=50),
        operations_view="matter",
    ),
    _spec(
        "case-solutions-kb",
        "Soluções, base de conhecimento e sugestões",
        "case_solutions",
        ("solutions", "case_links", "suggestions", "publication"),
        ("upsert_solution", "link_solution", "publish_solution", "unpublish_solution"),
        ("kanban", "edit"),
        ("documents",),
        _read("solutions", "/api/cases/solutions/", limit=50),
    ),
    _spec(
        "case-mailboxes-watchers",
        "Mailboxes, seguidores e observação",
        "case_mailboxes",
        ("mailboxes", "watchers", "watched_cases", "email_ingestion"),
        ("upsert_mailbox", "watch_case", "unwatch_case"),
        ("integrations", "manage"),
        ("matters",),
        _read("mailboxes", "/api/cases/mailboxes/", limit=50),
        _read("watching", "/api/cases/watching/", limit=50),
    ),
    _spec(
        "case-merge-hierarchy",
        "Mesclagem e árvore de casos",
        "case_hierarchy",
        ("tree", "parents", "children", "merge_history"),
        ("merge_cases", "unmerge_case", "link_parent", "close_with_children"),
        ("kanban", "edit"),
        ("matters",),
    ),
    _spec(
        "case-time-billing",
        "Tempo aplicado e faturamento",
        "time_entries",
        ("time_entries", "running_timers", "billable_summary", "invoice_origin"),
        ("start_timer", "stop_timer", "add_time_entry", "mark_billable"),
        ("finance", "manage"),
        ("matters", "finance_entries"),
    ),
    _spec(
        "case-analytics-bulk-import",
        "Analytics, exportação e operações em lote",
        "case_analytics",
        ("frt", "mttr", "backlog", "agents", "sla", "bulk_operations"),
        ("preview_import", "commit_import", "bulk_update", "export"),
        ("relatorios", "export"),
        ("matters", "audit_events"),
        _read("frt", "/api/cases/analytics/frt/"),
        _read("mttr", "/api/cases/analytics/mttr/"),
        _read("backlog", "/api/cases/analytics/backlog/"),
        _read("agents", "/api/cases/analytics/agents/"),
        _read("sla", "/api/cases/analytics/sla/"),
    ),
    _spec(
        "case-pipelines-reopen",
        "Pipelines, etapas e política de reabertura",
        "case_pipelines",
        ("pipelines", "stages", "ordering", "reopen_policy"),
        ("upsert_pipeline", "reorder_stages", "set_reopen_policy"),
        ("automations", "manage"),
        ("matters",),
        _read("pipelines", "/api/cases/pipelines/", limit=50),
        _read("reopen_policy", "/api/cases/reopen-policy/"),
    ),
    _spec(
        "tasks-calendar",
        "Tarefas e calendário",
        "tasks_calendar",
        ("tasks", "calendar", "assignees", "deadlines"),
        ("upsert_task", "complete_task", "schedule_task", "sync_workspace"),
        ("kanban", "edit"),
        ("tasks", "meetings"),
        _read("tasks", "/api/tasks/", limit=50),
        _read("kanban", "/api/tasks/kanban/"),
        operations_view="work",
    ),
    _spec(
        "task-boards-pipelines",
        "Boards e pipelines de tarefas",
        "task_boards",
        ("boards", "columns", "tasks", "ordering"),
        ("upsert_board", "move_task", "reorder_columns"),
        ("automations", "manage"),
        ("tasks",),
        _read("boards", "/api/boards/", limit=50),
        _read("task_kanban", "/api/tasks/kanban/"),
        operations_view="work",
    ),
    _spec(
        "products-estimates-quotes",
        "Produtos, estimativas e propostas",
        "commercial_quotes",
        ("products", "estimates", "line_items", "legal_proposals"),
        ("upsert_product", "upsert_estimate", "convert_to_proposal"),
        ("finance", "manage"),
        ("finance_entries",),
        _read("products", "/api/invoices/products/", limit=50),
        _read("estimates", "/api/invoices/estimates/", limit=50),
        operations_view="finance",
    ),
    _spec(
        "invoices-recurrence",
        "Faturas e recorrência",
        "invoices",
        ("invoices", "recurrence", "payments", "aging"),
        ("upsert_invoice", "schedule_recurrence", "cancel_recurrence"),
        ("finance", "manage"),
        ("finance_entries",),
        _read("invoices", "/api/invoices/", limit=50),
        _read("recurring", "/api/invoices/recurring/", limit=50),
        operations_view="finance",
    ),
    _spec(
        "orders",
        "Pedidos e itens comerciais",
        "orders",
        ("orders", "items", "commercial_status", "invoice_equivalent"),
        ("upsert_order", "set_order_status", "convert_order_to_invoice"),
        ("finance", "manage"),
        ("finance_entries",),
        mode="klm_equivalent",
    ),
    _spec(
        "templates-macros",
        "Templates e macros",
        "macros",
        ("templates", "variables", "rendering", "usage"),
        ("upsert_macro", "render_macro", "archive_macro"),
        ("automations", "manage"),
        ("workspace",),
        _read("macros", "/api/macros/", limit=50),
    ),
    _spec(
        "opportunity-aging-goals",
        "Aging de oportunidades e metas",
        "opportunity_goals",
        ("aging", "goals", "leaderboard", "alerts"),
        ("set_aging_config", "upsert_goal", "close_goal"),
        ("kanban", "edit"),
        ("matters",),
        _read("aging", "/api/opportunities/aging-config/"),
        _read("goals", "/api/opportunities/goals/", limit=50),
        _read("leaderboard", "/api/opportunities/goals/leaderboard/"),
        operations_view="funnel",
    ),
    _spec(
        "invoice-estimate-portals",
        "Portais tokenizados de fatura e estimativa",
        "finance_portals",
        ("invoice_portals", "estimate_portals", "access_policy", "revocation"),
        ("publish_portal", "revoke_portal", "rotate_portal_link"),
        ("integrations", "manage"),
        ("finance_entries",),
        _read("invoices", "/api/invoices/", limit=20),
        _read("estimates", "/api/invoices/estimates/", limit=20),
        operations_view="finance",
    ),
    _spec(
        "approvals-routing-escalation",
        "Aprovações, roteamento e escalonamento",
        "case_workflow",
        ("approval_inbox", "routing_rules", "escalation_policies", "human_gates"),
        (
            "request_approval",
            "approve",
            "reject",
            "upsert_routing_rule",
            "upsert_escalation_policy",
        ),
        ("aprovacoes", "approve"),
        ("approvals",),
        _read("approvals", "/api/cases/approvals/", limit=50),
        _read("routing", "/api/cases/routing-rules/", limit=50),
        _read("escalation", "/api/cases/escalation-policies/", limit=50),
    ),
    _spec(
        "business-hours-sla-csat",
        "Horários, SLA e CSAT",
        "service_quality",
        ("business_calendar", "sla", "escalations", "csat"),
        ("upsert_calendar", "upsert_sla", "pause_sla", "record_csat"),
        ("configuracoes", "edit"),
        ("matters",),
        _read("calendar", "/api/business-hours/calendar/"),
        _read("sla", "/api/cases/analytics/sla/"),
        _read("csat", "/api/cases/csat/aggregate/"),
    ),
    _spec(
        "custom-fields-duplicates",
        "Campos customizados e duplicidade",
        "data_quality",
        ("field_definitions", "duplicate_candidates", "resolution", "schema_policy"),
        ("upsert_custom_field", "resolve_duplicate", "merge_duplicate"),
        ("kanban", "edit"),
        ("clients", "matters"),
        _read("custom_fields", "/api/custom-fields/", limit=50),
    ),
    _spec(
        "tags-documents",
        "Tags e documentos compartilhados",
        "tags_documents",
        ("tags", "documents", "record_links", "rag_promotion"),
        ("upsert_tag", "attach_document", "detach_document", "restore_document"),
        ("kanban", "edit"),
        ("documents",),
        _read("tags", "/api/tags/", limit=50),
        _read("documents", "/api/documents/", limit=50),
    ),
    _spec(
        "notifications-sse",
        "Notificações em tempo real por SSE",
        "notifications",
        ("feed", "unread", "subscriptions", "activity_projection"),
        ("mark_read", "mark_all_read", "subscribe"),
        ("notifications", "manage"),
        ("notifications",),
        _read("notifications", "/api/notifications/", limit=50),
        operations_view="work",
    ),
    _spec(
        "import-export",
        "Importação e exportação",
        "data_exchange",
        ("preview", "validation", "commit", "export", "receipts"),
        ("preview_import", "commit_import", "export"),
        ("lista", "export"),
        ("clients", "matters"),
        mode="klm_canonical",
    ),
    _spec(
        "users-rls-rbac",
        "Usuários, RLS e RBAC",
        "users_security",
        ("users", "roles", "grants", "denials", "tenant_isolation"),
        ("upsert_user", "set_role", "disable_user"),
        ("users", "manage"),
        ("memberships",),
        _read("users", "/api/users/", limit=50),
        _read("teams", "/api/users/get-teams-and-users/"),
        mode="klm_canonical",
    ),
    _spec(
        "audit-suspicious-activity",
        "Auditoria e atividade suspeita",
        "audit",
        ("events", "alerts", "token_activity", "investigation"),
        ("acknowledge_alert", "close_alert", "export"),
        ("audit", "view"),
        ("audit_events",),
        _read("activities", "/api/activities/", limit=50),
        mode="klm_canonical",
    ),
    _spec(
        "api-tokens-mcp",
        "API tokens e MCP",
        "api_access",
        ("tokens", "scopes", "mcp_tools", "revocation", "audit"),
        ("create_token", "revoke_token", "register_mcp_scope"),
        ("integrations", "manage"),
        ("api_tokens",),
        _read("personal_tokens", "/api/profile/tokens/", limit=50),
        _read("api_settings", "/api/api-settings/", limit=50),
        mode="klm_canonical",
    ),
    _spec(
        "invoice-products",
        "Catálogo de produtos e serviços",
        "invoice_products",
        ("products", "prices", "taxes", "active_catalog"),
        ("upsert_product", "archive_product", "restore_record"),
        ("finance", "manage"),
        ("finance_entries",),
        _read("products", "/api/invoices/products/", limit=50),
        operations_view="finance",
    ),
    _spec(
        "invoice-templates",
        "Templates de fatura e estimativa",
        "invoice_templates",
        ("templates", "branding", "numbering", "document_defaults"),
        ("upsert_template", "archive_template", "restore_record"),
        ("finance", "manage"),
        ("workspace",),
        _read("templates", "/api/invoices/templates/", limit=50),
        operations_view="finance",
    ),
    _spec(
        "invoice-reports",
        "Relatórios financeiros do CRM",
        "invoice_reports",
        ("dashboard", "revenue", "aging", "scheduled_reports"),
        ("generate_report", "schedule_report", "export"),
        ("relatorios", "export"),
        ("finance_entries",),
        _read("dashboard", "/api/invoices/reports/dashboard/"),
        _read("revenue", "/api/invoices/reports/revenue/", group_by="month"),
        _read("aging", "/api/invoices/reports/aging/"),
        operations_view="finance",
    ),
    _spec(
        "ticket-approvals",
        "Aprovações de tickets",
        "ticket_approvals",
        ("inbox", "decisions", "reasons", "audit"),
        ("approve_ticket", "reject_ticket", "request_changes"),
        ("aprovacoes", "approve"),
        ("approvals",),
        _read("approvals", "/api/cases/approvals/", limit=50),
    ),
    _spec(
        "ticket-routing",
        "Regras de roteamento de tickets",
        "ticket_routing",
        ("rules", "conditions", "destinations", "dry_run"),
        ("upsert_routing_rule", "test_routing_rule", "disable_routing_rule"),
        ("automations", "manage"),
        ("workspace",),
        _read("routing", "/api/cases/routing-rules/", limit=50),
    ),
    _spec(
        "ticket-escalation",
        "Escalonamento de tickets",
        "ticket_escalation",
        ("policies", "thresholds", "destinations", "dry_run"),
        ("upsert_escalation_policy", "run_escalation_dry_run", "disable_escalation_policy"),
        ("automations", "manage"),
        ("workspace",),
        _read("escalation", "/api/cases/escalation-policies/", limit=50),
    ),
    _spec(
        "organization-settings",
        "Organização do CRM local",
        "organization_settings",
        ("identity", "locale", "currency", "defaults", "binding"),
        ("update_organization_settings", "verify_binding"),
        ("configuracoes", "edit"),
        ("workspace",),
        _read("settings", "/api/org/settings/"),
    ),
    _spec(
        "organization-switcher",
        "Seletor de organização do CRM",
        "organization_switcher",
        ("available_organizations", "current_binding", "switch_policy"),
        ("select_organization", "verify_binding"),
        ("configuracoes", "edit"),
        ("workspace",),
        _read("identity", "/api/auth/me/"),
    ),
    _spec(
        "mobile-app",
        "Experiência móvel responsiva KLM",
        "mobile_preferences",
        ("responsive_shell", "compact_navigation", "touch_actions", "offline_policy"),
        ("update_mobile_preferences", "reset_mobile_preferences"),
        ("configuracoes", "edit"),
        ("workspace",),
        mode="klm_responsive",
    ),
    _spec(
        "external-integrations",
        "Configuração de integrações externas",
        "external_integrations",
        ("email", "calendar", "payments", "notifications", "external_gate"),
        ("upsert_integration_config", "disable_integration", "test_integration"),
        ("integrations", "manage"),
        ("integration_connections",),
        _read("api_settings", "/api/api-settings/", limit=50),
        mode="configuration_only",
    ),
)

CAPABILITY_KEYS = tuple(spec.key for spec in _CAPABILITIES)
_SPEC_BY_KEY = {spec.key: spec for spec in _CAPABILITIES}

if len(CAPABILITY_KEYS) != 36 or len(_SPEC_BY_KEY) != 36:
    raise RuntimeError("O adapter BottleCRM deve declarar exatamente 36 capacidades únicas.")


_ACTION_PERMISSION_OVERRIDES: dict[str, tuple[str, str]] = {
    "bulk_update": ("kanban", "edit"),
    "close_alert": ("admin", "manage"),
    "commit_import": ("kanban", "edit"),
    "create_token": ("integrations", "manage"),
    "export": ("relatorios", "export"),
    "preview_import": ("lista", "export"),
    "request_approval": ("aprovacoes", "view"),
    "revoke_token": ("integrations", "manage"),
    "acknowledge_alert": ("admin", "manage"),
    "upsert_escalation_policy": ("automations", "manage"),
    "upsert_routing_rule": ("automations", "manage"),
}

_COMPUTED_COMMANDS = {
    "export",
    "generate_report",
    "preview_import",
    "render_macro",
    "run_escalation_dry_run",
    "test_integration",
    "test_routing_rule",
    "verify_binding",
}
_SYNC_COMMANDS = {"sync_workspace"}
_TOMBSTONE_COMMANDS = {
    "archive_macro",
    "archive_product",
    "archive_record",
    "archive_template",
    "detach_document",
    "revoke_portal",
}
_RESTORE_COMMANDS = {"restore_document", "restore_record"}
_MERGE_COMMANDS = {"merge_cases", "merge_duplicate"}

# Remote execution remains opt-in. Every mapped command still requires the digest returned
# by snapshot plus confirmRemoteWrite=true; all other commands stay in canonical KLM state.
_REMOTE_ACTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("opportunities-kanban", "move_opportunity"): ("POST", "/api/opportunities/{id}/move/"),
    ("case-solutions-kb", "publish_solution"): (
        "POST",
        "/api/cases/solutions/{id}/publish/",
    ),
    ("case-solutions-kb", "unpublish_solution"): (
        "POST",
        "/api/cases/solutions/{id}/unpublish/",
    ),
    ("case-mailboxes-watchers", "watch_case"): ("POST", "/api/cases/{id}/watch/"),
    ("case-merge-hierarchy", "merge_cases"): (
        "POST",
        "/api/cases/{id}/merge/{into_id}/",
    ),
    ("case-merge-hierarchy", "unmerge_case"): ("POST", "/api/cases/{id}/unmerge/"),
    ("case-merge-hierarchy", "link_parent"): ("POST", "/api/cases/{id}/link/"),
    ("case-merge-hierarchy", "close_with_children"): (
        "POST",
        "/api/cases/{id}/close-with-children/",
    ),
    ("case-time-billing", "start_timer"): (
        "POST",
        "/api/cases/{id}/time-entries/start/",
    ),
    ("case-time-billing", "add_time_entry"): (
        "POST",
        "/api/cases/{id}/time-entries/",
    ),
    ("task-boards-pipelines", "move_task"): ("POST", "/api/tasks/{id}/move/"),
    ("products-estimates-quotes", "convert_to_proposal"): (
        "POST",
        "/api/invoices/estimates/{id}/convert/",
    ),
    ("invoices-recurrence", "cancel_recurrence"): (
        "POST",
        "/api/invoices/recurring/{id}/toggle/",
    ),
    ("approvals-routing-escalation", "approve"): (
        "POST",
        "/api/cases/approvals/{id}/approve/",
    ),
    ("approvals-routing-escalation", "reject"): (
        "POST",
        "/api/cases/approvals/{id}/reject/",
    ),
    ("notifications-sse", "mark_read"): (
        "POST",
        "/api/notifications/{id}/read/",
    ),
    ("notifications-sse", "mark_all_read"): (
        "POST",
        "/api/notifications/read-all/",
    ),
    ("ticket-approvals", "approve_ticket"): (
        "POST",
        "/api/cases/approvals/{id}/approve/",
    ),
    ("ticket-approvals", "reject_ticket"): (
        "POST",
        "/api/cases/approvals/{id}/reject/",
    ),
    ("organization-switcher", "select_organization"): (
        "POST",
        "/api/auth/switch-org/",
    ),
}


class BottleCRMNativeConflictError(RuntimeError):
    """Raised only when a caller explicitly asks for exception-style conflicts."""

    def __init__(self, conflict: dict[str, Any]):
        super().__init__(str(conflict.get("message") or "Conflito de estado BottleCRM."))
        self.conflict = conflict


class BottleCRMNativeWorkspaceBindingError(PermissionError):
    """Raised before remote access when the BottleCRM organization is not tenant-safe."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        return {
            str(key)[:160]: _json_safe(item, depth=depth + 1)
            for key, item in list(value.items())[:_MAX_ROWS]
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:_MAX_ROWS]]
    if isinstance(value, datetime):
        candidate = value if value.tzinfo else value.replace(tzinfo=UTC)
        return candidate.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return _json_safe(value.value, depth=depth + 1)
    if isinstance(value, str):
        return value[:_MAX_STRING]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:_MAX_STRING]


def _without_credentials(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        redacted: list[str] = []
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if (
                item == "[REDACTED]"
                or normalized.endswith("token")
                or any(fragment in normalized for fragment in _CREDENTIAL_KEY_FRAGMENTS)
            ):
                redacted.append(key)
                continue
            result[key] = _without_credentials(item)
        if redacted:
            result["redactedFields"] = sorted(redacted)
        return result
    if isinstance(value, list):
        return [_without_credentials(item) for item in value]
    return value


def _storage_safe(value: Any) -> Any:
    return _without_credentials(sanitize_oss_json(_json_safe(value)))


def _output_safe(value: Any, *, include_sensitive: bool = False) -> Any:
    sanitized = _storage_safe(value)
    if include_sensitive:
        return sanitized
    if isinstance(sanitized, dict):
        result: dict[str, Any] = {}
        for key, item in sanitized.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            sensitive_suffix = any(
                normalized.endswith(suffix)
                for suffix in ("cpf", "email", "name", "phone", "taxid", "telephone")
            )
            if normalized in _SENSITIVE_OUTPUT_KEYS or sensitive_suffix:
                result[key] = "[RESTRITO]" if item not in (None, "") else item
            else:
                result[key] = _output_safe(item, include_sensitive=False)
        return result
    if isinstance(sanitized, list):
        return [_output_safe(item, include_sensitive=False) for item in sanitized]
    return sanitized


def _digest(value: Any) -> str:
    canonical = json.dumps(_storage_safe(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _default_state() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "version": 0,
        "records": {},
        "tombstones": {},
        "conflicts": [],
        "lastRemoteDigest": None,
        "updatedAt": None,
        "updatedBy": None,
    }


def _normalize_state(value: Any) -> dict[str, Any]:
    state = copy.deepcopy(value) if isinstance(value, dict) else {}
    normalized = _default_state()
    normalized.update(state)
    normalized["version"] = max(0, int(normalized.get("version") or 0))
    normalized["records"] = (
        dict(normalized["records"]) if isinstance(normalized.get("records"), dict) else {}
    )
    normalized["tombstones"] = (
        dict(normalized["tombstones"]) if isinstance(normalized.get("tombstones"), dict) else {}
    )
    normalized["conflicts"] = (
        list(normalized["conflicts"])[-_MAX_ROWS:]
        if isinstance(normalized.get("conflicts"), list)
        else []
    )
    normalized = _storage_safe(normalized)
    while len(json.dumps(normalized, separators=(",", ":")).encode("utf-8")) > _STATE_MAX_BYTES:
        if normalized["conflicts"]:
            normalized["conflicts"].pop(0)
            continue
        if normalized["tombstones"]:
            oldest = next(iter(normalized["tombstones"]))
            normalized["tombstones"].pop(oldest)
            continue
        raise ValueError("Estado nativo BottleCRM excede o limite persistente seguro.")
    return normalized


class BottleCRMNativeService:
    """Expose all pinned BottleCRM capabilities through one conservative KLM contract."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._client_instance: BottleCRMClient | None = None
        self._operations_instance: BottleCRMOperationsService | None = None
        self._sync_instance: BottleCRMSyncService | None = None
        self._state_backend_checked = False
        self._state_backend_instance: Any = None

    def _spec(self, capability_key: str) -> CapabilitySpec:
        key = str(capability_key or "").strip()
        spec = _SPEC_BY_KEY.get(key)
        if spec is None:
            raise ValueError(f"Capacidade BottleCRM desconhecida: {key or '[vazia]'}.")
        return spec

    def _client(self) -> BottleCRMClient:
        if self._client_instance is None:
            self._client_instance = BottleCRMClient()
        return self._client_instance

    def _operations(self) -> BottleCRMOperationsService:
        if self._operations_instance is None:
            service = BottleCRMOperationsService(self.db)
            service.client = self._client()
            self._operations_instance = service
        return self._operations_instance

    def _sync(self) -> BottleCRMSyncService:
        if self._sync_instance is None:
            service = BottleCRMSyncService(self.db)
            service.client = self._client()
            self._sync_instance = service
        return self._sync_instance

    async def _bound_organization(
        self,
        workspace_id: UUID,
        *,
        for_update: bool,
    ) -> str:
        statement = select(ExternalEntityLink).where(
            ExternalEntityLink.workspace_id == workspace_id,
            ExternalEntityLink.provider == OSSBridgeProvider.bottlecrm,
            ExternalEntityLink.klm_entity_type == "workspace",
            ExternalEntityLink.external_entity_type == "organization",
            ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
        )
        if for_update:
            statement = statement.with_for_update()
        bindings = list((await self.db.execute(statement)).scalars())
        if not bindings:
            raise BottleCRMNativeWorkspaceBindingError(
                "workspace_binding_missing",
                "BottleCRM bloqueado: o workspace não possui organização vinculada e ativa.",
            )
        if len(bindings) != 1:
            raise BottleCRMNativeWorkspaceBindingError(
                "workspace_binding_ambiguous",
                "BottleCRM bloqueado: o workspace possui mais de uma organização ativa.",
            )

        binding = bindings[0]
        organization_id = str(binding.external_account_id or "").strip()
        if (
            not organization_id
            or binding.klm_entity_id != workspace_id
            or binding.external_entity_id != organization_id
        ):
            raise BottleCRMNativeWorkspaceBindingError(
                "workspace_binding_invalid",
                "BottleCRM bloqueado: o vínculo da organização está inconsistente.",
            )

        foreign_statement = select(ExternalEntityLink.id).where(
            ExternalEntityLink.provider == OSSBridgeProvider.bottlecrm,
            ExternalEntityLink.external_account_id == organization_id,
            ExternalEntityLink.klm_entity_type == "workspace",
            ExternalEntityLink.external_entity_type == "organization",
            ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
            ExternalEntityLink.workspace_id != workspace_id,
        )
        if for_update:
            foreign_statement = foreign_statement.with_for_update()
        foreign_binding = await self.db.scalar(foreign_statement)
        if foreign_binding is not None:
            raise BottleCRMNativeWorkspaceBindingError(
                "organization_bound_to_foreign_workspace",
                "BottleCRM bloqueado: a organização está vinculada a outro workspace.",
            )
        return organization_id

    async def _validated_remote_organization(
        self,
        workspace_id: UUID,
        *,
        for_update: bool,
    ) -> str:
        expected_organization_id = await self._bound_organization(
            workspace_id,
            for_update=for_update,
        )
        runtime_organization_id = str(await self._client().organization_id()).strip()
        if runtime_organization_id != expected_organization_id:
            raise BottleCRMNativeWorkspaceBindingError(
                "runtime_organization_mismatch",
                "BottleCRM bloqueado: a sessão remota não pertence à organização vinculada.",
            )
        return expected_organization_id

    def _late_state_backend(self) -> Any:
        if self._state_backend_checked:
            return self._state_backend_instance
        self._state_backend_checked = True
        try:
            module = importlib.import_module("app.services.oss_native_state_service")
        except ModuleNotFoundError as error:
            if error.name != "app.services.oss_native_state_service":
                raise
            return None
        backend_type = getattr(module, "OSSNativeStateService", None)
        if backend_type is None:
            raise RuntimeError(
                "oss_native_state_service existe, mas não expõe OSSNativeStateService."
            )
        self._state_backend_instance = backend_type(self.db)
        return self._state_backend_instance

    @staticmethod
    async def _invoke_backend(method: Callable[..., Any], values: dict[str, Any]) -> Any:
        signature = inspect.signature(method)
        kwargs: dict[str, Any] = {}
        for name, parameter in signature.parameters.items():
            if name in values:
                kwargs[name] = values[name]
            elif parameter.default is inspect.Parameter.empty:
                raise RuntimeError(f"Contrato de estado nativo não reconhecido: parâmetro {name}.")
        result = method(**kwargs)
        return await result if inspect.isawaitable(result) else result

    async def _workspace(self, workspace_id: UUID, *, for_update: bool) -> Workspace:
        statement = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        workspace = await self.db.scalar(statement)
        if workspace is None:
            raise LookupError("Workspace KLM não encontrado.")
        return workspace

    async def _load_state(
        self,
        workspace_id: UUID,
        capability_key: str,
        *,
        for_update: bool,
    ) -> tuple[dict[str, Any], Workspace | None, Any]:
        backend = self._late_state_backend()
        if backend is not None:
            method = next(
                (
                    getattr(backend, name, None)
                    for name in ("get_snapshot", "load", "read", "snapshot")
                    if getattr(backend, name, None)
                ),
                None,
            )
            if method is None:
                raise RuntimeError("OSSNativeStateService não possui operação de leitura.")
            result = await self._invoke_backend(
                method,
                {
                    "workspace_id": workspace_id,
                    "provider": _PROVIDER,
                    "capability": capability_key,
                    "capability_key": capability_key,
                    "query": {},
                    "for_update": for_update,
                },
            )
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json")
            if isinstance(result, dict):
                snapshot_state = result.get("state")
                if isinstance(snapshot_state, dict) and "configuration" in snapshot_state:
                    configuration = dict(snapshot_state.get("configuration") or {})
                    adapter_state = configuration.get("adapterState")
                    if not isinstance(adapter_state, dict):
                        adapter_state = configuration.get("adapter_state")
                    handle = _StateBackendHandle(
                        service=backend,
                        config_version=int(
                            snapshot_state.get("config_version")
                            or snapshot_state.get("configVersion")
                            or 0
                        ),
                        configuration=configuration,
                    )
                    return _normalize_state(adapter_state), None, handle
                result = result.get("state", result.get("nativeState", result))
            return _normalize_state(result), None, _StateBackendHandle(backend, 0, {})

        workspace = await self._workspace(workspace_id, for_update=for_update)
        settings = dict(workspace.settings or {})
        root = settings.get(_STATE_ROOT)
        root = root if isinstance(root, dict) else {}
        provider_state = root.get(_PROVIDER)
        provider_state = provider_state if isinstance(provider_state, dict) else {}
        return _normalize_state(provider_state.get(capability_key)), workspace, None

    async def _save_state(
        self,
        *,
        workspace_id: UUID,
        capability_key: str,
        state: dict[str, Any],
        expected_version: int,
        actor_user_id: UUID,
        workspace: Workspace | None,
        backend: Any,
    ) -> dict[str, Any]:
        normalized = _normalize_state(state)
        if isinstance(backend, _StateBackendHandle) and hasattr(backend.service, "update_config"):
            schemas = importlib.import_module("app.schemas.oss_native")
            models = importlib.import_module("app.models.oss_native")
            configuration = {
                **backend.configuration,
                "adapterState": normalized,
            }
            data = schemas.OSSNativeConfigPatch(
                expected_version=backend.config_version,
                configuration=configuration,
                execution_mode=models.OSSNativeExecutionMode.local_native,
            )
            try:
                result = await backend.service.update_config(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    provider=_PROVIDER,
                    capability=capability_key,
                    data=data,
                )
            except Exception as error:
                if error.__class__.__name__ != "OSSNativeVersionConflictError":
                    raise
                raise BottleCRMNativeConflictError(
                    self._conflict(
                        conflict_type="state_backend_version_mismatch",
                        message="A configuração nativa mudou durante a escrita.",
                        expected=backend.config_version,
                        actual=getattr(error, "current_version", None),
                    )
                ) from error
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json")
            if isinstance(result, dict):
                snapshot_state = result.get("state")
                if isinstance(snapshot_state, dict):
                    backend.config_version = int(
                        snapshot_state.get("config_version")
                        or snapshot_state.get("configVersion")
                        or backend.config_version + 1
                    )
                    backend.configuration = dict(snapshot_state.get("configuration") or {})
                    saved = backend.configuration.get("adapterState")
                    if isinstance(saved, dict):
                        return _normalize_state(saved)
            return normalized

        if isinstance(backend, _StateBackendHandle):
            backend = backend.service
        if backend is not None:
            method = next(
                (
                    getattr(backend, name, None)
                    for name in ("compare_and_set", "save", "replace", "execute")
                    if getattr(backend, name, None)
                ),
                None,
            )
            if method is None:
                raise RuntimeError("OSSNativeStateService não possui operação de escrita.")
            result = await self._invoke_backend(
                method,
                {
                    "workspace_id": workspace_id,
                    "actor_user_id": actor_user_id,
                    "provider": _PROVIDER,
                    "capability_key": capability_key,
                    "command": "replace_state",
                    "payload": {"state": normalized, "expectedVersion": expected_version},
                    "state": normalized,
                    "expected_version": expected_version,
                },
            )
            if isinstance(result, dict):
                result_state = result.get("state", result.get("nativeState"))
                if isinstance(result_state, dict):
                    return _normalize_state(result_state)
            return normalized

        if workspace is None:
            raise RuntimeError("Workspace bloqueado ausente para persistência de estado.")
        settings = dict(workspace.settings or {})
        root = dict(settings.get(_STATE_ROOT) or {})
        provider_state = dict(root.get(_PROVIDER) or {})
        provider_state[capability_key] = normalized
        root[_PROVIDER] = provider_state
        workspace.settings = {**settings, _STATE_ROOT: root}
        await self.db.flush()
        return normalized

    async def _actor_permissions(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> set[tuple[str, str]]:
        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == actor_user_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        if membership is None:
            raise PermissionError("Usuário sem vínculo ativo com o workspace.")
        return {
            (permission.module, permission.action)
            for permission in get_effective_permissions(
                membership.role,
                membership.permission_grants,
                membership.permission_denials,
            )
        }

    async def _require_command_permission(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        spec: CapabilitySpec,
        command: str,
    ) -> tuple[str, str]:
        required = _ACTION_PERMISSION_OVERRIDES.get(command, spec.permission)
        permissions = await self._actor_permissions(workspace_id, actor_user_id)
        if required not in permissions:
            raise PermissionError(
                f"A ação {command} exige a permissão {required[0]}:{required[1]}."
            )
        return required

    async def _canonical_counts(
        self,
        workspace_id: UUID,
        resources: tuple[str, ...],
    ) -> dict[str, int]:
        async def direct(model: Any) -> int:
            statement = (
                select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
            )
            if hasattr(model, "deleted_at"):
                statement = statement.where(model.deleted_at.is_(None))
            return int(await self.db.scalar(statement) or 0)

        async def through_matter(model: Any) -> int:
            return int(
                await self.db.scalar(
                    select(func.count())
                    .select_from(model)
                    .join(Matter, model.matter_id == Matter.id)
                    .where(
                        Matter.workspace_id == workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                )
                or 0
            )

        counters: dict[str, Callable[[], Awaitable[int]]] = {
            "api_tokens": lambda: direct(WorkspacePublicApiToken),
            "audit_events": lambda: direct(KlmAuditEvent),
            "clients": lambda: direct(Client),
            "finance_entries": lambda: direct(FinanceEntry),
            "integration_connections": lambda: direct(IntegrationConnection),
            "matters": lambda: direct(Matter),
            "meetings": lambda: direct(Meeting),
            "memberships": lambda: direct(WorkspaceMembership),
            "notifications": lambda: direct(Notification),
            "approvals": lambda: through_matter(ApprovalQueueItem),
            "documents": lambda: through_matter(Document),
            "tasks": lambda: through_matter(Task),
            "workspace": lambda: self._workspace_count(workspace_id),
        }
        return {
            resource: await counters[resource]() for resource in resources if resource in counters
        }

    async def _workspace_count(self, workspace_id: UUID) -> int:
        return 1 if await self._workspace(workspace_id, for_update=False) else 0

    @staticmethod
    def _error_contract(error: Exception) -> dict[str, str]:
        if isinstance(error, BottleCRMNotFoundError):
            return {"code": "remote_tombstone", "message": "O recurso remoto não existe mais."}
        if isinstance(error, BottleCRMClientError):
            return {
                "code": "bottlecrm_unavailable",
                "message": "BottleCRM local indisponível ou com contrato incompatível.",
            }
        return {"code": "adapter_error", "message": "Falha ao normalizar o recurso BottleCRM."}

    async def _operations_context(
        self,
        spec: CapabilitySpec,
        workspace_id: UUID,
        query: dict[str, Any],
    ) -> dict[str, Any] | None:
        if spec.operations_view is None:
            return None
        operations = self._operations()
        if spec.operations_view == "funnel":
            return await operations.funnel_context(workspace_id, include_sensitive=False)
        if spec.operations_view == "work":
            return await operations.work_context(workspace_id, include_sensitive=False)
        if spec.operations_view == "finance":
            return await operations.finance_context(workspace_id, include_sensitive=False)
        raw_matter_id = query.get("matterId")
        if spec.operations_view == "matter" and raw_matter_id:
            try:
                matter_id = UUID(str(raw_matter_id))
            except (TypeError, ValueError) as error:
                raise ValueError("matterId inválido para o contexto BottleCRM.") from error
            return await operations.matter_context(
                workspace_id,
                matter_id,
                include_sensitive=False,
            )
        return {
            "status": "context_required",
            "requiredQuery": ["matterId"],
            "sensitiveDataHidden": True,
        }

    async def _remote_bundle(
        self,
        spec: CapabilitySpec,
        workspace_id: UUID,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        resources: dict[str, Any] = {}
        errors: dict[str, Any] = {}
        operations: dict[str, Any] | None = None
        remote_required = bool(spec.operations_view or spec.remote_reads)
        binding: dict[str, Any] = {
            "required": remote_required,
            "validated": False,
            "organizationId": None,
        }
        if remote_required:
            try:
                organization_id = await self._validated_remote_organization(
                    workspace_id,
                    for_update=True,
                )
            except BottleCRMClientError as error:
                errors["binding"] = self._error_contract(error)
            else:
                binding = {
                    "required": True,
                    "validated": True,
                    "organizationId": organization_id,
                }

        if binding["validated"]:
            try:
                operations = await self._operations_context(spec, workspace_id, query)
            except (BottleCRMClientError, LookupError, ValueError) as error:
                errors["operations"] = self._error_contract(error)

            client = self._client()
            if spec.remote_reads:
                for remote_read in spec.remote_reads:
                    params = dict(remote_read.params)
                    if "limit" in params:
                        params["limit"] = min(
                            max(int(query.get("limit") or params["limit"]), 1),
                            _MAX_ROWS,
                        )
                    try:
                        resources[remote_read.name] = await client.read_payload(
                            remote_read.path,
                            params=params or None,
                        )
                    except BottleCRMClientError as error:
                        errors[remote_read.name] = self._error_contract(error)

        safe_bundle = _storage_safe(
            {
                "binding": binding,
                "operations": operations,
                "resources": resources,
                "errors": errors,
            }
        )
        available = bool(resources or operations) and not (
            errors and not resources and operations is None
        )
        return {
            "available": available,
            "binding": safe_bundle["binding"],
            "resources": safe_bundle["resources"],
            "operations": safe_bundle["operations"],
            "errors": safe_bundle["errors"],
            "digest": _digest(safe_bundle),
        }

    @staticmethod
    def _filtered_state(state: dict[str, Any], query: dict[str, Any]) -> dict[str, Any]:
        records = list(state["records"].values())
        search = str(query.get("search") or "").strip().lower()
        status_filter = str(query.get("status") or "").strip().lower()
        if search:
            records = [
                record
                for record in records
                if search in json.dumps(record, ensure_ascii=False).lower()
            ]
        if status_filter:
            records = [
                record
                for record in records
                if str(record.get("data", {}).get("status") or "").lower() == status_filter
            ]
        limit = min(max(int(query.get("limit") or 50), 1), _MAX_ROWS)
        result = {
            "version": state["version"],
            "total": len(records),
            "items": records[:limit],
            "conflicts": list(state["conflicts"])[-20:],
            "lastRemoteDigest": state.get("lastRemoteDigest"),
            "updatedAt": state.get("updatedAt"),
        }
        if bool(query.get("includeTombstones")):
            result["tombstones"] = list(state["tombstones"].values())[:limit]
        return result

    @staticmethod
    def _action_effect(spec: CapabilitySpec, command: str) -> Effect:
        if command in _COMPUTED_COMMANDS:
            return "computed"
        if command in _SYNC_COMMANDS:
            return "sync"
        if (spec.key, command) in _REMOTE_ACTIONS:
            return "remote"
        return "state"

    def _action_contracts(self, spec: CapabilitySpec) -> list[dict[str, Any]]:
        return [
            {
                "command": command,
                "effect": self._action_effect(spec, command),
                "permission": {
                    "module": _ACTION_PERMISSION_OVERRIDES.get(command, spec.permission)[0],
                    "action": _ACTION_PERMISSION_OVERRIDES.get(command, spec.permission)[1],
                },
                "requiresExpectedVersion": command not in _COMPUTED_COMMANDS,
                "remoteOptIn": (spec.key, command) in _REMOTE_ACTIONS or command in _SYNC_COMMANDS,
                "destructive": command in _TOMBSTONE_COMMANDS or command in _MERGE_COMMANDS,
                "required": (
                    ["recordId", "intoId"]
                    if command in _MERGE_COMMANDS
                    else ["recordId"]
                    if command in _TOMBSTONE_COMMANDS | _RESTORE_COMMANDS
                    else ["provider"]
                    if command == "test_integration"
                    else []
                ),
            }
            for command in spec.actions
        ]

    async def snapshot(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict,
    ) -> dict:
        spec = self._spec(capability_key)
        await self._workspace(workspace_id, for_update=False)
        safe_query = _storage_safe(query if isinstance(query, dict) else {})
        state, _, _ = await self._load_state(
            workspace_id,
            spec.key,
            for_update=False,
        )
        canonical = await self._canonical_counts(workspace_id, spec.canonical_resources)
        remote = await self._remote_bundle(spec, workspace_id, safe_query)
        state["lastRemoteDigest"] = remote["digest"]
        filtered = self._filtered_state(state, safe_query)
        return _output_safe(
            {
                "provider": _PROVIDER,
                "capabilityKey": spec.key,
                "workspaceId": workspace_id,
                "title": spec.title,
                "mode": spec.mode,
                "sourceOfTruth": "klm",
                "collection": spec.collection,
                "view": {
                    "sections": [
                        {"key": section, "label": section.replace("_", " ").title()}
                        for section in spec.sections
                    ],
                    "summary": {
                        "nativeRecords": filtered["total"],
                        "canonicalRecords": sum(canonical.values()),
                        "remoteResources": len(remote["resources"]),
                        "conflicts": len(filtered["conflicts"]),
                    },
                },
                "canonical": {"counts": canonical, "tenantScoped": True},
                "nativeState": filtered,
                "upstream": remote,
                "actions": self._action_contracts(spec),
                "query": safe_query,
                "generatedAt": _utc_now(),
                "sensitiveDataHidden": True,
            }
        )

    @staticmethod
    def _conflict(
        *,
        conflict_type: str,
        message: str,
        expected: Any,
        actual: Any,
        record_id: str | None = None,
        remote_preserved: bool | None = True,
        uncertain: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": str(uuid4()),
            "type": conflict_type,
            "message": message,
            "expected": _storage_safe(expected),
            "actual": _storage_safe(actual),
            "recordId": record_id,
            "detectedAt": _utc_now().isoformat(),
            "remotePreserved": remote_preserved,
            "uncertain": uncertain,
            "resolved": False,
        }

    async def _persist_conflict(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        spec: CapabilitySpec,
        state: dict[str, Any],
        conflict: dict[str, Any],
        workspace: Workspace | None,
        backend: Any,
    ) -> dict[str, Any]:
        state["conflicts"] = [*state["conflicts"][-(_MAX_ROWS - 1) :], conflict]
        try:
            await self._save_state(
                workspace_id=workspace_id,
                capability_key=spec.key,
                state=state,
                expected_version=state["version"],
                actor_user_id=actor_user_id,
                workspace=workspace,
                backend=backend,
            )
        except BottleCRMNativeConflictError as error:
            await self.db.rollback()
            conflict = error.conflict
        await self.db.commit()
        return {
            "provider": _PROVIDER,
            "capabilityKey": spec.key,
            "status": "conflict",
            "version": state["version"],
            "conflict": _output_safe(conflict),
        }

    @staticmethod
    def _record_id(payload: dict[str, Any], *, required: bool) -> str:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        value = payload.get("recordId") or payload.get("id") or data.get("id")
        if value in (None, ""):
            if required:
                raise ValueError("recordId é obrigatório para esta ação.")
            return str(uuid4())
        record_id = str(value).strip()
        if not record_id or len(record_id) > 160:
            raise ValueError("recordId inválido.")
        return record_id

    @staticmethod
    def _mutation_data(payload: dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload.get("data"), dict):
            source = payload["data"]
        else:
            source = {key: value for key, value in payload.items() if key not in _CONTROL_FIELDS}
        return _storage_safe(source)

    @staticmethod
    def _status_for(command: str) -> str | None:
        prefixes = (
            ("approve", "approved"),
            ("reject", "rejected"),
            ("complete", "completed"),
            ("close", "closed"),
            ("disable", "disabled"),
            ("cancel", "cancelled"),
            ("publish", "published"),
            ("mark_read", "read"),
            ("request_changes", "changes_requested"),
            ("schedule", "scheduled"),
            ("select", "selected"),
        )
        return next((status for prefix, status in prefixes if command.startswith(prefix)), None)

    @staticmethod
    def _validated_import_rows(
        payload: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raw_rows = payload.get("rows")
        if raw_rows is None:
            return [], [
                {
                    "row": None,
                    "code": "rows_required",
                    "message": "rows deve ser uma lista de objetos.",
                }
            ]
        if not isinstance(raw_rows, list):
            return [], [
                {
                    "row": None,
                    "code": "invalid_rows",
                    "message": "rows deve ser uma lista de objetos.",
                }
            ]
        if len(raw_rows) > _MAX_ROWS:
            return [], [
                {
                    "row": _MAX_ROWS + 1,
                    "code": "row_limit_exceeded",
                    "message": f"A importação aceita no máximo {_MAX_ROWS} linhas por lote.",
                }
            ]

        normalized: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for index, row in enumerate(raw_rows, start=1):
            if not isinstance(row, dict):
                errors.append(
                    {"row": index, "code": "invalid_row", "message": "Linha deve ser objeto."}
                )
                continue
            if not row:
                errors.append(
                    {"row": index, "code": "empty_row", "message": "Linha não pode ser vazia."}
                )
                continue
            invalid_key = next(
                (
                    key
                    for key in row
                    if not isinstance(key, str) or not key.strip() or len(key) > 120
                ),
                None,
            )
            if invalid_key is not None:
                errors.append(
                    {
                        "row": index,
                        "code": "invalid_field",
                        "message": "Linha contém nome de campo inválido.",
                    }
                )
                continue
            safe_row = _storage_safe(row)
            encoded = json.dumps(
                safe_row,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > 16 * 1024:
                errors.append(
                    {
                        "row": index,
                        "code": "row_too_large",
                        "message": "Linha excede o limite de 16 KiB.",
                    }
                )
                continue
            normalized.append(safe_row)
        return normalized, errors

    def _computed_result(
        self,
        *,
        spec: CapabilitySpec,
        command: str,
        payload: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "preview_import":
            rows, errors = self._validated_import_rows(payload)
            raw_rows = payload.get("rows")
            total = len(raw_rows) if isinstance(raw_rows, list) else 0
            return {
                "preview": {
                    "total": total,
                    "valid": len(rows),
                    "invalid": len(errors),
                    "errors": errors,
                    "commitAllowed": bool(rows) and not errors,
                    "disposition": "validation_only",
                    "imported": False,
                }
            }
        if command == "export":
            return {
                "export": {
                    "format": str(payload.get("format") or "json").lower(),
                    "count": len(state["records"]),
                    "rows": list(state["records"].values())[:_MAX_ROWS],
                    "generatedAt": _utc_now(),
                }
            }
        if command == "render_macro":
            template = str(payload.get("template") or "")[:_MAX_STRING]
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}

            def replace(match: re.Match[str]) -> str:
                value: Any = context
                for part in match.group(1).split("."):
                    value = value.get(part) if isinstance(value, dict) else None
                return str(value)[:500] if value is not None else match.group(0)

            return {"rendered": _TEMPLATE_VARIABLE.sub(replace, template), "unresolvedSafe": True}
        if command == "test_routing_rule":
            conditions = (
                payload.get("conditions") if isinstance(payload.get("conditions"), dict) else {}
            )
            sample = payload.get("sample") if isinstance(payload.get("sample"), dict) else {}
            matches = all(sample.get(key) == value for key, value in conditions.items())
            return {"dryRun": True, "matches": matches, "evaluated": len(conditions)}
        if command == "run_escalation_dry_run":
            threshold = max(0, int(payload.get("thresholdMinutes") or 0))
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            candidates = [
                item
                for item in items[:_MAX_ROWS]
                if isinstance(item, dict) and int(item.get("overdueMinutes") or 0) >= threshold
            ]
            return {"dryRun": True, "thresholdMinutes": threshold, "candidates": candidates}
        if command == "test_integration":
            provider = str(payload.get("provider") or "").strip()
            return {
                "configurationOnly": True,
                "provider": provider,
                "valid": bool(provider),
                "networkCallPerformed": False,
                "externalGate": "approval_required",
            }
        if command == "verify_binding":
            return {
                "verified": True,
                "bindingPolicy": "one_bottlecrm_organization_per_klm_workspace",
                "externalMutation": False,
            }
        return {
            "report": {
                "collection": spec.collection,
                "records": len(state["records"]),
                "tombstones": len(state["tombstones"]),
                "conflicts": len(state["conflicts"]),
                "generatedAt": _utc_now(),
            }
        }

    @staticmethod
    def _remote_path(template: str, payload: dict[str, Any]) -> str:
        values = {
            "id": payload.get("externalId") or payload.get("recordId") or payload.get("id"),
            "into_id": payload.get("intoExternalId") or payload.get("intoId"),
        }
        for name in re.findall(r"{([a-z_]+)}", template):
            value = str(values.get(name) or "").strip()
            if not _REMOTE_ID.fullmatch(value):
                raise ValueError(f"Identificador remoto {name} inválido ou ausente.")
            template = template.replace("{" + name + "}", value)
        return template

    async def _execute_remote(
        self,
        *,
        workspace_id: UUID,
        spec: CapabilitySpec,
        command: str,
        payload: dict[str, Any],
        remote: dict[str, Any],
    ) -> dict[str, Any]:
        organization_id = await self._validated_remote_organization(
            workspace_id,
            for_update=True,
        )
        requested_organizations = {
            str(source[key]).strip()
            for source in (payload, payload.get("remotePayload"))
            if isinstance(source, dict)
            for key in ("organizationId", "organization_id", "orgId", "org_id")
            if source.get(key) is not None and str(source[key]).strip()
        }
        if requested_organizations and requested_organizations != {organization_id}:
            raise BottleCRMNativeWorkspaceBindingError(
                "remote_organization_mismatch",
                "BottleCRM bloqueado: a ação aponta para uma organização estrangeira.",
            )
        if command == "select_organization" and not requested_organizations:
            raise ValueError("select_organization exige a organização vinculada no payload.")

        expected_digest = str(payload.get("expectedRemoteDigest") or "")
        if not expected_digest:
            raise BottleCRMNativeConflictError(
                self._conflict(
                    conflict_type="remote_precondition_required",
                    message="A escrita remota exige o digest obtido no snapshot mais recente.",
                    expected="expectedRemoteDigest",
                    actual=None,
                )
            )
        if expected_digest != remote["digest"]:
            raise BottleCRMNativeConflictError(
                self._conflict(
                    conflict_type="remote_changed",
                    message="BottleCRM mudou desde o snapshot; a edição remota foi preservada.",
                    expected=expected_digest,
                    actual=remote["digest"],
                )
            )
        if not remote["available"]:
            raise BottleCRMNativeConflictError(
                self._conflict(
                    conflict_type="remote_unavailable",
                    message="Não foi possível confirmar o estado remoto antes da escrita.",
                    expected=expected_digest,
                    actual="unavailable",
                )
            )
        if payload.get("confirmRemoteWrite") is not True:
            raise BottleCRMNativeConflictError(
                self._conflict(
                    conflict_type="remote_confirmation_required",
                    message="A escrita remota exige confirmRemoteWrite=true.",
                    expected=True,
                    actual=payload.get("confirmRemoteWrite"),
                )
            )

        if command in _SYNC_COMMANDS:
            return _storage_safe(await self._sync().sync_workspace(workspace_id))

        method, template = _REMOTE_ACTIONS[(spec.key, command)]
        path = self._remote_path(template, payload)
        remote_payload = payload.get("remotePayload")
        if not isinstance(remote_payload, dict):
            remote_payload = self._mutation_data(payload)
        result = await self._client()._request_payload(
            method,
            path,
            json=_storage_safe(remote_payload),
            expected=(200, 201, 202),
        )
        return _storage_safe(result)

    async def _record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        spec: CapabilitySpec,
        command: str,
        record_id: str | None,
        before: Any,
        after: Any,
    ) -> None:
        entity_id: UUID | None = None
        if record_id:
            try:
                entity_id = UUID(record_id)
            except ValueError:
                entity_id = None
        actor_label = await resolve_human_actor_label(self.db, actor_user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type=f"bottlecrm_native.{command}"[:120],
                entity_type=f"bottlecrm.{spec.collection}"[:100],
                entity_id=entity_id,
                summary=f"Ação nativa BottleCRM executada: {spec.title} / {command}.",
                correlation_id=None,
                before_data=_storage_safe(before) if before is not None else None,
                after_data=_storage_safe(after) if after is not None else None,
                occurred_at=_utc_now(),
            )
        )
        await self.db.flush()

    @classmethod
    async def validate_native_configuration(
        cls,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        del actor_user_id
        spec = _SPEC_BY_KEY.get(str(capability_key or "").strip())
        if spec is None:
            raise ValueError(f"Capacidade BottleCRM desconhecida: {capability_key!r}.")
        configuration = payload.get("configuration") if isinstance(payload, dict) else None
        if configuration is not None and not isinstance(configuration, dict):
            raise TypeError("configuration deve ser um objeto.")
        return _output_safe(
            {
                "provider": _PROVIDER,
                "workspaceId": workspace_id,
                "capabilityKey": spec.key,
                "command": "__native_config__",
                "mode": "configure",
                "status": "validated",
                "configurationFieldCount": len(configuration or {}),
                "externalNetworkExecuted": False,
            }
        )

    async def validate(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict,
        *,
        mode: str,
    ) -> dict[str, Any]:
        """Validate a BottleCRM DSL command without mutating local or remote state."""

        spec = self._spec(capability_key)
        normalized_command = str(command or "").strip()
        if mode not in {"execute", "dry-run", "configure"}:
            raise ValueError("Modo de validação BottleCRM inválido.")
        if normalized_command == "__native_config__":
            return await self.validate_native_configuration(
                workspace_id, actor_user_id, capability_key, payload
            )
        if normalized_command not in spec.actions:
            raise ValueError(
                f"Comando {normalized_command or '[vazio]'} não pertence a {spec.key}."
            )
        safe_payload = _storage_safe(payload if isinstance(payload, dict) else {})
        await self._require_command_permission(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            spec=spec,
            command=normalized_command,
        )
        state, _, _ = await self._load_state(workspace_id, spec.key, for_update=False)
        if safe_payload.get("applyRemote") is True:
            raise PermissionError("Validação nativa não permite escrita remota.")
        if normalized_command not in _COMPUTED_COMMANDS:
            if "expectedVersion" not in safe_payload:
                raise ValueError("Toda escrita exige expectedVersion obtido no snapshot.")
            try:
                expected_version = int(safe_payload["expectedVersion"])
            except (TypeError, ValueError) as error:
                raise ValueError("expectedVersion deve ser inteiro.") from error
            if expected_version != state["version"]:
                raise ValueError(
                    f"expectedVersion desatualizado: esperado {state['version']}, "
                    f"recebido {expected_version}."
                )
        if normalized_command in _TOMBSTONE_COMMANDS | _RESTORE_COMMANDS | _MERGE_COMMANDS:
            self._record_id(safe_payload, required=True)
        if normalized_command == "test_integration" and not str(
            safe_payload.get("provider") or ""
        ).strip():
            raise ValueError("provider é obrigatório para testar a integração.")
        if normalized_command == "commit_import":
            rows, errors = self._validated_import_rows(safe_payload)
            if errors or not rows:
                raise ValueError(
                    "Importação rejeitada: todas as linhas devem ser objetos válidos e o lote "
                    "não pode estar vazio."
                )

        return _output_safe(
            {
                "provider": _PROVIDER,
                "workspaceId": workspace_id,
                "capabilityKey": spec.key,
                "command": normalized_command,
                "mode": mode,
                "status": "validated",
                "effect": self._action_effect(spec, normalized_command),
                "stateVersion": state["version"],
                "externalNetworkExecuted": False,
            }
        )

    async def execute(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict,
    ) -> dict:
        spec = self._spec(capability_key)
        command = str(command or "").strip()
        if command not in spec.actions:
            raise ValueError(f"Comando {command or '[vazio]'} não pertence a {spec.key}.")
        safe_payload = _storage_safe(payload if isinstance(payload, dict) else {})
        required_permission = await self._require_command_permission(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            spec=spec,
            command=command,
        )
        staged_rows: list[dict[str, Any]] | None = None
        if command == "commit_import":
            staged_rows, import_errors = self._validated_import_rows(safe_payload)
            if import_errors or not staged_rows:
                raise ValueError(
                    "Importação rejeitada: todas as linhas devem ser objetos válidos e o lote "
                    "não pode estar vazio."
                )
            safe_payload = {
                **safe_payload,
                "data": {
                    "stagingState": "local_staging",
                    "rowCount": len(staged_rows),
                    "rowsDigest": _digest(staged_rows),
                    "rows": staged_rows,
                    "imported": False,
                },
            }
        state, workspace, backend = await self._load_state(
            workspace_id,
            spec.key,
            for_update=True,
        )

        if command in _COMPUTED_COMMANDS:
            result = self._computed_result(
                spec=spec,
                command=command,
                payload=safe_payload,
                state=state,
            )
            await self._record_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                spec=spec,
                command=command,
                record_id=None,
                before=None,
                after=result,
            )
            await self.db.commit()
            return _output_safe(
                {
                    "provider": _PROVIDER,
                    "capabilityKey": spec.key,
                    "command": command,
                    "status": "completed",
                    "version": state["version"],
                    "result": result,
                    "permission": ":".join(required_permission),
                    "externalMutation": False,
                }
            )

        if "expectedVersion" not in safe_payload:
            conflict = self._conflict(
                conflict_type="version_precondition_required",
                message="Toda escrita exige expectedVersion obtido no snapshot.",
                expected=state["version"],
                actual=None,
            )
            return await self._persist_conflict(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                spec=spec,
                state=state,
                conflict=conflict,
                workspace=workspace,
                backend=backend,
            )
        try:
            expected_version = int(safe_payload["expectedVersion"])
        except (TypeError, ValueError) as error:
            raise ValueError("expectedVersion deve ser inteiro.") from error
        if expected_version != state["version"]:
            conflict = self._conflict(
                conflict_type="version_mismatch",
                message="O estado KLM mudou; nenhuma edição foi sobrescrita.",
                expected=expected_version,
                actual=state["version"],
            )
            return await self._persist_conflict(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                spec=spec,
                state=state,
                conflict=conflict,
                workspace=workspace,
                backend=backend,
            )

        if command in _SYNC_COMMANDS or safe_payload.get("applyRemote") is True:
            if command not in _SYNC_COMMANDS and (spec.key, command) not in _REMOTE_ACTIONS:
                conflict = self._conflict(
                    conflict_type="remote_endpoint_not_mapped",
                    message="A ação possui estado KLM, mas não possui escrita remota auditada.",
                    expected="mapped remote action",
                    actual=command,
                )
                return await self._persist_conflict(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    spec=spec,
                    state=state,
                    conflict=conflict,
                    workspace=workspace,
                    backend=backend,
                )
            query = safe_payload.get("query") if isinstance(safe_payload.get("query"), dict) else {}
            remote = await self._remote_bundle(spec, workspace_id, query)
            try:
                remote_payload = {**safe_payload, "workspaceId": str(workspace_id)}
                remote_result = await self._execute_remote(
                    workspace_id=workspace_id,
                    spec=spec,
                    command=command,
                    payload=remote_payload,
                    remote=remote,
                )
            except BottleCRMNativeConflictError as error:
                return await self._persist_conflict(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    spec=spec,
                    state=state,
                    conflict=error.conflict,
                    workspace=workspace,
                    backend=backend,
                )
            except BottleCRMNotFoundError:
                conflict = self._conflict(
                    conflict_type="remote_tombstone",
                    message="O registro remoto foi apagado; o KLM não o recriou silenciosamente.",
                    expected=safe_payload.get("expectedRemoteDigest"),
                    actual="not_found",
                    record_id=str(safe_payload.get("recordId") or "") or None,
                )
                return await self._persist_conflict(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    spec=spec,
                    state=state,
                    conflict=conflict,
                    workspace=workspace,
                    backend=backend,
                )
            except (BottleCRMClientError, LookupError) as error:
                conflict = self._conflict(
                    conflict_type="remote_execution_uncertain",
                    message=(
                        "A execução remota não pôde ser confirmada; reconciliação manual exigida."
                    ),
                    expected=safe_payload.get("expectedRemoteDigest"),
                    actual=self._error_contract(error),
                    record_id=str(safe_payload.get("recordId") or "") or None,
                    remote_preserved=None,
                    uncertain=True,
                )
                return await self._persist_conflict(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    spec=spec,
                    state=state,
                    conflict=conflict,
                    workspace=workspace,
                    backend=backend,
                )
        else:
            remote_result = None

        record_required = command in _TOMBSTONE_COMMANDS | _RESTORE_COMMANDS | _MERGE_COMMANDS
        if command in _SYNC_COMMANDS and not safe_payload.get("recordId"):
            safe_payload["recordId"] = f"sync:{spec.key}"
        record_id = self._record_id(safe_payload, required=record_required)
        existing = state["records"].get(record_id)
        tombstone = state["tombstones"].get(record_id)
        expected_record_version = safe_payload.get("expectedRecordVersion")
        if existing is not None and expected_record_version is not None:
            if int(expected_record_version) != int(existing.get("version") or 0):
                conflict = self._conflict(
                    conflict_type="record_version_mismatch",
                    message="O registro mudou; a versão anterior foi preservada.",
                    expected=expected_record_version,
                    actual=existing.get("version"),
                    record_id=record_id,
                )
                return await self._persist_conflict(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    spec=spec,
                    state=state,
                    conflict=conflict,
                    workspace=workspace,
                    backend=backend,
                )

        now = _utc_now().isoformat()
        before = copy.deepcopy(existing or tombstone)
        if command in _TOMBSTONE_COMMANDS or command in _MERGE_COMMANDS:
            previous = state["records"].pop(record_id, None)
            state["tombstones"][record_id] = {
                "id": record_id,
                "kind": spec.collection,
                "deletedAt": now,
                "deletedBy": str(actor_user_id),
                "reason": str(safe_payload.get("reason") or command)[:500],
                "previousVersion": int((previous or {}).get("version") or 0),
                "previous": previous,
                "mergedInto": safe_payload.get("intoId") if command in _MERGE_COMMANDS else None,
                "remotePreserved": remote_result is None,
            }
            after = state["tombstones"][record_id]
        elif command in _RESTORE_COMMANDS:
            if tombstone is None:
                raise LookupError("Tombstone não encontrado para restauração.")
            restored = tombstone.get("previous")
            restored = dict(restored) if isinstance(restored, dict) else {}
            restored.update(
                {
                    "id": record_id,
                    "kind": spec.collection,
                    "version": int(restored.get("version") or 0) + 1,
                    "updatedAt": now,
                    "updatedBy": str(actor_user_id),
                    "lastCommand": command,
                    "sync": {"state": "local", "remotePreserved": True},
                }
            )
            state["records"][record_id] = restored
            state["tombstones"].pop(record_id, None)
            after = restored
        else:
            data = dict(existing.get("data") or {}) if isinstance(existing, dict) else {}
            data.update(self._mutation_data(safe_payload))
            status = self._status_for(command)
            if status:
                data["status"] = status
            record = {
                "id": record_id,
                "kind": spec.collection,
                "version": int((existing or {}).get("version") or 0) + 1,
                "data": data,
                "createdAt": (existing or {}).get("createdAt") or now,
                "updatedAt": now,
                "updatedBy": str(actor_user_id),
                "lastCommand": command,
                "sync": {
                    "state": (
                        "synced"
                        if remote_result is not None
                        else "local_staging"
                        if command == "commit_import"
                        else "local"
                    ),
                    "remotePreserved": remote_result is None,
                    "remoteDigest": safe_payload.get("expectedRemoteDigest"),
                },
            }
            state["records"][record_id] = record
            state["tombstones"].pop(record_id, None)
            after = record

        state["version"] += 1
        state["updatedAt"] = now
        state["updatedBy"] = str(actor_user_id)
        if safe_payload.get("expectedRemoteDigest"):
            state["lastRemoteDigest"] = safe_payload["expectedRemoteDigest"]
        await self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            spec=spec,
            command=command,
            record_id=record_id,
            before=before,
            after=after,
        )
        try:
            saved = await self._save_state(
                workspace_id=workspace_id,
                capability_key=spec.key,
                state=state,
                expected_version=expected_version,
                actor_user_id=actor_user_id,
                workspace=workspace,
                backend=backend,
            )
        except BottleCRMNativeConflictError as error:
            await self.db.rollback()
            return {
                "provider": _PROVIDER,
                "capabilityKey": spec.key,
                "status": "conflict",
                "version": expected_version,
                "conflict": _output_safe(error.conflict),
            }
        await self.db.commit()
        response = {
            "provider": _PROVIDER,
            "capabilityKey": spec.key,
            "command": command,
            "status": "local_staging" if command == "commit_import" else "completed",
            "version": saved["version"],
            "record": after,
            "remote": remote_result,
            "permission": ":".join(required_permission),
            "sourceOfTruth": "klm",
        }
        if command == "commit_import":
            response.update(
                {
                    "stagedLocally": True,
                    "imported": False,
                }
            )
        return _output_safe(response)


__all__ = [
    "BottleCRMNativeConflictError",
    "BottleCRMNativeService",
    "BottleCRMNativeWorkspaceBindingError",
    "CAPABILITY_KEYS",
]
