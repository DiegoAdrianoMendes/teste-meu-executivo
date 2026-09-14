from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.models.user import User
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.schemas.whatsapp import (
    ConversationRead,
    SendDocumentRequest,
    SendMessageRequest,
    SendMessageResponse,
    SendStructuredMessageRequest,
    WhatsAppContactRead,
    WhatsAppMessageRead,
)
from app.services.s3_service import S3Service
from app.services.transcription_service import get_transcription_service
from app.services.whatsapp.cloud_payload import provider_media_references
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.helpers import normalize_phone_number, phone_number_variants
from app.services.whatsapp.provider_media_receipt_service import (
    ProviderMediaReceiptError,
    ProviderMediaReceiptService,
)
from app.services.whatsapp.strategy_factory import WhatsAppStrategyFactory

logger = logging.getLogger(__name__)


class WhatsAppMessageService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        connection_service: WhatsAppConnectionService,
        strategy_factory: WhatsAppStrategyFactory,
    ) -> None:
        self.db = db
        self.connection_service = connection_service
        self.strategy_factory = strategy_factory

    async def send_message(
        self,
        payload: SendMessageRequest,
        sent_by_id: UUID,
        workspace_id: UUID,
    ) -> SendMessageResponse:
        return await self._send_outbound(
            kind="text",
            contact_id=payload.contact_id,
            explicit_connection_id=payload.connection_id,
            body=payload.body,
            media_url=None,
            delivery_media_url=None,
            media_digest_reference=None,
            filename=None,
            caption=None,
            structured_payload=None,
            requested_idempotency_key=payload.idempotency_key,
            sent_by_id=sent_by_id,
            workspace_id=workspace_id,
        )

    async def send_document(
        self,
        payload: SendDocumentRequest,
        sent_by_id: UUID,
        workspace_id: UUID,
    ) -> SendMessageResponse:
        persisted_reference = self._persistable_external_media_reference(payload.url)
        return await self._send_outbound(
            kind="document",
            contact_id=payload.contact_id,
            explicit_connection_id=payload.connection_id,
            body=payload.caption or f"[documento] {payload.filename}",
            media_url=persisted_reference,
            delivery_media_url=payload.url,
            media_digest_reference=persisted_reference,
            filename=payload.filename,
            caption=payload.caption,
            structured_payload=None,
            requested_idempotency_key=payload.idempotency_key,
            sent_by_id=sent_by_id,
            workspace_id=workspace_id,
        )

    async def send_storage_document(
        self,
        payload: SendDocumentRequest,
        sent_by_id: UUID,
        workspace_id: UUID,
        *,
        storage_object_version_id: UUID,
        storage_key: str,
    ) -> SendMessageResponse:
        """Dispatch a clean private document without persisting its signed URL.

        The provider receives the short-lived URL, while idempotency and the
        message row use the immutable storage-version reference. A worker retry
        can therefore mint another URL without changing the outbound digest.
        """
        return await self._send_outbound(
            kind="document",
            contact_id=payload.contact_id,
            explicit_connection_id=payload.connection_id,
            body=payload.caption or f"[documento] {payload.filename}",
            media_url=storage_key,
            delivery_media_url=payload.url,
            media_digest_reference=f"storage-object-version:{storage_object_version_id}",
            filename=payload.filename,
            caption=payload.caption,
            structured_payload=None,
            requested_idempotency_key=payload.idempotency_key,
            sent_by_id=sent_by_id,
            workspace_id=workspace_id,
        )

    async def send_structured(
        self,
        payload: SendStructuredMessageRequest,
        sent_by_id: UUID,
        workspace_id: UUID,
    ) -> SendMessageResponse:
        kind = str(payload.message_payload.get("kind") or "structured")
        body = str(
            payload.message_payload.get("body")
            or payload.message_payload.get("name")
            or f"[{kind}]"
        )
        return await self._send_outbound(
            kind="structured",
            contact_id=payload.contact_id,
            explicit_connection_id=payload.connection_id,
            body=body,
            media_url=None,
            delivery_media_url=None,
            media_digest_reference=None,
            filename=None,
            caption=None,
            structured_payload=payload.message_payload,
            requested_idempotency_key=payload.idempotency_key,
            sent_by_id=sent_by_id,
            workspace_id=workspace_id,
        )

    async def _send_outbound(
        self,
        *,
        kind: str,
        contact_id: UUID,
        explicit_connection_id: Optional[UUID],
        body: str,
        media_url: Optional[str],
        delivery_media_url: Optional[str],
        media_digest_reference: Optional[str],
        filename: Optional[str],
        caption: Optional[str],
        structured_payload: dict[str, Any] | None,
        requested_idempotency_key: Optional[str],
        sent_by_id: Optional[UUID],
        workspace_id: UUID,
    ) -> SendMessageResponse:
        contact = await self._get_contact(contact_id, workspace_id)
        idempotency_key = self._normalize_outbound_idempotency_key(requested_idempotency_key)
        stored_key = self._stored_idempotency_key(workspace_id, idempotency_key)
        existing = await self.db.scalar(
            select(WhatsAppMessage).where(WhatsAppMessage.outbound_idempotency_key == stored_key)
        )
        if existing is not None:
            if (
                explicit_connection_id is not None
                and explicit_connection_id != existing.whatsapp_connection_id
            ):
                self._raise_idempotency_conflict()
            connection = await self.connection_service.get_connection(
                existing.whatsapp_connection_id,
                workspace_id,
            )
        else:
            connection = await self._resolve_outbound_connection(
                workspace_id=workspace_id,
                explicit_connection_id=explicit_connection_id,
                contact=contact,
            )
        payload_digest = self._outbound_payload_digest(
            kind=kind,
            workspace_id=workspace_id,
            contact_id=contact.id,
            connection_id=connection.id,
            sent_by_id=sent_by_id,
            body=body,
            media_url=media_digest_reference,
            filename=filename,
            caption=caption,
            structured_payload=structured_payload,
        )
        from app.services.omnichannel_legacy_bridge_service import (
            OmnichannelLegacyBridgeService,
        )

        message: WhatsAppMessage | None = None
        if existing is not None:
            message = await self._get_or_create_outbound_message(
                existing=existing,
                contact=contact,
                connection=connection,
                sent_by_id=sent_by_id,
                kind=kind,
                body=body,
                media_url=media_url,
                structured_payload=structured_payload,
                stored_idempotency_key=stored_key,
                payload_digest=payload_digest,
            )
            if message.status != WaMessageStatus.pending:
                await OmnichannelLegacyBridgeService(self.db).ingest_whatsapp_message(
                    workspace_id=workspace_id,
                    connection=connection,
                    contact=contact,
                    message=message,
                    body=body,
                )
                return self._replay_outbound(message, idempotency_key)

        verified_provider_media_receipt_ids = (
            await self._verify_structured_provider_media(
                workspace_id=workspace_id,
                connection=connection,
                payload=structured_payload or {},
            )
            if kind == "structured"
            else set()
        )
        if message is None:
            message = await self._get_or_create_outbound_message(
                existing=None,
                contact=contact,
                connection=connection,
                sent_by_id=sent_by_id,
                kind=kind,
                body=body,
                media_url=media_url,
                structured_payload=structured_payload,
                stored_idempotency_key=stored_key,
                payload_digest=payload_digest,
            )

        await OmnichannelLegacyBridgeService(self.db).ingest_whatsapp_message(
            workspace_id=workspace_id,
            connection=connection,
            contact=contact,
            message=message,
            body=body,
        )

        if not external_delivery_allowed():
            logger.info(
                "whatsapp.outbound_recorded_pending workspace_id=%s message_id=%s kind=%s",
                workspace_id,
                message.id,
                kind,
            )
            return self._outbound_response(message, idempotency_key)

        owns_dispatch = await self._claim_outbound_dispatch(message.id)
        if not owns_dispatch:
            await self.db.refresh(message)
            return self._replay_outbound(message, idempotency_key)

        try:
            strategy = self.strategy_factory.build(connection)
            if kind == "document":
                result = await strategy.send_document(
                    connection,
                    contact.phone,
                    delivery_media_url or media_url or "",
                    filename or "documento",
                    caption,
                )
            elif kind == "structured":
                result = await strategy.send_structured(
                    connection,
                    contact.phone,
                    structured_payload or {},
                    opaque_callback_data=f"klm:{message.id}",
                    verified_provider_media_receipt_ids=(
                        verified_provider_media_receipt_ids
                    ),
                )
            else:
                result = await strategy.send_text(connection, contact.phone, body)
            if not result.wamid:
                raise WhatsAppProviderError(
                    code="WA-DELIVERY-UNCERTAIN",
                    message=(
                        "O provedor aceitou a requisição sem retornar o identificador da mensagem."
                    ),
                    status_code=502,
                    request_started=True,
                    delivery_outcome_known=False,
                )
        except WhatsAppProviderError as exc:
            await self._record_outbound_provider_error(message.id, exc)
            if exc.connection_unhealthy:
                await self.connection_service.mark_connection_error(
                    connection,
                    exc.code,
                    exc.message,
                )
            raise exc.to_http_exception() from exc
        except Exception as exc:
            await self._record_outbound_unknown_error(message.id, exc)
            logger.warning(
                "whatsapp.outbound_delivery_uncertain workspace_id=%s message_id=%s "
                "kind=%s error_type=%s",
                workspace_id,
                message.id,
                kind,
                safe_error_type(exc),
            )
            raise

        checkpoint = await self.db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == message.id,
                WhatsAppMessage.status == WaMessageStatus.delivery_uncertain,
                WhatsAppMessage.outbound_request_started_at.is_not(None),
            )
            .values(
                wamid=result.wamid,
                status=WaMessageStatus.sent,
                delivery_uncertain_at=None,
                outbound_error=None,
            )
            .execution_options(synchronize_session=False)
        )
        if checkpoint.rowcount != 1:
            await self.db.rollback()
            raise RuntimeError("outbound WhatsApp success checkpoint was fenced")
        contact.whatsapp_connection_id = connection.id
        await self.db.commit()
        await self.db.refresh(message)
        return self._outbound_response(message, idempotency_key)

    async def _verify_structured_provider_media(
        self,
        *,
        workspace_id: UUID,
        connection: WhatsAppConnection,
        payload: dict[str, Any],
    ) -> set[str]:
        references = provider_media_references(payload)
        if not references:
            return set()
        if connection.connection_type == ConnectionType.QR_CODE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "provider_media_official_connection_required",
                    "message": "Mídia por ID exige uma conexão oficial da Meta.",
                },
            )
        receipt_service = ProviderMediaReceiptService(self.db)
        try:
            await self.connection_service.ensure_omnichannel_projection(connection)
            native = await receipt_service.resolve_native_connection(
                workspace_id=workspace_id,
                legacy_connection_id=connection.id,
                provider="meta_cloud",
            )
            return await receipt_service.verify_payload(
                workspace_id=workspace_id,
                connection_id=native.id,
                provider="meta_cloud",
                payload=payload,
            )
        except ProviderMediaReceiptError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    async def _get_or_create_outbound_message(
        self,
        *,
        existing: Optional[WhatsAppMessage],
        contact: WhatsAppContact,
        connection: WhatsAppConnection,
        sent_by_id: Optional[UUID],
        kind: str,
        body: str,
        media_url: Optional[str],
        structured_payload: dict[str, Any] | None,
        stored_idempotency_key: str,
        payload_digest: str,
    ) -> WhatsAppMessage:
        message = existing
        if message is None:
            candidate = WhatsAppMessage(
                contact_id=contact.id,
                whatsapp_connection_id=connection.id,
                direction=WaMessageDirection.outbound,
                status=WaMessageStatus.pending,
                body=body,
                media_url=media_url,
                media_type="document" if kind == "document" else None,
                message_metadata={
                    "originSurface": "cloud_api",
                    "transportMode": connection.transport_mode,
                    "structuredContent": structured_payload or {},
                },
                sent_by_id=sent_by_id,
                outbound_idempotency_key=stored_idempotency_key,
                outbound_payload_digest=payload_digest,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(candidate)
                    await self.db.flush()
                message = candidate
            except IntegrityError:
                message = await self.db.scalar(
                    select(WhatsAppMessage).where(
                        WhatsAppMessage.outbound_idempotency_key == stored_idempotency_key
                    )
                )
                if message is None:
                    raise
            else:
                await self.db.commit()

        if message.outbound_payload_digest != payload_digest:
            self._raise_idempotency_conflict()
        return message

    async def _claim_outbound_dispatch(self, message_id: UUID) -> bool:
        now = datetime.now(timezone.utc)
        claim = await self.db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == message_id,
                WhatsAppMessage.status == WaMessageStatus.pending,
                WhatsAppMessage.outbound_request_started_at.is_(None),
            )
            .values(
                status=WaMessageStatus.delivery_uncertain,
                outbound_request_started_at=now,
                delivery_uncertain_at=now,
                outbound_error=None,
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        return claim.rowcount == 1

    async def _record_outbound_provider_error(
        self,
        message_id: UUID,
        error: WhatsAppProviderError,
    ) -> None:
        uncertain = error.delivery_uncertain
        now = datetime.now(timezone.utc)
        await self.db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == message_id,
                WhatsAppMessage.status == WaMessageStatus.delivery_uncertain,
            )
            .values(
                status=(
                    WaMessageStatus.delivery_uncertain if uncertain else WaMessageStatus.failed
                ),
                delivery_uncertain_at=now if uncertain else None,
                outbound_error={
                    "code": error.code,
                    "message": error.message[:1000],
                    "httpStatus": error.status_code,
                    "providerStatusCode": error.provider_status_code,
                    "requestStarted": error.request_started,
                    "deliveryOutcomeKnown": error.delivery_outcome_known,
                    "errorType": safe_error_type(error),
                },
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()

    async def _record_outbound_unknown_error(
        self,
        message_id: UUID,
        error: Exception,
    ) -> None:
        await self.db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == message_id,
                WhatsAppMessage.status == WaMessageStatus.delivery_uncertain,
            )
            .values(
                delivery_uncertain_at=datetime.now(timezone.utc),
                outbound_error={
                    "code": "WA-DELIVERY-UNCERTAIN",
                    "message": (
                        "A entrega não pôde ser confirmada; reconciliação manual é obrigatória."
                    ),
                    "httpStatus": 502,
                    "requestStarted": True,
                    "deliveryOutcomeKnown": False,
                    "errorType": safe_error_type(error),
                },
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()

    def _replay_outbound(
        self,
        message: WhatsAppMessage,
        idempotency_key: str,
    ) -> SendMessageResponse:
        if message.status == WaMessageStatus.failed:
            error = message.outbound_error or {}
            raise HTTPException(
                status_code=int(error.get("httpStatus") or status.HTTP_409_CONFLICT),
                detail={
                    "code": str(error.get("code") or "WA-IDEMPOTENT-FAILED"),
                    "message": str(
                        error.get("message") or "O envio associado à chave de idempotência falhou."
                    ),
                },
            )
        return self._outbound_response(message, idempotency_key)

    @staticmethod
    def _raise_idempotency_conflict() -> None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WA-IDEMPOTENCY-CONFLICT",
                "message": "A chave de idempotência já pertence a outro envio.",
            },
        )

    @staticmethod
    def _outbound_response(
        message: WhatsAppMessage,
        idempotency_key: str,
    ) -> SendMessageResponse:
        return SendMessageResponse(
            messageId=message.id,
            wamid=message.wamid,
            status=message.status,
            idempotencyKey=idempotency_key,
        )

    @staticmethod
    def _normalize_outbound_idempotency_key(value: Optional[str]) -> str:
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-IDEMPOTENCY-REQUIRED",
                    "message": "Idempotency-Key é obrigatório para qualquer envio externo.",
                },
            )
        normalized = value.strip()
        if len(normalized) < 8 or len(normalized) > 160:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-IDEMPOTENCY-INVALID",
                    "message": "A chave de idempotência deve ter entre 8 e 160 caracteres.",
                },
            )
        return normalized

    @staticmethod
    def _persistable_external_media_reference(value: str) -> str:
        """Return a stable opaque reference, never the bearer download URL."""
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"external-document:sha256:{digest}"

    @staticmethod
    def _media_reference_for_response(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized.startswith(("http://", "https://", "external-document:")):
            return None
        return value

    @staticmethod
    def _stored_idempotency_key(workspace_id: UUID, idempotency_key: str) -> str:
        material = f"klm-whatsapp-outbound-v1:{workspace_id}:{idempotency_key}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _outbound_payload_digest(
        *,
        kind: str,
        workspace_id: UUID,
        contact_id: UUID,
        connection_id: UUID,
        sent_by_id: Optional[UUID],
        body: str,
        media_url: Optional[str],
        filename: Optional[str],
        caption: Optional[str],
        structured_payload: dict[str, Any] | None,
    ) -> str:
        canonical = json.dumps(
            {
                "version": 1,
                "kind": kind,
                "workspaceId": str(workspace_id),
                "contactId": str(contact_id),
                "connectionId": str(connection_id),
                "sentById": str(sent_by_id),
                "body": body,
                "mediaUrl": media_url,
                "filename": filename,
                "caption": caption,
                "structuredPayload": structured_payload,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def send_text_direct(
        self,
        *,
        phone: str,
        body: str,
        workspace_id: UUID,
        connection_id: Optional[UUID] = None,
        idempotency_key: str,
    ) -> bool:
        """Send a system-authored text through the durable outbound ledger.

        Older producers called the provider directly and therefore had no row
        that could fence a worker retry after an ambiguous response.  System
        producers now resolve the existing tenant contact and use the same
        WhatsAppMessage state machine as operator sends.  ``sent_by_id`` stays
        null so the audit trail never attributes an automated send to a user.
        """
        normalized_key = self._normalize_outbound_idempotency_key(idempotency_key)
        if not external_delivery_allowed():
            logger.info(
                "whatsapp.direct_send_blocked_by_runtime_gate workspace_id=%s",
                workspace_id,
            )
            return False

        normalized_phone = normalize_phone_number(phone) or phone.strip()
        conditions = [
            WhatsAppContact.workspace_id == workspace_id,
            WhatsAppContact.phone.in_(phone_number_variants(normalized_phone)),
        ]
        if connection_id is not None:
            conditions.append(WhatsAppContact.whatsapp_connection_id == connection_id)
        contact = await self.db.scalar(
            select(WhatsAppContact)
            .where(*conditions)
            .order_by(WhatsAppContact.updated_at.desc(), WhatsAppContact.id.desc())
            .limit(1)
        )
        if contact is None:
            logger.warning(
                "whatsapp.direct_send_contact_not_found workspace_id=%s phone_ref=%s",
                workspace_id,
                opaque_ref(normalized_phone, kind="phone"),
            )
            return False

        try:
            response = await self._send_outbound(
                kind="text",
                contact_id=contact.id,
                explicit_connection_id=connection_id,
                body=body,
                media_url=None,
                delivery_media_url=None,
                media_digest_reference=None,
                filename=None,
                caption=None,
                structured_payload=None,
                requested_idempotency_key=normalized_key,
                sent_by_id=None,
                workspace_id=workspace_id,
            )
            return response.status in {
                WaMessageStatus.sent,
                WaMessageStatus.delivered,
                WaMessageStatus.read,
            }
        except Exception as exc:
            logger.warning(
                "whatsapp.direct_send_failed workspace_id=%s phone_ref=%s "
                "error_type=%s",
                workspace_id,
                opaque_ref(normalized_phone, kind="phone"),
                safe_error_type(exc),
            )
            return False

    async def list_contacts(
        self,
        workspace_id: UUID,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[WhatsAppContactRead]:
        ranked_messages = (
            select(
                WhatsAppMessage.contact_id.label("contact_id"),
                WhatsAppMessage.body.label("body"),
                WhatsAppMessage.timestamp.label("timestamp"),
                func.row_number()
                .over(
                    partition_by=WhatsAppMessage.contact_id,
                    order_by=(
                        WhatsAppMessage.timestamp.desc(),
                        WhatsAppMessage.id.desc(),
                    ),
                )
                .label("message_rank"),
            )
            .join(
                WhatsAppContact,
                WhatsAppContact.id == WhatsAppMessage.contact_id,
            )
            .where(WhatsAppContact.workspace_id == workspace_id)
            .subquery("ranked_whatsapp_messages")
        )
        last_messages = (
            select(
                ranked_messages.c.contact_id,
                ranked_messages.c.body,
                ranked_messages.c.timestamp,
            )
            .where(ranked_messages.c.message_rank == 1)
            .subquery("last_whatsapp_messages")
        )
        unread_messages = (
            select(
                WhatsAppMessage.contact_id.label("contact_id"),
                func.count(WhatsAppMessage.id).label("unread_count"),
            )
            .join(
                WhatsAppContact,
                WhatsAppContact.id == WhatsAppMessage.contact_id,
            )
            .where(
                WhatsAppContact.workspace_id == workspace_id,
                WhatsAppMessage.direction == WaMessageDirection.inbound,
                WhatsAppMessage.status != WaMessageStatus.read,
            )
            .group_by(WhatsAppMessage.contact_id)
            .subquery("unread_whatsapp_messages")
        )
        assigned_user = aliased(User)
        query = (
            select(
                WhatsAppContact,
                assigned_user.name.label("assigned_user_name"),
                last_messages.c.body.label("last_message_body"),
                last_messages.c.timestamp.label("last_message_time"),
                func.coalesce(unread_messages.c.unread_count, 0).label("unread_count"),
            )
            .outerjoin(
                assigned_user,
                assigned_user.id == WhatsAppContact.assigned_user_id,
            )
            .outerjoin(
                last_messages,
                last_messages.c.contact_id == WhatsAppContact.id,
            )
            .outerjoin(
                unread_messages,
                unread_messages.c.contact_id == WhatsAppContact.id,
            )
            .where(WhatsAppContact.workspace_id == workspace_id)
        )
        if search:
            query = query.where(
                WhatsAppContact.name.ilike(f"%{search}%")
                | WhatsAppContact.phone.ilike(f"%{search}%")
            )
        query = query.order_by(WhatsAppContact.updated_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(query)

        items = []
        for (
            contact,
            assigned_user_name,
            last_message_body,
            last_message_time,
            unread_count,
        ) in result.all():
            items.append(
                WhatsAppContactRead(
                    id=contact.id,
                    whatsappConnectionId=contact.whatsapp_connection_id,
                    phone=contact.phone,
                    name=contact.name,
                    avatarUrl=contact.avatar_url,
                    matterId=contact.matter_id,
                    lastSeen=contact.last_seen,
                    isBlocked=contact.is_blocked,
                    unreadCount=int(unread_count or 0),
                    lastMessage=last_message_body,
                    lastMessageTime=last_message_time,
                    assignedUserId=contact.assigned_user_id,
                    assignedUserName=assigned_user_name,
                    queueStatus=contact.queue_status,
                    humanControlState=contact.human_control_state,
                    activeFlowTemplateId=contact.active_flow_template_id,
                    activeFlowLabel=contact.active_flow_label,
                    nextFollowUpAt=contact.next_follow_up_at,
                )
            )
        return items

    async def get_conversation(self, contact_id: UUID, workspace_id: UUID) -> ConversationRead:
        result = await self.db.execute(
            select(WhatsAppContact)
            .options(
                selectinload(WhatsAppContact.messages),
                selectinload(WhatsAppContact.assigned_user),
            )
            .where(WhatsAppContact.id == contact_id, WhatsAppContact.workspace_id == workspace_id)
        )
        contact = result.scalars().first()
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-003", "message": "Contato não encontrado."},
            )

        unread_result = await self.db.execute(
            select(func.count(WhatsAppMessage.id)).where(
                WhatsAppMessage.contact_id == contact.id,
                WhatsAppMessage.direction == WaMessageDirection.inbound,
                WhatsAppMessage.status != WaMessageStatus.read,
            )
        )
        unread_count = unread_result.scalar() or 0
        last_message = contact.messages[-1] if contact.messages else None

        contact_read = WhatsAppContactRead(
            id=contact.id,
            whatsappConnectionId=contact.whatsapp_connection_id,
            phone=contact.phone,
            name=contact.name,
            avatarUrl=contact.avatar_url,
            matterId=contact.matter_id,
            lastSeen=contact.last_seen,
            isBlocked=contact.is_blocked,
            unreadCount=unread_count,
            lastMessage=last_message.body if last_message else None,
            lastMessageTime=last_message.timestamp if last_message else None,
            assignedUserId=contact.assigned_user_id,
            assignedUserName=contact.assigned_user.name if contact.assigned_user else None,
            queueStatus=contact.queue_status,
            humanControlState=contact.human_control_state,
            activeFlowTemplateId=contact.active_flow_template_id,
            activeFlowLabel=contact.active_flow_label,
            nextFollowUpAt=contact.next_follow_up_at,
        )
        messages = [
            WhatsAppMessageRead(
                id=message.id,
                contactId=message.contact_id,
                whatsappConnectionId=message.whatsapp_connection_id,
                wamid=message.wamid,
                direction=message.direction,
                status=message.status,
                body=message.body,
                mediaUrl=self._media_reference_for_response(message.media_url),
                mediaType=message.media_type,
                transcript=message.transcript,
                imageTranscript=message.image_transcript,
                sentById=message.sent_by_id,
                timestamp=message.timestamp,
                deliveredAt=message.delivered_at,
                readAt=message.read_at,
                messageMetadata=message.message_metadata or {},
            )
            for message in sorted(contact.messages, key=lambda item: item.timestamp)
        ]
        return ConversationRead(contact=contact_read, messages=messages)

    async def image_message_to_pdf(self, message_id: UUID, workspace_id: UUID) -> bytes:
        message = await self.db.scalar(
            select(WhatsAppMessage)
            .join(WhatsAppContact, WhatsAppContact.id == WhatsAppMessage.contact_id)
            .options(selectinload(WhatsAppMessage.connection))
            .where(
                WhatsAppMessage.id == message_id,
                WhatsAppContact.workspace_id == workspace_id,
            )
        )
        if message is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-MEDIA-404", "message": "Mensagem não encontrada."},
            )
        if message.media_type != "image" or not message.media_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-MEDIA-001",
                    "message": "A mensagem não contém uma imagem convertível.",
                },
            )

        image_bytes: bytes | None = None
        try:
            local_path = S3Service.local_object_path(message.media_url)
            if local_path.is_file():
                image_bytes = await asyncio.to_thread(local_path.read_bytes)
        except ValueError:
            image_bytes = None

        if image_bytes is None:
            source_media_id = str(
                (message.inbound_pipeline or {}).get("sourceMediaId") or ""
            ).strip()
            if not external_delivery_allowed() or not source_media_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WA-MEDIA-002",
                        "message": "A imagem ainda não está disponível no armazenamento local.",
                    },
                )
            access_token = await self.connection_service.resolve_meta_access_token(message.connection)
            image_bytes, mime_type = await get_transcription_service().download_whatsapp_media(
                source_media_id,
                access_token,
            )
            if not mime_type.lower().startswith("image/"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "WA-MEDIA-003",
                        "message": "A mídia recebida não é uma imagem.",
                    },
                )

        return await get_transcription_service().images_to_pdf([image_bytes])

    async def mark_as_read(self, contact_id: UUID, workspace_id: UUID) -> None:
        result = await self.db.execute(
            select(WhatsAppMessage)
            .join(WhatsAppContact, WhatsAppContact.id == WhatsAppMessage.contact_id)
            .where(
                WhatsAppMessage.contact_id == contact_id,
                WhatsAppContact.workspace_id == workspace_id,
                WhatsAppMessage.direction == WaMessageDirection.inbound,
                WhatsAppMessage.status != WaMessageStatus.read,
            )
        )
        now = datetime.now(timezone.utc)
        for message in result.scalars().all():
            message.status = WaMessageStatus.read
            message.read_at = now
        await self.db.commit()

    async def _resolve_outbound_connection(
        self,
        *,
        workspace_id: UUID,
        explicit_connection_id: Optional[UUID],
        contact: Optional[WhatsAppContact],
    ) -> WhatsAppConnection:
        if explicit_connection_id:
            connection = await self.connection_service.get_connection(
                explicit_connection_id, workspace_id
            )
            if connection.status == WaConnectionStatus.connected:
                return connection
        if contact and contact.whatsapp_connection_id:
            connection = await self.connection_service.get_connection(
                contact.whatsapp_connection_id, workspace_id
            )
            if connection.status == WaConnectionStatus.connected:
                return connection
        connection = await self.connection_service.get_default_connection(
            workspace_id, connected_only=True
        )
        if connection:
            return connection
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "WA-001",
                "message": "Nenhuma conexão WhatsApp elegível está conectada neste workspace.",
            },
        )

    async def _get_contact(self, contact_id: UUID, workspace_id: UUID) -> WhatsAppContact:
        result = await self.db.execute(
            select(WhatsAppContact).where(
                WhatsAppContact.id == contact_id,
                WhatsAppContact.workspace_id == workspace_id,
            )
        )
        contact = result.scalars().first()
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-003", "message": "Contato não encontrado."},
            )
        return contact
