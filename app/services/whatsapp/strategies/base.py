from __future__ import annotations

from typing import Any, Protocol

from app.models.whatsapp import WhatsAppConnection
from app.services.whatsapp.types import WhatsAppSendResult


class WhatsAppSendStrategy(Protocol):
    async def send_text(self, connection: WhatsAppConnection, to: str, body: str) -> WhatsAppSendResult:
        ...

    async def send_document(
        self,
        connection: WhatsAppConnection,
        to: str,
        url: str,
        filename: str,
        caption: str | None = None,
    ) -> WhatsAppSendResult:
        ...

    async def send_structured(
        self,
        connection: WhatsAppConnection,
        to: str,
        payload: dict[str, Any],
        *,
        opaque_callback_data: str | None = None,
        verified_provider_media_receipt_ids: set[str] | None = None,
    ) -> WhatsAppSendResult:
        ...
