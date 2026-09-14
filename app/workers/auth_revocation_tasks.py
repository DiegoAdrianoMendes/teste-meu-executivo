"""Periodic recovery for durable authentication revocation intents."""

from __future__ import annotations

import asyncio

from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.services.auth_revocation_service import AuthRevocationService
from app.services.identity_outbox_service import LocalIdentityOutboxService
from app.workers.celery_app import celery_app

MAX_REVOCATIONS_PER_RUN = 100
MAX_OUTBOX_MESSAGES_PER_RUN = 500
MAX_IDENTITY_DELIVERIES_PER_RUN = 25


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="auth_revocation_tasks.retry_pending")
def retry_pending_auth_revocations() -> int:
    return _run_async(_async_retry_pending_auth_revocations())


async def _async_retry_pending_auth_revocations(
    *,
    session_factory=None,
    service_factory=AuthRevocationService,
) -> int:
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        return await service_factory(db).retry_pending(limit=MAX_REVOCATIONS_PER_RUN)


@celery_app.task(name="auth_revocation_tasks.purge_expired_outbox")
def purge_expired_identity_outbox() -> int:
    return _run_async(_async_purge_expired_identity_outbox())


async def _async_purge_expired_identity_outbox(
    *,
    session_factory=None,
    service_factory=LocalIdentityOutboxService,
) -> int:
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        removed = await service_factory(db).purge_expired(
            limit=MAX_OUTBOX_MESSAGES_PER_RUN
        )
        await db.commit()
        return removed


@celery_app.task(name="auth_revocation_tasks.deliver_identity_outbox")
def deliver_identity_outbox() -> dict[str, int]:
    return _run_async(_async_deliver_identity_outbox())


async def _async_deliver_identity_outbox(
    *,
    session_factory=None,
    service_factory=LocalIdentityOutboxService,
) -> dict[str, int]:
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        result = await service_factory(db).deliver_pending(
            limit=MAX_IDENTITY_DELIVERIES_PER_RUN
        )
        await db.commit()
        return result
