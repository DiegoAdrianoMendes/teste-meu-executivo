"""Adaptador Resend oficial para as caixas de vendas e suporte da plataforma."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import time
from datetime import datetime, timedelta
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


class PlatformInboxEmailService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def configured(channel: PlatformInboxChannel) -> bool:
        address = (
            settings.PLATFORM_EMAIL_SUPPORT_ADDRESS
            if channel == PlatformInboxChannel.email_support
            else settings.PLATFORM_EMAIL_SALES_ADDRESS
        )
        return bool(settings.RESEND_API_KEY.strip() and settings.RESEND_WEBHOOK_SECRET.strip() and address.strip())

    @staticmethod
    def verify_svix(*, body: bytes, message_id: str, timestamp: str, signature: str) -> bool:
        secret = settings.RESEND_WEBHOOK_SECRET.strip()
        if not secret or not message_id or not timestamp or not signature:
            return False
        try:
            issued_at = int(timestamp)
        except ValueError:
            return False
        if abs(int(time.time()) - issued_at) > 300:
            return False
        encoded_secret = secret.removeprefix("whsec_")
        try:
            key = base64.b64decode(encoded_secret)
        except (ValueError, TypeError):
            return False
        signed = f"{message_id}.{timestamp}.".encode() + body
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        candidates = [part.split(",", 1)[1] for part in signature.split() if part.startswith("v1,")]
        return any(hmac.compare_digest(candidate, expected) for candidate in candidates)

    @staticmethod
    def _plain_text(value: dict[str, Any]) -> str:
        text = str(value.get("text") or "").strip()
        if text:
            return text
        markup = str(value.get("html") or "")
        if not markup:
            return ""
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()

    async def _received_email(self, email_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"https://api.resend.com/emails/receiving/{email_id}",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY.strip()}"},
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError("resend_received_email_unavailable")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("resend_received_email_invalid")
        return payload

    async def ingest(self, payload: dict[str, Any], *, svix_id: str) -> dict[str, int | bool | str | None]:
        event_type = str(payload.get("type") or "")
        if not isinstance(payload.get("data"), dict):
            return {"matched": False, "channel": None, "messages": 0, "statuses": 0}
        data = payload["data"]
        email_id = str(data.get("email_id") or "")
        status_map = {
            "email.sent": PlatformInboxDeliveryStatus.sent,
            "email.delivered": PlatformInboxDeliveryStatus.delivered,
            "email.opened": PlatformInboxDeliveryStatus.read,
            "email.bounced": PlatformInboxDeliveryStatus.failed,
            "email.failed": PlatformInboxDeliveryStatus.failed,
        }
        if event_type in status_map and email_id:
            message = await self.db.scalar(select(PlatformInboxMessage).where(
                PlatformInboxMessage.external_message_id == email_id
            ))
            if message is None:
                return {"matched": False, "channel": None, "messages": 0, "statuses": 0}
            message.delivery_status = status_map[event_type]
            message.metadata_json = {**(message.metadata_json or {}), "lastResendEvent": event_type, "lastResendSvixId": svix_id}
            conversation = await self.db.get(PlatformInboxConversation, message.conversation_id)
            await self.db.commit()
            return {"matched": True, "channel": conversation.channel.value if conversation else None, "messages": 0, "statuses": 1}
        if event_type != "email.received":
            return {"matched": False, "channel": None, "messages": 0, "statuses": 0}
        sender = str(data.get("from") or "").strip().lower()
        recipients = {str(item).strip().lower() for item in (data.get("to") or [])}
        sales = settings.PLATFORM_EMAIL_SALES_ADDRESS.strip().lower()
        support = settings.PLATFORM_EMAIL_SUPPORT_ADDRESS.strip().lower()
        if support and support in recipients:
            channel, queue, recipient = PlatformInboxChannel.email_support, PlatformInboxQueue.support, support
        elif sales and sales in recipients:
            channel, queue, recipient = PlatformInboxChannel.email_sales, PlatformInboxQueue.sales, sales
        else:
            return {"matched": False, "channel": None, "messages": 0, "statuses": 0}
        if not email_id or not sender or not settings.RESEND_API_KEY.strip():
            raise RuntimeError("resend_email_event_incomplete")
        if await self.db.scalar(select(PlatformInboxMessage.id).where(
            PlatformInboxMessage.external_message_id == email_id
        )):
            return {"matched": True, "channel": channel.value, "messages": 0, "statuses": 0}
        received = await self._received_email(email_id)
        body = self._plain_text(received)
        if not body:
            raise RuntimeError("resend_email_body_empty")
        occurred_at = datetime.fromisoformat(str(data.get("created_at") or payload.get("created_at")).replace("Z", "+00:00"))
        thread_key = f"{recipient}:{sender}"
        conversation = await self.db.scalar(select(PlatformInboxConversation).where(
            PlatformInboxConversation.channel == channel,
            PlatformInboxConversation.external_thread_id == thread_key,
        ))
        subject = str(data.get("subject") or received.get("subject") or "Sem assunto")[:240]
        if conversation is None:
            first_hours, resolution_hours = (1, 24) if queue == PlatformInboxQueue.support else (4, 48)
            conversation = PlatformInboxConversation(
                channel=channel, external_thread_id=thread_key,
                contact_name=sender, contact_address=sender, subject=subject,
                queue=queue, status=PlatformInboxStatus.open,
                last_message_preview=body[:300], last_message_at=occurred_at, unread_count=1,
                first_response_due_at=occurred_at + timedelta(hours=first_hours),
                resolution_due_at=occurred_at + timedelta(hours=resolution_hours),
                metadata_json={"platformRecipient": recipient},
            )
            self.db.add(conversation)
            await self.db.flush()
        else:
            conversation.last_message_preview = body[:300]
            conversation.last_message_at = occurred_at
            conversation.unread_count += 1
            conversation.subject = subject
            if conversation.status == PlatformInboxStatus.resolved:
                conversation.status = PlatformInboxStatus.open
                conversation.resolved_at = None
        self.db.add(PlatformInboxMessage(
            conversation_id=conversation.id, direction=PlatformInboxDirection.inbound,
            body=body, delivery_status=PlatformInboxDeliveryStatus.received,
            actor_label=sender, external_message_id=email_id,
            idempotency_key=f"resend:{svix_id}", occurred_at=occurred_at,
            metadata_json={"messageId": data.get("message_id"), "subject": subject},
        ))
        await self.db.commit()
        return {"matched": True, "channel": channel.value, "messages": 1, "statuses": 0}

    async def send_text(
        self,
        *,
        channel: PlatformInboxChannel,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> tuple[bool, str | None]:
        if channel not in {PlatformInboxChannel.email_sales, PlatformInboxChannel.email_support}:
            return False, None
        if not self.configured(channel):
            return False, None
        sender = settings.EMAIL_FROM.strip()
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {settings.RESEND_API_KEY.strip()}",
                        "Idempotency-Key": idempotency_key,
                    },
                    json={"from": f"{settings.EMAIL_FROM_NAME} <{sender}>", "to": [recipient], "subject": subject, "text": body},
                )
            if response.status_code < 200 or response.status_code >= 300:
                return False, None
            result = response.json()
            return True, str(result.get("id") or "") or None
        except (httpx.HTTPError, ValueError, TypeError):
            return False, None
