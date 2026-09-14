"""Focused contract tests for the native ChatterMate adapter."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.agent_run import AgentRunStatus
from app.models.klm_operations import ThreadStatus
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.agent_governance_service import AgentGovernanceService
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_native_chattermate_service import (
    CAPABILITY_SPECS,
    CHATTERMATE_NATIVE_CAPABILITY_KEYS,
    ChatterMateNativeGateError,
    ChatterMateNativeService,
    ChatterMateWorkspaceIsolationError,
    ChatterMateWorkspaceScope,
    UnknownChatterMateCapabilityError,
    UnsupportedChatterMateCommandError,
)

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UUID("22222222-2222-4222-8222-222222222222")
THREAD_ID = UUID("33333333-3333-4333-8333-333333333333")
MATTER_ID = UUID("44444444-4444-4444-8444-444444444444")
RUN_ID = UUID("55555555-5555-4555-8555-555555555555")


class FakeDB:
    def __init__(self) -> None:
        self.commits = 0
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1


class FakeNativeStateService:
    """The concrete get_snapshot/update_config contract used by KLM."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.updates: list[dict[str, Any]] = []

    async def get_snapshot(
        self,
        workspace_id: UUID,
        provider: str,
        capability: str,
        *,
        command_limit: int = 25,
        audit_limit: int = 50,
    ) -> dict[str, Any]:
        del command_limit, audit_limit
        key = (str(workspace_id), capability)
        row = self.rows.get(key, {"configuration": {}, "config_version": 0})
        return {
            "state": {
                "workspace_id": str(workspace_id),
                "provider": provider,
                "capability": capability,
                "configured": bool(row["config_version"]),
                "configuration": copy.deepcopy(row["configuration"]),
                "config_version": row["config_version"],
                "execution_mode": "local_native" if row["config_version"] else "disabled",
            },
            "commands": [],
            "audit_trail": [],
        }

    async def update_config(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        provider: str,
        capability: str,
        data: Any,
    ) -> dict[str, Any]:
        key = (str(workspace_id), capability)
        current = self.rows.get(key, {"configuration": {}, "config_version": 0})
        assert data.expected_version == current["config_version"]
        row = {
            "configuration": copy.deepcopy(data.configuration),
            "config_version": current["config_version"] + 1,
        }
        self.rows[key] = row
        self.updates.append(
            {
                "workspace_id": workspace_id,
                "actor_user_id": actor_user_id,
                "provider": provider,
                "capability": capability,
                "data": data,
            }
        )
        return await self.get_snapshot(workspace_id, provider, capability)


class FakeChatterMateClient:
    _cleared_sessions: list[str] = []

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.identity_calls = 0
        self._access_token = "local-access"
        self._identity: dict[str, Any] | None = None
        self.agents = [
            {
                "id": "agent-1",
                "name": "Hermes",
                "tools": [{"id": 7, "name": "KLM local"}],
                "use_workflow": True,
            }
        ]
        self.people_document: dict[str, Any] = {
            "items": [{"id": "person-1", "name": "Lead local"}],
            "total": 1,
        }
        self.graphs = {
            "workflow-1": {
                "nodes": [
                    {
                        "id": "node-start",
                        "node_type": "message",
                        "config": {"message": "Olá"},
                    },
                    {"id": "node-end", "node_type": "end", "config": {}},
                ],
                "connections": [
                    {
                        "source_node_id": "node-start",
                        "target_node_id": "node-end",
                    }
                ],
            }
        }

    async def identity(self) -> dict[str, Any]:
        self.identity_calls += 1
        return {
            "id": "user-1",
            "organization_id": "org-1",
            "email": "admin@example.test",
            "api_key": "sk-0123456789abcdef",
        }

    async def list_agents(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.agents)

    async def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"operation": "create_agent", "data": copy.deepcopy(data)})
        return {"id": "agent-created", **data}

    async def update_agent(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(
            {"operation": "update_agent", "agent_id": agent_id, "data": copy.deepcopy(data)}
        )
        return {"id": agent_id, **data}

    async def workflow_for_agent(self, agent_id: str) -> dict[str, Any]:
        workflow_id = UPSTREAM_WORKFLOW_ID if agent_id == UPSTREAM_AGENT_ID else "workflow-1"
        return {"id": workflow_id, "agent_id": agent_id, "status": "draft"}

    async def create_workflow(self, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"operation": "create_workflow", "data": copy.deepcopy(data)})
        return {"id": "workflow-created", **data}

    async def update_workflow(self, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(
            {
                "operation": "update_workflow",
                "workflow_id": workflow_id,
                "data": copy.deepcopy(data),
            }
        )
        return {"id": workflow_id, **data}

    async def workflow_nodes(self, workflow_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.graphs.get(workflow_id, {"nodes": [], "connections": []}))

    async def replace_workflow_nodes(
        self,
        workflow_id: str,
        *,
        nodes: list[dict[str, Any]],
        connections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "operation": "replace_workflow_nodes",
                "workflow_id": workflow_id,
                "nodes": copy.deepcopy(nodes),
                "connections": copy.deepcopy(connections),
            }
        )
        self.graphs[workflow_id] = {
            "nodes": copy.deepcopy(nodes),
            "connections": copy.deepcopy(connections),
        }
        return {"workflow_id": workflow_id, "node_count": len(nodes)}

    async def analytics(self, time_range: str) -> dict[str, Any]:
        return {"time_range": time_range, "conversations": 14}

    async def agent_performance(self, time_range: str) -> dict[str, Any]:
        return {"time_range": time_range, "agents": [{"id": "agent-1", "score": 4.9}]}

    async def people(self) -> dict[str, Any]:
        return copy.deepcopy(self.people_document)

    async def people_stats(self) -> dict[str, Any]:
        return {"total": 1, "customers": 0}

    async def mcp_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "id": 7,
                "name": "KLM local",
                "transport_type": "http",
                "url": "http://127.0.0.1:8765/mcp",
            },
            {
                "id": 9,
                "name": "KLM jurídico",
                "transport_type": "stdio",
                "command": "python3",
            },
        ]

    async def groups(self) -> list[dict[str, Any]]:
        return [{"id": "group-1", "name": "Jurídico"}]

    async def notifications(self) -> list[dict[str, Any]]:
        return [{"id": 1, "message": "Handoff aguardando"}]

    async def knowledge(self, organization_id: str) -> dict[str, Any]:
        return {"organization_id": organization_id, "items": [{"id": 1, "name": "FAQ"}]}

    async def knowledge_queue(self, organization_id: str) -> dict[str, Any]:
        return {"organization_id": organization_id, "pending": 0}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        call = {
            "operation": "request",
            "method": method,
            "path": path,
            "params": copy.deepcopy(params),
            "json": copy.deepcopy(json),
            "expected": expected,
        }
        self.calls.append(call)
        if method == "GET" and path.startswith("/api/v1/mcp-tools/agent/"):
            return {
                "id": path.rsplit("/", 1)[-1],
                "name": "Hermes",
                "mcp_tools": [{"id": 7, "name": "KLM local"}],
            }
        if path.endswith("/regenerate-key"):
            return {"id": "widget-app-1", "api_key": "sk-0123456789abcdef"}
        if method == "DELETE":
            return {"deleted": True, "path": path}
        return {
            "id": "upstream-1",
            "path": path,
            "items": [{"id": "upstream-1", "name": "Registro local"}],
            "payload": copy.deepcopy(json) if json is not None else {},
        }

    def _cache_key(self) -> str:
        return "fake-chattermate-session"

    @classmethod
    def _clear_shared_session(cls, cache_key: str) -> None:
        cls._cleared_sessions.append(cache_key)


