from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.klm_operations import KlmAuditEvent
from app.models.meeting import (
    Meeting,
    MeetingCreationReceipt,
    MeetingNotificationOutbox,
)


pytestmark = pytest.mark.usefixtures("active_subscription")


def _payload(*, title: str = "Reunião idempotente") -> dict[str, str]:
    starts_at = datetime.now(UTC) + timedelta(days=5)
    return {
        "title": title,
        "startsAt": starts_at.isoformat(),
        "endsAt": (starts_at + timedelta(minutes=45)).isoformat(),
        "location": "Online",
    }


@pytest.mark.asyncio
async def test_meeting_create_requires_idempotency_key(client):
    response = await client.post("/api/v1/meetings", json=_payload())

    assert response.status_code == 422
    assert any(item["loc"] == ["header", "Idempotency-Key"] for item in response.json()["detail"])


@pytest.mark.asyncio
async def test_meeting_create_replays_exact_result_without_side_effects(
    client,
    db_session,
    auth_context,
):
    headers = {"Idempotency-Key": "meeting-create-replay-0001"}
    payload = _payload()

    first = await client.post("/api/v1/meetings", headers=headers, json=payload)
    replay = await client.post("/api/v1/meetings", headers=headers, json=payload)

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    meeting_id = first.json()["id"]
    assert (
        await db_session.scalar(select(func.count(Meeting.id)).where(Meeting.id == meeting_id)) == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(MeetingCreationReceipt.id)).where(
                MeetingCreationReceipt.workspace_id == auth_context.workspace_id,
                MeetingCreationReceipt.actor_user_id == auth_context.user_id,
                MeetingCreationReceipt.idempotency_key == headers["Idempotency-Key"],
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(MeetingNotificationOutbox.id)).where(
                MeetingNotificationOutbox.meeting_id == meeting_id
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.entity_type == "meeting",
                KlmAuditEvent.entity_id == meeting_id,
                KlmAuditEvent.event_type == "meeting_created",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_meeting_create_rejects_changed_payload_for_same_key(client):
    headers = {"Idempotency-Key": "meeting-create-mismatch-0001"}
    first = await client.post(
        "/api/v1/meetings",
        headers=headers,
        json=_payload(title="Primeiro conteúdo"),
    )
    changed = await client.post(
        "/api/v1/meetings",
        headers=headers,
        json=_payload(title="Conteúdo divergente"),
    )

    assert first.status_code == 201
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "MEETING_IDEMPOTENCY_PAYLOAD_MISMATCH"


@pytest.mark.asyncio
async def test_meeting_create_rejects_whitespace_only_key(client):
    response = await client.post(
        "/api/v1/meetings",
        headers={"Idempotency-Key": "        "},
        json=_payload(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEE-020"
