"""Durable local delivery worker for native FunctionUnit outbox events."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal
from app.models.workspace import Workspace, WorkspaceStatus
from app.services.oss_native_outbox_service import (
    DEFAULT_LEASE_DURATION,
    OSSNativeOutboxClaim,
    OSSNativeOutboxService,
)
from app.websockets.manager import ws_manager
from app.workers.celery_app import celery_app

MAX_BATCH_SIZE = 50
MAX_BATCHES_PER_TASK = 10
MAX_WORKSPACES_PER_RECOVERY = 1_000
# Com `task_acks_late` sobre Redis, um `countdown` acima do `visibility_timeout`
# do transporte (default 3600s, sem `broker_transport_options` no projeto) faz o
# broker reentregar a mensagem antes de ela rodar, e a task executa duas vezes.
# O backoff máximo do outbox é 1h e o worker somava 1s: 3601s, um segundo
# acima do teto. Acordar antes de `available_at` não tem custo: o claim exige
# `available_at <= now`, e o beat de recuperação redespacha a cada minuto.
MAX_RETRY_COUNTDOWN_SECONDS = 50 * 60


def _run_async(coroutine):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


async def _deliver_local_handoff(claim: OSSNativeOutboxClaim) -> None:
    """Acknowledge the durable handoff without invoking an external provider."""

    await ws_manager.broadcast_board_update(
        "NATIVE_FUNCTION_UNIT_HANDOFF",
        {
            "eventId": str(claim.event_id),
            "unitId": claim.unit_id,
            "topic": claim.topic,
            "resourceId": str(claim.payload.get("resourceId") or ""),
            "attempt": claim.attempt,
            "externalNetworkExecuted": False,
        },
        workspace_id=claim.workspace_id,
    )


async def _settle_claim(
    claim: OSSNativeOutboxClaim,
    *,
    error: Exception | None,
    session_factory=WorkerSessionLocal,
) -> None:
    async with session_factory() as db:
        service = OSSNativeOutboxService(db)
        if error is None:
            await service.complete(claim)
        else:
            transition = await service.fail(claim, error, retryable=True)
            if transition.status.value == "pending":
                countdown = min(
                    max(
                        1,
                        int((transition.available_at - service.clock()).total_seconds()) + 1,
                    ),
                    MAX_RETRY_COUNTDOWN_SECONDS,
                )
                process_native_outbox_workspace.apply_async(
                    args=[str(claim.workspace_id)],
                    countdown=countdown,
                )
        await db.commit()


async def _process_workspace(
    workspace_id: UUID,
    *,
    session_factory=WorkerSessionLocal,
) -> dict[str, int | str]:
    claimed_total = 0
    completed_total = 0
    failed_total = 0
    for _ in range(MAX_BATCHES_PER_TASK):
        async with session_factory() as db:
            service = OSSNativeOutboxService(db)
            claims = await service.claim_batch(
                workspace_id,
                limit=MAX_BATCH_SIZE,
            )
            await db.commit()
        if not claims:
            break

        claimed_total += len(claims)
        process_native_outbox_workspace.apply_async(
            args=[str(workspace_id)],
            countdown=int(DEFAULT_LEASE_DURATION.total_seconds()) + 1,
        )
        for claim in claims:
            error: Exception | None = None
            try:
                await _deliver_local_handoff(claim)
            except Exception as caught:
                error = caught
                logger.warning(
                    "native_outbox_local_handoff_failed",
                    workspace_id=str(workspace_id),
                    event_id=str(claim.event_id),
                    error_type=safe_error_type(caught),
                )
            await _settle_claim(
                claim,
                error=error,
                session_factory=session_factory,
            )
            if error is None:
                completed_total += 1
            else:
                failed_total += 1
        if len(claims) < MAX_BATCH_SIZE:
            break
    return {
        "workspaceId": str(workspace_id),
        "claimed": claimed_total,
        "completed": completed_total,
        "failed": failed_total,
    }


@celery_app.task(name="native_outbox_tasks.process_workspace")
def process_native_outbox_workspace(workspace_id: str) -> dict[str, int | str]:
    return _run_async(_process_workspace(UUID(str(workspace_id))))


async def _recover_active_workspaces(
    *,
    session_factory=WorkerSessionLocal,
) -> int:
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
        process_native_outbox_workspace.delay(str(workspace_id))
    return len(workspace_ids)


@celery_app.task(name="native_outbox_tasks.recover_active_workspaces")
def recover_native_outbox_workspaces() -> int:
    return _run_async(_recover_active_workspaces())


__all__ = [
    "MAX_BATCHES_PER_TASK",
    "MAX_BATCH_SIZE",
    "MAX_WORKSPACES_PER_RECOVERY",
    "process_native_outbox_workspace",
    "recover_native_outbox_workspaces",
]
