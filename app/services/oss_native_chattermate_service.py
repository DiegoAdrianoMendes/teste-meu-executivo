"""Native, capability-scoped ChatterMate adapter for the KLM Hub.

The adapter deliberately separates local upstream mutations, KLM-canonical
operations, persistent native fallback state, and closed external dispatches.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select

from app.ai.structured_output import parse_and_validate, validate_response_schema
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.klm_operations import ThreadStatus
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
    sanitize_oss_json,
)
from app.schemas.klm_operations import OmnichannelThreadUpdate
from app.services.agent_governance_service import AgentGovernanceService
from app.services.chattermate_client import ChatterMateClient, ChatterMateClientError
from app.services.chattermate_operations_service import ChatterMateOperationsService
from app.services.flow_service import FlowService
from app.services.klm_agent_service import AGENT_RESPONSE_SCHEMA, KlmAgentService
from app.services.klm_operations_service import KlmOperationsService
from app.services.knowledge_service import KnowledgeService

_PROVIDER = "chattermate"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,160}$")
_COMMAND_RE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_MAX_QUERY_BYTES = 32 * 1024
_MAX_PAYLOAD_BYTES = 256 * 1024


class ChatterMateNativeError(RuntimeError):
    """Base error for the native capability adapter."""


class UnknownChatterMateCapabilityError(ChatterMateNativeError):
    """The requested capability is not part of the pinned 27-key contract."""


class UnsupportedChatterMateCommandError(ChatterMateNativeError):
    """The capability does not expose the requested command."""


class ChatterMateNativeGateError(PermissionError, ChatterMateNativeError):
    """A local policy gate rejected an external or high-impact action."""


class ChatterMateWorkspaceIsolationError(PermissionError, ChatterMateNativeError):
    """The sidecar resource is not explicitly bound to the current workspace."""


class ChatterMateNativeStateUnavailableError(ChatterMateNativeError):
    """The late-bound persistent native state service is unavailable."""


@dataclass(frozen=True)
class CapabilitySpec:
    title: str
    mode: str
    commands: tuple[str, ...]
    gate: str
    external_dispatch: bool = False


@dataclass(frozen=True)
class ChatterMateWorkspaceScope:
    """Verified organization and resource ACL for one KLM workspace."""

    workspace_id: UUID
    organization_id: str
    upstream_user_id: str
    agent_ids: frozenset[str]
    workflow_ids: frozenset[str]
    person_ids: frozenset[str]

    @property
    def cache_fingerprint(self) -> str:
        document = {
            "organizationId": self.organization_id,
            "userId": self.upstream_user_id,
            "agentIds": sorted(self.agent_ids),
            "workflowIds": sorted(self.workflow_ids),
            "personIds": sorted(self.person_ids),
        }
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def _spec(
    title: str,
    mode: str,
    commands: Sequence[str],
    gate: str = "local_authenticated",
) -> CapabilitySpec:
    return CapabilitySpec(title, mode, tuple(commands), gate)


CAPABILITY_SPECS: dict[str, CapabilitySpec] = {
    "agents-rbac": _spec(
        "Agentes e RBAC",
        "hybrid",
        (
            "sync",
            "create_agent",
            "update_agent",
            "configure_agent",
            "create_role",
            "update_role",
            "delete_role",
            "grant_permission",
            "revoke_permission",
            "create_user",
            "update_user",
            "delete_user",
        ),
    ),
    "visual-editor": _spec(
        "Editor visual",
        "upstream_crud",
        ("create_workflow", "update_workflow", "delete_workflow", "replace_graph"),
    ),
    "nodes-forms-conditions": _spec(
        "Nós, formulários e condições",
        "upstream_crud",
        ("replace_graph", "update_node", "save_native_draft"),
    ),
    "nodes-form-action-end-transfer-landing-llm": _spec(
        "Nós avançados e IA controlada",
        "hybrid_gated",
        ("replace_graph", "update_node", "dry_run_node", "save_native_draft"),
        "external_nodes_dry_run_only",
    ),
    "preview-execution": _spec(
        "Preview local autenticado",
        "klm_runtime",
        ("preview", "cancel_preview", "clear_cache"),
        "no_external_effects",
    ),
    "draft-publish-version": _spec(
        "Rascunho, publicação e versão",
        "hybrid",
        ("save_revision", "publish", "archive", "restore_draft"),
        "publish_requires_safe_graph",
    ),
    "mcp-tools-agent-association": _spec(
        "Ferramentas MCP por agente",
        "upstream_crud",
        ("create_tool", "update_tool", "delete_tool", "associate", "dissociate"),
        "loopback_mcp_only",
    ),
    "lead-capture-people": _spec(
        "Pessoas e captura de lead",
        "hybrid",
        ("update_lead_capture", "mark_customer", "link_klm_person"),
    ),
    "notifications-groups": _spec(
        "Grupos e notificações",
        "hybrid",
        (
            "create_group",
            "update_group",
            "delete_group",
            "add_user",
            "remove_user",
            "mark_notification_read",
            "save_notification_rule",
        ),
    ),
    "ratings-feedback": _spec(
        "Avaliações e feedback",
        "hybrid",
        ("create_rating", "link_rating"),
    ),
    "widget-apps": _spec(
        "Aplicativos de widget",
        "upstream_crud",
        ("create_app", "update_app", "delete_app", "regenerate_key"),
        "local_widget_only",
    ),
    "file-upload": _spec(
        "Upload e promoção ao RAG",
        "klm_state",
        ("register_file", "promote_to_rag", "remove_file"),
        "metadata_and_extracted_text_only",
    ),
    "jira-shopify": _spec(
        "Jira e Shopify",
        "configuration_only",
        ("save_config", "delete_config", "dry_run"),
        "external_dispatch_closed",
    ),
    "handoff-inbox-transfer": _spec(
        "Handoff, inbox e transferência",
        "hybrid",
        ("request_handoff", "takeover", "reassign", "close_handoff"),
        "hermes_or_human_only",
    ),
    "structured-output": _spec(
        "Saída estruturada",
        "klm_runtime",
        ("validate", "persist_contract"),
        "schema_validation_only",
    ),
    "knowledge-faq": _spec(
        "Conhecimento e FAQ",
        "hybrid",
        ("add_text", "link_agent", "unlink_agent", "delete", "promote_to_rag"),
    ),
    "widgets-help-center": _spec(
        "Widgets e help center",
        "hybrid",
        ("create_widget", "delete_widget", "end_chat", "update_help_center", "save_preview"),
        "public_dispatch_closed",
    ),
    "analytics": _spec(
        "Analytics",
        "read_projection",
        ("refresh", "save_metric_view"),
        "read_only_upstream",
    ),
    "api-websocket": _spec(
        "API e websocket",
        "local_runtime",
        ("probe", "invalidate_session_cache", "dry_run_event"),
        "loopback_only",
    ),
    "external-ai-channels": _spec(
        "IA e canais externos",
        "configuration_only",
        ("save_config", "delete_config", "dry_run"),
        "external_dispatch_closed",
    ),
    "private-enterprise-modules": _spec(
        "Módulos enterprise privados",
        "configuration_only",
        ("record_requirement", "delete_requirement", "dry_run"),
        "license_gate_closed",
    ),
    "human-agent-roster": _spec(
        "Escala humana",
        "hybrid",
        (
            "create_user",
            "update_user",
            "add_to_group",
            "remove_from_group",
            "takeover",
            "reassign",
        ),
        "klm_rbac_authoritative",
    ),
    "faq-management": _spec(
        "Gestão de FAQ",
        "upstream_crud",
        ("create", "update", "delete", "bulk_status", "bulk_delete"),
    ),
    "user-settings": _spec(
        "Preferências do usuário",
        "hybrid",
        ("update_profile", "set_preferences"),
        "identity_fields_restricted",
    ),
    "organization-settings": _spec(
        "Configurações da organização",
        "hybrid",
        ("update", "set_preferences"),
        "workspace_binding_immutable",
    ),
    "integration-settings": _spec(
        "Configurações de integrações",
        "configuration_only",
        ("save_config", "delete_config", "dry_run"),
        "external_dispatch_closed",
    ),
    "ai-provider-settings": _spec(
        "Configurações de provedores de IA",
        "configuration_only",
        ("save_config", "delete_config", "dry_run"),
        "external_dispatch_closed",
    ),
}

CHATTERMATE_NATIVE_CAPABILITY_KEYS = frozenset(CAPABILITY_SPECS)
_CONFIGURATION_ONLY = frozenset(
    {
        "jira-shopify",
        "external-ai-channels",
        "private-enterprise-modules",
        "integration-settings",
        "ai-provider-settings",
    }
)
_WORKFLOW_KEYS = frozenset(
    {
        "visual-editor",
        "nodes-forms-conditions",
        "nodes-form-action-end-transfer-landing-llm",
        "preview-execution",
        "draft-publish-version",
    }
)
_CLOSED_DISPATCH_COMMANDS = frozenset(
    {"connect", "dispatch", "enable", "execute", "install", "publish_external", "send"}
)
_EXTERNAL_NODE_TYPES = frozenset({"llm"})
_EXTERNAL_ACTION_TYPES = frozenset({"database", "email", "webhook"})
_LOCAL_ONLY_SNAPSHOT_KEYS = frozenset(
    {
        "file-upload",
        "jira-shopify",
        "structured-output",
        "external-ai-channels",
        "private-enterprise-modules",
    }
)
_SCOPE_AWARE_SNAPSHOT_KEYS = frozenset(
    {
        "agents-rbac",
        "mcp-tools-agent-association",
        "lead-capture-people",
        "knowledge-faq",
        "analytics",
        "user-settings",
        "organization-settings",
    }
)
_SCOPE_AWARE_EXECUTE_KEYS = frozenset(
    {
        "agents-rbac",
        "mcp-tools-agent-association",
        "lead-capture-people",
        "knowledge-faq",
        "widgets-help-center",
        "analytics",
        "organization-settings",
    }
)


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, UUID | Decimal | date | datetime | Enum):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, Mapping):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_jsonify(item) for item in value]
    if isinstance(value, bytes | bytearray):
        raise TypeError("Conteúdo binário não pode atravessar o adapter nativo.")
    if hasattr(value, "model_dump"):
        return _jsonify(value.model_dump())
    if is_dataclass(value):
        return _jsonify(asdict(value))
    raise TypeError(f"Valor não serializável no adapter ChatterMate: {type(value).__name__}.")


def _sanitize(value: Any) -> Any:
    return sanitize_oss_json(_jsonify(value))


def _storage_safe(value: Any) -> tuple[Any, list[str]]:
    """Drop credential-shaped fields before writing native OSS state."""

    omitted: list[str] = []

    def visit(item: Any, path: str) -> Any:
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                child_path = f"{path}.{key}"
                sanitized = sanitize_oss_json({str(key): _jsonify(child)})[str(key)]
                if sanitized == "[REDACTED]":
                    omitted.append(child_path)
                    if str(key).lower() == "session_id" and child not in (None, ""):
                        result["sessionReferenceHash"] = hashlib.sha256(
                            str(child).encode("utf-8")
                        ).hexdigest()[:24]
                    continue
                result[str(key)] = visit(child, child_path)
            return result
        if isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            return [visit(child, f"{path}[{index}]") for index, child in enumerate(item)]
        sanitized = sanitize_oss_json(_jsonify(item))
        if sanitized == "[REDACTED]":
            omitted.append(path)
            return None
        return sanitized

    return visit(value, "$"), omitted


def _validated_object(value: dict[str, Any], *, max_bytes: int, label: str) -> dict[str, Any]:
    normalized = _jsonify(value)
    if not isinstance(normalized, dict):
        raise TypeError(f"{label} deve ser um objeto JSON.")
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} excede o limite de {max_bytes} bytes.")
    return normalized


def _identifier(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ValueError(f"{field} inválido.")
    return result


def _uuid(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} deve ser UUID.") from error


def _integer_id(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} deve ser inteiro.") from error
    if result < 1:
        raise ValueError(f"{field} deve ser positivo.")
    return result


def _body(payload: dict[str, Any], *excluded: str) -> dict[str, Any]:
    nested = payload.get("data")
    if isinstance(nested, dict):
        return dict(nested)
    blocked = set(excluded) | {"dry_run", "approved", "matter_id", "thread_id"}
    return {key: value for key, value in payload.items() if key not in blocked}


def _serialize_entity(value: Any, fields: Sequence[str]) -> dict[str, Any]:
    return {
        field: _jsonify(getattr(value, field))
        for field in fields
        if getattr(value, field, None) is not None
    }


def _closed_graph_features(nodes: Sequence[dict[str, Any]]) -> list[str]:
    closed: set[str] = set()
    for node in nodes:
        node_type = str(node.get("node_type") or node.get("type") or "").lower()
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        action_type = str(
            node.get("action_type") or config.get("action_type") or config.get("actionType") or ""
        ).lower()
        if node_type in _EXTERNAL_NODE_TYPES:
            closed.add(node_type)
        if node_type == "action" and action_type in _EXTERNAL_ACTION_TYPES:
            closed.add(f"action:{action_type}")
    return sorted(closed)


class ChatterMateNativeService:
    """Expose all pinned ChatterMate capabilities through explicit native contracts."""

    CACHE_TTL_SECONDS = 8.0
    _snapshot_cache: ClassVar[dict[tuple[str, str, str], tuple[float, dict[str, Any]]]] = {}
    _snapshot_loads: ClassVar[dict[tuple[str, str, str], asyncio.Task[dict[str, Any]]]] = {}
    _cache_epoch: ClassVar[int] = 0

    def __init__(self, db: Any):
        self.db = db
        self.client: ChatterMateClient | Any | None = None
        self.chatter_operations: ChatterMateOperationsService | Any | None = None
        self.flow_service: FlowService | Any | None = None
        self.klm_operations: KlmOperationsService | Any | None = None
        self.agent_service: KlmAgentService | Any | None = None
        self.knowledge_service: KnowledgeService | Any | None = None
        self.workspace_scope_loader: (
            Callable[[UUID], Awaitable[ChatterMateWorkspaceScope]] | None
        ) = None
        self.agent_run_loader: Callable[[UUID, UUID], Awaitable[Any | None]] | None = None

    @classmethod
    def clear_cache(
        cls,
        workspace_id: UUID | None = None,
        capability_key: str | None = None,
    ) -> None:
        cls._cache_epoch += 1
        workspace = str(workspace_id) if workspace_id else None
        for key in list(cls._snapshot_cache):
            if workspace is not None and key[0] != workspace:
                continue
            if capability_key is not None and key[1] != capability_key:
                continue
            cls._snapshot_cache.pop(key, None)
        for key in list(cls._snapshot_loads):
            if workspace is not None and key[0] != workspace:
                continue
            if capability_key is not None and key[1] != capability_key:
                continue
            cls._snapshot_loads.pop(key, None)

    def _client(self) -> ChatterMateClient | Any:
        if self.client is None:
            self.client = ChatterMateClient()
        return self.client

    def _chatter_operations(self) -> ChatterMateOperationsService | Any:
        if self.chatter_operations is None:
            self.chatter_operations = ChatterMateOperationsService(self.db, client=self._client())
        return self.chatter_operations

    def _flows(self) -> FlowService | Any:
        if self.flow_service is None:
            self.flow_service = FlowService(self.db)
        return self.flow_service

    def _operations(self) -> KlmOperationsService | Any:
        if self.klm_operations is None:
            self.klm_operations = KlmOperationsService(self.db)
        return self.klm_operations

    def _agents(self) -> KlmAgentService | Any:
        if self.agent_service is None:
            self.agent_service = KlmAgentService(self.db)
        return self.agent_service

    def _knowledge(self) -> KnowledgeService | Any:
        if self.knowledge_service is None:
            self.knowledge_service = KnowledgeService(self.db)
        return self.knowledge_service

    @staticmethod
    def _spec(capability_key: str) -> CapabilitySpec:
        spec = CAPABILITY_SPECS.get(capability_key)
        if spec is None:
            raise UnknownChatterMateCapabilityError(
                f"Capacidade ChatterMate desconhecida: {capability_key}."
            )
        return spec

    @staticmethod
    def _cache_key(
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None = None,
    ) -> tuple[str, str, str]:
        encoded = json.dumps(
            {
                "query": query,
                "workspaceScope": scope.cache_fingerprint if scope else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return str(workspace_id), capability_key, hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _linked_ids(
        rows: Sequence[ExternalEntityLink],
        organization_id: str,
        external_entity_types: set[str],
    ) -> frozenset[str]:
        identifiers: set[str] = set()
        for row in rows:
            if row.external_account_id != organization_id:
                continue
            if row.external_entity_type not in external_entity_types:
                continue
            identifier = str(row.external_entity_id or "").strip()
            if not _IDENTIFIER_RE.fullmatch(identifier):
                raise ChatterMateWorkspaceIsolationError(
                    "Vínculo ChatterMate contém identificador remoto inválido."
                )
            identifiers.add(identifier)
        return frozenset(identifiers)

    async def _workspace_scope(self, workspace_id: UUID) -> ChatterMateWorkspaceScope:
        if self.workspace_scope_loader is not None:
            scope = await self.workspace_scope_loader(workspace_id)
            if scope.workspace_id != workspace_id:
                raise ChatterMateWorkspaceIsolationError(
                    "O resolvedor retornou um escopo de workspace incompatível."
                )
            return scope

        rows = list(
            await self.db.scalars(
                select(ExternalEntityLink).where(
                    ExternalEntityLink.workspace_id == workspace_id,
                    ExternalEntityLink.provider == OSSBridgeProvider.chattermate,
                    ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
                )
            )
        )
        organization_links = [
            row
            for row in rows
            if row.klm_entity_type == "workspace"
            and row.klm_entity_id == workspace_id
            and row.external_entity_type == "organization"
            and row.external_account_id == row.external_entity_id
        ]
        if len(organization_links) != 1:
            raise ChatterMateWorkspaceIsolationError(
                "Workspace sem vínculo organizacional ChatterMate único e sincronizado."
            )
        organization_id = _identifier(
            organization_links[0].external_account_id,
            "organization_id",
        )

        # Identity is the first remote read and only occurs after a persisted binding exists.
        identity = await self._client().identity()
        identity_organization_id = _identifier(
            identity.get("organization_id"),
            "organization_id",
        )
        if identity_organization_id != organization_id:
            raise ChatterMateWorkspaceIsolationError(
                "A organização autenticada no ChatterMate não corresponde ao workspace."
            )

        visible_foreign_bindings = list(
            await self.db.scalars(
                select(ExternalEntityLink).where(
                    ExternalEntityLink.workspace_id != workspace_id,
                    ExternalEntityLink.provider == OSSBridgeProvider.chattermate,
                    ExternalEntityLink.external_account_id == organization_id,
                    ExternalEntityLink.klm_entity_type == "workspace",
                    ExternalEntityLink.external_entity_type == "organization",
                    ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
                )
            )
        )
        if visible_foreign_bindings:
            raise ChatterMateWorkspaceIsolationError(
                "Organização ChatterMate vinculada de forma ambígua entre workspaces."
            )

        return ChatterMateWorkspaceScope(
            workspace_id=workspace_id,
            organization_id=organization_id,
            upstream_user_id=_identifier(identity.get("id"), "user_id"),
            agent_ids=self._linked_ids(rows, organization_id, {"agent", "workflow_agent"}),
            workflow_ids=self._linked_ids(rows, organization_id, {"workflow"}),
            person_ids=self._linked_ids(
                rows,
                organization_id,
                {"client", "customer", "person", "people"},
            ),
        )

    @staticmethod
    def _snapshot_requires_remote(capability_key: str) -> bool:
        return capability_key not in _LOCAL_ONLY_SNAPSHOT_KEYS

    @staticmethod
    def _command_requires_remote(
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> bool:
        if capability_key in _CONFIGURATION_ONLY:
            return False
        if capability_key in _WORKFLOW_KEYS:
            if command in {"clear_cache", "cancel_preview", "save_native_draft", "dry_run_node"}:
                return False
            if command == "preview" and payload.get("flow_id"):
                return False
            if command == "replace_graph":
                nodes = payload.get("nodes")
                if isinstance(nodes, list) and _closed_graph_features(nodes):
                    return False
            if command == "update_node" and _closed_graph_features([payload]):
                return False
            return True
        local_commands = {
            "lead-capture-people": {"link_klm_person"},
            "notifications-groups": {"save_notification_rule"},
            "ratings-feedback": {"create_rating", "link_rating"},
            "file-upload": {"register_file", "promote_to_rag", "remove_file"},
            "handoff-inbox-transfer": {"request_handoff", "close_handoff"},
            "structured-output": {"validate", "persist_contract"},
            "knowledge-faq": {"promote_to_rag"},
            "widgets-help-center": {"save_preview"},
            "analytics": {"save_metric_view"},
            "api-websocket": {"invalidate_session_cache", "dry_run_event"},
            "user-settings": {"set_preferences"},
            "organization-settings": {"set_preferences"},
        }
        return command not in local_commands.get(capability_key, set())

    @staticmethod
    def _require_scope(
        scope: ChatterMateWorkspaceScope | None,
    ) -> ChatterMateWorkspaceScope:
        if scope is None:
            raise ChatterMateWorkspaceIsolationError(
                "Operação remota ChatterMate sem escopo de workspace verificado."
            )
        return scope

    @staticmethod
    def _require_linked_agent(
        scope: ChatterMateWorkspaceScope,
        value: Any,
    ) -> str:
        agent_id = _identifier(value, "agent_id")
        if agent_id not in scope.agent_ids:
            raise ChatterMateWorkspaceIsolationError(
                "Agente ChatterMate não vinculado ao workspace atual."
            )
        return agent_id

    @staticmethod
    def _require_linked_workflow(
        scope: ChatterMateWorkspaceScope,
        value: Any,
    ) -> str:
        workflow_id = _identifier(value, "workflow_id")
        if workflow_id not in scope.workflow_ids:
            raise ChatterMateWorkspaceIsolationError(
                "Workflow ChatterMate não vinculado ao workspace atual."
            )
        return workflow_id

    @staticmethod
    def _require_linked_person(
        scope: ChatterMateWorkspaceScope,
        value: Any,
    ) -> str:
        person_id = _identifier(value, "customer_id")
        if scope.person_ids and person_id not in scope.person_ids:
            raise ChatterMateWorkspaceIsolationError(
                "Pessoa ChatterMate não vinculada ao workspace atual."
            )
        return person_id

    async def _linked_agents(
        self,
        scope: ChatterMateWorkspaceScope,
    ) -> list[dict[str, Any]]:
        rows = await self._client().list_agents()
        return [
            row
            for row in rows
            if isinstance(row, dict) and str(row.get("id") or "") in scope.agent_ids
        ]

    async def _linked_people(self, scope: ChatterMateWorkspaceScope) -> Any:
        document = await self._client().people()
        if not scope.person_ids:
            return document
        if isinstance(document, list):
            return [
                row
                for row in document
                if isinstance(row, dict) and str(row.get("id") or "") in scope.person_ids
            ]
        if not isinstance(document, dict):
            return document
        result = copy.deepcopy(document)
        for key in ("items", "people", "customers", "data"):
            rows = result.get(key)
            if isinstance(rows, list):
                result[key] = [
                    row
                    for row in rows
                    if isinstance(row, dict) and str(row.get("id") or "") in scope.person_ids
                ]
                if isinstance(result.get("total"), int):
                    result["total"] = len(result[key])
                break
        return result

    def _stage_native_resource_link(
        self,
        scope: ChatterMateWorkspaceScope,
        external_entity_type: str,
        external_entity_id: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        resource_id = _identifier(external_entity_id, f"{external_entity_type}_id")
        self.db.add(
            ExternalEntityLink(
                workspace_id=scope.workspace_id,
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type=f"native_{external_entity_type}",
                klm_entity_id=uuid5(
                    NAMESPACE_URL,
                    (
                        f"urn:klm:chattermate:{scope.workspace_id}:"
                        f"{external_entity_type}:{resource_id}"
                    ),
                ),
                external_account_id=scope.organization_id,
                external_entity_type=external_entity_type,
                external_entity_id=resource_id,
                link_metadata={
                    "managedBy": "oss-native-chattermate-adapter",
                    **(metadata or {}),
                },
                sync_state=ExternalEntitySyncState.synced,
            )
        )

    async def snapshot(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict,
    ) -> dict:
        """Return a sanitized, capability-specific native snapshot."""

        self._spec(capability_key)
        normalized_query = _validated_object(
            dict(query or {}), max_bytes=_MAX_QUERY_BYTES, label="query"
        )
        refresh = bool(normalized_query.pop("refresh", False))
        scope = (
            await self._workspace_scope(workspace_id)
            if self._snapshot_requires_remote(capability_key)
            else None
        )
        key = self._cache_key(workspace_id, capability_key, normalized_query, scope)
        cached = type(self)._snapshot_cache.get(key)
        now = time.monotonic()
        if not refresh and cached and cached[0] > now:
            result = copy.deepcopy(cached[1])
            result["cache"] = {"hit": True, "ttlSeconds": self.CACHE_TTL_SECONDS}
            return result

        task = type(self)._snapshot_loads.get(key)
        if task is None or task.done():
            cache_epoch = type(self)._cache_epoch
            task = asyncio.create_task(
                self._load_and_cache_snapshot(
                    key,
                    workspace_id,
                    capability_key,
                    normalized_query,
                    cache_epoch,
                    scope,
                )
            )
            type(self)._snapshot_loads[key] = task

            def discard(completed: asyncio.Task[dict[str, Any]]) -> None:
                if type(self)._snapshot_loads.get(key) is completed:
                    type(self)._snapshot_loads.pop(key, None)

            task.add_done_callback(discard)
        return copy.deepcopy(await asyncio.shield(task))

    async def _load_and_cache_snapshot(
        self,
        key: tuple[str, str, str],
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
        cache_epoch: int,
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        snapshot = await self._load_snapshot(workspace_id, capability_key, query, scope)
        if cache_epoch == type(self)._cache_epoch:
            type(self)._snapshot_cache[key] = (
                time.monotonic() + self.CACHE_TTL_SECONDS,
                copy.deepcopy(snapshot),
            )
        return snapshot

    async def _load_snapshot(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        spec = self._spec(capability_key)
        try:
            data = await self._snapshot_data(workspace_id, capability_key, query, scope)
            status = "ready"
        except asyncio.CancelledError:
            raise
        except (ChatterMateClientError, TimeoutError) as error:
            data = {
                "upstream": {
                    "status": "unavailable",
                    "httpStatus": getattr(error, "status_code", None),
                },
                "nativeState": await self._state_snapshot(workspace_id, capability_key, query),
            }
            status = "degraded"
        if not isinstance(data, dict) or not data:
            data = {"status": "empty", "items": [], "count": 0}
        return _sanitize(
            {
                "provider": _PROVIDER,
                "capabilityKey": capability_key,
                "title": spec.title,
                "mode": spec.mode,
                "status": status,
                "source": self._source_for(capability_key),
                "gate": {
                    "policy": spec.gate,
                    "externalDispatchAllowed": spec.external_dispatch,
                },
                "availableCommands": list(spec.commands),
                "actions": [
                    {
                        "command": command,
                        **(
                            {"mutates": True}
                            if capability_key == "agents-rbac" and command == "sync"
                            else {}
                        ),
                    }
                    for command in spec.commands
                ],
                "query": query,
                "data": data,
                "cache": {"hit": False, "ttlSeconds": self.CACHE_TTL_SECONDS},
            }
        )

    @staticmethod
    def _source_for(capability_key: str) -> str:
        if capability_key in _CONFIGURATION_ONLY:
            return "klm_persistent_state"
        if CAPABILITY_SPECS[capability_key].mode.startswith("upstream"):
            return "chattermate_api"
        return "chattermate_api+klm"

    async def _collect(
        self,
        calls: Mapping[str, Callable[[], Awaitable[Any]]],
    ) -> dict[str, Any]:
        names = list(calls)
        results = await asyncio.gather(
            *(calls[name]() for name in names),
            return_exceptions=True,
        )
        collected: dict[str, Any] = {}
        for name, result in zip(names, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                collected[name] = {
                    "status": "unavailable",
                    "httpStatus": getattr(result, "status_code", None),
                }
            else:
                collected[name] = {"status": "ready", "value": result}
        return collected

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        return await self._client()._request(  # noqa: SLF001 - adapter owns upstream routes
            method,
            path,
            params=params,
            json=payload,
            expected=expected,
        )

    async def _snapshot_data(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if capability_key in _WORKFLOW_KEYS:
            return await self._snapshot_workflows(
                workspace_id,
                capability_key,
                query,
                self._require_scope(scope),
            )
        handler_name = "_snapshot_" + capability_key.replace("-", "_")
        handler = getattr(self, handler_name, None)
        if handler is None:
            raise UnknownChatterMateCapabilityError(
                f"Snapshot nativo ausente para {capability_key}."
            )
        if capability_key in _SCOPE_AWARE_SNAPSHOT_KEYS:
            return await handler(workspace_id, query, self._require_scope(scope))
        return await handler(workspace_id, query)

    async def _snapshot_agents_rbac(
        self,
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        limit = max(1, min(int(query.get("limit", 100)), 200))

        async def identity() -> dict[str, str]:
            return {
                "id": scope.upstream_user_id,
                "organization_id": scope.organization_id,
            }

        return await self._collect(
            {
                "identity": identity,
                "upstreamAgents": lambda: self._linked_agents(scope),
                "users": lambda: self._request("GET", "/api/v1/users", params={"limit": limit}),
                "roles": lambda: self._request("GET", "/api/v1/roles"),
                "permissions": lambda: self._request("GET", "/api/v1/roles/permissions/all"),
                "klmRegistry": lambda: self._agents().registry(workspace_id),
            }
        )

    async def _snapshot_workflows(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        agents = await self._linked_agents(scope)
        agent_id = str(query.get("agent_id") or "").strip()
        requested_workflow_id = str(query.get("workflow_id") or "").strip()
        if agent_id:
            agent_id = self._require_linked_agent(scope, agent_id)
        if requested_workflow_id:
            requested_workflow_id = self._require_linked_workflow(
                scope, requested_workflow_id
            )

        workflow: dict[str, Any] | None = None
        candidates = [agent_id] if agent_id else [str(row.get("id") or "") for row in agents]
        for candidate_agent_id in candidates:
            if not candidate_agent_id:
                continue
            candidate = await self._client().workflow_for_agent(candidate_agent_id)
            candidate_workflow_id = str((candidate or {}).get("id") or "")
            if not candidate or candidate_workflow_id not in scope.workflow_ids:
                continue
            if requested_workflow_id and candidate_workflow_id != requested_workflow_id:
                continue
            workflow = candidate
            agent_id = candidate_agent_id
            break
        if requested_workflow_id and workflow is None:
            raise ChatterMateWorkspaceIsolationError(
                "Workflow não pertence a um agente vinculado ao workspace atual."
            )

        workflow_id = str((workflow or {}).get("id") or "").strip()
        graph = (
            await self._client().workflow_nodes(workflow_id)
            if workflow_id
            else {"nodes": [], "connections": []}
        )
        node_types: set[str] | None = None
        if capability_key == "nodes-forms-conditions":
            node_types = {"condition", "form", "guardrails", "message", "user_input", "wait"}
        elif capability_key == "nodes-form-action-end-transfer-landing-llm":
            node_types = {"action", "end", "form", "human_transfer", "landing_page", "llm"}
        filtered_nodes = [
            row
            for row in graph.get("nodes", [])
            if node_types is None
            or str(row.get("node_type") or row.get("type") or "").lower() in node_types
        ]
        result: dict[str, Any] = {
            "agentId": agent_id or None,
            "workflowId": workflow_id or None,
            "agents": agents,
            "workflow": workflow or {"status": "not_configured"},
            "graph": {**graph, "nodes": filtered_nodes},
            "closedDispatchFeatures": _closed_graph_features(graph.get("nodes", [])),
            "previewPolicy": {
                "authenticated": True,
                "externalEffects": 0,
                "llmDispatch": "closed",
            },
        }
        if capability_key in {"preview-execution", "draft-publish-version"}:
            local_flows = await self._flows().list_flows(workspace_id)
            result["klmFlows"] = [
                _serialize_entity(
                    flow,
                    (
                        "id",
                        "name",
                        "description",
                        "is_active",
                        "current_revision",
                        "current_revision_id",
                        "updated_at",
                    ),
                )
                for flow in local_flows
            ]
        result["nativeState"] = await self._state_snapshot(workspace_id, capability_key, query)
        return result

    async def _snapshot_mcp_tools_agent_association(
        self,
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        agent_id = str(query.get("agent_id") or "").strip()
        calls: dict[str, Callable[[], Awaitable[Any]]] = {
            "tools": self._client().mcp_tools,
            "agents": lambda: self._linked_agents(scope),
        }
        if agent_id:
            safe_agent_id = self._require_linked_agent(scope, agent_id)
            calls["associations"] = lambda: self._request(
                "GET", f"/api/v1/mcp-tools/agent/{safe_agent_id}"
            )
        result = await self._collect(calls)
        result["policy"] = {
            "allowedTransports": ["http", "sse", "stdio"],
            "network": "loopback_only",
        }
        return result

    async def _snapshot_lead_capture_people(
        self,
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        calls: dict[str, Callable[[], Awaitable[Any]]] = {
            "people": lambda: self._linked_people(scope),
            "stats": self._client().people_stats,
            "nativeLinks": lambda: self._state_snapshot(workspace_id, "lead-capture-people", query),
        }
        agent_id = str(query.get("agent_id") or "").strip()
        if agent_id:
            safe_agent_id = self._require_linked_agent(scope, agent_id)
            calls["leadCapture"] = lambda: self._request(
                "GET", f"/api/v1/agent/{safe_agent_id}/lead-capture"
            )
        return await self._collect(calls)

    async def _snapshot_notifications_groups(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._collect(
            {
                "groups": self._client().groups,
                "notifications": self._client().notifications,
                "unread": lambda: self._request("GET", "/api/v1/notifications/unread-count"),
                "rules": lambda: self._state_snapshot(workspace_id, "notifications-groups", query),
            }
        )

    async def _snapshot_ratings_feedback(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "upstreamRouteMounted": False,
            "upstreamEvidence": "app/api/rating.py exists but is not included by app/main.py",
            "analytics": await self._collect({"overview": lambda: self._client().analytics("30d")}),
            "nativeState": await self._state_snapshot(workspace_id, "ratings-feedback", query),
        }

    async def _snapshot_widget_apps(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._collect(
            {
                "apps": lambda: self._request("GET", "/api/v1/widget-apps"),
                "widgets": lambda: self._request("GET", "/api/v1/widgets"),
            }
        )

    async def _snapshot_file_upload(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "accepted": {
                "maxBytes": 10 * 1024 * 1024,
                "requiresExtractedText": True,
                "binaryTransport": "outside_native_json_contract",
            },
            "nativeState": await self._state_snapshot(workspace_id, "file-upload", query),
        }
        matter_id = query.get("matter_id")
        if matter_id:
            chunks = await self._knowledge().retrieve_relevant_chunks(
                workspace_id=workspace_id,
                matter_id=_uuid(matter_id, "matter_id"),
                query_text=str(query.get("search") or ""),
                context_mode="full",
                limit=max(1, min(int(query.get("limit", 20)), 50)),
            )
            result["ragChunks"] = [
                _serialize_entity(
                    chunk,
                    ("id", "matter_id", "source_type", "source_ref", "title", "summary", "tags"),
                )
                for chunk in chunks
            ]
        return result

    async def _snapshot_jira_shopify(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._configuration_snapshot(
            workspace_id,
            "jira-shopify",
            query,
            connectors=("jira", "shopify"),
        )

    async def _snapshot_handoff_inbox_transfer(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        limit = max(1, min(int(query.get("limit", 50)), 100))
        calls = await self._collect(
            {
                "inbox": lambda: self._request(
                    "GET", "/api/v1/chats/recent", params={"skip": 0, "limit": limit}
                ),
                "roster": lambda: self._request("GET", "/api/v1/users/team-overview"),
            }
        )
        waiting = await self._operations().list_threads(
            workspace_id, status=ThreadStatus.waiting_human, limit=limit
        )
        active = await self._operations().list_threads(
            workspace_id, status=ThreadStatus.human_active, limit=limit
        )
        calls["klmQueue"] = {
            "status": "ready",
            "value": {
                "waiting": [self._thread_row(row) for row in waiting],
                "humanActive": [self._thread_row(row) for row in active],
            },
        }
        calls["policy"] = {
            "requester": "hermes",
            "humanVeto": True,
            "customerDispatch": False,
        }
        return calls

    async def _snapshot_structured_output(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "defaultSchema": AGENT_RESPONSE_SCHEMA,
            "validator": "jsonschema-draft-2020-12",
            "contracts": await self._state_snapshot(workspace_id, "structured-output", query),
            "policy": {"additionalProperties": False, "dispatch": "none"},
        }

    async def _snapshot_knowledge_faq(
        self,
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        result = await self._collect(
            {
                "sources": lambda: self._client().knowledge(scope.organization_id),
                "queue": lambda: self._client().knowledge_queue(scope.organization_id),
                "processor": lambda: self._request("GET", "/api/v1/knowledge/processor/status"),
            }
        )
        matter_id = query.get("matter_id")
        if matter_id:
            chunks = await self._knowledge().retrieve_relevant_chunks(
                workspace_id=workspace_id,
                matter_id=_uuid(matter_id, "matter_id"),
                query_text=str(query.get("search") or ""),
                context_mode="full",
                limit=max(1, min(int(query.get("limit", 20)), 50)),
            )
            result["klmRag"] = {
                "status": "ready",
                "value": [
                    _serialize_entity(
                        row,
                        ("id", "source_type", "source_ref", "title", "summary", "tags"),
                    )
                    for row in chunks
                ],
            }
        return result

    async def _snapshot_widgets_help_center(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._collect(
            {
                "widgets": lambda: self._request("GET", "/api/v1/widgets"),
                "settings": lambda: self._request("GET", "/api/v1/help-center/settings"),
                "faqs": lambda: self._request(
                    "GET", "/api/v1/help-center/faqs", params={"page": 1, "page_size": 20}
                ),
                "preview": lambda: self._state_snapshot(workspace_id, "widgets-help-center", query),
            }
        )

    async def _snapshot_analytics(
        self,
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        time_range = str(query.get("time_range") or "7d")
        if time_range not in {"24h", "7d", "30d", "90d"}:
            raise ValueError("time_range inválido.")
        result = await self._collect(
            {
                "overview": lambda: self._client().analytics(time_range),
                "agentPerformance": lambda: self._client().agent_performance(time_range),
                "customers": lambda: self._request(
                    "GET", "/api/v1/analytics/customer-analytics", params={"time_range": time_range}
                ),
                "sentiment": lambda: self._request(
                    "GET", "/api/v1/analytics/sentiment", params={"time_range": time_range}
                ),
                "klmKpis": lambda: self._operations().kpis(workspace_id),
                "klmQuality": lambda: self._operations().quality(workspace_id),
                "views": lambda: self._state_snapshot(workspace_id, "analytics", query),
            }
        )
        performance = result.get("agentPerformance", {}).get("value")
        if isinstance(performance, dict) and isinstance(performance.get("agents"), list):
            performance["agents"] = [
                row
                for row in performance["agents"]
                if isinstance(row, dict) and str(row.get("id") or "") in scope.agent_ids
            ]
        return result

    async def _snapshot_api_websocket(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._collect(
            {
                "identity": self._client().identity,
                "recentChatsProbe": lambda: self._request(
                    "GET", "/api/v1/chats/recent", params={"skip": 0, "limit": 1}
                ),
                "activeSessions": lambda: self._request("GET", "/api/v1/active-sessions"),
                "nativeEvents": lambda: self._state_snapshot(workspace_id, "api-websocket", query),
            }
        ) | {
            "contracts": {
                "rest": "/api/v1",
                "widgetNamespace": "/widget",
                "transport": "loopback_authenticated",
                "externalDispatch": False,
            }
        }

    async def _snapshot_external_ai_channels(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._configuration_snapshot(
            workspace_id,
            "external-ai-channels",
            query,
            connectors=("llm", "email", "meta", "sms", "telegram", "voice"),
        )

    async def _snapshot_private_enterprise_modules(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "edition": "community",
            "license": "not_present",
            "dispatch": "closed",
            "requirements": await self._state_snapshot(
                workspace_id, "private-enterprise-modules", query
            ),
        }

    async def _snapshot_human_agent_roster(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self._collect(
            {
                "users": lambda: self._request("GET", "/api/v1/users", params={"limit": 200}),
                "overview": lambda: self._request("GET", "/api/v1/users/team-overview"),
                "groups": self._client().groups,
            }
        )
        waiting = await self._operations().list_threads(
            workspace_id, status=ThreadStatus.waiting_human, limit=100
        )
        result["klmWaiting"] = {
            "status": "ready",
            "value": [self._thread_row(row) for row in waiting],
        }
        return result

    async def _snapshot_faq_management(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        page = max(1, int(query.get("page", 1)))
        page_size = max(1, min(int(query.get("page_size", 50)), 100))
        return await self._collect(
            {
                "faqs": lambda: self._request(
                    "GET",
                    "/api/v1/help-center/faqs",
                    params={"page": page, "page_size": page_size},
                ),
                "categories": lambda: self._request("GET", "/api/v1/help-center/faqs/categories"),
            }
        )

    async def _snapshot_user_settings(
        self,
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        return await self._collect(
            {
                "identity": lambda: self._client().identity(),
                "profile": lambda: self._request(
                    "GET", f"/api/v1/users/{scope.upstream_user_id}"
                ),
                "preferences": lambda: self._state_snapshot(workspace_id, "user-settings", query),
            }
        )

    async def _snapshot_organization_settings(
        self,
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        return await self._collect(
            {
                "organization": lambda: self._request(
                    "GET", f"/api/v1/organizations/{scope.organization_id}"
                ),
                "setup": lambda: self._request("GET", "/api/v1/organizations/setup-status"),
                "stats": lambda: self._request(
                    "GET", f"/api/v1/organizations/{scope.organization_id}/stats"
                ),
                "preferences": lambda: self._state_snapshot(
                    workspace_id, "organization-settings", query
                ),
            }
        )

    async def _snapshot_integration_settings(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self._configuration_snapshot(
            workspace_id,
            "integration-settings",
            query,
            connectors=("email", "line", "meta", "slack", "sms", "telegram"),
        )
        upstream = await self._collect(
            {"configuredAccounts": lambda: self._request("GET", "/api/v1/channels/accounts")}
        )
        return {**result, "upstreamReadOnly": upstream}

    async def _snapshot_ai_provider_settings(
        self, workspace_id: UUID, query: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self._configuration_snapshot(
            workspace_id,
            "ai-provider-settings",
            query,
            connectors=("chattermate", "custom-model"),
        )
        result["providers"] = await self._collect(
            {"catalog": lambda: self._request("GET", "/api/v1/ai/providers")}
        )
        return result

    async def _configuration_snapshot(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
        *,
        connectors: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "connectors": [
                {"key": key, "configuration": "draft", "dispatch": "closed"} for key in connectors
            ],
            "nativeState": await self._state_snapshot(workspace_id, capability_key, query),
            "policy": {
                "externalDispatchAllowed": False,
                "allowedActions": ["save_config", "delete_config", "dry_run"],
                "secretsPersisted": False,
            },
        }

    @staticmethod
    def _thread_row(thread: Any) -> dict[str, Any]:
        return _serialize_entity(
            thread,
            (
                "id",
                "matter_id",
                "client_id",
                "assigned_human_id",
                "primary_channel",
                "status",
                "subject",
                "owner_agent_key",
                "score",
                "last_message_preview",
                "last_message_at",
                "thread_metadata",
            ),
        )

    def _state_service(self) -> Any:
        try:
            module = importlib.import_module("app.services.oss_native_state_service")
        except ModuleNotFoundError as error:
            if error.name != "app.services.oss_native_state_service":
                raise
            raise ChatterMateNativeStateUnavailableError(
                "oss_native_state_service ainda não está materializado."
            ) from error
        factory = next(
            (
                getattr(module, name)
                for name in (
                    "OSSNativeStateService",
                    "OssNativeStateService",
                    "NativeOSSStateService",
                )
                if getattr(module, name, None) is not None
            ),
            None,
        )
        if factory is None:
            raise ChatterMateNativeStateUnavailableError(
                "oss_native_state_service não expõe uma classe compatível."
            )
        return factory(self.db)

    async def _invoke_state(
        self,
        method_names: Sequence[str],
        *,
        workspace_id: UUID,
        capability_key: str,
        actor_user_id: UUID | None = None,
        command: str | None = None,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        service = self._state_service()
        method = next(
            (getattr(service, name) for name in method_names if hasattr(service, name)),
            None,
        )
        if method is None:
            raise ChatterMateNativeStateUnavailableError(
                f"Estado nativo não implementa {method_names[0]}."
            )
        values = {
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "provider": _PROVIDER,
            "service": _PROVIDER,
            "capability_key": capability_key,
            "capability": capability_key,
            "command": command,
            "payload": payload or {},
            "data": payload or {},
            "query": query or {},
        }
        signature = inspect.signature(method)
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        for name, parameter in signature.parameters.items():
            if name in {"self", "cls"}:
                continue
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                continue
            if name not in values:
                if parameter.default is inspect.Parameter.empty and not accepts_kwargs:
                    raise ChatterMateNativeStateUnavailableError(
                        f"Parâmetro desconhecido no estado nativo: {name}."
                    )
                continue
            if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
                args.append(values[name])
            else:
                kwargs[name] = values[name]
        if accepts_kwargs:
            for name in ("provider", "capability_key"):
                kwargs.setdefault(name, values[name])
        result = method(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def _state_snapshot(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            value = await self._invoke_state(
                ("get_snapshot", "snapshot", "read", "get_state", "list"),
                workspace_id=workspace_id,
                capability_key=capability_key,
                query=_sanitize(query),
            )
        except ChatterMateNativeStateUnavailableError:
            return {
                "status": "state_service_unavailable",
                "persistent": False,
                "records": [],
            }
        document = _jsonify(value)
        if isinstance(document, dict) and isinstance(document.get("state"), dict):
            state_row = document["state"]
            configuration = state_row.get("configuration")
            configuration = configuration if isinstance(configuration, dict) else {}
            adapter_state = configuration.get("adapterState")
            if not isinstance(adapter_state, dict):
                adapter_state = configuration.get("adapter_state")
            adapter_state = adapter_state if isinstance(adapter_state, dict) else {}
            collections = adapter_state.get("collections")
            collections = collections if isinstance(collections, dict) else {}
            records = [
                record
                for rows in collections.values()
                if isinstance(rows, list)
                for record in rows
                if isinstance(record, dict)
            ]
            requested_collection = str(query.get("collection") or "").strip()
            if requested_collection:
                records = [
                    record
                    for record in collections.get(requested_collection, [])
                    if isinstance(record, dict)
                ]
            return {
                "status": "ready",
                "persistent": True,
                "configured": bool(state_row.get("configured")),
                "configVersion": int(
                    state_row.get("config_version") or state_row.get("configVersion") or 0
                ),
                "executionMode": state_row.get("execution_mode")
                or state_row.get("executionMode")
                or "disabled",
                "state": adapter_state,
                "records": records,
                "recordCount": len(records),
            }
        return {
            "status": "ready",
            "persistent": True,
            "value": document if document not in (None, {}, []) else {"records": [], "count": 0},
        }

    @staticmethod
    def _state_collection(capability_key: str, command: str) -> str:
        if capability_key in _CONFIGURATION_ONLY:
            return "configurations"
        exact = {
            ("lead-capture-people", "link_klm_person"): "personLinks",
            ("notifications-groups", "save_notification_rule"): "notificationRules",
            ("ratings-feedback", "create_rating"): "ratings",
            ("ratings-feedback", "link_rating"): "ratingLinks",
            ("file-upload", "register_file"): "files",
            ("file-upload", "remove_file"): "files",
            ("structured-output", "persist_contract"): "contracts",
            ("widgets-help-center", "save_preview"): "previews",
            ("analytics", "save_metric_view"): "metricViews",
            ("draft-publish-version", "save_revision"): "workflowRevisions",
            ("api-websocket", "dry_run_event"): "eventDryRuns",
            ("user-settings", "set_preferences"): "preferences",
            ("organization-settings", "set_preferences"): "preferences",
        }
        if (capability_key, command) in exact:
            return exact[(capability_key, command)]
        if "draft" in command or capability_key in _WORKFLOW_KEYS:
            return "workflowDrafts"
        return "records"

    @staticmethod
    def _state_record_id(payload: dict[str, Any]) -> str:
        for field in (
            "id",
            "file_id",
            "rating_id",
            "workflow_id",
            "node_id",
            "person_id",
            "contract_id",
            "view_id",
            "filter_id",
            "source_ref",
            "connector",
            "module",
            "provider",
            "key",
            "name",
        ):
            if payload.get(field) not in (None, ""):
                return str(payload[field])[:180]
        if payload.get("sessionReferenceHash"):
            return str(payload["sessionReferenceHash"])
        return f"native-{uuid4()}"

    def _mutate_native_state(
        self,
        current: dict[str, Any],
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        state = copy.deepcopy(current) if isinstance(current, dict) else {}
        safe_payload, omitted = _storage_safe(payload)
        safe_payload = safe_payload if isinstance(safe_payload, dict) else {}
        collection = self._state_collection(capability_key, command)
        collections = state.get("collections")
        collections = copy.deepcopy(collections) if isinstance(collections, dict) else {}
        records = [
            dict(record) for record in collections.get(collection, []) if isinstance(record, dict)
        ]
        if collection == "preferences":
            record_id = "preferences"
        elif command == "save_revision":
            workflow_id = str(safe_payload.get("workflow_id") or "workflow")
            record_id = f"{workflow_id}:revision:{int(state.get('revision') or 0) + 1}"
        else:
            record_id = self._state_record_id(safe_payload)
        removed = 0

        delete_command = command in {"delete", "delete_config", "remove_file"}
        if delete_command:
            identifiers = {
                str(value)
                for key, value in safe_payload.items()
                if key == "id"
                or key.endswith("_id")
                or key in {"connector", "key", "module", "name", "provider", "source_ref"}
                if value not in (None, "")
            }
            if identifiers:
                retained: list[dict[str, Any]] = []
                for record in records:
                    values = {
                        str(value)
                        for key, value in record.items()
                        if key == "id"
                        or key.endswith("Id")
                        or key.endswith("_id")
                        or key
                        in {
                            "connector",
                            "key",
                            "module",
                            "name",
                            "provider",
                            "source_ref",
                        }
                        if value not in (None, "")
                    }
                    if values & identifiers:
                        removed += 1
                    else:
                        retained.append(record)
                records = retained
            else:
                removed = len(records)
                records = []
        else:
            record = {
                **safe_payload,
                "id": record_id,
                "nativeCommand": command,
                "dispatch": "closed",
                "revision": int(state.get("revision") or 0) + 1,
            }
            replaced = False
            for index, existing in enumerate(records):
                if str(existing.get("id")) == record_id:
                    records[index] = {**existing, **record}
                    replaced = True
                    break
            if not replaced:
                records.append(record)
            records = records[-200:]

        collections[collection] = records
        revision = int(state.get("revision") or 0) + 1
        state.update(
            {
                "schemaVersion": 1,
                "revision": revision,
                "collections": collections,
                "lastCommand": {
                    "command": command,
                    "collection": collection,
                    "recordId": record_id,
                    "dispatch": "closed",
                },
            }
        )
        history = [row for row in state.get("history", []) if isinstance(row, dict)]
        history.append(
            {
                "revision": revision,
                "command": command,
                "collection": collection,
                "recordId": record_id,
                "removed": removed,
            }
        )
        state["history"] = history[-100:]
        mutation = {
            "revision": revision,
            "collection": collection,
            "recordId": record_id,
            "storedCount": len(records),
            "removedCount": removed,
            "omittedSensitiveFields": omitted,
            "externalDispatchExecuted": False,
        }
        return state, mutation

    async def _state_execute_via_config(
        self,
        service: Any,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        last_conflict: BaseException | None = None
        for _attempt in range(2):
            raw_snapshot = await service.get_snapshot(workspace_id, _PROVIDER, capability_key)
            document = _jsonify(raw_snapshot)
            state_row = document.get("state", {}) if isinstance(document, dict) else {}
            configuration = state_row.get("configuration")
            configuration = dict(configuration) if isinstance(configuration, dict) else {}
            current = configuration.get("adapterState")
            if not isinstance(current, dict):
                current = configuration.get("adapter_state")
            next_state, mutation = self._mutate_native_state(
                current if isinstance(current, dict) else {},
                capability_key,
                command,
                payload,
            )
            schemas = importlib.import_module("app.schemas.oss_native")
            models = importlib.import_module("app.models.oss_native")
            patch = schemas.OSSNativeConfigPatch(
                expected_version=int(
                    state_row.get("config_version") or state_row.get("configVersion") or 0
                ),
                configuration={**configuration, "adapterState": next_state},
                execution_mode=models.OSSNativeExecutionMode.local_native,
            )
            try:
                saved = await service.update_config(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    provider=_PROVIDER,
                    capability=capability_key,
                    data=patch,
                )
            except BaseException as error:
                if error.__class__.__name__ != "OSSNativeVersionConflictError":
                    raise
                last_conflict = error
                continue
            saved_document = _jsonify(saved)
            return {
                "source": "klm_persistent_state",
                "persistent": True,
                "dispatch": "closed",
                "mutation": mutation,
                "state": next_state,
                "snapshot": saved_document,
            }
        assert last_conflict is not None
        raise last_conflict

    async def _state_execute(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        service = self._state_service()
        if callable(getattr(service, "get_snapshot", None)) and callable(
            getattr(service, "update_config", None)
        ):
            return await self._state_execute_via_config(
                service,
                workspace_id,
                actor_user_id,
                capability_key,
                command,
                payload,
            )
        value = await self._invoke_state(
            ("execute", "mutate", "write", "upsert"),
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            capability_key=capability_key,
            command=command,
            payload=_sanitize(payload),
        )
        return {
            "source": "klm_persistent_state",
            "persistent": True,
            "result": value if value not in (None, {}, []) else {"status": "stored"},
        }

    @classmethod
    async def validate_native_configuration(
        cls,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        cls._spec(capability_key)
        normalized_payload = _validated_object(
            dict(payload or {}), max_bytes=_MAX_PAYLOAD_BYTES, label="payload"
        )
        configuration = normalized_payload.get("configuration")
        if configuration is not None and not isinstance(configuration, dict):
            raise TypeError("configuration deve ser um objeto.")
        return _sanitize(
            {
                "provider": _PROVIDER,
                "workspaceId": workspace_id,
                "actorUserId": actor_user_id,
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
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict,
        *,
        mode: str,
    ) -> dict[str, Any]:
        """Validate the concrete ChatterMate command contract without dispatch."""

        spec = self._spec(capability_key)
        normalized_command = str(command or "").strip().lower()
        if mode not in {"execute", "dry-run", "configure"}:
            raise ValueError("Modo de validação ChatterMate inválido.")
        if normalized_command == "__native_config__":
            return await self.validate_native_configuration(
                workspace_id, actor_user_id, capability_key, dict(payload or {})
            )
        if not _COMMAND_RE.fullmatch(normalized_command):
            raise UnsupportedChatterMateCommandError("Comando ChatterMate inválido.")
        if normalized_command not in spec.commands:
            raise UnsupportedChatterMateCommandError(
                f"{capability_key} não suporta o comando {normalized_command}."
            )
        normalized_payload = _validated_object(
            dict(payload or {}), max_bytes=_MAX_PAYLOAD_BYTES, label="payload"
        )
        if capability_key in _CONFIGURATION_ONLY and (
            normalized_command in _CLOSED_DISPATCH_COMMANDS
            or bool(normalized_payload.get("dispatch"))
        ):
            raise ChatterMateNativeGateError(
                f"Dispatch externo permanece fechado para {capability_key}."
            )
        return _sanitize(
            {
                "provider": _PROVIDER,
                "workspaceId": workspace_id,
                "actorUserId": actor_user_id,
                "capabilityKey": capability_key,
                "command": normalized_command,
                "mode": mode,
                "status": "validated",
                "contract": spec.mode,
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
        """Execute one reviewed local command and return a sanitized result."""

        spec = self._spec(capability_key)
        normalized_command = str(command or "").strip().lower()
        if not _COMMAND_RE.fullmatch(normalized_command):
            raise UnsupportedChatterMateCommandError("Comando ChatterMate inválido.")
        if normalized_command not in spec.commands:
            raise UnsupportedChatterMateCommandError(
                f"{capability_key} não suporta o comando {normalized_command}."
            )
        normalized_payload = _validated_object(
            dict(payload or {}), max_bytes=_MAX_PAYLOAD_BYTES, label="payload"
        )
        await self._audit(
            workspace_id,
            actor_user_id,
            capability_key,
            normalized_command,
            "requested",
            normalized_payload,
        )
        try:
            scope = (
                await self._workspace_scope(workspace_id)
                if self._command_requires_remote(
                    capability_key,
                    normalized_command,
                    normalized_payload,
                )
                else None
            )
            result = await self._execute_data(
                workspace_id,
                actor_user_id,
                capability_key,
                normalized_command,
                normalized_payload,
                scope,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._audit(
                    workspace_id,
                    actor_user_id,
                    capability_key,
                    normalized_command,
                    "cancelled",
                    normalized_payload,
                )
            )
            raise
        except BaseException:
            await self._audit(
                workspace_id,
                actor_user_id,
                capability_key,
                normalized_command,
                "failed",
                normalized_payload,
            )
            raise
        self.clear_cache(workspace_id, capability_key)
        await self._audit(
            workspace_id,
            actor_user_id,
            capability_key,
            normalized_command,
            "completed",
            normalized_payload,
        )
        return _sanitize(
            {
                "provider": _PROVIDER,
                "capabilityKey": capability_key,
                "command": normalized_command,
                "mode": spec.mode,
                "status": "completed",
                "gate": spec.gate,
                "externalDispatchAllowed": False,
                "data": result if result not in (None, {}, []) else {"status": "completed"},
            }
        )

    async def _audit(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        phase: str,
        payload: dict[str, Any],
    ) -> None:
        safe_payload = _sanitize(payload)
        await self._operations().record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type=f"chattermate_native_{phase}",
            entity_type="oss_native_capability",
            entity_id=None,
            summary=f"ChatterMate {capability_key}: {command} ({phase}).",
            correlation_id=f"chattermate:{capability_key}:{command}"[:120],
            after_data={
                "capabilityKey": capability_key,
                "command": command,
                "phase": phase,
                "payload": safe_payload,
            },
        )
        await self.db.commit()

    async def _execute_data(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if capability_key in _CONFIGURATION_ONLY:
            return await self._execute_configuration_only(
                workspace_id, actor_user_id, capability_key, command, payload
            )
        if capability_key in _WORKFLOW_KEYS:
            return await self._execute_workflow(
                workspace_id,
                actor_user_id,
                capability_key,
                command,
                payload,
                scope,
            )
        handler_name = "_execute_" + capability_key.replace("-", "_")
        handler = getattr(self, handler_name, None)
        if handler is None:
            raise UnsupportedChatterMateCommandError(
                f"Executor nativo ausente para {capability_key}."
            )
        if capability_key in _SCOPE_AWARE_EXECUTE_KEYS:
            return await handler(workspace_id, actor_user_id, command, payload, scope)
        return await handler(workspace_id, actor_user_id, command, payload)

    async def _execute_configuration_only(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command in _CLOSED_DISPATCH_COMMANDS or bool(payload.get("dispatch")):
            raise ChatterMateNativeGateError(
                f"Dispatch externo permanece fechado para {capability_key}."
            )
        if command == "dry_run":
            return {
                "source": "klm_dry_run",
                "valid": True,
                "dispatch": "closed",
                "configuration": _sanitize(payload),
            }
        state_command = {
            "record_requirement": "upsert",
            "delete_requirement": "delete",
            "save_config": "upsert",
            "delete_config": "delete",
        }.get(command, command)
        return await self._state_execute(
            workspace_id,
            actor_user_id,
            capability_key,
            state_command,
            payload,
        )

    async def _execute_agents_rbac(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None = None,
    ) -> dict[str, Any]:
        if command == "sync":
            result = await self._chatter_operations().sync_agents(workspace_id, actor_user_id)
            return {"source": "chattermate_api+klm", "sync": result}
        if command == "create_agent":
            verified_scope = self._require_scope(scope)
            result = await self._client().create_agent(_body(payload))
            self._stage_native_resource_link(
                verified_scope,
                "agent",
                result.get("id") if isinstance(result, dict) else None,
            )
        elif command == "update_agent":
            agent_id = self._require_linked_agent(
                self._require_scope(scope), payload.get("agent_id")
            )
            data = _body(payload, "agent_id")
            if not data:
                raise ValueError("Nenhuma configuração de agente foi informada.")
            result = await self._client().update_agent(agent_id, data)
        elif command == "configure_agent":
            return await self._configure_agent(payload, self._require_scope(scope))
        elif command in {"create_role", "update_role", "delete_role"}:
            role_id = (
                _integer_id(payload.get("role_id"), "role_id") if command != "create_role" else None
            )
            if command == "create_role":
                result = await self._request("POST", "/api/v1/roles", payload=_body(payload))
            elif command == "update_role":
                result = await self._request(
                    "PUT", f"/api/v1/roles/{role_id}", payload=_body(payload, "role_id")
                )
            else:
                result = await self._request("DELETE", f"/api/v1/roles/{role_id}", expected=(204,))
        elif command in {"grant_permission", "revoke_permission"}:
            role_id = _integer_id(payload.get("role_id"), "role_id")
            permission = _identifier(payload.get("permission"), "permission")
            method = "POST" if command == "grant_permission" else "DELETE"
            result = await self._request(
                method, f"/api/v1/roles/{role_id}/permissions/{permission}"
            )
        elif command in {"create_user", "update_user", "delete_user"}:
            user_id = (
                _identifier(payload.get("user_id"), "user_id") if command != "create_user" else None
            )
            if command == "create_user":
                result = await self._request("POST", "/api/v1/users", payload=_body(payload))
            elif command == "update_user":
                result = await self._request(
                    "PUT", f"/api/v1/users/{user_id}", payload=_body(payload, "user_id")
                )
            else:
                result = await self._request("DELETE", f"/api/v1/users/{user_id}", expected=(204,))
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    @staticmethod
    def _mcp_tool_ids(value: Any) -> set[int]:
        rows = value
        if isinstance(value, dict):
            rows = value.get("mcp_tools") or value.get("tools") or value.get("items") or []
        if not isinstance(rows, list):
            raise TypeError("Lista de ferramentas MCP inválida.")
        identifiers: set[int] = set()
        for row in rows:
            raw_id = row.get("id") if isinstance(row, dict) else row
            identifiers.add(_integer_id(raw_id, "tool_id"))
        return identifiers

    async def _configure_agent(
        self,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        agent_id = self._require_linked_agent(scope, payload.get("agent_id"))
        data = _body(payload, "agent_id")
        tools_supplied = "tools" in data
        requested_tools = data.pop("tools", None)
        if "workflow_id" in data:
            workflow_id = data.pop("workflow_id")
            data["active_workflow_id"] = (
                self._require_linked_workflow(scope, workflow_id)
                if workflow_id not in (None, "")
                else None
            )

        agent_result: Any = {"id": agent_id, "unchanged": True}
        if data:
            agent_result = await self._client().update_agent(agent_id, data)

        associations: dict[str, Any] = {
            "requested": tools_supplied,
            "added": [],
            "removed": [],
        }
        if tools_supplied:
            desired = self._mcp_tool_ids(requested_tools)
            for tool_id in sorted(desired):
                await self._approved_mcp_tool(tool_id)
            current_document = await self._request("GET", f"/api/v1/mcp-tools/agent/{agent_id}")
            current = self._mcp_tool_ids(current_document)
            for tool_id in sorted(desired - current):
                await self._request(
                    "POST",
                    "/api/v1/mcp-tools/agent-association",
                    payload={"mcp_tool_id": tool_id, "agent_id": agent_id},
                )
                associations["added"].append(tool_id)
            for tool_id in sorted(current - desired):
                await self._request(
                    "DELETE",
                    f"/api/v1/mcp-tools/agent-association/{tool_id}/{agent_id}",
                )
                associations["removed"].append(tool_id)
            associations["desired"] = sorted(desired)

        if not data and not tools_supplied:
            raise ValueError("Nenhuma configuração de agente foi informada.")
        return {
            "source": "chattermate_api",
            "localMutationApplied": True,
            "result": agent_result,
            "toolAssociations": associations,
        }

    async def _approved_mcp_tool(self, tool_id: int) -> dict[str, Any]:
        tools = await self._client().mcp_tools()
        tool = next(
            (row for row in tools if isinstance(row, dict) and int(row.get("id") or 0) == tool_id),
            None,
        )
        if tool is None:
            raise LookupError(f"Ferramenta MCP {tool_id} não encontrada no ChatterMate.")
        self._validate_mcp_transport(tool)
        return tool

    @staticmethod
    def _upstream_graph_payload(
        workflow_id: str,
        nodes: Sequence[dict[str, Any]],
        connections: Sequence[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        normalized_nodes: list[dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                raise TypeError("Cada nó do workflow deve ser um objeto JSON.")
            row = dict(node)
            node_type = str(row.get("node_type") or row.get("type") or "").strip().lower()
            if not node_type:
                raise ValueError("node_type é obrigatório em todos os nós.")
            row["node_type"] = node_type
            row.setdefault("workflow_id", workflow_id)
            row.setdefault("name", node_type.replace("_", " ").title())
            row.setdefault("config", {})
            normalized_nodes.append(row)

        normalized_connections: list[dict[str, Any]] = []
        for connection in connections:
            if not isinstance(connection, dict):
                raise TypeError("Cada conexão do workflow deve ser um objeto JSON.")
            row = dict(connection)
            row.setdefault("workflow_id", workflow_id)
            normalized_connections.append(row)
        return normalized_nodes, normalized_connections

    async def _execute_workflow(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if command == "clear_cache":
            self.clear_cache(workspace_id)
            return {"source": "klm_cache", "cleared": True}
        if command == "preview":
            return await self._execute_preview(workspace_id, payload, scope)
        if command == "cancel_preview":
            run_id = payload.get("run_id")
            if run_id:
                run = await self._flows().cancel_flow_run(_uuid(run_id, "run_id"), workspace_id)
                await self.db.commit()
                return {
                    "source": "klm_flow_runtime",
                    "cancelled": run is not None,
                    "runId": str(run_id),
                }
            return await self._state_execute(
                workspace_id,
                actor_user_id,
                capability_key,
                "cancel_preview",
                payload,
            )
        if command in {"save_native_draft", "dry_run_node"}:
            return await self._state_execute(
                workspace_id,
                actor_user_id,
                capability_key,
                command,
                {**payload, "dispatch": False, "dry_run": True},
            )

        workflow_id = (
            _identifier(payload.get("workflow_id"), "workflow_id")
            if command not in {"create_workflow"}
            else None
        )
        if command == "create_workflow":
            verified_scope = self._require_scope(scope)
            data = _body(payload)
            data["agent_id"] = self._require_linked_agent(
                verified_scope, data.get("agent_id")
            )
            if str(data.get("status") or "draft").lower() == "published":
                raise ChatterMateNativeGateError(
                    "Workflow deve ser criado como rascunho e publicado após inspeção do grafo."
                )
            result = await self._client().create_workflow(data)
            self._stage_native_resource_link(
                verified_scope,
                "workflow",
                result.get("id") if isinstance(result, dict) else None,
                metadata={"agentId": data["agent_id"]},
            )
        elif command == "update_workflow":
            workflow_id = self._require_linked_workflow(
                self._require_scope(scope), workflow_id
            )
            data = _body(payload, "workflow_id")
            if str(data.get("status") or "").lower() == "published":
                raise ChatterMateNativeGateError("Use o comando publish para publicar.")
            result = await self._client().update_workflow(workflow_id, data)
        elif command == "save_revision":
            workflow_id = self._require_linked_workflow(
                self._require_scope(scope), workflow_id
            )
            data = _body(payload, "workflow_id")
            if str(data.get("status") or "").lower() == "published":
                raise ChatterMateNativeGateError("Use o comando publish para publicar.")
            result = await self._client().update_workflow(workflow_id, data)
            version = await self._state_execute(
                workspace_id,
                actor_user_id,
                capability_key,
                "save_revision",
                {
                    **payload,
                    "upstreamWorkflow": result,
                    "dispatch": False,
                },
            )
            return {
                "source": "chattermate_api+klm_persistent_state",
                "localMutationApplied": True,
                "result": result,
                "version": version,
            }
        elif command == "delete_workflow":
            workflow_id = self._require_linked_workflow(
                self._require_scope(scope), workflow_id
            )
            result = await self._request(
                "DELETE", f"/api/v1/workflow/{workflow_id}", expected=(204,)
            )
        elif command == "replace_graph":
            nodes = payload.get("nodes")
            connections = payload.get("connections")
            if not isinstance(nodes, list) or not isinstance(connections, list):
                raise ValueError("nodes e connections devem ser listas.")
            closed = _closed_graph_features(nodes)
            if closed:
                if not payload.get("dry_run"):
                    raise ChatterMateNativeGateError(
                        "Nós externos exigem dry_run e não podem ser publicados."
                    )
                return await self._state_execute(
                    workspace_id,
                    actor_user_id,
                    capability_key,
                    "save_gated_graph_draft",
                    {
                        "workflow_id": workflow_id,
                        "nodes": nodes,
                        "connections": connections,
                        "closedFeatures": closed,
                        "dispatch": False,
                    },
                )
            workflow_id = self._require_linked_workflow(
                self._require_scope(scope), workflow_id
            )
            normalized_nodes, normalized_connections = self._upstream_graph_payload(
                workflow_id,
                nodes,
                connections,
            )
            result = await self._client().replace_workflow_nodes(
                workflow_id,
                nodes=normalized_nodes,
                connections=normalized_connections,
            )
        elif command == "update_node":
            node_id = _identifier(payload.get("node_id"), "node_id")
            data = _body(payload, "workflow_id", "node_id")
            closed = _closed_graph_features([data])
            if closed:
                if not payload.get("dry_run"):
                    raise ChatterMateNativeGateError("Nó externo exige dry_run.")
                return await self._state_execute(
                    workspace_id,
                    actor_user_id,
                    capability_key,
                    "save_gated_node_draft",
                    {**payload, "closedFeatures": closed, "dispatch": False},
                )
            workflow_id = self._require_linked_workflow(
                self._require_scope(scope), workflow_id
            )
            result = await self._request(
                "PUT",
                f"/api/v1/workflow/{workflow_id}/nodes/{node_id}",
                payload=data,
            )
        elif command in {"publish", "archive", "restore_draft"}:
            workflow_id = self._require_linked_workflow(
                self._require_scope(scope), workflow_id
            )
            if command == "publish":
                graph = await self._client().workflow_nodes(workflow_id)
                closed = _closed_graph_features(graph.get("nodes", []))
                if closed:
                    raise ChatterMateNativeGateError(
                        "Publicação bloqueada: o grafo contém dispatch externo ou LLM."
                    )
                status = "published"
            elif command == "archive":
                status = "archived"
            else:
                status = "draft"
            result = await self._client().update_workflow(workflow_id, {"status": status})
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    async def _execute_preview(
        self,
        workspace_id: UUID,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if payload.get("flow_id"):
            result = await self._flows().dry_run_flow(
                _uuid(payload["flow_id"], "flow_id"),
                _uuid(payload.get("matter_id"), "matter_id"),
                workspace_id,
                context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
                max_steps=max(1, min(int(payload.get("max_steps", 100)), 200)),
                revision_number=(int(payload["revision"]) if payload.get("revision") else None),
            )
            return {
                "source": "klm_flow_runtime",
                "authenticated": True,
                "externalActionsExecuted": 0,
                "result": result,
            }
        workflow_id = self._require_linked_workflow(
            self._require_scope(scope), payload.get("workflow_id")
        )
        graph = await self._client().workflow_nodes(workflow_id)
        trace = self._preview_upstream_graph(
            graph.get("nodes", []),
            graph.get("connections", []),
            payload.get("context") if isinstance(payload.get("context"), dict) else {},
        )
        return {
            "source": "klm_chattermate_simulator",
            "authenticated": True,
            "externalActionsExecuted": 0,
            "result": trace,
        }

    @staticmethod
    def _preview_upstream_graph(
        nodes: Sequence[dict[str, Any]],
        connections: Sequence[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        nodes_by_id = {str(row.get("id")): row for row in nodes if row.get("id")}
        incoming = {
            str(row.get("target_node_id") or row.get("target"))
            for row in connections
            if row.get("target_node_id") or row.get("target")
        }
        current = next((node_id for node_id in nodes_by_id if node_id not in incoming), None)
        trace: list[dict[str, Any]] = []
        visited: set[str] = set()
        status = "completed"
        while current and current not in visited and len(trace) < 100:
            visited.add(current)
            node = nodes_by_id[current]
            node_type = str(node.get("node_type") or node.get("type") or "unknown").lower()
            entry = {
                "nodeId": current,
                "type": node_type,
                "status": "simulated",
            }
            if node_type in _EXTERNAL_NODE_TYPES:
                entry["status"] = "gated"
                entry["dispatch"] = "closed"
            if node_type == "human_transfer":
                entry["status"] = "waiting_human"
                status = "waiting_human"
                trace.append(entry)
                break
            if node_type == "condition":
                config = node.get("config") if isinstance(node.get("config"), dict) else {}
                field = str(config.get("field") or "")
                expected = config.get("value")
                matched = str(context.get(field, "")).lower() == str(expected or "").lower()
                entry["branch"] = "true" if matched else "false"
            trace.append(entry)
            outgoing = [
                row
                for row in connections
                if str(row.get("source_node_id") or row.get("source") or "") == current
            ]
            current = (
                str(
                    (outgoing[0].get("target_node_id") or outgoing[0].get("target"))
                    if outgoing
                    else ""
                )
                or None
            )
        if current in visited:
            status = "cycle_detected"
        return {"status": status, "trace": trace, "context": context}

    async def _execute_mcp_tools_agent_association(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if command == "create_tool":
            data = _body(payload, "tool_id")
            self._validate_mcp_transport(data)
            result = await self._request("POST", "/api/v1/mcp-tools", payload=data)
        elif command == "update_tool":
            tool_id = _integer_id(payload.get("tool_id"), "tool_id")
            current = await self._approved_mcp_tool(tool_id)
            data = _body(payload, "tool_id")
            self._validate_mcp_transport({**current, **data})
            result = await self._request("PUT", f"/api/v1/mcp-tools/{tool_id}", payload=data)
        elif command == "delete_tool":
            tool_id = _integer_id(payload.get("tool_id"), "tool_id")
            result = await self._request("DELETE", f"/api/v1/mcp-tools/{tool_id}")
        elif command in {"associate", "dissociate"}:
            tool_id = _integer_id(payload.get("tool_id"), "tool_id")
            agent_id = self._require_linked_agent(
                self._require_scope(scope), payload.get("agent_id")
            )
            if command == "associate":
                await self._approved_mcp_tool(tool_id)
                result = await self._request(
                    "POST",
                    "/api/v1/mcp-tools/agent-association",
                    payload={"mcp_tool_id": tool_id, "agent_id": agent_id},
                )
            else:
                result = await self._request(
                    "DELETE", f"/api/v1/mcp-tools/agent-association/{tool_id}/{agent_id}"
                )
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    @staticmethod
    def _validate_mcp_transport(data: dict[str, Any]) -> None:
        transport = str(data.get("transport_type") or "").lower()
        if transport not in {"http", "sse", "stdio"}:
            raise ValueError("transport_type MCP inválido ou ausente.")
        if transport in {"http", "sse"}:
            parsed = urlparse(str(data.get("url") or ""))
            if parsed.scheme != "http" or parsed.hostname not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                raise ChatterMateNativeGateError("MCP HTTP/SSE deve usar origem loopback local.")
        if transport == "stdio":
            command = str(data.get("command") or "")
            if command not in {"node", "npx", "python", "python3", "uv", "uvx"}:
                raise ChatterMateNativeGateError("Comando MCP stdio não aprovado.")

    async def _execute_lead_capture_people(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if command == "update_lead_capture":
            agent_id = self._require_linked_agent(
                self._require_scope(scope), payload.get("agent_id")
            )
            result = await self._request(
                "PUT",
                f"/api/v1/agent/{agent_id}/lead-capture",
                payload=_body(payload, "agent_id"),
            )
            source = "chattermate_api"
        elif command == "mark_customer":
            customer_id = self._require_linked_person(
                self._require_scope(scope), payload.get("customer_id")
            )
            result = await self._request("POST", f"/api/v1/people/{customer_id}/mark-customer")
            source = "chattermate_api"
        elif command == "link_klm_person":
            return await self._state_execute(
                workspace_id, actor_user_id, "lead-capture-people", command, payload
            )
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": source, "localMutationApplied": True, "result": result}

    async def _execute_notifications_groups(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "save_notification_rule":
            return await self._state_execute(
                workspace_id, actor_user_id, "notifications-groups", command, payload
            )
        if command in {"create_group", "update_group", "delete_group"}:
            group_id = (
                _identifier(payload.get("group_id"), "group_id")
                if command != "create_group"
                else None
            )
            if command == "create_group":
                result = await self._request("POST", "/api/v1/groups", payload=_body(payload))
            elif command == "update_group":
                result = await self._request(
                    "PUT", f"/api/v1/groups/{group_id}", payload=_body(payload, "group_id")
                )
            else:
                result = await self._request(
                    "DELETE", f"/api/v1/groups/{group_id}", expected=(204,)
                )
        elif command in {"add_user", "remove_user"}:
            group_id = _identifier(payload.get("group_id"), "group_id")
            user_id = _identifier(payload.get("user_id"), "user_id")
            method = "POST" if command == "add_user" else "DELETE"
            result = await self._request(method, f"/api/v1/groups/{group_id}/users/{user_id}")
        elif command == "mark_notification_read":
            notification_id = _integer_id(payload.get("notification_id"), "notification_id")
            result = await self._request("PATCH", f"/api/v1/notifications/{notification_id}/read")
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    async def _execute_ratings_feedback(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "create_rating":
            rating = int(payload.get("rating", 0))
            if not 1 <= rating <= 5:
                raise ValueError("rating deve estar entre 1 e 5.")
            payload = {
                **payload,
                "session_id": _identifier(payload.get("session_id"), "session_id"),
                "rating": rating,
                "feedback": str(payload.get("feedback") or "")[:2000] or None,
                "upstreamRouteMounted": False,
            }
        return await self._state_execute(
            workspace_id,
            actor_user_id,
            "ratings-feedback",
            command,
            payload,
        )

    async def _execute_widget_apps(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        app_id = _identifier(payload.get("app_id"), "app_id") if command != "create_app" else None
        if command == "create_app":
            result = await self._request(
                "POST", "/api/v1/widget-apps", payload=_body(payload), expected=(201,)
            )
        elif command == "update_app":
            result = await self._request(
                "PATCH", f"/api/v1/widget-apps/{app_id}", payload=_body(payload, "app_id")
            )
        elif command == "delete_app":
            result = await self._request("DELETE", f"/api/v1/widget-apps/{app_id}", expected=(204,))
        elif command == "regenerate_key":
            result = await self._request("POST", f"/api/v1/widget-apps/{app_id}/regenerate-key")
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    async def _execute_file_upload(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command in {"register_file", "remove_file"}:
            if "content" in payload and not isinstance(payload.get("content"), str):
                raise TypeError("Arquivo binário deve ser tratado antes do adapter nativo.")
            return await self._state_execute(
                workspace_id, actor_user_id, "file-upload", command, payload
            )
        return await self._promote_to_rag(workspace_id, actor_user_id, "chattermate_file", payload)

    async def _execute_handoff_inbox_transfer(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        thread_id = (
            _uuid(payload.get("thread_id"), "thread_id") if payload.get("thread_id") else None
        )
        upstream_result: Any = None
        if command == "request_handoff":
            if thread_id is None:
                raise ValueError("thread_id é obrigatório para handoff.")
            declared_requester = str(
                payload.get("requested_by_agent_key") or "orquestrador"
            ).lower()
            if declared_requester != "orquestrador":
                raise ChatterMateNativeGateError(
                    "A solicitação de handoff deve ser emitida pelo Orquestrador verificado."
                )
            run_id = _uuid(payload.get("agent_run_id"), "agent_run_id")
            thread_before = await self._operations().get_thread(workspace_id, thread_id)
            if thread_before is None:
                raise LookupError("Thread KLM não encontrada.")
            run = await self._verified_owner_bridge_run(
                workspace_id,
                thread_id,
                thread_before.matter_id,
                run_id,
            )
            governance = dict(run.input_payload["governance"])
            thread_metadata = dict(thread_before.thread_metadata or {})
            thread_metadata["handoffGovernance"] = {
                "sourceAgentRunId": str(run.id),
                "policyVersion": governance["policy_version"],
                "policyDigestAlgorithm": governance["policy_digest_algorithm"],
                "policyDigest": governance["policy_digest"],
                "hierarchyPath": list(governance["hierarchy_path"]),
            }
            thread = await self._operations().update_thread(
                workspace_id,
                thread_id,
                actor_user_id,
                OmnichannelThreadUpdate(
                    status=ThreadStatus.waiting_human,
                    owner_agent_key=None,
                    assigned_human_id=actor_user_id,
                    queue="human",
                    thread_metadata={
                        **thread_metadata,
                        "handoffSource": "klm_agent_governance",
                        "handoffReason": str(
                            payload.get("reason") or "Revisão humana solicitada"
                        )[:500],
                    },
                ),
            )
        elif command in {"takeover", "reassign"}:
            session_id = _identifier(payload.get("session_id"), "session_id")
            if command == "takeover":
                upstream_result = await self._request(
                    "POST", f"/api/v1/sessions/{session_id}/takeover"
                )
                assigned_human = actor_user_id
            else:
                assigned_human = _uuid(payload.get("to_user_id"), "to_user_id")
                upstream_result = await self._request(
                    "POST",
                    f"/api/v1/sessions/{session_id}/reassign",
                    params={"to_user_id": str(assigned_human)},
                )
            thread = (
                await self._operations().update_thread(
                    workspace_id,
                    thread_id,
                    actor_user_id,
                    OmnichannelThreadUpdate(
                        status=ThreadStatus.human_active,
                        assigned_human_id=assigned_human,
                    ),
                )
                if thread_id
                else None
            )
        elif command == "close_handoff":
            if thread_id is None:
                raise ValueError("thread_id é obrigatório para encerrar handoff.")
            thread = await self._operations().update_thread(
                workspace_id,
                thread_id,
                actor_user_id,
                OmnichannelThreadUpdate(
                    status=ThreadStatus.open,
                    assigned_human_id=None,
                ),
            )
        else:
            raise UnsupportedChatterMateCommandError(command)
        if thread_id and thread is None:
            raise LookupError("Thread KLM não encontrada.")
        await self.db.commit()
        return {
            "source": "chattermate_api+klm" if upstream_result is not None else "klm",
            "thread": self._thread_row(thread) if thread is not None else None,
            "upstream": upstream_result,
            "humanVetoPreserved": True,
        }

    async def _verified_owner_bridge_run(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        matter_id: UUID | None,
        run_id: UUID,
    ) -> AgentRun | Any:
        run = (
            await self.agent_run_loader(workspace_id, run_id)
            if self.agent_run_loader is not None
            else await self.db.scalar(
                select(AgentRun).where(
                    AgentRun.id == run_id,
                    AgentRun.workspace_id == workspace_id,
                )
            )
        )
        if run is None:
            raise ChatterMateNativeGateError(
                "O handoff exige uma execução persistida e verificável do Hermes."
            )

        run_status = getattr(run.status, "value", run.status)
        input_payload = run.input_payload if isinstance(run.input_payload, dict) else {}
        output_payload = run.output_payload if isinstance(run.output_payload, dict) else {}
        governance = input_payload.get("governance")
        current_policy = AgentGovernanceService.policy_snapshot()
        expected = {
            "target_agent_key": "orquestrador",
            "initiator_type": "human",
            "requested_by_agent_key": None,
            "delegation_depth": 0,
            "hierarchy_path": ["orquestrador"],
            "workspace_id": str(workspace_id),
            "policy_version": current_policy["version"],
            "policy_digest_algorithm": current_policy["policyDigestAlgorithm"],
            "policy_digest": current_policy["policyDigest"],
        }
        governance_valid = isinstance(governance, dict) and all(
            governance.get(key) == value for key, value in expected.items()
        )
        run_matches_thread = (
            str(run.thread_id or "") == str(thread_id)
            and matter_id is not None
            and run.matter_id == matter_id
        )
        approval_recorded = bool(output_payload.get("approvalId")) and (
            output_payload.get("status") == AgentRunStatus.waiting_human.value
        )
        if not (
            run.agent_name == "orquestrador"
            and run_status == AgentRunStatus.waiting_human.value
            and input_payload.get("requestHuman") is True
            and governance_valid
            and run_matches_thread
            and approval_recorded
        ):
            raise ChatterMateNativeGateError(
                "O handoff não possui uma execução válida do Orquestrador para esta conversa."
            )
        return run

    async def _execute_structured_output(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "persist_contract":
            schema = payload.get("schema")
            if not isinstance(schema, dict):
                raise ValueError("schema é obrigatório.")
            validate_response_schema(schema, contains_sensitive_data=False)
            return await self._state_execute(
                workspace_id, actor_user_id, "structured-output", command, payload
            )
        schema = payload.get("schema") or AGENT_RESPONSE_SCHEMA
        if not isinstance(schema, dict):
            raise ValueError("schema deve ser objeto JSON Schema.")
        validate_response_schema(schema, contains_sensitive_data=False)
        output = payload.get("output")
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        validated = parse_and_validate(text, schema)
        return {
            "source": "klm_structured_output",
            "valid": True,
            "output": validated,
        }

    async def _execute_knowledge_faq(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if command == "promote_to_rag":
            return await self._promote_to_rag(
                workspace_id, actor_user_id, "chattermate_knowledge", payload
            )
        if command == "add_text":
            verified_scope = self._require_scope(scope)
            body = _body(payload)
            title = str(body.pop("title", None) or body.pop("name", None) or "").strip()
            content = str(
                body.pop("content", None)
                or body.pop("text", None)
                or body.pop("content_text", None)
                or ""
            ).strip()
            if not title or not content:
                raise ValueError("title/name e content/text são obrigatórios.")
            body["title"] = title[:255]
            body["content"] = content[:200_000]
            supplied_org_id = body.pop("organization_id", body.get("org_id"))
            if supplied_org_id not in (None, "", verified_scope.organization_id):
                raise ChatterMateWorkspaceIsolationError(
                    "organization_id do payload não corresponde ao workspace atual."
                )
            body["org_id"] = verified_scope.organization_id
            result = await self._request("POST", "/api/v1/knowledge/add/text", payload=body)
        elif command in {"link_agent", "unlink_agent"}:
            knowledge_id = _integer_id(payload.get("knowledge_id"), "knowledge_id")
            agent_id = self._require_linked_agent(
                self._require_scope(scope), payload.get("agent_id")
            )
            method = "POST" if command == "link_agent" else "DELETE"
            path = (
                "/api/v1/knowledge/link" if command == "link_agent" else "/api/v1/knowledge/unlink"
            )
            result = await self._request(
                method,
                path,
                params={"knowledge_id": knowledge_id, "agent_id": agent_id},
            )
        elif command == "delete":
            knowledge_id = _integer_id(payload.get("knowledge_id"), "knowledge_id")
            result = await self._request("DELETE", f"/api/v1/knowledge/{knowledge_id}")
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    async def _promote_to_rag(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        source_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        content = str(payload.get("content_text") or payload.get("content") or "").strip()
        if not content:
            raise ValueError("content_text extraído é obrigatório para promoção ao RAG.")
        chunk = await self._knowledge().upsert_chunk(
            workspace_id=workspace_id,
            matter_id=_uuid(payload.get("matter_id"), "matter_id"),
            source_type=source_type,
            source_ref=str(payload.get("source_ref") or "")[:120] or None,
            content_text=content[:200_000],
            title=str(payload.get("title") or "Conteúdo ChatterMate")[:255],
            summary=str(payload.get("summary") or content[:240])[:1000],
            tags=[str(item)[:80] for item in payload.get("tags", []) if str(item).strip()][:30],
            metadata=_sanitize(payload.get("metadata") or {}),
            rank_hint=max(0, min(int(payload.get("rank_hint", 3)), 10)),
            created_by_user_id=actor_user_id,
        )
        await self.db.commit()
        return {
            "source": "klm_rag",
            "persistent": True,
            "chunk": _serialize_entity(
                chunk,
                ("id", "matter_id", "source_type", "source_ref", "title", "summary", "tags"),
            ),
        }

    async def _execute_widgets_help_center(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if command in {"save_preview"}:
            return await self._state_execute(
                workspace_id, actor_user_id, "widgets-help-center", command, payload
            )
        if command == "create_widget":
            data = _body(payload)
            if data.get("is_active") is True:
                raise ChatterMateNativeGateError(
                    "Widget público deve permanecer inativo até aprovação de publicação."
                )
            data.pop("is_active", None)
            data = {key: value for key, value in data.items() if key in {"agent_id", "name"}}
            data["agent_id"] = self._require_linked_agent(
                self._require_scope(scope), data.get("agent_id")
            )
            if not str(data.get("name") or "").strip():
                raise ValueError("name é obrigatório para criar o widget local.")
            result = await self._request("POST", "/api/v1/widgets", payload=data)
        elif command == "delete_widget":
            widget_id = _identifier(payload.get("widget_id"), "widget_id")
            result = await self._request("DELETE", f"/api/v1/widgets/{widget_id}")
        elif command == "end_chat":
            widget_id = _identifier(payload.get("widget_id"), "widget_id")
            session_id = _identifier(payload.get("session_id"), "session_id")
            params = {"session_id": session_id}
            if payload.get("reason") not in (None, ""):
                params["reason"] = str(payload["reason"])[:120]
            if payload.get("description") not in (None, ""):
                params["description"] = str(payload["description"])[:1000]
            result = await self._request(
                "POST",
                f"/api/v1/widgets/{widget_id}/end-chat",
                params=params,
            )
        elif command == "update_help_center":
            data = _body(payload)
            activating = {
                key
                for key in (
                    "ai_search_enabled",
                    "auto_generate",
                    "chat_widget_enabled",
                    "enabled",
                )
                if data.get(key) is True
            }
            if activating:
                raise ChatterMateNativeGateError(
                    "Ativação externa do help center permanece fechada: "
                    + ", ".join(sorted(activating))
                )
            result = await self._request("PUT", "/api/v1/help-center/settings", payload=data)
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    async def _execute_analytics(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if command == "refresh":
            self.clear_cache(workspace_id, "analytics")
            data = await self._snapshot_analytics(
                workspace_id,
                payload,
                self._require_scope(scope),
            )
            return {"source": "chattermate_api+klm", "refreshed": True, "snapshot": data}
        return await self._state_execute(workspace_id, actor_user_id, "analytics", command, payload)

    async def _execute_api_websocket(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "invalidate_session_cache":
            client = self._client()
            cache_key = client._cache_key()  # noqa: SLF001 - explicit local session control
            type(client)._clear_shared_session(cache_key)  # noqa: SLF001
            client._access_token = None  # noqa: SLF001
            client._identity = None  # noqa: SLF001
            self.clear_cache(workspace_id)
            return {"source": "chattermate_session", "invalidated": True}
        if command == "probe":
            identity = await self._client().identity()
            chats = await self._request(
                "GET", "/api/v1/chats/recent", params={"skip": 0, "limit": 1}
            )
            return {"source": "chattermate_api", "identity": identity, "recentChats": chats}
        return await self._state_execute(
            workspace_id,
            actor_user_id,
            "api-websocket",
            "dry_run_event",
            {**payload, "dispatch": False},
        )

    async def _execute_human_agent_roster(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        aliases = {
            "add_to_group": "add_user",
            "remove_from_group": "remove_user",
        }
        if command in {"takeover", "reassign"}:
            return await self._execute_handoff_inbox_transfer(
                workspace_id, actor_user_id, command, payload
            )
        if command in {"add_to_group", "remove_from_group"}:
            return await self._execute_notifications_groups(
                workspace_id, actor_user_id, aliases[command], payload
            )
        return await self._execute_agents_rbac(workspace_id, actor_user_id, command, payload)

    async def _execute_faq_management(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "create":
            result = await self._request(
                "POST", "/api/v1/help-center/faqs", payload=_body(payload), expected=(201,)
            )
        elif command == "update":
            faq_id = _identifier(payload.get("faq_id"), "faq_id")
            result = await self._request(
                "PUT", f"/api/v1/help-center/faqs/{faq_id}", payload=_body(payload, "faq_id")
            )
        elif command == "delete":
            faq_id = _identifier(payload.get("faq_id"), "faq_id")
            result = await self._request(
                "DELETE", f"/api/v1/help-center/faqs/{faq_id}", expected=(204,)
            )
        elif command in {"bulk_status", "bulk_delete"}:
            suffix = "bulk-status" if command == "bulk_status" else "bulk-delete"
            result = await self._request(
                "POST", f"/api/v1/help-center/faqs/{suffix}", payload=_body(payload)
            )
        else:
            raise UnsupportedChatterMateCommandError(command)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    async def _execute_user_settings(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "set_preferences":
            return await self._state_execute(
                workspace_id, actor_user_id, "user-settings", command, payload
            )
        allowed = {"full_name", "is_online", "language", "timezone"}
        data = {key: value for key, value in _body(payload).items() if key in allowed}
        if not data:
            raise ValueError("Nenhum campo de perfil permitido foi informado.")
        result = await self._request("PATCH", "/api/v1/users/me", payload=data)
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}

    async def _execute_organization_settings(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        command: str,
        payload: dict[str, Any],
        scope: ChatterMateWorkspaceScope | None,
    ) -> dict[str, Any]:
        if command == "set_preferences":
            return await self._state_execute(
                workspace_id, actor_user_id, "organization-settings", command, payload
            )
        organization_id = self._require_scope(scope).organization_id
        allowed = {"name", "display_name", "timezone", "language"}
        data = {key: value for key, value in _body(payload).items() if key in allowed}
        if not data:
            raise ValueError("Nenhum campo organizacional permitido foi informado.")
        result = await self._request(
            "PATCH", f"/api/v1/organizations/{organization_id}", payload=data
        )
        return {"source": "chattermate_api", "localMutationApplied": True, "result": result}
