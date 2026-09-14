"""Official WhatsApp Cloud API adapter for the canonical delivery worker."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import OmnichannelMessage
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
)
from app.services.omnichannel_delivery import (
    ScheduledDispatchError,
    ScheduledDispatchReceipt,
    ScheduledDispatchRequest,
)
from app.services.whatsapp.cloud_payload import (
    WhatsAppPayloadError,
    normalize_internal_payload,
    render_cloud_api_message,
)
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.provider_media_receipt_service import (
    ProviderMediaReceiptError,
    ProviderMediaReceiptService,
)


class MetaCloudScheduledDispatcher:
    # The Graph messages endpoint accepts a correlation value, but does not
    # guarantee that retrying the POST with the same value is deduplicated.
    supports_idempotency = False

    def __init__(
        self,
        db: AsyncSession,
        connection: OmnichannelChannelConnection,
        *,
        meta_client: MetaCloudClient | None = None,
    ) -> None:
        self.db = db
        self.connection = connection
        self.meta_client = meta_client or MetaCloudClient()
        self.connection_service = WhatsAppConnectionService(
            db,
            meta_client=self.meta_client,
        )

    async def _legacy_connection(self) -> WhatsAppConnection:
        if self.connection.provider != "meta_cloud" or self.connection.channel != "whatsapp":
            raise ScheduledDispatchError(
                "A rota não pertence ao WhatsApp oficial.",
                code="whatsapp_route_mismatch",
                retryable=False,
            )
        if self.connection.transport_mode not in {"cloud_api", "official_api", "coexistence"}:
            raise ScheduledDispatchError(
                "O modo de transporte não usa a Cloud API oficial.",
                code="whatsapp_transport_unsupported",
                retryable=False,
            )
        configuration = self.connection.configuration or {}
        reference = str(self.connection.credential_reference or "")
        legacy_id = str(configuration.get("legacyConnectionId") or "")
        if reference.startswith("whatsapp-connection://"):
            legacy_id = reference.removeprefix("whatsapp-connection://")
        filters = []
        if legacy_id:
            try:
                filters.append(WhatsAppConnection.id == UUID(legacy_id))
            except ValueError:
                raise ScheduledDispatchError(
                    "A referência da credencial WhatsApp é inválida.",
                    code="whatsapp_credential_reference_invalid",
                    retryable=False,
                ) from None
        phone_number_id = str(configuration.get("phoneNumberId") or "").strip()
        if phone_number_id:
            filters.append(WhatsAppConnection.phone_number_id == phone_number_id)
        if not filters:
            raise ScheduledDispatchError(
                "A conexão nativa ainda não foi vinculada à credencial oficial.",
                code="whatsapp_credential_not_linked",
                retryable=False,
            )
        legacy = await self.db.scalar(
            select(WhatsAppConnection).where(
                WhatsAppConnection.workspace_id == self.connection.workspace_id,
                or_(*filters),
            )
        )
        if legacy is None:
            raise ScheduledDispatchError(
                "A credencial oficial vinculada não foi encontrada neste workspace.",
                code="whatsapp_credential_not_found",
                retryable=False,
            )
        if legacy.connection_type == ConnectionType.QR_CODE:
            raise ScheduledDispatchError(
                "A rota selecionada não é uma conexão oficial da Meta.",
                code="whatsapp_official_connection_required",
                retryable=False,
            )
        if legacy.status != WaConnectionStatus.connected or not legacy.phone_number_id:
            raise ScheduledDispatchError(
                "O número oficial está desconectado.",
                code="whatsapp_connection_inactive",
                retryable=True,
            )
        if legacy.transport_mode != self.connection.transport_mode and not (
            legacy.transport_mode == "official_api" and self.connection.transport_mode == "cloud_api"
        ):
            raise ScheduledDispatchError(
                "A projeção nativa e a credencial divergem sobre o modo de transporte.",
                code="whatsapp_transport_mismatch",
                retryable=False,
            )
        return legacy

    @staticmethod
    def _recipient(request: ScheduledDispatchRequest) -> str:
        value = request.address or request.external_user_id
        normalized = "".join(character for character in str(value) if character.isdigit())
        if not 8 <= len(normalized) <= 15:
            raise ScheduledDispatchError(
                "O destinatário do WhatsApp é inválido.",
                code="whatsapp_recipient_invalid",
                retryable=False,
            )
        return normalized

    @staticmethod
    def _payload(request: ScheduledDispatchRequest) -> dict:
        try:
            return normalize_internal_payload(
                request.message_payload,
                body=request.body,
                template_id=request.template_id,
                attachments=request.attachments,
            )
        except WhatsAppPayloadError as error:
            raise ScheduledDispatchError(
                str(error),
                code="whatsapp_payload_invalid",
                retryable=False,
            ) from error

    @staticmethod
    def _local_reply_message_id(request: ScheduledDispatchRequest) -> UUID | None:
        """Read the KLM collaboration envelope without forwarding it to Meta."""
        payload = request.message_payload or {}
        metadata = payload.get("messageMetadata")
        if not isinstance(metadata, dict):
            metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = payload
        collaboration = metadata.get("collaboration")
        if not isinstance(collaboration, dict):
            collaboration = payload.get("collaboration")
        if not isinstance(collaboration, dict):
            return None
        raw_message_id = collaboration.get("replyToMessageId")
        if raw_message_id in {None, ""}:
            return None
        try:
            return UUID(str(raw_message_id))
        except (TypeError, ValueError):
            raise ScheduledDispatchError(
                "A referência da mensagem respondida é inválida.",
                code="whatsapp_reply_target_invalid",
                retryable=False,
            ) from None

    async def _resolve_reply_provider_message_id(
        self,
        request: ScheduledDispatchRequest,
    ) -> str | None:
        reply_message_id = self._local_reply_message_id(request)
        if reply_message_id is None:
            return None

        canonical = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == request.workspace_id,
                OmnichannelMessage.id == request.canonical_message_id,
            )
        )
        if canonical is None:
            raise ScheduledDispatchError(
                "A mensagem canônica da resposta não foi encontrada.",
                code="canonical_message_missing",
                retryable=False,
            )
        target = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == request.workspace_id,
                OmnichannelMessage.id == reply_message_id,
                OmnichannelMessage.thread_id == canonical.thread_id,
                OmnichannelMessage.channel == "whatsapp",
            )
        )
        if target is None:
            raise ScheduledDispatchError(
                "A mensagem respondida não pertence a esta conversa WhatsApp.",
                code="whatsapp_reply_target_not_found",
                retryable=False,
            )
        if target.connection_id not in {None, request.connection_id}:
            raise ScheduledDispatchError(
                "A mensagem respondida pertence a outra conexão WhatsApp.",
                code="whatsapp_reply_connection_mismatch",
                retryable=False,
            )
        provider_message_id = str(target.external_message_id or "").strip()
        if not provider_message_id:
            raise ScheduledDispatchError(
                "A mensagem respondida ainda não possui confirmação do provedor.",
                code="whatsapp_reply_target_not_delivered",
                retryable=False,
            )
        return provider_message_id

    async def dispatch(self, request: ScheduledDispatchRequest) -> ScheduledDispatchReceipt:
        payload = self._payload(request)
        if (
            payload["kind"] != "template"
            and (
                request.service_window_expires_at is None
                or request.service_window_expires_at <= datetime.now(UTC)
            )
        ):
            raise ScheduledDispatchError(
                "Fora da janela de atendimento, o WhatsApp oficial exige um modelo aprovado.",
                code="whatsapp_template_required",
                retryable=False,
            )
        legacy = await self._legacy_connection()
        try:
            verified_media_receipts = await ProviderMediaReceiptService(
                self.db
            ).verify_payload(
                workspace_id=request.workspace_id,
                connection_id=self.connection.id,
                provider=self.connection.provider,
                payload=payload,
            )
        except ProviderMediaReceiptError as error:
            raise ScheduledDispatchError(
                str(error),
                code=error.code,
                retryable=False,
            ) from error
        access_token = await self.connection_service.resolve_meta_access_token(legacy)
        correlation = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()
        reply_to_provider_message_id = await self._resolve_reply_provider_message_id(request)
        try:
            response = await self.meta_client.send_message_payload(
                phone_number_id=legacy.phone_number_id or "",
                access_token=access_token,
                to=self._recipient(request),
                message=render_cloud_api_message(
                    payload,
                    verified_provider_media_receipt_ids=verified_media_receipts,
                    reply_to_provider_message_id=reply_to_provider_message_id,
                ),
                opaque_callback_data=f"klm:{correlation}",
            )
        except WhatsAppProviderError as error:
            raise ScheduledDispatchError(
                error.message,
                code=error.code,
                retryable=not error.request_started or error.status_code >= 500,
                outcome_uncertain=error.delivery_uncertain,
            ) from error
        external_message_id = str((response.get("messages") or [{}])[0].get("id") or "")
        if not external_message_id:
            raise ScheduledDispatchError(
                "A Meta aceitou a chamada sem retornar o ID da mensagem.",
                code="whatsapp_missing_wamid",
                retryable=False,
                outcome_uncertain=True,
            )
        return ScheduledDispatchReceipt(
            provider="meta_cloud",
            external_message_id=external_message_id,
            provider_status="accepted",
            payload={
                "transportMode": self.connection.transport_mode,
                "originSurface": "cloud_api",
                "messageKind": payload["kind"],
                "correlation": correlation,
            },
            pricing_category="template" if payload["kind"] == "template" else "service",
        )
