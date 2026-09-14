from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
)
from app.services.whatsapp.connection_health_service import WhatsAppConnectionHealthService


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _make_connection(db_session, workspace_id):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        transport_mode="official_api",
        is_default=True,
        phone_number_id="pn_health_api_1",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    await db_session.commit()
    return connection


async def _make_sent_recipients(db_session, *, workspace_id, connection, count, sent_at):
    """Semeia destinatários já 'sent' dentro da janela de 24h: a quota real
    (W1.5) é fonte de query, não mais o cache `register_recipients`."""
    campaign = WhatsAppCampaign(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        title=f"Campanha saúde API {uuid4().hex[:8]}",
        status=CampaignStatus.completed,
    )
    db_session.add(campaign)
    await db_session.flush()
    for index in range(count):
        db_session.add(
            WhatsAppCampaignRecipient(
                campaign_id=campaign.id,
                workspace_id=workspace_id,
                phone=f"+55119{index:08d}",
                status=CampaignRecipientStatus.sent,
                sent_at=sent_at,
                send_context={},
            )
        )
    await db_session.commit()
    return campaign


@pytest.mark.asyncio
async def test_get_connection_health_returns_local_data_with_honest_flag(client, db_session, auth_context, monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp.connection_health_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)

    response = await client.get(f"/api/v1/whatsapp/connections/{connection.id}/health")

    assert response.status_code == 200
    data = response.json()
    assert data["connectionId"] == str(connection.id)
    assert data["tier"] == "tier_250"
    assert data["dailyLimit"] == 250
    assert data["dailyUsed"] == 0
    assert data["dailyRemaining"] == 250
    assert data["qualityRating"] is None
    assert data["rampUpStage"] == 0
    assert data["syncedFromMeta"] is False
    assert data["honest"] is True


@pytest.mark.asyncio
async def test_get_connection_health_reflects_registered_recipients_and_quality(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.connection_health_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)

    await _make_sent_recipients(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        count=120,
        sent_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    health_service = WhatsAppConnectionHealthService(db_session)
    await health_service.set_quality_rating(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        quality_rating="amarelo",
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/whatsapp/connections/{connection.id}/health")

    assert response.status_code == 200
    data = response.json()
    assert data["dailyUsed"] == 120
    assert data["dailyRemaining"] == 130
    assert data["qualityRating"] == "amarelo"


@pytest.mark.asyncio
async def test_get_connection_health_returns_404_for_unknown_connection(client):
    response = await client.get(f"/api/v1/whatsapp/connections/{uuid4()}/health")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "WA-HEALTH-004"


@pytest.mark.asyncio
async def test_get_connection_health_returns_404_for_connection_of_other_workspace(
    client, db_session
):
    other_workspace_id = uuid4()
    connection = WhatsAppConnection(
        workspace_id=other_workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        transport_mode="official_api",
        is_default=True,
        phone_number_id="pn_health_api_other_ws",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    await db_session.commit()

    response = await client.get(f"/api/v1/whatsapp/connections/{connection.id}/health")
    assert response.status_code == 404
