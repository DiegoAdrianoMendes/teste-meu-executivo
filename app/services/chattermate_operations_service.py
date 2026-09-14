"""KLM-canonical facade for local ChatterMate agents and operational telemetry."""

from __future__ import annotations

import asyncio
import copy
import fcntl
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import KlmAuditEvent
from app.models.oss_bridge import ExternalEntityLink, ExternalEntitySyncState, OSSBridgeProvider
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.chattermate_client import ChatterMateClient
from app.services.klm_agent_service import AGENT_DEFINITIONS, KlmAgentService
from app.services.oss_bridge_service import OSSBridgeService

_ROOT = Path(__file__).resolve().parents[2]
_PROCESS_SYNC_LOCK = asyncio.Lock()
_CONTEXT_CACHE_TTL_SECONDS = 15.0
_context_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_context_locks: dict[tuple[str, str], asyncio.Lock] = {}
_context_epoch = 0


def invalidate_chattermate_context_cache(workspace_id: UUID | None = None) -> None:
    """Drop tenant-scoped snapshots after reconciliation or test reconfiguration."""

    global _context_epoch
    _context_epoch += 1
    if workspace_id is None:
        _context_cache.clear()
        for key in [key for key, lock in _context_locks.items() if not lock.locked()]:
            _context_locks.pop(key, None)
        return
    workspace_key = str(workspace_id)
    for key in [key for key in _context_cache if key[0] == workspace_key]:
        _context_cache.pop(key, None)
    for key in [
        key for key, lock in _context_locks.items() if key[0] == workspace_key and not lock.locked()
    ]:
        _context_locks.pop(key, None)


@asynccontextmanager
async def _exclusive_chattermate_sync():
    """Serialize reconciliation against the single local organization."""

    async with _PROCESS_SYNC_LOCK:
        lock_dir = _ROOT / ".local" / "locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(lock_dir, 0o700)
        lock_path = lock_dir / "chattermate-sync.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_EX)
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _pick(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if row.get(key) is not None}


def _agent_type(key: str) -> str:
    if key in {"sdr", "closer", "marketing", "infoproduto", "cobranca"}:
        return "sales"
    if key in {"track"}:
        return "tech_support"
    if key in {"secretaria", "andamento"}:
        return "customer_support"
    return "custom"


def _agent_marker(workspace_id: UUID, key: str) -> str:
    return f"KLM-AGENT-ID:{workspace_id}:{key}"


def _agent_instructions(workspace_id: UUID, definition: Any) -> list[str]:
    return [
        definition.description,
        f"Gate obrigatório: {definition.gate}",
        f"Ferramentas KLM permitidas: {', '.join(definition.tools)}",
        _agent_marker(workspace_id, definition.key),
    ]


def _contains_marker(row: dict[str, Any], marker: str) -> bool:
    instructions = row.get("instructions")
    if isinstance(instructions, list):
        return marker in {str(item) for item in instructions}
    return marker in str(instructions or "")