class FakeChatterOperations:
    async def sync_agents(self, workspace_id: UUID, actor_user_id: UUID) -> dict[str, Any]:
        return {
            "workspace_id": str(workspace_id),
            "actor_user_id": str(actor_user_id),
            "synced": 1,
        }


class FakeFlowService:
    def __init__(self) -> None:
        self.cancelled: list[UUID] = []

    async def list_flows(self, workspace_id: UUID) -> list[Any]:
        return [
            SimpleNamespace(
                id=uuid4(),
                name="Triagem negativa",
                description="Fluxo local",
                is_active=True,
                current_revision=3,
                current_revision_id=uuid4(),
            )
        ]

    async def dry_run_flow(
        self,
        flow_id: UUID,
        matter_id: UUID,
        workspace_id: UUID,
        *,
        context: dict[str, Any],
        max_steps: int,
        revision_number: int | None,
    ) -> dict[str, Any]:
        return {
            "flow_id": str(flow_id),
            "matter_id": str(matter_id),
            "workspace_id": str(workspace_id),
            "context": context,
            "max_steps": max_steps,
            "revision_number": revision_number,
            "status": "completed",
        }

    async def cancel_flow_run(self, run_id: UUID, workspace_id: UUID) -> Any:
        del workspace_id
        self.cancelled.append(run_id)
        return SimpleNamespace(id=run_id, status="cancelled")


class FakeKlmOperations:
    def __init__(self) -> None:
        self.audits: list[dict[str, Any]] = []
        self.thread = SimpleNamespace(
            id=THREAD_ID,
            matter_id=MATTER_ID,
            client_id=uuid4(),
            assigned_human_id=None,
            primary_channel="whatsapp",
            status=ThreadStatus.open,
            subject="Negativa de medicamento",
            owner_agent_key="sdr",
            score=88,
            last_message_preview="Preciso do medicamento com urgência",
            thread_metadata={"source": "google_ads"},
        )

    async def record_audit(self, **kwargs: Any) -> Any:
        self.audits.append(copy.deepcopy(kwargs))
        return SimpleNamespace(id=uuid4(), **kwargs)

    async def list_threads(
        self,
        workspace_id: UUID,
        *,
        status: ThreadStatus,
        limit: int,
    ) -> list[Any]:
        del workspace_id, limit
        return [self.thread] if self.thread.status == status else []

    async def get_thread(self, workspace_id: UUID, thread_id: UUID) -> Any | None:
        del workspace_id
        return self.thread if thread_id == self.thread.id else None

    async def update_thread(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        data: Any,
    ) -> Any:
        del workspace_id, actor_user_id
        if thread_id != self.thread.id:
            return None
        changes = data.model_dump(exclude_unset=True)
        for key, value in changes.items():
            setattr(self.thread, key, value)
        return self.thread

    async def kpis(self, workspace_id: UUID) -> dict[str, Any]:
        return {"workspace_id": str(workspace_id), "conversion_rate": 42.5}

    async def quality(self, workspace_id: UUID) -> dict[str, Any]:
        return {"workspace_id": str(workspace_id), "sla_compliance": 98.2}


class FakeAgentService:
    async def registry(self, workspace_id: UUID) -> dict[str, Any]:
        return {"workspace_id": str(workspace_id), "agents": [{"key": "hermes"}]}


class FakeKnowledgeService:
    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []

    async def retrieve_relevant_chunks(self, **kwargs: Any) -> list[Any]:
        return [
            SimpleNamespace(
                id=uuid4(),
                matter_id=kwargs["matter_id"],
                source_type="document",
                source_ref="doc-1",
                title="Negativa",
                summary="Resumo indexado",
                tags=["saude"],
            )
        ]

    async def upsert_chunk(self, **kwargs: Any) -> Any:
        self.upserts.append(copy.deepcopy(kwargs))
        return SimpleNamespace(
            id=uuid4(),
            matter_id=kwargs["matter_id"],
            source_type=kwargs["source_type"],
            source_ref=kwargs["source_ref"],
            title=kwargs["title"],
            summary=kwargs["summary"],
            tags=kwargs["tags"],
        )


@pytest.fixture
def harness() -> SimpleNamespace:
    ChatterMateNativeService.clear_cache()
    db = FakeDB()
    client = FakeChatterMateClient()
    state = FakeNativeStateService()
    flows = FakeFlowService()
    operations = FakeKlmOperations()
    knowledge = FakeKnowledgeService()
    service = ChatterMateNativeService(db)
    service.client = client
    service.chatter_operations = FakeChatterOperations()
    service.flow_service = flows
    service.klm_operations = operations
    service.agent_service = FakeAgentService()
    service.knowledge_service = knowledge
    service._state_service = lambda: state  # type: ignore[method-assign]

    async def workspace_scope(workspace_id: UUID) -> ChatterMateWorkspaceScope:
        return ChatterMateWorkspaceScope(
            workspace_id=workspace_id,
            organization_id="org-1",
            upstream_user_id="user-1",
            agent_ids=frozenset({"agent-1", UPSTREAM_AGENT_ID}),
            workflow_ids=frozenset({"workflow-1", UPSTREAM_WORKFLOW_ID}),
            person_ids=frozenset({"person-1", UPSTREAM_USER_ID}),
        )

    service.workspace_scope_loader = workspace_scope

    async def agent_run_loader(workspace_id: UUID, run_id: UUID) -> Any | None:
        if workspace_id != WORKSPACE_ID or run_id != RUN_ID:
            return None
        policy = AgentGovernanceService.policy_snapshot()
        return SimpleNamespace(
            id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            matter_id=MATTER_ID,
            thread_id=str(THREAD_ID),
            agent_name="orquestrador",
            status=AgentRunStatus.waiting_human,
            input_payload={
                "requestHuman": True,
                "governance": {
                    "target_agent_key": "orquestrador",
                    "initiator_type": "human",
                    "requested_by_agent_key": None,
                    "delegation_depth": 0,
                    "hierarchy_path": ["orquestrador"],
                    "workspace_id": str(WORKSPACE_ID),
                    "policy_version": policy["version"],
                    "policy_digest_algorithm": policy["policyDigestAlgorithm"],
                    "policy_digest": policy["policyDigest"],
                },
            },
            output_payload={
                "approvalId": str(uuid4()),
                "status": AgentRunStatus.waiting_human.value,
            },
        )

    service.agent_run_loader = agent_run_loader
    return SimpleNamespace(
        service=service,
        db=db,
        client=client,
        state=state,
        flows=flows,
        operations=operations,
        knowledge=knowledge,
    )


