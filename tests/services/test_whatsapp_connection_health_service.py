from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

import app.services.whatsapp.connection_health_service as connection_health_service_module
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
    WhatsAppConnectionTier,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
)
from app.services.whatsapp.connection_health_service import (
    ConnectionHealthError,
    WhatsAppConnectionHealthService,
)
from app.services.whatsapp.errors import WhatsAppProviderError


class _ConnectionTokenStub:
    async def resolve_meta_access_token(self, _connection):
        return "provider-token"


class _MetaHealthStub:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error
        self.calls = []

    async def get_phone_number_details(self, phone_number_id, access_token):
        self.calls.append((phone_number_id, access_token))
        if self.error:
            raise self.error
        return self.payload


async def _make_connection(db_session, workspace_id):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        transport_mode="official_api",
        is_default=True,
        phone_number_id="pn_health_1",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


async def _make_sent_recipients(
    db_session,
    *,
    workspace_id,
    connection,
    phones: list[str],
    sent_at,
):
    """Cria uma campanha com recipients já 'sent' em `sent_at`, para
    exercitar a janela deslizante real (fonte da verdade da quota)."""
    campaign = WhatsAppCampaign(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection.id,
        title=f"Campanha saúde {uuid4().hex[:8]}",
        status=CampaignStatus.completed,
    )
    db_session.add(campaign)
    await db_session.flush()
    for phone in phones:
        db_session.add(
            WhatsAppCampaignRecipient(
                campaign_id=campaign.id,
                workspace_id=workspace_id,
                phone=phone,
                status=CampaignRecipientStatus.sent,
                sent_at=sent_at,
                send_context={},
            )
        )
    await db_session.flush()
    return campaign


@pytest.mark.asyncio
async def test_get_or_create_tier_defaults_to_tier_250(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)

    record = await service.get_or_create_tier(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )

    assert record.tier == WhatsAppConnectionTier.tier_250
    assert record.daily_unique_recipients == 0
    assert record.ramp_up_stage == 0
    assert record.quality_rating is None

    # Idempotente: chamar de novo retorna o mesmo registro, não duplica.
    record_again = await service.get_or_create_tier(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )
    assert record_again.id == record.id


@pytest.mark.asyncio
async def test_get_or_create_tier_rejects_connection_from_other_workspace(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    await service.get_or_create_tier(workspace_id=auth_context.workspace_id, connection_id=connection.id)

    with pytest.raises(ConnectionHealthError) as excinfo:
        await service.get_or_create_tier(workspace_id=uuid4(), connection_id=connection.id)
    assert excinfo.value.code == "WA-HEALTH-001"


@pytest.mark.asyncio
async def test_register_recipients_accumulates_within_window(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    base = datetime.now(timezone.utc)

    await service.register_recipients(
        workspace_id=auth_context.workspace_id, connection_id=connection.id, count=100, when=base
    )
    record = await service.register_recipients(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        count=50,
        when=base + timedelta(hours=2),
    )

    assert record.daily_unique_recipients == 150


@pytest.mark.asyncio
async def test_register_recipients_resets_after_rolling_window_expires(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    base = datetime.now(timezone.utc)

    await service.register_recipients(
        workspace_id=auth_context.workspace_id, connection_id=connection.id, count=200, when=base
    )
    record = await service.register_recipients(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        count=30,
        when=base + timedelta(hours=25),
    )

    # Janela expirou: o contador reseta e soma só o novo lote.
    assert record.daily_unique_recipients == 30
    assert record.window_started_at == base + timedelta(hours=25)


@pytest.mark.asyncio
async def test_register_recipients_rejects_negative_count(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)

    with pytest.raises(ConnectionHealthError) as excinfo:
        await service.register_recipients(
            workspace_id=auth_context.workspace_id, connection_id=connection.id, count=-1
        )
    assert excinfo.value.code == "WA-HEALTH-002"


@pytest.mark.asyncio
async def test_remaining_daily_quota_maps_tier_to_limit(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    record = await service.get_or_create_tier(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )
    record.tier = WhatsAppConnectionTier.tier_1k
    await db_session.flush()

    now = datetime.now(timezone.utc)
    await _make_sent_recipients(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        phones=[f"+55119{i:08d}" for i in range(400)],
        sent_at=now - timedelta(hours=1),
    )
    quota = await service.remaining_daily_quota(
        workspace_id=auth_context.workspace_id, connection_id=connection.id, now=now
    )

    assert quota["tier"] == WhatsAppConnectionTier.tier_1k
    assert quota["limit"] == 1_000
    assert quota["used"] == 400
    assert quota["remaining"] == 600


@pytest.mark.asyncio
async def test_remaining_daily_quota_unlimited_tier_has_no_limit(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    record = await service.get_or_create_tier(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )
    record.tier = WhatsAppConnectionTier.unlimited
    await db_session.flush()

    quota = await service.remaining_daily_quota(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )
    assert quota["limit"] is None
    assert quota["remaining"] is None


@pytest.mark.asyncio
async def test_set_quality_rating_validates_allowed_values(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)

    with pytest.raises(ConnectionHealthError) as excinfo:
        await service.set_quality_rating(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            quality_rating="azul",
        )
    assert excinfo.value.code == "WA-HEALTH-003"


@pytest.mark.asyncio
async def test_set_quality_rating_persists_and_writes_audit_event(db_session, auth_context):
    from sqlalchemy import select

    from app.models.klm_operations import KlmAuditEvent

    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)

    record = await service.set_quality_rating(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        quality_rating="verde",
        actor_label="Teste",
    )
    await db_session.commit()

    assert record.quality_rating == "verde"
    assert record.last_synced_at is not None

    result = await db_session.execute(
        select(KlmAuditEvent).where(
            KlmAuditEvent.event_type == "whatsapp_connection_quality_rating_updated"
        )
    )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].after_data == {"qualityRating": "verde"}


@pytest.mark.asyncio
async def test_sync_from_provider_stub_is_honest_when_gate_closed(db_session, auth_context, monkeypatch):
    monkeypatch.setattr(
        connection_health_service_module, "external_delivery_allowed", lambda: False
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)

    result = await service.sync_from_provider_stub(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )

    assert result["synced"] is False
    assert result["reason"] == "external_delivery_disabled"


@pytest.mark.asyncio
async def test_sync_from_provider_maps_real_meta_quality_and_tier(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        connection_health_service_module, "external_delivery_allowed", lambda: True
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    client = _MetaHealthStub(
        {"quality_rating": "GREEN", "messaging_limit_tier": "TIER_10K"}
    )
    service = WhatsAppConnectionHealthService(
        db_session,
        meta_client=client,
        connection_service=_ConnectionTokenStub(),
    )

    result = await service.sync_from_provider(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )

    assert result["synced"] is True
    assert result["reason"] == "provider_confirmed"
    assert result["record"].quality_rating == "verde"
    assert result["record"].tier == WhatsAppConnectionTier.tier_10k
    assert result["record"].last_synced_at is not None
    assert client.calls == [("pn_health_1", "provider-token")]


@pytest.mark.asyncio
async def test_sync_from_provider_preserves_local_state_on_sanitized_provider_failure(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        connection_health_service_module, "external_delivery_allowed", lambda: True
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    client = _MetaHealthStub(
        error=WhatsAppProviderError(
            code="META-400",
            message="provider response containing sensitive diagnostics",
            status_code=400,
        )
    )
    service = WhatsAppConnectionHealthService(
        db_session,
        meta_client=client,
        connection_service=_ConnectionTokenStub(),
    )
    record = await service.get_or_create_tier(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
    )
    record.tier = WhatsAppConnectionTier.tier_1k
    record.quality_rating = "amarelo"

    result = await service.sync_from_provider(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
    )

    assert result["synced"] is False
    assert result["reason"] == "provider_unavailable"
    assert "sensitive diagnostics" not in result["message"]
    assert result["record"].tier == WhatsAppConnectionTier.tier_1k
    assert result["record"].quality_rating == "amarelo"


@pytest.mark.asyncio
async def test_get_health_returns_consolidated_payload(db_session, auth_context, monkeypatch):
    monkeypatch.setattr(
        connection_health_service_module, "external_delivery_allowed", lambda: False
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    await _make_sent_recipients(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        phones=[f"+55119{i:08d}" for i in range(10)],
        sent_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    payload = await service.get_health(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )

    assert payload["connectionId"] == connection.id
    assert payload["tier"] == WhatsAppConnectionTier.tier_250
    assert payload["dailyLimit"] == 250
    assert payload["dailyUsed"] == 10
    assert payload["dailyRemaining"] == 240
    assert payload["syncedFromMeta"] is False
    assert payload["honest"] is True


@pytest.mark.asyncio
async def test_get_health_raises_for_unknown_connection(db_session, auth_context):
    service = WhatsAppConnectionHealthService(db_session)
    with pytest.raises(ConnectionHealthError) as excinfo:
        await service.get_health(workspace_id=auth_context.workspace_id, connection_id=uuid4())
    assert excinfo.value.code == "WA-HEALTH-004"


# ── Janela deslizante real (W1.5, revisão pós-auditoria) ────────────────────


@pytest.mark.asyncio
async def test_sliding_window_does_not_allow_2x_at_boundary(db_session, auth_context):
    """
    Cenário do revisor: 250 destinatários enviados às 23h de ontem +
    tentativa de mais 250 às 10h de hoje. A janela deslizante real conta os
    de ontem (ainda <24h de distância) e barra a nova rodada — o contador
    tumbling antigo resetaria a janela e permitiria os 500 no mesmo dia.
    """
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    record = await service.get_or_create_tier(
        workspace_id=auth_context.workspace_id, connection_id=connection.id
    )
    record.tier = WhatsAppConnectionTier.tier_250
    await db_session.flush()

    yesterday_23h = datetime(2026, 7, 22, 23, 0, tzinfo=timezone.utc)
    today_10h = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)
    assert today_10h - yesterday_23h < timedelta(hours=24)

    await _make_sent_recipients(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        phones=[f"+55119{i:08d}" for i in range(250)],
        sent_at=yesterday_23h,
    )

    quota = await service.remaining_daily_quota(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        now=today_10h,
    )

    assert quota["used"] == 250
    assert quota["remaining"] == 0

    usage = await service.sliding_window_usage(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        now=today_10h,
    )
    assert usage["used"] == 250


@pytest.mark.asyncio
async def test_sliding_window_excludes_events_older_than_24h(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    now = datetime.now(timezone.utc)

    await _make_sent_recipients(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        phones=["+5511900000001"],
        sent_at=now - timedelta(hours=25),
    )

    usage = await service.sliding_window_usage(
        workspace_id=auth_context.workspace_id, connection_id=connection.id, now=now
    )
    assert usage["used"] == 0


@pytest.mark.asyncio
async def test_sliding_window_dedups_same_phone_across_sources(db_session, auth_context):
    """Mesmo telefone enviado 2x (campanha + avulso) conta 1 destinatário único."""
    from app.models.whatsapp import (
        WaMessageDirection,
        WaMessageStatus,
        WhatsAppContact,
        WhatsAppMessage,
    )

    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    now = datetime.now(timezone.utc)
    phone = "+5511900000002"

    await _make_sent_recipients(
        db_session,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        phones=[phone],
        sent_at=now - timedelta(hours=1),
    )

    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone=phone,
    )
    db_session.add(contact)
    await db_session.flush()
    db_session.add(
        WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=connection.id,
            wamid=f"wamid-{uuid4().hex[:10]}",
            direction=WaMessageDirection.outbound,
            status=WaMessageStatus.sent,
            body="mensagem avulsa",
            timestamp=now - timedelta(minutes=30),
        )
    )
    await db_session.flush()

    usage = await service.sliding_window_usage(
        workspace_id=auth_context.workspace_id, connection_id=connection.id, now=now
    )
    assert usage["used"] == 1


@pytest.mark.asyncio
async def test_sliding_window_counts_standalone_message_recipients(db_session, auth_context):
    from app.models.whatsapp import (
        WaMessageDirection,
        WaMessageStatus,
        WhatsAppContact,
        WhatsAppMessage,
    )

    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = WhatsAppConnectionHealthService(db_session)
    now = datetime.now(timezone.utc)

    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="+5511900000003",
    )
    db_session.add(contact)
    await db_session.flush()
    db_session.add(
        WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=connection.id,
            wamid=f"wamid-{uuid4().hex[:10]}",
            direction=WaMessageDirection.outbound,
            status=WaMessageStatus.delivered,
            body="mensagem avulsa",
            timestamp=now - timedelta(hours=2),
        )
    )
    await db_session.flush()

    usage = await service.sliding_window_usage(
        workspace_id=auth_context.workspace_id, connection_id=connection.id, now=now
    )
    assert usage["used"] == 1
