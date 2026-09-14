"""Varredura periódica e idempotente do ciclo de vida dos chamados do SAC."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.support_ticket import SupportTicket, SupportTicketStatus
from app.services.support_ticket_service import SupportTicketService
from app.workers.celery_app import celery_app
from app.workers.fanout import bounded_batch_size

SUPPORT_LIFECYCLE_BATCH_SIZE = 100


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _workspace_ids(
    *,
    session_factory=None,
    batch_size: int = SUPPORT_LIFECYCLE_BATCH_SIZE,
) -> list[UUID]:
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat despacha
        # nada: ver app/db/worker_discovery.py (S6).
        await set_worker_discovery_context(db, family="support_lifecycle")
        return list(
            (
                await db.scalars(
                    select(SupportTicket.workspace_id)
                    .where(
                        SupportTicket.status.in_(
                            (
                                SupportTicketStatus.new,
                                SupportTicketStatus.triage,
                                SupportTicketStatus.in_progress,
                                SupportTicketStatus.waiting_customer,
                                SupportTicketStatus.waiting_internal,
                                SupportTicketStatus.validating,
                                SupportTicketStatus.resolved,
                            )
                        )
                    )
                    .distinct()
                    .limit(bounded_batch_size(batch_size))
                )
            ).all()
        )


async def _async_process_workspace(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    batch_size: int = SUPPORT_LIFECYCLE_BATCH_SIZE,
) -> dict[str, int]:
    resolved_workspace_id = UUID(str(workspace_id))
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        result = await SupportTicketService(db).process_lifecycle(
            workspace_id=resolved_workspace_id,
            limit=bounded_batch_size(batch_size),
        )
        await db.commit()
        return result


async def _async_dispatch_due(
    *,
    session_factory=None,
    batch_size: int = SUPPORT_LIFECYCLE_BATCH_SIZE,
) -> int:
    workspace_ids = await _workspace_ids(
        session_factory=session_factory,
        batch_size=batch_size,
    )
    for workspace_id in workspace_ids:
        process_support_lifecycle_workspace.apply_async(args=[str(workspace_id)])
    return len(workspace_ids)


@celery_app.task(name="support_lifecycle_tasks.process_workspace")
def process_support_lifecycle_workspace(workspace_id: str) -> dict[str, int]:
    return _run_async(_async_process_workspace(workspace_id))


@celery_app.task(name="support_lifecycle_tasks.process_due")
def process_due_support_lifecycle() -> int:
    return _run_async(_async_dispatch_due())


__all__ = [
    "_async_dispatch_due",
    "_async_process_workspace",
    "process_due_support_lifecycle",
    "process_support_lifecycle_workspace",
]