REPRESENTATIVE_ACTIONS: dict[str, tuple[str, dict[str, Any]]] = {
    "agents-rbac": (
        "configure_agent",
        {"agent_id": "agent-1", "tools": [7, 9], "use_workflow": True},
    ),
    "visual-editor": (
        "create_workflow",
        {"name": "Triagem saúde", "status": "draft", "agent_id": "agent-1"},
    ),
    "nodes-forms-conditions": (
        "replace_graph",
        {
            "workflow_id": "workflow-1",
            "nodes": [{"id": "node-form", "node_type": "form", "config": {}}],
            "connections": [],
        },
    ),
    "nodes-form-action-end-transfer-landing-llm": (
        "save_native_draft",
        {"workflow_id": "workflow-1", "name": "Rascunho controlado"},
    ),
    "preview-execution": ("preview", {"workflow_id": "workflow-1"}),
    "draft-publish-version": (
        "save_revision",
        {"workflow_id": "workflow-1", "name": "Revisão 4"},
    ),
    "mcp-tools-agent-association": (
        "create_tool",
        {
            "name": "KLM local",
            "transport_type": "http",
            "url": "http://127.0.0.1:8765/mcp",
        },
    ),
    "lead-capture-people": (
        "link_klm_person",
        {"person_id": "person-1", "klm_client_id": str(uuid4())},
    ),
    "notifications-groups": (
        "save_notification_rule",
        {"id": "rule-1", "event": "handoff", "group_id": "group-1"},
    ),
    "ratings-feedback": (
        "create_rating",
        {"session_id": "session-1", "rating": 5, "feedback": "Ótimo atendimento"},
    ),
    "widget-apps": (
        "create_app",
        {"name": "KLM local", "url": "http://127.0.0.1:4173/widget"},
    ),
    "file-upload": (
        "register_file",
        {"file_id": "file-1", "name": "negativa.pdf", "extracted_text": True},
    ),
    "jira-shopify": ("dry_run", {"connector": "jira", "enabled": False}),
    "handoff-inbox-transfer": (
        "request_handoff",
        {
            "thread_id": str(THREAD_ID),
            "agent_run_id": str(RUN_ID),
            "requested_by_agent_key": "orquestrador",
            "reason": "Cliente pediu advogado",
        },
    ),
    "structured-output": (
        "validate",
        {
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            "output": {"ok": True},
        },
    ),
    "knowledge-faq": (
        "add_text",
        {"name": "Direitos em saúde", "text": "Conteúdo jurídico revisado."},
    ),
    "widgets-help-center": (
        "save_preview",
        {"id": "preview-1", "theme": "klm", "is_active": False},
    ),
    "analytics": ("refresh", {"time_range": "7d"}),
    "api-websocket": ("probe", {}),
    "external-ai-channels": (
        "dry_run",
        {"connector": "voice", "enabled": False},
    ),
    "private-enterprise-modules": (
        "record_requirement",
        {"module": "sso", "status": "not_licensed"},
    ),
    "human-agent-roster": (
        "create_user",
        {
            "email": "advogado@example.test",
            "full_name": "Advogado",
            "password": "Temporary-Local-Password-42",
            "role_id": 2,
        },
    ),
    "faq-management": (
        "create",
        {"question": "Como funciona?", "answer": "Com análise individual."},
    ),
    "user-settings": ("set_preferences", {"dense_mode": True, "locale": "pt-BR"}),
    "organization-settings": (
        "set_preferences",
        {"default_queue": "juridico", "timezone": "America/Sao_Paulo"},
    ),
    "integration-settings": (
        "dry_run",
        {"connector": "email", "enabled": False},
    ),
    "ai-provider-settings": (
        "dry_run",
        {"provider": "openai", "enabled": False},
    ),
}

UPSTREAM_AGENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UPSTREAM_WORKFLOW_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
UPSTREAM_NODE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
UPSTREAM_USER_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
UPSTREAM_GROUP_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
UPSTREAM_FAQ_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
UPSTREAM_APP_ID = "12121212-1212-4212-8212-121212121212"

SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}

