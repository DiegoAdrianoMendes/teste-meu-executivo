"""Adaptador Meta oficial da Inbox própria (Messenger e Instagram)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.platform_inbox import (
    PlatformInboxChannel,
    PlatformInboxConversation,
    PlatformInboxDeliveryStatus,
    PlatformInboxDirection,
    PlatformInboxMessage,
    PlatformInboxQueue,
    PlatformInboxStatus,
)


class PlatformInboxMetaService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def configured(channel: PlatformInboxChannel) -> bool:
        if channel == PlatformInboxChannel.messenger:
            return bool(settings.PLATFORM_META_PAGE_ID.strip() and settings.PLATFORM_META_PAGE_ACCESS_TOKEN.strip())
        if channel == PlatformInboxChannel.instagram:
            return bool(settings.PLATFORM_INSTAGRAM_ACCOUNT_ID.strip() and settings.PLATFORM_INSTAGRAM_ACCESS_TOKEN.strip())
        return False

    @staticmethod
    def _channel(payload: dict[str, Any]) -> PlatformInboxChannel | None:
        object_type = str(payload.get("object") or "").lower()
        if object_type == "page":
            return PlatformInboxChannel.messenger
        if object_type == "instagram":
            return PlatformInboxChannel.instagram
        return None

    async def ingest(self, payload: dict[str, Any]) -> dict[str, int | bool | str | None]:
        channel = self._channel(payload)
        if channel is None:
            return {"matched": False, "channel": None, "messages": 0}
        expected_recipient = (
            settings.PLATFORM_META_PAGE_ID.strip()
            if channel == PlatformInboxChannel.messenger
            else settings.PLATFORM_INSTAGRAM_ACCOUNT_ID.strip()
        )
        if not expected_recipient:
            return {"matched": False, "channel": channel.value, "messages": 0}

        received = 0
        matched = False
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            for event in entry.get("messaging") or []:
                if not isinstance(event, dict):
                    continue
                sender = str((event.get("sender") or {}).get("id") or "")
                recipient = str((event.get("recipient") or {}).get("id") or entry.get("id") or "")
                message = event.get("message") or {}
                external_id = str(message.get("mid") or "")
                body = str(message.get("text") or "").strip()
                if recipient != expected_recipient or not sender or not external_id or not body:
                    continue
                matched = True
                if await self.db.scalar(select(PlatformInboxMessage.id).where(
                    PlatformInboxMessage.external_message_id == external_id
                )):
                    continue
                occurred_at = (
                    datetime.fromtimestamp(int(event["timestamp"]) / 1000, tz=timezone.utc)
                    if event.get("timestamp") else datetime.now(timezone.utc)
                )
                thread_key = f"{recipient}:{sender}"
                conversation = await self.db.scalar(select(PlatformInboxConversation).where(
                    PlatformInboxConversation.channel == channel,
                    PlatformInboxConversation.external_thread_id == thread_key,
                ))
                if conversation is None:
                    conversation = PlatformInboxConversation(
                        channel=channel,
                        external_thread_id=thread_key,
                        contact_name=sender,
                        contact_address=sender,
                        queue=PlatformInboxQueue.sales,
                        status=PlatformInboxStatus.open,
                        last_message_preview=body[:300],
                        last_message_at=occurred_at,
                        unread_count=1,
                        first_response_due_at=occurred_at + timedelta(hours=4),
                        resolution_due_at=occurred_at + timedelta(hours=48),
                        metadata_json={"metaRecipientId": recipient},
                    )
                    self.db.add(conversation)
                    await self.db.flush()
                else:
                    conversation.last_message_preview = body[:300]
                    conversation.last_message_at = occurred_at
                    conversation.unread_count += 1
                    if conversation.status == PlatformInboxStatus.resolved:
                        conversation.status = PlatformInboxStatus.open
                        conversation.resolved_at = None
                self.db.add(PlatformInboxMessage(
                    conversation_id=conversation.id,
                    direction=PlatformInboxDirection.inbound,
                    body=body,
                    delivery_status=PlatformInboxDeliveryStatus.received,
                    actor_label=sender,
                    external_message_id=external_id,
                    idempotency_key=f"meta-social:{external_id}",
                    occurred_at=occurred_at,
                    metadata_json={"channel": channel.value},
                ))
                received += 1
        if matched:
            await self.db.commit()
        return {"matched": matched, "channel": channel.value, "messages": received}

    async def send_text(
        self,
        *,
        channel: PlatformInboxChannel,
        recipient_id: str,
        body: str,
    ) -> tuple[bool, str | None]:
        if channel == PlatformInboxChannel.messenger:
            sender_id = settings.PLATFORM_META_PAGE_ID.strip()
            token = settings.PLATFORM_META_PAGE_ACCESS_TOKEN.strip()
        elif channel == PlatformInboxChannel.instagram:
            sender_id = settings.PLATFORM_INSTAGRAM_ACCOUNT_ID.strip()
            token = settings.PLATFORM_INSTAGRAM_ACCESS_TOKEN.strip()
        else:
            return False, None
        if not sender_id or not token:
            return False, None
        url = f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{sender_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"recipient": {"id": recipient_id}, "messaging_type": "RESPONSE", "message": {"text": body}},
                )
            if response.status_code < 200 or response.status_code >= 300:
                return False, None
            payload = response.json()
            return True, str(payload.get("message_id") or payload.get("messageId") or "") or None
        except (httpx.HTTPError, ValueError, TypeError):
            return False, None
