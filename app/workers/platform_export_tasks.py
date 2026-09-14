"""Celery execution and recovery for private Blackbox exports."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select

from app.db.session import WorkerSessionLocal
from app.models.platform_export import PlatformExportJob
from app.services.platform_export_service import PlatformExportError, PlatformExportService
from app.workers.celery_app import celery_app


@celery_app.task(name="platform_export_tasks.run")
def run_platform_export(job_id: str) -> None:
    async def execute() -> None:
        async with WorkerSessionLocal() as db:
            await PlatformExportService(db).run_job(UUID(job_id))

    asyncio.run(execute())


@celery_app.task(name="platform_export_tasks.recover")
def recover_platform_exports() -> int:
    async def recover() -> int:
        async with WorkerSessionLocal() as db:
            service = PlatformExportService(db)
            cutoff = datetime.now(UTC) - timedelta(minutes=5)
            jobs = list((await db.scalars(
                select(PlatformExportJob).where(
                    or_(
                        PlatformExportJob.status == "queued",
                        (PlatformExportJob.status == "running")
                        & (PlatformExportJob.updated_at < cutoff),
                    )
                ).limit(100)
            )).all())
            for job in jobs:
                if job.status == "running":
                    job.status = "queued"
            await db.commit()
            for job in jobs:
                run_platform_export.apply_async(args=[str(job.id)])
            expired = list((await db.scalars(
                select(PlatformExportJob).where(
                    PlatformExportJob.expires_at.is_not(None),
                    PlatformExportJob.expires_at <= datetime.now(UTC),
                    PlatformExportJob.status == "completed",
                ).limit(100)
            )).all())
            for job in expired:
                if job.storage_path:
                    try:
                        await service.delete_artifact(job.storage_path)
                    except PlatformExportError:
                        # A autorização já expira no banco; o lifecycle/retry
                        # posterior pode concluir a remoção física privada.
                        pass
                job.status = "expired"
            await db.commit()
            return len(jobs)

    return asyncio.run(recover())
