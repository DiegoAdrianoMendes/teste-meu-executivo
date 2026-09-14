"""Agenda availability, public booking and meeting briefing contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.card_score import CardScore
from app.models.client import Client
from app.models.conversation_insight import ConversationInsight
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.matter import HealthSubtheme, Matter, PracticeArea
from app.models.meeting import Meeting
from app.schemas.agenda import (
    AvailabilityWindow,
    BookingLinkCreate,
    CalendarProvider,
    CalendarSettingsUpdate,
    DayAvailability,
)
from app.schemas.control_plane import MeetingCreate
from app.services.agenda_service import AgendaService
from app.services.meeting_service import MeetingService

pytestmark = pytest.mark.usefixtures("active_subscription")


def _open_week() -> list[DayAvailability]:
    return [
        DayAvailability(
            weekday=weekday,
            enabled=True,
            windows=[AvailabilityWindow(start="00:00", end="24:00")],
        )
        for weekday in range(7)
    ]


@pytest.mark.asyncio
async def test_public_booking_is_private_and_prevents_double_booking(
    db_session,
    auth_context,
    unauthenticated_client,
):
    service = AgendaService(db_session)
    await service.update_settings(
        auth_context.workspace_id,
        CalendarSettingsUpdate(
            provider=CalendarProvider.google_meet,
            default_meeting_url="https://meet.google.com/abc-defg-hij",
            default_duration_minutes=30,
            slot_interval_minutes=30,
            minimum_notice_hours=0,
            booking_horizon_days=2,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            weekly_availability=_open_week(),
        ),
        actor_user_id=auth_context.user_id,
        actor_label="Usuário Teste",
    )
    link = await service.create_booking_link(
        auth_context.workspace_id,
        BookingLinkCreate(
            title="Reunião inicial KLM",
            duration_minutes=30,
            max_uses=2,
        ),
        actor_user_id=auth_context.user_id,
        actor_label="Usuário Teste",
    )
    await db_session.commit()

    page = await unauthenticated_client.get(
        f"/api/v1/agenda/public/{link.booking_path.rsplit('/', 1)[-1]}"
    )
    assert page.status_code == 200
    payload = page.json()
    assert payload["officeName"] == "Workspace Teste"
    assert payload["meetingProvider"] == "google_meet"
    assert payload["slots"]
    assert "matterId" not in payload
    assert "client" not in payload

    requested_slot = payload["slots"][0]
    booked = await unauthenticated_client.post(
        f"/api/v1/agenda/public/{link.booking_path.rsplit('/', 1)[-1]}",
        json={
            "startsAt": requested_slot["startsAt"],
            "name": "Maria Cliente",
            "phone": "+5561999999999",
        },
    )
    assert booked.status_code == 201
    assert booked.json()["meetingUrl"] == "https://meet.google.com/abc-defg-hij"

    duplicate = await unauthenticated_client.post(
        f"/api/v1/agenda/public/{link.booking_path.rsplit('/', 1)[-1]}",
        json={
            "startsAt": requested_slot["startsAt"],
            "name": "Outro Lead",
            "email": "outro@example.com",
        },
    )
    assert duplicate.status_code == 409
    meetings = list((await db_session.scalars(select(Meeting))).all())
    assert len(meetings) == 1
    assert meetings[0].matter_id is None
    assert meetings[0].recipient_overrides[0]["name"] == "Maria Cliente"


@pytest.mark.asyncio
async def test_orphan_booking_can_be_linked_to_an_existing_card(
    client,
    db_session,
    auth_context,
):
    lead = Client(
        workspace_id=auth_context.workspace_id,
        name="Lead para vínculo",
        phone="+5561988888888",
    )
    db_session.add(lead)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=lead.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Negativa de medicamento",
        health_subtheme=HealthSubtheme.medicamento_alto_custo,
    )
    db_session.add(matter)
    await db_session.flush()
    starts_at = datetime.now(UTC) + timedelta(days=1)
    meeting = await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            title="Booking órfão",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=45),
            location="Reunião online",
        ),
    )
    await db_session.commit()

    linked = await client.patch(
        f"/api/v1/meetings/{meeting.id}",
        json={"matterId": str(matter.id), "expectedVersion": 1},
    )

    assert linked.status_code == 200
    assert linked.json()["matterId"] == str(matter.id)


@pytest.mark.asyncio
async def test_meeting_and_daily_briefings_use_real_card_context(
    client,
    db_session,
    auth_context,
):
    lead = Client(
        workspace_id=auth_context.workspace_id,
        name="Ana Saúde",
        phone="+5561977777777",
    )
    db_session.add(lead)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=lead.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Negativa de Home Care",
        health_subtheme=HealthSubtheme.home_care,
        health_object_name="Internação domiciliar 24 horas",
        health_insurer="Plano Exemplo",
        medical_urgency_hours=12,
        lead_score=91,
        executive_summary="Paciente recebeu negativa apesar da indicação médica.",
        next_action="Confirmar a data formal da negativa.",
    )
    db_session.add(matter)
    await db_session.flush()
    db_session.add_all(
        [
            ConversationInsight(
                workspace_id=auth_context.workspace_id,
                matter_id=matter.id,
                summary="Família precisa organizar a medida urgente e já enviou a negativa.",
                no_show_risk="high",
                objections=["Receio sobre o custo"],
                missing_information=["Data da prescrição"],
                recommended_action="Validar prescrição e relatório médico na reunião.",
            ),
            CardScore(
                matter_id=matter.id,
                overall_score=0.91,
                explanation="Caso prioritário pela urgência clínica e qualidade da prova.",
            ),
            Document(
                matter_id=matter.id,
                name="Negativa do plano",
                doc_type=DocumentType.comprovante,
                status=DocumentStatus.completo,
            ),
            Document(
                matter_id=matter.id,
                name="Relatório médico",
                doc_type=DocumentType.comprovante,
                status=DocumentStatus.parcial,
            ),
        ]
    )
    starts_at = datetime.now(UTC) + timedelta(hours=2)
    meeting = await MeetingService(db_session).create_for_workspace(
        auth_context.workspace_id,
        MeetingCreate(
            matter_id=matter.id,
            title="Reunião com Ana Saúde",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=45),
            location="Microsoft Teams",
            meeting_url="https://teams.microsoft.com/l/meetup-join/example",
        ),
    )
    await db_session.commit()

    briefing = await client.get(f"/api/v1/agenda/meetings/{meeting.id}/briefing")
    assert briefing.status_code == 200
    data = briefing.json()
    assert data["clientName"] == "Ana Saúde"
    assert data["documentCount"] == 2
    assert data["readyDocumentCount"] == 1
    assert data["noShowRisk"] == "high"
    assert data["generationMode"] == "local_context"
    assert data["meetingUrl"].startswith("https://teams.microsoft.com/")
    assert any("Urgência médica" in item for item in data["warnings"])

    day = starts_at.astimezone(ZoneInfo("America/Sao_Paulo")).date().isoformat()
    daily = await client.get("/api/v1/agenda/daily-briefing", params={"day": day})
    assert daily.status_code == 200
    daily_data = daily.json()
    assert daily_data["total"] == 1
    assert daily_data["linkedCount"] == 1
    assert daily_data["missingRoomCount"] == 0
    assert daily_data["items"][0]["clientName"] == "Ana Saúde"
    assert daily_data["items"][0]["noShowRisk"] == "high"


@pytest.mark.asyncio
async def test_internal_booking_uses_live_availability_and_matter_scoped_lists(
    client,
    db_session,
    auth_context,
):
    service = AgendaService(db_session)
    await service.update_settings(
        auth_context.workspace_id,
        CalendarSettingsUpdate(
            provider=CalendarProvider.google_meet,
            default_meeting_url="https://meet.google.com/live-slot-test",
            default_duration_minutes=30,
            slot_interval_minutes=30,
            minimum_notice_hours=0,
            booking_horizon_days=2,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            weekly_availability=_open_week(),
        ),
        actor_user_id=auth_context.user_id,
        actor_label="Usuário Teste",
    )
    clients = [
        Client(workspace_id=auth_context.workspace_id, name="Lead A"),
        Client(workspace_id=auth_context.workspace_id, name="Lead B"),
    ]
    db_session.add_all(clients)
    await db_session.flush()
    matters = [
        Matter(
            workspace_id=auth_context.workspace_id,
            client_id=lead.id,
            practice_area=PracticeArea.saude_suplementar,
            demand_type="Negativa de medicamento",
        )
        for lead in clients
    ]
    db_session.add_all(matters)
    await db_session.flush()
    await service.create_booking_link(
        auth_context.workspace_id,
        BookingLinkCreate(
            matter_id=matters[0].id,
            title="Escolha do lead A",
            duration_minutes=30,
        ),
        actor_user_id=auth_context.user_id,
        actor_label="Usuário Teste",
    )
    await service.create_booking_link(
        auth_context.workspace_id,
        BookingLinkCreate(
            matter_id=matters[1].id,
            title="Escolha do lead B",
            duration_minutes=30,
        ),
        actor_user_id=auth_context.user_id,
        actor_label="Usuário Teste",
    )
    await db_session.commit()

    availability = await client.get(
        "/api/v1/agenda/availability",
        params={"duration_minutes": 30},
    )
    assert availability.status_code == 200, availability.text
    preview = availability.json()
    assert preview["meetingProvider"] == "google_meet"
    assert preview["meetingUrl"] == "https://meet.google.com/live-slot-test"
    assert preview["slots"]

    selected = preview["slots"][0]
    created = await client.post(
        "/api/v1/agenda/internal-bookings",
        json={
            "matterId": str(matters[0].id),
            "title": "Reunião escolhida no chat",
            "startsAt": selected["startsAt"],
            "durationMinutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["matterId"] == str(matters[0].id)
    assert created.json()["meetingUrl"] == "https://meet.google.com/live-slot-test"

    duplicate = await client.post(
        "/api/v1/agenda/internal-bookings",
        json={
            "matterId": str(matters[1].id),
            "title": "Conflito real",
            "startsAt": selected["startsAt"],
            "durationMinutes": 30,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "BOOK-004"

    meetings = await client.get(
        "/api/v1/meetings",
        params={"matter_id": str(matters[0].id)},
    )
    assert meetings.status_code == 200
    assert [item["matterId"] for item in meetings.json()] == [str(matters[0].id)]

    links = await client.get(
        "/api/v1/agenda/booking-links",
        params={"matter_id": str(matters[0].id)},
    )
    assert links.status_code == 200
    assert len(links.json()) == 1
    assert links.json()[0]["matterId"] == str(matters[0].id)

    for index, matter in enumerate(matters):
        response = await client.post(
            "/api/v1/finance/entries",
            json={
                "matterId": str(matter.id),
                "title": f"Honorários {index}",
                "amount": 4000 + index,
            },
        )
        assert response.status_code == 201, response.text
    entries = await client.get(
        "/api/v1/finance/entries",
        params={"matter_id": str(matters[0].id)},
    )
    assert entries.status_code == 200
    assert len(entries.json()) == 1
    assert entries.json()[0]["matterId"] == str(matters[0].id)
