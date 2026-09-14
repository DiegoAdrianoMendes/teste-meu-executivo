"""Task periódica: refresh gated de tokens Instagram Login e Facebook Login
for Business (Página/Messenger) perto de expirar.

Percorre workspaces com conexões Instagram e Página (Messenger) e chama
`MetaOAuthService.refresh_due_tokens` por canal, que é fail-closed: com
`external_delivery_allowed()` fechado, apenas identifica e audita a
intenção de renovação (`refresh_prepared`), sem tocar em rede externa.
Com o gate aberto, renova a credencial junto à Meta e persiste somente
o valor cifrado. Cada execução recalcula o estado a partir de
`capability_matrix.oauth`.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import distinct, select

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_workspace_context
from app.models.meta_channels import MetaChannelConnection, MetaChannelType
from app.models.workspace import Workspace
from app.services.meta_oauth_service import MetaOAuthService
from app.workers.celery_app import celery_app
from app.workers.fanout import FanoutMetrics, bounded_batch_size

WORKSPACE_BATCH_SIZE = 128

DEFAULT_REFRESH_CHANNEL_TYPES: tuple[MetaChannelType, ...] = (
    MetaChannelType.instagram,
    MetaChannelType.messenger,
)


def _run(coro):
    return asyncio.run(coro)


async def _due_workspace_ids(
    *,
    channel_type: MetaChannelType = MetaChannelType.instagram,
    session_factory=WorkerSessionLocal,
    batch_size: int = WORKSPACE_BATCH_SIZE,
) -> list[UUID]:
    async with session_factory() as db:
        result = await db.execute(
            select(distinct(MetaChannelConnection.workspace_id))
            .join(Workspace, Workspace.id == MetaChannelConnection.workspace_id)
            .where(
                Workspace.deleted_at.is_(None),
                Workspace.is_active.is_(True),
                MetaChannelConnection.channel_type == channel_type,
                MetaChannelConnection.access_token_encrypted.is_not(None),
            )
            .limit(bounded_batch_size(batch_size))
        )
        return list(result.scalars().all())


async def refresh_workspace_tokens(
    workspace_id: UUID,
    *,
    channel_type: MetaChannelType = MetaChannelType.instagram,
    session_factory=WorkerSessionLocal,
) -> dict[str, int]:
    async with session_factory() as db:
        await set_workspace_context(db, workspace_id)
        service = MetaOAuthService(db)
        outcome = await service.refresh_due_tokens(channel_type=channel_type)
        await db.commit()
        return outcome


async def _refresh_channel_due_tokens_async(
    channel_type: MetaChannelType,
    *,
    session_factory=WorkerSessionLocal,
    batch_size: int = WORKSPACE_BATCH_SIZE,
) -> dict[str, int]:
    workspace_ids = await _due_workspace_ids(
        channel_type=channel_type, session_factory=session_factory, batch_size=batch_size
    )
    metrics = FanoutMetrics(
        event=f"meta_oauth_refresh_due.{channel_type.value.lower()}",
        batch_limit=bounded_batch_size(batch_size),
        discovery_rows=len(workspace_ids),
        discovered_workspaces=len(workspace_ids),
    )
    totals = {"due": 0, "prepared": 0, "refreshed": 0, "failed": 0, "skipped": 0}
    for workspace_id in workspace_ids:
        try:
            outcome = await refresh_workspace_tokens(
                workspace_id, channel_type=channel_type, session_factory=session_factory
            )
            for key in totals:
                totals[key] += outcome.get(key, 0)
            metrics.enqueued_workspaces += 1
        except Exception as error:
            metrics.publish_failures += 1
            logger.error(
                "meta_oauth.refresh_workspace_failed",
                workspace_id=str(workspace_id),
                channel_type=channel_type.value,
                error_type=safe_error_type(error),
            )
    metrics.emit()
    return {"workspaces": len(workspace_ids), **totals}


async def refresh_due_tokens_async(
    *,
    channel_types: tuple[MetaChannelType, ...] = DEFAULT_REFRESH_CHANNEL_TYPES,
    session_factory=WorkerSessionLocal,
    batch_size: int = WORKSPACE_BATCH_SIZE,
) -> dict[str, int]:
    total = {
        "workspaces": 0,
        "due": 0,
        "prepared": 0,
        "refreshed": 0,
        "failed": 0,
        "skipped": 0,
    }
    for channel_type in channel_types:
        outcome = await _refresh_channel_due_tokens_async(
            channel_type, session_factory=session_factory, batch_size=batch_size
        )
        for key in total:
            total[key] += outcome.get(key, 0)
    return total


@celery_app.task(name="meta_oauth_tasks.refresh_due_tokens")
def refresh_due_tokens(batch_size: int = WORKSPACE_BATCH_SIZE) -> dict[str, int]:
    return _run(refresh_due_tokens_async(batch_size=batch_size))


__all__ = [
    "refresh_due_tokens",
    "refresh_due_tokens_async",
    "refresh_workspace_tokens",
]
