"""Meta Policy Gate aplicado ANTES do dispatch, nos dois workers de entrega.

Ver docs/PLANO_META_MAXIMO_2026-07-23.md seção 4-I1 e seção 5; achados ALTOS de
Instagram/Messenger da auditoria de 23/07/2026 (janela 24h, cap 200/h por
conexão, private_reply 7 dias).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.omnichannel_v6 import (
    OmnichannelActionIntent,
    OmnichannelChannelConnection,
    OmnichannelScheduledMessage,
)
from app.services.social_action_delivery import (
    SocialActionDispatchReceipt,
    SocialActionDispatchRequest,
)
from app.workers import omnichannel_action_tasks as action_tasks
from app.workers import omnichannel_scheduled_tasks as scheduled_tasks


pytestmark = pytest.mark.usefixtures("active_subscription")


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


class RecordingDispatcher:
    def __init__(self) -> None:
        self.supports_idempotency = True
        self.requests: list[SocialActionDispatchRequest] = []

    async def dispatch(self, request: SocialActionDispatchRequest) -> SocialActionDispatchReceipt:
        self.requests.append(request)
        return SocialActionDispatchReceipt(
            provider=request.provider,
            external_action_id=f"provider-action-{request.action_intent_id}",
            payload={"accepted": True},
        )


async def _create_instagram_connection(client, db_session: AsyncSession, suffix: str):
    created = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "instagram",
            "provider": "meta_instagram",
            "connectionKey": f"instagram:gate-{suffix}",
            "displayName": f"Instagram Gate {suffix}",
            "transportMode": "official_api",
            "configuration": {
                "pageId": f"page-{suffix}",
                "instagramAccountId": f"ig-{suffix}",
            },
        },
    )
    assert created.status_code == 201, created.text
    connection = await db_session.get(
        OmnichannelChannelConnection, UUID(created.json()["id"])
    )
    assert connection is not None
    capabilities = {key: dict(value) for key, value in connection.capabilities.items()}
    capabilities["outbound_messages"].update(
        {"supported": True, "enabled": True, "state": "available_external"}
    )
    capabilities["private_reply"].update(
        {"supported": True, "enabled": True, "state": "available_external"}
    )
    connection.capabilities = capabilities
    connection.status = "connected"
    await db_session.flush()
    return created.json(), connection


async def _create_send_message_intent(
    client,
    db_session: AsyncSession,
    monkeypatch,
    *,
    suffix: str,
    inbound_age: timedelta | None,
):
    connection_json, connection = await _create_instagram_connection(client, db_session, suffix)
    monkeypatch.setattr(
        "app.services.omnichannel_orchestration_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(action_tasks, "external_delivery_allowed", lambda: True)

    inbound = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json={
            "provider": "meta_instagram",
            "connectionKey": connection_json["connectionKey"],
            "channel": "instagram",
            "eventType": "direct_message",
            "externalEventId": f"ig-gate-event-{suffix}",
            "externalThreadId": f"ig-gate-thread-{suffix}",
            "externalUserId": f"ig-gate-user-{suffix}",
            "displayName": "Lead de teste",
            "address": f"@lead_{suffix}",
            "direction": "inbound",
            "authorType": "client",
            "body": "Preciso de ajuda.",
        },
    )
    assert inbound.status_code == 201, inbound.text
    thread_id = UUID(inbound.json()["thread"]["id"])
    thread = await db_session.get(OmnichannelThread, thread_id)
    assert thread is not None
    canonical_inbound = await db_session.scalar(
        select(OmnichannelMessage).where(
            OmnichannelMessage.workspace_id == thread.workspace_id,
            OmnichannelMessage.thread_id == thread_id,
            OmnichannelMessage.connection_id == connection.id,
            OmnichannelMessage.direction == MessageDirection.inbound,
        )
    )
    assert canonical_inbound is not None
    if inbound_age is None:
        await db_session.delete(canonical_inbound)
        thread.last_inbound_at = None
    else:
        occurred_at = datetime.now(UTC) - inbound_age
        canonical_inbound.occurred_at = occurred_at
        thread.last_inbound_at = occurred_at
    await db_session.flush()

    action = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/actions",
        json={
            "connectionId": connection_json["id"],
            "actionType": "send_message",
            "body": "Resposta automática.",
            "idempotencyKey": f"action-gate-{suffix}",
        },
    )
    assert action.status_code == 201, action.text
    intent = await db_session.get(OmnichannelActionIntent, UUID(action.json()["id"]))
    assert intent is not None
    await db_session.commit()
    return intent, connection


@pytest.mark.asyncio
async def test_instagram_send_within_window_dispatches(
    client, db_session: AsyncSession, monkeypatch
):
    intent, _connection = await _create_send_message_intent(
        client, db_session, monkeypatch, suffix=uuid4().hex[-8:], inbound_age=timedelta(hours=1)
    )
    dispatcher = RecordingDispatcher()
    result = await action_tasks.process_action_intent(
        intent.workspace_id,
        intent.id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: dispatcher,
    )
    assert result == "accepted"
    assert len(dispatcher.requests) == 1


@pytest.mark.asyncio
async def test_instagram_send_past_24h_window_fails_non_retryable(
    client, db_session: AsyncSession, monkeypatch
):
    intent, _connection = await _create_send_message_intent(
        client,
        db_session,
        monkeypatch,
        suffix=uuid4().hex[-8:],
        inbound_age=timedelta(hours=25),
    )
    dispatcher = RecordingDispatcher()
    result = await action_tasks.process_action_intent(
        intent.workspace_id,
        intent.id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: dispatcher,
    )
    assert result == "failed"
    assert len(dispatcher.requests) == 0
    await db_session.refresh(intent)
    assert intent.last_error_code == "policy_gate_window_expired"


@pytest.mark.asyncio
async def test_instagram_send_without_any_inbound_denies_gatilho_first(
    client, db_session: AsyncSession, monkeypatch
):
    intent, _connection = await _create_send_message_intent(
        client, db_session, monkeypatch, suffix=uuid4().hex[-8:], inbound_age=None
    )

    dispatcher = RecordingDispatcher()
    result = await action_tasks.process_action_intent(
        intent.workspace_id,
        intent.id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: dispatcher,
    )
    assert result == "failed"
    assert len(dispatcher.requests) == 0
    await db_session.refresh(intent)
    assert intent.last_error_code == "policy_gate_no_inbound_yet"


@pytest.mark.asyncio
async def test_instagram_hourly_cap_blocks_dispatch(
    client, db_session: AsyncSession, monkeypatch
):
    suffix = uuid4().hex[-8:]
    intent, connection = await _create_send_message_intent(
        client, db_session, monkeypatch, suffix=suffix, inbound_age=timedelta(minutes=5)
    )
    # Simular 200 mensagens automatizadas já enviadas por esta conexão na última hora.
    now = datetime.now(UTC)
    for index in range(200):
        db_session.add(
            OmnichannelMessage(
                workspace_id=intent.workspace_id,
                thread_id=intent.thread_id,
                connection_id=connection.id,
                channel="instagram",
                direction=MessageDirection.outbound,
                author_type=MessageAuthorType.agent,
                author_label="Bot",
                body=f"mensagem automatizada {index}",
                occurred_at=now - timedelta(minutes=1),
                idempotency_key=f"cap-fill-{suffix}-{index}",
            )
        )
    await db_session.commit()

    dispatcher = RecordingDispatcher()
    result = await action_tasks.process_action_intent(
        intent.workspace_id,
        intent.id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: dispatcher,
    )
    assert result == "failed"
    assert len(dispatcher.requests) == 0
    await db_session.refresh(intent)
    assert intent.last_error_code == "policy_gate_ig_hourly_cap"


class ScheduledRecordingDispatcher:
    def __init__(self, *, supports_idempotency: bool = True) -> None:
        self.supports_idempotency = supports_idempotency
        self.keys: list[str] = []

    async def dispatch(self, request):
        self.keys.append(request.idempotency_key)
        from app.services.omnichannel_delivery import ScheduledDispatchReceipt

        return ScheduledDispatchReceipt(
            provider=request.provider,
            external_message_id=f"provider-{request.scheduled_message_id}",
            provider_status="accepted",
            payload={"accepted": True},
        )


@pytest.mark.asyncio
async def test_scheduled_instagram_message_past_window_is_blocked_non_retryable(
    client, db_session: AsyncSession
):
    suffix = uuid4().hex[-8:]
    connection_json, connection = await _create_instagram_connection(
        client, db_session, suffix
    )
    inbound = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json={
            "provider": "meta_instagram",
            "connectionKey": connection_json["connectionKey"],
            "channel": "instagram",
            "eventType": "direct_message",
            "externalEventId": f"ig-sched-event-{suffix}",
            "externalThreadId": f"ig-sched-thread-{suffix}",
            "externalUserId": f"ig-sched-user-{suffix}",
            "displayName": "Lead de teste",
            "address": f"@lead_{suffix}",
            "direction": "inbound",
            "authorType": "client",
            "body": "Preciso de ajuda.",
        },
    )
    assert inbound.status_code == 201, inbound.text
    thread_id = UUID(inbound.json()["thread"]["id"])
    thread = await db_session.get(OmnichannelThread, thread_id)
    assert thread is not None
    occurred_at = datetime.now(UTC) - timedelta(hours=48)
    canonical_inbound = await db_session.scalar(
        select(OmnichannelMessage).where(
            OmnichannelMessage.workspace_id == thread.workspace_id,
            OmnichannelMessage.thread_id == thread_id,
            OmnichannelMessage.connection_id == connection.id,
            OmnichannelMessage.direction == MessageDirection.inbound,
        )
    )
    assert canonical_inbound is not None
    canonical_inbound.occurred_at = occurred_at
    thread.last_inbound_at = occurred_at
    await db_session.flush()

    scheduled = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/scheduled-messages",
        json={
            "endpointId": inbound.json()["endpoint"]["id"],
            "connectionKey": connection_json["connectionKey"],
            "channel": "instagram",
            "body": "Mensagem agendada fora da janela.",
            "scheduledAt": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "idempotencyKey": f"schedule-gate-{suffix}",
        },
    )
    assert scheduled.status_code == 201, scheduled.text
    schedule = await db_session.scalar(
        select_schedule(UUID(scheduled.json()["id"]))
    )
    schedule.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    dispatcher = ScheduledRecordingDispatcher()
    processed = await scheduled_tasks._async_process_due(
        session_factory=_session_factory(db_session),
        allow_external=True,
        dispatcher_factory=lambda _connection: dispatcher,
    )
    await db_session.refresh(schedule)
    assert processed == 0
    assert dispatcher.keys == []
    assert schedule.state == "failed"
    assert schedule.last_error_code == "policy_gate_window_expired"


def select_schedule(schedule_id: UUID):
    return select(OmnichannelScheduledMessage).where(
        OmnichannelScheduledMessage.id == schedule_id
    )
