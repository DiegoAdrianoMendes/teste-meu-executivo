"""
Testes do endpoint GET /platform/meta-health (Sprint "Super Admin enxerga tudo").

Cobre:
- 403 para usuário não-staff (sem is_platform_admin);
- agregação correta com dados semeados: campanha pausada com motivo lido do
  evento campaign_auto_paused, top erros Meta por código, funil de eventos
  (24h/7d) e supressões por motivo;
- filtro por workspace_id isolando os dados de outro workspace.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import AcquisitionEvent
from app.models.whatsapp import WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
    WhatsAppSuppressionEntry,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

pytestmark = pytest.mark.asyncio


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"meta-health-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


async def _seed_scenario(db_session: AsyncSession):
    workspace = _workspace("Meta Health WS")
    other_workspace = _workspace("Meta Health WS Outro")
    db_session.add_all([workspace, other_workspace])
    await db_session.flush()

    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        phone_number_id=f"phone-{uuid4().hex[:10]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()

    # Campanha pausada pelo kill switch (evento de auditoria grava o motivo).
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title="Campanha pausada por política",
        status=CampaignStatus.paused,
        paused_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add(campaign)
    await db_session.flush()

    recipient_failed = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=workspace.id,
        phone="+5561999990001",
        status=CampaignRecipientStatus.failed,
        last_error="[368] Conta com restrição de envio.",
        send_context={},
    )
    recipient_failed_2 = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=workspace.id,
        phone="+5561999990002",
        status=CampaignRecipientStatus.failed,
        last_error="[368] Conta com restrição de envio.",
        send_context={},
    )
    recipient_failed_3 = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=workspace.id,
        phone="+5561999990003",
        status=CampaignRecipientStatus.failed,
        last_error="[131047] Fora da janela de 24h; exige template.",
        send_context={},
    )
    db_session.add_all([recipient_failed, recipient_failed_2, recipient_failed_3])
    await db_session.flush()

    db_session.add(
        WhatsAppCampaignDeliveryEvent(
            campaign_id=campaign.id,
            recipient_id=recipient_failed.id,
            wamid=None,
            status=CampaignRecipientStatus.failed,
            payload={
                "event": "campaign_auto_paused",
                "pauseReason": (
                    "Kill switch automático: taxa de falha de 100% excedeu o limite."
                ),
                "providerCode": 368,
            },
            occurred_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )

    # Funil de aquisição: 1 evento nas últimas 24h, 1 fora da janela de 24h
    # mas dentro dos 7 dias.
    db_session.add_all(
        [
            AcquisitionEvent(
                workspace_id=workspace.id,
                event_name="LeadSubmitted",
                channel="whatsapp",
                dedupe_key=f"lead-{uuid4().hex}",
                happened_at=datetime.now(UTC) - timedelta(hours=2),
            ),
            AcquisitionEvent(
                workspace_id=workspace.id,
                event_name="QualifiedLead",
                channel="whatsapp",
                dedupe_key=f"qlead-{uuid4().hex}",
                happened_at=datetime.now(UTC) - timedelta(days=3),
            ),
        ]
    )

    db_session.add(
        WhatsAppSuppressionEntry(
            workspace_id=workspace.id,
            phone="+5561999990009",
            reason="opt_out",
            is_active=True,
        )
    )

    # Ruído de outro workspace: não deve aparecer quando filtrando por
    # workspace_id.
    other_connection = WhatsAppConnection(
        workspace_id=other_workspace.id,
        phone_number_id=f"phone-{uuid4().hex[:10]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(other_connection)
    await db_session.flush()
    other_campaign = WhatsAppCampaign(
        workspace_id=other_workspace.id,
        whatsapp_connection_id=other_connection.id,
        title="Campanha de outro workspace",
        status=CampaignStatus.paused,
        paused_at=datetime.now(UTC),
    )
    db_session.add(other_campaign)
    await db_session.flush()
    db_session.add(
        WhatsAppSuppressionEntry(
            workspace_id=other_workspace.id,
            phone="+5561999990099",
            reason="manual",
            is_active=True,
        )
    )

    await db_session.commit()
    return workspace, other_workspace, campaign


async def test_meta_health_requires_platform_staff(client):
    response = await client.get("/api/v1/platform/meta-health")
    assert response.status_code == 403


async def test_meta_health_aggregates_seeded_data(db_session, platform_client):
    workspace, _other_workspace, campaign = await _seed_scenario(db_session)

    response = await platform_client.get("/api/v1/platform/meta-health")
    assert response.status_code == 200
    payload = response.json()

    paused = payload["paused_campaigns"]
    assert any(item["campaign_id"] == str(campaign.id) for item in paused)
    seeded = next(item for item in paused if item["campaign_id"] == str(campaign.id))
    assert seeded["workspace_id"] == str(workspace.id)
    assert "Kill switch" in seeded["pause_reason"]
    assert seeded["provider_code"] == 368
    assert seeded["paused_by_policy"] is True

    top_errors = {row["provider_code"]: row["count"] for row in payload["top_meta_errors"]}
    assert top_errors["368"] == 2
    assert top_errors["131047"] == 1

    funnel_24h = {row["event_name"]: row["count"] for row in payload["funnel_last_24h"]}
    funnel_7d = {row["event_name"]: row["count"] for row in payload["funnel_last_7d"]}
    assert funnel_24h.get("LeadSubmitted") == 1
    assert "QualifiedLead" not in funnel_24h
    assert funnel_7d.get("LeadSubmitted") == 1
    assert funnel_7d.get("QualifiedLead") == 1

    suppressions = {row["reason"]: row["count"] for row in payload["suppressions_by_reason"]}
    assert suppressions.get("opt_out") == 1

    assert payload["data_notes"]


async def test_meta_health_filters_by_workspace(db_session, platform_client):
    workspace, other_workspace, campaign = await _seed_scenario(db_session)

    response = await platform_client.get(
        "/api/v1/platform/meta-health",
        params={"workspaceId": str(other_workspace.id)},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["workspace_id"] == str(other_workspace.id)
    assert all(item["workspace_id"] == str(other_workspace.id) for item in payload["paused_campaigns"])
    assert not payload["top_meta_errors"]
    suppressions = {row["reason"]: row["count"] for row in payload["suppressions_by_reason"]}
    assert suppressions == {"manual": 1}


async def test_meta_health_surfaces_capi_feedback_receipts(db_session, platform_client):
    """Super Admin visibility (diretriz 7): recibos CAPI simulados/reais devem
    aparecer sem acessar o banco diretamente."""
    from app.models.conversion_feedback import AdsConversionFeedbackRecord
    from app.models.klm_operations import (
        ExternalActionIntent,
        ExternalActionIntentStatus,
    )

    workspace = _workspace("Meta Health CAPI WS")
    db_session.add(workspace)
    await db_session.flush()
    intent = ExternalActionIntent(
        workspace_id=workspace.id,
        action_type="ads_conversion_feedback",
        provider="meta_ads",
        idempotency_key=f"ads-conversion-feedback:meta_ads:{uuid4()}",
        status=ExternalActionIntentStatus.blocked_external,
        payload={"schemaVersion": "klm.ads.conversion-feedback.v2"},
        reason="teste",
    )
    db_session.add(intent)
    await db_session.flush()
    db_session.add(
        AdsConversionFeedbackRecord(
            workspace_id=workspace.id,
            intent_id=intent.id,
            provider="meta_ads",
            record_type="provider_receipt",
            state="blocked",
            attempt_number=0,
            provider_event_id="klm-abc",
            idempotency_key=intent.idempotency_key,
            payload_sha256="c" * 64,
            request_envelope={},
            provider_receipt={"simulated": True, "reason": "gate fechado"},
        )
    )
    await db_session.commit()

    response = await platform_client.get(
        "/api/v1/platform/meta-health",
        params={"workspaceId": str(workspace.id)},
    )
    assert response.status_code == 200
    payload = response.json()
    capi = payload["capi_feedback"]
    assert capi["total_receipts"] == 1
    assert capi["simulated_blocked"] == 1
    assert capi["accepted"] == 0
    assert capi["by_provider"]["meta_ads"]["blocked"] == 1


async def test_meta_health_surfaces_template_counts_by_workspace(db_session, platform_client):
    """W2.2 (diretriz 7): rejeitados/pausados/recategorizados por workspace,
    visíveis ao Super Admin sem acessar o banco."""
    from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate

    workspace = _workspace("Meta Health Templates WS")
    db_session.add(workspace)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        phone_number_id=f"phone-{uuid4().hex[:10]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()

    db_session.add_all(
        [
            WhatsAppMetaTemplate(
                workspace_id=workspace.id,
                whatsapp_connection_id=connection.id,
                external_template_id="ext-rej-1",
                name="rejeitado_1",
                language="pt_BR",
                category="utility",
                status=MetaTemplateStatus.rejected,
                components=[],
                raw_payload={"rejected_reason": "INVALID_FORMAT"},
            ),
            WhatsAppMetaTemplate(
                workspace_id=workspace.id,
                whatsapp_connection_id=connection.id,
                external_template_id="ext-paused-1",
                name="pausado_1",
                language="pt_BR",
                category="utility",
                status=MetaTemplateStatus.paused,
                components=[],
                raw_payload={},
            ),
            WhatsAppMetaTemplate(
                workspace_id=workspace.id,
                whatsapp_connection_id=connection.id,
                external_template_id="ext-recat-1",
                name="recategorizado_1",
                language="pt_BR",
                category="marketing",
                status=MetaTemplateStatus.approved,
                components=[],
                raw_payload={"previousCategory": "utility"},
            ),
        ]
    )
    await db_session.commit()

    response = await platform_client.get(
        "/api/v1/platform/meta-health",
        params={"workspaceId": str(workspace.id)},
    )
    assert response.status_code == 200
    payload = response.json()
    templates_health = payload["templates_health"]
    assert templates_health["totals"] == {
        "rejected": 1,
        "paused": 1,
        "disabled": 0,
        "recategorized": 1,
    }
    by_workspace = {row["workspace_id"]: row for row in templates_health["by_workspace"]}
    assert by_workspace[str(workspace.id)]["rejected"] == 1
    assert by_workspace[str(workspace.id)]["paused"] == 1
    assert by_workspace[str(workspace.id)]["recategorized"] == 1
