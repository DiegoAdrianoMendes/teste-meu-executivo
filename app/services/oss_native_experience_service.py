"""Rich KLM-native experiences inspired by the reviewed community products.

These read models intentionally preserve the established SPA contracts while
using only KLM tenant-owned tables. Sidecar clients are never constructed here.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.flow import Flow
from app.models.honorarium_estimate import HonorariumEstimate
from app.models.klm_operations import (
    FollowUpStatus,
    FollowUpTask,
    KlmAuditEvent,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import KanbanColumnId, Matter
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.oss_bridge import ChatwootRoutingWrite
from app.services.klm_agent_service import AGENT_DEFINITIONS, KlmAgentService
from app.services.playbook_defaults import default_playbook_content

_CHAT_STATUS_BY_THREAD = {
    "open": "open",
    "waiting_documents": "pending",
    "waiting_human": "pending",
    "human_active": "open",
    "closed": "resolved",
    "blocked": "snoozed",
}

_STAGE_LABELS = {
    "novo_lead": "Novo lead",
    "triagem_sdr": "Triagem SDR",
    "documentos": "Documentos",
    "reuniao": "Reuniao",
    "proposta": "Proposta",
    "contrato": "Contrato",
    "pagamento": "Pagamento",
    "ganho": "Ganho",
    "perdido": "Perdido",
    "entrada": "Entrada",
    "qualificacao": "Qualificacao",
    "proposta_fechamento": "Proposta e fechamento",
    "coleta_dados": "Coleta de dados",
    "triagem_juridica": "Triagem juridica",
    "pesquisa_validacao": "Pesquisa e validacao",
    "redacao_peca": "Redacao de peca",
    "aprovacao_pendente": "Aprovacao pendente",
    "pronto_protocolo": "Pronto para protocolo",
    "migrar_sistema": "Migrar sistema",
    "assinado": "Assinado",
}

_LEGACY_AGENT_KEYS = {
    "analyst": "juridico",
    "drafter": "peticao",
}

_TIME_RANGE_DAYS = {
    "24h": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value))


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _money(value: Any) -> float:
    return float(Decimal(str(value or 0)))


def _stable_int(value: UUID) -> int:
    return max(1, int.from_bytes(value.bytes[:4], "big") & 0x7FFFFFFF)


def _age_days(value: datetime | None) -> int:
    if value is None:
        return 0
    normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - normalized).days)


def _json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _native_id(kind: str, value: UUID) -> str:
    return f"klm:{kind}:{value}"


def _canonical_agent_key(value: str) -> str:
    key = value.strip().lower().replace("-", "_")
    if key.startswith("klm_"):
        key = key[4:]
    return _LEGACY_AGENT_KEYS.get(key, key)


class OSSNativeExperienceService:
    """Compose mature OSS-inspired surfaces directly from KLM domain state."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _thread(self, workspace_id: UUID, thread_id: UUID) -> OmnichannelThread:
        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.id == thread_id,
            )
        )
        if thread is None:
            raise LookupError("Thread nao encontrada no workspace.")
        return thread

    async def _matter(self, workspace_id: UUID, matter_id: UUID) -> Matter:
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.workspace_id == workspace_id,
                Matter.id == matter_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise LookupError("Caso nao encontrado no workspace.")
        return matter

    async def _messages(
        self,
        workspace_id: UUID,
        thread_id: UUID,
    ) -> list[OmnichannelMessage]:
        return list(
            (
                await self.db.scalars(
                    select(OmnichannelMessage)
                    .where(
                        OmnichannelMessage.workspace_id == workspace_id,
                        OmnichannelMessage.thread_id == thread_id,
                    )
                    .order_by(OmnichannelMessage.occurred_at.asc())
                )
            ).all()
        )

    async def _members(
        self,
        workspace_id: UUID,
    ) -> list[tuple[WorkspaceMembership, User]]:
        rows = await self.db.execute(
            select(WorkspaceMembership, User)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(User.name.asc())
        )
        return list(rows.all())

    async def _audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        event_type: str,
        entity_type: str,
        entity_id: UUID | None,
        summary: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label="Usuario KLM",
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                before_data=before,
                after_data=after,
                occurred_at=datetime.now(UTC),
            )
        )

    async def _chat_projection(
        self,
        workspace_id: UUID,
        thread_id: UUID,
    ) -> dict[str, Any]:
        messages = await self._messages(workspace_id, thread_id)
        received = sum(message.direction == MessageDirection.inbound for message in messages)
        indexed = sum(
            bool((message.message_metadata or {}).get("nativeExperienceIndexedAt"))
            for message in messages
            if message.direction != MessageDirection.inbound
        )
        outbound = sum(message.direction != MessageDirection.inbound for message in messages)
        last_indexed = [
            str((message.message_metadata or {}).get("nativeExperienceIndexedAt"))
            for message in messages
            if (message.message_metadata or {}).get("nativeExperienceIndexedAt")
        ]
        return {
            "totalMessages": len(messages),
            "projectedMessages": indexed,
            "receivedMessages": received,
            "pendingMessages": max(0, outbound - indexed),
            "failedMessages": sum(
                message.delivery_status == MessageDeliveryStatus.failed
                for message in messages
            ),
            "lastSyncedAt": max(last_indexed) if last_indexed else None,
            "externalChannelDeliveryEnabled": False,
        }

    async def chat_context(
        self,
        workspace_id: UUID,
        thread_id: UUID,
    ) -> dict[str, Any]:
        thread = await self._thread(workspace_id, thread_id)
        metadata = dict(thread.thread_metadata or {})
        routing = dict(metadata.get("nativeRouting") or {})
        members = await self._members(workspace_id)
        agent_by_user = {_stable_int(user.id): user for _, user in members}
        assigned = (
            next(
                (
                    user
                    for _, user in members
                    if user.id == thread.assigned_human_id
                ),
                None,
            )
            if thread.assigned_human_id
            else None
        )
        labels = list(dict.fromkeys(str(item) for item in routing.get("labels", [])))
        known_labels = list(
            dict.fromkeys(
                [
                    *labels,
                    "urgente",
                    "documentos-pendentes",
                    "reuniao",
                    "proposta",
                    "handoff-humano",
                ]
            )
        )
        teams: list[dict[str, Any]] = []
        for index, role in enumerate(
            sorted({_enum(membership.role) for membership, _ in members}),
            start=1,
        ):
            teams.append({"id": index, "name": role.replace("_", " ").title()})
        playbook = default_playbook_content()
        canned = [
            {
                "id": index,
                "short_code": objection.id,
                "content": (
                    f"{objection.confirmation_question} {objection.allowed_response}"
                ),
            }
            for index, objection in enumerate(playbook.objections, start=1)
        ]
        status = str(
            routing.get("status")
            or _CHAT_STATUS_BY_THREAD.get(_enum(thread.status), "open")
        )
        conversation = {
            "id": _native_id("conversation", thread.id),
            "status": status,
            "priority": str(routing.get("priority") or "medium"),
            "labels": labels,
            "created_at": _iso(thread.created_at),
            "updated_at": _iso(thread.updated_at),
            "assignee": (
                {"id": _stable_int(assigned.id), "name": assigned.name}
                if assigned
                else None
            ),
            "team": next(
                (team for team in teams if team["id"] == routing.get("teamId")),
                None,
            ),
            "inbox": {"id": 1, "name": "Atendimento omnichannel KLM"},
            "engine": "klm_native",
            "externalNetworkExecuted": False,
        }
        return {
            "linked": True,
            "threadId": thread.id,
            "conversationId": _native_id("conversation", thread.id),
            "conversation": conversation,
            "inboxes": [{"id": 1, "name": "Atendimento omnichannel KLM"}],
            "teams": teams,
            "labels": [
                {"id": index, "title": label}
                for index, label in enumerate(known_labels, start=1)
            ],
            "agents": [
                {
                    "id": identifier,
                    "name": user.name,
                    "role": _enum(user.role),
                    "availability": "online" if user.is_active else "offline",
                }
                for identifier, user in agent_by_user.items()
            ],
            "cannedResponses": canned,
            "projection": await self._chat_projection(workspace_id, thread_id),
            "sourceOfTruth": "klm",
        }

    async def update_chat_routing(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        changes: ChatwootRoutingWrite,
    ) -> dict[str, Any]:
        thread = await self._thread(workspace_id, thread_id)
        metadata = dict(thread.thread_metadata or {})
        before = dict(metadata.get("nativeRouting") or {})
        routing = dict(before)
        values = changes.model_dump(exclude_unset=True)
        if "status" in values:
            routing["status"] = values["status"]
        if "priority" in values:
            routing["priority"] = values["priority"]
        if "labels" in values:
            routing["labels"] = values["labels"] or []
        if "team_id" in values:
            routing["teamId"] = values["team_id"]
        if "assignee_id" in values:
            assignee_id = values["assignee_id"]
            if assignee_id is None:
                thread.assigned_human_id = None
            else:
                members = await self._members(workspace_id)
                matched = next(
                    (user for _, user in members if _stable_int(user.id) == assignee_id),
                    None,
                )
                if matched is None:
                    raise LookupError("Atendente nao pertence ao workspace.")
                thread.assigned_human_id = matched.id
        routing["updatedAt"] = datetime.now(UTC).isoformat()
        routing["updatedByUserId"] = str(actor_user_id)
        metadata["nativeRouting"] = routing
        thread.thread_metadata = metadata
        thread.updated_at = datetime.now(UTC)
        await self._audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            event_type="native_chat_routing_updated",
            entity_type="omnichannel_thread",
            entity_id=thread.id,
            summary="Roteamento do atendimento nativo atualizado.",
            before=before,
            after=routing,
        )
        await self.db.flush()
        return await self.chat_context(workspace_id, thread_id)

    async def reconcile_chat(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        await self._thread(workspace_id, thread_id)
        messages = await self._messages(workspace_id, thread_id)
        now = datetime.now(UTC).isoformat()
        indexed_now = 0
        for message in messages:
            if message.direction == MessageDirection.inbound:
                continue
            metadata = dict(message.message_metadata or {})
            if metadata.get("nativeExperienceIndexedAt"):
                continue
            metadata["nativeExperienceIndexedAt"] = now
            metadata["nativeExperienceEngine"] = "klm_native"
            message.message_metadata = metadata
            indexed_now += 1
        await self._audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            event_type="native_chat_reconciled",
            entity_type="omnichannel_thread",
            entity_id=thread_id,
            summary=f"Contexto nativo reconciliado: {indexed_now} mensagem(ns) indexada(s).",
            after={"indexedNow": indexed_now, "externalNetworkExecuted": False},
        )
        await self.db.flush()
        projection = await self._chat_projection(workspace_id, thread_id)
        return {
            "status": "synced",
            "threadId": thread_id,
            "conversationId": _native_id("conversation", thread_id),
            **projection,
            "projectedNow": indexed_now,
            "contactCreated": False,
            "rateLimited": False,
            "sourceOfTruth": "klm",
        }

    async def collaboration_context(
        self,
        workspace_id: UUID,
        thread_id: UUID,
    ) -> dict[str, Any]:
        thread = await self._thread(workspace_id, thread_id)
        messages = [
            message
            for message in await self._messages(workspace_id, thread_id)
            if message.direction == MessageDirection.internal
            or message.channel == OmnichannelChannel.internal
        ]
        members = await self._members(workspace_id)
        discussions: dict[str, dict[str, Any]] = {}
        serialized_messages: list[dict[str, Any]] = []
        for message in messages:
            metadata = dict(message.message_metadata or {})
            attachments = metadata.get("attachments")
            reactions = metadata.get("reactions")
            discussion = str(metadata.get("discussion") or "").strip()
            if discussion:
                current = discussions.setdefault(
                    discussion,
                    {
                        "_id": _json_digest([thread.id, discussion])[:20],
                        "fname": discussion,
                        "name": discussion,
                        "msgs": 0,
                        "ts": _iso(message.occurred_at),
                        "lm": _iso(message.occurred_at),
                    },
                )
                current["msgs"] += 1
                current["lm"] = _iso(message.occurred_at)
            serialized_messages.append(
                {
                    "_id": str(message.id),
                    "msg": message.body,
                    "user": {
                        "_id": str(message.author_user_id or "system"),
                        "name": message.author_label,
                        "username": message.author_label.casefold().replace(" ", "."),
                    },
                    "ts": _iso(message.occurred_at),
                    "attachments": attachments if isinstance(attachments, list) else [],
                    "reactions": reactions if isinstance(reactions, list) else [],
                    "tcount": int(metadata.get("replyCount") or 0),
                }
            )
        room_name = thread.subject or thread.object_name or "Sala interna do caso"
        room_id = _native_id("room", thread.id)
        return {
            "linked": True,
            "threadId": thread.id,
            "roomId": room_id,
            "roomType": "p",
            "room": {
                "_id": room_id,
                "name": f"caso-{str(thread.id)[:8]}",
                "fname": room_name,
                "engine": "klm_native",
            },
            "members": [
                {
                    "_id": str(user.id),
                    "name": user.name,
                    "username": (user.email or user.name).split("@")[0],
                    "status": "online" if user.is_active else "offline",
                    "role": _enum(membership.role),
                }
                for membership, user in members
            ],
            "messages": serialized_messages,
            "discussions": list(discussions.values()),
            "runtimeCompatibility": "klm-native-1",
            "auditedReference": "Rocket.Chat feature contract",
            "sourceOfTruth": "klm",
            "sensitiveDataHidden": False,
        }

    async def reconcile_collaboration(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        context = await self.collaboration_context(workspace_id, thread_id)
        await self._audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            event_type="native_collaboration_reconciled",
            entity_type="omnichannel_thread",
            entity_id=thread_id,
            summary="Sala interna nativa reconciliada sem acesso externo.",
            after={
                "messagesSeen": len(context["messages"]),
                "externalNetworkExecuted": False,
            },
        )
        await self.db.flush()
        return {
            "status": "synced",
            "threadId": thread_id,
            "roomId": context["roomId"],
            "messagesSeen": len(context["messages"]),
            "messagesProjected": 0,
            "externalNetworkExecuted": False,
        }

    async def agent_context(
        self,
        workspace_id: UUID,
        time_range: str,
        *,
        include_sensitive: bool = True,
    ) -> dict[str, Any]:
        registry = await KlmAgentService(self.db).registry(workspace_id)
        range_days = _TIME_RANGE_DAYS.get(time_range, 7)
        range_started_at = datetime.now(UTC) - timedelta(days=range_days)
        flows = list(
            (
                await self.db.scalars(
                    select(Flow)
                    .where(
                        Flow.workspace_id == workspace_id,
                        Flow.deleted_at.is_(None),
                    )
                    .order_by(Flow.updated_at.desc())
                )
            ).all()
        )
        runs = list(
            (
                await self.db.scalars(
                    select(AgentRun)
                    .where(
                        AgentRun.workspace_id == workspace_id,
                        AgentRun.created_at >= range_started_at,
                    )
                    .order_by(AgentRun.created_at.desc())
                    .limit(250)
                )
            ).all()
        )
        clients = int(
            await self.db.scalar(
                select(func.count(Client.id)).where(
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
            or 0
        )
        matters = int(
            await self.db.scalar(
                select(func.count(Matter.id)).where(
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            or 0
        )
        new_leads_7d = int(
            await self.db.scalar(
                select(func.count(Client.id)).where(
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                    Client.created_at >= datetime.now(UTC) - timedelta(days=7),
                )
            )
            or 0
        )
        recent_clients = list(
            (
                await self.db.scalars(
                    select(Client)
                    .where(
                        Client.workspace_id == workspace_id,
                        Client.deleted_at.is_(None),
                    )
                    .order_by(Client.created_at.desc())
                    .limit(20)
                )
            ).all()
        )
        recent_matters = (
            list(
                (
                    await self.db.scalars(
                        select(Matter)
                        .where(
                            Matter.workspace_id == workspace_id,
                            Matter.client_id.in_([client.id for client in recent_clients]),
                            Matter.deleted_at.is_(None),
                        )
                        .order_by(Matter.updated_at.desc(), Matter.created_at.desc())
                    )
                ).all()
            )
            if recent_clients
            else []
        )
        knowledge = list(
            (
                await self.db.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.workspace_id == workspace_id)
                    .order_by(KnowledgeChunk.updated_at.desc())
                    .limit(50)
                )
            ).all()
        )
        run_counts = Counter(_enum(run.status) for run in runs)
        run_counts_by_agent: dict[str, Counter[str]] = defaultdict(Counter)
        for run in runs:
            stats = run_counts_by_agent[_canonical_agent_key(run.agent_name)]
            stats["total_runs"] += 1
            stats[f"status:{_enum(run.status)}"] += 1
            stats["input_tokens"] += run.input_tokens
            stats["output_tokens"] += run.output_tokens
        matters_by_client: dict[UUID, list[Matter]] = defaultdict(list)
        for matter in recent_matters:
            matters_by_client[matter.client_id].append(matter)
        agents = [
            {
                "id": f"klm-agent:{row['key']}",
                "name": f"KLM - {row['name']}",
                "display_name": row["name"],
                "description": row["description"],
                "is_active": row["state"] != "paused",
                "state": row["state"],
                "quality_score": row["qualityScore"],
                "queue_size": row["queueSize"],
                "transfer_to_human": bool(row["permissions"]["requestHuman"]),
                "tools": row["tools"],
                "runtime": row["modelPolicy"]["strategy"],
            }
            for row in registry["agents"]
        ]
        tools = sorted(
            {
                tool
                for definition in AGENT_DEFINITIONS
                for tool in definition.tools
            }
        )
        groups = [
            {
                "id": _json_digest(group)[:16],
                "name": group,
                "agents": [
                    definition.key
                    for definition in AGENT_DEFINITIONS
                    if definition.group == group
                ],
            }
            for group in sorted({definition.group for definition in AGENT_DEFINITIONS})
        ]
        return {
            "linked": True,
            "organizationId": _native_id("workspace", workspace_id),
            "agents": agents,
            "workflows": [
                {
                    "id": str(flow.id),
                    "name": flow.name,
                    "description": flow.description,
                    "status": "active" if flow.is_active else "paused",
                    "trigger": flow.trigger,
                    "revision": flow.current_revision,
                    "node_count": len(flow.nodes or []),
                    "connection_count": len(flow.edges or []),
                    "run_count": flow.run_count,
                }
                for flow in flows
            ],
            "analytics": {
                "runs": len(runs),
                "by_status": dict(sorted(run_counts.items())),
                "total_tokens": sum(run.input_tokens + run.output_tokens for run in runs),
                "external_actions_executed": 0,
            },
            "performance": {
                "bot_agents": [
                    {
                        "id": agent["id"],
                        "name": agent["display_name"],
                        "quality_score": agent["quality_score"],
                        "queue_size": agent["queue_size"],
                        "total_chats": run_counts_by_agent[
                            agent["id"].removeprefix("klm-agent:")
                        ]["total_runs"],
                        "completed_runs": run_counts_by_agent[
                            agent["id"].removeprefix("klm-agent:")
                        ][f"status:{AgentRunStatus.completed.value}"],
                        "failed_runs": run_counts_by_agent[
                            agent["id"].removeprefix("klm-agent:")
                        ][f"status:{AgentRunStatus.failed.value}"],
                        "waiting_human_runs": run_counts_by_agent[
                            agent["id"].removeprefix("klm-agent:")
                        ][f"status:{AgentRunStatus.waiting_human.value}"],
                    }
                    for agent in agents
                ],
                "human_agents": [
                    {
                        "id": str(user.id),
                        "name": user.name,
                        "active": user.is_active,
                        "triggered_runs": sum(
                            run.triggered_by_user_id == user.id for run in runs
                        ),
                    }
                    for _, user in await self._members(workspace_id)
                ],
            },
            "people": {
                "items": [
                    {
                        "id": str(client.id),
                        "name": client.name if include_sensitive else "Pessoa restrita",
                        "stage": (
                            _enum(matters_by_client[client.id][0].column_id)
                            if matters_by_client[client.id]
                            else "sem_caso"
                        ),
                        "active_matters": len(matters_by_client[client.id]),
                    }
                    for client in recent_clients
                ],
                "total": clients,
            },
            "peopleStats": {
                "total_people": clients,
                "active_matters": matters,
                "new_leads_7d": new_leads_7d,
            },
            "mcpTools": [
                {
                    "id": index,
                    "name": tool,
                    "status": "local_ready",
                    "external": False,
                }
                for index, tool in enumerate(tools, start=1)
            ],
            "groups": groups,
            "notifications": [
                {
                    "id": f"run:{run.id}",
                    "title": f"{run.agent_name}: {_enum(run.status)}",
                    "message": run.error_message or "Execucao requer acompanhamento.",
                    "created_at": _iso(run.created_at),
                }
                for run in runs
                if run.status in {AgentRunStatus.failed, AgentRunStatus.waiting_human}
            ][:20],
            "knowledge": {
                "knowledge": [
                    {
                        "id": str(chunk.id),
                        "name": chunk.title or chunk.source_type,
                        "type": chunk.source_type,
                        "title": chunk.title or chunk.source_type,
                        "source_type": chunk.source_type,
                        "summary": chunk.summary or chunk.content_text[:180],
                        "matter_id": str(chunk.matter_id),
                        "tags": chunk.tags,
                    }
                    for chunk in knowledge
                ],
                "pagination": {"total": len(knowledge), "limit": 50},
                "queueItems": [],
            },
            "timeRange": time_range,
            "sourceOfTruth": "klm",
            "sensitiveDataHidden": not include_sensitive,
            "externalExecutionEnabled": False,
        }

    async def reconcile_agents(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        registry = await KlmAgentService(self.db).registry(workspace_id)
        await self._audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            event_type="native_agents_reconciled",
            entity_type="agent_registry",
            entity_id=None,
            summary=f"Registro nativo reconciliado: {len(registry['agents'])} agentes.",
            after={
                "seen": len(registry["agents"]),
                "externalNetworkExecuted": False,
            },
        )
        await self.db.flush()
        return {
            "status": "synced",
            "created": 0,
            "updated": 0,
            "adopted": len(registry["agents"]),
            "conflicts": 0,
            "seen": len(registry["agents"]),
        }

    async def flow_context(
        self,
        workspace_id: UUID,
        flow_id: UUID,
    ) -> dict[str, Any]:
        flow = await self.db.scalar(
            select(Flow).where(
                Flow.workspace_id == workspace_id,
                Flow.id == flow_id,
                Flow.deleted_at.is_(None),
            )
        )
        if flow is None:
            raise LookupError("Flow nao encontrado no workspace.")
        graph = {
            "flowId": str(flow.id),
            "revision": flow.current_revision,
            "nodes": flow.nodes or [],
            "edges": flow.edges or [],
        }
        digest = _json_digest(graph)
        return {
            "linked": True,
            "flowId": flow.id,
            "revision": flow.current_revision,
            "agentId": f"klm-flow-agent:{flow.id}",
            "workflowId": f"klm-workflow:{flow.id}",
            "workflowStatus": "active" if flow.is_active else "paused",
            "nodeCount": len(flow.nodes or []),
            "connectionCount": len(flow.edges or []),
            "sourceDigest": digest,
            "digestMatches": True,
            "graphMatches": True,
            "staleLink": False,
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }

    async def reconcile_flow(
        self,
        *,
        workspace_id: UUID,
        flow_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        context = await self.flow_context(workspace_id, flow_id)
        await self._audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            event_type="native_flow_reconciled",
            entity_type="flow",
            entity_id=flow_id,
            summary="Grafo do flow nativo validado e reconciliado.",
            after={
                "sourceDigest": context["sourceDigest"],
                "nodeCount": context["nodeCount"],
                "connectionCount": context["connectionCount"],
                "externalNetworkExecuted": False,
            },
        )
        await self.db.flush()
        return {
            "status": "synced",
            "flowId": flow_id,
            "agentId": context["agentId"],
            "workflowId": context["workflowId"],
            "agentAction": "unchanged",
            "workflowAction": "unchanged",
            "graphAction": "unchanged",
            "nodeCount": context["nodeCount"],
            "connectionCount": context["connectionCount"],
            "revision": context["revision"],
            "sourceDigest": context["sourceDigest"],
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }

    async def _matters(self, workspace_id: UUID) -> list[Matter]:
        return list(
            (
                await self.db.scalars(
                    select(Matter)
                    .where(
                        Matter.workspace_id == workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                    .order_by(Matter.card_order.asc(), Matter.updated_at.desc())
                )
            ).all()
        )

    async def _tasks(self, workspace_id: UUID) -> list[Task]:
        return list(
            (
                await self.db.scalars(
                    select(Task)
                    .join(Matter, Matter.id == Task.matter_id)
                    .where(
                        Matter.workspace_id == workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                    .order_by(Task.created_at.desc())
                )
            ).all()
        )

    async def _crm_projection(
        self,
        workspace_id: UUID,
        *,
        matters: list[Matter] | None = None,
        tasks: list[Task] | None = None,
    ) -> dict[str, Any]:
        matter_rows = matters if matters is not None else await self._matters(workspace_id)
        task_rows = tasks if tasks is not None else await self._tasks(workspace_id)
        clients = int(
            await self.db.scalar(
                select(func.count(Client.id)).where(
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
            or 0
        )
        finance = int(
            await self.db.scalar(
                select(func.count(FinanceEntry.id)).where(
                    FinanceEntry.workspace_id == workspace_id,
                    FinanceEntry.deleted_at.is_(None),
                )
            )
            or 0
        )
        by_entity = {
            "contact": clients,
            "lead": len(matter_rows),
            "opportunity": len(matter_rows),
            "case": len(matter_rows),
            "task": len(task_rows),
            "finance_entry": finance,
        }
        total = sum(by_entity.values())
        updated = [matter.updated_at for matter in matter_rows if matter.updated_at]
        updated.extend(task.updated_at for task in task_rows if task.updated_at)
        return {
            "linked": True,
            "organizationId": _native_id("workspace", workspace_id),
            "totalLinks": total,
            "syncedLinks": total,
            "failedLinks": 0,
            "staleLinks": 0,
            "byEntityType": by_entity,
            "lastSyncedAt": _iso(max(updated)) if updated else None,
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }

    @staticmethod
    def _matter_amount(matter: Matter) -> float:
        return _money(matter.economic_value_annual or matter.claim_value)

    @staticmethod
    def _matter_item(matter: Matter, *, include_sensitive: bool) -> dict[str, Any]:
        stage = _enum(matter.column_id)
        amount = OSSNativeExperienceService._matter_amount(matter)
        days = _age_days(matter.updated_at or matter.created_at)
        probability = min(100, max(0, int(matter.lead_score or 0)))
        return {
            "id": str(matter.id),
            "entityType": "opportunity",
            "klmEntityId": matter.id,
            "klmEntityType": "matter",
            "name": (
                matter.health_object_name or matter.demand_type
                if include_sensitive
                else "Oportunidade restrita"
            ),
            "stage": stage,
            "status": _enum(matter.micro_status),
            "priority": _enum(matter.priority),
            "amount": amount,
            "currency": "BRL",
            "probability": probability,
            "daysInStage": days,
            "agingStatus": "red" if days > 14 else "yellow" if days > 7 else "green",
            "dueDate": matter.next_deadline,
            "nextFollowUp": matter.next_action,
            "overdue": False,
            "slaBreached": bool(matter.sla_due_at < datetime.now(UTC))
            if getattr(matter, "sla_due_at", None)
            else False,
            "escalationCount": len(matter.blocks or []),
        }

    async def crm_funnel_context(
        self,
        workspace_id: UUID,
        *,
        include_sensitive: bool,
    ) -> dict[str, Any]:
        matters = await self._matters(workspace_id)
        tasks = await self._tasks(workspace_id)
        grouped: dict[str, list[Matter]] = defaultdict(list)
        for matter in matters:
            grouped[_enum(matter.column_id)].append(matter)
        columns: list[dict[str, Any]] = []
        pipeline: list[dict[str, Any]] = []
        for order, stage in enumerate(KanbanColumnId):
            key = stage.value
            stage_matters = grouped.get(key, [])
            value = sum(self._matter_amount(matter) for matter in stage_matters)
            items = [
                self._matter_item(matter, include_sensitive=include_sensitive)
                for matter in stage_matters
            ]
            columns.append(
                {
                    "id": key,
                    "name": _STAGE_LABELS.get(key, key.replace("_", " ").title()),
                    "order": order,
                    "color": None,
                    "stageType": "closed" if key in {"ganho", "perdido"} else "open",
                    "wipLimit": None,
                    "itemCount": len(items),
                    "items": items,
                }
            )
            if stage_matters:
                pipeline.append(
                    {
                        "id": key,
                        "label": _STAGE_LABELS.get(key, key.replace("_", " ").title()),
                        "count": len(stage_matters),
                        "value": value,
                    }
                )
        total_value = sum(self._matter_amount(matter) for matter in matters)
        weighted = sum(
            self._matter_amount(matter) * min(100, max(0, matter.lead_score or 0)) / 100
            for matter in matters
        )
        won = len(grouped.get("ganho", []))
        finished = won + len(grouped.get("perdido", []))
        today = date.today().isoformat()
        overdue_tasks = sum(
            bool(task.due_date and task.due_date < today and task.status != TaskStatus.done)
            for task in tasks
        )
        followups_today = int(
            await self.db.scalar(
                select(func.count(FollowUpTask.id)).where(
                    FollowUpTask.workspace_id == workspace_id,
                    FollowUpTask.status.in_([FollowUpStatus.scheduled, FollowUpStatus.due]),
                    func.date(FollowUpTask.due_at) == date.today(),
                )
            )
            or 0
        )
        notifications = int(
            await self.db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.workspace_id == workspace_id,
                    Notification.is_read.is_(False),
                )
            )
            or 0
        )
        return {
            "projection": await self._crm_projection(
                workspace_id, matters=matters, tasks=tasks
            ),
            "counts": {
                "accounts": 1,
                "contacts": int(
                    await self.db.scalar(
                        select(func.count(Client.id)).where(
                            Client.workspace_id == workspace_id,
                            Client.deleted_at.is_(None),
                        )
                    )
                    or 0
                ),
                "leads": len(matters),
                "opportunities": len(matters),
            },
            "urgentCounts": {
                "overdue_tasks": overdue_tasks,
                "followups_today": followups_today,
                "hot_leads": sum((matter.lead_score or 0) >= 70 for matter in matters),
            },
            "pipeline": pipeline,
            "revenue": {
                "total_pipeline": total_value,
                "weighted_pipeline": weighted,
                "conversion_rate": round(100 * won / finished, 2) if finished else 0,
                "currency": "BRL",
            },
            "opportunityColumns": columns,
            "leadColumns": columns,
            "caseColumns": columns,
            "goals": [],
            "agingConfig": [
                {"stage": stage.value, "expectedDays": 7, "warningDays": 5}
                for stage in KanbanColumnId
            ],
            "notificationsUnread": notifications,
            "sensitiveDataHidden": not include_sensitive,
        }

    async def crm_matter_context(
        self,
        workspace_id: UUID,
        matter_id: UUID,
        *,
        include_sensitive: bool,
    ) -> dict[str, Any]:
        matter = await self._matter(workspace_id, matter_id)
        client = await self.db.scalar(
            select(Client).where(
                Client.workspace_id == workspace_id,
                Client.id == matter.client_id,
                Client.deleted_at.is_(None),
            )
        )
        tasks = list(
            (
                await self.db.scalars(
                    select(Task).where(Task.matter_id == matter.id).order_by(Task.created_at.desc())
                )
            ).all()
        )
        label = (
            matter.health_object_name or matter.demand_type
            if include_sensitive
            else "Caso restrito"
        )
        common = {
            "currency": "BRL",
            "overdue": False,
            "slaBreached": False,
            "escalationCount": 0,
            "syncState": "synced",
            "lastSyncedAt": _iso(matter.updated_at),
        }
        records: list[dict[str, Any]] = []
        if client is not None:
            records.append(
                {
                    **common,
                    "id": str(client.id),
                    "entityType": "contact",
                    "klmEntityId": client.id,
                    "klmEntityType": "client",
                    "name": client.name if include_sensitive else "Contato restrito",
                    "status": "active",
                }
            )
        for entity_type in ("lead", "opportunity", "case"):
            records.append(
                {
                    **common,
                    "id": f"{entity_type}:{matter.id}",
                    "entityType": entity_type,
                    "klmEntityId": matter.id,
                    "klmEntityType": "matter",
                    "name": label,
                    "stage": _enum(matter.column_id),
                    "status": _enum(matter.micro_status),
                    "priority": _enum(matter.priority),
                    "amount": self._matter_amount(matter),
                    "probability": min(100, max(0, matter.lead_score or 0)),
                    "daysInStage": _age_days(matter.updated_at or matter.created_at),
                    "agingStatus": "green",
                    "dueDate": matter.next_deadline,
                    "nextFollowUp": matter.next_action,
                }
            )
        records.extend(
            {
                **common,
                "id": str(task.id),
                "entityType": "task",
                "klmEntityId": task.id,
                "klmEntityType": "task",
                "name": task.title if include_sensitive else "Tarefa restrita",
                "status": _enum(task.status),
                "priority": _enum(task.priority) if task.priority else None,
                "dueDate": task.due_date,
                "overdue": bool(
                    task.due_date
                    and task.due_date < date.today().isoformat()
                    and task.status != TaskStatus.done
                ),
            }
            for task in tasks
        )
        return {
            "projection": await self._crm_projection(
                workspace_id, matters=[matter], tasks=tasks
            ),
            "matterId": matter.id,
            "records": records,
            "sensitiveDataHidden": not include_sensitive,
        }

    async def crm_work_context(
        self,
        workspace_id: UUID,
        *,
        include_sensitive: bool,
    ) -> dict[str, Any]:
        tasks = await self._tasks(workspace_id)
        matters = await self._matters(workspace_id)
        matter_by_id = {matter.id: matter for matter in matters}
        grouped: dict[str, list[Task]] = defaultdict(list)
        for task in tasks:
            grouped[_enum(task.status)].append(task)
        items: list[dict[str, Any]] = []
        columns: list[dict[str, Any]] = []
        for order, status in enumerate(TaskStatus):
            status_tasks = grouped.get(status.value, [])
            column_items: list[dict[str, Any]] = []
            for task in status_tasks:
                matter = matter_by_id.get(task.matter_id)
                item = {
                    "id": str(task.id),
                    "entityType": "task",
                    "klmEntityId": task.id,
                    "klmEntityType": "task",
                    "name": task.title if include_sensitive else "Tarefa restrita",
                    "stage": _enum(matter.column_id) if matter else None,
                    "status": status.value,
                    "priority": _enum(task.priority) if task.priority else None,
                    "currency": "BRL",
                    "dueDate": task.due_date,
                    "overdue": bool(
                        task.due_date
                        and task.due_date < date.today().isoformat()
                        and task.status != TaskStatus.done
                    ),
                    "slaBreached": False,
                    "escalationCount": 0,
                }
                column_items.append(item)
                items.append(item)
            columns.append(
                {
                    "id": status.value,
                    "name": status.value.replace("_", " ").title(),
                    "order": order,
                    "color": None,
                    "stageType": "closed" if status == TaskStatus.done else "open",
                    "wipLimit": None,
                    "itemCount": len(column_items),
                    "items": column_items,
                }
            )
        flows = int(
            await self.db.scalar(
                select(func.count(Flow.id)).where(
                    Flow.workspace_id == workspace_id,
                    Flow.deleted_at.is_(None),
                )
            )
            or 0
        )
        notifications = int(
            await self.db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.workspace_id == workspace_id,
                    Notification.is_read.is_(False),
                )
            )
            or 0
        )
        return {
            "projection": await self._crm_projection(
                workspace_id, matters=matters, tasks=tasks
            ),
            "taskColumns": columns,
            "tasks": items,
            "boards": [
                {
                    "id": "legal-pipeline",
                    "name": "Pipeline juridico",
                    "description": "Cards e etapas canônicas do KLM.",
                    "columnCount": len(KanbanColumnId),
                },
                {
                    "id": "automation-flows",
                    "name": "Automacoes",
                    "description": f"{flows} flow(s) no workspace.",
                    "columnCount": 5,
                },
            ],
            "notificationsUnread": notifications,
            "sensitiveDataHidden": not include_sensitive,
        }

    async def crm_finance_context(
        self,
        workspace_id: UUID,
        *,
        include_sensitive: bool,
    ) -> dict[str, Any]:
        entries = list(
            (
                await self.db.scalars(
                    select(FinanceEntry)
                    .where(
                        FinanceEntry.workspace_id == workspace_id,
                        FinanceEntry.deleted_at.is_(None),
                    )
                    .order_by(FinanceEntry.created_at.desc())
                )
            ).all()
        )
        matters = await self._matters(workspace_id)
        estimates = list(
            (
                await self.db.scalars(
                    select(HonorariumEstimate)
                    .join(Matter, Matter.id == HonorariumEstimate.matter_id)
                    .where(
                        Matter.workspace_id == workspace_id,
                        Matter.deleted_at.is_(None),
                    )
                    .order_by(HonorariumEstimate.updated_at.desc())
                )
            ).all()
        )
        task_rows = await self._tasks(workspace_id)
        receivables = [
            entry for entry in entries if entry.entry_type == FinanceEntryType.receivable
        ]
        paid = [entry for entry in receivables if entry.status == FinanceEntryStatus.paid]
        due = [
            entry
            for entry in receivables
            if entry.status
            in {
                FinanceEntryStatus.pending,
                FinanceEntryStatus.scheduled,
                FinanceEntryStatus.overdue,
            }
        ]
        overdue = [
            entry
            for entry in due
            if entry.status == FinanceEntryStatus.overdue
            or bool(entry.due_date and entry.due_date.date() < date.today())
        ]
        status_counts = Counter(_enum(entry.status) for entry in entries)
        aging = Counter(
            {
                "current": 0.0,
                "1_30_days": 0.0,
                "31_60_days": 0.0,
                "61_90_days": 0.0,
                "over_90_days": 0.0,
            }
        )
        aging_counts = Counter({key: 0 for key in aging})
        for entry in due:
            if entry.due_date is None or entry.due_date.date() >= date.today():
                bucket = "current"
            else:
                days = (date.today() - entry.due_date.date()).days
                bucket = (
                    "1_30_days"
                    if days <= 30
                    else "31_60_days"
                    if days <= 60
                    else "61_90_days"
                    if days <= 90
                    else "over_90_days"
                )
            aging[bucket] += _money(entry.amount)
            aging_counts[bucket] += 1
        invoices = [
            {
                "id": str(entry.id),
                "entityType": "invoice",
                "name": entry.title if include_sensitive else "Fatura restrita",
                "number": entry.external_ref,
                "status": _enum(entry.status),
                "amount": _money(entry.amount),
                "amountDue": (
                    0.0
                    if entry.status == FinanceEntryStatus.paid
                    else _money(entry.amount)
                ),
                "currency": entry.currency,
                "issueDate": _iso(entry.created_at),
                "dueDate": _iso(entry.due_date),
                "validUntil": None,
                "category": _enum(entry.entry_type),
                "active": entry.status != FinanceEntryStatus.cancelled,
            }
            for entry in receivables
        ]
        estimate_rows = [
            {
                "id": str(estimate.id),
                "entityType": "estimate",
                "name": (
                    f"Honorarios - {estimate.contract_type or 'proposta'}"
                    if include_sensitive
                    else "Estimativa restrita"
                ),
                "number": None,
                "status": "draft",
                "amount": _money(estimate.total_estimated_value or estimate.fixed_fee),
                "amountDue": None,
                "currency": "BRL",
                "issueDate": _iso(estimate.created_at),
                "dueDate": None,
                "validUntil": None,
                "category": estimate.contract_type,
                "active": True,
            }
            for estimate in estimates
        ]
        revenue_by_month: dict[str, list[FinanceEntry]] = defaultdict(list)
        for entry in paid:
            when = entry.paid_at or entry.updated_at or entry.created_at
            revenue_by_month[when.strftime("%Y-%m")].append(entry)
        notifications = int(
            await self.db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.workspace_id == workspace_id,
                    Notification.is_read.is_(False),
                )
            )
            or 0
        )
        return {
            "projection": await self._crm_projection(
                workspace_id, matters=matters, tasks=task_rows
            ),
            "summary": {
                "total_invoiced": sum(_money(entry.amount) for entry in receivables),
                "total_paid": sum(_money(entry.amount) for entry in paid),
                "total_due": sum(_money(entry.amount) for entry in due),
                "currency": "BRL",
            },
            "statusCounts": dict(sorted(status_counts.items())),
            "overdue": {
                "count": len(overdue),
                "amount": sum(_money(entry.amount) for entry in overdue),
            },
            "recentActivity": {
                "entries": len(entries),
                "paid": len(paid),
                "pending": len(due),
            },
            "estimateSummary": {
                "count": len(estimates),
                "active": len(estimates),
            },
            "invoices": invoices,
            "estimates": estimate_rows,
            "products": [
                {
                    "id": "legal-health-plan-denial",
                    "entityType": "product",
                    "name": "Atuacao em negativa de plano de saude",
                    "status": "active",
                    "amount": 4000.0,
                    "currency": "BRL",
                    "category": "servico_juridico",
                    "active": True,
                }
            ],
            "aging": [
                {"bucket": key, "count": aging_counts[key], "amount": amount}
                for key, amount in aging.items()
            ],
            "revenueSeries": [
                {
                    "period": period,
                    "revenue": sum(_money(entry.amount) for entry in rows),
                    "count": len(rows),
                }
                for period, rows in sorted(revenue_by_month.items())
            ],
            "notificationsUnread": notifications,
            "sensitiveDataHidden": not include_sensitive,
        }


__all__ = ["OSSNativeExperienceService"]
