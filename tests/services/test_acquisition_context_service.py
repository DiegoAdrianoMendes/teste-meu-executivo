from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.ai.context_compiler import ContextCompiler, ContextEvidence, ContextProvenance
from app.models.campaign_attribution import CampaignAttribution
from app.models.client import Client
from app.models.klm_operations import AcquisitionEvent
from app.models.matter import Matter, PracticeArea
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.acquisition_context_service import (
    ACQUISITION_CONTEXT_QUERY_LIMIT,
    AcquisitionContextService,
)


def _event(
    *,
    workspace_id,
    dedupe_key: str,
    happened_at: datetime,
    matter_id=None,
    client_id=None,
    event_name: str = "Lead",
    source: str = "meta_ads",
    channel: str = "instagram",
):
    return AcquisitionEvent(
        workspace_id=workspace_id,
        matter_id=matter_id,
        client_id=client_id,
        event_name=event_name,
        channel=channel,
        source=source,
        medium="paid_social",
        campaign="Negativa de plano",
        dedupe_key=dedupe_key,
        happened_at=happened_at,
        currency="BRL",
        click_ids={"fbclid": "sensitive-click-value"},
        event_metadata={
            "providerReferences": {"ad_id": "ad-123", "campaign_id": "cmp-9"},
            "phone_number": "+5511999999999",
        },
    )


@pytest.mark.asyncio
async def test_acquisition_context_is_tenant_and_matter_scoped(db_session, auth_context):
    person = Client(workspace_id=auth_context.workspace_id, name="Lead Aquisição")
    db_session.add(person)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=person.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Medicamento de alto custo",
        lead_source="indicação manual",
        manual_context_revision=4,
    )
    other_matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=person.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Outra demanda",
    )
    db_session.add_all([matter, other_matter])
    await db_session.flush()
    attribution = CampaignAttribution(
        matter_id=matter.id,
        source="google_ads",
        medium="cpc",
        campaign="Busca liminar",
        channel="google_search",
        first_touch_at="2026-07-18T12:00:00Z",
        last_touch_at="2026-07-20T12:00:00Z",
    )
    db_session.add(attribution)

    foreign_workspace = Workspace(
        name="Conta Externa",
        slug=f"conta-externa-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_person = Client(workspace_id=foreign_workspace.id, name="Lead Externo")
    db_session.add(foreign_person)
    await db_session.flush()
    foreign_matter = Matter(
        workspace_id=foreign_workspace.id,
        client_id=foreign_person.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Não pode vazar",
    )
    db_session.add(foreign_matter)
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            _event(
                workspace_id=auth_context.workspace_id,
                matter_id=matter.id,
                client_id=person.id,
                dedupe_key="matter-event",
                happened_at=now - timedelta(minutes=3),
            ),
            _event(
                workspace_id=auth_context.workspace_id,
                client_id=person.id,
                dedupe_key="unbound-client-event",
                happened_at=now - timedelta(minutes=2),
                event_name="Schedule",
            ),
            _event(
                workspace_id=auth_context.workspace_id,
                matter_id=other_matter.id,
                client_id=person.id,
                dedupe_key="other-matter-event",
                happened_at=now - timedelta(minutes=1),
                event_name="DoNotLeakAcrossMatters",
            ),
            _event(
                workspace_id=foreign_workspace.id,
                matter_id=foreign_matter.id,
                client_id=foreign_person.id,
                dedupe_key="foreign-event",
                happened_at=now,
                event_name="DoNotLeakAcrossTenants",
            ),
        ]
    )
    await db_session.flush()

    snapshot = await AcquisitionContextService(db_session).build(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        client_id=person.id,
        declared_source=matter.lead_source,
        campaign_attribution=attribution,
        manual_context_revision=matter.manual_context_revision,
    )

    assert snapshot.event_count == 2
    assert snapshot.card_data["primary_source"] == "indicação manual"
    assert snapshot.card_data["manual_context_revision"] == 4
    assert snapshot.card_data["measured"]["sources"] == ["google_ads", "meta_ads"]
    evidence = json.loads(snapshot.evidence_content or "{}")
    assert [event["event_name"] for event in evidence["journey"]] == ["Lead", "Schedule"]
    assert evidence["journey"][0]["click_id_providers"] == ["fbclid"]
    assert evidence["journey"][0]["metadata"] == {
        "adId": "ad-123",
        "campaignId": "cmp-9",
    }
    assert "sensitive-click-value" not in (snapshot.evidence_content or "")
    assert "+5511999999999" not in (snapshot.evidence_content or "")
    assert "DoNotLeak" not in (snapshot.evidence_content or "")


@pytest.mark.asyncio
async def test_acquisition_context_is_bounded_and_changes_compiled_digest(
    db_session,
    auth_context,
):
    person = Client(workspace_id=auth_context.workspace_id, name="Lead Limite")
    db_session.add(person)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=person.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Tratamento",
    )
    db_session.add(matter)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all(
        [
            _event(
                workspace_id=auth_context.workspace_id,
                matter_id=matter.id,
                client_id=person.id,
                dedupe_key=f"bounded-{index}",
                happened_at=now + timedelta(seconds=index),
                event_name=f"event-{index}",
            )
            for index in range(ACQUISITION_CONTEXT_QUERY_LIMIT + 4)
        ]
    )
    await db_session.flush()

    snapshot = await AcquisitionContextService(db_session).build(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        client_id=person.id,
        declared_source=None,
        campaign_attribution=None,
    )
    assert snapshot.event_count == ACQUISITION_CONTEXT_QUERY_LIMIT
    evidence = ContextEvidence(
        content=snapshot.evidence_content or "sem eventos",
        title="Jornada de aquisição",
        rank=85,
        provenance=ContextProvenance(
            workspace_id=str(auth_context.workspace_id),
            matter_id=str(matter.id),
            source_type="acquisition_journey",
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
        ),
    )
    compiler = ContextCompiler()
    without_tracking = compiler.compile(
        workspace_id=str(auth_context.workspace_id),
        matter_id=str(matter.id),
        card={"id": str(matter.id)},
    )
    with_tracking = compiler.compile(
        workspace_id=str(auth_context.workspace_id),
        matter_id=str(matter.id),
        card={"id": str(matter.id), "acquisition_context": snapshot.card_data},
        evidence=[evidence],
    )
    assert with_tracking.digest != without_tracking.digest
    assert "event-19" in (snapshot.evidence_content or "")
    assert "event-0" not in (snapshot.evidence_content or "")
