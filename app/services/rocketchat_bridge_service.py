"""Bidirectional local bridge between KLM internal notes and Rocket.Chat."""

from __future__ import annotations

import asyncio
import fcntl
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    IntegrationEventReceiptStatus,
    OSSBridgeProvider,
)
from app.schemas.klm_operations import OmnichannelMessageCreate, OmnichannelThreadCreate
from app.schemas.oss_bridge import ExternalEntityLinkUpsert, IntegrationEventReceiptCreate
from app.services.klm_operations_service import KlmOperationsService
from app.services.oss_bridge_service import OSSBridgeService, compute_payload_hash
from app.services.rocketchat_client import RocketChatClient

ROCKET_CHANNELS = (
    "comercial",
    "juridico-saude",
    "secretaria",
    "paralegal",
    "peticoes",
    "financeiro",
    "marketing",
    "track",
    "ti",
    "incidentes",
)

_ROOT = Path(__file__).resolve().parents[2]
_PROCESS_SYNC_LOCK = asyncio.Lock()


class RocketChatWorkspaceBindingError(ValueError):
    """Raised when one local Rocket.Chat server is bound to another workspace."""


@asynccontextmanager
async def _exclusive_rocketchat_sync():
    """Serialize reconciliation against the single local Rocket.Chat workspace."""
    async with _PROCESS_SYNC_LOCK:
        lock_dir = _ROOT / ".local" / "locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(lock_dir, 0o700)
        lock_path = lock_dir / "rocketchat-sync.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_EX)
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _account_id(info: dict[str, Any]) -> str:
    value = str(
        info.get("hashedWorkspaceUrl") or info.get("cloudWorkspaceId") or info.get("uniqueID") or ""
    ).strip()
    if not value:
        raise ValueError("Rocket.Chat não informou sua identidade estável.")
    return value


def _event_time(value: Any) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


class RocketChatBridgeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.links = OSSBridgeService(db)
        self.operations = KlmOperationsService(db)
        self.client = RocketChatClient()

    async def _bind_workspace(self, workspace_id: UUID, account_id: str) -> None:
        conflicting_workspace = await self.db.scalar(
            select(ExternalEntityLink).where(
                ExternalEntityLink.workspace_id != workspace_id,
                ExternalEntityLink.provider == OSSBridgeProvider.rocketchat,
                ExternalEntityLink.external_account_id == account_id,
                ExternalEntityLink.klm_entity_type == "workspace",
                ExternalEntityLink.external_entity_type == "account",
                ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
            )
        )
        if conflicting_workspace is not None:
            raise RocketChatWorkspaceBindingError(
                "Este servidor Rocket.Chat local já está vinculado a outro workspace."
            )
        previous_links = list(
            (
                await self.db.execute(
                    select(ExternalEntityLink).where(
                        ExternalEntityLink.workspace_id == workspace_id,
                        ExternalEntityLink.provider == OSSBridgeProvider.rocketchat,
                        ExternalEntityLink.external_account_id != account_id,
                        ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
                    )
                )
            ).scalars()
        )
        superseded_at = datetime.now(UTC).isoformat()
        for link in previous_links:
            link.sync_state = ExternalEntitySyncState.stale
            link.link_metadata = {
                **(link.link_metadata or {}),
                "supersededAt": superseded_at,
                "supersededByRuntimeVersion": "6.13.1",
            }
        await self.links.upsert_link(
            workspace_id,
            ExternalEntityLinkUpsert(
                provider=OSSBridgeProvider.rocketchat,
                klm_entity_type="workspace",
                klm_entity_id=workspace_id,
                external_account_id=account_id,
                external_entity_type="account",
                external_entity_id=account_id,
                sync_state=ExternalEntitySyncState.synced,
                last_synced_at=datetime.now(UTC),
                metadata={
                    "managedBy": "klm-local-bootstrap",
                    "runtimeVersion": "6.13.1",
                    "referenceVersion": "8.6.1",
                    "runtimePolicy": "local_compatibility",
                },
            ),
        )

    async def _ensure_channel_thread(
        self,
        workspace_id: UUID,
        account_id: str,
        room_id: str,
        room_name: str,
        room_type: str = "c",
    ) -> OmnichannelThread:
        link = await self.links.find_link_by_external_entity(
            workspace_id,
            OSSBridgeProvider.rocketchat,
            account_id,
            "room",
            room_id,
        )
        thread = (
            await self.operations.get_thread(workspace_id, link.klm_entity_id) if link else None
        )
        if thread:
            return thread
        thread = await self.operations.create_thread(
            workspace_id,
            None,
            OmnichannelThreadCreate(
                primary_channel=OmnichannelChannel.internal,
                subject=f"Equipe · {room_name.replace('-', ' ').title()}",
                owner_agent_key="orquestrador",
                thread_metadata={
                    "source": "rocketchat",
                    "rocketRoomId": room_id,
                    "rocketRoomName": room_name,
                    "scope": "internal_team",
                    "coordinationAgentKey": "orquestrador",
                    "ownerBridgeAgentKey": "hermes",
                },
            ),
            actor_type="system",
            actor_label="Rocket.Chat bridge",
        )
        if link:
            link.klm_entity_type = "omnichannel_thread"
            link.klm_entity_id = thread.id
            link.sync_state = ExternalEntitySyncState.synced
            link.last_synced_at = datetime.now(UTC)
            link.link_metadata = {
                "roomName": room_name,
                "roomType": room_type,
                "scope": "internal_team",
            }
        else:
            await self.links.upsert_link(
                workspace_id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.rocketchat,
                    klm_entity_type="omnichannel_thread",
                    klm_entity_id=thread.id,
                    external_account_id=account_id,
                    external_entity_type="room",
                    external_entity_id=room_id,
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=datetime.now(UTC),
                    metadata={
                        "roomName": room_name,
                        "roomType": room_type,
                        "scope": "internal_team",
                    },
                ),
            )
        return thread

    async def sync_workspace(self, workspace_id: UUID) -> dict[str, int]:
        async with _exclusive_rocketchat_sync():
            return await self._sync_workspace_unlocked(workspace_id)

    async def sync_thread(self, workspace_id: UUID, thread_id: UUID) -> dict[str, Any]:
        """Project one KLM case room without reconciling the whole workspace."""
        async with _exclusive_rocketchat_sync():
            info = await self.client.info()
            account_id = _account_id(info)
            await self._bind_workspace(workspace_id, account_id)
            thread = await self.operations.get_thread(workspace_id, thread_id)
            if thread is None:
                raise LookupError("Caso ou conversa interna não encontrado.")

            link = await self.links.find_link_by_klm_entity(
                workspace_id,
                OSSBridgeProvider.rocketchat,
                account_id,
                "omnichannel_thread",
                thread.id,
                "room",
            )
            if link is None:
                metadata = thread.thread_metadata or {}
                room_name = str(metadata.get("rocketRoomName") or "").strip()
                if room_name and metadata.get("source") == "rocketchat":
                    room = await self.client.channel(room_name)
                else:
                    room_name = (
                        f"caso-{thread.matter_id.hex}"
                        if thread.matter_id
                        else f"klm-thread-{thread.id.hex}"
                    )
                    room = await self.client.ensure_private_group(room_name)
                link = await self.links.upsert_link(
                    workspace_id,
                    ExternalEntityLinkUpsert(
                        provider=OSSBridgeProvider.rocketchat,
                        klm_entity_type="omnichannel_thread",
                        klm_entity_id=thread.id,
                        external_account_id=account_id,
                        external_entity_type="room",
                        external_entity_id=str(room["_id"]),
                        sync_state=ExternalEntitySyncState.synced,
                        last_synced_at=datetime.now(UTC),
                        metadata={
                            "roomName": room_name,
                            "roomType": str(room.get("t") or "p"),
                            "scope": "case" if thread.matter_id else "internal_thread",
                        },
                    ),
                )

            messages = list(
                (
                    await self.db.execute(
                        select(OmnichannelMessage).where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.thread_id == thread.id,
                            OmnichannelMessage.direction == MessageDirection.internal,
                        )
                    )
                ).scalars()
            )
            projected = 0
            for message in messages:
                metadata = message.message_metadata or {}
                external_id = str(metadata.get("rocketMessageId") or f"klm_{message.id.hex}")
                _, created = await self.client.send_message(
                    room_id=link.external_entity_id,
                    message_id=external_id,
                    body=message.body,
                )
                message.external_message_id = message.external_message_id or external_id
                message.delivery_status = MessageDeliveryStatus.sent
                message.message_metadata = {
                    **metadata,
                    "rocketAccountId": account_id,
                    "rocketRoomId": link.external_entity_id,
                    "rocketMessageId": external_id,
                    "rocketProjection": "local_only",
                }
                projected += int(created)
            link.sync_state = ExternalEntitySyncState.synced
            link.last_synced_at = datetime.now(UTC)
            link.sync_error = None
            await self.db.commit()
            return {
                "threadId": str(thread.id),
                "roomId": link.external_entity_id,
                "messagesSeen": len(messages),
                "messagesProjected": projected,
            }

    async def _sync_workspace_unlocked(self, workspace_id: UUID) -> dict[str, int]:
        info = await self.client.info()
        account_id = _account_id(info)
        await self._bind_workspace(workspace_id, account_id)
        counts = {"channels": 0, "caseRooms": 0, "messages": 0}
        for room_name in ROCKET_CHANNELS:
            room = await self.client.channel(room_name)
            await self._ensure_channel_thread(
                workspace_id,
                account_id,
                str(room["_id"]),
                room_name,
                str(room.get("t") or "c"),
            )
            counts["channels"] += 1

        threads = list(
            (
                await self.db.execute(
                    select(OmnichannelThread).where(
                        OmnichannelThread.workspace_id == workspace_id,
                        OmnichannelThread.matter_id.is_not(None),
                    )
                )
            ).scalars()
        )
        for thread in threads:
            room_name = f"caso-{thread.matter_id.hex}"
            room = await self.client.ensure_private_group(room_name)
            room_id = str(room["_id"])
            await self.links.upsert_link(
                workspace_id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.rocketchat,
                    klm_entity_type="omnichannel_thread",
                    klm_entity_id=thread.id,
                    external_account_id=account_id,
                    external_entity_type="room",
                    external_entity_id=room_id,
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=datetime.now(UTC),
                    metadata={
                        "roomName": room_name,
                        "roomType": str(room.get("t") or "p"),
                        "scope": "case",
                    },
                ),
            )
            counts["caseRooms"] += 1
            messages = list(
                (
                    await self.db.execute(
                        select(OmnichannelMessage).where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.thread_id == thread.id,
                            OmnichannelMessage.direction == MessageDirection.internal,
                        )
                    )
                ).scalars()
            )
            for message in messages:
                metadata = message.message_metadata or {}
                external_id = str(metadata.get("rocketMessageId") or f"klm_{message.id.hex}")
                _, created = await self.client.send_message(
                    room_id=room_id,
                    message_id=external_id,
                    body=message.body,
                )
                if not message.external_message_id:
                    message.external_message_id = external_id
                message.delivery_status = MessageDeliveryStatus.sent
                message.message_metadata = {
                    **metadata,
                    "rocketAccountId": account_id,
                    "rocketRoomId": room_id,
                    "rocketMessageId": external_id,
                    "rocketProjection": "local_only",
                }
                counts["messages"] += int(created)
        await self.db.commit()
        return counts

    async def ingest_webhook(
        self,
        *,
        workspace_id: UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        message_id = str(payload.get("message_id") or "").strip()
        room_id = str(payload.get("channel_id") or "").strip()
        room_name = str(payload.get("channel_name") or "").removeprefix("#").strip()
        if not message_id or not room_id or not room_name:
            raise ValueError("Webhook Rocket.Chat sem mensagem ou sala.")
        # Projections created by the KLM sync are already authoritative in KLM.
        # Ignore them before any Rocket API call or SQLite write to avoid an
        # outgoing-webhook feedback loop competing with the sync transaction.
        if message_id.startswith("klm_"):
            return {"status": "ignored", "deduplicated": False}
        info = await self.client.info()
        account_id = _account_id(info)
        binding = await self.links.find_link_by_external_entity(
            workspace_id,
            OSSBridgeProvider.rocketchat,
            account_id,
            "account",
            account_id,
        )
        if not binding or binding.klm_entity_type != "workspace":
            raise PermissionError("Servidor Rocket.Chat não vinculado ao workspace.")

        receipt_payload = {key: value for key, value in payload.items() if key != "token"}
        revision_hash = compute_payload_hash(receipt_payload)
        receipt, deduplicated = await self.links.register_receipt(
            workspace_id,
            IntegrationEventReceiptCreate(
                provider=OSSBridgeProvider.rocketchat,
                external_event_id=f"{message_id[:190]}:{revision_hash[:32]}",
                payload=receipt_payload,
            ),
        )
        await self.db.commit()
        receipt_id = receipt.id
        lease_started_at = await self.links.claim_receipt(
            workspace_id,
            OSSBridgeProvider.rocketchat,
            receipt_id,
        )
        if lease_started_at is None:
            await self.db.refresh(receipt)
            return {"status": receipt.status.value, "deduplicated": True}

        try:
            thread = await self._ensure_channel_thread(
                workspace_id,
                account_id,
                room_id,
                room_name,
            )
            message = await self.db.scalar(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.thread_id == thread.id,
                    OmnichannelMessage.external_message_id == message_id,
                )
            )
            message_updated = message is not None
            if message is not None:
                metadata = message.message_metadata or {}
                before_data = {
                    "body": message.body,
                    "authorLabel": message.author_label,
                    "revisionHash": metadata.get("rocketLastRevision"),
                }
                observed_body = str(payload.get("text") or "[mensagem sem texto]")[:100_000]
                from_rocket = metadata.get("source") == "rocketchat_webhook"
                if from_rocket:
                    message.body = observed_body
                    message.author_label = str(payload.get("user_name") or "Equipe Rocket.Chat")[
                        :180
                    ]
                message.delivery_status = MessageDeliveryStatus.received
                message.message_metadata = {
                    **metadata,
                    "source": (
                        "rocketchat_webhook" if from_rocket else "klm_rocketchat_projection"
                    ),
                    "rocketAccountId": account_id,
                    "rocketRoomId": room_id,
                    "rocketRoomName": room_name,
                    "rocketMessageId": message_id,
                    "rocketThreadId": payload.get("tmid"),
                    "rocketLastRevision": revision_hash,
                    "rocketLastEventAt": _event_time(payload.get("timestamp")).isoformat(),
                    "rocketEditCount": int(metadata.get("rocketEditCount") or 0) + 1,
                }
                await self.operations.record_audit(
                    workspace_id=workspace_id,
                    actor_user_id=None,
                    actor_type="system",
                    actor_label="Rocket.Chat bridge",
                    event_type="rocketchat.message_updated",
                    entity_type="omnichannel_message",
                    entity_id=message.id,
                    summary="Mensagem interna reconciliada a partir do Rocket.Chat local.",
                    correlation_id=f"rocketchat:{message_id}",
                    before_data=before_data,
                    after_data={
                        "body": message.body,
                        "authorLabel": message.author_label,
                        "revisionHash": revision_hash,
                        "contentUpdated": from_rocket,
                    },
                )
                await self.db.flush()
                message_deduplicated = False
            else:
                message, message_deduplicated = await self.operations.create_message(
                    workspace_id,
                    thread.id,
                    None,
                    OmnichannelMessageCreate(
                        channel=OmnichannelChannel.internal,
                        direction=MessageDirection.internal,
                        author_type=MessageAuthorType.human,
                        author_label=str(payload.get("user_name") or "Equipe Rocket.Chat")[:180],
                        body=str(payload.get("text") or "[mensagem sem texto]")[:100_000],
                        idempotency_key=f"rocketchat:{message_id}"[:160],
                        external_message_id=message_id,
                        delivery_status=MessageDeliveryStatus.received,
                        occurred_at=_event_time(payload.get("timestamp")),
                        message_metadata={
                            "source": "rocketchat_webhook",
                            "rocketAccountId": account_id,
                            "rocketRoomId": room_id,
                            "rocketRoomName": room_name,
                            "rocketMessageId": message_id,
                            "rocketThreadId": payload.get("tmid"),
                            "rocketLastRevision": revision_hash,
                            "rocketLastEventAt": _event_time(payload.get("timestamp")).isoformat(),
                            "rocketEditCount": 0,
                        },
                    ),
                    trusted_external_receipt=True,
                )
            if not await self.links.finish_receipt(
                workspace_id,
                OSSBridgeProvider.rocketchat,
                receipt_id,
                lease_started_at,
                IntegrationEventReceiptStatus.processed,
            ):
                await self.db.rollback()
                await self.db.refresh(receipt)
                return {"status": receipt.status.value, "deduplicated": True}
            await self.db.commit()
            return {
                "status": "processed",
                "deduplicated": deduplicated or message_deduplicated,
                "threadId": str(thread.id),
                "messageId": str(message.id),
                "messageUpdated": message_updated,
            }
        except Exception:
            await self.db.rollback()
            await self.links.finish_receipt(
                workspace_id,
                OSSBridgeProvider.rocketchat,
                receipt_id,
                lease_started_at,
                IntegrationEventReceiptStatus.failed,
            )
            await self.db.commit()
            raise
