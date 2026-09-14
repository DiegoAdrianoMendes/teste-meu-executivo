"""Celery: provisionamento assíncrono e idempotente da biblioteca UTILITY."""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.qr_gateway_client import QrGatewayClient
from app.services.whatsapp.secrets import WhatsAppSecretCipher
from app.services.whatsapp.utility_template_provisioning_service import (
    UtilityTemplateProvisioningService,
)
from app.workers.celery_app import celery_app


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _provision(
    workspace_id: UUID,
    connection_id: UUID,
    *,
    only_keys: set[str] | None = None,
    retry_rejected: bool = False,
) -> dict[str, int | bool]:
    async with WorkerSessionLocal() as db:
        await set_workspace_context(db, workspace_id)
        meta_client = MetaCloudClient()
        connection_service = WhatsAppConnectionService(
            db,
            cipher=WhatsAppSecretCipher(),
            meta_client=meta_client,
            qr_client=QrGatewayClient(),
        )
        return await UtilityTemplateProvisioningService(
            db,
            connection_service=connection_service,
            meta_client=meta_client,
        ).provision(
            workspace_id=workspace_id,
            connection_id=connection_id,
            only_keys=only_keys,
            retry_rejected=retry_rejected,
        )


@celery_app.task(
    bind=True,
    name="whatsapp_utility_template_tasks.provision",
    max_retries=4,
)
def provision_utility_templates(
    self,
    workspace_id: str,
    connection_id: str,
    only_keys: list[str] | None = None,
    retry_rejected: bool = False,
):
    result = _run(
        _provision(
            UUID(workspace_id),
            UUID(connection_id),
            only_keys=set(only_keys) if only_keys else None,
            retry_rejected=retry_rejected,
        )
    )
    if result.get("retryable"):
        raise self.retry(countdown=min(300, 5 * (2 ** self.request.retries)))
    return result
