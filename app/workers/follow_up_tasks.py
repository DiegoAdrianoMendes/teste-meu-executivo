"""
Task periódica que varre `klm_follow_up_tasks` vencidos e enfileira a
notificação — mesmo padrão de duas camadas (descoberta → fan-out por
workspace) usado por `notification_tasks.send_meeting_reminders`.

Fail-closed: o envio real (`OperationalAlertService._dispatch_event`) é
gated por `external_delivery_allowed()`. Como esta task lida com fan-out de
notificação e não com estado interno de negócio, o mesmo gate é re-checado
antes de tocar o banco em cada camada, igual às demais tasks de notificação
deste worker (`notification_tasks`, `whatsapp_campaign_tasks`,
`lead_hydration_tasks`) — com o gate fechado, nada é lido nem escrito, e o
follow-up permanece pendente (elegível) para o próximo tick.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.db.worker_discovery import set_worker_discovery_context
from app.models.klm_operations import FollowUpStatus, FollowUpTask
from app.models.workspace import Workspace
from app.services.operational_alert_service import OperationalAlertService
from app.workers.celery_app import celery_app
from app.workers.fanout import FanoutMetrics, bounded_batch_size, unique_workspace_ids

FOLLOW_UP_WORKSPACE_BATCH_SIZE = 64
FOLLOW_UP_DISCOVERY_ROW_LIMIT = 512


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _workspace_id(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("workspace_id must be a valid UUID") from exc


async def _due_follow_up_workspace_ids(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = FOLLOW_UP_WORKSPACE_BATCH_SIZE,
) -> tuple[list[UUID], int]:
    reference_now = now or datetime.now(UTC)
    limit = bounded_batch_size(batch_size)
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as discovery_db:
        # Sem isto, a leitura sob FORCE RLS devolve zero linhas e o beat
        # despacha nada: ver app/db/worker_discovery.py.
        await set_worker_discovery_context(discovery_db, family="follow_up")
        rows = list(
            (
                await discovery_db.execute(
                    select(FollowUpTask.workspace_id)
                    .join(Workspace, Workspace.id == FollowUpTask.workspace_id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                        FollowUpTask.status.in_([FollowUpStatus.scheduled, FollowUpStatus.due]),
                        FollowUpTask.due_at <= reference_now,
                        FollowUpTask.last_notified_at.is_(None),
                    )
                    .order_by(FollowUpTask.due_at)
                    .limit(FOLLOW_UP_DISCOVERY_ROW_LIMIT)
                )
            ).all()
        )
    workspace_ids = (row[0] for row in rows)
    return unique_workspace_ids(workspace_ids, limit=limit), len(rows)


async def _dispatch_workspaces(
    task,
    workspace_ids: list[UUID],
    *,
    metrics: FanoutMetrics,
) -> int:
    metrics.discovered_workspaces = len(workspace_ids)
    for workspace_id in workspace_ids:
        try:
            task.apply_async(kwargs={"workspace_id": str(workspace_id)})
            metrics.enqueued_workspaces += 1
        except Exception:
            metrics.publish_failures += 1
            raise
    metrics.emit()
    return metrics.enqueued_workspaces


@celery_app.task(name="follow_up_tasks.process_due")
def process_due(workspace_id: str | None = None) -> int:
    if workspace_id is None:
        return _run_async(_async_dispatch_due_follow_ups())
    return _run_async(_async_process_due_follow_ups(workspace_id))


async def _async_dispatch_due_follow_ups(
    *,
    session_factory=None,
    now: datetime | None = None,
    batch_size: int = FOLLOW_UP_WORKSPACE_BATCH_SIZE,
) -> int:
    if not external_delivery_allowed():
        logger.info("follow_up_tasks.dispatch.blocked_by_klm_gate")
        return 0
    limit = bounded_batch_size(batch_size)
    workspace_ids, discovery_rows = await _due_follow_up_workspace_ids(
        session_factory=session_factory,
        now=now,
        batch_size=limit,
    )
    metrics = FanoutMetrics("follow_up.process_due", limit)
    metrics.discovery_rows = discovery_rows
    return await _dispatch_workspaces(
        process_due,
        workspace_ids,
        metrics=metrics,
    )


async def _async_process_due_follow_ups(
    workspace_id: UUID | str,
    *,
    session_factory=None,
) -> int:
    resolved_workspace_id = _workspace_id(workspace_id)
    if not external_delivery_allowed():
        logger.info(
            "follow_up_tasks.process_due.blocked_by_klm_gate",
            workspace_id=str(resolved_workspace_id),
        )
        return 0

    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        sent = await OperationalAlertService(db).send_due_follow_up_reminders()
        if sent:
            logger.info(
                "follow_up_tasks.notified",
                workspace_id=str(resolved_workspace_id),
                count=sent,
            )
        return sent
