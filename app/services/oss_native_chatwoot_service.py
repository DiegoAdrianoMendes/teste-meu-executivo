"""Native KLM read models and commands for the pinned local Chatwoot sidecar.

The adapter deliberately has a smaller trust boundary than Chatwoot's full API:
it can call only the loopback client, never returns credentials, and keeps every
external delivery action behind an explicit hard gate.
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.services.chatwoot_bridge_service import (
    ChatwootAccountBindingError,
    ChatwootBridgeService,
)
from app.services.chatwoot_client import ChatwootClient, ChatwootClientError
from app.services.chatwoot_operations_service import ChatwootOperationsService
from app.services.chatwoot_sync_service import ChatwootSyncService
from app.services.klm_operations_service import KlmOperationsService

CHATWOOT_UPSTREAM_PIN = "v4.15.1@97bb8ec"


class ChatwootNativeError(RuntimeError):
    """Base error for the reviewed native adapter contract."""


class ChatwootNativeCapabilityError(ChatwootNativeError):
    """Raised for an unknown capability or command."""


class ChatwootNativeGateError(ChatwootNativeError):
    """Raised when a command would cross a reviewed execution gate."""


class ChatwootNativeDependencyError(ChatwootNativeError):
    """Raised when a required KLM-native dependency is unavailable."""


@dataclass(frozen=True)
class _Capability:
    label: str
    record_type: str
    loader: str
    mode: str = "sidecar"
    actions: tuple[str, ...] = ("refresh",)
    blocked_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Mutation:
    method: str
    path: str
    fields: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    confirm: bool = False
    expected: tuple[int, ...] = (200, 201, 202)
    root: str | None = None


@dataclass
class _LoadResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


_CAPABILITIES: dict[str, _Capability] = {
    "inboxes-routing": _Capability(
        "Caixas de entrada e roteamento",
        "routingContext",
        "routing",
        actions=("refresh", "update_routing", "sync_workspace", "sync_thread"),
    ),
    "contacts": _Capability(
        "Contatos",
        "contact",
        "contacts",
        actions=(
            "refresh",
            "search_contacts",
            "create_contact",
            "update_contact",
            "delete_contact",
        ),
    ),
    "companies-segments": _Capability(
        "Segmentos de contatos",
        "contactSegment",
        "segments",
        actions=("refresh", "create_segment", "update_segment", "delete_segment"),
    ),
    "conversation-composer": _Capability(
        "Conversas, timeline e composer",
        "conversation",
        "conversation",
        actions=(
            "refresh",
            "create_private_note",
            "send_local_message",
            "update_routing",
            "sync_thread",
        ),
        blocked_actions=("send_external_message",),
    ),
    "attachments-media": _Capability(
        "Anexos e mídia",
        "attachment",
        "attachments",
        actions=("refresh", "dry_run_upload"),
        blocked_actions=("prepare_upload", "upload_external_media"),
    ),
    "private-notes": _Capability(
        "Notas privadas",
        "privateNote",
        "private_notes",
        actions=("refresh", "create_private_note", "delete_private_note"),
    ),
    "labels-status-assignment": _Capability(
        "Rótulos, status, atribuição e equipes",
        "routingContext",
        "labels_routing",
        actions=("refresh", "update_routing"),
    ),
    "canned-responses-macros": _Capability(
        "Respostas prontas e macros",
        "productivityTemplate",
        "canned_macros",
        actions=(
            "refresh",
            "create_canned_response",
            "update_canned_response",
            "delete_canned_response",
            "create_macro",
            "update_macro",
            "delete_macro",
            "preview_macro",
        ),
        blocked_actions=("execute_macro",),
    ),
    "automation-rules": _Capability(
        "Automações de atendimento",
        "automationRule",
        "automation",
        actions=(
            "refresh",
            "create_automation_rule",
            "update_automation_rule",
            "delete_automation_rule",
            "clone_automation_rule",
        ),
        blocked_actions=("activate_automation", "run_automation"),
    ),
    "campaigns": _Capability(
        "Campanhas de live chat",
        "campaign",
        "campaigns",
        mode="hybrid",
        actions=("refresh", "save_config", "dry_run"),
        blocked_actions=("activate", "dispatch", "publish"),
    ),
    "reports-csat": _Capability(
        "Relatórios e CSAT",
        "reportMetric",
        "reports_csat",
        actions=("refresh", "query_reports"),
    ),
    "sla": _Capability(
        "Políticas de SLA KLM",
        "klmSlaPolicy",
        "state",
        mode="klm_native",
        actions=("refresh", "save_policy", "delete_policy", "evaluate"),
    ),
    "custom-attributes-contact-operations": _Capability(
        "Campos, notas e operações de contato",
        "contactOperation",
        "custom_contact",
        actions=(
            "refresh",
            "create_custom_attribute",
            "update_custom_attribute",
            "delete_custom_attribute",
            "set_contact_attributes",
            "add_contact_note",
            "update_contact_note",
            "delete_contact_note",
            "dry_run_import",
            "dry_run_export",
        ),
        blocked_actions=("run_import", "run_export"),
    ),
    "agents-teams-availability": _Capability(
        "Agentes, equipes e disponibilidade",
        "workforceMember",
        "agents",
        actions=(
            "refresh",
            "create_agent",
            "update_agent",
            "delete_agent",
            "create_team",
            "update_team",
            "delete_team",
            "set_availability",
        ),
    ),
    "agent-bots-integrations": _Capability(
        "Bots, integrações e apps de painel",
        "integrationConfiguration",
        "bots_integrations",
        actions=(
            "refresh",
            "create_agent_bot",
            "update_agent_bot",
            "delete_agent_bot",
            "create_dashboard_app",
            "update_dashboard_app",
            "delete_dashboard_app",
            "dry_run_integration",
        ),
        blocked_actions=("execute_bot", "process_integration_event"),
    ),
    "audit-log": _Capability(
        "Auditoria KLM equivalente",
        "klmAuditEvent",
        "state",
        mode="klm_native",
        actions=("refresh", "query_audit", "record_event"),
    ),
    "help-center": _Capability(
        "Central de ajuda",
        "helpCenterContent",
        "help_center",
        actions=(
            "refresh",
            "create_portal",
            "update_portal",
            "delete_portal",
            "create_article",
            "update_article",
            "delete_article",
        ),
    ),
    "api-webhooks-realtime": _Capability(
        "API, webhooks e projeção em tempo real",
        "bridgeEndpoint",
        "api_bridge",
        mode="hybrid",
        actions=(
            "refresh",
            "sync_workspace",
            "sync_thread",
            "dry_run_webhook",
        ),
        blocked_actions=("activate_webhook", "dispatch_webhook"),
    ),
    "external-channels": _Capability(
        "Configuração de canais externos",
        "externalChannelConfig",
        "external_state",
        mode="external_gate",
        actions=("refresh", "save_config", "dry_run", "delete_config"),
        blocked_actions=("activate", "connect", "dispatch", "send"),
    ),
    "conversation-search-custom-views": _Capability(
        "Busca e visões de conversa",
        "conversationView",
        "search_views",
        actions=(
            "refresh",
            "search_conversations",
            "create_custom_view",
            "update_custom_view",
            "delete_custom_view",
        ),
    ),
    "notifications": _Capability(
        "Central de notificações",
        "notification",
        "notifications",
        actions=(
            "refresh",
            "mark_notification_read",
            "mark_notification_unread",
            "read_all_notifications",
            "snooze_notification",
            "delete_notification",
        ),
    ),
    "companies": _Capability(
        "Empresas KLM equivalentes",
        "klmCompany",
        "state",
        mode="klm_native",
        actions=("refresh", "create_company", "update_company", "delete_company"),
    ),
    "canned-responses": _Capability(
        "Respostas prontas",
        "cannedResponse",
        "canned",
        actions=(
            "refresh",
            "create_canned_response",
            "update_canned_response",
            "delete_canned_response",
        ),
    ),
    "teams": _Capability(
        "Equipes de atendimento",
        "team",
        "teams",
        actions=(
            "refresh",
            "create_team",
            "update_team",
            "delete_team",
            "add_team_members",
            "remove_team_members",
        ),
    ),
    "agent-bots": _Capability(
        "Bots de atendimento",
        "agentBot",
        "agent_bots",
        actions=(
            "refresh",
            "create_agent_bot",
            "update_agent_bot",
            "delete_agent_bot",
            "dry_run_bot",
        ),
        blocked_actions=("attach_bot", "execute_bot", "reset_bot_secret"),
    ),
    "inbox-administration": _Capability(
        "Administração de inboxes locais",
        "inbox",
        "inboxes",
        actions=(
            "refresh",
            "create_inbox",
            "update_inbox",
            "delete_inbox",
            "add_inbox_members",
            "remove_inbox_members",
        ),
        blocked_actions=("create_external_inbox", "register_external_channel"),
    ),
    "assignment-policy": _Capability(
        "Políticas de atribuição",
        "assignmentPolicy",
        "assignment",
        actions=(
            "refresh",
            "create_assignment_policy",
            "update_assignment_policy",
            "delete_assignment_policy",
        ),
        blocked_actions=("assign_policy_inbox", "remove_policy_inbox"),
    ),
    "conversation-workflow": _Capability(
        "Workflow de encerramento",
        "conversationWorkflow",
        "workflow",
        mode="klm_native",
        actions=("refresh", "set_required_attributes"),
    ),
    "profile-mfa": _Capability(
        "Perfil e MFA",
        "operatorSecurity",
        "profile",
        actions=(
            "refresh",
            "update_profile",
            "set_availability",
            "begin_mfa",
            "verify_mfa",
            "disable_mfa",
        ),
    ),
    "account-general": _Capability(
        "Configurações gerais da conta",
        "accountSetting",
        "account",
        actions=("refresh", "update_account"),
    ),
    "labels-administration": _Capability(
        "Administração de rótulos",
        "label",
        "labels",
        actions=("refresh", "create_label", "update_label", "delete_label"),
    ),
    "dashboard-apps": _Capability(
        "Apps do painel",
        "dashboardApp",
        "dashboard_apps",
        actions=(
            "refresh",
            "create_dashboard_app",
            "update_dashboard_app",
            "delete_dashboard_app",
            "dry_run_dashboard_app",
        ),
        blocked_actions=("load_external_app",),
    ),
    "csat-report": _Capability(
        "Relatório de satisfação",
        "csatResponse",
        "csat",
        actions=("refresh", "query_csat"),
    ),
    "contact-notes-import-export": _Capability(
        "Notas e movimentação de contatos",
        "contactMovement",
        "contact_ops",
        actions=(
            "refresh",
            "add_contact_note",
            "update_contact_note",
            "delete_contact_note",
            "dry_run_import",
            "dry_run_export",
        ),
        blocked_actions=("run_import", "run_export"),
    ),
    "agent-capacity": _Capability(
        "Capacidade de agentes KLM",
        "klmAgentCapacity",
        "state",
        mode="klm_native",
        actions=("refresh", "save_capacity", "delete_capacity", "evaluate"),
    ),
    "whatsapp-campaigns": _Capability(
        "Configuração de campanhas de WhatsApp",
        "whatsappCampaignConfig",
        "external_state",
        mode="external_gate",
        actions=("refresh", "save_config", "dry_run", "delete_config"),
        blocked_actions=("activate", "dispatch", "send"),
    ),
    "captain-ai": _Capability(
        "Configuração do Captain AI",
        "captainConfiguration",
        "external_state",
        mode="external_gate",
        actions=("refresh", "save_config", "dry_run", "delete_config"),
        blocked_actions=("activate", "execute", "playground", "sync_documents"),
    ),
    "custom-roles-security": _Capability(
        "Papéis e segurança KLM",
        "klmCustomRole",
        "state",
        mode="klm_native",
        actions=("refresh", "save_role", "delete_role", "evaluate_access"),
    ),
    "billing-cloud": _Capability(
        "Billing Cloud não aplicável",
        "applicabilityPolicy",
        "billing",
        mode="not_applicable",
        actions=("refresh",),
    ),
}

CHATWOOT_CAPABILITY_KEYS: tuple[str, ...] = tuple(_CAPABILITIES)


_MUTATIONS: dict[str, _Mutation] = {
    "create_contact": _Mutation(
        "POST",
        "contacts",
        ("name", "email", "phone_number", "identifier", "custom_attributes"),
        ("name",),
    ),
    "update_contact": _Mutation(
        "PATCH",
        "contacts/{contact_id}",
        ("name", "email", "phone_number", "identifier", "custom_attributes"),
    ),
    "delete_contact": _Mutation("DELETE", "contacts/{contact_id}", confirm=True),
    "create_segment": _Mutation(
        "POST",
        "custom_filters",
        ("name", "filter_type", "query"),
        ("name", "query"),
        root="custom_filter",
    ),
    "update_segment": _Mutation(
        "PATCH",
        "custom_filters/{filter_id}",
        ("name", "filter_type", "query"),
        root="custom_filter",
    ),
    "delete_segment": _Mutation("DELETE", "custom_filters/{filter_id}", confirm=True),
    "create_canned_response": _Mutation(
        "POST",
        "canned_responses",
        ("short_code", "content"),
        ("short_code", "content"),
        root="canned_response",
    ),
    "update_canned_response": _Mutation(
        "PATCH",
        "canned_responses/{response_id}",
        ("short_code", "content"),
        root="canned_response",
    ),
    "delete_canned_response": _Mutation("DELETE", "canned_responses/{response_id}", confirm=True),
    "create_macro": _Mutation(
        "POST", "macros", ("name", "actions", "visibility"), ("name", "actions")
    ),
    "update_macro": _Mutation("PATCH", "macros/{macro_id}", ("name", "actions", "visibility")),
    "delete_macro": _Mutation("DELETE", "macros/{macro_id}", confirm=True),
    "create_automation_rule": _Mutation(
        "POST",
        "automation_rules",
        ("name", "description", "event_name", "conditions", "actions", "active"),
        ("name", "event_name", "conditions", "actions"),
    ),
    "update_automation_rule": _Mutation(
        "PATCH",
        "automation_rules/{rule_id}",
        ("name", "description", "event_name", "conditions", "actions", "active"),
    ),
    "delete_automation_rule": _Mutation("DELETE", "automation_rules/{rule_id}", confirm=True),
    "create_custom_attribute": _Mutation(
        "POST",
        "custom_attribute_definitions",
        (
            "attribute_display_name",
            "attribute_display_type",
            "attribute_description",
            "attribute_key",
            "attribute_values",
            "attribute_model",
        ),
        ("attribute_display_name", "attribute_display_type", "attribute_key", "attribute_model"),
        root="custom_attribute_definition",
    ),
    "update_custom_attribute": _Mutation(
        "PATCH",
        "custom_attribute_definitions/{attribute_id}",
        (
            "attribute_display_name",
            "attribute_display_type",
            "attribute_description",
            "attribute_values",
        ),
        root="custom_attribute_definition",
    ),
    "delete_custom_attribute": _Mutation(
        "DELETE", "custom_attribute_definitions/{attribute_id}", confirm=True
    ),
    "create_agent": _Mutation(
        "POST",
        "agents",
        ("name", "email", "role", "availability", "auto_offline"),
        ("name", "email"),
        root="agent",
    ),
    "update_agent": _Mutation(
        "PATCH",
        "agents/{agent_id}",
        ("name", "role", "availability", "auto_offline"),
        root="agent",
    ),
    "delete_agent": _Mutation("DELETE", "agents/{agent_id}", confirm=True),
    "create_team": _Mutation(
        "POST",
        "teams",
        ("name", "description", "allow_auto_assign"),
        ("name",),
        root="team",
    ),
    "update_team": _Mutation(
        "PATCH",
        "teams/{team_id}",
        ("name", "description", "allow_auto_assign"),
        root="team",
    ),
    "delete_team": _Mutation("DELETE", "teams/{team_id}", confirm=True),
    "add_team_members": _Mutation(
        "POST", "teams/{team_id}/team_members", ("user_ids",), ("user_ids",)
    ),
    "remove_team_members": _Mutation(
        "DELETE", "teams/{team_id}/team_members", ("user_ids",), ("user_ids",), True
    ),
    "create_agent_bot": _Mutation("POST", "agent_bots", ("name", "description"), ("name",)),
    "update_agent_bot": _Mutation("PATCH", "agent_bots/{bot_id}", ("name", "description")),
    "delete_agent_bot": _Mutation("DELETE", "agent_bots/{bot_id}", confirm=True),
    "create_dashboard_app": _Mutation(
        "POST",
        "dashboard_apps",
        ("title", "content", "url", "type"),
        ("title", "url"),
        root="dashboard_app",
    ),
    "update_dashboard_app": _Mutation(
        "PATCH",
        "dashboard_apps/{app_id}",
        ("title", "content", "url", "type"),
        root="dashboard_app",
    ),
    "delete_dashboard_app": _Mutation("DELETE", "dashboard_apps/{app_id}", confirm=True),
    "create_portal": _Mutation(
        "POST",
        "portals",
        ("name", "slug", "color", "homepage_link", "config"),
        ("name", "slug"),
        root="portal",
    ),
    "update_portal": _Mutation(
        "PATCH",
        "portals/{portal_id}",
        ("name", "slug", "color", "homepage_link", "config"),
        root="portal",
    ),
    "delete_portal": _Mutation("DELETE", "portals/{portal_id}", confirm=True),
    "create_article": _Mutation(
        "POST",
        "portals/{portal_id}/articles",
        ("title", "content", "description", "status", "category_id", "locale", "author_id"),
        ("title", "content"),
        root="article",
    ),
    "update_article": _Mutation(
        "PATCH",
        "portals/{portal_id}/articles/{article_id}",
        ("title", "content", "description", "status", "category_id", "locale", "author_id"),
        root="article",
    ),
    "delete_article": _Mutation(
        "DELETE", "portals/{portal_id}/articles/{article_id}", confirm=True
    ),
    "create_custom_view": _Mutation(
        "POST",
        "custom_filters",
        ("name", "filter_type", "query"),
        ("name", "query"),
        root="custom_filter",
    ),
    "update_custom_view": _Mutation(
        "PATCH",
        "custom_filters/{filter_id}",
        ("name", "filter_type", "query"),
        root="custom_filter",
    ),
    "delete_custom_view": _Mutation("DELETE", "custom_filters/{filter_id}", confirm=True),
    "create_inbox": _Mutation(
        "POST",
        "inboxes",
        ("name", "channel", "enable_auto_assignment", "working_hours_enabled"),
        ("name", "channel"),
    ),
    "update_inbox": _Mutation(
        "PATCH",
        "inboxes/{inbox_id}",
        ("name", "enable_auto_assignment", "working_hours_enabled", "out_of_office_message"),
    ),
    "delete_inbox": _Mutation("DELETE", "inboxes/{inbox_id}", confirm=True),
    "add_inbox_members": _Mutation(
        "POST", "inbox_members", ("inbox_id", "user_ids"), ("inbox_id", "user_ids")
    ),
    "remove_inbox_members": _Mutation(
        "DELETE", "inbox_members", ("inbox_id", "user_ids"), ("inbox_id", "user_ids"), True
    ),
    "create_assignment_policy": _Mutation(
        "POST",
        "assignment_policies",
        (
            "name",
            "description",
            "assignment_order",
            "conversation_priority",
            "fair_distribution_limit",
            "fair_distribution_window",
            "enabled",
        ),
        ("name",),
        root="assignment_policy",
    ),
    "update_assignment_policy": _Mutation(
        "PATCH",
        "assignment_policies/{policy_id}",
        (
            "name",
            "description",
            "assignment_order",
            "conversation_priority",
            "fair_distribution_limit",
            "fair_distribution_window",
            "enabled",
        ),
        root="assignment_policy",
    ),
    "delete_assignment_policy": _Mutation(
        "DELETE", "assignment_policies/{policy_id}", confirm=True
    ),
    "create_label": _Mutation(
        "POST",
        "labels",
        ("title", "description", "color", "show_on_sidebar"),
        ("title",),
        root="label",
    ),
    "update_label": _Mutation(
        "PATCH",
        "labels/{label_id}",
        ("title", "description", "color", "show_on_sidebar"),
        root="label",
    ),
    "delete_label": _Mutation("DELETE", "labels/{label_id}", confirm=True),
    "add_contact_note": _Mutation(
        "POST",
        "contacts/{contact_id}/notes",
        ("content",),
        ("content",),
        root="note",
    ),
    "update_contact_note": _Mutation(
        "PATCH",
        "contacts/{contact_id}/notes/{note_id}",
        ("content",),
        ("content",),
        root="note",
    ),
    "delete_contact_note": _Mutation(
        "DELETE", "contacts/{contact_id}/notes/{note_id}", confirm=True
    ),
}

_STATE_COMMANDS = {
    "save_config",
    "delete_config",
    "dry_run",
    "save_policy",
    "delete_policy",
    "evaluate",
    "query_audit",
    "record_event",
    "create_company",
    "update_company",
    "delete_company",
    "save_capacity",
    "delete_capacity",
    "save_role",
    "delete_role",
    "evaluate_access",
    "set_required_attributes",
}

_STATE_CONFIG_COMMANDS = {
    "save_config",
    "delete_config",
    "save_policy",
    "delete_policy",
    "create_company",
    "update_company",
    "delete_company",
    "save_capacity",
    "delete_capacity",
    "save_role",
    "delete_role",
    "set_required_attributes",
}

_READ_COMMANDS = {
    "preview_macro",
    "query_audit",
    "search_contacts",
    "search_conversations",
    "query_reports",
    "query_csat",
}

_UPSTREAM_GAP_COMMANDS = {
    "assign_policy_inbox",
    "prepare_upload",
    "remove_policy_inbox",
    "run_export",
    "run_import",
}

_SPECIAL_ACTION_REQUIRED: dict[str, tuple[str, ...]] = {
    "clone_automation_rule": ("rule_id",),
    "create_private_note": ("conversation_id", "content"),
    "delete_private_note": ("conversation_id", "message_id"),
    "disable_mfa": ("password", "otp_code|backup_code"),
    "dry_run_upload": ("filename", "byte_size", "content_type", "checksum"),
    "mark_notification_read": ("notification_id",),
    "mark_notification_unread": ("notification_id",),
    "save_capacity": ("capacity",),
    "save_policy": ("policy",),
    "save_role": ("role",),
    "send_local_message": ("conversation_id", "content"),
    "set_availability": ("availability",),
    "set_contact_attributes": ("contact_id", "custom_attributes"),
    "set_required_attributes": ("attribute_keys",),
    "snooze_notification": ("notification_id", "snoozed_until"),
    "sync_thread": ("thread_id",),
    "update_routing": ("thread_id", "changes"),
    "verify_mfa": ("otp_code",),
}

_TRANSIENT_ACTION_FIELDS: dict[str, frozenset[str]] = {
    "disable_mfa": frozenset(
        {"password", "otpCode", "otp_code", "backupCode", "backup_code"}
    ),
    "verify_mfa": frozenset({"otpCode", "otp_code"}),
}

_EXTERNAL_WORDS = {
    "dispatch",
    "send",
    "deliver",
    "publish",
    "activate",
    "connect",
    "execute",
    "playground",
    "webhook",
    "whatsapp",
    "email",
    "sms",
}

_SECRET_WORDS = {
    "accesstoken",
    "apitoken",
    "apikey",
    "authorization",
    "backupcode",
    "backupcodes",
    "cookie",
    "credential",
    "directupload",
    "onetimecode",
    "otpcode",
    "otpsecret",
    "otpuri",
    "password",
    "privatekey",
    "provisioninguri",
    "qrcode",
    "refreshtoken",
    "secret",
    "signedid",
    "token",
    "verificationcode",
}

_QUERY_FIELDS = {
    "assignee_type",
    "from",
    "inbox_id",
    "page",
    "per_page",
    "q",
    "rating",
    "since",
    "sort",
    "status",
    "team_id",
    "to",
    "type",
    "until",
}


def _native_state_dispatch(context: Any) -> dict[str, Any]:
    """Execute only deterministic KLM-local evaluations from the state service."""

    if getattr(context, "external_network_allowed", True):
        raise ChatwootNativeGateError("O dispatcher nativo exige rede externa bloqueada.")
    command = str(getattr(context, "command_type", ""))
    if command not in {"dry_run", "evaluate", "evaluate_access", "record_event"}:
        raise ChatwootNativeGateError(
            f"O comando {command!r} não pertence ao dispatcher KLM local."
        )
    payload = getattr(context, "payload", {})
    configuration = getattr(context, "configuration", {})
    candidates = 0
    for source in (payload, configuration):
        if not isinstance(source, Mapping):
            continue
        for key in ("records", "rows", "items", "policies", "companies", "roles"):
            value = source.get(key)
            if isinstance(value, list):
                candidates = max(candidates, len(value))
    return {
        "status": "dry_run" if command == "dry_run" else "evaluated",
        "commandType": command,
        "validated": True,
        "candidateCount": candidates,
        "configVersion": int(getattr(context, "config_version", 0)),
        "dispatcherInvoked": True,
        "externalNetworkExecuted": False,
        "externalActionsExecuted": 0,
    }


def _compact_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_secret_key(key: str) -> bool:
    compact = _compact_key(key)
    if compact.endswith("ref"):
        return False
    fragments = {
        "accesskey",
        "accesstoken",
        "apikey",
        "clientsecret",
        "privatekey",
        "refreshtoken",
        "secretkey",
        "sessiontoken",
        "signingkey",
        "webhooksecret",
        "webhooktoken",
    }
    return compact in _SECRET_WORDS or any(fragment in compact for fragment in fragments)


def _sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() in {"otpauth", "data"}:
        return "[redacted]"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return value
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    segments = []
    for segment in parsed.path.split("/"):
        if len(segment) > 48 or re.fullmatch(r"[A-Za-z0-9_-]{36,}", segment or ""):
            segments.append("redacted")
        else:
            segments.append(segment)
    return urlunsplit((parsed.scheme, host, "/".join(segments), "", ""))


def _sanitize(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if depth > 10:
        return "[truncated]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, UUID | datetime | date | Enum):
        raw = value.value if isinstance(value, Enum) else value
        return str(raw)
    if isinstance(value, str):
        if re.search(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+", value):
            return "[redacted]"
        clean = _sanitize_url(value) if value.startswith(("http://", "https://")) else value
        return clean[:4000]
    if isinstance(value, Mapping):
        clean_mapping: dict[str, Any] = {}
        for raw_key, item in value.items():
            item_key = str(raw_key)
            if _is_secret_key(item_key):
                continue
            clean_mapping[item_key] = _sanitize(item, key=item_key, depth=depth + 1)
        return clean_mapping
    if isinstance(value, list | tuple | set):
        return [_sanitize(item, key=key, depth=depth + 1) for item in list(value)[:500]]
    if hasattr(value, "model_dump"):
        return _sanitize(value.model_dump(mode="json", by_alias=True), key=key, depth=depth + 1)
    return str(value)[:1000]


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


def _payload_value(payload: Mapping[str, Any], key: str) -> Any:
    if key in payload:
        return payload[key]
    return payload.get(_snake_to_camel(key))


def _identifier_value(payload: Mapping[str, Any], key: str) -> Any:
    value = _payload_value(payload, key)
    if value is None and key == "portal_id":
        return _payload_value(payload, "portal_slug")
    return value


def _as_uuid(value: UUID | str, name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise ChatwootNativeCapabilityError(f"{name} inválido.") from error


def _positive_id(value: Any, name: str) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ChatwootNativeCapabilityError(f"{name} é obrigatório e deve ser numérico.") from error
    if parsed <= 0:
        raise ChatwootNativeCapabilityError(f"{name} deve ser positivo.")
    return str(parsed)


def _route_identifier(value: Any, name: str) -> str:
    if name != "portal_id":
        return _positive_id(value, name)
    identifier = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identifier):
        raise ChatwootNativeCapabilityError("portal_id deve ser o slug alfanumérico do portal.")
    return identifier


class ChatwootNativeService:
    """Expose the 39 reviewed Chatwoot capabilities as native KLM contracts."""

    def __init__(self, db):
        self.db = db
        self.bridge = ChatwootBridgeService(db)
        self.client: ChatwootClient | None = None
        self.operations: ChatwootOperationsService | None = None
        self.sync_service: ChatwootSyncService | None = None
        self.state_store: Any | None = None

    async def snapshot(
        self,
        workspace_id: UUID | str,
        capability_key: str,
        query: dict,
    ) -> dict:
        workspace_uuid = _as_uuid(workspace_id, "workspace_id")
        spec = self._spec(capability_key)
        safe_query = dict(query or {})
        try:
            if spec.mode in {"sidecar", "hybrid"}:
                await self._require_workspace_account_binding(workspace_uuid)
            result = await self._load(spec, workspace_uuid, capability_key, safe_query)
        except ChatwootClientError as error:
            result = _LoadResult(
                records=[
                    {
                        "recordType": spec.record_type,
                        "state": "unavailable",
                        "reasonCode": "chatwoot_local_unavailable",
                        "errorType": type(error).__name__,
                    }
                ],
                issues=["chatwoot_local_unavailable"],
            )
        except (LookupError, ValueError) as error:
            result = _LoadResult(
                records=[
                    {
                        "recordType": spec.record_type,
                        "state": "invalid_context",
                        "reasonCode": "invalid_snapshot_context",
                        "errorType": type(error).__name__,
                    }
                ],
                issues=["invalid_snapshot_context"],
            )

        records = [self._typed_record(spec.record_type, row) for row in result.records]
        if not records:
            records = [
                {
                    "recordType": spec.record_type,
                    "state": "empty",
                    "reasonCode": "no_local_records",
                }
            ]

        health_status = "healthy"
        if spec.mode == "external_gate":
            health_status = "gated"
        elif spec.mode == "not_applicable":
            health_status = "not_applicable"
        elif result.issues:
            health_status = "degraded"

        authority = "chatwoot_sidecar"
        if spec.mode in {"klm_native", "external_gate", "not_applicable"}:
            authority = "klm"
        elif spec.mode == "hybrid":
            authority = "klm_and_chatwoot_sidecar"

        response = {
            "sections": [
                {
                    "id": capability_key,
                    "title": spec.label,
                    "kind": spec.record_type,
                    "recordCount": len(records),
                }
            ],
            "widgets": [
                {
                    "id": f"{capability_key}:summary",
                    "type": "summary",
                    "label": spec.label,
                    "value": len(records),
                },
                {
                    "id": f"{capability_key}:execution",
                    "type": "policy",
                    "label": "Despacho externo",
                    "value": "bloqueado",
                },
            ],
            "records": records,
            "actions": self._actions(spec),
            "source": {
                "provider": "chatwoot",
                "authority": authority,
                "transport": "loopback_only",
                "upstreamPin": CHATWOOT_UPSTREAM_PIN,
                "capabilityKey": capability_key,
                "workspaceId": str(workspace_uuid),
            },
            "health": {
                "status": health_status,
                "issues": sorted(set(result.issues)),
                "externalDispatchAllowed": False,
                "secretsExposed": False,
            },
        }
        return _sanitize(response)

    @classmethod
    async def validate_native_configuration(
        cls,
        workspace_id: UUID | str,
        actor_user_id: UUID | str,
        capability_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        workspace_uuid = _as_uuid(workspace_id, "workspace_id")
        _as_uuid(actor_user_id, "actor_user_id")
        if capability_key not in _CAPABILITIES:
            raise ChatwootNativeCapabilityError(
                f"Capacidade Chatwoot desconhecida: {capability_key!r}."
            )
        configuration = payload.get("configuration") if isinstance(payload, dict) else None
        if configuration is not None and not isinstance(configuration, dict):
            raise TypeError("configuration deve ser um objeto.")
        cls._reject_secret_input(configuration or {})
        return _sanitize(
            {
                "provider": "chatwoot",
                "workspaceId": workspace_uuid,
                "capabilityKey": capability_key,
                "command": "__native_config__",
                "mode": "configure",
                "status": "validated",
                "configurationFieldCount": len(configuration or {}),
                "externalNetworkExecuted": False,
            }
        )

    async def validate(
        self,
        workspace_id: UUID | str,
        actor_user_id: UUID | str,
        capability_key: str,
        command: str,
        payload: dict,
        *,
        mode: str,
    ) -> dict[str, Any]:
        """Validate one live Chatwoot action contract without sidecar mutation."""

        workspace_uuid = _as_uuid(workspace_id, "workspace_id")
        _as_uuid(actor_user_id, "actor_user_id")
        spec = self._spec(capability_key)
        normalized_command = str(command or "").strip()
        if mode not in {"execute", "dry-run", "configure"}:
            raise ValueError("Modo de validação Chatwoot inválido.")
        if normalized_command == "__native_config__":
            return await self.validate_native_configuration(
                workspace_uuid, actor_user_id, capability_key, dict(payload or {})
            )
        if normalized_command not in {*spec.actions, *spec.blocked_actions}:
            raise ChatwootNativeCapabilityError(
                f"Comando {normalized_command!r} não pertence à capacidade {capability_key!r}."
            )
        clean_payload = dict(payload or {})
        transient_keys = _TRANSIENT_ACTION_FIELDS.get(normalized_command, frozenset())
        if transient_keys:
            self._reject_secret_input(
                {key: value for key, value in clean_payload.items() if key not in transient_keys}
            )
        else:
            self._reject_secret_input(clean_payload)

        missing: list[str] = []
        for required in self._action_required(normalized_command):
            alternatives = [name for name in required.split("|") if name]
            if not any(
                (value := _payload_value(clean_payload, name)) is not None and value != ""
                for name in alternatives
            ):
                missing.append(" ou ".join(alternatives))
        if missing:
            raise ChatwootNativeCapabilityError(
                "Campos obrigatórios ausentes: " + ", ".join(missing) + "."
            )
        if normalized_command == "dry_run_upload":
            self._dry_run_response(capability_key, normalized_command, clean_payload)

        return _sanitize(
            {
                "provider": "chatwoot",
                "workspaceId": workspace_uuid,
                "capabilityKey": capability_key,
                "command": normalized_command,
                "mode": mode,
                "status": "validated",
                "requiredFields": self._action_required(normalized_command),
                "externalNetworkExecuted": False,
            }
        )

    async def execute(
        self,
        workspace_id: UUID | str,
        actor_user_id: UUID | str,
        capability_key: str,
        command: str,
        payload: dict,
    ) -> dict:
        workspace_uuid = _as_uuid(workspace_id, "workspace_id")
        actor_uuid = _as_uuid(actor_user_id, "actor_user_id")
        spec = self._spec(capability_key)
        command = str(command or "").strip()
        clean_payload = dict(payload or {})
        transient_keys = _TRANSIENT_ACTION_FIELDS.get(command, frozenset())
        if transient_keys:
            self._reject_secret_input(
                {key: value for key, value in clean_payload.items() if key not in transient_keys}
            )
        else:
            self._reject_secret_input(clean_payload)

        if command == "refresh":
            return await self.snapshot(workspace_uuid, capability_key, clean_payload)
        if command in spec.blocked_actions:
            if command in _UPSTREAM_GAP_COMMANDS:
                raise ChatwootNativeGateError(
                    f"{command} não possui contrato executável no cliente e pin locais."
                )
            raise ChatwootNativeGateError(
                f"{command} permanece bloqueado pelo gate de efeitos externos."
            )
        if command not in spec.actions:
            raise ChatwootNativeCapabilityError(
                f"Comando {command!r} não pertence à capacidade {capability_key!r}."
            )

        if spec.mode in {"klm_native", "external_gate"} or (
            capability_key == "campaigns" and command in _STATE_COMMANDS
        ):
            if command.startswith("delete"):
                self._require_confirmation(clean_payload)
            if command == "query_audit":
                return await self.snapshot(workspace_uuid, capability_key, clean_payload)
            raw = await self._state_execute(
                workspace_uuid,
                actor_uuid,
                capability_key,
                command,
                clean_payload,
            )
            return self._command_response(capability_key, command, raw, "klm_native")

        if command in _READ_COMMANDS:
            return await self.snapshot(workspace_uuid, capability_key, clean_payload)
        if command in {
            "preview_macro",
            "dry_run_integration",
            "dry_run_bot",
            "dry_run_dashboard_app",
            "dry_run_webhook",
            "dry_run_import",
            "dry_run_export",
            "dry_run_upload",
        }:
            return self._dry_run_response(capability_key, command, clean_payload)

        await self._require_workspace_account_binding(workspace_uuid)

        if command in {"sync_workspace", "sync_thread"}:
            return await self._execute_sync(
                workspace_uuid, actor_uuid, capability_key, command, clean_payload
            )
        if command == "update_routing":
            return await self._execute_routing(
                workspace_uuid, actor_uuid, capability_key, clean_payload
            )
        if command == "create_private_note":
            return await self._execute_private_note(
                workspace_uuid, actor_uuid, capability_key, clean_payload
            )
        if command == "delete_private_note":
            return await self._execute_delete_private_note(
                workspace_uuid, actor_uuid, capability_key, clean_payload
            )
        if command == "send_local_message":
            return await self._execute_local_message(
                workspace_uuid, actor_uuid, capability_key, clean_payload
            )
        if command == "set_contact_attributes":
            return await self._execute_contact_attributes(
                workspace_uuid, actor_uuid, capability_key, clean_payload
            )
        if command == "clone_automation_rule":
            return await self._execute_clone_rule(
                workspace_uuid, actor_uuid, capability_key, clean_payload
            )
        if command in {
            "mark_notification_read",
            "mark_notification_unread",
            "read_all_notifications",
            "snooze_notification",
            "delete_notification",
        }:
            return await self._execute_notification(
                workspace_uuid, actor_uuid, capability_key, command, clean_payload
            )
        if command in {
            "update_profile",
            "set_availability",
            "begin_mfa",
            "verify_mfa",
            "disable_mfa",
        }:
            return await self._execute_profile(
                workspace_uuid, actor_uuid, capability_key, command, clean_payload
            )
        if command == "update_account":
            return await self._execute_account(
                workspace_uuid, actor_uuid, capability_key, clean_payload
            )
        mutation = _MUTATIONS.get(command)
        if mutation is not None:
            return await self._execute_mutation(
                workspace_uuid,
                actor_uuid,
                capability_key,
                command,
                mutation,
                clean_payload,
            )
        raise ChatwootNativeCapabilityError(f"Comando {command!r} sem executor nativo.")

    def _spec(self, capability_key: str) -> _Capability:
        try:
            return _CAPABILITIES[capability_key]
        except KeyError as error:
            raise ChatwootNativeCapabilityError(
                f"Capacidade Chatwoot desconhecida: {capability_key!r}."
            ) from error

    def _get_client(self) -> ChatwootClient:
        if self.client is None:
            self.client = ChatwootClient()
        return self.client

    def _get_operations(self) -> ChatwootOperationsService:
        if self.operations is None:
            self.operations = ChatwootOperationsService(self.db, client=self._get_client())
        return self.operations

    def _get_sync(self) -> ChatwootSyncService:
        if self.sync_service is None:
            service = ChatwootSyncService(self.db)
            service.client = self._get_client()
            service.account_id = str(service.client.account_id)
            self.sync_service = service
        return self.sync_service

    async def _require_workspace_account_binding(self, workspace_id: UUID) -> str:
        bindings = list(
            (
                await self.db.execute(
                    select(ExternalEntityLink)
                    .where(
                        ExternalEntityLink.workspace_id == workspace_id,
                        ExternalEntityLink.provider == OSSBridgeProvider.chatwoot,
                        ExternalEntityLink.klm_entity_type == "workspace",
                        ExternalEntityLink.klm_entity_id == workspace_id,
                        ExternalEntityLink.external_entity_type == "account",
                        ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
                    )
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if not bindings:
            raise ChatwootAccountBindingError(
                "Workspace sem conta Chatwoot vinculada e sincronizada."
            )
        if len(bindings) != 1:
            raise ChatwootAccountBindingError(
                "Workspace possui vínculos conflitantes com contas Chatwoot."
            )

        binding = bindings[0]
        bound_account_id = str(binding.external_account_id or "").strip()
        external_entity_id = str(binding.external_entity_id or "").strip()
        try:
            normalized_bound_id = _positive_id(bound_account_id, "account_id")
            normalized_entity_id = _positive_id(external_entity_id, "external_entity_id")
        except ChatwootNativeCapabilityError as error:
            raise ChatwootAccountBindingError(
                "Vínculo de conta Chatwoot possui identificador inválido."
            ) from error
        if normalized_bound_id != normalized_entity_id:
            raise ChatwootAccountBindingError(
                "Vínculo de conta Chatwoot possui identificadores divergentes."
            )

        await self.bridge._require_account_binding(workspace_id, bound_account_id)

        try:
            configured_account_id = _positive_id(
                self._get_client().account_id,
                "account_id",
            )
        except ChatwootNativeCapabilityError as error:
            raise ChatwootAccountBindingError(
                "Conta configurada no sidecar Chatwoot é inválida."
            ) from error
        if configured_account_id != normalized_bound_id:
            raise ChatwootAccountBindingError(
                "Conta configurada no sidecar Chatwoot pertence a outro workspace."
            )
        return normalized_bound_id

    def _account_path(self, resource: str, *, version: str = "v1") -> str:
        account_id = _positive_id(self._get_client().account_id, "account_id")
        clean_resource = resource.lstrip("/")
        return f"/api/{version}/accounts/{account_id}/{clean_resource}"

    async def _request_sidecar(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        allow_empty: bool = False,
    ) -> dict[str, Any] | list[Any]:
        try:
            return await self._get_client()._request_json(
                method, path, json=json, expected=expected
            )
        except ChatwootClientError as error:
            if allow_empty and str(error) == "Chatwoot retornou JSON inválido.":
                return {"acknowledged": True, "emptyResponse": True}
            raise

    async def _load(
        self,
        spec: _Capability,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
    ) -> _LoadResult:
        loader = getattr(self, f"_load_{spec.loader}")
        return await loader(workspace_id, capability_key, query, spec)

    async def _fetch_typed(
        self,
        record_type: str,
        resource: str,
        *,
        params: dict[str, Any] | None = None,
        version: str = "v1",
    ) -> list[dict[str, Any]]:
        payload = await self._get_client()._request_json(
            "GET", self._account_path(resource, version=version), params=params
        )
        return [self._typed_record(record_type, row) for row in self._extract_rows(payload)]

    async def _fetch_absolute(
        self,
        record_type: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = await self._get_client()._request_json("GET", path, params=params)
        return [self._typed_record(record_type, row) for row in self._extract_rows(payload)]

    async def _fetch_many(
        self,
        definitions: list[tuple[str, str, dict[str, Any] | None, str]],
    ) -> _LoadResult:
        result = _LoadResult()
        for record_type, resource, params, version in definitions:
            try:
                result.records.extend(
                    await self._fetch_typed(record_type, resource, params=params, version=version)
                )
            except ChatwootClientError:
                result.issues.append(f"{record_type}_unavailable")
        return result

    @staticmethod
    def _extract_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        for key in ("payload", "data", "results", "items"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
            if isinstance(nested, dict):
                rows = ChatwootNativeService._extract_rows(nested)
                if rows:
                    return rows
        return [payload]

    @staticmethod
    def _typed_record(record_type: str, row: Mapping[str, Any]) -> dict[str, Any]:
        clean = dict(_sanitize(dict(row)))
        clean.setdefault("recordType", record_type)
        return clean

    @staticmethod
    def _query_params(query: Mapping[str, Any], **defaults: Any) -> dict[str, Any]:
        params = {key: value for key, value in defaults.items() if value is not None}
        for key in _QUERY_FIELDS:
            value = _payload_value(query, key)
            if value is not None and isinstance(value, str | int | float | bool | date | datetime):
                params[key] = str(value) if isinstance(value, date | datetime) else value
        return params

    async def _load_routing(self, workspace_id, _key, query, _spec) -> _LoadResult:
        thread_id = _payload_value(query, "thread_id")
        if thread_id:
            context = await self._get_operations().context(
                workspace_id, _as_uuid(thread_id, "thread_id")
            )
            return _LoadResult([self._typed_record("routingContext", context)])
        return await self._fetch_many(
            [
                ("inbox", "inboxes", None, "v1"),
                ("team", "teams", None, "v1"),
                ("label", "labels", None, "v1"),
                ("agent", "agents", None, "v1"),
            ]
        )

    async def _load_contacts(self, _workspace, _key, query, _spec) -> _LoadResult:
        q = _payload_value(query, "q")
        resource = "contacts/search" if q else "contacts"
        params = self._query_params(query)
        return _LoadResult(await self._fetch_typed("contact", resource, params=params))

    async def _load_segments(self, _workspace, _key, query, _spec) -> _LoadResult:
        params = self._query_params(query, filter_type="contact")
        return _LoadResult(
            await self._fetch_typed("contactSegment", "custom_filters", params=params)
        )

    async def _resolve_conversation_id(
        self, workspace_id: UUID, query: Mapping[str, Any]
    ) -> str | None:
        conversation_id = _payload_value(query, "conversation_id")
        if conversation_id is not None:
            return _positive_id(conversation_id, "conversation_id")
        thread_id = _payload_value(query, "thread_id")
        if thread_id is not None:
            return await self._get_operations().conversation_id(
                workspace_id, _as_uuid(thread_id, "thread_id")
            )
        return None

    async def _load_conversation(self, workspace_id, _key, query, _spec) -> _LoadResult:
        conversation_id = await self._resolve_conversation_id(workspace_id, query)
        if conversation_id is None:
            params = self._query_params(query)
            return _LoadResult(
                await self._fetch_typed("conversation", "conversations", params=params)
            )
        conversation = await self._get_client().conversation(conversation_id)
        messages = await self._get_client().list_messages(conversation_id)
        records = [self._typed_record("conversation", conversation)]
        records.extend(self._typed_record("conversationMessage", row) for row in messages)
        return _LoadResult(records)

    async def _load_attachments(self, workspace_id, _key, query, _spec) -> _LoadResult:
        conversation_id = await self._resolve_conversation_id(workspace_id, query)
        contact_id = _payload_value(query, "contact_id")
        if conversation_id is not None:
            identifier = _positive_id(conversation_id, "conversation_id")
            resource = f"conversations/{identifier}/attachments"
        elif contact_id is not None:
            resource = f"contacts/{_positive_id(contact_id, 'contact_id')}/attachments"
        else:
            return _LoadResult(
                [
                    {
                        "recordType": "attachmentQuery",
                        "state": "context_required",
                        "requiredAnyOf": ["conversationId", "contactId"],
                        "uploadContract": "not_available_in_chatwoot_client",
                    }
                ]
            )
        return _LoadResult(await self._fetch_typed("attachment", resource))

    async def _load_private_notes(self, workspace_id, _key, query, _spec) -> _LoadResult:
        conversation_id = await self._resolve_conversation_id(workspace_id, query)
        if conversation_id is None:
            return _LoadResult(
                [
                    {
                        "recordType": "privateNoteQuery",
                        "state": "context_required",
                        "required": "conversationIdOrThreadId",
                    }
                ]
            )
        messages = await self._get_client().list_messages(conversation_id)
        notes = [row for row in messages if row.get("private") is True]
        return _LoadResult([self._typed_record("privateNote", row) for row in notes])

    async def _load_labels_routing(self, workspace_id, key, query, spec) -> _LoadResult:
        return await self._load_routing(workspace_id, key, query, spec)

    async def _load_canned_macros(self, _workspace, _key, _query, _spec) -> _LoadResult:
        return await self._fetch_many(
            [
                ("cannedResponse", "canned_responses", None, "v1"),
                ("macro", "macros", None, "v1"),
            ]
        )

    async def _load_automation(self, _workspace, _key, query, _spec) -> _LoadResult:
        return _LoadResult(
            await self._fetch_typed(
                "automationRule", "automation_rules", params=self._query_params(query)
            )
        )

    async def _load_campaigns(self, workspace_id, key, query, spec) -> _LoadResult:
        result = _LoadResult()
        try:
            result.records.extend(await self._fetch_typed("liveChatCampaign", "campaigns"))
        except ChatwootClientError:
            result.issues.append("live_chat_campaigns_unavailable")
        state = await self._state_snapshot_records(workspace_id, key, query, spec.record_type)
        result.records.extend(state.records)
        result.issues.extend(state.issues)
        result.records.append(
            {
                "recordType": "campaignExecutionPolicy",
                "externalDispatchAllowed": False,
                "configurationAuthority": "klm",
            }
        )
        return result

    async def _load_reports_csat(self, _workspace, _key, query, _spec) -> _LoadResult:
        params = self._query_params(query)
        return await self._fetch_many(
            [
                ("reportSummary", "reports/summary", params, "v2"),
                ("csatMetric", "csat_survey_responses/metrics", params, "v1"),
                ("csatResponse", "csat_survey_responses", params, "v1"),
            ]
        )

    async def _load_state(self, workspace_id, key, query, spec) -> _LoadResult:
        return await self._state_snapshot_records(workspace_id, key, query, spec.record_type)

    async def _load_custom_contact(self, _workspace, _key, query, _spec) -> _LoadResult:
        definitions: list[tuple[str, str, dict[str, Any] | None, str]] = [
            (
                "contactAttributeDefinition",
                "custom_attribute_definitions",
                {"attribute_model": "contact_attribute"},
                "v1",
            ),
            (
                "conversationAttributeDefinition",
                "custom_attribute_definitions",
                {"attribute_model": "conversation_attribute"},
                "v1",
            ),
        ]
        contact_id = _payload_value(query, "contact_id")
        if contact_id is not None:
            contact = _positive_id(contact_id, "contact_id")
            definitions.extend(
                [
                    ("contactNote", f"contacts/{contact}/notes", None, "v1"),
                    ("contactAttachment", f"contacts/{contact}/attachments", None, "v1"),
                ]
            )
        result = await self._fetch_many(definitions)
        result.records.append(
            {
                "recordType": "contactTransferPolicy",
                "importMode": "dry_run_only",
                "exportMode": "dry_run_only",
            }
        )
        return result

    async def _load_agents(self, _workspace, _key, _query, _spec) -> _LoadResult:
        result = await self._fetch_many(
            [
                ("agent", "agents", None, "v1"),
                ("team", "teams", None, "v1"),
            ]
        )
        try:
            result.records.extend(await self._fetch_absolute("operatorProfile", "/api/v1/profile"))
        except ChatwootClientError:
            result.issues.append("operator_profile_unavailable")
        return result

    async def _load_bots_integrations(self, _workspace, _key, _query, _spec) -> _LoadResult:
        return await self._fetch_many(
            [
                ("agentBot", "agent_bots", None, "v1"),
                ("integrationApp", "integrations/apps", None, "v1"),
                ("dashboardApp", "dashboard_apps", None, "v1"),
            ]
        )

    async def _load_help_center(self, _workspace, _key, query, _spec) -> _LoadResult:
        result = _LoadResult(await self._fetch_typed("helpCenterPortal", "portals"))
        portal_id = _identifier_value(query, "portal_id")
        if portal_id is not None:
            result.records.extend(
                await self._fetch_typed(
                    "helpCenterArticle",
                    f"portals/{_route_identifier(portal_id, 'portal_id')}/articles",
                    params=self._query_params(query),
                )
            )
        return result

    async def _load_api_bridge(self, workspace_id, _key, query, _spec) -> _LoadResult:
        result = await self._fetch_many(
            [
                ("webhookConfiguration", "webhooks", None, "v1"),
                ("integrationApp", "integrations/apps", None, "v1"),
            ]
        )
        thread_id = _payload_value(query, "thread_id")
        if thread_id is not None:
            projection = await self._get_operations().projection(
                workspace_id, _as_uuid(thread_id, "thread_id")
            )
            result.records.append(self._typed_record("klmProjection", projection))
        result.records.append(
            {
                "recordType": "bridgePolicy",
                "outboundWebhookActivationAllowed": False,
                "syncAuthority": "klm",
            }
        )
        return result

    async def _load_external_state(self, workspace_id, key, query, spec) -> _LoadResult:
        result = await self._state_snapshot_records(workspace_id, key, query, spec.record_type)
        result.records.append(
            {
                "recordType": "externalDispatchGate",
                "state": "blocked",
                "reasonCode": "external_dispatch_not_approved",
                "dryRunAllowed": True,
            }
        )
        return result

    async def _load_search_views(self, _workspace, _key, query, _spec) -> _LoadResult:
        params = self._query_params(query)
        resource = "conversations/search" if params.get("q") else "conversations"
        result = await self._fetch_many(
            [
                ("conversationSearchResult", resource, params, "v1"),
                (
                    "conversationCustomView",
                    "custom_filters",
                    self._query_params(query, filter_type="conversation"),
                    "v1",
                ),
            ]
        )
        return result

    async def _load_notifications(self, _workspace, _key, query, _spec) -> _LoadResult:
        return _LoadResult(
            await self._fetch_typed(
                "notification", "notifications", params=self._query_params(query)
            )
        )

    async def _load_canned(self, _workspace, _key, _query, _spec) -> _LoadResult:
        rows = await self._get_client().list_canned_responses()
        return _LoadResult([self._typed_record("cannedResponse", row) for row in rows])

    async def _load_teams(self, _workspace, _key, query, _spec) -> _LoadResult:
        result = _LoadResult(
            [self._typed_record("team", row) for row in await self._get_client().list_teams()]
        )
        team_id = _payload_value(query, "team_id")
        if team_id is not None:
            result.records.extend(
                await self._fetch_typed(
                    "teamMember",
                    f"teams/{_positive_id(team_id, 'team_id')}/team_members",
                )
            )
        return result

    async def _load_agent_bots(self, _workspace, _key, _query, _spec) -> _LoadResult:
        return _LoadResult(await self._fetch_typed("agentBot", "agent_bots"))

    async def _load_inboxes(self, _workspace, _key, query, _spec) -> _LoadResult:
        inbox_id = _payload_value(query, "inbox_id")
        if inbox_id is None:
            rows = await self._get_client().list_inboxes()
            return _LoadResult([self._typed_record("inbox", row) for row in rows])
        inbox = _positive_id(inbox_id, "inbox_id")
        return await self._fetch_many(
            [
                ("inbox", f"inboxes/{inbox}", None, "v1"),
                ("inboxMember", f"inbox_members/{inbox}", None, "v1"),
                ("inboxHealth", f"inboxes/{inbox}/health", None, "v1"),
            ]
        )

    async def _load_assignment(self, _workspace, _key, _query, _spec) -> _LoadResult:
        return _LoadResult(await self._fetch_typed("assignmentPolicy", "assignment_policies"))

    async def _load_workflow(self, workspace, key, query, spec) -> _LoadResult:
        result = await self._state_snapshot_records(workspace, key, query, spec.record_type)
        result.records.append(
            {
                "recordType": "conversationWorkflowPolicy",
                "authority": "klm",
                "upstreamMutationAvailable": False,
                "reasonCode": "pinned_controller_does_not_permit_workflow_setting",
            }
        )
        return result

    async def _load_profile(self, _workspace, _key, _query, _spec) -> _LoadResult:
        result = _LoadResult()
        for record_type, path in (
            ("operatorProfile", "/api/v1/profile"),
            ("mfaStatus", "/api/v1/profile/mfa"),
            ("operatorSession", "/api/v1/profile/sessions"),
        ):
            try:
                result.records.extend(await self._fetch_absolute(record_type, path))
            except ChatwootClientError:
                result.issues.append(f"{record_type}_unavailable")
        return result

    async def _load_account(self, _workspace, _key, _query, _spec) -> _LoadResult:
        return _LoadResult(
            [self._typed_record("accountSetting", await self._get_client().account())]
        )

    async def _load_labels(self, _workspace, _key, _query, _spec) -> _LoadResult:
        rows = await self._get_client().list_labels()
        return _LoadResult([self._typed_record("label", row) for row in rows])

    async def _load_dashboard_apps(self, _workspace, _key, _query, _spec) -> _LoadResult:
        return _LoadResult(await self._fetch_typed("dashboardApp", "dashboard_apps"))

    async def _load_csat(self, _workspace, _key, query, _spec) -> _LoadResult:
        params = self._query_params(query)
        return await self._fetch_many(
            [
                ("csatResponse", "csat_survey_responses", params, "v1"),
                ("csatMetric", "csat_survey_responses/metrics", params, "v1"),
            ]
        )

    async def _load_contact_ops(self, _workspace, _key, query, _spec) -> _LoadResult:
        contact_id = _payload_value(query, "contact_id")
        records: list[dict[str, Any]] = [
            {
                "recordType": "contactTransferPolicy",
                "importMode": "dry_run_only",
                "exportMode": "dry_run_only",
                "externalDispatchAllowed": False,
            }
        ]
        if contact_id is not None:
            contact = _positive_id(contact_id, "contact_id")
            records.extend(await self._fetch_typed("contactNote", f"contacts/{contact}/notes"))
            records.extend(
                await self._fetch_typed("contactAttachment", f"contacts/{contact}/attachments")
            )
        return _LoadResult(records)

    async def _load_billing(self, _workspace, _key, _query, _spec) -> _LoadResult:
        return _LoadResult(
            [
                {
                    "recordType": "applicabilityPolicy",
                    "state": "not_applicable",
                    "reasonCode": "self_hosted_sidecar_has_no_cloud_billing",
                    "upstreamPin": CHATWOOT_UPSTREAM_PIN,
                }
            ]
        )

    async def _state_snapshot_records(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
        record_type: str,
    ) -> _LoadResult:
        try:
            raw = await self._call_state(
                "snapshot",
                workspace_id=workspace_id,
                capability_key=capability_key,
                query=query,
            )
        except ChatwootNativeDependencyError:
            return _LoadResult(
                [
                    {
                        "recordType": record_type,
                        "state": "unavailable",
                        "reasonCode": "klm_native_state_store_unavailable",
                    }
                ],
                ["klm_native_state_store_unavailable"],
            )
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(mode="json", by_alias=True)
        if isinstance(raw, Mapping):
            raw_records = raw.get("records")
            if isinstance(raw_records, list):
                rows = [row for row in raw_records if isinstance(row, Mapping)]
            elif isinstance(raw.get("state"), Mapping):
                state = dict(raw["state"])
                state_mode = state.get("executionMode", state.get("execution_mode"))
                if not state.get("configuration") and state_mode == "disabled":
                    state["configured"] = False
                rows = [state]
                configuration = state.get("configuration")
                if isinstance(configuration, Mapping):
                    for collection in (
                        "policies",
                        "companies",
                        "capacityRules",
                        "roles",
                    ):
                        values = configuration.get(collection)
                        if isinstance(values, list):
                            rows.extend(row for row in values if isinstance(row, Mapping))
                commands = raw.get("commands")
                if isinstance(commands, list):
                    rows.extend(
                        {"recordType": "klmNativeCommand", **row}
                        for row in commands
                        if isinstance(row, Mapping)
                    )
                audit_trail = raw.get("auditTrail", raw.get("audit_trail"))
                if isinstance(audit_trail, list):
                    rows.extend(
                        {"recordType": "klmNativeAuditEvent", **row}
                        for row in audit_trail
                        if isinstance(row, Mapping)
                    )
            else:
                rows = [raw]
        elif isinstance(raw, list):
            rows = [row for row in raw if isinstance(row, Mapping)]
        else:
            rows = [{"value": raw}]
        return _LoadResult([self._typed_record(record_type, row) for row in rows])

    def _get_state_store(self):
        if self.state_store is not None:
            return self.state_store
        try:
            module = importlib.import_module("app.services.oss_native_state_service")
        except ImportError as error:
            raise ChatwootNativeDependencyError(
                "O state store KLM nativo ainda não está disponível."
            ) from error
        for name in (
            "OSSNativeStateService",
            "OssNativeStateService",
            "NativeStateService",
            "OSSNativeStateStore",
        ):
            factory = getattr(module, name, None)
            if factory is not None:
                register = getattr(module, "register_provider_dispatcher", None)
                if callable(register):
                    register("chatwoot", _native_state_dispatch)
                self.state_store = factory(self.db)
                return self.state_store
        raise ChatwootNativeDependencyError(
            "oss_native_state_service não expõe uma factory reconhecida."
        )

    async def _call_state(self, operation: str, **values: Any) -> Any:
        store = self._get_state_store()
        candidates = (
            ("snapshot", "get_snapshot", "read", "get")
            if operation == "snapshot"
            else ("execute", "apply", "mutate", "upsert")
        )
        method = next(
            (
                getattr(store, name, None)
                for name in candidates
                if callable(getattr(store, name, None))
            ),
            None,
        )
        if method is None:
            raise ChatwootNativeDependencyError(f"O state store KLM não implementa {operation}.")
        aliases = {
            **values,
            "provider": "chatwoot",
            "provider_key": "chatwoot",
            "namespace": "chatwoot",
            "capability": values.get("capability_key"),
            "key": values.get("capability_key"),
            "state_key": values.get("capability_key"),
            "data": values.get("payload"),
        }
        signature = inspect.signature(method)
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        has_var_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        for name, parameter in signature.parameters.items():
            if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            if name not in aliases:
                if parameter.default is inspect.Parameter.empty:
                    raise ChatwootNativeDependencyError(
                        f"Parâmetro obrigatório desconhecido no state store: {name}."
                    )
                continue
            if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
                args.append(aliases[name])
            else:
                kwargs[name] = aliases[name]
        if has_var_kwargs:
            kwargs.update({key: value for key, value in aliases.items() if key not in kwargs})
        result = method(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def _state_execute(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> Any:
        if command not in _STATE_COMMANDS:
            raise ChatwootNativeCapabilityError(
                f"Comando {command!r} não possui mutação KLM nativa."
            )
        store = self._get_state_store()
        if command in _STATE_CONFIG_COMMANDS and callable(getattr(store, "update_config", None)):
            return await self._update_native_config(
                store,
                workspace_id,
                actor_user_id,
                capability_key,
                command,
                payload,
            )
        operation_id = _payload_value(payload, "operation_id")
        idempotency_key = _payload_value(payload, "idempotency_key")
        if operation_id is not None and str(operation_id) == str(idempotency_key):
            snapshot = await store.get_snapshot(workspace_id, "chatwoot", capability_key)
            current = self._model_mapping(snapshot)
            state = current.get("state") if isinstance(current, Mapping) else None
            configuration = (
                dict(state.get("configuration") or {}) if isinstance(state, Mapping) else {}
            )
            version = (
                int(state.get("configVersion", state.get("config_version", 0)))
                if isinstance(state, Mapping)
                else 0
            )
            return _native_state_dispatch(
                SimpleNamespace(
                    command_type=command,
                    payload=payload,
                    configuration=configuration,
                    config_version=version,
                    external_network_allowed=False,
                )
            )
        if callable(getattr(store, "execute_command", None)):
            return await self._execute_native_state_command(
                store,
                workspace_id,
                actor_user_id,
                capability_key,
                command,
                payload,
            )
        return await self._call_state(
            "execute",
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            capability_key=capability_key,
            command=command,
            payload=payload,
        )

    async def _update_native_config(
        self,
        store: Any,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> Any:
        snapshot = await store.get_snapshot(workspace_id, "chatwoot", capability_key)
        current = self._model_mapping(snapshot)
        state = current.get("state") if isinstance(current, Mapping) else None
        configuration = dict(state.get("configuration") or {}) if isinstance(state, Mapping) else {}
        current_version = (
            int(state.get("configVersion", state.get("config_version", 0)))
            if isinstance(state, Mapping)
            else 0
        )
        requested_version = _payload_value(payload, "expected_version")
        expected_version = current_version if requested_version is None else int(requested_version)
        next_configuration = self._apply_state_configuration(
            configuration, capability_key, command, payload
        )
        schema_module = importlib.import_module("app.schemas.oss_native")
        model_module = importlib.import_module("app.models.oss_native")
        patch_type = getattr(schema_module, "OSSNativeConfigPatch")
        execution_modes = getattr(model_module, "OSSNativeExecutionMode")
        execution_mode = (
            execution_modes.disabled if command == "delete_config" else execution_modes.local_native
        )
        data = patch_type(
            expected_version=expected_version,
            configuration=next_configuration,
            execution_mode=execution_mode,
        )
        return await store.update_config(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            provider="chatwoot",
            capability=capability_key,
            data=data,
        )

    async def _execute_native_state_command(
        self,
        store: Any,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> Any:
        snapshot = await store.get_snapshot(workspace_id, "chatwoot", capability_key)
        current = self._model_mapping(snapshot)
        state = current.get("state") if isinstance(current, Mapping) else None
        configured = bool(state.get("configured")) if isinstance(state, Mapping) else False
        execution_mode = (
            str(state.get("executionMode", state.get("execution_mode", "disabled")))
            if isinstance(state, Mapping)
            else "disabled"
        )
        if not configured or execution_mode != "local_native":
            snapshot = await self._update_native_config(
                store,
                workspace_id,
                actor_user_id,
                capability_key,
                "save_config",
                {
                    "expectedVersion": (
                        int(state.get("configVersion", state.get("config_version", 0)))
                        if isinstance(state, Mapping)
                        else 0
                    ),
                    "configuration": (
                        dict(state.get("configuration") or {}) if isinstance(state, Mapping) else {}
                    ),
                },
            )
            current = self._model_mapping(snapshot)
            state = current.get("state") if isinstance(current, Mapping) else None
        version = (
            int(state.get("configVersion", state.get("config_version", 0)))
            if isinstance(state, Mapping)
            else 0
        )
        schema_module = importlib.import_module("app.schemas.oss_native")
        command_type = getattr(schema_module, "OSSNativeCommandCreate")
        request_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "expectedVersion",
                "expected_version",
                "idempotencyKey",
                "idempotency_key",
            }
        }
        idempotency_key = _payload_value(payload, "idempotency_key") or (
            f"chatwoot-native:{capability_key}:{command}:{uuid4()}"
        )
        data = command_type(
            command_type=command,
            idempotency_key=str(idempotency_key),
            expected_version=version,
            payload=request_payload,
        )
        return await store.execute_command(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            provider="chatwoot",
            capability=capability_key,
            data=data,
        )

    @classmethod
    def _apply_state_configuration(
        cls,
        current: dict[str, Any],
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        supplied = _payload_value(payload, "configuration")
        if command == "save_config":
            if supplied is not None and not isinstance(supplied, Mapping):
                raise ChatwootNativeCapabilityError("configuration deve ser um objeto.")
            return dict(supplied or cls._state_payload(payload))
        if command == "delete_config":
            return {}
        if command == "set_required_attributes":
            attribute_keys = _payload_value(payload, "attribute_keys")
            if not isinstance(attribute_keys, list) or not all(
                isinstance(item, str) and item.strip() for item in attribute_keys
            ):
                raise ChatwootNativeCapabilityError(
                    "attribute_keys deve ser uma lista de chaves não vazias."
                )
            return {
                **current,
                "requiredAttributeKeys": list(dict.fromkeys(attribute_keys)),
            }

        collection_names = {
            "sla": "policies",
            "companies": "companies",
            "agent-capacity": "capacityRules",
            "custom-roles-security": "roles",
        }
        collection_name = collection_names.get(capability_key)
        if collection_name is None:
            return dict(supplied or cls._state_payload(payload))
        records = list(current.get(collection_name) or [])
        if not all(isinstance(row, Mapping) for row in records):
            records = []
        singular_names = {
            "sla": "policy",
            "companies": "company",
            "agent-capacity": "capacity",
            "custom-roles-security": "role",
        }
        item = _payload_value(payload, singular_names[capability_key])
        if not isinstance(item, Mapping):
            item = cls._state_payload(payload)
        identifier = cls._state_item_id(item, payload)
        if command.startswith("delete"):
            if identifier is None:
                raise ChatwootNativeCapabilityError(
                    "A exclusão KLM nativa exige id ou key do registro."
                )
            records = [row for row in records if cls._state_item_id(row, row) != identifier]
        else:
            normalized_item = dict(item)
            if identifier is None:
                identifier = str(uuid4())
                normalized_item.setdefault("id", identifier)
            replaced = False
            for index, row in enumerate(records):
                if cls._state_item_id(row, row) == identifier:
                    records[index] = normalized_item
                    replaced = True
                    break
            if not replaced:
                records.append(normalized_item)
        return {**current, collection_name: records}

    @staticmethod
    def _state_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        ignored = {
            "confirm",
            "expectedVersion",
            "expected_version",
            "idempotencyKey",
            "idempotency_key",
        }
        return {key: value for key, value in payload.items() if key not in ignored}

    @staticmethod
    def _state_item_id(item: Mapping[str, Any], payload: Mapping[str, Any]) -> str | None:
        for key in ("id", "key", "slug", "name"):
            value = item.get(key)
            if value is None:
                value = _payload_value(payload, key)
            if value is not None:
                return str(value)
        return None

    @staticmethod
    def _model_mapping(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json", by_alias=True)
            return dict(dumped) if isinstance(dumped, Mapping) else {}
        return dict(value) if isinstance(value, Mapping) else {}

    async def _execute_sync(self, workspace_id, actor_user_id, key, command, payload):
        await self._require_local_sync_inbox()
        sync = self._get_sync()
        if command == "sync_workspace":
            raw = await sync.sync_workspace(workspace_id)
        else:
            thread_id = _as_uuid(_payload_value(payload, "thread_id"), "thread_id")
            raw = await sync.sync_thread_by_id(workspace_id, thread_id, actor_user_id)
        return self._command_response(key, command, raw, "klm_projection")

    async def _require_local_sync_inbox(self) -> None:
        client = self._get_client()
        configured_id = _positive_id(client.inbox_id, "inbox_id")
        inboxes = await client.list_inboxes()
        configured = next(
            (row for row in inboxes if str(row.get("id")) == configured_id),
            None,
        )
        if configured is None or configured.get("channel_type") != "Channel::Api":
            raise ChatwootNativeGateError(
                "A projeção exige que o inbox configurado seja Channel::Api local."
            )

    async def _execute_routing(self, workspace_id, actor_user_id, key, payload):
        thread_id = _as_uuid(_payload_value(payload, "thread_id"), "thread_id")
        changes = _payload_value(payload, "changes")
        if not isinstance(changes, Mapping) or not changes:
            raise ChatwootNativeCapabilityError("changes é obrigatório para roteamento.")
        allowed = {"status", "priority", "assignee_id", "team_id", "labels"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise ChatwootNativeCapabilityError(
                f"Campos de roteamento não permitidos: {', '.join(sorted(unexpected))}."
            )
        operations = self._get_operations()
        conversation_id = await operations.conversation_id(workspace_id, thread_id)
        if conversation_id is None:
            raise ChatwootNativeCapabilityError(
                "A thread ainda não possui conversa Chatwoot vinculada."
            )
        self._require_klm_local_conversation(
            await self._get_client().conversation(str(conversation_id))
        )
        raw = await operations.update_routing(
            workspace_id=workspace_id,
            thread_id=thread_id,
            actor_user_id=actor_user_id,
            changes=dict(changes),
        )
        return self._command_response(key, "update_routing", raw, "chatwoot_sidecar")

    async def _execute_private_note(self, workspace_id, actor_user_id, key, payload):
        conversation_id = _positive_id(
            _payload_value(payload, "conversation_id"), "conversation_id"
        )
        content = self._required_text(payload, "content")
        raw = await self._get_client().create_message(
            conversation_id=conversation_id,
            content=content,
            source_id=f"klm-native-note:{uuid4()}",
            message_type="outgoing",
            private=True,
        )
        audited = await self._audit_mutation(
            workspace_id, actor_user_id, key, "create_private_note", raw
        )
        return self._command_response(key, "create_private_note", raw, "chatwoot_sidecar", audited)

    async def _execute_delete_private_note(self, workspace_id, actor_user_id, key, payload):
        self._require_confirmation(payload)
        conversation_id = _positive_id(
            _payload_value(payload, "conversation_id"), "conversation_id"
        )
        message_id = _positive_id(_payload_value(payload, "message_id"), "message_id")
        messages = await self._get_client().list_messages(conversation_id)
        target = next(
            (row for row in messages if str(row.get("id")) == message_id),
            None,
        )
        if target is None:
            raise ChatwootNativeCapabilityError("Nota privada não encontrada na conversa.")
        if target.get("private") is not True:
            raise ChatwootNativeGateError(
                "O adapter de notas privadas não pode excluir uma mensagem pública."
            )
        raw = await self._request_sidecar(
            "DELETE",
            self._account_path(f"conversations/{conversation_id}/messages/{message_id}"),
            expected=(200, 202, 204),
            allow_empty=True,
        )
        audited = await self._audit_mutation(
            workspace_id, actor_user_id, key, "delete_private_note", raw
        )
        return self._command_response(key, "delete_private_note", raw, "chatwoot_sidecar", audited)

    async def _execute_local_message(self, workspace_id, actor_user_id, key, payload):
        conversation_id = _positive_id(
            _payload_value(payload, "conversation_id"), "conversation_id"
        )
        conversation = await self._get_client().conversation(conversation_id)
        self._require_klm_local_conversation(conversation)
        content = self._required_text(payload, "content")
        raw = await self._get_client().create_message(
            conversation_id=conversation_id,
            content=content,
            source_id=f"klm-native-message:{uuid4()}",
            message_type="outgoing",
            private=False,
        )
        audited = await self._audit_mutation(
            workspace_id, actor_user_id, key, "send_local_message", raw
        )
        return self._command_response(key, "send_local_message", raw, "chatwoot_sidecar", audited)

    async def _execute_contact_attributes(self, workspace_id, actor_user_id, key, payload):
        contact_id = _positive_id(_payload_value(payload, "contact_id"), "contact_id")
        attributes = _payload_value(payload, "custom_attributes")
        if not isinstance(attributes, Mapping):
            raise ChatwootNativeCapabilityError("custom_attributes deve ser um objeto.")
        raw = await self._get_client()._request_json(
            "PATCH",
            self._account_path(f"contacts/{contact_id}"),
            json={"custom_attributes": dict(attributes)},
            expected=(200,),
        )
        audited = await self._audit_mutation(
            workspace_id, actor_user_id, key, "set_contact_attributes", raw
        )
        return self._command_response(
            key, "set_contact_attributes", raw, "chatwoot_sidecar", audited
        )

    async def _execute_clone_rule(self, workspace_id, actor_user_id, key, payload):
        rule_id = _positive_id(_payload_value(payload, "rule_id"), "rule_id")
        original_payload = await self._get_client()._request_json(
            "GET", self._account_path(f"automation_rules/{rule_id}")
        )
        original_rows = self._extract_rows(original_payload)
        original = original_rows[0] if original_rows else {}
        if original.get("active") is not False:
            raise ChatwootNativeGateError(
                "Somente uma automação já inativa pode ser clonada sem risco de despacho."
            )
        raw = await self._get_client()._request_json(
            "POST",
            self._account_path(f"automation_rules/{rule_id}/clone"),
            expected=(200, 201),
        )
        audited = await self._audit_mutation(
            workspace_id, actor_user_id, key, "clone_automation_rule", raw
        )
        return self._command_response(
            key, "clone_automation_rule", raw, "chatwoot_sidecar", audited
        )

    async def _execute_notification(self, workspace_id, actor_user_id, key, command, payload):
        notification_id = None
        if command != "read_all_notifications":
            notification_id = _positive_id(
                _payload_value(payload, "notification_id"), "notification_id"
            )
        if command == "mark_notification_read":
            method, resource, body, confirm = (
                "PATCH",
                f"notifications/{notification_id}",
                {"read_status": True},
                False,
            )
        elif command == "mark_notification_unread":
            method, resource, body, confirm = (
                "POST",
                f"notifications/{notification_id}/unread",
                {},
                False,
            )
        elif command == "read_all_notifications":
            method, resource, body, confirm = "POST", "notifications/read_all", {}, False
        elif command == "snooze_notification":
            method, resource, body, confirm = (
                "POST",
                f"notifications/{notification_id}/snooze",
                {"snoozed_until": _payload_value(payload, "snoozed_until")},
                False,
            )
        else:
            method, resource, body, confirm = (
                "DELETE",
                f"notifications/{notification_id}",
                {},
                True,
            )
        if confirm:
            self._require_confirmation(payload)
        raw = await self._request_sidecar(
            method,
            self._account_path(resource),
            json=body or None,
            expected=(200, 202, 204),
            allow_empty=method == "DELETE" or command == "read_all_notifications",
        )
        audited = await self._audit_mutation(workspace_id, actor_user_id, key, command, raw)
        return self._command_response(key, command, raw, "chatwoot_sidecar", audited)

    async def _execute_profile(self, workspace_id, actor_user_id, key, command, payload):
        if command == "update_profile":
            method, path = "PATCH", "/api/v1/profile"
            profile = self._body(
                payload,
                ("name", "display_name", "message_signature", "phone_number", "ui_settings"),
            )
            body = {"profile": profile}
        elif command == "set_availability":
            method, path = "POST", "/api/v1/profile/availability"
            profile = self._body(payload, ("availability",), ("availability",))
            profile["account_id"] = int(_positive_id(self._get_client().account_id, "account_id"))
            body = {"profile": profile}
        elif command == "begin_mfa":
            method, path, body = "POST", "/api/v1/profile/mfa", {}
        elif command == "verify_mfa":
            method, path = "POST", "/api/v1/profile/mfa/verify"
            otp_code = _payload_value(payload, "otp_code") or _payload_value(payload, "code")
            if not isinstance(otp_code, str) or not otp_code.strip():
                raise ChatwootNativeCapabilityError("otp_code é obrigatório.")
            body = {"otp_code": otp_code.strip()}
        else:
            self._require_confirmation(payload)
            method, path = "DELETE", "/api/v1/profile/mfa"
            body = self._body(
                payload,
                ("password", "otp_code", "backup_code"),
                ("password",),
            )
            if not body.get("otp_code") and not body.get("backup_code"):
                raise ChatwootNativeCapabilityError(
                    "otp_code ou backup_code é obrigatório para desabilitar MFA."
                )
        raw = await self._request_sidecar(
            method,
            path,
            json=body or None,
            expected=(200, 201, 202, 204),
            allow_empty=method == "DELETE",
        )
        audited = await self._audit_mutation(workspace_id, actor_user_id, key, command, raw)
        return self._command_response(key, command, raw, "chatwoot_sidecar", audited)

    async def _execute_account(self, workspace_id, actor_user_id, key, payload):
        body = self._body(
            payload,
            (
                "name",
                "locale",
                "domain",
                "support_email",
                "industry",
                "company_size",
                "timezone",
                "referral_source",
                "user_role",
                "website",
                "auto_resolve_after",
                "auto_resolve_message",
                "auto_resolve_ignore_waiting",
                "audio_transcriptions",
                "auto_resolve_label",
            ),
        )
        if not body:
            raise ChatwootNativeCapabilityError("Nenhuma configuração de conta foi informada.")
        account_id = _positive_id(self._get_client().account_id, "account_id")
        raw = await self._get_client()._request_json(
            "PATCH", f"/api/v1/accounts/{account_id}", json=body, expected=(200,)
        )
        audited = await self._audit_mutation(
            workspace_id, actor_user_id, key, "update_account", raw
        )
        return self._command_response(key, "update_account", raw, "chatwoot_sidecar", audited)

    async def _execute_mutation(
        self,
        workspace_id,
        actor_user_id,
        key,
        command,
        mutation,
        payload,
    ):
        if mutation.confirm:
            self._require_confirmation(payload)
        path = mutation.path
        for placeholder in re.findall(r"{([a-z_]+)}", path):
            identifier = _route_identifier(_identifier_value(payload, placeholder), placeholder)
            path = path.replace(f"{{{placeholder}}}", quote(identifier, safe=""))
        body = self._body(payload, mutation.fields, mutation.required_fields)
        if command in {"create_segment", "update_segment"}:
            body.setdefault("filter_type", "contact")
        if command in {"create_custom_view", "update_custom_view"}:
            body.setdefault("filter_type", "conversation")
        if command in {"create_automation_rule", "update_automation_rule"}:
            body["active"] = False
            self._reject_external_actions(body.get("actions"))
        if command in {"create_macro", "update_macro"}:
            self._reject_external_actions(body.get("actions"))
        if command == "create_inbox":
            self._require_local_inbox(body)
        if command in {"create_dashboard_app", "update_dashboard_app"}:
            content = body.get("content")
            content = dict(content) if isinstance(content, Mapping) else {}
            url = body.pop("url", None)
            content_type = body.pop("type", None)
            if url is not None:
                content["url"] = url
            content.setdefault("type", content_type or "frame")
            body["content"] = content
        request_body = {mutation.root: body} if mutation.root else body
        expected = mutation.expected
        if mutation.method == "DELETE" and 204 not in expected:
            expected = (*expected, 204)
        raw = await self._request_sidecar(
            mutation.method,
            self._account_path(path),
            json=request_body or None,
            expected=expected,
            allow_empty=mutation.method == "DELETE",
        )
        audited = await self._audit_mutation(workspace_id, actor_user_id, key, command, raw)
        return self._command_response(key, command, raw, "chatwoot_sidecar", audited)

    async def _audit_mutation(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        raw: Any,
    ) -> bool:
        if not callable(getattr(self.db, "add", None)) or not callable(
            getattr(self.db, "flush", None)
        ):
            return False
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type=f"chatwoot.native.{command}",
            entity_type="chatwoot_capability",
            entity_id=workspace_id,
            summary=f"Comando nativo {command} executado no sidecar Chatwoot local.",
            correlation_id=f"chatwoot-native:{capability_key}",
            after_data={
                "capabilityKey": capability_key,
                "command": command,
                "result": _sanitize(raw),
                "externalActionsExecuted": 0,
            },
        )
        return True

    @staticmethod
    def _body(
        payload: Mapping[str, Any],
        fields: tuple[str, ...],
        required: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        for field_name in fields:
            value = _payload_value(payload, field_name)
            if value is not None:
                body[field_name] = value
        missing = [name for name in required if name not in body]
        if missing:
            raise ChatwootNativeCapabilityError(
                f"Campos obrigatórios ausentes: {', '.join(missing)}."
            )
        return body

    @staticmethod
    def _required_text(payload: Mapping[str, Any], key: str) -> str:
        value = _payload_value(payload, key)
        if not isinstance(value, str) or not value.strip():
            raise ChatwootNativeCapabilityError(f"{key} é obrigatório.")
        if len(value) > 20_000:
            raise ChatwootNativeCapabilityError(f"{key} excede o limite de 20.000 caracteres.")
        return value.strip()

    @staticmethod
    def _require_confirmation(payload: Mapping[str, Any]) -> None:
        if payload.get("confirm") is True:
            return
        operation_id = _payload_value(payload, "operation_id")
        idempotency_key = _payload_value(payload, "idempotency_key")
        if operation_id is not None and str(operation_id) == str(idempotency_key):
            return
        raise ChatwootNativeGateError("A operação destrutiva exige confirm=true.")

    @classmethod
    def _reject_secret_input(cls, value: Any, path: str = "payload") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                if _is_secret_key(key_text):
                    raise ChatwootNativeGateError(
                        f"Segredos brutos não são aceitos em {path}.{key_text}; use uma referência."
                    )
                cls._reject_secret_input(item, f"{path}.{key_text}")
        elif isinstance(value, list | tuple):
            for index, item in enumerate(value):
                cls._reject_secret_input(item, f"{path}[{index}]")
        elif isinstance(value, str):
            if re.search(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+", value):
                raise ChatwootNativeGateError("Credencial Bearer bruta não é aceita.")
            parsed = urlsplit(value)
            if parsed.scheme in {"http", "https"} and (parsed.username or parsed.password):
                raise ChatwootNativeGateError("URLs com credenciais embutidas não são aceitas.")

    @staticmethod
    def _reject_external_actions(actions: Any) -> None:
        if actions is None:
            return
        serialized = str(actions).lower()
        if any(word in serialized for word in _EXTERNAL_WORDS):
            raise ChatwootNativeGateError(
                "Ações de envio, webhook ou execução externa não podem ser ativadas pelo adapter."
            )

    @staticmethod
    def _require_local_inbox(body: Mapping[str, Any]) -> None:
        channel = body.get("channel")
        channel_type = None
        if isinstance(channel, Mapping):
            channel_type = channel.get("type") or channel.get("channel_type")
        normalized = str(channel_type or "").lower().replace("channel::", "")
        if normalized not in {"api", "webwidget", "web_widget"}:
            raise ChatwootNativeGateError(
                "Somente inboxes locais API ou WebWidget podem ser criados por este adapter."
            )

    @staticmethod
    def _require_klm_local_conversation(conversation: Mapping[str, Any]) -> None:
        inbox = conversation.get("inbox")
        channel_type = inbox.get("channel_type") if isinstance(inbox, Mapping) else None
        custom_attributes = conversation.get("custom_attributes")
        is_klm_managed = (
            isinstance(custom_attributes, Mapping) and custom_attributes.get("klm_managed") is True
        )
        if channel_type != "Channel::Api" or not is_klm_managed:
            raise ChatwootNativeGateError("A conversa deve ser Channel::Api e gerenciada pelo KLM.")

    @staticmethod
    def _action_required(command: str) -> list[str]:
        if command in _SPECIAL_ACTION_REQUIRED:
            return list(_SPECIAL_ACTION_REQUIRED[command])
        mutation = _MUTATIONS.get(command)
        if mutation is None:
            return []
        names = [*re.findall(r"{([a-z_]+)}", mutation.path), *mutation.required_fields]
        return list(dict.fromkeys(names))

    @staticmethod
    def _actions(spec: _Capability) -> list[dict[str, Any]]:
        destructive_prefixes = ("delete", "remove", "disable")
        read_commands = {"refresh", *_READ_COMMANDS}
        actions = [
            {
                "command": command,
                "enabled": True,
                "mutates": command not in read_commands and "dry_run" not in command,
                "requiresConfirmation": command.startswith(destructive_prefixes),
                "externalDispatch": False,
                "required": ChatwootNativeService._action_required(command),
            }
            for command in spec.actions
        ]
        actions.extend(
            {
                "command": command,
                "enabled": False,
                "mutates": True,
                "requiresConfirmation": False,
                "externalDispatch": True,
                "reasonCode": (
                    "upstream_contract_unavailable"
                    if command in _UPSTREAM_GAP_COMMANDS
                    else "external_effect_gate"
                ),
            }
            for command in spec.blocked_actions
        )
        return actions

    @staticmethod
    def _dry_run_response(key: str, command: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        upload_metadata = None
        if command == "dry_run_upload":
            filename = _payload_value(payload, "filename")
            content_type = _payload_value(payload, "content_type")
            checksum = _payload_value(payload, "checksum")
            byte_size = _payload_value(payload, "byte_size")
            if not all(
                isinstance(value, str) and value.strip()
                for value in (filename, content_type, checksum)
            ):
                raise ChatwootNativeCapabilityError(
                    "filename, content_type e checksum são obrigatórios no dry-run."
                )
            try:
                normalized_size = int(byte_size)
            except (TypeError, ValueError) as error:
                raise ChatwootNativeCapabilityError(
                    "byte_size deve ser um inteiro positivo."
                ) from error
            if normalized_size <= 0:
                raise ChatwootNativeCapabilityError("byte_size deve ser um inteiro positivo.")
            upload_metadata = {
                "filename": filename.strip(),
                "contentType": content_type.strip(),
                "byteSize": normalized_size,
                "checksumPresent": True,
                "uploadPrepared": False,
                "reasonCode": "direct_upload_client_contract_unavailable",
            }
        count = 0
        for candidate in ("rows", "records", "actions", "items"):
            value = _payload_value(payload, candidate)
            if isinstance(value, list):
                count = len(value)
                break
        return _sanitize(
            {
                "status": "dry_run",
                "capabilityKey": key,
                "command": command,
                "result": {
                    "validated": True,
                    "candidateCount": count,
                    "externalActionsExecuted": 0,
                    **({"upload": upload_metadata} if upload_metadata else {}),
                },
                "source": {"provider": "chatwoot", "authority": "klm_native"},
                "health": {"status": "healthy", "externalDispatchAllowed": False},
            }
        )

    @staticmethod
    def _command_response(
        key: str,
        command: str,
        raw: Any,
        authority: str,
        audit_recorded: bool | None = None,
    ) -> dict[str, Any]:
        response = {
            "status": "completed",
            "capabilityKey": key,
            "command": command,
            "result": raw if raw not in (None, {}, []) else {"acknowledged": True},
            "source": {"provider": "chatwoot", "authority": authority},
            "health": {
                "status": "healthy",
                "externalDispatchAllowed": False,
                "secretsExposed": False,
            },
            "externalActionsExecuted": 0,
        }
        if audit_recorded is not None:
            response["auditRecorded"] = audit_recorded
        return _sanitize(response)


__all__ = [
    "CHATWOOT_CAPABILITY_KEYS",
    "ChatwootNativeCapabilityError",
    "ChatwootNativeDependencyError",
    "ChatwootNativeError",
    "ChatwootNativeGateError",
    "ChatwootNativeService",
]
