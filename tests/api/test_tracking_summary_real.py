"""Truthful tracking summaries backed only by persisted acquisition events."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.conversion_feedback import AdsConversionFeedbackRecord
from app.models.klm_operations import AcquisitionEvent, ExternalActionIntent
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _record_event(
    client,
    event_name: str,
    dedupe_key: str,
    *,
    channel: str = "google_search",
    value: float | None = None,
    happened_at: datetime | None = None,
    click_ids: dict[str, str] | None = None,
    event_metadata: dict | None = None,
    client_id: str | None = None,
):
    payload = {
        "eventName": event_name,
        "channel": channel,
        "dedupeKey": dedupe_key,
    }
    if value is not None:
        payload["value"] = value
    if happened_at is not None:
        payload["happenedAt"] = happened_at.isoformat()
    if click_ids is not None:
        payload["clickIds"] = click_ids
    if event_metadata is not None:
        payload["eventMetadata"] = event_metadata
    if client_id is not None:
        payload["clientId"] = client_id
    response = await client.post("/api/v1/klm/tracking/events", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_tracking_summary_is_empty_without_invented_rows(client):
    response = await client.get("/api/v1/klm/tracking/summary")

    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["periodDays"] is None
    assert summary["periodStart"] is None
    assert summary["totalEvents"] == 0
    assert summary["leads"] == 0
    assert summary["qualifiedLeads"] == 0
    assert summary["meetings"] == 0
    assert summary["clients"] == 0
    assert summary["adSpend"] == 0
    assert summary["attributableRevenue"] == 0
    assert summary["conversionRate"] is None
    assert summary["cac"] is None
    assert summary["roas"] is None
    assert summary["byChannel"] == []
    assert summary["timeTrend"] == []
    assert summary["journeys"] == []
    assert summary["remarketing"] == []
    assert summary["containsLocalFixtureData"] is False
    assert all(not item["covered"] and item["count"] == 0 for item in summary["eventCoverage"])


@pytest.mark.asyncio
async def test_tracking_summary_flags_local_fixture_data_but_not_manual_entries(client):
    connection_response = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "facebook",
            "provider": "meta_ads",
            "connectionKey": "meta-ads:fixture-flag",
            "displayName": "Meta Ads",
            "transportMode": "local_fixture",
            "configuration": {"mappingVersion": "v7"},
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    connection_id = connection_response.json()["id"]

    manual_response = await client.post(
        f"/api/v1/klm/omnichannel/connections/{connection_id}/ads-manager/snapshots",
        json={
            "externalSyncId": "manual:flag-check",
            "source": "manual",
            "observedAt": datetime.now(UTC).isoformat(),
            "entities": [
                {
                    "entityType": "campaign",
                    "externalId": "manual-flag-campaign",
                    "displayName": "Campanha manual",
                    "effectiveStatus": "active",
                    "metrics": [
                        {"metricDate": datetime.now(UTC).date().isoformat(), "spend": "500.00"}
                    ],
                }
            ],
        },
    )
    assert manual_response.status_code == 201, manual_response.text

    manual_only_summary = (await client.get("/api/v1/klm/tracking/summary")).json()
    assert manual_only_summary["containsLocalFixtureData"] is False

    fixture_response = await client.post(
        f"/api/v1/klm/omnichannel/connections/{connection_id}/ads-manager/snapshots",
        json={
            "externalSyncId": "fixture:flag-check",
            "source": "local_fixture",
            "observedAt": datetime.now(UTC).isoformat(),
            "entities": [
                {
                    "entityType": "campaign",
                    "externalId": "fixture-flag-campaign",
                    "displayName": "Campanha de demonstração",
                    "effectiveStatus": "active",
                    "metrics": [
                        {"metricDate": datetime.now(UTC).date().isoformat(), "spend": "1280.00"}
                    ],
                }
            ],
        },
    )
    assert fixture_response.status_code == 201, fixture_response.text

    with_fixture_summary = (await client.get("/api/v1/klm/tracking/summary")).json()
    assert with_fixture_summary["containsLocalFixtureData"] is True


@pytest.mark.asyncio
async def test_tracking_summary_normalizes_camel_and_stored_event_aliases(client):
    aliases = [
        ("PageView", "page_view"),
        ("ViewContent", "view_content"),
        ("Contact", "contact"),
        ("Lead", "lead"),
        ("Schedule", "schedule"),
        ("Client", "client"),
    ]
    for index, (camel_name, snake_name) in enumerate(aliases):
        await _record_event(client, camel_name, f"camel-{index}")
        await _record_event(client, snake_name, f"snake-{index}")
    await _record_event(client, "QualifiedLead", "qualified-camel")
    await _record_event(client, "lead_qualified", "qualified-stored")

    response = await client.get("/api/v1/klm/tracking/summary")

    assert response.status_code == 200, response.text
    summary = response.json()
    coverage = {item["event"]: item for item in summary["eventCoverage"]}
    assert summary["totalEvents"] == 14
    assert summary["leads"] == 2
    assert summary["qualifiedLeads"] == 2
    assert summary["meetings"] == 2
    assert summary["clients"] == 2
    assert set(coverage) == {snake_name for _, snake_name in aliases}
    assert all(item["covered"] and item["count"] == 2 for item in coverage.values())


@pytest.mark.asyncio
async def test_tracking_summary_applies_optional_period_to_every_metric(client):
    now = datetime.now(UTC)
    await _record_event(
        client,
        "Lead",
        "recent-lead",
        happened_at=now - timedelta(days=2),
    )
    await _record_event(
        client,
        "payment_confirmed",
        "old-client",
        value=2500,
        happened_at=now - timedelta(days=20),
    )

    all_time = (await client.get("/api/v1/klm/tracking/summary")).json()
    response = await client.get("/api/v1/klm/tracking/summary?period_days=7")

    assert response.status_code == 200, response.text
    summary = response.json()
    assert all_time["totalEvents"] == 2
    assert summary["periodDays"] == 7
    assert summary["periodStart"] is not None
    assert summary["totalEvents"] == 1
    assert summary["leads"] == 1
    assert summary["clients"] == 0
    assert summary["attributableRevenue"] == 0
    assert summary["conversionRate"] == 0
    assert len(summary["timeTrend"]) == 1


@pytest.mark.asyncio
async def test_tracking_summary_classifies_spend_revenue_and_ignored_values(client):
    valued_events = [
        ("ad_spend", 100),
        ("contract_created", 200),
        ("payment_confirmed", 300),
        ("purchase", 400),
        ("Client", 500),
        ("Lead", 999),
    ]
    for index, (event_name, value) in enumerate(valued_events):
        await _record_event(client, event_name, f"money-{index}", value=value)

    response = await client.get("/api/v1/klm/tracking/summary")

    assert response.status_code == 200, response.text
    summary = response.json()
    channel = summary["byChannel"][0]
    assert summary["adSpend"] == 100
    assert summary["attributableRevenue"] == 700
    assert summary["ignoredValueEvents"] == 3
    assert summary["leads"] == 1
    # "clients" conta toda conversão atribuída (TRACKING_CONVERSION_EVENTS):
    # contract_created, payment_confirmed, purchase e Client = 4.
    assert summary["clients"] == 4
    assert summary["conversionRate"] == 400
    assert summary["cac"] == 25
    assert summary["roas"] == 7
    assert channel["adSpend"] == 100
    assert channel["attributableRevenue"] == 700
    assert channel["value"] == 700


@pytest.mark.asyncio
async def test_tracking_summary_derives_identified_journeys_and_remarketing(client):
    converted_click = {"gclid": "converted-journey"}
    prospect_click = {"fbclid": "remarketing-journey"}
    for index, event_name in enumerate(
        ["PageView", "Lead", "Schedule", "Client", "payment_confirmed"]
    ):
        await _record_event(
            client,
            event_name,
            f"converted-{index}",
            value=1200 if event_name == "payment_confirmed" else None,
            click_ids=converted_click,
        )
    for index, event_name in enumerate(["ViewContent", "Lead", "qualified_lead"]):
        await _record_event(
            client,
            event_name,
            f"prospect-{index}",
            channel="meta_ads",
            click_ids=prospect_click,
        )
    await _record_event(client, "Lead", "anonymous-lead")

    response = await client.get("/api/v1/klm/tracking/summary")

    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["identifiedJourneys"] == 2
    assert len(summary["journeys"]) == 2
    converted = next(item for item in summary["journeys"] if item["converted"])
    assert converted["eventPath"] == [
        "page_view",
        "lead",
        "schedule",
        "client",
        "payment_confirmed",
    ]
    assert converted["attributableRevenue"] == 1200
    assert summary["remarketing"] == [
        {
            "stage": "qualified_lead",
            "channel": "meta_ads",
            "audienceSize": 1,
            "lastActivityAt": summary["remarketing"][0]["lastActivityAt"],
        }
    ]


@pytest.mark.asyncio
async def test_tracking_summary_is_tenant_isolated(client, db_session, auth_context):
    await _record_event(client, "Lead", "tenant-own-lead", channel="organic")
    other_workspace = Workspace(
        name="Other Workspace",
        slug="tracking-other-workspace",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    db_session.add(
        AcquisitionEvent(
            workspace_id=other_workspace.id,
            event_name="Client",
            channel="other-tenant-paid",
            dedupe_key="tenant-other-client",
            happened_at=datetime.now(UTC),
            value=9000,
            currency="BRL",
            click_ids={},
            event_metadata={},
        )
    )
    await db_session.flush()

    response = await client.get("/api/v1/klm/tracking/summary")

    assert response.status_code == 200, response.text
    summary = response.json()
    assert str(auth_context.workspace_id) != str(other_workspace.id)
    assert summary["totalEvents"] == 1
    assert summary["leads"] == 1
    assert summary["clients"] == 0
    assert summary["attributableRevenue"] == 0
    assert [item["channel"] for item in summary["byChannel"]] == ["organic"]


@pytest.mark.asyncio
async def test_exact_campaign_reporting_does_not_fake_provider_match_eligibility(client):
    await _record_event(
        client,
        "payment_confirmed",
        "meta-campaign-only-conversion",
        channel="meta_ads",
        value=4000,
        event_metadata={
            "provider": "meta_ads",
            "accountId": "act-100",
            "campaignId": "cmp-200",
            "campaignName": "Negativa de plano de saude",
            "adSetId": "set-300",
            "adId": "ad-400",
            "creativeId": "creative-500",
            "creativeName": "Video liminar 24h",
        },
    )

    summary_response = await client.get("/api/v1/klm/tracking/summary")
    prepare_response = await client.post(
        "/api/v1/klm/tracking/conversion-feedback/prepare"
    )

    assert summary_response.status_code == 200, summary_response.text
    assert prepare_response.status_code == 200, prepare_response.text
    summary = summary_response.json()
    row = next(item for item in summary["attributionRows"] if item["provider"] == "meta_ads")
    assert row["campaignId"] == "cmp-200"
    assert row["adId"] == "ad-400"
    assert row["creativeId"] == "creative-500"
    assert row["attributableRevenue"] == 4000
    assert summary["attributionCoverage"]["attributedConversions"] == 1
    assert summary["attributionCoverage"]["feedbackEligibleConversions"] == 0
    assert prepare_response.json() == {
        "scanned": 1,
        "prepared": 0,
        "deduplicated": 0,
        "skippedWithoutEvidence": 1,
        "suppressedBusinessDuplicates": 0,
        "providers": {},
        "externalActionsExecuted": 0,
    }


@pytest.mark.asyncio
async def test_conversion_feedback_is_local_idempotent_and_normalizes_meta_fbc(
    client, db_session, auth_context
):
    happened_at = datetime(2026, 7, 21, 18, 0, tzinfo=UTC)
    event = AcquisitionEvent(
        workspace_id=auth_context.workspace_id,
        event_name="Client",
        channel="meta_ads",
        source="facebook_ads",
        dedupe_key="meta-click-conversion",
        happened_at=happened_at,
        value=8000,
        currency="BRL",
        click_ids={"fbclid": "raw-click-id", "fbp": "fb.1.123.browser-id"},
        event_metadata={"provider": "meta_ads", "campaignId": "cmp-800"},
    )
    db_session.add(event)
    await db_session.flush()

    first = await client.post("/api/v1/klm/tracking/conversion-feedback/prepare")
    second = await client.post("/api/v1/klm/tracking/conversion-feedback/prepare")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["prepared"] == 1
    assert first.json()["deduplicated"] == 0
    assert first.json()["externalActionsExecuted"] == 0
    assert second.json()["prepared"] == 0
    assert second.json()["deduplicated"] == 1
    intents = list(
        (
            await db_session.scalars(
                select(ExternalActionIntent).where(
                    ExternalActionIntent.workspace_id == auth_context.workspace_id,
                    ExternalActionIntent.action_type == "ads_conversion_feedback",
                )
            )
        ).all()
    )
    assert len(intents) == 1
    match_keys = intents[0].payload["providerMatchKeys"]
    assert match_keys == {
        "fbc": f"fb.1.{int(happened_at.timestamp() * 1000)}.raw-click-id",
        "fbp": "fb.1.123.browser-id",
    }
    assert "fbclid" not in match_keys
    assert intents[0].payload["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_hashed_canonical_identity_enables_feedback_without_leaking_case_data(
    client, db_session, auth_context
):
    canonical_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Lead Saúde",
        email="lead.saude@example.com",
        phone="+55 (11) 98888-7777",
        cpf="123.456.789-00",
        notes="Diagnóstico oncológico e medicamento sigiloso.",
    )
    db_session.add(canonical_client)
    await db_session.flush()
    event = AcquisitionEvent(
        workspace_id=auth_context.workspace_id,
        client_id=canonical_client.id,
        event_name="Client",
        channel="google_ads",
        source="google_ads",
        dedupe_key="google-hashed-identity-conversion",
        happened_at=datetime(2026, 7, 21, 18, 10, tzinfo=UTC),
        value=4000,
        currency="BRL",
        click_ids={},
        event_metadata={
            "provider": "google_ads",
            "campaignId": "campaign-1",
            "creativeId": "creative-1",
            "adDataConsent": "GRANTED",
            "adDataConsentEvidenceRef": "consent-ledger:test-google-hash",
            "diagnosis": "não pode sair da plataforma",
        },
    )
    db_session.add(event)
    await db_session.flush()

    first = await client.post("/api/v1/klm/tracking/conversion-feedback/prepare")
    second = await client.post("/api/v1/klm/tracking/conversion-feedback/prepare")
    summary_response = await client.get("/api/v1/klm/tracking/summary")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert summary_response.status_code == 200, summary_response.text
    assert first.json()["prepared"] == 1
    assert second.json()["deduplicated"] == 1
    intent = await db_session.scalar(
        select(ExternalActionIntent).where(
            ExternalActionIntent.workspace_id == auth_context.workspace_id,
            ExternalActionIntent.action_type == "ads_conversion_feedback",
            ExternalActionIntent.provider == "google_ads",
        )
    )
    assert intent is not None
    payload_text = str(intent.payload).casefold()
    assert intent.payload["matchingMode"] == "hashed_first_party"
    assert set(intent.payload["providerMatchKeyNames"]) >= {
        "sha256_email",
        "sha256_phone",
        "sha256_external_id",
    }
    assert intent.payload["readyForDispatch"] is False
    assert intent.payload["missingConfiguration"] == [
        "customer_id",
        "conversion_action",
        "credential_ref",
        "ad_user_data_consent_granted",
    ]
    for forbidden in (
        "lead.saude@example.com",
        "5511988887777",
        "123.456.789-00",
        "diagnóstico",
        "diagnostico",
        "medicamento",
        "não pode sair",
    ):
        assert forbidden not in payload_text
    proof = await db_session.scalar(
        select(AdsConversionFeedbackRecord).where(
            AdsConversionFeedbackRecord.workspace_id == auth_context.workspace_id,
            AdsConversionFeedbackRecord.intent_id == intent.id,
            AdsConversionFeedbackRecord.record_type == "prepared",
        )
    )
    assert proof is not None
    assert proof.state == "prepared_local"
    assert proof.payload_sha256 == intent.payload["payloadSha256"]
    assert proof.external_actions_executed == 0
    summary = summary_response.json()
    feedback_event = next(
        item for item in summary["feedbackEvents"] if item["intentId"] == str(intent.id)
    )
    assert feedback_event["proofState"] == "prepared_local"
    assert feedback_event["receiptState"] is None
    assert feedback_event["receiptCount"] == 0
    assert summary["conversionFeedback"]["duplicatePrevented"] >= 1
    assert summary["conversionFeedback"]["preparedProofs"] >= 1
    assert summary["conversionFeedback"]["providerReceipts"] == 0


@pytest.mark.asyncio
async def test_channel_metrics_apply_last_paid_touch_to_crm_conversion(
    client, db_session, auth_context
):
    canonical_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Lead atribuído",
        email="atribuido@example.com",
        phone="+5511988887777",
    )
    db_session.add(canonical_client)
    await db_session.flush()
    db_session.add_all(
        [
            AcquisitionEvent(
                workspace_id=auth_context.workspace_id,
                client_id=canonical_client.id,
                event_name="lead",
                channel="google_ads",
                source="google_ads",
                dedupe_key="paid-touch-google",
                happened_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
                click_ids={"gclid": "EAIaIQobChMI1234567890"},
                event_metadata={"provider": "google_ads", "campaignId": "cmp-paid"},
            ),
            AcquisitionEvent(
                workspace_id=auth_context.workspace_id,
                client_id=canonical_client.id,
                event_name="payment_confirmed",
                channel="crm",
                source="internal",
                dedupe_key="crm-payment-attributed",
                happened_at=datetime(2026, 7, 21, 11, 0, tzinfo=UTC),
                value=4000,
                currency="BRL",
                click_ids={},
                event_metadata={},
            ),
        ]
    )
    await db_session.flush()

    summary = (await client.get("/api/v1/klm/tracking/summary")).json()
    google = next(row for row in summary["byChannel"] if row["channel"] == "google_ads")
    assert google["attributableRevenue"] == 4000
    assert all(
        row["attributableRevenue"] == 0
        for row in summary["byChannel"]
        if row["channel"] == "crm"
    )


@pytest.mark.asyncio
async def test_purchase_and_payment_for_same_transaction_count_and_prepare_once(
    client, db_session, auth_context
):
    canonical_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Lead com transação única",
        email="transacao@example.com",
        phone="+5511966665555",
    )
    db_session.add(canonical_client)
    await db_session.flush()
    transaction_metadata = {
        "provider": "google_ads",
        "transactionId": "txn-health-0001",
        "adDataConsent": "GRANTED",
        "adDataConsentEvidenceRef": "consent-ledger:txn-health-0001",
    }
    purchase = await _record_event(
        client,
        "purchase",
        "purchase-txn-health-0001",
        channel="google_ads",
        value=100000,
        happened_at=datetime(2026, 7, 21, 14, 0, tzinfo=UTC),
        click_ids={"gclid": "EAIaIQobChMI1234567890"},
        event_metadata=transaction_metadata,
        client_id=str(canonical_client.id),
    )
    payment = await _record_event(
        client,
        "payment_confirmed",
        "payment-txn-health-0001",
        channel="crm",
        value=100000,
        happened_at=datetime(2026, 7, 21, 14, 5, tzinfo=UTC),
        event_metadata=transaction_metadata,
        client_id=str(canonical_client.id),
    )

    summary_response = await client.get("/api/v1/klm/tracking/summary")
    prepare_response = await client.post(
        "/api/v1/klm/tracking/conversion-feedback/prepare"
    )

    assert summary_response.status_code == 200, summary_response.text
    assert prepare_response.status_code == 200, prepare_response.text
    summary = summary_response.json()
    assert summary["attributableRevenue"] == 100000
    assert summary["attributionCoverage"]["conversionEvents"] == 1
    assert summary["attributionCoverage"]["businessDuplicatesSuppressed"] == 1
    assert prepare_response.json()["scanned"] == 1
    assert prepare_response.json()["suppressedBusinessDuplicates"] == 1
    intents = list(
        (
            await db_session.scalars(
                select(ExternalActionIntent).where(
                    ExternalActionIntent.workspace_id == auth_context.workspace_id,
                    ExternalActionIntent.action_type == "ads_conversion_feedback",
                )
            )
        ).all()
    )
    assert len(intents) == 1
    assert intents[0].payload["sourceEventId"] == payment["id"]
    assert intents[0].payload["sourceEventId"] != purchase["id"]
    assert intents[0].payload["conversionEvent"] == "payment_confirmed"
    assert intents[0].payload["businessEventFamily"] == "realized_revenue"
    assert len(intents[0].payload["businessEventIdentityHash"]) == 64


@pytest.mark.asyncio
async def test_feedback_envelope_can_be_superseded_before_external_action(
    client, db_session, auth_context
):
    canonical_client = Client(
        workspace_id=auth_context.workspace_id,
        name="Lead com consentimento",
        email="consentido@example.com",
        phone="+5511977776666",
    )
    db_session.add(canonical_client)
    await db_session.flush()
    db_session.add(
        AcquisitionEvent(
            workspace_id=auth_context.workspace_id,
            client_id=canonical_client.id,
            event_name="payment_confirmed",
            channel="google_ads",
            source="google_ads",
            dedupe_key="feedback-versioned-envelope",
            happened_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
            value=4000,
            currency="BRL",
            click_ids={"gclid": "EAIaIQobChMI1234567890"},
            event_metadata={
                "provider": "google_ads",
                "adDataConsent": "GRANTED",
                "adDataConsentEvidenceRef": "consent-ledger:versioned",
            },
        )
    )
    await db_session.flush()

    first = await client.post("/api/v1/klm/tracking/conversion-feedback/prepare")
    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    workspace.settings = {
        "adsConversionFeedback": {
            "google_ads": {
                "customerId": "123",
                "conversionAction": "customers/123/conversionActions/456",
                "credentialRef": "secret/google",
                "adUserDataConsent": "GRANTED",
            }
        }
    }
    await db_session.flush()
    second = await client.post("/api/v1/klm/tracking/conversion-feedback/prepare")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["prepared"] == 1
    intent = await db_session.scalar(
        select(ExternalActionIntent).where(
            ExternalActionIntent.workspace_id == auth_context.workspace_id,
            ExternalActionIntent.action_type == "ads_conversion_feedback",
        )
    )
    assert intent is not None
    records = list(
        (
            await db_session.scalars(
                select(AdsConversionFeedbackRecord)
                .where(AdsConversionFeedbackRecord.intent_id == intent.id)
                .order_by(AdsConversionFeedbackRecord.attempt_number)
            )
        ).all()
    )
    assert intent.payload["readyForDispatch"] is True
    assert [record.attempt_number for record in records] == [0, 1]
    assert records[0].payload_sha256 != records[1].payload_sha256


@pytest.mark.asyncio
async def test_future_conversion_is_not_prepared(client):
    await _record_event(
        client,
        "payment_confirmed",
        "future-payment",
        value=4000,
        happened_at=datetime.now(UTC) + timedelta(days=2),
        channel="google_ads",
        click_ids={"gclid": "EAIaIQobChMI1234567890"},
    )

    result = await client.post("/api/v1/klm/tracking/conversion-feedback/prepare")
    assert result.status_code == 200, result.text
    assert result.json()["scanned"] == 0


@pytest.mark.asyncio
async def test_tracking_summary_masks_campaign_and_internal_refs_without_lead_access(
    client, db_session, auth_context
):
    await _record_event(
        client,
        "lead",
        "restricted-tracking-row",
        channel="meta_ads",
        event_metadata={
            "provider": "meta_ads",
            "campaignId": "campaign-sensitive",
            "campaignName": "Negativa oncológica",
            "creativeId": "creative-sensitive",
            "threadId": "00000000-0000-0000-0000-000000000999",
        },
    )
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.id == auth_context.membership_id
        )
    )
    membership.role = WorkspaceRole.legal_manager
    membership.permission_denials = [
        {"module": "lead_data", "action": "view", "scope": "workspace"}
    ]
    await db_session.flush()

    response = await client.get("/api/v1/klm/tracking/summary")
    assert response.status_code == 200, response.text
    row = response.json()["attributionRows"][0]
    assert row["campaignId"] is None
    assert row["campaignName"] is None
    assert row["creativeId"] is None
    assert row["sampleThreadId"] is None
    assert response.json()["feedbackEvents"] == []
