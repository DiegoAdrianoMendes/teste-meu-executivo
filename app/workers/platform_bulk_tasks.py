"""Execução e recuperação dos lotes persistidos do Blackbox."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.core.cache import invalidate_workspace_cache
from app.db.session import WorkerSessionLocal
from app.models.platform_operations import PlatformBulkJob, PlatformBulkPreview
from app.services.platform_bulk_service import PlatformBulkService
from app.workers.celery_app import celery_app

BULK_CHUNK_SIZE = 25
ORPHAN_LEASE = timedelta(minutes=5)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _async_run_job(job_id: UUID | str, *, session_factory=None) -> str:
    session_maker = session_factory or WorkerSessionLocal
    async with session_maker() as db:
        service = PlatformBulkService(db)
        job = await service.run_job(
            UUID(str(job_id)),
            max_items=BULK_CHUNK_SIZE,
        )
        await db.commit()
        status = job.status
        # S22: a validade da membership fica em cache por 10 min; sem invalidar
        # depois do commit, um workspace suspenso continua aceitando tokens já
        # emitidos. Pós-commit para uma requisição concorrente não repovoar o
        # cache com a linha ainda ativa.
        for workspace_id in service.workspaces_to_invalidate:
            await invalidate_workspace_cache(workspace_id)
    if status == "running":
        run_platform_bulk_job.apply_async(args=[str(job_id)])
    return status


async def _async_build_preview(preview_id: UUID | str, *, session_factory=None) -> str:
    session_maker = session_factory or WorkerSessionLocal
    async with session_maker() as db:
        preview = await PlatformBulkService(db).build_deferred_preview(UUID(str(preview_id)))
        await db.commit()
        return preview.status


async def _async_recover_jobs(*, session_factory=None, limit: int = 100) -> int:
    session_maker = session_factory or WorkerSessionLocal
    now = datetime.now(UTC)
    stale_before = now - ORPHAN_LEASE
    async with session_maker() as db:
        ids = list(
            (
                await db.scalars(
                    select(PlatformBulkJob.id)
                    .where(
                        PlatformBulkJob.status.in_(("queued", "running", "cancelling")),
                        (
                            (PlatformBulkJob.heartbeat_at.is_(None))
                            | (PlatformBulkJob.heartbeat_at < stale_before)
                        ),
                    )
                    .order_by(PlatformBulkJob.created_at.asc())
                    .limit(limit)
                )
            ).all()
        )
        preview_ids = list(
            (
                await db.scalars(
                    select(PlatformBulkPreview.id)
                    .where(
                        PlatformBulkPreview.status == "building",
                        PlatformBulkPreview.created_at < now - timedelta(seconds=10),
                    )
                    .order_by(PlatformBulkPreview.created_at.asc())
                    .limit(limit)
                )
            ).all()
        )
    for job_id in ids:
        run_platform_bulk_job.apply_async(args=[str(job_id)])
    for preview_id in preview_ids:
        build_platform_bulk_preview.apply_async(args=[str(preview_id)])
    return len(ids) + len(preview_ids)


@celery_app.task(name="platform_bulk_tasks.run_job")
def run_platform_bulk_job(job_id: str) -> str:
    return _run_async(_async_run_job(job_id))


@celery_app.task(name="platform_bulk_tasks.build_preview")
def build_platform_bulk_preview(preview_id: str) -> str:
    return _run_async(_async_build_preview(preview_id))


@celery_app.task(name="platform_bulk_tasks.recover")
def recover_platform_bulk_jobs() -> int:
    return _run_async(_async_recover_jobs())


__all__ = [
    "_async_recover_jobs",
    "_async_build_preview",
    "_async_run_job",
    "build_platform_bulk_preview",
    "recover_platform_bulk_jobs",
    "run_platform_bulk_job",
]
