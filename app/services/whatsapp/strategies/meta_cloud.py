from __future__ import annotations

from typing import Any

from app.models.whatsapp import WhatsAppConnection
from app.services.whatsapp.cloud_payload import render_cloud_api_message
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.types import WhatsAppSendResult


class MetaCloudStrategy:
    def __init__(self, meta_client: MetaCloudClient, access_token_resolver) -> None:
        self._meta_client = meta_client
        self._access_token_resolver = access_token_resolver

    async def send_text(self, connection: WhatsAppConnection, to: str, body: str) -> WhatsAppSendResult:
        access_token = self._access_token_resolver(connection)
        response = await self._meta_client.send_text_message(
            phone_number_id=connection.phone_number_id or "",
            access_token=access_token,
            to=to,
            body=body,
        )
        wamid = response.get("messages", [{}])[0].get("id")
        return WhatsAppSendResult(wamid=wamid, raw_status="sent")

    async def send_document(
        self,
        connection: WhatsAppConnection,
        to: str,
        url: str,
        filename: str,
        caption: str | None = None,
    ) -> WhatsAppSendResult:
        access_token = self._access_token_resolver(connection)
        response = await self._meta_client.send_document_message(
            phone_number_id=connection.phone_number_id or "",
            access_token=access_token,
            to=to,
            link=url,
            filename=filename,
            caption=caption,
        )
        wamid = response.get("messages", [{}])[0].get("id")
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
        access_token = self._access_token_resolver(connection)
        response = await self._meta_client.send_message_payload(
            phone_number_id=connection.phone_number_id or "",
            access_token=access_token,
            to=to,
            message=render_cloud_api_message(
                payload,
                verified_provider_media_receipt_ids=(
                    verified_provider_media_receipt_ids
                ),
            ),
            opaque_callback_data=opaque_callback_data,
        )
        wamid = response.get("messages", [{}])[0].get("id")
        return WhatsAppSendResult(wamid=wamid, raw_status="sent")
