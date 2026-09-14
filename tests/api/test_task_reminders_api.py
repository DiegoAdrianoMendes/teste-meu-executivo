from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.deps import AuthContext
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)

pytestmark = pytest.mark.usefixtures("active_subscription")


async def _task(client) -> dict:
    client_row = await client.post(
        "/api/v1/clients",
        json={"name": "Cliente Lembrete", "email": f"reminder-{uuid4().hex[:8]}@example.com"},
    )
    assert client_row.status_code == 201, client_row.text
    matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": client_row.json()["id"],
            "practiceArea": "civel",
            "demandType": "Tarefa com lembrete",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
        },
    )
    assert matter.status_code == 201, matter.text
    task = await client.post(
        f"/api/v1/matters/{matter.json()['id']}/tasks",
        json={
            "title": "Responder intimação",
            "dueAt": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert task.status_code == 201, task.text
    return task.json()


async def _unrelated_member(db_session, auth: AuthContext) -> WorkspaceMembership:
    user = User(
        name="Usuário sem vínculo",
        email=f"reminder-outsider-{uuid4().hex[:8]}@example.com",
        initials="SV",
        color="#475569",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth.workspace_id,
        user_id=user.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
    )
    db_session.add(membership)
    await db_session.commit()
    return membership


@pytest.mark.asyncio
async def test_reminder_rules_crud_cas_idempotency_and_events(client) -> None:
    task = await _task(client)
    path = f"/api/v1/tasks/{task['id']}/reminder-rules"
    payload = {
        "expectedVersion": task["version"],
        "relativeTo": "general_due",
        "offsetMinutesBefore": 1440,
        "channel": "in_app",
        "recipient": "owner",
    }
    missing_key = await client.post(path, json=payload)
    assert missing_key.status_code == 422

    created = await client.post(
        path,
        headers={"Idempotency-Key": "api-reminder-create-001"},
        json=payload,
    )
    assert created.status_code == 201, created.text
    assert created.headers["Idempotent-Replayed"] == "false"
    body = created.json()
    assert body["version"] == 1
    assert body["deliveryAvailable"] is True
    assert body["state"] == "scheduled"

    replay = await client.post(
        path,
        headers={"Idempotency-Key": "api-reminder-create-001"},
        json=payload,
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == body

    conflict = await client.post(
        path,
        headers={"Idempotency-Key": "api-reminder-create-001"},
        json={**payload, "offsetMinutesBefore": 60},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "TASK_REMINDER_IDEMPOTENCY_CONFLICT"

    listed = await client.get(path)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [body["id"]]

    stale = await client.patch(
        f"{path}/{body['id']}",
        headers={"Idempotency-Key": "api-reminder-update-stale"},
        json={"expectedVersion": 99, "offsetMinutesBefore": 60},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "TASK_REMINDER_VERSION_CONFLICT"

    updated = await client.patch(
        f"{path}/{body['id']}",
        headers={"Idempotency-Key": "api-reminder-update-001"},
        json={"expectedVersion": 1, "offsetMinutesBefore": 60},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["offsetMinutesBefore"] == 60

    update_replay = await client.patch(
        f"{path}/{body['id']}",
        headers={"Idempotency-Key": "api-reminder-update-001"},
        json={"expectedVersion": 1, "offsetMinutesBefore": 60},
    )
    assert update_replay.status_code == 200, update_replay.text
    assert update_replay.headers["Idempotent-Replayed"] == "true"
    assert update_replay.json() == updated.json()

    update_key_conflict = await client.patch(
        f"{path}/{body['id']}",
        headers={"Idempotency-Key": "api-reminder-update-001"},
        json={"expectedVersion": 1, "offsetMinutesBefore": 30},
    )
    assert update_key_conflict.status_code == 409
    assert (
        update_key_conflict.json()["detail"]["code"]
        == "TASK_REMINDER_IDEMPOTENCY_CONFLICT"
    )

    events = await client.get(f"{path}/{body['id']}/events")
    assert events.status_code == 200, events.text
    assert events.json()["total"] == 2
    assert {item["eventType"] for item in events.json()["items"]} == {"created", "updated"}

    deleted = await client.delete(
        f"{path}/{body['id']}",
        headers={"Idempotency-Key": "api-reminder-delete-001"},
        params={"expectedVersion": 2},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {
        "id": body["id"],
        "taskId": task["id"],
        "deleted": True,
        "version": 3,
    }
    delete_replay = await client.delete(
        f"{path}/{body['id']}",
        headers={"Idempotency-Key": "api-reminder-delete-001"},
        params={"expectedVersion": 2},
    )
    assert delete_replay.status_code == 200, delete_replay.text
    assert delete_replay.headers["Idempotent-Replayed"] == "true"
    assert delete_replay.json() == deleted.json()
    assert (await client.get(path)).json() == []


@pytest.mark.asyncio
async def test_task_edit_permission_denial_blocks_reminder_mutation(
    client,
    db_session,
    auth_context: AuthContext,
) -> None:
    task = await _task(client)
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == auth_context.membership_id,
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
        )
    )
    assert membership is not None
    membership.permission_denials = [
        {"module": "tasks", "action": "edit", "scope": "workspace"}
    ]
    await db_session.commit()

    response = await client.post(
        f"/api/v1/tasks/{task['id']}/reminder-rules",
        headers={"Idempotency-Key": "api-reminder-denied-001"},
        json={
            "expectedVersion": task["version"],
            "relativeTo": "general_due",
            "offsetMinutesBefore": 60,
            "channel": "in_app",
            "recipient": "owner",
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
async def test_unrelated_workspace_owner_cannot_read_or_mutate_task_reminders(
    client,
    db_session,
    auth_context: AuthContext,
) -> None:
    task = await _task(client)
    path = f"/api/v1/tasks/{task['id']}/reminder-rules"
    payload = {
        "expectedVersion": task["version"],
        "relativeTo": "general_due",
        "offsetMinutesBefore": 60,
        "channel": "in_app",
        "recipient": "owner",
    }
    created = await client.post(
        path,
        headers={"Idempotency-Key": "api-reminder-bola-owner-create"},
        json=payload,
    )
    assert created.status_code == 201, created.text

    original_user_id = auth_context.user_id
    original_membership_id = auth_context.membership_id
    outsider = await _unrelated_member(db_session, auth_context)
    auth_context.user_id = outsider.user_id
    auth_context.membership_id = outsider.id
    try:
        listed = await client.get(path)
        assert listed.status_code == 404

        attempted_create = await client.post(
            path,
            headers={"Idempotency-Key": "api-reminder-bola-outsider-create"},
            json=payload,
        )
        assert attempted_create.status_code == 404

        attempted_update = await client.patch(
            f"{path}/{created.json()['id']}",
            headers={"Idempotency-Key": "api-reminder-bola-outsider-update"},
            json={"expectedVersion": 1, "offsetMinutesBefore": 30},
        )
        assert attempted_update.status_code == 404

        events = await client.get(f"{path}/{created.json()['id']}/events")
        assert events.status_code == 404
    finally:
        auth_context.user_id = original_user_id
        auth_context.membership_id = original_membership_id

    assert (await client.get(path)).status_code == 200
