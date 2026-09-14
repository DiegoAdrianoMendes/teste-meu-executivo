from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.klm_operations import MessageDeliveryStatus, OmnichannelMessage
from app.models.omnichannel_v6 import (
    OmnichannelScheduledMessage,
    OmnichannelScheduledMessageEvent,
)
from app.workers import omnichannel_scheduled_tasks as scheduled_tasks


pytestmark = pytest.mark.usefixtures("active_subscription")


class SimulatedProcessDeath(BaseException):
    pass


class RecordingDispatcher:
    def __init__(self, *, supports_idempotency: bool) -> None:
        self.supports_idempotency = supports_idempotency
        self.keys: list[str] = []

    async def dispatch(
        self, request: scheduled_tasks.ScheduledDispatchRequest
    ) -> scheduled_tasks.ScheduledDispatchReceipt:
        self.keys.append(request.idempotency_key)
        return scheduled_tasks.ScheduledDispatchReceipt(
            provider=request.provider,
            external_message_id=f"provider-{request.scheduled_message_id}",
            provider_status="accepted",
            payload={"accepted": True},
        )


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def _create_due_schedule(client, db_session: AsyncSession, *, suffix: str):
    connection_key = f"meta_cloud:native-schedule-{suffix}"
    phone_suffix = f"{uuid4().int % 10_000:04d}"
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "whatsapp",
            "provider": "meta_cloud",
            "connectionKey": connection_key,
            "displayName": f"WhatsApp {suffix}",
            "transportMode": "official_api",
            "isDefault": False,
        },
    )
    assert connection.status_code == 201, connection.text
    inbound = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json={
            "provider": "meta_cloud",
            "connectionKey": connection_key,
            "channel": "whatsapp",
            "eventType": "message.received",
            "externalEventId": f"wamid-schedule-{suffix}",
            "externalThreadId": f"thread-schedule-{suffix}",
            "externalUserId": f"5511999{phone_suffix}",
            "displayName": f"Lead {suffix}",
            "address": f"+5511999{phone_suffix}",
            "body": "Pode me lembrar depois?",
            "direction": "inbound",
            "authorType": "client",
        },
    )
    assert inbound.status_code == 201, inbound.text
    scheduled = await client.post(
        f"/api/v1/klm/omnichannel/threads/{inbound.json()['thread']['id']}/scheduled-messages",
        json={
            "endpointId": inbound.json()["endpoint"]["id"],
            "connectionKey": connection_key,
            "channel": "whatsapp",
            "body": "Este é o lembrete combinado.",
            "scheduledAt": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "idempotencyKey": f"schedule-native-{suffix}",
        },
    )
    assert scheduled.status_code == 201, scheduled.text
    schedule = await db_session.scalar(
        select(OmnichannelScheduledMessage).where(
            OmnichannelScheduledMessage.id == UUID(scheduled.json()["id"])
        )
    )
    assert schedule is not None
    schedule.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    return schedule


def _enable_canonical_external_projection(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.klm_operations_service.external_delivery_allowed",
        lambda: True,
    )


@pytest.mark.asyncio
async def test_native_schedule_gate_closed_executes_nothing(
    client, db_session: AsyncSession
):
    schedule = await _create_due_schedule(
        client, db_session, suffix=f"gate-{uuid4().hex[-4:]}"
    )
    dispatcher = RecordingDispatcher(supports_idempotency=True)

    count = await scheduled_tasks._async_process_due(
        session_factory=_session_factory(db_session),
        allow_external=False,
        dispatcher_factory=lambda _connection: dispatcher,
    )

    await db_session.refresh(schedule)
    assert count == 0
    assert dispatcher.keys == []
    assert schedule.state == "scheduled"
    assert schedule.attempts == 0