COMMAND_PAYLOADS: dict[str, dict[str, dict[str, Any]]] = {
    "agents-rbac": {
        "sync": {},
        "create_agent": {
            "name": "Agente jurídico",
            "agent_type": "general",
            "instructions": ["Atue somente no ambiente local."],
        },
        "update_agent": {"agent_id": UPSTREAM_AGENT_ID, "display_name": "Jurídico"},
        "configure_agent": {"agent_id": UPSTREAM_AGENT_ID, "use_workflow": False},
        "create_role": {"name": "Revisor", "permissions": []},
        "update_role": {"role_id": 2, "name": "Revisor sênior"},
        "delete_role": {"role_id": 2},
        "grant_permission": {"role_id": 2, "permission": "manage_chats"},
        "revoke_permission": {"role_id": 2, "permission": "manage_chats"},
        "create_user": {
            "email": "novo@example.test",
            "full_name": "Novo usuário",
            "password": "Temporary-Local-Password-42",
            "role_id": 2,
        },
        "update_user": {"user_id": UPSTREAM_USER_ID, "full_name": "Usuário revisado"},
        "delete_user": {"user_id": UPSTREAM_USER_ID},
    },
    "visual-editor": {
        "create_workflow": {
            "name": "Fluxo local",
            "status": "draft",
            "agent_id": UPSTREAM_AGENT_ID,
        },
        "update_workflow": {"workflow_id": UPSTREAM_WORKFLOW_ID, "name": "Fluxo 2"},
        "delete_workflow": {"workflow_id": UPSTREAM_WORKFLOW_ID},
        "replace_graph": {
            "workflow_id": UPSTREAM_WORKFLOW_ID,
            "nodes": [{"id": UPSTREAM_NODE_ID, "node_type": "end", "config": {}}],
            "connections": [],
        },
    },
    "nodes-forms-conditions": {
        "replace_graph": {
            "workflow_id": UPSTREAM_WORKFLOW_ID,
            "nodes": [{"id": UPSTREAM_NODE_ID, "node_type": "form", "config": {}}],
            "connections": [],
        },
        "update_node": {
            "workflow_id": UPSTREAM_WORKFLOW_ID,
            "node_id": UPSTREAM_NODE_ID,
            "node_type": "form",
            "name": "Coleta de documentos",
        },
        "save_native_draft": {"workflow_id": UPSTREAM_WORKFLOW_ID, "name": "Draft"},
    },
    "nodes-form-action-end-transfer-landing-llm": {
        "replace_graph": {
            "workflow_id": UPSTREAM_WORKFLOW_ID,
            "nodes": [{"id": UPSTREAM_NODE_ID, "node_type": "end", "config": {}}],
            "connections": [],
        },
        "update_node": {
            "workflow_id": UPSTREAM_WORKFLOW_ID,
            "node_id": UPSTREAM_NODE_ID,
            "node_type": "end",
            "name": "Fim",
        },
        "dry_run_node": {"workflow_id": UPSTREAM_WORKFLOW_ID, "node_type": "llm"},
        "save_native_draft": {"workflow_id": UPSTREAM_WORKFLOW_ID, "name": "Draft"},
    },
    "preview-execution": {
        "preview": {"workflow_id": "workflow-1"},
        "cancel_preview": {"run_id": str(RUN_ID)},
        "clear_cache": {},
    },
    "draft-publish-version": {
        "save_revision": {"workflow_id": "workflow-1", "name": "Revisão"},
        "publish": {"workflow_id": "workflow-1"},
        "archive": {"workflow_id": "workflow-1"},
        "restore_draft": {"workflow_id": "workflow-1"},
    },
    "mcp-tools-agent-association": {
        "create_tool": {
            "name": "MCP local",
            "transport_type": "http",
            "url": "http://127.0.0.1:8765/mcp",
        },
        "update_tool": {"tool_id": 7, "name": "MCP local 2"},
        "delete_tool": {"tool_id": 7},
        "associate": {"tool_id": 9, "agent_id": UPSTREAM_AGENT_ID},
        "dissociate": {"tool_id": 9, "agent_id": UPSTREAM_AGENT_ID},
    },
    "lead-capture-people": {
        "update_lead_capture": {
            "agent_id": UPSTREAM_AGENT_ID,
            "enabled": False,
            "require_consent": True,
            "fields": [],
        },
        "mark_customer": {"customer_id": UPSTREAM_USER_ID},
        "link_klm_person": {"person_id": UPSTREAM_USER_ID, "klm_client_id": str(uuid4())},
    },
    "notifications-groups": {
        "create_group": {"name": "Jurídico", "description": "Atendimento"},
        "update_group": {"group_id": UPSTREAM_GROUP_ID, "name": "Jurídico sênior"},
        "delete_group": {"group_id": UPSTREAM_GROUP_ID},
        "add_user": {"group_id": UPSTREAM_GROUP_ID, "user_id": UPSTREAM_USER_ID},
        "remove_user": {"group_id": UPSTREAM_GROUP_ID, "user_id": UPSTREAM_USER_ID},
        "mark_notification_read": {"notification_id": 1},
        "save_notification_rule": {"id": "rule-1", "event": "handoff"},
    },
    "ratings-feedback": {
        "create_rating": {"session_id": "session-1", "rating": 5, "feedback": "Ótimo"},
        "link_rating": {"rating_id": "rating-1", "matter_id": str(MATTER_ID)},
    },
    "widget-apps": {
        "create_app": {"name": "Aplicativo local", "description": "KLM"},
        "update_app": {"app_id": UPSTREAM_APP_ID, "name": "Aplicativo 2"},
        "delete_app": {"app_id": UPSTREAM_APP_ID},
        "regenerate_key": {"app_id": UPSTREAM_APP_ID},
    },
    "file-upload": {
        "register_file": {"file_id": "file-1", "name": "negativa.pdf"},
        "promote_to_rag": {
            "matter_id": str(MATTER_ID),
            "content_text": "Conteúdo extraído do documento.",
            "source_ref": "file-1",
        },
        "remove_file": {"file_id": "file-1"},
    },
    "jira-shopify": {
        "save_config": {"connector": "jira", "enabled": False},
        "delete_config": {"connector": "jira"},
        "dry_run": {"connector": "jira", "enabled": False},
    },
    "handoff-inbox-transfer": {
        "request_handoff": {
            "thread_id": str(THREAD_ID),
            "agent_run_id": str(RUN_ID),
            "requested_by_agent_key": "orquestrador",
        },
        "takeover": {"thread_id": str(THREAD_ID), "session_id": "session-1"},
        "reassign": {
            "thread_id": str(THREAD_ID),
            "session_id": "session-1",
            "to_user_id": UPSTREAM_USER_ID,
        },
        "close_handoff": {"thread_id": str(THREAD_ID)},
    },
    "structured-output": {
        "validate": {"schema": SIMPLE_SCHEMA, "output": {"ok": True}},
        "persist_contract": {"id": "contract-1", "schema": SIMPLE_SCHEMA},
    },
    "knowledge-faq": {
        "add_text": {"name": "Direitos", "text": "Conteúdo revisado."},
        "link_agent": {"knowledge_id": 1, "agent_id": UPSTREAM_AGENT_ID},
        "unlink_agent": {"knowledge_id": 1, "agent_id": UPSTREAM_AGENT_ID},
        "delete": {"knowledge_id": 1},
        "promote_to_rag": {
            "matter_id": str(MATTER_ID),
            "content_text": "Conteúdo da base de conhecimento.",
            "source_ref": "knowledge-1",
        },
    },
    "widgets-help-center": {
        "create_widget": {"name": "Widget local", "agent_id": UPSTREAM_AGENT_ID},
        "delete_widget": {"widget_id": "widget-1"},
        "end_chat": {"widget_id": "widget-1", "session_id": "session-1"},
        "update_help_center": {"enabled": False, "title": "Central KLM"},
        "save_preview": {"id": "preview-1", "title": "Central KLM"},
    },
    "analytics": {
        "refresh": {"time_range": "7d"},
        "save_metric_view": {"id": "view-1", "metrics": ["conversations"]},
    },
    "api-websocket": {
        "probe": {},
        "invalidate_session_cache": {},
        "dry_run_event": {"event": "preview.updated", "dispatch": False},
    },
    "external-ai-channels": {
        "save_config": {"connector": "voice", "enabled": False},
        "delete_config": {"connector": "voice"},
        "dry_run": {"connector": "voice", "enabled": False},
    },
    "private-enterprise-modules": {
        "record_requirement": {"module": "sso", "status": "not_licensed"},
        "delete_requirement": {"module": "sso"},
        "dry_run": {"module": "sso", "enabled": False},
    },
    "human-agent-roster": {
        "create_user": {
            "email": "humano@example.test",
            "full_name": "Humano",
            "password": "Temporary-Local-Password-42",
            "role_id": 2,
        },
        "update_user": {"user_id": UPSTREAM_USER_ID, "full_name": "Humano 2"},
        "add_to_group": {"group_id": UPSTREAM_GROUP_ID, "user_id": UPSTREAM_USER_ID},
        "remove_from_group": {"group_id": UPSTREAM_GROUP_ID, "user_id": UPSTREAM_USER_ID},
        "takeover": {"thread_id": str(THREAD_ID), "session_id": "session-1"},
        "reassign": {
            "thread_id": str(THREAD_ID),
            "session_id": "session-1",
            "to_user_id": UPSTREAM_USER_ID,
        },
    },
    "faq-management": {
        "create": {"question": "Como funciona?", "answer": "Análise individual."},
        "update": {"faq_id": UPSTREAM_FAQ_ID, "answer": "Resposta revisada."},
        "delete": {"faq_id": UPSTREAM_FAQ_ID},
        "bulk_status": {"faq_ids": [UPSTREAM_FAQ_ID], "status": "published"},
        "bulk_delete": {"faq_ids": [UPSTREAM_FAQ_ID]},
    },
    "user-settings": {
        "update_profile": {"full_name": "Administrador KLM", "language": "pt-BR"},
        "set_preferences": {"dense_mode": True, "locale": "pt-BR"},
    },
    "organization-settings": {
        "update": {"name": "KLM Advogados", "timezone": "America/Sao_Paulo"},
        "set_preferences": {"default_queue": "juridico"},
    },
    "integration-settings": {
        "save_config": {"connector": "email", "enabled": False},
        "delete_config": {"connector": "email"},
        "dry_run": {"connector": "email", "enabled": False},
    },
    "ai-provider-settings": {
        "save_config": {"provider": "openai", "enabled": False},
        "delete_config": {"provider": "openai"},
        "dry_run": {"provider": "openai", "enabled": False},
    },
}

