"""Meeting mutations publish workspace-scoped realtime events after commit."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.post_commit import (
    publish_committed_realtime_events,
    queue_meeting_notification_wakeup,
)
from app.models.klm_operations import KlmAuditEvent
from app.models.meeting import (
    Meeting,
    MeetingNotificationOutbox,
    MeetingNotificationStatus,
    MeetingStatus,
)
from app.schemas.control_plane import MeetingCreate, MeetingUpdate
from app.services.meeting_service import MeetingService
from app.websockets.manager import ws_manager


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_postcommit_meeting_wakeup_failure_never_rejects_commit(
    db_session,
    auth_context,
    monkeypatch,
):
    def broker_unavailable(*_args, **_kwargs):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(
        "app.workers.meeting_notification_tasks."
        "process_meeting_notification_workspace.apply_async",
        broker_unavailable,
    )
    queue_meeting_notification_wakeup(
        db_session,
        workspace_id=auth_context.workspace_id,
    )

    await publish_committed_realtime_events(db_session)

    # The wakeup is advisory: its durable outbox/recovery path owns retries.
    await publish_committed_realtime_events(db_session)


@pytest.mark.asyncio
async def test_meeting_crud_queues_realtime_events(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    published: list[dict] = []

    async def capture(event_type, data, *, workspace_id):
        published.append(
            {
                "event_type": event_type,
                "data": data,
                "workspace_id": workspace_id,
            }
        )

    monkeypatch.setattr(ws_manager, "broadcast_board_update", capture)
    monkeypatch.setattr(
        "app.workers.meeting_notification_tasks."
        "process_meeting_notification_workspace.apply_async",
        lambda *args, **kwargs: None,
    )
    starts_at = datetime.now(timezone.utc) + timedelta(days=1)
    created = await client.post(
        "/api/v1/meetings",
        headers={"Idempotency-Key": f"meeting-realtime-{uuid4().hex}"},
        json={
            "title": "Booking recebido pelo site",
            "startsAt": starts_at.isoformat(),
            "endsAt": (starts_at + timedelta(hours=1)).isoformat(),
            "location": "Google Meet",
        },
    )

    assert created.status_code == 201
    assert published == []
    await publish_committed_realtime_events(db_session)
    assert published[-1]["event_type"] == "MEETING_CREATED"
    assert published[-1]["data"]["meeting_id"] == created.json()["id"]
    assert published[-1]["data"]["title"] == "Booking recebido pelo site"
    assert published[-1]["workspace_id"] == auth_context.workspace_id

    missing_version = await client.patch(
        f"/api/v1/meetings/{created.json()['id']}",
        json={"title": "Atualização sem compare-and-swap"},
    )
    assert missing_version.status_code == 422

    updated = await client.patch(
        f"/api/v1/meetings/{created.json()['id']}",
        json={"title": "Booking confirmado", "expectedVersion": 1},
    )
    assert updated.status_code == 200
    await publish_committed_realtime_events(db_session)
    assert published[-1]["event_type"] == "MEETING_UPDATED"

    missing_cancel_version = await client.delete(
        f"/api/v1/meetings/{created.json()['id']}",
    )
    assert missing_cancel_version.status_code == 422

    stale_cancel = await client.delete(
        f"/api/v1/meetings/{created.json()['id']}",
        params={"expectedVersion": 1},
    )
    assert stale_cancel.status_code == 409
    assert stale_cancel.json()["detail"]["code"] == "MEE-009"

    cancelled = await client.delete(
        f"/api/v1/meetings/{created.json()['id']}",
        params={"expectedVersion": 2},
    )
    assert cancelled.status_code == 204, cancelled.text
    await publish_committed_realtime_events(db_session)
    assert published[-1]["event_type"] == "MEETING_CANCELLED"
    assert published[-1]["data"]["meeting_id"] == created.json()["id"]
    persisted = await db_session.get(Meeting, created.json()["id"])
    assert persisted is not None
    assert persisted.status == MeetingStatus.cancelled
    assert persisted.version == 3
    outbox_rows = list(
        (
            await db_session.scalars(
                select(MeetingNotificationOutbox)
                .where(MeetingNotificationOutbox.meeting_id == persisted.id)
                .order_by(MeetingNotificationOutbox.meeting_version)
            )
        ).all()
    )
    assert [row.meeting_version for row in outbox_rows] == [1, 2]
    assert [row.status for row in outbox_rows] == [
        MeetingNotificationStatus.disabled,
        MeetingNotificationStatus.disabled,
    ]
    assert [row.last_error_code for row in outbox_rows] == [
        "meeting_superseded",
        "meeting_cancelled",
    ]

    published_count = len(published)
    replay = await client.delete(
        f"/api/v1/meetings/{created.json()['id']}",
        params={"expectedVersion": 2},
    )
    assert replay.status_code == 204, replay.text
    await publish_committed_realtime_events(db_session)
    assert len(published) == published_count
    await db_session.refresh(persisted)
    assert persisted.version == 3
    audit_events = list(
        (
            await db_session.scalars(
                select(KlmAuditEvent)
                .where(
                    KlmAuditEvent.workspace_id == auth_context.workspace_id,
                    KlmAuditEvent.entity_type == "meeting",
                    KlmAuditEvent.entity_id == created.json()["id"],
                )
                .order_by(KlmAuditEvent.occurred_at, KlmAuditEvent.id)
            )
        ).all()
    )
    by_type = {event.event_type: event for event in audit_events}
    assert {"meeting_created", "meeting_updated", "meeting_cancelled"} <= set(by_type)
    assert by_type["meeting_created"].before_data is None
    assert by_type["meeting_created"].after_data["version"] == 1
    assert by_type["meeting_updated"].before_data["title"] == "Booking recebido pelo site"
    assert by_type["meeting_updated"].after_data["title"] == "Booking confirmado"
    assert by_type["meeting_cancelled"].before_data["version"] == 2
    assert by_type["meeting_cancelled"].after_data["version"] == 3
    assert by_type["meeting_cancelled"].after_data["status"] == "cancelled"
    assert sum(event.event_type == "meeting_cancelled" for event in audit_events) == 1


@pytest.mark.asyncio
async def test_direct_meeting_service_creation_also_queues_realtime_event(
    db_session,
    auth_context,
    monkeypatch,
):
    published: list[str] = []

    async def capture(event_type, data, *, workspace_id):
        published.append(event_type)

    monkeypatch.setattr(ws_manager, "broadcast_board_update", capture)
    monkeypatch.setattr(
        "app.workers.meeting_notification_tasks."
        "process_meeting_notification_workspace.apply_async",
        lambda *args, **kwargs: None,
    )
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Booking criado por agente interno",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            location="Google Meet",
        ),
    )

    assert published == []
    await db_session.commit()
    await publish_committed_realtime_events(db_session)
    assert published == ["MEETING_CREATED"]


@pytest.mark.asyncio
async def test_manual_no_show_records_status_audit(
    client,
    db_session,
    auth_context,
):
    starts_at = datetime.now(timezone.utc) + timedelta(days=3)
    created = await client.post(
        "/api/v1/meetings",
        headers={"Idempotency-Key": f"meeting-no-show-{uuid4().hex}"},
        json={
            "title": "Consulta para confirmar presença",
            "startsAt": starts_at.isoformat(),
            "endsAt": (starts_at + timedelta(minutes=45)).isoformat(),
            "location": "Online",
        },
    )
    assert created.status_code == 201

    updated = await client.patch(
        f"/api/v1/meetings/{created.json()['id']}",
        json={"status": "no_show", "expectedVersion": 1},
    )

    assert updated.status_code == 200
    assert updated.json()["status"] == "no_show"
    audit = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.entity_type == "meeting",
                KlmAuditEvent.entity_id == created.json()["id"],
                KlmAuditEvent.event_type == "meeting_status_changed",
            )
        )
    ).one()
    assert audit.actor_user_id == auth_context.user_id
    assert audit.actor_type == "human"
    assert audit.before_data["status"] == "scheduled"
    assert audit.after_data["status"] == "no_show"
    assert audit.before_data["version"] == 1
    assert audit.after_data["version"] == 2
    assert audit.before_data["title"] == "Consulta para confirmar presença"
    assert audit.after_data["title"] == "Consulta para confirmar presença"


@pytest.mark.asyncio
async def test_noop_meeting_update_does_not_advance_version_or_duplicate_outbox(
    db_session,
    auth_context,
):
    starts_at = datetime.now(timezone.utc) + timedelta(days=6)
    service = MeetingService(db_session)
    meeting = await service.create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Compromisso sem mudança",
            starts_at=starts_at,
            location="Online",
        ),
        actor_user_id=auth_context.user_id,
    )
    unchanged = await service.update_for_workspace(
        auth_context.workspace_id,
        meeting.id,
        MeetingUpdate(expected_version=1, title="Compromisso sem mudança"),
        actor_user_id=auth_context.user_id,
    )
    await db_session.commit()

    assert unchanged.version == 1
    assert await db_session.scalar(
        select(func.count(MeetingNotificationOutbox.id)).where(
            MeetingNotificationOutbox.meeting_id == meeting.id
        )
    ) == 1
    assert await db_session.scalar(
        select(func.count(KlmAuditEvent.id)).where(
            KlmAuditEvent.entity_type == "meeting",
            KlmAuditEvent.entity_id == meeting.id,
            KlmAuditEvent.event_type == "meeting_updated",
        )
    ) == 0


@pytest.mark.asyncio
async def test_changed_meeting_url_enqueues_new_version_even_when_both_urls_are_masked(
    db_session,
    auth_context,
):
    starts_at = datetime.now(timezone.utc) + timedelta(days=7)
    service = MeetingService(db_session)
    meeting = await service.create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Compromisso com link",
            starts_at=starts_at,
            location="Online",
            meeting_url="https://meet.example.com/sala-a",
        ),
        actor_user_id=auth_context.user_id,
    )
    original = await db_session.scalar(
        select(MeetingNotificationOutbox).where(
            MeetingNotificationOutbox.meeting_id == meeting.id
        )
    )
    assert original is not None
    original.status = MeetingNotificationStatus.delivered
    original.delivered_at = datetime.now(timezone.utc)
    await db_session.flush()

    updated = await service.update_for_workspace(
        auth_context.workspace_id,
        meeting.id,
        MeetingUpdate(
            expected_version=1,
            meeting_url="https://meet.example.com/sala-b",
        ),
        actor_user_id=auth_context.user_id,
    )
    await db_session.commit()

    rows = list(
        (
            await db_session.scalars(
                select(MeetingNotificationOutbox)
                .where(MeetingNotificationOutbox.meeting_id == meeting.id)
                .order_by(MeetingNotificationOutbox.meeting_version)
            )
        ).all()
    )
    assert updated.version == 2
    assert [(row.meeting_version, row.status) for row in rows] == [
        (1, MeetingNotificationStatus.delivered),
        (2, MeetingNotificationStatus.pending),
    ]
