"""Safe visual mirroring of KLM-canonical flows into local ChatterMate."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import Flow
from app.models.klm_operations import KlmAuditEvent
from app.models.oss_bridge import (
    ExternalEntitySyncState,
    OSSBridgeProvider,
    sanitize_oss_json,
)
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.chattermate_client import ChatterMateClient
from app.services.chattermate_operations_service import (
    ChatterMateOperationsService,
    _exclusive_chattermate_sync,
    invalidate_chattermate_context_cache,
)
from app.services.oss_bridge_service import OSSBridgeService

_NODE_TYPES = {
    "trigger": "user_input",
    "send_message": "message",
    "wait": "wait",
    "condition": "condition",
    "human_pause": "human_transfer",
    "agent": "action",
    "zapsign_request": "action",
    "scheduled_message": "action",
}


def _flow_marker(workspace_id: UUID, flow_id: UUID) -> str:
    return f"KLM-FLOW-ID:{workspace_id}:{flow_id}"


def _bounded_name(prefix: str, name: str, flow_id: UUID, maximum: int = 100) -> str:
    suffix = f" · {str(flow_id)[:8]}"
    normalized = " ".join(name.split()) or "Flow jurídico"
    room = max(1, maximum - len(prefix) - len(suffix))
    return f"{prefix}{normalized[:room].rstrip()}{suffix}"


def _flow_digest(flow: Flow) -> str:
    payload = {
        "id": str(flow.id),
        "revision": flow.current_revision,
        "name": flow.name,
        "description": flow.description,
        "isActive": flow.is_active,
        "trigger": flow.trigger,
        "triggerConfig": flow.trigger_config or {},
        "nodes": flow.nodes or [],
        "edges": flow.edges or [],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dictionary(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _flow_node_config(node: dict[str, Any]) -> dict[str, Any]:
    config = node.get("config")
    if isinstance(config, dict):
        return config
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _node_label(node: dict[str, Any], index: int) -> str:
    data = _dictionary(node.get("data"))
    return str(data.get("label") or node.get("label") or f"Etapa {index + 1}")


def _project_graph(flow: Flow) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_nodes = [row for row in (flow.nodes or []) if isinstance(row, dict)]
    raw_edges = [row for row in (flow.edges or []) if isinstance(row, dict)]
    label_counts = Counter(_node_label(node, index) for index, node in enumerate(raw_nodes))
    label_seen: Counter[str] = Counter()
    nodes: list[dict[str, Any]] = []
    for index, node in enumerate(raw_nodes):
        node_id = str(node.get("id") or f"node-{index + 1}")
        klm_type = str(node.get("type") or "human_pause")
        label = _node_label(node, index)
        label_seen[label] += 1
        unique_label = f"{label} {label_seen[label]}" if label_counts[label] > 1 else label
        position = _dictionary(node.get("position"))
        klm_config = cast(
            dict[str, Any],
            sanitize_oss_json(_flow_node_config(node)),
        )
        visual_config: dict[str, Any] = {
            "klm_node_id": node_id,
            "klm_node_type": klm_type,
            "klm_config": klm_config,
            "execution_policy": "visual_mirror_only",
            "externalExecutionEnabled": False,
        }
        if klm_type == "send_message":
            visual_config["message_text"] = str(klm_config.get("message") or "")
        elif klm_type == "wait":
            hours = max(0.0, min(_number(klm_config.get("delay_hours"), 0.0), 8760.0))
            visual_config["wait_duration"] = round(hours * 3600)
            visual_config["wait_unit"] = "seconds"
        elif klm_type == "condition":
            field = str(klm_config.get("field") or "context")
            operator = str(klm_config.get("operator") or "eq")
            expected = str(klm_config.get("value") or "")
            visual_config["condition_expression"] = f"{field} {operator} {expected}".strip()
        elif klm_type == "human_pause":
            visual_config["transfer_message"] = str(
                klm_config.get("reason") or "Revisão humana obrigatória no KLM"
            )
        elif klm_type in {"agent", "zapsign_request", "scheduled_message"}:
            visual_config["action_type"] = "custom"
            visual_config["action_config"] = {
                "operation": klm_type,
                "externalExecutionEnabled": False,
                "runtime": "klm",
            }
        nodes.append(
            {
                "id": node_id,
                "node_type": _NODE_TYPES.get(klm_type, "action"),
                "name": unique_label[:100],
                "description": (
                    f"Espelho visual do nó KLM {node_id}; execução canônica no Meu Executivo."
                ),
                "position_x": _number(position.get("x")),
                "position_y": _number(position.get("y")),
                "config": visual_config,
            }
        )

    node_ids = {str(node["id"]) for node in nodes}
    connections: list[dict[str, Any]] = []
    for index, edge in enumerate(raw_edges):
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in node_ids or target not in node_ids:
            continue
        edge_id = str(edge.get("id") or f"edge-{index + 1}")
        label = str(edge.get("label") or "") or None
        connections.append(
            {
                "id": edge_id,
                "source_node_id": source,
                "target_node_id": target,
                "label": label,
                "condition": str(edge.get("condition_value") or edge.get("conditionValue") or "")
                or None,
                "priority": index,
                "connection_metadata": {
                    "klm_edge_id": edge_id,
                    "klm_source_id": source,
                    "klm_target_id": target,
                    "execution_policy": "visual_mirror_only",
                },
            }
        )
    return nodes, connections


def _graph_signature(graph: dict[str, Any]) -> str:
    nodes = []
    for row in graph.get("nodes", []):
        if not isinstance(row, dict):
            continue
        config = _dictionary(row.get("config"))
        node_id = str(config.get("klm_node_id") or row.get("id") or "")
        nodes.append(
            {
                "id": node_id,
                "node_type": str(row.get("node_type") or ""),
                "name": str(row.get("name") or ""),
                "description": str(row.get("description") or ""),
                "position_x": _number(row.get("position_x")),
                "position_y": _number(row.get("position_y")),
                "config": config,
            }
        )
    connections = []
    for row in graph.get("connections", []):
        if not isinstance(row, dict):
            continue
        metadata = _dictionary(row.get("connection_metadata"))
        connections.append(
            {
                "id": str(metadata.get("klm_edge_id") or row.get("id") or ""),
                "source_node_id": str(
                    metadata.get("klm_source_id") or row.get("source_node_id") or ""
                ),
                "target_node_id": str(
                    metadata.get("klm_target_id") or row.get("target_node_id") or ""
                ),
                "label": row.get("label"),
                "condition": row.get("condition"),
                "priority": int(row.get("priority") or 0),
                "connection_metadata": metadata,
            }
        )
    canonical = json.dumps(
        {
            "nodes": sorted(nodes, key=lambda row: row["id"]),
            "connections": sorted(connections, key=lambda row: row["id"]),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ChatterMateFlowService:
    def __init__(self, db: AsyncSession, client: ChatterMateClient | None = None):
        self.db = db
        self.client = client or ChatterMateClient()
        self.links = OSSBridgeService(db)

    async def _flow(self, workspace_id: UUID, flow_id: UUID) -> Flow:
        flow = await self.db.scalar(
            select(Flow).where(
                Flow.id == flow_id,
                Flow.workspace_id == workspace_id,
                Flow.deleted_at.is_(None),
            )
        )
        if flow is None:
            raise LookupError("Flow KLM não encontrado no workspace atual.")
        return flow

    async def _ensure_workspace_binding(
        self,
        workspace_id: UUID,
        organization_id: str,
    ) -> None:
        await ChatterMateOperationsService(
            self.db,
            client=self.client,
        ).ensure_workspace_binding(workspace_id, organization_id)

    async def flow_agent_id(self, workspace_id: UUID, flow_id: UUID) -> str | None:
        identity = await self.client.identity()
        organization_id = str(identity["organization_id"])
        link = await self.links.find_link_by_klm_entity(
            workspace_id,
            OSSBridgeProvider.chattermate,
            organization_id,
            "flow",
            flow_id,
            "workflow_agent",
        )
        return (
            link.external_entity_id
            if link and link.sync_state == ExternalEntitySyncState.synced
            else None
        )

    async def sync_flow(
        self,
        workspace_id: UUID,
        flow_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        async with _exclusive_chattermate_sync():
            return await self._sync_flow_unlocked(workspace_id, flow_id, actor_user_id)

    async def _sync_flow_unlocked(
        self,
        workspace_id: UUID,
        flow_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        flow = await self._flow(workspace_id, flow_id)
        identity = await self.client.identity()
        organization_id = str(identity.get("organization_id") or "").strip()
        await self._ensure_workspace_binding(workspace_id, organization_id)
        marker = _flow_marker(workspace_id, flow.id)
        source_digest = _flow_digest(flow)
        agent_name = _bounded_name("KLM Flow · ", flow.name, flow.id)
        agent_link = await self.links.find_link_by_klm_entity(
            workspace_id,
            OSSBridgeProvider.chattermate,
            organization_id,
            "flow",
            flow.id,
            "workflow_agent",
        )
        agents = await self.client.list_agents()
        agents_by_id = {str(row.get("id")): row for row in agents if row.get("id")}
        marker_matches = [
            row for row in agents if marker in {str(item) for item in row.get("instructions", [])}
        ]
        current_agent = agents_by_id.get(agent_link.external_entity_id) if agent_link else None
        current_agent = current_agent or (marker_matches[0] if marker_matches else None)
        current_agent = current_agent or next(
            (row for row in agents if row.get("name") == agent_name),
            None,
        )
        agent_payload = {
            "display_name": f"Flow · {flow.name}"[:100],
            "instructions": [
                "Runner visual inativo; a execução canônica ocorre no Meu Executivo.",
                "Não executar LLM, webhook, e-mail, canal ou ferramenta externa.",
                marker,
            ],
            "is_active": False,
            "transfer_to_human": False,
            "ask_for_rating": False,
            "handoff_collect_email": False,
            "handoff_collect_name": False,
            "enable_rate_limiting": True,
            "overall_limit_per_ip": 20,
            "requests_per_sec": 0.5,
            "use_workflow": False,
            "allow_attachments": False,
            "require_token_auth": True,
        }
        if current_agent is None:
            current_agent = await self.client.create_agent(
                {
                    "name": agent_name,
                    "description": "Espelho visual seguro de um flow canônico do Meu Executivo.",
                    "agent_type": "custom",
                    "tools": [],
                    "is_default": False,
                    **agent_payload,
                }
            )
            agent_action = "created"
        elif any(current_agent.get(key) != value for key, value in agent_payload.items()):
            current_agent = await self.client.update_agent(
                str(current_agent["id"]),
                agent_payload,
            )
            agent_action = "updated"
        else:
            agent_action = "unchanged"
        agent_id = str(current_agent["id"])
        await self.links.upsert_link(
            workspace_id,
            ExternalEntityLinkUpsert(
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="flow",
                klm_entity_id=flow.id,
                external_account_id=organization_id,
                external_entity_type="workflow_agent",
                external_entity_id=agent_id,
                sync_state=ExternalEntitySyncState.synced,
                last_synced_at=datetime.now(UTC),
                metadata={
                    "managedBy": "klm-local-bridge",
                    "canonicalMarker": marker,
                    "sourceDigest": source_digest,
                },
            ),
        )

        workflow_name = _bounded_name("KLM · ", flow.name, flow.id)
        workflow_payload = {
            "name": workflow_name,
            "description": (
                f"{flow.description or 'Flow jurídico KLM.'}\n\n"
                "Espelho visual; execução e publicação permanecem no Meu Executivo."
            ),
            "status": "draft",
            "is_template": False,
            "default_language": "pt-BR",
            "canvas_data": {
                "source": "klm",
                "flowId": str(flow.id),
                "revision": flow.current_revision,
            },
            "settings": {
                "sourceOfTruth": "klm",
                "klmFlowId": str(flow.id),
                "klmRevision": flow.current_revision,
                "klmSourceDigest": source_digest,
                "externalExecutionEnabled": False,
                "publicationPolicy": "draft_only_local_mirror",
            },
            "agent_id": agent_id,
        }
        workflow = await self.client.workflow_for_agent(agent_id)
        if workflow is None:
            workflow = await self.client.create_workflow(workflow_payload)
            workflow_action = "created"
            current_graph: dict[str, Any] = {"nodes": [], "connections": []}
        else:
            comparable = {key: workflow.get(key) for key in workflow_payload if key != "agent_id"}
            desired_comparable = {
                key: value for key, value in workflow_payload.items() if key != "agent_id"
            }
            if comparable != desired_comparable or str(workflow.get("agent_id")) != agent_id:
                workflow = await self.client.update_workflow(
                    str(workflow["id"]),
                    workflow_payload,
                )
                workflow_action = "updated"
            else:
                workflow_action = "unchanged"
            current_graph = await self.client.workflow_nodes(str(workflow["id"]))

        desired_nodes, desired_connections = _project_graph(flow)
        desired_graph = {"nodes": desired_nodes, "connections": desired_connections}
        if _graph_signature(current_graph) != _graph_signature(desired_graph):
            await self.client.replace_workflow_nodes(
                str(workflow["id"]),
                nodes=desired_nodes,
                connections=desired_connections,
            )
            graph_action = "replaced"
        else:
            graph_action = "unchanged"

        workflow_id = str(workflow["id"])
        await self.links.upsert_link(
            workspace_id,
            ExternalEntityLinkUpsert(
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="flow",
                klm_entity_id=flow.id,
                external_account_id=organization_id,
                external_entity_type="workflow",
                external_entity_id=workflow_id,
                sync_state=ExternalEntitySyncState.synced,
                last_synced_at=datetime.now(UTC),
                metadata={
                    "managedBy": "klm-local-bridge",
                    "sourceDigest": source_digest,
                    "revision": flow.current_revision,
                    "agentId": agent_id,
                    "executionPolicy": "visual_mirror_only",
                },
            ),
        )
        actor_label = await resolve_human_actor_label(self.db, actor_user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="chattermate.flow_mirrored",
                entity_type="flow",
                entity_id=flow.id,
                summary=(
                    f"Flow espelhado no ChatterMate: agente {agent_action}, "
                    f"workflow {workflow_action}, grafo {graph_action}."
                ),
                correlation_id=f"chattermate:{workflow_id}",
                after_data={
                    "agentAction": agent_action,
                    "workflowAction": workflow_action,
                    "graphAction": graph_action,
                    "sourceDigest": source_digest,
                    "externalExecutionEnabled": False,
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.commit()
        invalidate_chattermate_context_cache(workspace_id)
        return {
            "status": "synced",
            "flowId": str(flow.id),
            "agentId": agent_id,
            "workflowId": workflow_id,
            "agentAction": agent_action,
            "workflowAction": workflow_action,
            "graphAction": graph_action,
            "nodeCount": len(desired_nodes),
            "connectionCount": len(desired_connections),
            "revision": flow.current_revision,
            "sourceDigest": source_digest,
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }

    async def context(self, workspace_id: UUID, flow_id: UUID) -> dict[str, Any]:
        flow = await self._flow(workspace_id, flow_id)
        identity = await self.client.identity()
        organization_id = str(identity["organization_id"])
        agent_link = await self.links.find_link_by_klm_entity(
            workspace_id,
            OSSBridgeProvider.chattermate,
            organization_id,
            "flow",
            flow.id,
            "workflow_agent",
        )
        workflow_link = await self.links.find_link_by_klm_entity(
            workspace_id,
            OSSBridgeProvider.chattermate,
            organization_id,
            "flow",
            flow.id,
            "workflow",
        )
        if not agent_link or not workflow_link:
            return {
                "linked": False,
                "flowId": str(flow.id),
                "revision": flow.current_revision,
                "sourceOfTruth": "klm",
                "externalExecutionEnabled": False,
            }
        workflow = await self.client.workflow_for_agent(agent_link.external_entity_id)
        if workflow is None or str(workflow.get("id")) != workflow_link.external_entity_id:
            return {
                "linked": False,
                "flowId": str(flow.id),
                "revision": flow.current_revision,
                "staleLink": True,
                "sourceOfTruth": "klm",
                "externalExecutionEnabled": False,
            }
        graph = await self.client.workflow_nodes(str(workflow["id"]))
        desired_nodes, desired_connections = _project_graph(flow)
        source_digest = _flow_digest(flow)
        return {
            "linked": True,
            "flowId": str(flow.id),
            "revision": flow.current_revision,
            "agentId": agent_link.external_entity_id,
            "workflowId": str(workflow["id"]),
            "workflowStatus": str(workflow.get("status") or "draft"),
            "nodeCount": len(graph.get("nodes", [])),
            "connectionCount": len(graph.get("connections", [])),
            "sourceDigest": source_digest,
            "digestMatches": (
                _dictionary(workflow.get("settings")).get("klmSourceDigest") == source_digest
            ),
            "graphMatches": _graph_signature(graph)
            == _graph_signature({"nodes": desired_nodes, "connections": desired_connections}),
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }
