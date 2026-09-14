"""`conversion_events_service` — registrador único dos marcos do funil."""

from __future__ import annotations

import pytest

from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.services.conversion_events_service import (
    UnknownFunnelMilestoneError,
    canonical_event_name,
    record_conversion_event,
)


def test_canonical_event_name_maps_all_four_milestones() -> None:
    assert canonical_event_name("lead_qualificado") == "lead_qualified"
    assert canonical_event_name("reuniao_agendada") == "meeting_booked"
    assert canonical_event_name("contrato_assinado") == "contract_created"
    assert canonical_event_name("cobranca_paga") == "payment_confirmed"


def test_unknown_milestone_raises() -> None:
    with pytest.raises(UnknownFunnelMilestoneError):
        canonical_event_name("marco_inexistente")


async def _matter(db_session, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente marco")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Marco de funil",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


@pytest.mark.asyncio
async def test_record_conversion_event_is_idempotent_per_matter(db_session, auth_context):
    matter = await _matter(db_session, auth_context.workspace_id)

    first, first_reused = await record_conversion_event(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=None,
        marco="lead_qualificado",
        matter_id=matter.id,
        client_id=matter.client_id,
        channel="whatsapp",
    )
    second, second_reused = await record_conversion_event(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=None,
        marco="lead_qualificado",
        matter_id=matter.id,
        client_id=matter.client_id,
        channel="whatsapp",
    )

    assert first_reused is False
    assert second_reused is True
    assert first.id == second.id
    assert first.event_name == "lead_qualified"
    assert first.dedupe_key == f"conversion:lead_qualificado:{matter.id}"


@pytest.mark.asyncio
async def test_record_conversion_event_dedupe_suffix_allows_multiple_per_matter(
    db_session, auth_context
):
    matter = await _matter(db_session, auth_context.workspace_id)

    first, _ = await record_conversion_event(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=None,
        marco="reuniao_agendada",
        matter_id=matter.id,
        client_id=matter.client_id,
        channel="agenda",
        dedupe_suffix="meeting-1",
    )
    second, _ = await record_conversion_event(
        db_session,
        workspace_id=auth_context.workspace_id,
        user_id=None,
        marco="reuniao_agendada",
        matter_id=matter.id,
        client_id=matter.client_id,
        channel="agenda",
        dedupe_suffix="meeting-2",
    )

    assert first.id != second.id
    assert first.dedupe_key == f"conversion:reuniao_agendada:{matter.id}:meeting-1"
    assert second.dedupe_key == f"conversion:reuniao_agendada:{matter.id}:meeting-2"
