"""Canonical stock-capacity enforcement for persisted WhatsApp contacts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entitlement import EntitlementKey
from app.models.whatsapp import WhatsAppContact
from app.services.entitlement_service import (
    CurrentStockSnapshot,
    EntitlementEnforcementService,
)


class WhatsAppContactCapacityService:
    """Serialize and count contact rows before a new canonical contact is added."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enforce_new_contact(
        self,
        *,
        workspace_id: UUID,
        requested_quantity: int = 1,
    ) -> CurrentStockSnapshot:
        async def load_current_quantity() -> int:
            value = await self.db.scalar(
                select(func.count(WhatsAppContact.id)).where(
                    WhatsAppContact.workspace_id == workspace_id
                )
            )
            return int(value or 0)

        return await EntitlementEnforcementService(self.db).enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.contacts,
            load_current_quantity=load_current_quantity,
            requested_quantity=requested_quantity,
        )


__all__ = ["WhatsAppContactCapacityService"]