ALL_COMMAND_CASES = [
    (capability_key, command, COMMAND_PAYLOADS[capability_key][command])
    for capability_key, spec in CAPABILITY_SPECS.items()
    for command in spec.commands
]


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_adapter_keys_match_all_registry_chattermate_capabilities() -> None:
    registry_keys = {
        item["key"]
        for item in oss_capability_registry()["capabilities"]
        if item["provider"] == "chattermate"
    }
    assert len(CHATTERMATE_NATIVE_CAPABILITY_KEYS) == 27
    assert CHATTERMATE_NATIVE_CAPABILITY_KEYS == registry_keys
    assert set(REPRESENTATIVE_ACTIONS) == registry_keys
    assert set(COMMAND_PAYLOADS) == registry_keys
    assert len(ALL_COMMAND_CASES) == 105


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_key", sorted(CHATTERMATE_NATIVE_CAPABILITY_KEYS))
async def test_every_capability_has_specific_sanitized_snapshot(
    harness: SimpleNamespace,
    capability_key: str,
) -> None:
    snapshot = await harness.service.snapshot(WORKSPACE_ID, capability_key, {})

    assert snapshot["provider"] == "chattermate"
    assert snapshot["capabilityKey"] == capability_key
    assert snapshot["title"] == CAPABILITY_SPECS[capability_key].title
    assert snapshot["source"]
    assert snapshot["mode"]
    assert snapshot["availableCommands"]
    assert isinstance(snapshot["data"], dict)
    assert snapshot["data"]
    encoded = _encoded(snapshot)
    assert "sk-0123456789abcdef" not in encoded
    assert "local-access" not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability_key", "command", "payload"),
    [(key, command, payload) for key, (command, payload) in sorted(REPRESENTATIVE_ACTIONS.items())],
)
async def test_every_capability_executes_a_specific_sanitized_native_action(
    harness: SimpleNamespace,
    capability_key: str,
    command: str,
    payload: dict[str, Any],
) -> None:
    result = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        capability_key,
        command,
        copy.deepcopy(payload),
    )

    assert result["provider"] == "chattermate"
    assert result["capabilityKey"] == capability_key
    assert result["command"] == command
    assert result["status"] == "completed"
    assert isinstance(result["data"], dict)
    assert result["data"]
    assert result["externalDispatchAllowed"] is False
    assert "sk-0123456789abcdef" not in _encoded(result)
    assert "Temporary-Local-Password-42" not in _encoded(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability_key", "command", "payload"),
    ALL_COMMAND_CASES,
    ids=[f"{capability}:{command}" for capability, command, _payload in ALL_COMMAND_CASES],
)
async def test_every_advertised_command_has_a_working_native_execution_path(
    harness: SimpleNamespace,
    capability_key: str,
    command: str,
    payload: dict[str, Any],
) -> None:
    result = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        capability_key,
        command,
        copy.deepcopy(payload),
    )

    assert result["capabilityKey"] == capability_key
    assert result["command"] == command
    assert result["status"] == "completed"
    assert isinstance(result["data"], dict)
    assert result["data"]
    encoded = _encoded(result)
    assert "Temporary-Local-Password-42" not in encoded
    assert "sk-0123456789abcdef" not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability_key", "command", "payload"),
    ALL_COMMAND_CASES,
    ids=[f"validate:{capability}:{command}" for capability, command, _ in ALL_COMMAND_CASES],
)
async def test_every_advertised_command_has_a_side_effect_free_validation_path(
    harness: SimpleNamespace,
    capability_key: str,
    command: str,
    payload: dict[str, Any],
) -> None:
    result = await harness.service.validate(
        WORKSPACE_ID,
        ACTOR_ID,
        capability_key,
        command,
        copy.deepcopy(payload),
        mode="dry-run",
    )

    assert result["status"] == "validated"
    assert result["externalNetworkExecuted"] is False


@pytest.mark.asyncio
async def test_workflow_crud_and_agent_configuration_preserve_approved_gates(
    harness: SimpleNamespace,
) -> None:
    service = harness.service
    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "visual-editor",
        "create_workflow",
        {"name": "Novo workflow", "status": "draft", "agent_id": "agent-1"},
    )
    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "visual-editor",
        "update_workflow",
        {"workflow_id": "workflow-1", "name": "Workflow revisado"},
    )
    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "visual-editor",
        "replace_graph",
        {
            "workflow_id": "workflow-1",
            "nodes": [{"id": "node-end", "node_type": "end", "config": {}}],
            "connections": [],
        },
    )
    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "visual-editor",
        "delete_workflow",
        {"workflow_id": "workflow-1"},
    )
    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "agents-rbac",
        "configure_agent",
        {
            "agent_id": "agent-1",
            "tools": [{"id": 7}, {"id": 9}],
            "use_workflow": True,
            "workflow_id": "workflow-1",
        },
    )

    operations = [call["operation"] for call in harness.client.calls]
    assert operations.count("create_workflow") == 1
    assert operations.count("update_workflow") == 1
    assert operations.count("replace_workflow_nodes") == 1
    graph_call = next(
        call for call in harness.client.calls if call["operation"] == "replace_workflow_nodes"
    )
    assert graph_call["nodes"][0]["workflow_id"] == "workflow-1"
    assert graph_call["nodes"][0]["name"] == "End"
    assert any(
        call.get("method") == "DELETE" and call.get("path") == "/api/v1/workflow/workflow-1"
        for call in harness.client.calls
    )
    agent_call = next(call for call in harness.client.calls if call["operation"] == "update_agent")
    assert agent_call["data"]["use_workflow"] is True
    assert agent_call["data"]["active_workflow_id"] == "workflow-1"
    assert "tools" not in agent_call["data"]
    assert any(
        call.get("path") == "/api/v1/mcp-tools/agent-association"
        and call.get("json") == {"mcp_tool_id": 9, "agent_id": "agent-1"}
        for call in harness.client.calls
    )
    association_calls = len(
        [call for call in harness.client.calls if "agent-association" in str(call.get("path"))]
    )
    await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "agents-rbac",
        "configure_agent",
        {"agent_id": "agent-1", "use_workflow": False},
    )
    assert (
        len([call for call in harness.client.calls if "agent-association" in str(call.get("path"))])
        == association_calls
    )
    latest_agent_call = [
        call for call in harness.client.calls if call["operation"] == "update_agent"
    ][-1]
    assert latest_agent_call["data"] == {"use_workflow": False}
    cleared = await service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "agents-rbac",
        "configure_agent",
        {"agent_id": "agent-1", "tools": []},
    )
    assert cleared["data"]["toolAssociations"]["removed"] == [7]
    assert any(
        call.get("path") == "/api/v1/mcp-tools/agent-association/7/agent-1"
        and call.get("method") == "DELETE"
        for call in harness.client.calls
    )


