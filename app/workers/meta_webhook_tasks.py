"""Durable workers for Meta webhook subscription reconciliation and receipt retry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.core.klm_runtime import external_delivery_allowed
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.models.meta_webhook_operations import MetaWebhookSubscriptionJob
from app.services.whatsapp.webhooks.subscription_service import MetaWebhookControlPlaneService
from app.workers.celery_app import celery_app


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _reconcile(workspace_id: UUID, job_id: UUID) -> dict:
    async with WorkerSessionLocal() as db:
        await set_workspace_context(db, workspace_id)
        service = MetaWebhookControlPlaneService(db)
        if not external_delivery_allowed():
            job = await db.scalar(
                select(MetaWebhookSubscriptionJob)
                .where(
                    MetaWebhookSubscriptionJob.workspace_id == workspace_id,
                    MetaWebhookSubscriptionJob.id == job_id,
                )
                .with_for_update()
            )
            if job is None:
                return {"status": "not_found"}
            job.status = "failed"
            job.sanitized_error_code = "KLM-EXT-001"
            job.completed_at = datetime.now(UTC)
            db.add(job)
            await db.commit()
            return service.serialize_job(job)
        try:
            job = await service.run_reconciliation_job(workspace_id=workspace_id, job_id=job_id)
            await db.commit()
            return service.serialize_job(job)
        except Exception as exc:
            await db.rollback()
            job = await db.scalar(
                select(MetaWebhookSubscriptionJob)
                .where(
                    MetaWebhookSubscriptionJob.workspace_id == workspace_id,
                    MetaWebhookSubscriptionJob.id == job_id,
                )
                .with_for_update()
            )
            if job is not None:
                job.status = "failed"
                job.sanitized_error_code = type(exc).__name__[:120]
                job.completed_at = datetime.now(UTC)
                db.add(job)
                await db.commit()
            raise


async def _retry_receipt(workspace_id: UUID, receipt_id: UUID) -> dict:
    async with WorkerSessionLocal() as db:
        await set_workspace_context(db, workspace_id)
        service = MetaWebhookControlPlaneService(db)
        receipt = await service.retry_receipt(workspace_id=workspace_id, receipt_id=receipt_id)
        await db.commit()
        return service.serialize_receipt(receipt)


async def _finish_reconciliation_retry_exhausted(workspace_id: UUID, job_id: UUID) -> dict:
    async with WorkerSessionLocal() as db:
        await set_workspace_context(db, workspace_id)
        job = await db.scalar(
            select(MetaWebhookSubscriptionJob)
            .where(
                MetaWebhookSubscriptionJob.workspace_id == workspace_id,
                MetaWebhookSubscriptionJob.id == job_id,
            )
            .with_for_update()
        )
        if job is None:
            return {"status": "not_found"}
        job.status = "completed_with_errors"
        job.sanitized_error_code = "META_WEBHOOK_PROVIDER_RETRY_EXHAUSTED"
        job.completed_at = datetime.now(UTC)
        db.add(job)
        await db.commit()
        return MetaWebhookControlPlaneService.serialize_job(job)


@celery_app.task(
    bind=True,
    name="meta_webhook_tasks.reconcile_subscriptions",
    # Initial execution plus two retries: three provider attempts total.
    max_retries=2,
)
def reconcile_meta_webhook_subscriptions(self, workspace_id: str, job_id: str):
    try:
        result = _run(_reconcile(UUID(workspace_id), UUID(job_id)))
    except Exception as exc:
        countdown = min(300, 30 * (2**self.request.retries))
        raise self.retry(exc=exc, countdown=countdown)
    if result.get("status") != "retrying":
        return result
    if self.request.retries >= self.max_retries:
        return _run(_finish_reconciliation_retry_exhausted(UUID(workspace_id), UUID(job_id)))
    countdown = min(300, 30 * (2**self.request.retries))
    raise self.retry(countdown=countdown)


@celery_app.task(name="meta_webhook_tasks.retry_receipt", max_retries=0)
def retry_meta_webhook_receipt(workspace_id: str, receipt_id: str):
    return _run(_retry_receipt(UUID(workspace_id), UUID(receipt_id)))
