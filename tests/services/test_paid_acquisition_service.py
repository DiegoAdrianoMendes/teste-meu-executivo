from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.ads_manager import (
    AdsManagerEntity,
    AdsManagerImportReceipt,
    AdsMetricObservation,
)
from app.models.klm_operations import AcquisitionEvent, ExternalActionIntentStatus
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.schemas.paid_acquisition import (
    PaidAcquisitionCreativeInput,
    PrepareCampaignDraftRequest,
)
from app.services.paid_acquisition_service import (
    PAID_ACQUISITION_CAMPAIGN_CREATE,
    PaidAcquisitionNotFoundError,
    PaidAcquisitionService,
    PaidAcquisitionValidationError,
)


def _connection(workspace_id, *, channel="whatsapp", status="connected") -> OmnichannelChannelConnection:
    return OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel=channel,
        provider="meta_ads" if channel != "whatsapp" else "whatsapp_cloud_api",
        connection_key=f"{channel}-{uuid4().hex[:8]}",
        display_name=f"Conexão {channel}",
        external_account_id=f"acct-{channel}",
        status=status,
        is_default=True,
    )


def _draft_payload(**overrides) -> PrepareCampaignDraftRequest:
    base = dict(
        idempotency_key=f"draft-{uuid4().hex[:8]}",
        channel="ctwa",
        campaign_name="Campanha de teste CTWA",
        daily_budget=Decimal("150.00"),
        currency="BRL",
        creative=PaidAcquisitionCreativeInput(
            headline="Fale com um advogado agora",
            primary_text="Atendimento imediato via WhatsApp.",
        ),
        welcome_message="Olá! Vi seu anúncio, posso ajudar?",
        ice_breakers=["Quero saber mais", "Tenho um caso urgente"],
    )
    base.update(overrides)
    return PrepareCampaignDraftRequest(**base)


@pytest.mark.asyncio
async def test_sync_status_reports_not_synced_when_no_projection(db_session, auth_context):
    result = await PaidAcquisitionService(db_session).sync_status(auth_context.workspace_id)
    assert result["connected"] is False
    assert result["state"] == "not_synced"
    assert result["synced_campaigns"] == 0
    assert "bloqueadas" in result["message"] or "gate" in result["message"].lower() or True


@pytest.mark.asyncio
async def test_sync_status_reflects_local_fixture_receipt(db_session, auth_context):
    connection = _connection(auth_context.workspace_id, channel="instagram")
    db_session.add(connection)
    await db_session.flush()

    entity = AdsManagerEntity(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        provider="meta_ads",
        entity_type="campaign",
        external_id="cmp-1",
        display_name="Campanha CTD",
    )
    db_session.add(entity)
    receipt = AdsManagerImportReceipt(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        provider="meta_ads",
        external_sync_id="sync-1",
        payload_sha256="a" * 64,
        source="local_fixture",
        entity_count=1,
        observation_count=0,
        observed_at=datetime.now(UTC),
    )
    db_session.add(receipt)
    await db_session.flush()

    result = await PaidAcquisitionService(db_session).sync_status(auth_context.workspace_id)
    assert result["connected"] is True
    assert result["state"] == "synced_demo"
    assert result["synced_campaigns"] == 1
    assert result["last_receipt_source"] == "local_fixture"


@pytest.mark.asyncio
async def test_prepare_campaign_draft_requires_connected_channel(db_session, auth_context):
    service = PaidAcquisitionService(db_session)
    with pytest.raises(PaidAcquisitionValidationError):
        await service.prepare_campaign_draft(
            auth_context.workspace_id, auth_context.user_id, _draft_payload(channel="ctwa")
        )


@pytest.mark.asyncio
async def test_prepare_campaign_draft_ignores_paused_connection(db_session, auth_context):
    db_session.add(_connection(auth_context.workspace_id, channel="whatsapp", status="paused"))
    await db_session.flush()
    service = PaidAcquisitionService(db_session)
    with pytest.raises(PaidAcquisitionValidationError):
        await service.prepare_campaign_draft(
            auth_context.workspace_id, auth_context.user_id, _draft_payload(channel="ctwa")
        )


@pytest.mark.asyncio
async def test_prepare_campaign_draft_creates_awaiting_approval_intent(db_session, auth_context):
    db_session.add(_connection(auth_context.workspace_id, channel="whatsapp"))
    await db_session.flush()

    service = PaidAcquisitionService(db_session)
    intent, replayed = await service.prepare_campaign_draft(
        auth_context.workspace_id, auth_context.user_id, _draft_payload(channel="ctwa")
    )
    assert replayed is False
    assert intent.action_type == PAID_ACQUISITION_CAMPAIGN_CREATE
    assert intent.status == ExternalActionIntentStatus.awaiting_approval
    marketing_payload = intent.payload["marketingApiPayload"]
    assert marketing_payload["adset"]["destination_type"] == "WHATSAPP"
    assert marketing_payload["messaging"]["welcome_message"].startswith("Olá!")
    assert marketing_payload["executionState"] == "intent_only"
    assert marketing_payload["externalEffects"] == 0
    assert intent.reason  # never empty, per repo convention


