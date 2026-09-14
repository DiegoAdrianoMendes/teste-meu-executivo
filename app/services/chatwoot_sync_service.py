"""Project KLM clients, conversations and messages into local Chatwoot."""

from __future__ import annotations

import asyncio
import fcntl
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.client import Client
from app.models.klm_operations import (
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.oss_bridge import ExternalEntitySyncState, OSSBridgeProvider
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.chatwoot_client import ChatwootClient, ChatwootRateLimitError
from app.services.chatwoot_operations_service import ChatwootOperationsService
from app.services.klm_operations_service import KlmOperationsService
from app.services.oss_bridge_service import OSSBridgeService

_ROOT = Path(__file__).resolve().parents[2]
_PROCESS_SYNC_LOCK = asyncio.Lock()


@asynccontextmanager
async def _exclusive_chatwoot_sync():
    """Serialize reconciliation against the single local Chatwoot account."""
    async with _PROCESS_SYNC_LOCK:
        lock_dir = _ROOT / ".local" / "locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(lock_dir, 0o700)
        lock_path = lock_dir / "chatwoot-sync.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_EX)
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


class ChatwootSyncService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = ChatwootClient()
        self.links = OSSBridgeService(db)
        self.account_id = settings.KLM_CHATWOOT_ACCOUNT_ID.strip()

    async def ensure_workspace_binding(self, workspace_id: UUID) -> None:
        await self.links.upsert_link(
            workspace_id,
            ExternalEntityLinkUpsert(
                provider=OSSBridgeProvider.chatwoot,
                klm_entity_type="workspace",
                klm_entity_id=workspace_id,
                external_account_id=self.account_id,
                external_entity_type="account",
                external_entity_id=self.account_id,
                sync_state=ExternalEntitySyncState.synced,
                last_synced_at=datetime.now(UTC),
                metadata={"managedBy": "klm-local-bootstrap", "version": "4.15.1"},
            ),
        )

    async def sync_workspace(self, workspace_id: UUID) -> dict[str, int]:
        async with _exclusive_chatwoot_sync():
            return await self._sync_workspace_unlocked(workspace_id)

    async def sync_thread_by_id(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, object]:
        async with _exclusive_chatwoot_sync():
            await self.client.account()
            await self.ensure_workspace_binding(workspace_id)
            thread = await self.db.scalar(
                select(OmnichannelThread).where(
                    OmnichannelThread.workspace_id == workspace_id,
                    OmnichannelThread.id == thread_id,
                    OmnichannelThread.primary_channel != OmnichannelChannel.internal,
                    OmnichannelThread.client_id.is_not(None),
                )
            )
            if thread is None:
                raise LookupError(
                    "Conversa não encontrada ou sem cliente íntegro para projetar no Chatwoot."
                )
            result = await self.sync_thread(workspace_id, thread)
            projection = await ChatwootOperationsService(
                self.db,
                client=self.client,
            ).projection(workspace_id, thread.id)
            conversation_link = await self.links.find_link_by_klm_entity(
                workspace_id,
                OSSBridgeProvider.chatwoot,
                self.account_id,
                "omnichannel_thread",
                thread.id,
                "conversation",
            )
            if conversation_link is None:
                raise RuntimeError("A projeção terminou sem vínculo de conversa Chatwoot.")
            response: dict[str, object] = {
                "threadId": str(thread.id),
                "conversationId": conversation_link.external_entity_id,
                **projection,
                "status": "projected_local",
                "projectedNow": result["messages"],
                "contactCreated": bool(result["contacts"]),
                "rateLimited": result["pendingMessages"] > 0,
                "sourceOfTruth": "klm",
                "externalDeliveryChanged": False,
            }
            await KlmOperationsService(self.db).record_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label="Usuário KLM",
                event_type="chatwoot.thread_projected",
                entity_type="omnichannel_thread",
                entity_id=thread.id,
                summary="Conversa projetada no Chatwoot local pelo KLM.",
                correlation_id=f"chatwoot:{conversation_link.external_entity_id}",
                after_data={**response, "externalActionsExecuted": 0},
            )
            await self.db.commit()
            return response

    async def _sync_workspace_unlocked(self, workspace_id: UUID) -> dict[str, int]:
        await self.client.account()
        await self.ensure_workspace_binding(workspace_id)
        threads = list(
            (
                await self.db.execute(
                    select(OmnichannelThread)
                    .where(
                        OmnichannelThread.workspace_id == workspace_id,
                        OmnichannelThread.primary_channel != OmnichannelChannel.internal,
                        OmnichannelThread.client_id.is_not(None),
                    )
                    .order_by(OmnichannelThread.created_at.asc())
                )
            ).scalars()
        )
        counts = {"threads": 0, "messages": 0, "contacts": 0, "pendingMessages": 0}
        for thread in threads:
            result = await self.sync_thread(workspace_id, thread)
            counts["threads"] += 1
            counts["messages"] += result["messages"]
            counts["contacts"] += result["contacts"]
            counts["pendingMessages"] += result["pendingMessages"]
        await self.db.commit()
        return counts

    async def sync_thread(
        self,
        workspace_id: UUID,
        thread: OmnichannelThread,
    ) -> dict[str, int]:
        link = await self.links.find_link_by_klm_entity(
            workspace_id,
            OSSBridgeProvider.chatwoot,
            self.account_id,
            "omnichannel_thread",
            thread.id,
            "conversation",
        )
        created_contact = 0
        if link:
            conversation_id = link.external_entity_id
        else:
            client = await self.db.scalar(
                select(Client).where(
                    Client.id == thread.client_id,
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
            if not client:
                raise LookupError("Thread KLM sem cliente íntegro para sincronizar no Chatwoot.")
            contact = await self.client.create_contact(
                identifier=f"klm:client:{client.id}",
                source_id=f"klm-client-{client.id}",
                name=client.name,
                email=client.email,
                phone=client.phone,
            )
            contact_id = str(contact["id"])
            existing_contact_link = await self.links.find_link_by_klm_entity(
                workspace_id,
                OSSBridgeProvider.chatwoot,
                self.account_id,
                "client",
                client.id,
                "contact",
            )
            created_contact = 0 if existing_contact_link else 1
            link = await self.links.upsert_link(
                workspace_id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.chatwoot,
                    klm_entity_type="client",
                    klm_entity_id=client.id,
                    external_account_id=self.account_id,
                    external_entity_type="contact",
                    external_entity_id=contact_id,
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=datetime.now(UTC),
                    metadata={"identifier": f"klm:client:{client.id}"},
                ),
            )
            conversation = await self.client.create_conversation(
                contact_id=int(contact_id),
                source_id=f"klm-thread-{thread.id}",
                subject=thread.subject or thread.object_name or "Atendimento KLM",
                thread_id=str(thread.id),
            )
            conversation_id = str(conversation["id"])
            await self.links.upsert_link(
                workspace_id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.chatwoot,
                    klm_entity_type="omnichannel_thread",
                    klm_entity_id=thread.id,
                    external_account_id=self.account_id,
                    external_entity_type="conversation",
                    external_entity_id=conversation_id,
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=datetime.now(UTC),
                    metadata={"inboxId": settings.KLM_CHATWOOT_INBOX_ID},
                ),
            )
            await self.db.flush()

        messages = list(
            (
                await self.db.execute(
                    select(OmnichannelMessage)
                    .where(
                        OmnichannelMessage.workspace_id == workspace_id,
                        OmnichannelMessage.thread_id == thread.id,
                    )
                    .order_by(OmnichannelMessage.occurred_at.asc())
                )
            ).scalars()
        )
        existing_messages = await self.client.list_messages(conversation_id)
        existing_by_source = {
            str(row.get("source_id")): row
            for row in existing_messages
            if str(row.get("source_id") or "").startswith("klm:")
        }
        synced_messages = 0
        pending_messages = 0
        for index, message in enumerate(messages):
            metadata = message.message_metadata or {}
            if metadata.get("chatwootMessageId") or metadata.get("source") == "chatwoot_webhook":
                continue
            if metadata.get("chatwootConversationId") and metadata.get("chatwootMessageId"):
                continue
            private = message.direction == MessageDirection.internal
            message_type = (
                "incoming" if message.direction == MessageDirection.inbound else "outgoing"
            )
            source_id = f"klm:{message.id}"
            projected = existing_by_source.get(source_id)
            if projected is None:
                try:
                    projected = await self.client.create_message(
                        conversation_id=conversation_id,
                        content=message.body,
                        source_id=source_id,
                        message_type=message_type,
                        private=private,
                    )
                except ChatwootRateLimitError:
                    pending_messages += len(messages) - index
                    break
            external_message_id = str(projected.get("id") or "")
            if not external_message_id:
                raise RuntimeError("Chatwoot não retornou o ID da mensagem projetada.")
            if not message.external_message_id:
                message.external_message_id = external_message_id
            message.message_metadata = {
                **metadata,
                "chatwootAccountId": self.account_id,
                "chatwootConversationId": conversation_id,
                "chatwootMessageId": external_message_id,
                "chatwootProjection": "local_only",
            }
            synced_messages += 1
            await self.db.flush()
        synced_at = datetime.now(UTC)
        await self.links.upsert_link(
            workspace_id,
            ExternalEntityLinkUpsert(
                provider=OSSBridgeProvider.chatwoot,
                klm_entity_type="omnichannel_thread",
                klm_entity_id=thread.id,
                external_account_id=self.account_id,
                external_entity_type="conversation",
                external_entity_id=conversation_id,
                sync_state=ExternalEntitySyncState.synced,
                last_synced_at=synced_at,
                metadata={
                    **(link.link_metadata or {}),
                    "inboxId": settings.KLM_CHATWOOT_INBOX_ID,
                    "lastProjectedAt": synced_at.isoformat(),
                    "pendingMessages": pending_messages,
                },
            ),
        )
        await self.db.flush()
        return {
            "messages": synced_messages,
            "contacts": created_contact,
            "pendingMessages": pending_messages,
        }