@pytest.mark.asyncio
async def test_workflow_revisions_are_append_only_in_klm_state(
    harness: SimpleNamespace,
) -> None:
    for name in ("Revisão jurídica", "Revisão comercial"):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "draft-publish-version",
            "save_revision",
            {"workflow_id": "workflow-1", "name": name},
        )

    snapshot = await harness.service.snapshot(
        WORKSPACE_ID,
        "draft-publish-version",
        {"refresh": True},
    )
    native_state = snapshot["data"]["nativeState"]
    revisions = native_state["state"]["collections"]["workflowRevisions"]
    assert [row["name"] for row in revisions] == [
        "Revisão jurídica",
        "Revisão comercial",
    ]
    assert len({row["id"] for row in revisions}) == 2


@pytest.mark.asyncio
async def test_handoff_is_hermes_gated_and_human_takeover_updates_klm(
    harness: SimpleNamespace,
) -> None:
    requested = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "handoff-inbox-transfer",
        "request_handoff",
        {
            "thread_id": str(THREAD_ID),
            "agent_run_id": str(RUN_ID),
            "requested_by_agent_key": "orquestrador",
            "reason": "Revisão necessária",
        },
    )
    assert requested["data"]["thread"]["status"] == "waiting_human"
    assert requested["data"]["humanVetoPreserved"] is True
    handoff_governance = requested["data"]["thread"]["thread_metadata"][
        "handoffGovernance"
    ]
    assert handoff_governance["sourceAgentRunId"] == str(RUN_ID)
    assert handoff_governance["hierarchyPath"] == ["orquestrador"]

    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "handoff-inbox-transfer",
            "request_handoff",
            {
                "thread_id": str(THREAD_ID),
                "agent_run_id": str(RUN_ID),
                "requested_by_agent_key": "sdr",
            },
        )

    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "handoff-inbox-transfer",
            "request_handoff",
            {
                "thread_id": str(THREAD_ID),
                "agent_run_id": str(uuid4()),
                "requested_by_agent_key": "orquestrador",
            },
        )

    takeover = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "handoff-inbox-transfer",
        "takeover",
        {"thread_id": str(THREAD_ID), "session_id": "session-1"},
    )
    assert takeover["data"]["thread"]["status"] == "human_active"
    assert takeover["data"]["thread"]["assigned_human_id"] == str(ACTOR_ID)
    assert any(
        call.get("path") == "/api/v1/sessions/session-1/takeover" for call in harness.client.calls
    )


@pytest.mark.asyncio
async def test_handoff_rejects_stale_or_tampered_governance_digest(
    harness: SimpleNamespace,
) -> None:
    valid_loader = harness.service.agent_run_loader
    assert valid_loader is not None
    run = await valid_loader(WORKSPACE_ID, RUN_ID)
    run.input_payload["governance"]["policy_digest"] = "0" * 64

    async def tampered_loader(workspace_id: UUID, run_id: UUID) -> Any | None:
        return run if workspace_id == WORKSPACE_ID and run_id == RUN_ID else None

    harness.service.agent_run_loader = tampered_loader
    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "handoff-inbox-transfer",
            "request_handoff",
            {
                "thread_id": str(THREAD_ID),
                "agent_run_id": str(RUN_ID),
                "requested_by_agent_key": "orquestrador",
            },
        )


@pytest.mark.asyncio
async def test_knowledge_upstream_and_klm_rag_are_both_real_mutations(
    harness: SimpleNamespace,
) -> None:
    upstream = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "knowledge-faq",
        "add_text",
        {"name": "Home care", "text": "Material revisado sobre cobertura."},
    )
    request = next(
        call for call in harness.client.calls if call.get("path") == "/api/v1/knowledge/add/text"
    )
    assert request["json"]["org_id"] == "org-1"
    assert request["json"]["title"] == "Home care"
    assert request["json"]["content"] == "Material revisado sobre cobertura."
    assert "name" not in request["json"]
    assert "text" not in request["json"]
    assert upstream["data"]["localMutationApplied"] is True

    rag = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "knowledge-faq",
        "promote_to_rag",
        {
            "matter_id": str(MATTER_ID),
            "source_ref": "knowledge-1",
            "content_text": "A negativa foi analisada e classificada.",
            "title": "Análise da negativa",
            "tags": ["saude", "negativa"],
        },
    )
    assert rag["data"]["persistent"] is True
    assert rag["data"]["chunk"]["matter_id"] == str(MATTER_ID)
    assert harness.knowledge.upserts[0]["source_type"] == "chattermate_knowledge"


@pytest.mark.asyncio
async def test_native_state_crud_is_persistent_specific_and_credential_free(
    harness: SimpleNamespace,
) -> None:
    stored = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "integration-settings",
        "save_config",
        {
            "connector": "email",
            "endpoint": "http://127.0.0.1:8025",
            "api_key": "sk-0123456789abcdef",
            "dispatch": False,
        },
    )
    mutation = stored["data"]["mutation"]
    assert mutation["collection"] == "configurations"
    assert mutation["omittedSensitiveFields"] == ["$.api_key"]

    await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "file-upload",
        "register_file",
        {"file_id": "file-1", "name": "negativa.pdf", "content": "texto extraído"},
    )
    snapshot = await harness.service.snapshot(
        WORKSPACE_ID,
        "file-upload",
        {"refresh": True, "collection": "files"},
    )
    assert snapshot["data"]["nativeState"]["recordCount"] == 1
    assert snapshot["data"]["nativeState"]["records"][0]["name"] == "negativa.pdf"

    await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "file-upload",
        "remove_file",
        {"file_id": "file-1"},
    )
    removed = await harness.service.snapshot(
        WORKSPACE_ID,
        "file-upload",
        {"refresh": True, "collection": "files"},
    )
    assert removed["data"]["nativeState"]["recordCount"] == 0
    assert "sk-0123456789abcdef" not in repr(harness.state.rows)


@pytest.mark.asyncio
async def test_real_oss_native_state_service_round_trip(
    db_session: Any,
    auth_context: Any,
) -> None:
    ChatterMateNativeService.clear_cache()
    service = ChatterMateNativeService(db_session)
    service.klm_operations = FakeKlmOperations()
    service.client = FakeChatterMateClient()

    async def workspace_scope(workspace_id: UUID) -> ChatterMateWorkspaceScope:
        return ChatterMateWorkspaceScope(
            workspace_id=workspace_id,
            organization_id="org-1",
            upstream_user_id="user-1",
            agent_ids=frozenset(),
            workflow_ids=frozenset(),
            person_ids=frozenset(),
        )

    service.workspace_scope_loader = workspace_scope

    saved = await service.execute(
        auth_context.workspace_id,
        auth_context.user_id,
        "integration-settings",
        "save_config",
        {"connector": "email", "enabled": False, "dispatch": False},
    )
    assert saved["data"]["persistent"] is True

    snapshot = await service.snapshot(
        auth_context.workspace_id,
        "integration-settings",
        {"refresh": True, "collection": "configurations"},
    )
    native_state = snapshot["data"]["nativeState"]
    assert native_state["persistent"] is True
    assert native_state["configVersion"] == 1
    assert native_state["recordCount"] == 1
    assert native_state["records"][0]["connector"] == "email"


