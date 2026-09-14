"""Converge mature channel adapters into the native KLM omnichannel aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import MessageAuthorType, MessageDirection, OmnichannelChannel
from app.models.matter import Matter
from app.models.meta_channels import (
    MetaChannelConnection,
    MetaChannelType,
    MetaSocialConversation,
    MetaSocialMessage,
    SocialEventType,
)
from app.models.whatsapp import (
    ConnectionType,
    WaMessageDirection,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.schemas.omnichannel_v6 import CanonicalChannelEventIngest
from app.services.omnichannel_v6_service import CanonicalResolution, OmnichannelV6Service

_COMMENT_EVENT_TYPES = {
    SocialEventType.post_comment,
    SocialEventType.keyword_comment,
    SocialEventType.page_comment,
}


@dataclass(frozen=True)
class LegacyBridgeResult:
    resolution: CanonicalResolution
    event_id: UUID
    message_id: UUID | None
    deduplicated: bool


class OmnichannelLegacyBridgeService:
    """Keeps legacy source adapters operational while KLM becomes the source of truth."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.omnichannel = OmnichannelV6Service(db)

    async def _matter_links(
        self, workspace_id: UUID, matter_id: UUID | None
    ) -> tuple[UUID | None, UUID | None]:
        if matter_id is None:
            return None, None
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.workspace_id == workspace_id,
                Matter.id == matter_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            return None, None
        return matter.client_id, matter.id

    @staticmethod
    def _whatsapp_provider(connection: WhatsAppConnection) -> str:
        if connection.connection_type == ConnectionType.QR_CODE:
            return "qr_gateway"
        return "meta_cloud"

    @staticmethod
    def _whatsapp_account_id(connection: WhatsAppConnection) -> str:
        """Resolve the provider account across full models and lightweight test doubles."""
        for field_name in ("phone_number_id", "instance_id", "display_phone_number"):
            value = getattr(connection, field_name, None)
            if value:
                return str(value)
        return str(connection.id)

    async def ingest_whatsapp_message(
        self,
        *,
        workspace_id: UUID,
        connection: WhatsAppConnection,
        contact: WhatsAppContact,
        message: WhatsAppMessage,
        body: str | None = None,
    ) -> LegacyBridgeResult:
        from app.services.whatsapp.connection_service import WhatsAppConnectionService

        connection = await WhatsAppConnectionService(self.db).ensure_omnichannel_projection(
            connection
        )
        client_id, matter_id = await self._matter_links(workspace_id, contact.matter_id)
        inbound = message.direction == WaMessageDirection.inbound
        source_metadata = dict(message.message_metadata or {})
        origin_surface = str(
            source_metadata.get("originSurface") or ("customer" if inbound else "cloud_api")
        )
        campaign_outbound = not inbound and origin_surface == "campaign"
        transport_mode = str(
            source_metadata.get("transportMode") or connection.transport_mode or "official_api"
        )
        event_key = message.wamid or f"legacy-whatsapp-message:{message.id}"
        content = (body if body is not None else message.transcript or message.body).strip()
        payload = CanonicalChannelEventIngest(
            provider=self._whatsapp_provider(connection),
            connection_key=f"whatsapp:{connection.id}",
            channel=OmnichannelChannel.whatsapp,
            event_type="message.received" if inbound else "message.created",
            external_event_id=event_key,
            external_thread_id=f"whatsapp-contact:{contact.id}",
            external_user_id=contact.phone,
            external_account_id=self._whatsapp_account_id(connection),
            display_name=contact.name or f"Contato {contact.phone[-4:]}",
            profile_avatar_url=contact.avatar_url,
            address=contact.phone,
            canonical_phone=contact.phone,
            client_id=client_id,
            matter_id=matter_id,
            direction=(MessageDirection.inbound if inbound else MessageDirection.outbound),
            author_type=(
                MessageAuthorType.client
                if inbound
                else MessageAuthorType.agent
                if campaign_outbound
                else MessageAuthorType.human
            ),
            author_label=(
                contact.name
                if inbound
                else f"Disparo · {source_metadata.get('campaignTitle') or 'Campanha'}"
                if campaign_outbound
                else "Equipe pelo WhatsApp Business App"
                if origin_surface == "business_app"
                else "Equipe KLM"
            ),
            body=content or f"[{message.media_type or 'mensagem'}]",
            content_type=message.media_type or "text",
            visibility="client_private",
            actionability="reply_required" if inbound else "informational",
            occurred_at=message.timestamp,
            payload={
                "legacyMessageId": str(message.id),
                "mediaType": message.media_type,
                "mediaReference": message.media_url,
                "structuredContent": source_metadata.get("structuredContent") or {},
                "campaign": {
                    "id": source_metadata.get("campaignId"),
                    "title": source_metadata.get("campaignTitle"),
                    "recipientId": source_metadata.get("campaignRecipientId"),
                    "templateId": source_metadata.get("metaTemplateId"),
                    "templateName": source_metadata.get("metaTemplateName"),
                }
                if campaign_outbound
                else {},
            },
            metadata={
                "legacyWhatsAppMessageId": str(message.id),
                "legacyWhatsAppContactId": str(contact.id),
                "legacyConnectionId": str(connection.id),
                "providerStatus": "received" if inbound else "sent",
                "source": source_metadata.get("source") or "native_adapter_bridge",
                "originSurface": origin_surface,
                "transportMode": transport_mode,
                "externalMessageId": message.wamid,
                "coexistenceEcho": bool(source_metadata.get("coexistenceEcho")),
                "historySync": bool(source_metadata.get("historySync")),
                "billableByMeta": source_metadata.get("billableByMeta"),
                "broadcast": campaign_outbound,
                "campaignId": source_metadata.get("campaignId"),
                "campaignTitle": source_metadata.get("campaignTitle"),
                "campaignRecipientId": source_metadata.get("campaignRecipientId"),
                "metaTemplateId": source_metadata.get("metaTemplateId"),
                "metaTemplateName": source_metadata.get("metaTemplateName"),
                "followUpFlowId": source_metadata.get("followUpFlowId"),
                "flowStartMode": source_metadata.get("flowStartMode"),
            },
        )
        resolution, event, canonical_message, deduplicated = await self.omnichannel.ingest_event(
            workspace_id, payload
        )
        metadata = dict(message.message_metadata or {})
        metadata["canonical"] = {
            "identityId": str(resolution.identity.id),
            "threadId": str(resolution.thread.id),
            "eventId": str(event.id),
            "messageId": str(canonical_message.id) if canonical_message else None,
        }
        message.message_metadata = metadata
        return LegacyBridgeResult(
            resolution=resolution,
            event_id=event.id,
            message_id=canonical_message.id if canonical_message else None,
            deduplicated=deduplicated,
        )

    async def ingest_meta_social_message(
        self,
        *,
        workspace_id: UUID,
        connection: MetaChannelConnection,
        conversation: MetaSocialConversation,
        message: MetaSocialMessage,
    ) -> LegacyBridgeResult:
        client_id, matter_id = await self._matter_links(workspace_id, conversation.matter_id)
        channel = (
            OmnichannelChannel.instagram
            if conversation.channel_type == MetaChannelType.instagram
            else OmnichannelChannel.messenger
        )
        inbound = message.direction == WaMessageDirection.inbound
        public = conversation.event_type in _COMMENT_EVENT_TYPES or message.is_public_reply
        event_key = message.external_message_id or f"legacy-meta-message:{message.id}"
        participant_id = conversation.external_participant_id or conversation.external_thread_id
        payload = CanonicalChannelEventIngest(
            provider="meta_instagram"
            if channel == OmnichannelChannel.instagram
            else "meta_messenger",
            connection_key=f"meta-channel:{connection.id}",
            channel=channel,
            event_type=conversation.event_type.value,
            external_event_id=event_key,
            external_thread_id=conversation.external_thread_id,
            external_user_id=participant_id,
            external_account_id=connection.external_account_id or str(connection.id),
            display_name=conversation.participant_name,
            profile_avatar_url=conversation.participant_avatar_url,
            address=conversation.participant_handle or participant_id,
            client_id=client_id,
            matter_id=matter_id,
            direction=(MessageDirection.inbound if inbound else MessageDirection.outbound),
            author_type=(MessageAuthorType.client if inbound else MessageAuthorType.human),
            author_label=message.author_name or conversation.participant_name,
            body=message.body,
            visibility="public" if public else "client_private",
            public_url=conversation.source_url,
            actionability="reply_required" if inbound else "informational",
            occurred_at=message.timestamp,
            correlation={
                "sourceTitle": conversation.source_title,
                "eventType": conversation.event_type.value,
            },
            payload={"legacyMessageId": str(message.id)},
            metadata={
                "legacyMetaMessageId": str(message.id),
                "legacyMetaConversationId": str(conversation.id),
                "legacyConnectionId": str(connection.id),
                "providerStatus": "received" if inbound else "local_created",
                "source": "native_adapter_bridge",
            },
        )
        resolution, event, canonical_message, deduplicated = await self.omnichannel.ingest_event(
            workspace_id, payload
        )
        metadata: dict[str, Any] = dict(message.message_metadata or {})
        metadata["canonical"] = {
            "identityId": str(resolution.identity.id),
            "threadId": str(resolution.thread.id),
            "eventId": str(event.id),
            "messageId": str(canonical_message.id) if canonical_message else None,
        }
        message.message_metadata = metadata
        return LegacyBridgeResult(
            resolution=resolution,
            event_id=event.id,
            message_id=canonical_message.id if canonical_message else None,
            deduplicated=deduplicated,
        )
