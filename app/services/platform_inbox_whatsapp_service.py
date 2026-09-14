"""Roteamento do webhook oficial da Meta para a inbox própria do SaaS."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_inbox import (
    PlatformInboxChannel,
    PlatformInboxConversation,
    PlatformInboxDeliveryStatus,
    PlatformInboxDirection,
    PlatformInboxMessage,
    PlatformInboxQueue,
    PlatformInboxStatus,
)
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppRole,
)
from app.services.whatsapp.helpers import normalize_meta_phone_number, phone_number_variants
from app.services.whatsapp.meta_webhook_operations_service import (
    MetaWebhookOperationsService,
    supported_meta_webhook_fields,
)

_STATUS_MAP = {
    "sent": PlatformInboxDeliveryStatus.sent,
    "delivered": PlatformInboxDeliveryStatus.delivered,
    "read": PlatformInboxDeliveryStatus.read,
    "failed": PlatformInboxDeliveryStatus.failed,
}


class PlatformInboxWhatsAppService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _body(message: dict[str, Any]) -> str:
        message_type = str(message.get("type") or "unknown")
        if message_type == "text":
            return str((message.get("text") or {}).get("body") or "").strip()
        if message_type == "button":
            return str((message.get("button") or {}).get("text") or "[Botão]").strip()
        if message_type == "interactive":
            interactive = message.get("interactive") or {}
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            return str(reply.get("title") or reply.get("id") or "[Interação]").strip()
        return f"[{message_type}]"

    @staticmethod
    def _phone(value: Any) -> str:
        return normalize_meta_phone_number(str(value or "")) or ""

    async def _connection(
        self, phone_number_id: str
    ) -> PlatformOfficialWhatsAppConnection | None:
        if not phone_number_id:
            return None
        return await self.db.scalar(
            select(PlatformOfficialWhatsAppConnection).where(
                PlatformOfficialWhatsAppConnection.phone_number_id == phone_number_id
            )
        )

    async def _connection_by_route(
        self,
        *,
        phone_number_id: str,
        waba_id: str,
    ) -> PlatformOfficialWhatsAppConnection | None:
        if phone_number_id:
            connection = await self._connection(phone_number_id)
            if connection is not None:
                return connection
        if not waba_id:
            return None
        return await self.db.scalar(
            select(PlatformOfficialWhatsAppConnection).where(
                or_(
                    PlatformOfficialWhatsAppConnection.waba_id == waba_id,
                    PlatformOfficialWhatsAppConnection.business_account_id == waba_id,
                )
            )
        )

    async def _record_platform_signal(
        self,
        *,
        connection: PlatformOfficialWhatsAppConnection,
        field: str,
        value: dict[str, Any],
    ) -> None:
        if field not in supported_meta_webhook_fields():
            return
        await MetaWebhookOperationsService(self.db).record_platform(
            field=field,
            value=value,
            connection=connection,
        )

    async def _ingest_message(
        self,
        *,
        connection: PlatformOfficialWhatsAppConnection,
        raw: dict[str, Any],
        contact_phone: str,
        contact_name: str | None,
        direction: PlatformInboxDirection,
        historical: bool,
        source_field: str,
    ) -> bool:
        external_id = str(raw.get("id") or "").strip()
        contact = self._phone(contact_phone)
        if not external_id or not contact:
            return False
        if await self.db.scalar(
            select(PlatformInboxMessage.id).where(
                PlatformInboxMessage.external_message_id == external_id
            )
        ):
            return False

        phone_number_id = str(connection.phone_number_id or "")
        thread_key = f"{phone_number_id}:{contact}"
        thread_keys = {
            f"{phone_number_id}:{variant}" for variant in phone_number_variants(contact)
        }
        conversations = list(
            (
                await self.db.scalars(
                    select(PlatformInboxConversation)
                    .where(
                        PlatformInboxConversation.channel
                        == PlatformInboxChannel.whatsapp,
                        PlatformInboxConversation.external_thread_id.in_(thread_keys),
                    )
                    .with_for_update()
                )
            ).all()
        )
        conversation = next(
            (
                candidate
                for candidate in conversations
                if candidate.external_thread_id == thread_key
            ),
            conversations[0] if conversations else None,
        )
        if conversation is not None and len(conversations) > 1:
            await self._merge_alias_conversations(
                winner=conversation,
                aliases=[
                    candidate
                    for candidate in conversations
                    if candidate.id != conversation.id
                ],
            )
        occurred_at = (
            datetime.fromtimestamp(int(raw["timestamp"]), tz=UTC)
            if raw.get("timestamp")
            else datetime.now(UTC)
        )
        body = self._body(raw)
        inbound_live = direction == PlatformInboxDirection.inbound and not historical
        if conversation is None:
            queue = (
                PlatformInboxQueue.support
                if connection.role == PlatformOfficialWhatsAppRole.support
                else PlatformInboxQueue.sales
            )
            first_response_hours, resolution_hours = (
                (1, 24) if queue == PlatformInboxQueue.support else (4, 48)
            )
            conversation = PlatformInboxConversation(
                channel=PlatformInboxChannel.whatsapp,
                external_thread_id=thread_key,
                contact_name=contact_name or contact,
                contact_address=contact,
                queue=queue,
                status=PlatformInboxStatus.open,
                last_message_preview=body[:300],
                last_message_at=occurred_at,
                unread_count=1 if inbound_live else 0,
                first_response_due_at=(
                    occurred_at + timedelta(hours=first_response_hours)
                    if inbound_live
                    else None
                ),
                resolution_due_at=(
                    occurred_at + timedelta(hours=resolution_hours)
                    if inbound_live
                    else None
                ),
                metadata_json={
                    "platformOfficialRole": connection.role.value,
                    "phoneNumberId": phone_number_id,
                    "transportMode": connection.transport_mode,
                },
            )
            self.db.add(conversation)
            await self.db.flush()
        else:
            if conversation.external_thread_id != thread_key:
                conversation.external_thread_id = thread_key
            previous_message_at = conversation.last_message_at
            if previous_message_at is not None and previous_message_at.tzinfo is None:
                previous_message_at = previous_message_at.replace(tzinfo=UTC)
            if previous_message_at is None or occurred_at >= previous_message_at:
                conversation.last_message_preview = body[:300]
                conversation.last_message_at = occurred_at
            if inbound_live:
                conversation.unread_count += 1
                if conversation.status == PlatformInboxStatus.resolved:
                    conversation.status = PlatformInboxStatus.open
                    conversation.resolved_at = None

        self.db.add(
            PlatformInboxMessage(
                conversation_id=conversation.id,
                direction=direction,
                body=body,
                delivery_status=(
                    PlatformInboxDeliveryStatus.received
                    if direction == PlatformInboxDirection.inbound
                    else PlatformInboxDeliveryStatus.sent
                ),
                actor_label=(
                    contact_name or contact
                    if direction == PlatformInboxDirection.inbound
                    else "Equipe pelo WhatsApp Business App"
                ),
                external_message_id=external_id,
                idempotency_key=f"meta:{external_id}",
                occurred_at=occurred_at,
                metadata_json={
                    "type": raw.get("type"),
                    "transportMode": (
                        "coexistence"
                        if source_field != "messages"
                        else connection.transport_mode
                    ),
                    "sourceField": source_field,
                    "historySync": historical,
                },
            )
        )
        return True

    async def _merge_alias_conversations(
        self,
        *,
        winner: PlatformInboxConversation,
        aliases: list[PlatformInboxConversation],
    ) -> None:
        """Consolida threads que diferem apenas pelo nono digito brasileiro.

        O webhook pode reencontrar duas linhas historicas, uma criada pela lista
        com o nono digito e outra pelo ``wa_id`` sem ele. Atualizar a chave da
        segunda para a forma canonica colide com o indice unico e fazia a
        mensagem inteira entrar em retry. A consolidacao ocorre sob lock, move
        o historico e somente entao remove a linha redundante.
        """
        for alias in aliases:
            await self.db.execute(
                update(PlatformInboxMessage)
                .where(PlatformInboxMessage.conversation_id == alias.id)
                .values(conversation_id=winner.id)
            )
            winner.unread_count += alias.unread_count
            if (
                alias.last_message_at is not None
                and (
                    winner.last_message_at is None
                    or alias.last_message_at > winner.last_message_at
                )
            ):
                winner.last_message_at = alias.last_message_at
                winner.last_message_preview = alias.last_message_preview
            if winner.assigned_to_user_id is None:
                winner.assigned_to_user_id = alias.assigned_to_user_id
            if winner.prospect_id is None:
                winner.prospect_id = alias.prospect_id
            if winner.support_ticket_id is None:
                winner.support_ticket_id = alias.support_ticket_id
            if winner.workspace_id is None:
                winner.workspace_id = alias.workspace_id
            if winner.first_response_due_at is None:
                winner.first_response_due_at = alias.first_response_due_at
            if winner.resolution_due_at is None:
                winner.resolution_due_at = alias.resolution_due_at
            await self.db.delete(alias)
        await self.db.flush()

    async def _ingest_partner_event(self, payload: dict[str, Any]) -> dict[str, int | bool]:
        event = str(payload.get("event") or "")
        data = payload.get("data") or {}
        metadata = data.get("metadata") or {}
        connection = await self._connection(str(metadata.get("phone_number_id") or ""))
        if connection is None:
            return {"matched": False, "messages": 0, "statuses": 0}
        await self._record_platform_signal(
            connection=connection,
            field=event,
            value=data if isinstance(data, dict) else {},
        )
        if event == "smb_app_state_sync":
            connection.coexistence_sync_status = "contacts_received"
            connection.last_error_code = None
            connection.last_error_message = None
            await self.db.commit()
            return {"matched": True, "messages": 0, "statuses": 0}
        if event != "history":
            return {"matched": False, "messages": 0, "statuses": 0}

        received = 0
        failed = False
        business_phone = self._phone(
            metadata.get("display_phone_number") or connection.display_phone_number
        )
        for history in data.get("history") or []:
            if history.get("errors"):
                failed = True
                connection.last_error_code = "WA-COEX-HISTORY"
                connection.last_error_message = str(history.get("errors"))[:2000]
                continue
            for thread in history.get("threads") or []:
                thread_phone = self._phone(thread.get("id"))
                for raw in thread.get("messages") or []:
                    sender = self._phone(raw.get("from"))
                    outbound = bool(business_phone and sender == business_phone)
                    contact_phone = (
                        raw.get("to") or thread_phone
                        if outbound
                        else raw.get("from") or thread_phone
                    )
                    if await self._ingest_message(
                        connection=connection,
                        raw=raw,
                        contact_phone=str(contact_phone),
                        contact_name=None,
                        direction=(
                            PlatformInboxDirection.outbound
                            if outbound
                            else PlatformInboxDirection.inbound
                        ),
                        historical=True,
                        source_field="history",
                    ):
                        received += 1
        connection.coexistence_sync_status = "partial" if failed else "completed"
        if not failed:
            connection.last_error_code = None
            connection.last_error_message = None
        await self.db.commit()
        return {"matched": True, "messages": received, "statuses": 0}

    async def ingest(self, payload: dict[str, Any]) -> dict[str, int | bool]:
        if payload.get("event") in {"history", "smb_app_state_sync"}:
            return await self._ingest_partner_event(payload)
        received = 0
        statuses = 0
        matched = False
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            waba_id = str(entry.get("id") or "")
            for change in entry.get("changes") or []:
                value = change.get("value") if isinstance(change, dict) else None
                if not isinstance(value, dict):
                    continue
                metadata = value.get("metadata") or {}
                phone_number_id = str(metadata.get("phone_number_id") or "")
                connection = await self._connection_by_route(
                    phone_number_id=phone_number_id,
                    waba_id=waba_id,
                )
                if connection is None:
                    continue
                matched = True
                source_field = str(change.get("field") or "messages")
                await self._record_platform_signal(
                    connection=connection,
                    field=source_field,
                    value=value,
                )
                contacts = {
                    self._phone(contact.get("wa_id")): str(
                        (contact.get("profile") or {}).get("name")
                        or contact.get("wa_id")
                    )
                    for contact in (value.get("contacts") or [])
                    if isinstance(contact, dict) and contact.get("wa_id")
                }
                raw_messages = (
                    value.get("message_echoes") or []
                    if source_field in {"message_echoes", "smb_message_echoes"}
                    else value.get("messages") or []
                )
                for raw in raw_messages:
                    if not isinstance(raw, dict):
                        continue
                    outbound = source_field in {"message_echoes", "smb_message_echoes"}
                    contact_phone = raw.get("to") if outbound else raw.get("from")
                    normalized_contact = self._phone(contact_phone)
                    if await self._ingest_message(
                        connection=connection,
                        raw=raw,
                        contact_phone=str(contact_phone or ""),
                        contact_name=contacts.get(normalized_contact),
                        direction=(
                            PlatformInboxDirection.outbound
                            if outbound
                            else PlatformInboxDirection.inbound
                        ),
                        historical=False,
                        source_field=source_field,
                    ):
                        received += 1
                for delivery in value.get("statuses") or []:
                    if not isinstance(delivery, dict):
                        continue
                    external_id = str(delivery.get("id") or "")
                    mapped = _STATUS_MAP.get(str(delivery.get("status") or ""))
                    if not external_id or mapped is None:
                        continue
                    message = await self.db.scalar(select(PlatformInboxMessage).where(
                        PlatformInboxMessage.external_message_id == external_id
                    ))
                    if message is not None:
                        message.delivery_status = mapped
                        statuses += 1
        if matched:
            await self.db.commit()
        return {"matched": matched, "messages": received, "statuses": statuses}
