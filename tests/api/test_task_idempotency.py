"""Idempotency contract for one-click tasks created from the inbox."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.task import Task
from app.models.timeline_event import TimelineEvent, TimelineEventType


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _matter(client) -> dict:
    lead = await client.post(
        "/api/v1/clients",
        json={"name": "Lead do Copiloto", "email": f"copilot-{uuid4().hex[:8]}@example.com"},
    )
    assert lead.status_code == 201, lead.text
    matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": lead.json()["id"],
            "practiceArea": "saude_suplementar",
            "demandType": "Negativa de medicamento",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
        },
    )
    assert matter.status_code == 201, matter.text
    return matter.json()


@pytest.mark.asyncio
async def test_task_creation_replays_same_key_without_duplicate_or_second_timeline_event(
    client,
    db_session,
):
    matter = await _matter(client)
    payload = {
        "title": "Conferir prescrição médica",
        "description": "Criada pelo Assistente KLM.",
        "priority": "alta",
        "isAiGenerated": True,
        "idempotencyKey": "copilot-task:thread-001:prescricao",
    }

    first = await client.post(f"/api/v1/matters/{matter['id']}/tasks", json=payload)
    replay = await client.post(f"/api/v1/matters/{matter['id']}/tasks", json=payload)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    matter_id = UUID(matter["id"])
    task_count = await db_session.scalar(
        select(func.count(Task.id)).where(Task.matter_id == matter_id)
    )
    event_count = await db_session.scalar(
        select(func.count(TimelineEvent.id)).where(
            TimelineEvent.matter_id == matter_id,
            TimelineEvent.event_type == TimelineEventType.task_created,
        )
    )
    assert task_count == 1
    assert event_count == 1


@pytest.mark.asyncio
async def test_task_creation_rejects_reusing_key_with_different_payload(client):
    matter = await _matter(client)
    endpoint = f"/api/v1/matters/{matter['id']}/tasks"
    key = "copilot-task:thread-002:documentos"
    first = await client.post(
        endpoint,
        json={"title": "Solicitar negativa", "idempotencyKey": key},
    )
    conflict = await client.post(
        endpoint,
        json={"title": "Preparar contrato", "idempotencyKey": key},
    )

    assert first.status_code == 201, first.text
    assert conflict.status_code == 409, conflict.text
    assert "outro conteúdo" in conflict.json()["detail"]