@pytest.mark.asyncio
async def test_native_schedule_sends_once_and_records_evidence(
    client, db_session: AsyncSession, monkeypatch
):
    _enable_canonical_external_projection(monkeypatch)
    schedule = await _create_due_schedule(
        client, db_session, suffix=f"sent-{uuid4().hex[-4:]}"
    )
    dispatcher = RecordingDispatcher(supports_idempotency=True)

    first = await scheduled_tasks._async_process_due(
        session_factory=_session_factory(db_session),
        allow_external=True,
        dispatcher_factory=lambda _connection: dispatcher,
    )
    second = await scheduled_tasks._async_process_due(
        session_factory=_session_factory(db_session),
        allow_external=True,
        dispatcher_factory=lambda _connection: dispatcher,
    )

    await db_session.refresh(schedule)
    message = await db_session.get(OmnichannelMessage, schedule.canonical_message_id)
    event_types = list(
        (
            await db_session.scalars(
                select(OmnichannelScheduledMessageEvent.event_type)
                .where(
                    OmnichannelScheduledMessageEvent.workspace_id == schedule.workspace_id,
                    OmnichannelScheduledMessageEvent.scheduled_message_id == schedule.id,
                )
                .order_by(OmnichannelScheduledMessageEvent.occurred_at)
            )
        ).all()
    )
    expected_key = f"scheduled:{schedule.id}"
    assert first == 1
    assert second == 0
    assert dispatcher.keys == [expected_key]
    assert schedule.state == "sent"
    assert schedule.attempts == 1
    assert schedule.provider_message_id == f"provider-{schedule.id}"
    assert message is not None
    assert message.delivery_status == MessageDeliveryStatus.sent
    assert event_types == ["created", "claimed", "attempt_started", "sent"]


@pytest.mark.asyncio
async def test_unkeyed_accept_then_process_death_becomes_uncertain(
    client, db_session: AsyncSession, monkeypatch
):
    _enable_canonical_external_projection(monkeypatch)
    schedule = await _create_due_schedule(
        client, db_session, suffix=f"uncertain-{uuid4().hex[-4:]}"
    )
    dispatcher = RecordingDispatcher(supports_idempotency=False)

    def crash_after_accept(point: str, **_details) -> None:
        if point == "native_schedule.after_provider_accept":
            raise SimulatedProcessDeath()

    monkeypatch.setattr(scheduled_tasks, "_inject_fault", crash_after_accept)
    with pytest.raises(SimulatedProcessDeath):
        await scheduled_tasks._async_process_due(
            session_factory=_session_factory(db_session),
            allow_external=True,
            dispatcher_factory=lambda _connection: dispatcher,
        )

    await db_session.refresh(schedule)
    assert schedule.state == "processing"
    assert schedule.delivery_attempted_at is not None
    schedule.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_inject_fault", lambda *_args, **_kwargs: None)

    await scheduled_tasks._async_process_due(
        session_factory=_session_factory(db_session),
        allow_external=True,
        dispatcher_factory=lambda _connection: dispatcher,
    )
    await db_session.refresh(schedule)
    assert dispatcher.keys == [f"scheduled:{schedule.id}"]
    assert schedule.state == "delivery_uncertain"
    assert schedule.delivery_uncertain_at is not None


@pytest.mark.asyncio
async def test_idempotent_accept_then_process_death_reuses_same_key(
    client, db_session: AsyncSession, monkeypatch
):
    _enable_canonical_external_projection(monkeypatch)
    schedule = await _create_due_schedule(
        client, db_session, suffix=f"retry-{uuid4().hex[-4:]}"
    )
    dispatcher = RecordingDispatcher(supports_idempotency=True)

    def crash_after_accept(point: str, **_details) -> None:
        if point == "native_schedule.after_provider_accept":
            raise SimulatedProcessDeath()

    monkeypatch.setattr(scheduled_tasks, "_inject_fault", crash_after_accept)
    with pytest.raises(SimulatedProcessDeath):
        await scheduled_tasks._async_process_due(
            session_factory=_session_factory(db_session),
            allow_external=True,
            dispatcher_factory=lambda _connection: dispatcher,
        )

    await db_session.refresh(schedule)
    schedule.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    monkeypatch.setattr(scheduled_tasks, "_inject_fault", lambda *_args, **_kwargs: None)

    await scheduled_tasks._async_process_due(
        session_factory=_session_factory(db_session),
        allow_external=True,
        dispatcher_factory=lambda _connection: dispatcher,
    )
    await db_session.refresh(schedule)
    expected_key = f"scheduled:{schedule.id}"
    assert dispatcher.keys == [expected_key, expected_key]
    assert schedule.state == "sent"
    assert schedule.attempts == 2
    assert schedule.lease_generation == 2
