"""Safe and idempotent ChatterMate visual-flow mirroring contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.services.chattermate_flow_service import (
    ChatterMateFlowService,
    _flow_digest,
    _project_graph,
)

WORKSPACE_ID = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
FLOW_ID = UUID("8df7bffe-776d-4d33-8810-1aa9eff9fe8a")
ACTOR_ID = UUID("fbdcc4ce-63d3-450d-b85d-13ffb08ea82d")


def canonical_flow() -> SimpleNamespace:
    return SimpleNamespace(
        id=FLOW_ID,
        current_revision=4,
        name="Triagem saúde",
        description="Triagem até revisão humana.",
        is_active=True,
        trigger="new_lead",
        trigger_config={"channel": "whatsapp"},
        nodes=[
            {
                "id": "start",
                "type": "trigger",
                "position": {"x": 20, "y": 40},
                "data": {"label": "Novo lead"},
                "config": {"event": "new_lead"},
            },
            {
                "id": "message",
                "type": "send_message",
                "position": {"x": 220, "y": 40},
                "data": {"label": "Confirmar documentos"},
                "config": {"message": "Recebemos seus documentos."},
            },
            {
                "id": "legal-agent",
                "type": "agent",
                "position": {"x": 420, "y": 40},
                "data": {"label": "Revisão jurídica"},
                "config": {"agent": "juridico", "use_rag": True},
            },
            {
                "id": "human",
                "type": "human_pause",
                "position": {"x": 620, "y": 40},
                "data": {"label": "Aprovação humana"},
                "config": {"reason": "Advogado deve aprovar."},
            },
        ],
        edges=[
            {"id": "e1", "source": "start", "target": "message"},
            {"id": "e2", "source": "message", "target": "legal-agent"},
            {"id": "e3", "source": "legal-agent", "target": "human"},
        ],
    )


@pytest.mark.asyncio
async def test_flow_sync_is_safe_and_second_run_is_noop() -> None:
    flow = canonical_flow()
    state: dict[str, object] = {"agents": [], "workflow": None, "graph": None}

    async def create_agent(payload):
        row = {**payload, "id": "0afab914-cf4b-4f19-aa5b-7d979f87a832"}
        state["agents"] = [row]
        return row

    async def create_workflow(payload):
        row = {**payload, "id": "e7196d0c-e94d-4c5b-aa9b-a83118140268"}
        state["workflow"] = row
        return row

    async def replace_graph(_workflow_id, *, nodes, connections):
        graph = {"nodes": nodes, "connections": connections}
        state["graph"] = graph
        return graph

    client = SimpleNamespace(
        identity=AsyncMock(return_value={"organization_id": "org-local"}),
        list_agents=AsyncMock(side_effect=lambda: state["agents"]),
        create_agent=AsyncMock(side_effect=create_agent),
        update_agent=AsyncMock(),
        workflow_for_agent=AsyncMock(side_effect=lambda _agent_id: state["workflow"]),
        create_workflow=AsyncMock(side_effect=create_workflow),
        update_workflow=AsyncMock(),
        workflow_nodes=AsyncMock(side_effect=lambda _workflow_id: state["graph"]),
        replace_workflow_nodes=AsyncMock(side_effect=replace_graph),
    )
    service = object.__new__(ChatterMateFlowService)
    service.db = SimpleNamespace(
        add=MagicMock(),
        commit=AsyncMock(),
        scalar=AsyncMock(return_value="Usuário Teste"),
    )
    service.client = client
    service.links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(return_value=None),
        upsert_link=AsyncMock(return_value=SimpleNamespace()),
    )
    service._flow = AsyncMock(return_value=flow)  # type: ignore[method-assign]
    service._ensure_workspace_binding = AsyncMock(return_value=None)  # type: ignore[method-assign]

    first = await service.sync_flow(WORKSPACE_ID, FLOW_ID, ACTOR_ID)
    second = await service.sync_flow(WORKSPACE_ID, FLOW_ID, ACTOR_ID)

    assert first["agentAction"] == "created"
    assert first["workflowAction"] == "created"
    assert first["graphAction"] == "replaced"
    assert second["agentAction"] == "unchanged"
    assert second["workflowAction"] == "unchanged"
    assert second["graphAction"] == "unchanged"
    assert first["externalExecutionEnabled"] is False
    assert client.create_agent.await_count == 1
    assert client.create_workflow.await_count == 1
    assert client.replace_workflow_nodes.await_count == 1
    assert service._ensure_workspace_binding.await_count == 2
    service._ensure_workspace_binding.assert_awaited_with(WORKSPACE_ID, "org-local")

    agent_payload = client.create_agent.await_args.args[0]
    assert agent_payload["is_active"] is False
    assert agent_payload["use_workflow"] is False
    assert agent_payload["tools"] == []
    assert agent_payload["require_token_auth"] is True

    workflow_payload = client.create_workflow.await_args.args[0]
    assert workflow_payload["status"] == "draft"
    assert workflow_payload["settings"]["sourceOfTruth"] == "klm"
    assert workflow_payload["settings"]["externalExecutionEnabled"] is False

    graph = state["graph"]
    assert isinstance(graph, dict)
    nodes = graph["nodes"]
    assert isinstance(nodes, list)
    assert {node["node_type"] for node in nodes}.isdisjoint({"llm", "landing_page"})
    action = next(node for node in nodes if node["config"]["klm_node_id"] == "legal-agent")
    assert action["node_type"] == "action"
    assert action["config"]["action_type"] == "custom"
    assert action["config"]["action_config"]["externalExecutionEnabled"] is False
    assert all(
        "webhook" not in str(node).lower() and "email" not in str(node).lower()
        for node in nodes
    )


@pytest.mark.asyncio
async def test_flow_context_detects_matching_digest_and_graph() -> None:
    flow = canonical_flow()
    service = object.__new__(ChatterMateFlowService)
    service.db = SimpleNamespace()
    service._flow = AsyncMock(return_value=flow)  # type: ignore[method-assign]
    agent_link = SimpleNamespace(external_entity_id="agent-flow")
    workflow_link = SimpleNamespace(external_entity_id="workflow-flow")
    service.links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(side_effect=[agent_link, workflow_link]),
    )
    desired_nodes, desired_connections = _project_graph(flow)
    digest = _flow_digest(flow)
    service.client = SimpleNamespace(
        identity=AsyncMock(return_value={"organization_id": "org-local"}),
        workflow_for_agent=AsyncMock(
            return_value={
                "id": "workflow-flow",
                "status": "draft",
                "settings": {"klmSourceDigest": digest},
            }
        ),
        workflow_nodes=AsyncMock(
            return_value={"nodes": desired_nodes, "connections": desired_connections}
        ),
    )

    context = await service.context(WORKSPACE_ID, FLOW_ID)

    assert context["linked"] is True
    assert context["digestMatches"] is True
    assert context["graphMatches"] is True
    assert context["workflowStatus"] == "draft"
    assert context["externalExecutionEnabled"] is False


@pytest.mark.asyncio
async def test_flow_sync_rejects_foreign_chattermate_organization_before_remote_writes() -> None:
    service = object.__new__(ChatterMateFlowService)
    service.db = SimpleNamespace(
        add=MagicMock(),
        commit=AsyncMock(),
        scalar=AsyncMock(return_value="Usuário Teste"),
    )
    service._flow = AsyncMock(return_value=canonical_flow())  # type: ignore[method-assign]
    service._ensure_workspace_binding = AsyncMock(  # type: ignore[method-assign]
        side_effect=PermissionError(
            "Organização ChatterMate já vinculada a outro workspace KLM."
        )
    )
    service.client = SimpleNamespace(
        identity=AsyncMock(return_value={"organization_id": "org-foreign"}),
        list_agents=AsyncMock(),
        create_agent=AsyncMock(),
    )
    service.links = SimpleNamespace(upsert_link=AsyncMock())

    with pytest.raises(PermissionError, match="outro workspace"):
        await service.sync_flow(WORKSPACE_ID, FLOW_ID, ACTOR_ID)

    service._ensure_workspace_binding.assert_awaited_once_with(WORKSPACE_ID, "org-foreign")
    service.client.list_agents.assert_not_awaited()
    service.client.create_agent.assert_not_awaited()
    service.links.upsert_link.assert_not_awaited()
    service.db.commit.assert_not_awaited()
