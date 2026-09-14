from __future__ import annotations

from typing import Any

from app.models.whatsapp import WhatsAppConnection
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.qr_gateway_client import QrGatewayClient
from app.services.whatsapp.types import WhatsAppSendResult


class QrGatewayStrategy:
    def __init__(self, qr_client: QrGatewayClient, instance_token_resolver) -> None:
        self._qr_client = qr_client
        self._instance_token_resolver = instance_token_resolver

    async def send_text(self, connection: WhatsAppConnection, to: str, body: str) -> WhatsAppSendResult:
        instance_token = self._instance_token_resolver(connection)
        response = await self._qr_client.send_text_message(
            connection=connection,
            instance_token=instance_token,
            to=to,
            body=body,
        )
        wamid = response.get("wamid") or response.get("messageId") or response.get("id")
        return WhatsAppSendResult(wamid=wamid, raw_status="sent")

    async def send_document(
        self,
        connection: WhatsAppConnection,
        to: str,
        url: str,
        filename: str,
        caption: str | None = None,
    ) -> WhatsAppSendResult:
        instance_token = self._instance_token_resolver(connection)
        response = await self._qr_client.send_document_message(
            connection=connection,
            instance_token=instance_token,
            to=to,
            url=url,
            filename=filename,
            caption=caption,
        )
        wamid = response.get("wamid") or response.get("messageId") or response.get("id")
        return WhatsAppSendResult(wamid=wamid, raw_status="sent")

    async def send_structured(
        self,
        connection: WhatsAppConnection,
        to: str,
        payload: dict[str, Any],
        *,
        opaque_callback_data: str | None = None,
        verified_provider_media_receipt_ids: set[str] | None = None,
    ) -> WhatsAppSendResult:
        raise WhatsAppProviderError(
            code="WA-OFFICIAL-ONLY",
            message="Mensagens interativas exigem a conexão oficial da Meta.",
            status_code=422,
            request_started=False,
        )
