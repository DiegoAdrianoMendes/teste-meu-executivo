import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select

from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.models.finance_dimensional import FinanceExportJob
from app.models.workspace import Workspace
from app.services.finance_export_service import (
    FinanceExportService,
    finance_export_audit_snapshot,
    record_finance_export_event,
    record_finance_export_platform_lifecycle,
)
from app.services.platform_export_service import PlatformExportService
from app.workers.celery_app import celery_app


@celery_app.task(name="finance_export_tasks.run")
def run_finance_export(job_id: str, workspace_id: str):
    async def go():
        async with WorkerSessionLocal() as db:
            await set_workspace_context(db, UUID(workspace_id))
            job = await db.scalar(
                select(FinanceExportJob)
                .where(FinanceExportJob.id == UUID(job_id))
                .with_for_update()
            )
            if job:
                await FinanceExportService(db).run(job)

    asyncio.run(go())


@celery_app.task(name="finance_export_tasks.expire")
def expire_finance_exports():
    async def go():
        async with WorkerSessionLocal() as discovery:
            workspace_ids = list(
                (await discovery.scalars(select(Workspace.id).order_by(Workspace.id))).all()
            )
        expired = 0
        for workspace_id in workspace_ids:
            async with WorkerSessionLocal() as db:
                await set_workspace_context(db, workspace_id)
                rows = (
                    await db.scalars(
                        select(FinanceExportJob)
                        .where(
                            FinanceExportJob.expires_at <= datetime.now(UTC),
                            FinanceExportJob.status == "completed",
                        )
                        .limit(200)
                    )
                ).all()
                for job in rows:
                    if job.storage_path:
                        try:
                            await PlatformExportService(db).delete_artifact(job.storage_path)
                        except Exception:
                            continue
                    before_snapshot = finance_export_audit_snapshot(job)
                    before = str(getattr(job.status, "value", job.status))
                    job.status = "expired"
                    job.storage_path = None
                    record_finance_export_event(
                        db, job, "finance_export_expired", before_status=before
                    )
                    await record_finance_export_platform_lifecycle(
                        db, job, action="expired", before=before_snapshot
                    )
                    expired += 1
                await db.commit()
        return expired

    return asyncio.run(go())


@celery_app.task(name="finance_export_tasks.recover")
def recover_finance_exports() -> int:
    """Re-dispatch durable queued intents after a post-commit broker failure.

    `FinanceExportJob` is the transactional outbox record: it is committed
    with the request and remains queued until a worker claims it. This task
    intentionally uses no transient in-memory wakeup as the source of truth.
    """

    async def recover() -> int:
        async with WorkerSessionLocal() as discovery:
            workspace_ids = list(
                (await discovery.scalars(select(Workspace.id).order_by(Workspace.id))).all()
            )
        queued: list[tuple[UUID, UUID]] = []
        cutoff = datetime.now(UTC) - timedelta(minutes=5)
        for workspace_id in workspace_ids:
            async with WorkerSessionLocal() as db:
                await set_workspace_context(db, workspace_id)
                jobs = list(
                    (
                        await db.scalars(
                            select(FinanceExportJob)
                            .where(
                                or_(
                                    FinanceExportJob.status == "queued",
                                    (FinanceExportJob.status == "running")
                                    & (FinanceExportJob.updated_at < cutoff),
                                )
                            )
                            .order_by(FinanceExportJob.created_at)
                            .limit(100)
                        )
                    ).all()
                )
                for job in jobs:
                    if job.status == "running":
                        # The lost worker never produced a terminal receipt;
                        # requeue the durable intent and let the normal run
                        # produce a fresh started/completed lifecycle trail.
                        before_snapshot = finance_export_audit_snapshot(job)
                        job.status = "queued"
                        await record_finance_export_platform_lifecycle(
                            db, job, action="requeued", before=before_snapshot
                        )
                    queued.append((job.id, workspace_id))
                await db.commit()
        for job_id, workspace_id in queued:
            run_finance_export.apply_async(args=[str(job_id), str(workspace_id)], retry=False)
        return len(queued)

    return asyncio.run(recover())
