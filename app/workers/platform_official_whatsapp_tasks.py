"""Recovery worker for the SaaS-owned official WhatsApp outbox.

Only rows that have never crossed the provider-call boundary are dispatched.
Rows left in ``provider_call_started`` are fenced as uncertain so a worker
restart can never duplicate a message that Meta may already have accepted.
"""

from __future__ import annotations

import asyncio

from app.db.session import WorkerSessionLocal
from app.services.platform_official_whatsapp_service import (
    PlatformOfficialWhatsAppService,
)
from app.workers.celery_app import celery_app


async def _async_recover_official_whatsapp_deliveries(
    *,
    session_factory=WorkerSessionLocal,
    limit: int = 50,
) -> dict[str, int]:
    async with session_factory() as db:
        service = PlatformOfficialWhatsAppService(db)
        fenced = await service.mark_stale_started_uncertain()
        sent = await service.dispatch_prepared(limit=limit)
        return {"fenced": fenced, "sent": sent}


@celery_app.task(name="platform_official_whatsapp_tasks.recover")
def recover_platform_official_whatsapp_deliveries() -> dict[str, int]:
    return asyncio.run(_async_recover_official_whatsapp_deliveries())
