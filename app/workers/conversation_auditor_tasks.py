"""Periodic bounded scans for the native independent conversation auditor."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal
from app.models.workspace import Workspace, WorkspaceStatus
from app.services.conversation_auditor_service import ConversationAuditorService
from app.workers.celery_app import celery_app

MAX_WORKSPACES_PER_RECOVERY = 1_000
MAX_THREADS_PER_SCAN = 250


def _run_async(coroutine):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


async def _scan_workspace(
    workspace_id: UUID,
    *,
    session_factory=WorkerSessionLocal,
) -> dict[str, int | float | str]:
    async with session_factory() as db:
        dashboard = await ConversationAuditorService(db).scan_workspace(
            workspace_id,
            limit=MAX_THREADS_PER_SCAN,
        )
        await db.commit()
    return {
        "workspaceId": str(workspace_id),
        "inspectedThreads": dashboard.inspected_threads,
        "openFindings": dashboard.open_count,
        "criticalFindings": dashboard.critical_count,
        "coveragePercent": dashboard.coverage_percent,
    }


@celery_app.task(name="conversation_auditor_tasks.scan_workspace")
def scan_conversation_workspace(workspace_id: str) -> dict[str, int | float | str]:
    try:
        return _run_async(_scan_workspace(UUID(str(workspace_id))))
    except Exception as error:
        logger.warning(
            "conversation_auditor_scan_failed",
            workspace_id=str(workspace_id),
            error_type=safe_error_type(error),
        )
        raise


async def _recover_active_workspaces(*, session_factory=WorkerSessionLocal) -> int:
    async with session_factory() as db:
        workspace_ids = list(
            (
                await db.scalars(
                    select(Workspace.id)
                    .where(
                        Workspace.status == WorkspaceStatus.active,
                        Workspace.is_active.is_(True),
                    )
                    .order_by(Workspace.created_at, Workspace.id)
                    .limit(MAX_WORKSPACES_PER_RECOVERY)
                )
            ).all()
        )
    for workspace_id in workspace_ids:
        scan_conversation_workspace.delay(str(workspace_id))
    return len(workspace_ids)


@celery_app.task(name="conversation_auditor_tasks.recover_active_workspaces")
def recover_conversation_auditor_workspaces() -> int:
    return _run_async(_recover_active_workspaces())


__all__ = [
    "MAX_THREADS_PER_SCAN",
    "MAX_WORKSPACES_PER_RECOVERY",
    "recover_conversation_auditor_workspaces",
    "scan_conversation_workspace",
]
