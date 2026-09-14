"""Restauração periódica das continuidades temporárias vencidas."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.db.platform_staff_reassignment import (
    set_platform_staff_reassignment_context,
)
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.models.workspace import Workspace
from app.services.staff_reassignment_service import StaffReassignmentService
from app.workers.celery_app import celery_app
from app.workers.fanout import bounded_batch_size

STAFF_REASSIGNMENT_BATCH_SIZE = 100
STAFF_REASSIGNMENT_WORKSPACE_BATCH_SIZE = 500
SYSTEM_PLATFORM_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000001")


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _workspace_ids_with_retained_data(
    *,
    session_factory=None,
    limit: int = STAFF_REASSIGNMENT_WORKSPACE_BATCH_SIZE,
) -> list[UUID]:
    session_maker = session_factory or WorkerSessionLocal
    async with session_maker() as db:
        return list(
            (
                await db.scalars(
                    select(Workspace.id)
                    .where(
                        Workspace.deleted_at.is_(None),
                    )
                    .order_by(Workspace.id)
                    .limit(bounded_batch_size(limit))
                )
            ).all()
        )


async def _async_restore_workspace(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    limit: int = STAFF_REASSIGNMENT_BATCH_SIZE,
) -> int:
    resolved_workspace_id = UUID(str(workspace_id))
    session_maker = session_factory or WorkerSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        restored = await StaffReassignmentService(db).restore_due_workspace(
            workspace_id=resolved_workspace_id,
            limit=bounded_batch_size(limit),
        )
        await db.commit()
        return restored


async def _async_restore_platform(
    *,
    session_factory=None,
    limit: int = STAFF_REASSIGNMENT_BATCH_SIZE,
) -> int:
    session_maker = session_factory or WorkerSessionLocal
    async with session_maker() as db:
        await set_platform_staff_reassignment_context(
            db,
            actor_user_id=SYSTEM_PLATFORM_ACTOR_ID,
            action="approve",
        )
        restored = await StaffReassignmentService(db).restore_due_platform(
            limit=bounded_batch_size(limit),
        )
        await db.commit()
        return restored


async def _async_dispatch_due(*, session_factory=None) -> dict[str, int]:
    workspace_ids = await _workspace_ids_with_retained_data(
        session_factory=session_factory
    )
    for workspace_id in workspace_ids:
        restore_due_workspace_staff_reassignments.apply_async(
            args=[str(workspace_id)]
        )
    restore_due_platform_staff_reassignments.apply_async()
    return {
        "workspaceTasks": len(workspace_ids),
        "platformTasks": 1,
    }


@celery_app.task(name="staff_reassignment_tasks.restore_workspace")
def restore_due_workspace_staff_reassignments(workspace_id: str) -> int:
    return _run_async(_async_restore_workspace(workspace_id))


@celery_app.task(name="staff_reassignment_tasks.restore_platform")
def restore_due_platform_staff_reassignments() -> int:
    return _run_async(_async_restore_platform())


@celery_app.task(name="staff_reassignment_tasks.dispatch_due")
def dispatch_due_staff_reassignments() -> dict[str, int]:
    return _run_async(_async_dispatch_due())


__all__ = [
    "_async_dispatch_due",
    "_async_restore_platform",
    "_async_restore_workspace",
    "_workspace_ids_with_retained_data",
    "dispatch_due_staff_reassignments",
    "restore_due_platform_staff_reassignments",
    "restore_due_workspace_staff_reassignments",
]
