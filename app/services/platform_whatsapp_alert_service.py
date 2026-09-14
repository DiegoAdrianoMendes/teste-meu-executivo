"""
Canal institucional de WhatsApp da plataforma.
Usado para alertas operacionais globais do KLM Hub.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.models.platform_official_whatsapp import PlatformOfficialWhatsAppRole
from app.services.platform_official_whatsapp_service import PlatformOfficialWhatsAppService
from app.services.whatsapp.meta_cloud_client import MetaCloudClient


class PlatformWhatsAppAlertService:
    def __init__(
        self,
        db: AsyncSession | None = None,
        *,
        client: MetaCloudClient | None = None,
    ) -> None:
        self.db = db
        self.client = client or MetaCloudClient()

    async def is_configured(self) -> bool:
        if self.db is None:
            return False
        connection = await PlatformOfficialWhatsAppService(self.db).get_connection(
            PlatformOfficialWhatsAppRole.notifications,
            connected_only=True,
            active_only=True,
        )
        return connection is not None

    async def send_text(
        self,
        to: str,
        body: str,
        *,
        idempotency_key: str,
        source_id: str | None = None,
        requested_by_user_id: UUID | None = None,
        commit: bool = True,
    ) -> bool:
        # Compatibility-only knob for callers that batch their own ORM work.
        # Official delivery always uses its own durable transaction and never
        # commits (or rolls back) the caller's session.
        del commit
        if self.db is None:
            logger.warning(
                "platform_whatsapp_alert.outbox_unavailable",
                recipient_ref=opaque_ref(to, kind="phone"),
            )
            return False
        service = PlatformOfficialWhatsAppService(self.db, meta_client=self.client)
        try:
            success = await service.send_text(
                role=PlatformOfficialWhatsAppRole.notifications,
                to=to,
                body=body,
                idempotency_key=idempotency_key,
                requested_by_user_id=requested_by_user_id,
                source_type="operational_alert",
                source_id=source_id,
            )
            if success:
                logger.info(
                    "platform_whatsapp_alert.sent",
                    recipient_ref=opaque_ref(to, kind="phone"),
                    channel="notifications",
                )
            return success
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            logger.warning(
                "platform_whatsapp_alert.delivery_not_conclusive",
                recipient_ref=opaque_ref(to, kind="phone"),
                code=detail.get("code"),
                operation_id=detail.get("operationId"),
                error_type=safe_error_type(exc),
            )
            return False
