"""Typed operational facade over the loopback-only Chatwoot sidecar."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import MessageDeliveryStatus, OmnichannelMessage
from app.models.oss_bridge import OSSBridgeProvider
from app.services.chatwoot_client import ChatwootClient, ChatwootClientError
from app.services.klm_operations_service import KlmOperationsService
from app.services.oss_bridge_service import OSSBridgeService


def _pick(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if row.get(key) is not None}


class ChatwootRoutingUpdateError(ChatwootClientError):
    """Raised after a failed routing saga has been audited and compensated."""


class ChatwootOperationsService:
    """Expose reviewed Chatwoot operations without leaking its broad admin API."""

    def __init__(self, db: AsyncSession, client: ChatwootClient | None = None):
        self.db = db
        self.client = client or ChatwootClient()
        self.links = OSSBridgeService(db)

    async def _conversation_link(self, workspace_id: UUID, thread_id: UUID):
        return await self.links.find_link_by_klm_entity(
            workspace_id,
            OSSBridgeProvider.chatwoot,
            self.client.account_id,
            "omnichannel_thread",
            thread_id,
            "conversation",
        )

    async def _conversation_id(self, workspace_id: UUID, thread_id: UUID) -> str | None:
        link = await self._conversation_link(workspace_id, thread_id)
        return link.external_entity_id if link else None

    async def projection(self, workspace_id: UUID, thread_id: UUID) -> dict[str, Any]:
        link = await self._conversation_link(workspace_id, thread_id)
        messages = list(
            (
                await self.db.execute(
                    select(OmnichannelMessage).where(
                        OmnichannelMessage.workspace_id == workspace_id,
                        OmnichannelMessage.thread_id == thread_id,
                    )
                )
            ).scalars()
        )
        projected = 0
        received = 0
        pending = 0
        failed = 0
        for message in messages:
            metadata = message.message_metadata or {}
            from_webhook = metadata.get("source") == "chatwoot_webhook"
            has_projection = bool(metadata.get("chatwootMessageId"))
            if from_webhook:
                received += 1
            elif has_projection:
                projected += 1
            else:
                pending += 1
            if message.delivery_status == MessageDeliveryStatus.failed:
                failed += 1
        return {
            "totalMessages": len(messages),
            "projectedMessages": projected,
            "receivedMessages": received,
            "pendingMessages": pending,
            "failedMessages": failed,
            "lastSyncedAt": getattr(link, "last_synced_at", None) if link else None,
            "externalChannelDeliveryEnabled": False,
        }

    async def conversation_id(self, workspace_id: UUID, thread_id: UUID) -> str | None:
        """Return only the persisted link used by contextual motor launches."""

        return await self._conversation_id(workspace_id, thread_id)

    async def context(self, workspace_id: UUID, thread_id: UUID) -> dict[str, Any]:
        conversation_id = await self._conversation_id(workspace_id, thread_id)
        inboxes, teams, labels, agents, canned, projection = await asyncio.gather(
            self.client.list_inboxes(),
            self.client.list_teams(),
            self.client.list_labels(),
            self.client.list_agents(),
            self.client.list_canned_responses(),
            self.projection(workspace_id, thread_id),
        )
        conversation = await self.client.conversation(conversation_id) if conversation_id else None
        normalized_conversation = None
        if conversation is not None:
            normalized_conversation = _pick(
                conversation,
                "id",
                "status",
                "priority",
                "labels",
                "created_at",
                "updated_at",
            )
            for key in ("assignee", "team", "inbox"):
                nested = conversation.get(key)
                if isinstance(nested, dict):
                    normalized_conversation[key] = _pick(
                        nested, "id", "name", "email", "channel_type"
                    )

        return {
            "linked": conversation_id is not None,
            "threadId": str(thread_id),
            "conversationId": conversation_id,
            "conversation": normalized_conversation,
            "inboxes": [
                _pick(
                    row,
                    "id",
                    "name",
                    "channel_type",
                    "enable_auto_assignment",
                    "working_hours_enabled",
                )
                for row in inboxes
            ],
            "teams": [
                _pick(row, "id", "name", "description", "allow_auto_assign") for row in teams
            ],
            "labels": [
                _pick(row, "id", "title", "description", "color", "show_on_sidebar")
                for row in labels
            ],
            "agents": [
                _pick(row, "id", "name", "email", "role", "availability_status") for row in agents
            ],
            "cannedResponses": [_pick(row, "id", "short_code", "content") for row in canned],
            "projection": projection,
            "sourceOfTruth": "klm",
        }

    async def update_routing(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        conversation_id = await self._conversation_id(workspace_id, thread_id)
        if conversation_id is None:
            raise LookupError(
                "A thread ainda não possui conversa Chatwoot vinculada. "
                "Sincronize o módulo primeiro."
            )
        before = await self.client.conversation(conversation_id)
        applied: list[str] = []
        try:
            if "status" in changes:
                await self.client.set_conversation_status(conversation_id, changes["status"])
                applied.append("status")
            if "priority" in changes:
                await self.client.set_conversation_priority(
                    conversation_id, changes["priority"]
                )
                applied.append("priority")
            if "assignee_id" in changes or "team_id" in changes:
                await self.client.assign_conversation(
                    conversation_id,
                    assignee_id=changes.get("assignee_id"),
                    team_id=changes.get("team_id"),
                )
                applied.append("assignment")
            if "labels" in changes:
                await self.client.set_conversation_labels(
                    conversation_id, changes["labels"]
                )
                applied.append("labels")
            after = await self.client.conversation(conversation_id)
        except ChatwootClientError as error:
            compensated, compensation_failures = await self._restore_routing(
                conversation_id,
                before,
                changes,
            )
            try:
                observed = await self.client.conversation(conversation_id)
            except ChatwootClientError:
                observed = {}
            await KlmOperationsService(self.db).record_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label="Usuário KLM",
                event_type="chatwoot.routing_partial_failure",
                entity_type="omnichannel_thread",
                entity_id=thread_id,
                summary="Falha parcial no roteamento Chatwoot; compensação local executada.",
                correlation_id=f"chatwoot:{conversation_id}",
                before_data=_pick(before, "status", "priority", "labels", "assignee", "team"),
                after_data={
                    "requested": changes,
                    "appliedSteps": applied,
                    "compensatedSteps": compensated,
                    "compensationFailures": compensation_failures,
                    "observed": _pick(
                        observed,
                        "status",
                        "priority",
                        "labels",
                        "assignee",
                        "team",
                    ),
                    "errorType": type(error).__name__,
                },
            )
            await self.db.commit()
            detail = (
                "; a restauração também ficou incompleta"
                if compensation_failures
                else "; o estado anterior foi restaurado"
            )
            raise ChatwootRoutingUpdateError(
                f"O Chatwoot não concluiu o roteamento{detail}. Consulte a auditoria KLM."
            ) from error
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type="chatwoot.routing_updated",
            entity_type="omnichannel_thread",
            entity_id=thread_id,
            summary="Roteamento local do Chatwoot atualizado pelo KLM.",
            correlation_id=f"chatwoot:{conversation_id}",
            before_data=_pick(before, "status", "priority", "labels", "assignee", "team"),
            after_data=_pick(after, "status", "priority", "labels", "assignee", "team"),
        )
        return await self.context(workspace_id, thread_id)

    async def _restore_routing(
        self,
        conversation_id: str,
        before: dict[str, Any],
        changes: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        compensated: list[str] = []
        failures: list[str] = []

        async def restore(name: str, operation) -> None:
            try:
                await operation
                compensated.append(name)
            except ChatwootClientError:
                failures.append(name)

        if "status" in changes and before.get("status") is not None:
            await restore(
                "status",
                self.client.set_conversation_status(
                    conversation_id, str(before["status"])
                ),
            )
        if "priority" in changes:
            await restore(
                "priority",
                self.client.set_conversation_priority(
                    conversation_id,
                    str(before["priority"]) if before.get("priority") is not None else None,
                ),
            )
        if "assignee_id" in changes or "team_id" in changes:
            assignee = before.get("assignee")
            team = before.get("team")
            await restore(
                "assignment",
                self.client.assign_conversation(
                    conversation_id,
                    assignee_id=(
                        int(assignee["id"])
                        if isinstance(assignee, dict) and assignee.get("id") is not None
                        else None
                    ),
                    team_id=(
                        int(team["id"])
                        if isinstance(team, dict) and team.get("id") is not None
                        else None
                    ),
                ),
            )
        if "labels" in changes:
            labels = before.get("labels")
            previous_labels = [
                str(item.get("title") if isinstance(item, dict) else item)
                for item in labels
            ] if isinstance(labels, list) else []
            await restore(
                "labels",
                self.client.set_conversation_labels(conversation_id, previous_labels),
            )
        return compensated, failures
