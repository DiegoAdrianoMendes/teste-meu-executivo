"""Route-scoped customer-service window lookups."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import (
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)


async def latest_inbound_at_for_route(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread: OmnichannelThread,
    channel: OmnichannelChannel | str,
    connection_id: UUID,
) -> datetime | None:
    """Return the newest inbound for one identity, channel and connection."""

    resolved_channel = (
        channel if isinstance(channel, OmnichannelChannel) else OmnichannelChannel(channel)
    )
    statement = select(func.max(OmnichannelMessage.occurred_at)).where(
        OmnichannelMessage.workspace_id == workspace_id,
        OmnichannelMessage.direction == MessageDirection.inbound,
        OmnichannelMessage.channel == resolved_channel,
        OmnichannelMessage.connection_id == connection_id,
    )
    if thread.identity_id is None:
        statement = statement.where(OmnichannelMessage.thread_id == thread.id)
    else:
        identity_threads = select(OmnichannelThread.id).where(
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.identity_id == thread.identity_id,
        )
        statement = statement.where(OmnichannelMessage.thread_id.in_(identity_threads))
    return await db.scalar(statement)
