from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.append_only import ImmutableRecordError
from app.models.omnichannel_v6 import (
    OmnichannelActionIntent,
    OmnichannelActionIntentEvent,
    OmnichannelChannelConnection,
)
from app.services.social_action_delivery import (
    SocialActionDispatchError,
    SocialActionDispatchReceipt,
    SocialActionDispatchRequest,
)
from app.workers import omnichannel_action_tasks as action_tasks

pytestmark = pytest.mark.usefixtures("active_subscription")


class RecordingDispatcher:
    def __init__(
        self,
        *,
        supports_idempotency: bool,
        failure: SocialActionDispatchError | None = None,
    ) -> None:
        self.supports_idempotency = supports_idempotency
        self.failure = failure
        self.requests: list[SocialActionDispatchRequest] = []

    async def dispatch(
        self, request: SocialActionDispatchRequest
    ) -> SocialActionDispatchReceipt:
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return SocialActionDispatchReceipt(
            provider=request.provider,
            external_action_id=f"provider-action-{request.action_intent_id}",
            payload={"accepted": True},
        )


def test_due_action_fanout_uses_the_current_metrics_contract(monkeypatch):
    workspace_ids = [uuid4(), uuid4()]
    dispatched: list[str] = []

    async def due_workspace_ids(**_kwargs):
        return workspace_ids

    monkeypatch.setattr(action_tasks, "_due_workspace_ids", due_workspace_ids)
    monkeypatch.setattr(
        action_tasks.process_workspace_actions_task,
        "delay",
        lambda workspace_id: dispatched.append(workspace_id),
    )

    assert action_tasks.process_due_actions_task.run() == {
        "dispatched": 2,
        "failed": 0,
    }
    assert dispatched == [str(workspace_id) for workspace_id in workspace_ids]


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def _create_queued_action(client, db_session: AsyncSession, monkeypatch):
    suffix = uuid4().hex[-8:]
    connection_key = f"instagram:action-{suffix}"
    created = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "instagram",
            "provider": "meta_instagram",
            "connectionKey": connection_key,
            "displayName": f"Instagram Action {suffix}",
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
    connection.capabilities = capabilities
    connection.status = "connected"
    await db_session.flush()

    inbound = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json={
            "provider": "meta_instagram",
            "connectionKey": connection_key,
            "channel": "instagram",
            "eventType": "direct_message",
            "externalEventId": f"ig-action-event-{suffix}",
            "externalThreadId": f"ig-action-thread-{suffix}",
            "externalUserId": f"ig-action-user-{suffix}",
            "displayName": "Lead de teste",
            "address": f"@lead_{suffix}",
            "direction": "inbound",
            "authorType": "client",
            "body": "Preciso de ajuda com a negativa.",
        },
    )
    assert inbound.status_code == 201, inbound.text

    monkeypatch.setattr(
        "app.services.omnichannel_orchestration_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(action_tasks, "external_delivery_allowed", lambda: True)
    action = await client.post(
        f"/api/v1/klm/omnichannel/threads/{inbound.json()['thread']['id']}/actions",
        json={
            "connectionId": created.json()["id"],
            "actionType": "send_message",
            "body": "Vamos entender o seu caso.",
            "idempotencyKey": f"action-delivery-{suffix}",
        },
    )
    assert action.status_code == 201, action.text
    assert action.json()["state"] == "queued"
    intent = await db_session.get(OmnichannelActionIntent, UUID(action.json()["id"]))
    assert intent is not None
    await db_session.commit()
    return intent


@pytest.mark.asyncio
async def test_action_worker_sends_once_and_exposes_immutable_evidence(
    client, db_session: AsyncSession, monkeypatch
):
    intent = await _create_queued_action(client, db_session, monkeypatch)
    dispatcher = RecordingDispatcher(supports_idempotency=True)

    first = await action_tasks.process_workspace_actions(
        intent.workspace_id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: dispatcher,
    )
    second = await action_tasks.process_workspace_actions(
        intent.workspace_id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: dispatcher,
    )

    await db_session.refresh(intent)
    assert first == {"accepted": 1}
    assert second == {}
    assert len(dispatcher.requests) == 1
    assert dispatcher.requests[0].idempotency_key == f"social-action:{intent.id}"
    assert intent.state == "accepted"
    assert intent.attempt_count == 1
    assert intent.provider_action_id == f"provider-action-{intent.id}"

    response = await client.get(
        f"/api/v1/klm/omnichannel/action-intents/{intent.id}/events"
    )
    assert response.status_code == 200, response.text
    assert [row["eventType"] for row in response.json()] == [
        "created",
        "claimed",
        "attempt_started",
        "accepted",
    ]

    event = await db_session.scalar(
        select(OmnichannelActionIntentEvent).where(
            OmnichannelActionIntentEvent.action_intent_id == intent.id
        )
    )
    assert event is not None
    event.event_metadata = {"tampered": True}
    with pytest.raises(ImmutableRecordError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_retryable_action_is_retried_with_the_same_provider_key(
    client, db_session: AsyncSession, monkeypatch
):
    intent = await _create_queued_action(client, db_session, monkeypatch)
    temporary_failure = RecordingDispatcher(
        supports_idempotency=True,
        failure=SocialActionDispatchError(
            "Limite temporário do provedor.",
            code="provider_rate_limited",
            retryable=True,
        ),
    )

    first = await action_tasks.process_action_intent(
        intent.workspace_id,
        intent.id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: temporary_failure,
    )
    await db_session.refresh(intent)
    assert first == "retrying"
    assert intent.state == "retrying"
    assert intent.attempt_count == 1
    assert intent.next_attempt_at is not None

    intent.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    recovered = RecordingDispatcher(supports_idempotency=True)
    second = await action_tasks.process_action_intent(
        intent.workspace_id,
        intent.id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: recovered,
    )

    await db_session.refresh(intent)
    expected_key = f"social-action:{intent.id}"
    assert second == "accepted"
    assert [request.idempotency_key for request in temporary_failure.requests] == [expected_key]
    assert [request.idempotency_key for request in recovered.requests] == [expected_key]
    assert intent.state == "accepted"
    assert intent.attempt_count == 2
    assert intent.lease_generation == 2


@pytest.mark.asyncio
async def test_expired_unkeyed_attempt_becomes_uncertain_without_second_dispatch(
    client, db_session: AsyncSession, monkeypatch
):
    intent = await _create_queued_action(client, db_session, monkeypatch)
    intent.state = "processing"
    intent.lease_owner = "dead-worker"
    intent.lease_generation = 1
    intent.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    intent.delivery_attempted_at = datetime.now(UTC) - timedelta(minutes=6)
    intent.provider_supports_idempotency = False
    intent.attempt_count = 1
    await db_session.commit()
    dispatcher = RecordingDispatcher(supports_idempotency=False)

    outcome = await action_tasks.process_action_intent(
        intent.workspace_id,
        intent.id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: dispatcher,
    )

    await db_session.refresh(intent)
    assert outcome == "delivery_uncertain"
    assert dispatcher.requests == []
    assert intent.state == "delivery_uncertain"
    assert intent.delivery_uncertain_at is not None
    assert intent.last_error_code == "expired_unkeyed_attempt"


@pytest.mark.asyncio
async def test_delivery_gate_closes_before_dispatch(
    client, db_session: AsyncSession, monkeypatch
):
    intent = await _create_queued_action(client, db_session, monkeypatch)
    monkeypatch.setattr(action_tasks, "external_delivery_allowed", lambda: False)
    dispatcher = RecordingDispatcher(supports_idempotency=True)

    outcome = await action_tasks.process_action_intent(
        intent.workspace_id,
        intent.id,
        session_factory=_session_factory(db_session),
        dispatcher_factory=lambda _connection: dispatcher,
    )

    await db_session.refresh(intent)
    assert outcome == "blocked_external"
    assert dispatcher.requests == []
    assert intent.state == "blocked_external"
    assert intent.attempt_count == 0
