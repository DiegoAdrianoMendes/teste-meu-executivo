"""Celery execution and recovery for contextual restorations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.db.session import WorkerSessionLocal
from app.models.platform_restoration import PlatformRestorationJob
from app.services.platform_restoration_service import PlatformRestorationService
from app.workers.celery_app import celery_app


@celery_app.task(name="platform_restoration_tasks.run")
def run_platform_restoration(job_id: str) -> None:
    async def execute() -> None:
        async with WorkerSessionLocal() as db:
            await PlatformRestorationService(db).run_job(UUID(job_id))
    asyncio.run(execute())


@celery_app.task(name="platform_restoration_tasks.recover")
def recover_platform_restorations() -> int:
    async def recover() -> int:
        async with WorkerSessionLocal() as db:
            cutoff = datetime.now(UTC) - timedelta(minutes=5)
            jobs = list((await db.scalars(select(PlatformRestorationJob).where(
                (PlatformRestorationJob.status == "queued")
                | ((PlatformRestorationJob.status == "running") & (PlatformRestorationJob.updated_at < cutoff))
            ).limit(100))).all())
            for job in jobs:
                if job.status == "running":
                    job.status = "queued"
            await db.commit()
            for job in jobs:
                run_platform_restoration.apply_async(args=[str(job.id)])
            return len(jobs)
    return asyncio.run(recover())
