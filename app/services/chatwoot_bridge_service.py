"""Signed, idempotent Chatwoot webhook ingestion into the KLM case record."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tenant import set_workspace_context
from app.models.client import Client
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
)
from app.models.matter import HealthSubtheme, KanbanColumnId, Matter, MicroStatus, PracticeArea
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    IntegrationEventReceipt,
    IntegrationEventReceiptStatus,
    OSSBridgeProvider,
)
from app.models.workspace import Workspace
from app.schemas.klm_operations import OmnichannelMessageCreate, OmnichannelThreadCreate
from app.schemas.oss_bridge import ExternalEntityLinkUpsert, IntegrationEventReceiptCreate
from app.services.klm_operations_service import KlmOperationsService
from app.services.knowledge_service import KnowledgeService
from app.services.oss_bridge_service import OSSBridgeService
from app.services.whatsapp.helpers import phone_number_lookup_variants

CHATWOOT_RECEIPT_LEASE_TIMEOUT = timedelta(minutes=5)
SAFE_LOCAL_ATTACHMENT_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_LOCAL_ATTACHMENT_PORTS = frozenset({80, 443, 3000, 3100, 8080, 8443})


class ChatwootAccountBindingError(PermissionError):
    """Raised when a Chatwoot account is not bound to the selected workspace."""


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _identifier(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"Chatwoot webhook sem {field}.")
    if len(normalized) > 255:
        raise ValueError(f"Chatwoot webhook com {field} inválido.")
    return normalized


def chatwoot_account_id(payload: dict[str, Any]) -> str:
    """Return the single consistent account identity carried by a webhook."""
    account = _mapping(payload.get("account"))
    conversation = _mapping(payload.get("conversation"))
    candidates = {
        str(value).strip()
        for value in (
            account.get("id"),
            payload.get("account_id"),
            conversation.get("account_id"),
        )
        if value is not None and str(value).strip()
    }
    if not candidates:
        raise ValueError("Chatwoot webhook sem account ID.")
    if len(candidates) != 1:
        raise ChatwootAccountBindingError(
            "Webhook Chatwoot possui identificadores de conta divergentes."
        )
    return _identifier(candidates.pop(), field="account ID")


def _occurred_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _safe_local_attachment_url(value: Any) -> str | None:
    raw_url = str(value or "").strip()
    if not raw_url:
        return None
    try:
        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    if scheme not in {"http", "https"} or hostname not in SAFE_LOCAL_ATTACHMENT_HOSTS:
        return None
    effective_port = port or (443 if scheme == "https" else 80)
    if effective_port not in SAFE_LOCAL_ATTACHMENT_PORTS:
        return None
    authority_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = f"{authority_host}:{port}" if port is not None else authority_host
    return urlunsplit((scheme, authority, parsed.path or "/", "", ""))


def _safe_attachment_metadata(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("attachments")
    if not isinstance(rows, list):
        return []
    safe: list[dict[str, Any]] = []
    for row in rows[:25]:
        item = _mapping(row)
        safe.append(
            {
                "externalProvider": "chatwoot",
                "externalAttachmentId": str(item.get("id") or "")[:120] or None,
                "fileType": str(item.get("file_type") or item.get("type") or "")[:80] or None,
                "name": str(item.get("file_name") or item.get("name") or "")[:255] or None,
                "localUrl": _safe_local_attachment_url(
                    item.get("data_url") or item.get("file_url")
                ),
            }
        )
    return safe


def _channel(payload: dict[str, Any]) -> OmnichannelChannel:
    conversation = _mapping(payload.get("conversation"))
    inbox = _mapping(payload.get("inbox")) or _mapping(conversation.get("inbox"))
    raw = " ".join(
        str(value or "")
        for value in (
            inbox.get("channel_type"),
            inbox.get("name"),
            conversation.get("channel"),
        )
    ).lower()
    if "whatsapp" in raw:
        return OmnichannelChannel.whatsapp
    if "email" in raw:
        return OmnichannelChannel.email
    if "instagram" in raw:
        return OmnichannelChannel.instagram
    return OmnichannelChannel.form


def _sender(payload: dict[str, Any]) -> dict[str, Any]:
    conversation = _mapping(payload.get("conversation"))
    meta = _mapping(conversation.get("meta"))
    return (
        _mapping(payload.get("sender"))
        or _mapping(meta.get("sender"))
        or _mapping(payload.get("contact"))
    )


def _message_contract(payload: dict[str, Any]) -> tuple[MessageDirection, MessageAuthorType]:
    if bool(payload.get("private")):
        return MessageDirection.internal, MessageAuthorType.human
    message_type = str(payload.get("message_type") or "incoming").lower()
    if message_type == "incoming":
        return MessageDirection.inbound, MessageAuthorType.client
    sender_type = str(_sender(payload).get("type") or "").lower()
    author_type = MessageAuthorType.agent if "bot" in sender_type else MessageAuthorType.human
    return MessageDirection.outbound, author_type


def _delivery_status(payload: dict[str, Any], direction: MessageDirection) -> MessageDeliveryStatus:
    if direction in {MessageDirection.inbound, MessageDirection.internal}:
        return MessageDeliveryStatus.received
    raw = str(payload.get("status") or "sent").lower()
    return {
        "sent": MessageDeliveryStatus.sent,
        "delivered": MessageDeliveryStatus.delivered,
        "read": MessageDeliveryStatus.read,
        "failed": MessageDeliveryStatus.failed,
    }.get(raw, MessageDeliveryStatus.sent)


class ChatwootBridgeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.links = OSSBridgeService(db)
        self.operations = KlmOperationsService(db)

    async def ingest(
        self,
        *,
        workspace_id: UUID,
        delivery_id: str,
        payload: dict[str, Any],
        account_id: str | None = None,
    ) -> dict[str, Any]:
        await set_workspace_context(self.db, workspace_id)
        resolved_account_id = account_id or chatwoot_account_id(payload)

        workspace = await self.db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
                Workspace.is_active.is_(True),
            )
        )
        if not workspace:
            raise LookupError("Workspace do webhook não encontrado.")
        await self._require_account_binding(workspace_id, resolved_account_id)

        receipt, deduplicated = await self.links.register_receipt(
            workspace_id,
            IntegrationEventReceiptCreate(
                provider=OSSBridgeProvider.chatwoot,
                external_event_id=delivery_id,
                payload=payload,
            ),
        )
        receipt_id = receipt.id
        await self.db.commit()

        lease_started_at = await self._claim_receipt(workspace_id, receipt_id)
        if lease_started_at is None:
            await self.db.refresh(receipt)
            return await self._deduplicated_result(workspace_id, payload, receipt.status.value)

        try:
            event = str(payload.get("event") or "")
            receipt_status = IntegrationEventReceiptStatus.processed
            if event == "message_created":
                result = await self._ingest_message(
                    workspace_id,
                    payload,
                    resolved_account_id,
                )
            elif event == "message_updated":
                result = await self._ingest_message_update(
                    workspace_id,
                    payload,
                    resolved_account_id,
                )
            elif event in {
                "conversation_created",
                "conversation_updated",
                "conversation_status_changed",
            }:
                result = await self._ingest_conversation_event(
                    workspace_id,
                    payload,
                    resolved_account_id,
                    event,
                )
            elif event in {"contact_created", "contact_updated"}:
                result = await self._ingest_contact_event(
                    workspace_id,
                    payload,
                    resolved_account_id,
                    event,
                )
            else:
                receipt_status = IntegrationEventReceiptStatus.ignored
                result = {
                    "threadId": None,
                    "messageId": None,
                    "knowledgeChunkId": None,
                }
            if not await self._finish_receipt(
                workspace_id,
                receipt_id,
                lease_started_at,
                receipt_status,
            ):
                await self.db.rollback()
                await self.db.refresh(receipt)
                return await self._deduplicated_result(workspace_id, payload, receipt.status.value)
            await self.db.commit()
            return {
                "status": receipt_status.value,
                "event": event,
                "deduplicated": deduplicated,
                **result,
            }
        except Exception:
            await self.db.rollback()
            await self._finish_receipt(
                workspace_id,
                receipt_id,
                lease_started_at,
                IntegrationEventReceiptStatus.failed,
            )
            await self.db.commit()
            raise

    async def _claim_receipt(
        self,
        workspace_id: UUID,
        receipt_id: UUID,
    ) -> datetime | None:
        """Atomically claim a retriable receipt and return its updated_at fencing token."""
        lease_started_at = datetime.now(UTC)
        lease_expired_before = lease_started_at - CHATWOOT_RECEIPT_LEASE_TIMEOUT
        result = await self.db.execute(
            update(IntegrationEventReceipt)
            .where(
                IntegrationEventReceipt.id == receipt_id,
                IntegrationEventReceipt.workspace_id == workspace_id,
                IntegrationEventReceipt.provider == OSSBridgeProvider.chatwoot,
                or_(
                    IntegrationEventReceipt.status.in_(
                        (
                            IntegrationEventReceiptStatus.received,
                            IntegrationEventReceiptStatus.failed,
                        )
                    ),
                    and_(
                        IntegrationEventReceipt.status == IntegrationEventReceiptStatus.processing,
                        IntegrationEventReceipt.updated_at <= lease_expired_before,
                    ),
                ),
            )
            .values(
                status=IntegrationEventReceiptStatus.processing,
                processed_at=None,
                updated_at=lease_started_at,
            )
            .execution_options(synchronize_session="fetch")
        )
        await self.db.commit()
        return lease_started_at if result.rowcount == 1 else None

    async def _finish_receipt(
        self,
        workspace_id: UUID,
        receipt_id: UUID,
        lease_started_at: datetime,
        status: IntegrationEventReceiptStatus,
    ) -> bool:
        """Finish only the owned lease, inside the caller's processing transaction."""
        finished_at = datetime.now(UTC)
        result = await self.db.execute(
            update(IntegrationEventReceipt)
            .where(
                IntegrationEventReceipt.id == receipt_id,
                IntegrationEventReceipt.workspace_id == workspace_id,
                IntegrationEventReceipt.provider == OSSBridgeProvider.chatwoot,
                IntegrationEventReceipt.status == IntegrationEventReceiptStatus.processing,
                IntegrationEventReceipt.updated_at == lease_started_at,
            )
            .values(
                status=status,
                processed_at=finished_at,
                updated_at=finished_at,
            )
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount == 1

    async def _require_account_binding(
        self,
        workspace_id: UUID,
        account_id: str,
    ) -> None:
        binding_id = await self.db.scalar(
            select(ExternalEntityLink.id)
            .where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.provider == OSSBridgeProvider.chatwoot,
                ExternalEntityLink.external_account_id == account_id,
                ExternalEntityLink.klm_entity_type == "workspace",
                ExternalEntityLink.klm_entity_id == workspace_id,
                ExternalEntityLink.external_entity_type == "account",
                ExternalEntityLink.external_entity_id == account_id,
                ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
            )
            .limit(1)
        )
        if binding_id is None:
            raise ChatwootAccountBindingError(
                "Conta Chatwoot não vinculada ao workspace do webhook."
            )

    async def _deduplicated_result(
        self,
        workspace_id: UUID,
        payload: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        external_id = str(payload.get("id") or "")
        message = (
            await self.db.scalar(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.external_message_id == external_id,
                )
            )
            if external_id
            else None
        )
        return {
            "status": status,
            "event": str(payload.get("event") or ""),
            "deduplicated": True,
            "threadId": str(message.thread_id) if message else None,
            "messageId": str(message.id) if message else None,
            "knowledgeChunkId": None,
        }

    async def _ingest_message_update(
        self,
        workspace_id: UUID,
        payload: dict[str, Any],
        account_id: str,
    ) -> dict[str, Any]:
        message_id = _identifier(payload.get("id"), field="message ID")
        source_id = str(payload.get("source_id") or "")
        message = await self._projected_message(workspace_id, source_id)
        if message is None:
            message = await self.db.scalar(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.external_message_id == message_id,
                )
            )
        if message is None:
            return await self._ingest_message(workspace_id, payload, account_id)

        metadata = message.message_metadata or {}
        conversation = _mapping(payload.get("conversation"))
        conversation_id = _identifier(
            conversation.get("id")
            or payload.get("conversation_id")
            or metadata.get("chatwootConversationId"),
            field="conversation ID",
        )
        attachments = _safe_attachment_metadata(payload)
        from_chatwoot = metadata.get("source") == "chatwoot_webhook"
        observed_content = str(
            payload.get("content") or payload.get("processed_message_content") or ""
        )
        if from_chatwoot and observed_content:
            message.body = observed_content[:100_000]
        message.external_message_id = message_id
        message.delivery_status = _delivery_status(payload, message.direction)
        message.message_metadata = {
            **metadata,
            "source": "chatwoot_webhook" if from_chatwoot else "klm_chatwoot_projection",
            "chatwootAccountId": account_id,
            "chatwootConversationId": conversation_id,
            "chatwootMessageId": message_id,
            "chatwootLastEvent": "message_updated",
            "chatwootStatus": str(payload.get("status") or "")[:40] or None,
            "attachments": (
                attachments
                if "attachments" in payload
                else metadata.get("attachments", [])
            ),
            "externalActionsExecuted": 0,
        }

        knowledge_chunk_id = None
        if message.matter_id:
            chunk = await KnowledgeService(self.db).index_omnichannel_message(
                message=message,
            )
            knowledge_chunk_id = str(chunk.id)
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Chatwoot bridge",
            event_type="chatwoot.message_updated",
            entity_type="omnichannel_message",
            entity_id=message.id,
            summary="Estado da mensagem reconciliado a partir do Chatwoot local.",
            correlation_id=f"chatwoot:{message_id}",
            after_data={
                "deliveryStatus": message.delivery_status.value,
                "contentUpdated": bool(from_chatwoot and observed_content),
                "attachmentCount": len(attachments),
                "externalActionsExecuted": 0,
            },
        )
        await self.db.flush()
        return {
            "threadId": str(message.thread_id),
            "messageId": str(message.id),
            "knowledgeChunkId": knowledge_chunk_id,
        }

    async def _ingest_conversation_event(
        self,
        workspace_id: UUID,
        payload: dict[str, Any],
        account_id: str,
        event: str,
    ) -> dict[str, Any]:
        conversation = _mapping(payload.get("conversation")) or payload
        conversation_id = _identifier(
            conversation.get("id") or payload.get("conversation_id"),
            field="conversation ID",
        )
        link = await self.links.find_link_by_external_entity(
            workspace_id,
            OSSBridgeProvider.chatwoot,
            account_id,
            "conversation",
            conversation_id,
        )
        if link is None:
            return {
                "threadId": None,
                "messageId": None,
                "knowledgeChunkId": None,
                "linkState": "unlinked",
            }
        thread = await self.operations.get_thread(workspace_id, link.klm_entity_id)
        if thread is None:
            link.sync_state = ExternalEntitySyncState.stale
            link.sync_error = "Thread KLM vinculada não existe mais."
            raise LookupError("Vínculo Chatwoot aponta para thread inexistente.")

        assignee = _mapping(conversation.get("assignee"))
        team = _mapping(conversation.get("team"))
        observed = {
            "event": event,
            "status": str(conversation.get("status") or "")[:40] or None,
            "priority": str(conversation.get("priority") or "")[:40] or None,
            "labels": [str(item)[:100] for item in conversation.get("labels", [])[:50]]
            if isinstance(conversation.get("labels"), list)
            else [],
            "assigneeId": str(assignee.get("id") or "")[:120] or None,
            "assigneeName": str(assignee.get("name") or "")[:180] or None,
            "teamId": str(team.get("id") or "")[:120] or None,
            "teamName": str(team.get("name") or "")[:180] or None,
            "changedFields": sorted(_mapping(payload.get("changed_attributes")).keys())[:100],
            "observedAt": datetime.now(UTC).isoformat(),
        }
        thread.thread_metadata = {
            **(thread.thread_metadata or {}),
            "chatwootObserved": observed,
        }
        link.link_metadata = {**(link.link_metadata or {}), "lastObserved": observed}
        link.sync_state = ExternalEntitySyncState.synced
        link.last_synced_at = datetime.now(UTC)
        link.sync_error = None
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Chatwoot bridge",
            event_type=f"chatwoot.{event}",
            entity_type="omnichannel_thread",
            entity_id=thread.id,
            summary=(
                "Mudança de conversa observada no Chatwoot; "
                "estado jurídico canônico preservado."
            ),
            correlation_id=f"chatwoot:{conversation_id}",
            after_data={**observed, "externalActionsExecuted": 0},
        )
        await self.db.flush()
        return {
            "threadId": str(thread.id),
            "messageId": None,
            "knowledgeChunkId": None,
            "linkState": "linked",
        }

    async def _ingest_contact_event(
        self,
        workspace_id: UUID,
        payload: dict[str, Any],
        account_id: str,
        event: str,
    ) -> dict[str, Any]:
        contact = _mapping(payload.get("contact")) or payload
        contact_id = _identifier(contact.get("id"), field="contact ID")
        link = await self.links.find_link_by_external_entity(
            workspace_id,
            OSSBridgeProvider.chatwoot,
            account_id,
            "contact",
            contact_id,
        )
        if link is None:
            return {
                "threadId": None,
                "messageId": None,
                "knowledgeChunkId": None,
                "linkState": "unlinked",
            }
        changed_fields = sorted(_mapping(payload.get("changed_attributes")).keys())[:100]
        observed = {
            "event": event,
            "changedFields": changed_fields,
            "observedAt": datetime.now(UTC).isoformat(),
            "canonicalOwner": "klm",
        }
        link.link_metadata = {**(link.link_metadata or {}), "lastObserved": observed}
        link.sync_state = ExternalEntitySyncState.synced
        link.last_synced_at = datetime.now(UTC)
        link.sync_error = None
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Chatwoot bridge",
            event_type=f"chatwoot.{event}",
            entity_type="client",
            entity_id=link.klm_entity_id,
            summary="Mudança de contato observada no Chatwoot; cadastro canônico KLM preservado.",
            correlation_id=f"chatwoot-contact:{contact_id}",
            after_data={**observed, "externalActionsExecuted": 0},
        )
        await self.db.flush()
        return {
            "threadId": None,
            "messageId": None,
            "knowledgeChunkId": None,
            "linkState": "linked",
        }

    async def _ingest_message(
        self,
        workspace_id: UUID,
        payload: dict[str, Any],
        account_id: str,
    ) -> dict[str, Any]:
        conversation = _mapping(payload.get("conversation"))
        conversation_id = _identifier(
            conversation.get("id") or payload.get("conversation_id"),
            field="conversation ID",
        )
        message_id = _identifier(payload.get("id"), field="message ID")
        source_id = str(payload.get("source_id") or "")
        projected_message = await self._projected_message(workspace_id, source_id)
        if projected_message:
            await self._reconcile_projected_message(
                workspace_id=workspace_id,
                account_id=account_id,
                conversation_id=conversation_id,
                message_id=message_id,
                payload=payload,
                projected_message=projected_message,
            )
            return {
                "threadId": str(projected_message.thread_id),
                "messageId": str(projected_message.id),
                "knowledgeChunkId": None,
            }

        link = await self.links.find_link_by_external_entity(
            workspace_id,
            OSSBridgeProvider.chatwoot,
            account_id,
            "conversation",
            conversation_id,
        )
        thread = (
            await self.operations.get_thread(workspace_id, link.klm_entity_id) if link else None
        )
        if link and not thread:
            link.sync_state = ExternalEntitySyncState.stale
            link.sync_error = "Thread KLM vinculada não existe mais."
            raise LookupError("Vínculo Chatwoot aponta para thread inexistente.")

        sender = _sender(payload)
        if not thread:
            client = await self._resolve_client(workspace_id, account_id, sender)
            matter = await self._resolve_matter(workspace_id, client, conversation)
            channel = _channel(payload)
            inbox = _mapping(payload.get("inbox")) or _mapping(conversation.get("inbox"))
            thread = await self.operations.create_thread(
                workspace_id,
                None,
                OmnichannelThreadCreate(
                    matter_id=matter.id,
                    client_id=client.id,
                    primary_channel=channel,
                    subject=str(
                        conversation.get("subject") or inbox.get("name") or "Atendimento Chatwoot"
                    )[:220],
                    object_name=matter.health_object_name,
                    owner_agent_key="sdr_saude",
                    score=matter.lead_score,
                    sla_due_at=datetime.now(UTC) + timedelta(minutes=15),
                    thread_metadata={
                        "source": "chatwoot",
                        "chatwootAccountId": account_id,
                        "chatwootConversationId": conversation_id,
                        "chatwootInboxId": str(inbox.get("id") or "") or None,
                    },
                ),
                actor_type="system",
                actor_label="Chatwoot bridge",
            )
            await self.links.upsert_link(
                workspace_id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.chatwoot,
                    klm_entity_type="omnichannel_thread",
                    klm_entity_id=thread.id,
                    external_account_id=account_id,
                    external_entity_type="conversation",
                    external_entity_id=conversation_id,
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=datetime.now(UTC),
                    metadata={"channel": channel.value},
                ),
            )

        direction, author_type = _message_contract(payload)
        body = str(
            payload.get("content")
            or payload.get("processed_message_content")
            or "[mensagem sem texto]"
        )[:100_000]
        label = str(sender.get("name") or sender.get("email") or "Contato Chatwoot")[:180]
        channel = (
            OmnichannelChannel.internal
            if direction == MessageDirection.internal
            else _channel(payload)
        )
        attachments = _safe_attachment_metadata(payload)
        message, _message_deduplicated = await self.operations.create_message(
            workspace_id,
            thread.id,
            None,
            OmnichannelMessageCreate(
                channel=channel,
                direction=direction,
                author_type=author_type,
                author_label=label,
                body=body,
                external_message_id=message_id,
                idempotency_key=f"chatwoot:{account_id}:{message_id}"[:160],
                delivery_status=_delivery_status(payload, direction),
                occurred_at=_occurred_at(payload.get("created_at")),
                message_metadata={
                    "source": "chatwoot_webhook",
                    "chatwootAccountId": account_id,
                    "chatwootConversationId": conversation_id,
                    "chatwootMessageId": message_id,
                    "attachments": attachments,
                    "private": direction == MessageDirection.internal,
                    "externalActionsExecuted": 0,
                },
            ),
            trusted_external_receipt=True,
        )

        knowledge_chunk_id = None
        if thread.matter_id:
            chunk = await KnowledgeService(self.db).index_omnichannel_message(
                message=message,
            )
            knowledge_chunk_id = str(chunk.id)

        if link:
            link.sync_state = ExternalEntitySyncState.synced
            link.last_synced_at = datetime.now(UTC)
            link.sync_error = None
        return {
            "threadId": str(thread.id),
            "messageId": str(message.id),
            "knowledgeChunkId": knowledge_chunk_id,
        }

    async def _projected_message(
        self,
        workspace_id: UUID,
        source_id: str,
    ) -> OmnichannelMessage | None:
        if not source_id.startswith("klm:"):
            return None
        try:
            message_id = UUID(source_id.removeprefix("klm:"))
        except ValueError:
            return None
        return await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.id == message_id,
                OmnichannelMessage.workspace_id == workspace_id,
            )
        )

    async def _reconcile_projected_message(
        self,
        *,
        workspace_id: UUID,
        account_id: str,
        conversation_id: str,
        message_id: str,
        payload: dict[str, Any],
        projected_message: OmnichannelMessage,
    ) -> None:
        """Resolve bridge echoes before any contact, matter, or thread can be created."""
        link = await self.links.find_link_by_external_entity(
            workspace_id,
            OSSBridgeProvider.chatwoot,
            account_id,
            "conversation",
            conversation_id,
        )
        if link and link.klm_entity_id != projected_message.thread_id:
            raise ValueError("Eco Chatwoot aponta para uma conversa KLM divergente.")
        if not link:
            await self.links.upsert_link(
                workspace_id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.chatwoot,
                    klm_entity_type="omnichannel_thread",
                    klm_entity_id=projected_message.thread_id,
                    external_account_id=account_id,
                    external_entity_type="conversation",
                    external_entity_id=conversation_id,
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=datetime.now(UTC),
                    metadata={"recoveredFrom": "message_source_id"},
                ),
            )
        else:
            link.sync_state = ExternalEntitySyncState.synced
            link.last_synced_at = datetime.now(UTC)
            link.sync_error = None

        direction, _ = _message_contract(payload)
        projected_message.external_message_id = message_id
        projected_message.delivery_status = _delivery_status(payload, direction)
        projected_message.message_metadata = {
            **(projected_message.message_metadata or {}),
            "source": "klm_chatwoot_projection",
            "chatwootAccountId": account_id,
            "chatwootConversationId": conversation_id,
            "chatwootMessageId": message_id,
        }
        await self.db.flush()

    async def _resolve_client(
        self,
        workspace_id: UUID,
        account_id: str,
        sender: dict[str, Any],
    ) -> Client:
        contact_id = str(sender.get("id") or "").strip()
        if contact_id:
            contact_link = await self.links.find_link_by_external_entity(
                workspace_id,
                OSSBridgeProvider.chatwoot,
                account_id,
                "contact",
                contact_id,
            )
            if contact_link and contact_link.klm_entity_type == "client":
                client = await self.db.scalar(
                    select(Client).where(
                        Client.id == contact_link.klm_entity_id,
                        Client.workspace_id == workspace_id,
                        Client.deleted_at.is_(None),
                    )
                )
                if client:
                    return client

        email = str(sender.get("email") or "").strip().lower() or None
        phone = str(sender.get("phone_number") or sender.get("phone") or "").strip() or None
        predicates = []
        if email:
            predicates.append(func.lower(Client.email) == email)
        if phone:
            predicates.append(Client.phone.in_(phone_number_lookup_variants(phone)))
        client = (
            await self.db.scalar(
                select(Client)
                .where(
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                    or_(*predicates),
                )
                .limit(1)
            )
            if predicates
            else None
        )
        if not client:
            client = Client(
                workspace_id=workspace_id,
                name=str(sender.get("name") or email or phone or "Contato Chatwoot")[:200],
                email=email,
                phone=phone,
            )
            self.db.add(client)
            await self.db.flush()

        if contact_id:
            await self.links.upsert_link(
                workspace_id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.chatwoot,
                    klm_entity_type="client",
                    klm_entity_id=client.id,
                    external_account_id=account_id,
                    external_entity_type="contact",
                    external_entity_id=contact_id,
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=datetime.now(UTC),
                    metadata={"email": email, "phone": phone},
                ),
            )
        return client

    async def _resolve_matter(
        self,
        workspace_id: UUID,
        client: Client,
        conversation: dict[str, Any],
    ) -> Matter:
        matter = await self.db.scalar(
            select(Matter)
            .where(
                Matter.workspace_id == workspace_id,
                Matter.client_id == client.id,
                Matter.deleted_at.is_(None),
            )
            .order_by(Matter.updated_at.desc())
            .limit(1)
        )
        if matter:
            return matter
        matter = Matter(
            workspace_id=workspace_id,
            client_id=client.id,
            practice_area=PracticeArea.saude_suplementar,
            demand_type=str(conversation.get("subject") or "Triagem de negativa de plano de saúde")[
                :200
            ],
            lead_source="chatwoot",
            column_id=KanbanColumnId.novo_lead,
            micro_status=MicroStatus.lead_novo,
            health_subtheme=HealthSubtheme.outro,
            next_action="Triagem inicial da conversa recebida no Chatwoot.",
        )
        self.db.add(matter)
        await self.db.flush()
        return matter