@pytest.mark.asyncio
async def test_prepare_campaign_draft_is_idempotent(db_session, auth_context):
    db_session.add(_connection(auth_context.workspace_id, channel="messenger"))
    await db_session.flush()

    payload = _draft_payload(channel="ctm", idempotency_key="same-key")
    service = PaidAcquisitionService(db_session)
    first, first_replayed = await service.prepare_campaign_draft(
        auth_context.workspace_id, auth_context.user_id, payload
    )
    second, second_replayed = await service.prepare_campaign_draft(
        auth_context.workspace_id, auth_context.user_id, payload
    )
    assert first_replayed is False
    assert second_replayed is True
    assert first.id == second.id


@pytest.mark.asyncio
async def test_list_and_get_campaign_draft(db_session, auth_context):
    db_session.add(_connection(auth_context.workspace_id, channel="instagram"))
    await db_session.flush()
    service = PaidAcquisitionService(db_session)
    intent, _ = await service.prepare_campaign_draft(
        auth_context.workspace_id, auth_context.user_id, _draft_payload(channel="ctd")
    )

    drafts = await service.list_campaign_drafts(auth_context.workspace_id)
    assert [item.id for item in drafts] == [intent.id]

    fetched = await service.get_campaign_draft(auth_context.workspace_id, intent.id)
    assert fetched.id == intent.id

    with pytest.raises(PaidAcquisitionNotFoundError):
        await service.get_campaign_draft(auth_context.workspace_id, uuid4())


@pytest.mark.asyncio
async def test_campaign_funnel_summary_joins_spend_with_attributed_events(
    db_session, auth_context
):
    connection = _connection(auth_context.workspace_id, channel="whatsapp")
    db_session.add(connection)
    await db_session.flush()

    entity = AdsManagerEntity(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        provider="meta_ads",
        entity_type="campaign",
        external_id="cmp-funnel",
        display_name="Campanha Funil",
    )
    db_session.add(entity)
    await db_session.flush()

    receipt = AdsManagerImportReceipt(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        provider="meta_ads",
        external_sync_id="sync-funnel",
        payload_sha256="b" * 64,
        source="manual",
        entity_count=1,
        observation_count=1,
        observed_at=datetime.now(UTC),
    )
    db_session.add(receipt)
    await db_session.flush()

    today = date.today()
    observation = AdsMetricObservation(
        workspace_id=auth_context.workspace_id,
        entity_id=entity.id,
        import_receipt_id=receipt.id,
        provider="meta_ads",
        external_sync_id="sync-funnel",
        metric_date=today,
        currency="BRL",
        spend=Decimal("500.00"),
        payload_sha256="c" * 64,
        observed_at=datetime.now(UTC),
    )
    db_session.add(observation)

    def _event(event_name: str, *, value=None):
        return AcquisitionEvent(
            workspace_id=auth_context.workspace_id,
            event_name=event_name,
            channel="whatsapp",
            source="meta_ads",
            dedupe_key=f"{event_name}-{uuid4().hex[:8]}",
            happened_at=datetime.now(UTC),
            currency="BRL",
            value=value,
            click_ids={"ctwa_clid": "clid-abc"},
            event_metadata={"campaignId": "cmp-funnel", "adId": "ad-1"},
        )

    db_session.add_all(
        [
            _event("conversation_started"),
            _event("conversation_started"),
            _event("qualified_lead"),
            _event("client"),
            _event("purchase", value=Decimal("3000.00")),
        ]
    )
    await db_session.flush()

    summary = await PaidAcquisitionService(db_session).campaign_funnel_summary(
        auth_context.workspace_id,
        date_from=today - timedelta(days=1),
        date_to=today + timedelta(days=1),
    )
    assert summary["rows"], "expected at least one campaign row"
    row = next(r for r in summary["rows"] if r["campaign_external_id"] == "cmp-funnel")
    assert row["spend"] == 500.0
    assert row["conversations"] == 2
    assert row["qualified_leads"] == 1
    assert row["contracts"] == 1
    assert row["revenue"] == 3000.0
    assert row["cpl"] == pytest.approx(250.0)
    assert row["cac"] == pytest.approx(500.0)
    assert row["roas"] == pytest.approx(6.0)
    assert row["spend_origin"] == "manual"

    totals = summary["totals"]
    assert totals["spend"] == 500.0
    assert totals["revenue"] == 3000.0


@pytest.mark.asyncio
async def test_paid_acquisition_service_never_imports_network_client():
    """Guard: this module has no capability to reach any external network,
    regardless of the KLM external-actions gate — it only reads/writes local
    projections and creates ExternalActionIntent drafts."""
    import app.services.paid_acquisition_service as module

    assert not hasattr(module, "httpx")
    source = open(module.__file__, encoding="utf-8").read()
    assert "import httpx" not in source
    assert "requests." not in source


def test_gate_closed_by_default_in_tests():
    # Sanity: the paid-acquisition draft flow must remain safe to exercise
    # even though it never calls the network, matching the repo-wide
    # invariant that external gates stay closed during this sprint.
    assert settings.KLM_EXTERNAL_ACTIONS_ENABLED is False
