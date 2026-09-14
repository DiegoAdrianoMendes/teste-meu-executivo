from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, can_view_sensitive_lead_data
from app.main import app as fastapi_app
from app.models.meeting import Meeting, MeetingEventType, MeetingNature, MeetingStatus
from app.models.process_monitoring import (
    DeadlineCountingType,
    DeadlineStatus,
    ProcessPublication,
    ProcessPublicationSource,
)
from app.models.workspace_membership import WorkspaceMembership


@pytest.mark.asyncio
async def test_agenda_composes_sources_independently_and_masks_hidden_lead_data(
    client,
    db_session: AsyncSession,
    auth_context: AuthContext,
    active_subscription,
) -> None:
    secret_name = f"Cliente Oraculo {uuid4().hex[:8]}"
    lead = await client.post("/api/v1/clients", json={"name": secret_name})
    assert lead.status_code == 201, lead.text
    matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": lead.json()["id"],
            "practiceArea": "civel",
            "demandType": "Demanda agenda autorizada",
            "columnId": "novo_lead",
            "microStatus": "lead_novo",
        },
    )
    assert matter.status_code == 201, matter.text

    starts_at = datetime.now(UTC) + timedelta(days=1)
    task = await client.post(
        f"/api/v1/matters/{matter.json()['id']}/tasks",
        json={
            "title": f"Preparar manifestação de {secret_name}",
            "dueAt": (starts_at + timedelta(hours=1)).isoformat(),
            "participants": [
                {"userId": str(auth_context.user_id), "role": "owner"},
            ],
        },
    )
    assert task.status_code == 201, task.text

    db_session.add(
        Meeting(
            workspace_id=auth_context.workspace_id,
            matter_id=UUID(matter.json()["id"]),
            title=f"Reunião com {secret_name}",
            starts_at=starts_at,
            status=MeetingStatus.scheduled,
            natureza=MeetingNature.comercial,
            event_type=MeetingEventType.reuniao,
            meeting_url="https://meet.example.com/segredo",
        )
    )
    # A linha existe, mas o workspace não contratou o módulo processual. A
    # agenda deve omiti-la sem derrubar reunião/tarefa autorizadas.
    db_session.add(
        ProcessPublication(
            workspace_id=auth_context.workspace_id,
            fonte=ProcessPublicationSource.djen,
            external_id=f"agenda-auth-{uuid4().hex[:10]}",
            numero_cnj="0001234-37.2026.8.07.0001",
            conteudo="Intimação sigilosa",
            published_at=datetime.now(UTC),
            dias=5,
            tipo_contagem=DeadlineCountingType.dias_uteis,
            data_fatal=(starts_at + timedelta(days=1)).date(),
            prazo_status=DeadlineStatus.confirmado,
            memoria_calculo=[{"evento": "segredo"}],
            confirmed_by_user_id=auth_context.user_id,
            confirmed_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    fastapi_app.dependency_overrides[can_view_sensitive_lead_data] = lambda: False
    params = {
        "start": (starts_at - timedelta(hours=1)).isoformat(),
        "end": (starts_at + timedelta(days=3)).isoformat(),
    }
    masked = await client.get("/api/v1/agenda", params=params)
    assert masked.status_code == 200, masked.text
    assert {item["source"] for item in masked.json()} == {"meeting", "task_due"}
    assert secret_name not in masked.text
    assert "0001234-37.2026.8.07.0001" not in masked.text
    assert "meet.example.com/segredo" not in masked.text
    task_event = next(item for item in masked.json() if item["source"] == "task_due")
    assert task_event["clientLabel"].startswith("Lead ")

    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == auth_context.membership_id,
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
        )
    )
    assert membership is not None
    membership.permission_denials = [
        {"module": "tasks", "action": "view", "scope": "workspace"}
    ]
    await db_session.commit()

    without_tasks = await client.get("/api/v1/agenda", params=params)
    assert without_tasks.status_code == 200, without_tasks.text
    assert [item["source"] for item in without_tasks.json()] == ["meeting"]
