import pytest

from app.models.matter import PracticeArea
from app.services.sdr_intake_service import _create_lead_client_and_matter


@pytest.mark.asyncio
async def test_generic_whatsapp_intake_stays_in_general_triage(
    db_session,
    auth_context,
):
    _client, matter = await _create_lead_client_and_matter(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Lead sem classificação",
        lead_source="whatsapp",
        demand_type="A definir — lead via WhatsApp",
    )

    assert matter.legal_area_key == "triagem_geral"
    assert matter.practice_area == PracticeArea.civel


@pytest.mark.asyncio
async def test_intake_uses_deterministic_area_when_demand_has_evidence(
    db_session,
    auth_context,
):
    _client, matter = await _create_lead_client_and_matter(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Lead trabalhista",
        lead_source="form",
        demand_type="Consulta trabalhista sobre horas extras e FGTS",
    )

    assert matter.legal_area_key == "trabalhista"
    assert matter.practice_area == PracticeArea.trabalhista
