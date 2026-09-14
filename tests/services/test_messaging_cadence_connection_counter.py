"""`MessagingCadenceService.count_recent_agent_messages_for_connection`: contador
POR CONEXÃO (distinto do contador por workspace já existente), usado pelo cap
de 200 msgs automatizadas/hora do Instagram (Meta Policy Gate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.services.messaging_cadence_service import MessagingCadenceService


async def _thread(db_session, workspace_id) -> OmnichannelThread:
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        primary_channel="instagram",
        status=ThreadStatus.open,
    )
    db_session.add(thread)
    await db_session.flush()
    return thread


async def _message(
    db_session,
    *,
    workspace_id,
    thread_id,
    connection_id,
    author_type: MessageAuthorType,
    direction: MessageDirection,
    occurred_at: datetime,
    key: str,
) -> None:
    db_session.add(
        OmnichannelMessage(
            workspace_id=workspace_id,
            thread_id=thread_id,
            connection_id=connection_id,
            channel="instagram",
            direction=direction,
            author_type=author_type,
            author_label="Bot",
            body="mensagem",
            occurred_at=occurred_at,
            idempotency_key=key,
        )
    )


@pytest.mark.asyncio
async def test_counter_isolates_by_connection(db_session, auth_context):
    thread = await _thread(db_session, auth_context.workspace_id)
    connection_a = uuid4()
    connection_b = uuid4()
    now = datetime.now(UTC)
    for index in range(3):
        await _message(
            db_session,
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            connection_id=connection_a,
            author_type=MessageAuthorType.agent,
            direction=MessageDirection.outbound,
            occurred_at=now - timedelta(minutes=5),
            key=f"conn-a-{index}",
        )
    for index in range(5):
        await _message(
            db_session,
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            connection_id=connection_b,
            author_type=MessageAuthorType.agent,
            direction=MessageDirection.outbound,
            occurred_at=now - timedelta(minutes=5),
            key=f"conn-b-{index}",
        )
    await db_session.flush()

    service = MessagingCadenceService(db_session)
    count_a = await service.count_recent_agent_messages_for_connection(
        auth_context.workspace_id, connection_a, now=now
    )
    count_b = await service.count_recent_agent_messages_for_connection(
        auth_context.workspace_id, connection_b, now=now
    )
    assert count_a == 3
    assert count_b == 5


@pytest.mark.asyncio
async def test_counter_only_counts_agent_author_type(db_session, auth_context):
    thread = await _thread(db_session, auth_context.workspace_id)
    connection_id = uuid4()
    now = datetime.now(UTC)
    await _message(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        connection_id=connection_id,
        author_type=MessageAuthorType.agent,
        direction=MessageDirection.outbound,
        occurred_at=now - timedelta(minutes=1),
        key="agent-1",
    )
    await _message(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        connection_id=connection_id,
        author_type=MessageAuthorType.human,
        direction=MessageDirection.outbound,
        occurred_at=now - timedelta(minutes=1),
        key="human-1",
    )
    await _message(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        connection_id=connection_id,
        author_type=MessageAuthorType.client,
        direction=MessageDirection.inbound,
        occurred_at=now - timedelta(minutes=1),
        key="client-1",
    )
    await db_session.flush()

    service = MessagingCadenceService(db_session)
    count = await service.count_recent_agent_messages_for_connection(
        auth_context.workspace_id, connection_id, now=now
    )
    assert count == 1


@pytest.mark.asyncio
async def test_counter_excludes_messages_outside_window(db_session, auth_context):
    thread = await _thread(db_session, auth_context.workspace_id)
    connection_id = uuid4()
    now = datetime.now(UTC)
    await _message(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        connection_id=connection_id,
        author_type=MessageAuthorType.agent,
        direction=MessageDirection.outbound,
        occurred_at=now - timedelta(hours=2),
        key="stale-1",
    )
    await db_session.flush()

    service = MessagingCadenceService(db_session)
    count = await service.count_recent_agent_messages_for_connection(
        auth_context.workspace_id, connection_id, now=now
    )
    assert count == 0