@pytest.mark.asyncio
async def test_remote_reads_and_actions_are_isolated_between_two_workspaces(
    db_session: Any,
    auth_context: Any,
) -> None:
    ChatterMateNativeService.clear_cache()
    workspace_a = auth_context.workspace_id
    workspace_b_row = Workspace(
        name="Workspace B",
        slug=f"workspace-b-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace_b_row)
    await db_session.flush()
    workspace_b = workspace_b_row.id

    db_session.add_all(
        [
            ExternalEntityLink(
                workspace_id=workspace_a,
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="workspace",
                klm_entity_id=workspace_a,
                external_account_id="org-1",
                external_entity_type="organization",
                external_entity_id="org-1",
                link_metadata={},
                sync_state=ExternalEntitySyncState.synced,
            ),
            ExternalEntityLink(
                workspace_id=workspace_a,
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="agent",
                klm_entity_id=uuid4(),
                external_account_id="org-1",
                external_entity_type="agent",
                external_entity_id="agent-workspace-a",
                link_metadata={},
                sync_state=ExternalEntitySyncState.synced,
            ),
            ExternalEntityLink(
                workspace_id=workspace_b,
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="agent",
                klm_entity_id=uuid4(),
                external_account_id="org-1",
                external_entity_type="agent",
                external_entity_id="agent-workspace-b",
                link_metadata={},
                sync_state=ExternalEntitySyncState.synced,
            ),
        ]
    )
    await db_session.flush()

    client = FakeChatterMateClient()
    client.agents = [
        {"id": "agent-workspace-a", "name": "Agente A"},
        {"id": "agent-workspace-b", "name": "Agente B"},
    ]
    service = ChatterMateNativeService(db_session)
    service.client = client
    service.klm_operations = FakeKlmOperations()
    service.agent_service = FakeAgentService()

    snapshot = await service.snapshot(
        workspace_a,
        "agents-rbac",
        {"refresh": True},
    )
    upstream_agents = snapshot["data"]["upstreamAgents"]["value"]
    assert [row["id"] for row in upstream_agents] == ["agent-workspace-a"]
    assert "agent-workspace-b" not in _encoded(snapshot)

    identity_calls = client.identity_calls
    remote_calls = len(client.calls)
    with pytest.raises(ChatterMateWorkspaceIsolationError):
        await service.snapshot(
            workspace_b,
            "agents-rbac",
            {"refresh": True},
        )
    assert client.identity_calls == identity_calls
    assert len(client.calls) == remote_calls

    with pytest.raises(ChatterMateWorkspaceIsolationError):
        await service.snapshot(
            workspace_a,
            "mcp-tools-agent-association",
            {"refresh": True, "agent_id": "agent-workspace-b"},
        )
    assert not any(
        call.get("path") == "/api/v1/mcp-tools/agent/agent-workspace-b" for call in client.calls
    )

    identity_calls = client.identity_calls
    remote_calls = len(client.calls)
    with pytest.raises(ChatterMateWorkspaceIsolationError):
        await service.execute(
            workspace_b,
            auth_context.user_id,
            "agents-rbac",
            "create_role",
            {"name": "Não autorizado"},
        )
    assert client.identity_calls == identity_calls
    assert len(client.calls) == remote_calls


@pytest.mark.asyncio
async def test_ambiguous_organization_binding_fails_closed_before_cached_read(
    db_session: Any,
    auth_context: Any,
) -> None:
    ChatterMateNativeService.clear_cache()
    workspace_a = auth_context.workspace_id
    for organization_id in ("org-1", "org-2"):
        db_session.add(
            ExternalEntityLink(
                workspace_id=workspace_a,
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="workspace",
                klm_entity_id=workspace_a,
                external_account_id=organization_id,
                external_entity_type="organization",
                external_entity_id=organization_id,
                link_metadata={},
                sync_state=ExternalEntitySyncState.synced,
            )
        )
    await db_session.flush()

    client = FakeChatterMateClient()
    service = ChatterMateNativeService(db_session)
    service.client = client
    service.klm_operations = FakeKlmOperations()
    service.agent_service = FakeAgentService()

    with pytest.raises(ChatterMateWorkspaceIsolationError):
        await service.snapshot(workspace_a, "agents-rbac", {"refresh": True})
    assert client.identity_calls == 0
    assert client.calls == []


@pytest.mark.asyncio
async def test_same_chattermate_organization_cannot_bind_two_workspaces(
    db_session: Any,
    auth_context: Any,
) -> None:
    workspace_a = auth_context.workspace_id
    workspace_b_row = Workspace(
        name="Workspace B concorrente",
        slug=f"workspace-b-race-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace_b_row)
    await db_session.flush()

    for workspace_id in (workspace_a, workspace_b_row.id):
        db_session.add(
            ExternalEntityLink(
                workspace_id=workspace_id,
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="workspace",
                klm_entity_id=workspace_id,
                external_account_id="org-race",
                external_entity_type="organization",
                external_entity_id="org-race",
                link_metadata={},
                sync_state=ExternalEntitySyncState.synced,
            )
        )
        if workspace_id == workspace_a:
            await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_created_agent_and_workflow_receive_workspace_links_for_later_crud(
    db_session: Any,
    auth_context: Any,
) -> None:
    ChatterMateNativeService.clear_cache()
    workspace_id = auth_context.workspace_id
    db_session.add_all(
        [
            ExternalEntityLink(
                workspace_id=workspace_id,
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="workspace",
                klm_entity_id=workspace_id,
                external_account_id="org-1",
                external_entity_type="organization",
                external_entity_id="org-1",
                link_metadata={},
                sync_state=ExternalEntitySyncState.synced,
            ),
            ExternalEntityLink(
                workspace_id=workspace_id,
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="agent",
                klm_entity_id=uuid4(),
                external_account_id="org-1",
                external_entity_type="agent",
                external_entity_id="agent-existing",
                link_metadata={},
                sync_state=ExternalEntitySyncState.synced,
            ),
        ]
    )
    await db_session.flush()

    client = FakeChatterMateClient()
    client.agents = [{"id": "agent-existing", "name": "Agente existente"}]
    service = ChatterMateNativeService(db_session)
    service.client = client
    service.klm_operations = FakeKlmOperations()

    await service.execute(
        workspace_id,
        auth_context.user_id,
        "agents-rbac",
        "create_agent",
        {"name": "Agente nativo"},
    )
    agent_link = await db_session.scalar(
        select(ExternalEntityLink).where(
            ExternalEntityLink.workspace_id == workspace_id,
            ExternalEntityLink.provider == OSSBridgeProvider.chattermate,
            ExternalEntityLink.external_entity_type == "agent",
            ExternalEntityLink.external_entity_id == "agent-created",
            ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
        )
    )
    assert agent_link is not None
    assert agent_link.external_account_id == "org-1"

    await service.execute(
        workspace_id,
        auth_context.user_id,
        "visual-editor",
        "create_workflow",
        {"name": "Workflow nativo", "status": "draft", "agent_id": "agent-existing"},
    )
    workflow_link = await db_session.scalar(
        select(ExternalEntityLink).where(
            ExternalEntityLink.workspace_id == workspace_id,
            ExternalEntityLink.provider == OSSBridgeProvider.chattermate,
            ExternalEntityLink.external_entity_type == "workflow",
            ExternalEntityLink.external_entity_id == "workflow-created",
            ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
        )
    )
    assert workflow_link is not None
    assert workflow_link.link_metadata["agentId"] == "agent-existing"


@pytest.mark.asyncio
async def test_external_llm_publish_widget_and_mcp_gates_fail_closed(
    harness: SimpleNamespace,
) -> None:
    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "external-ai-channels",
            "save_config",
            {"connector": "voice", "dispatch": True},
        )

    llm_graph = {
        "workflow_id": "workflow-1",
        "nodes": [{"id": "node-llm", "node_type": "llm", "config": {}}],
        "connections": [],
    }
    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "nodes-form-action-end-transfer-landing-llm",
            "replace_graph",
            llm_graph,
        )
    gated_draft = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "nodes-form-action-end-transfer-landing-llm",
        "replace_graph",
        {**llm_graph, "dry_run": True},
    )
    assert gated_draft["data"]["dispatch"] == "closed"
    assert not any(call["operation"] == "replace_workflow_nodes" for call in harness.client.calls)

    harness.client.graphs["workflow-1"] = {
        "nodes": llm_graph["nodes"],
        "connections": [],
    }
    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "draft-publish-version",
            "publish",
            {"workflow_id": "workflow-1"},
        )

    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "mcp-tools-agent-association",
            "create_tool",
            {
                "name": "Remoto",
                "transport_type": "http",
                "url": "https://example.com/mcp",
            },
        )
    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "widgets-help-center",
            "create_widget",
            {"name": "Público", "is_active": True},
        )

    async def remote_tools() -> list[dict[str, Any]]:
        return [
            {
                "id": 42,
                "name": "Remoto existente",
                "transport_type": "http",
                "url": "http://example.com/mcp",
            }
        ]

    harness.client.mcp_tools = remote_tools
    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "mcp-tools-agent-association",
            "associate",
            {"tool_id": 42, "agent_id": "agent-1"},
        )
    with pytest.raises(ChatterMateNativeGateError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "mcp-tools-agent-association",
            "update_tool",
            {"tool_id": 42, "name": "Ainda remoto"},
        )