class ChatterMateOperationsService:
    def __init__(self, db: AsyncSession, client: ChatterMateClient | None = None):
        self.db = db
        self.client = client or ChatterMateClient()
        self.links = OSSBridgeService(db)

    async def _binding(self, workspace_id: UUID, organization_id: str) -> ExternalEntityLink | None:
        return await self.db.scalar(
            select(ExternalEntityLink).where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.provider == OSSBridgeProvider.chattermate,
                ExternalEntityLink.external_account_id == organization_id,
                ExternalEntityLink.klm_entity_type == "workspace",
                ExternalEntityLink.external_entity_type == "organization",
            )
        )

    async def _bind(self, workspace_id: UUID, organization_id: str) -> ExternalEntityLink:
        organization_id = str(organization_id or "").strip()
        if not organization_id:
            raise PermissionError("A sessão ChatterMate não informou uma organização válida.")

        workspace_bindings = list(
            await self.db.scalars(
                select(ExternalEntityLink)
                .where(
                    ExternalEntityLink.workspace_id == workspace_id,
                    ExternalEntityLink.provider == OSSBridgeProvider.chattermate,
                    ExternalEntityLink.klm_entity_type == "workspace",
                    ExternalEntityLink.external_entity_type == "organization",
                    ExternalEntityLink.sync_state != ExternalEntitySyncState.disabled,
                )
                .with_for_update()
            )
        )
        if len(workspace_bindings) > 1:
            raise PermissionError("Workspace KLM possui vínculos ChatterMate ambíguos.")
        current = workspace_bindings[0] if workspace_bindings else None
        if current is not None and (
            current.klm_entity_id != workspace_id
            or current.external_account_id != organization_id
            or current.external_entity_id != organization_id
        ):
            raise PermissionError(
                "Workspace KLM já está vinculado a outra organização ChatterMate."
            )

        foreign = await self.db.scalar(
            select(ExternalEntityLink)
            .where(
                ExternalEntityLink.workspace_id != workspace_id,
                ExternalEntityLink.provider == OSSBridgeProvider.chattermate,
                ExternalEntityLink.external_account_id == organization_id,
                ExternalEntityLink.klm_entity_type == "workspace",
                ExternalEntityLink.external_entity_type == "organization",
                ExternalEntityLink.sync_state != ExternalEntitySyncState.disabled,
            )
            .with_for_update()
        )
        if foreign is not None:
            raise PermissionError("Organização ChatterMate já vinculada a outro workspace KLM.")
        if current is not None and current.sync_state == ExternalEntitySyncState.synced:
            return current
        return await self.links.upsert_link(
            workspace_id,
            ExternalEntityLinkUpsert(
                provider=OSSBridgeProvider.chattermate,
                klm_entity_type="workspace",
                klm_entity_id=workspace_id,
                external_account_id=organization_id,
                external_entity_type="organization",
                external_entity_id=organization_id,
                sync_state=ExternalEntitySyncState.synced,
                last_synced_at=datetime.now(UTC),
                metadata={"version": "1.0.14", "sourceOfTruth": "klm"},
            ),
        )

    async def ensure_workspace_binding(
        self,
        workspace_id: UUID,
        organization_id: str,
    ) -> ExternalEntityLink:
        """Validate or create the one-to-one local organization binding."""

        return await self._bind(workspace_id, organization_id)

    async def launch_agent_id(self, workspace_id: UUID) -> str | None:
        """Prefer the canonical SDR agent when opening agent-scoped upstream tabs."""

        links = list(
            await self.db.scalars(
                select(ExternalEntityLink).where(
                    ExternalEntityLink.workspace_id == workspace_id,
                    ExternalEntityLink.provider == OSSBridgeProvider.chattermate,
                    ExternalEntityLink.klm_entity_type == "agent",
                    ExternalEntityLink.external_entity_type == "agent",
                    ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
                )
            )
        )
        if not links:
            return None
        preferred = next(
            (link for link in links if (link.link_metadata or {}).get("agentKey") == "sdr"),
            links[0],
        )
        return preferred.external_entity_id

    async def sync_agents(self, workspace_id: UUID, actor_user_id: UUID) -> dict[str, Any]:
        async with _exclusive_chattermate_sync():
            return await self._sync_agents_unlocked(workspace_id, actor_user_id)

    async def _sync_agents_unlocked(
        self, workspace_id: UUID, actor_user_id: UUID
    ) -> dict[str, Any]:
        identity = await self.client.identity()
        organization_id = str(identity["organization_id"])
        await self.ensure_workspace_binding(workspace_id, organization_id)
        registry = await KlmAgentService(self.db).registry(workspace_id)
        state_by_key = {row["key"]: row["state"] for row in registry["agents"]}
        upstream_agents = await self.client.list_agents()
        existing_by_id = {str(row.get("id")): row for row in upstream_agents if row.get("id")}
        existing_by_name = {str(row.get("name") or ""): row for row in upstream_agents}
        created = 0
        updated = 0
        adopted = 0
        conflicts = 0
        for definition in AGENT_DEFINITIONS:
            name = f"KLM · {definition.name}"
            marker = _agent_marker(workspace_id, definition.key)
            klm_agent_id = uuid5(NAMESPACE_URL, f"klm:{workspace_id}:agent:{definition.key}")
            desired_active = state_by_key.get(definition.key) != "paused"
            link = await self.links.find_link_by_klm_entity(
                workspace_id,
                OSSBridgeProvider.chattermate,
                organization_id,
                "agent",
                klm_agent_id,
                "agent",
            )
            marker_matches = [row for row in upstream_agents if _contains_marker(row, marker)]
            if len(marker_matches) > 1:
                conflicts += len(marker_matches) - 1
            current = existing_by_id.get(str(link.external_entity_id)) if link is not None else None
            current = current or (marker_matches[0] if marker_matches else None)
            current = current or existing_by_name.get(name)
            if current is None:
                current = await self.client.create_agent(
                    {
                        "name": name,
                        "display_name": definition.name,
                        "description": definition.description,
                        "agent_type": _agent_type(definition.key),
                        "instructions": _agent_instructions(workspace_id, definition),
                        "tools": [],
                        "is_active": desired_active,
                        "is_default": definition.key == "sdr",
                        "transfer_to_human": definition.can_request_human,
                        "ask_for_rating": False,
                        "handoff_collect_email": True,
                        "handoff_collect_name": True,
                        "enable_rate_limiting": True,
                        "overall_limit_per_ip": 100,
                        "requests_per_sec": 1.0,
                        "use_workflow": False,
                        "allow_attachments": False,
                        "require_token_auth": True,
                    }
                )
                created += 1
            else:
                if link is None:
                    adopted += 1
                desired = {
                    "display_name": definition.name,
                    "instructions": _agent_instructions(workspace_id, definition),
                    "is_active": desired_active,
                    "transfer_to_human": definition.can_request_human,
                    "ask_for_rating": False,
                    "handoff_collect_email": True,
                    "handoff_collect_name": True,
                    "enable_rate_limiting": True,
                    "overall_limit_per_ip": 100,
                    "requests_per_sec": 1.0,
                    "use_workflow": False,
                    "allow_attachments": False,
                    "require_token_auth": True,
                }
                if any(current.get(key) != value for key, value in desired.items()):
                    current = await self.client.update_agent(str(current["id"]), desired)
                    updated += 1
            await self.links.upsert_link(
                workspace_id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.chattermate,
                    klm_entity_type="agent",
                    klm_entity_id=klm_agent_id,
                    external_account_id=organization_id,
                    external_entity_type="agent",
                    external_entity_id=str(current["id"]),
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=datetime.now(UTC),
                    metadata={
                        "agentKey": definition.key,
                        "name": name,
                        "canonicalMarker": marker,
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
                event_type="chattermate_agents_synced",
                entity_type="oss_adapter",
                summary=(
                    "ChatterMate reconciliado: "
                    f"{created} criados, {updated} atualizados, {adopted} adotados."
                ),
                after_data={
                    "created": created,
                    "updated": updated,
                    "adopted": adopted,
                    "conflicts": conflicts,
                    "seen": len(AGENT_DEFINITIONS),
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.commit()
        invalidate_chattermate_context_cache(workspace_id)
        return {
            "created": created,
            "updated": updated,
            "adopted": adopted,
            "conflicts": conflicts,
            "seen": len(AGENT_DEFINITIONS),
        }

    async def context(self, workspace_id: UUID, time_range: str = "7d") -> dict[str, Any]:
        cache_key = (str(workspace_id), time_range)
        cached = _context_cache.get(cache_key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])

        lock = _context_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            while True:
                cached = _context_cache.get(cache_key)
                now = time.monotonic()
                if cached and cached[0] > now:
                    return copy.deepcopy(cached[1])
                load_epoch = _context_epoch
                snapshot = await self._load_context(workspace_id, time_range)
                if load_epoch != _context_epoch:
                    continue
                _context_cache[cache_key] = (
                    time.monotonic() + _CONTEXT_CACHE_TTL_SECONDS,
                    copy.deepcopy(snapshot),
                )
                return snapshot

    async def _load_context(
        self,
        workspace_id: UUID,
        time_range: str,
    ) -> dict[str, Any]:
        identity = await self.client.identity()
        organization_id = str(identity["organization_id"])
        binding = await self._binding(workspace_id, organization_id)
        if binding is None:
            return self._empty_context(organization_id, time_range)
        agents = await self.client.list_agents()
        (
            analytics,
            performance,
            people,
            people_stats,
            mcp_tools,
            groups,
            notifications,
            knowledge,
            queue,
        ) = await asyncio.gather(
            self.client.analytics(time_range),
            self.client.agent_performance(time_range),
            self.client.people(),
            self.client.people_stats(),
            self.client.mcp_tools(),
            self.client.groups(),
            self.client.notifications(),
            self.client.knowledge(organization_id),
            self.client.knowledge_queue(organization_id),
        )
        workflows = (
            await asyncio.gather(
                *(self.client.workflow_for_agent(str(agent["id"])) for agent in agents)
            )
            if agents
            else []
        )
        return {
            "linked": True,
            "organizationId": organization_id,
            "agents": [
                _pick(
                    row,
                    "id",
                    "name",
                    "display_name",
                    "description",
                    "agent_type",
                    "is_active",
                    "transfer_to_human",
                    "ask_for_rating",
                    "use_workflow",
                    "active_workflow_id",
                    "allow_attachments",
                    "require_token_auth",
                    "knowledge",
                    "groups",
                    "created_at",
                    "updated_at",
                )
                for row in agents
            ],
            "workflows": [
                _pick(
                    row,
                    "id",
                    "name",
                    "description",
                    "status",
                    "version",
                    "is_template",
                    "default_language",
                    "canvas_data",
                    "settings",
                    "agent_id",
                    "created_at",
                    "updated_at",
                )
                for row in workflows
                if isinstance(row, dict)
            ],
            "analytics": analytics,
            "performance": performance,
            "people": people,
            "peopleStats": people_stats,
            "mcpTools": [
                _pick(
                    row,
                    "id",
                    "name",
                    "description",
                    "transport_type",
                    "is_enabled",
                    "created_at",
                    "updated_at",
                )
                for row in mcp_tools
            ],
            "groups": [
                _pick(row, "id", "name", "description", "is_active", "users") for row in groups
            ],
            "notifications": [
                _pick(row, "id", "title", "message", "type", "is_read", "created_at")
                for row in notifications[:50]
            ],
            "knowledge": {
                "knowledge": [
                    _pick(row, "id", "name", "type", "agents", "pages", "error")
                    for row in knowledge.get("knowledge", [])
                    if isinstance(row, dict)
                ],
                "pagination": knowledge.get("pagination") or {},
                "queueItems": [
                    _pick(
                        row,
                        "id",
                        "source",
                        "source_type",
                        "status",
                        "error",
                        "processing_stage",
                        "progress_percentage",
                        "created_at",
                        "updated_at",
                    )
                    for row in queue.get("queue_items", [])
                    if isinstance(row, dict)
                ],
            },
            "timeRange": time_range,
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }

    @staticmethod
    def _empty_context(organization_id: str, time_range: str) -> dict[str, Any]:
        return {
            "linked": False,
            "organizationId": organization_id,
            "agents": [],
            "workflows": [],
            "analytics": {},
            "performance": {},
            "people": {"items": [], "total": 0},
            "peopleStats": {},
            "mcpTools": [],
            "groups": [],
            "notifications": [],
            "knowledge": {"knowledge": [], "pagination": {}, "queueItems": []},
            "timeRange": time_range,
            "sourceOfTruth": "klm",
            "externalExecutionEnabled": False,
        }