@pytest.mark.asyncio
async def test_widget_end_chat_uses_the_upstream_query_contract(
    harness: SimpleNamespace,
) -> None:
    result = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "widgets-help-center",
        "end_chat",
        {
            "widget_id": "widget-1",
            "session_id": "session-1",
            "reason": "handoff",
            "description": "Conversa assumida por humano",
        },
    )
    request = next(
        call
        for call in harness.client.calls
        if call.get("path") == "/api/v1/widgets/widget-1/end-chat"
    )
    assert request["json"] is None
    assert request["params"] == {
        "session_id": "session-1",
        "reason": "handoff",
        "description": "Conversa assumida por humano",
    }
    assert result["data"]["localMutationApplied"] is True


@pytest.mark.asyncio
async def test_snapshot_cache_survives_waiter_cancellation_and_returns_deep_copies(
    harness: SimpleNamespace,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def delayed_snapshot(
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        nonlocal calls
        del workspace_id, query, scope
        calls += 1
        started.set()
        await release.wait()
        return {"rows": [{"count": 1}]}

    harness.service._snapshot_analytics = delayed_snapshot
    first = asyncio.create_task(harness.service.snapshot(WORKSPACE_ID, "analytics", {}))
    await started.wait()
    second = asyncio.create_task(harness.service.snapshot(WORKSPACE_ID, "analytics", {}))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    second_result = await second
    assert calls == 1

    second_result["data"]["rows"][0]["count"] = 999
    cached = await harness.service.snapshot(WORKSPACE_ID, "analytics", {})
    assert cached["cache"]["hit"] is True
    assert cached["data"]["rows"][0]["count"] == 1
    assert calls == 1

    await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "preview-execution",
        "clear_cache",
        {},
    )
    refreshed = await harness.service.snapshot(WORKSPACE_ID, "analytics", {})
    assert refreshed["cache"]["hit"] is False
    assert calls == 2


@pytest.mark.asyncio
async def test_cache_invalidation_prevents_an_old_inflight_load_from_repopulating(
    harness: SimpleNamespace,
) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def changing_snapshot(
        workspace_id: UUID,
        query: dict[str, Any],
        scope: ChatterMateWorkspaceScope,
    ) -> dict[str, Any]:
        nonlocal calls
        del workspace_id, query, scope
        calls += 1
        call_number = calls
        if call_number == 1:
            first_started.set()
            await release_first.wait()
        return {"revision": call_number}

    harness.service._snapshot_analytics = changing_snapshot
    old_load = asyncio.create_task(harness.service.snapshot(WORKSPACE_ID, "analytics", {}))
    await first_started.wait()
    harness.service.clear_cache(WORKSPACE_ID, "analytics")
    fresh = await harness.service.snapshot(WORKSPACE_ID, "analytics", {})
    assert fresh["data"]["revision"] == 2

    release_first.set()
    old = await old_load
    assert old["data"]["revision"] == 1
    cached = await harness.service.snapshot(WORKSPACE_ID, "analytics", {})
    assert cached["cache"]["hit"] is True
    assert cached["data"]["revision"] == 2
    assert calls == 2


@pytest.mark.asyncio
async def test_preview_cancellation_and_execute_cancellation_are_audited(
    harness: SimpleNamespace,
) -> None:
    cancelled = await harness.service.execute(
        WORKSPACE_ID,
        ACTOR_ID,
        "preview-execution",
        "cancel_preview",
        {"run_id": str(RUN_ID)},
    )
    assert cancelled["data"]["cancelled"] is True
    assert harness.flows.cancelled == [RUN_ID]

    started = asyncio.Event()

    async def never_finishes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return {"unreachable": True}

    harness.service._execute_data = never_finishes
    task = asyncio.create_task(
        harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "api-websocket",
            "probe",
            {},
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    phases = [audit["after_data"]["phase"] for audit in harness.operations.audits]
    assert phases[-2:] == ["requested", "cancelled"]


@pytest.mark.asyncio
async def test_unknown_capability_and_unsupported_command_are_explicit(
    harness: SimpleNamespace,
) -> None:
    with pytest.raises(UnknownChatterMateCapabilityError):
        await harness.service.snapshot(WORKSPACE_ID, "missing-capability", {})
    with pytest.raises(UnsupportedChatterMateCommandError):
        await harness.service.execute(
            WORKSPACE_ID,
            ACTOR_ID,
            "analytics",
            "delete_everything",
            {},
        )
