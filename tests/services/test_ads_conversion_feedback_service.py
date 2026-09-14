"""Provider envelope contracts must stay deterministic and privacy bounded."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.services.ads_conversion_feedback_service import (
    build_provider_feedback_envelope,
    normalize_email,
    normalize_phone,
)


@pytest.mark.parametrize(
    ("provider", "match_keys", "config", "expected_marker"),
    [
        (
            "meta_ads",
            {
                "fbc": "fb.1.1721565000000.ABCdef1234567890",
                "fbp": "fb.1.1721565000000.1098115397",
            },
            {
                "datasetId": "dataset-1",
                "credentialRef": "secret/meta",
                "consentPolicyRef": "policy-1",
            },
            '"event_id"',
        ),
        (
            "google_ads",
            {"gclid": "EAIaIQobChMI1234567890"},
            {
                "customerId": "1234567890",
                "conversionAction": "customers/123/conversionActions/456",
                "credentialRef": "secret/google",
                "adUserDataConsent": "GRANTED",
            },
            '"conversion_action"',
        ),
        (
            "tiktok_ads",
            {"ttclid": "Cj0KCQjw1234567890abcdef"},
            {
                "pixelCode": "pixel-1",
                "advertiserId": "advertiser-1",
                "eventSourceUrl": "https://klmadvogados.com/obrigacao-de-fazer",
                "credentialRef": "secret/tiktok",
                "consentPolicyRef": "policy-1",
            },
            '"event_id"',
        ),
        (
            "linkedin_ads",
            {"li_fat_id": "123e4567-e89b-12d3-a456-426614174000"},
            {
                "accountId": "account-1",
                "conversionUrn": "urn:lla:llaPartnerConversion:123",
                "credentialRef": "secret/linkedin",
                "consentPolicyRef": "policy-1",
            },
            '"eventId"',
        ),
        (
            "microsoft_ads",
            {"msclkid": "dd4afcccb1c9a4cad9544dd7e5006abc"},
            {
                "customerAccountId": "123456789",
                "customerId": "987654321",
                "conversionName": "Contrato jurídico",
                "credentialRef": "secret/microsoft",
                "consentPolicyRef": "policy-1",
            },
            '"MicrosoftClickId"',
        ),
    ],
)
def test_provider_envelopes_are_specific_hashed_and_ready(
    provider: str,
    match_keys: dict[str, str],
    config: dict[str, str],
    expected_marker: str,
) -> None:
    workspace_id = uuid4()
    client_id = uuid4()
    event_id = uuid4()
    envelope = build_provider_feedback_envelope(
        provider=provider,
        conversion_event="purchase",
        source_event_id=event_id,
        happened_at=datetime(2026, 7, 21, 12, 30, tzinfo=UTC),
        value=4800,
        currency="BRL",
        match_keys=match_keys,
        workspace_id=workspace_id,
        client_id=client_id,
        email=" Lead.Exemplo@Example.COM ",
        phone="+55 (11) 99876-5432",
        config=config,
        ad_data_consent="GRANTED",
        consent_evidence_ref="consent-ledger:evt-123",
    ).public_dict()

    serialized = json.dumps(envelope, sort_keys=True)
    assert envelope["readyForDispatch"] is True
    assert envelope["missingConfiguration"] == []
    assert len(envelope["payloadSha256"]) == 64
    assert expected_marker in serialized
    assert "lead.exemplo@example.com" not in serialized.casefold()
    assert "5511998765432" not in serialized
    assert str(client_id) not in serialized
    assert "sha256_email" in envelope["matchKeyNames"]
    assert "sha256_phone" in envelope["matchKeyNames"]
    assert envelope["privacyGuard"]["sensitiveMatterDataIncluded"] is False
    assert envelope["privacyGuard"]["clickIdsFormatValidated"] is True
    assert envelope["consentState"] == "GRANTED"
    for forbidden in (
        "medicamento",
        "diagnostico",
        "demanda juridica",
        "prescricao",
        "nota interna",
    ):
        assert forbidden not in serialized.casefold()


def test_provider_envelope_is_deterministic_and_reports_missing_destination() -> None:
    args = {
        "provider": "google_ads",
        "conversion_event": "client",
        "source_event_id": uuid4(),
        "happened_at": datetime(2026, 7, 21, 12, 30, tzinfo=UTC),
        "value": 4000,
        "currency": "BRL",
        "match_keys": {},
        "workspace_id": uuid4(),
        "client_id": uuid4(),
        "email": "lead@example.com",
        "phone": None,
        "config": {},
        "ad_data_consent": "GRANTED",
        "consent_evidence_ref": "consent-ledger:evt-123",
    }
    first = build_provider_feedback_envelope(**args).public_dict()
    second = build_provider_feedback_envelope(**args).public_dict()

    assert first == second
    assert first["readyForDispatch"] is False
    assert first["matchingMode"] == "hashed_first_party"
    assert first["missingConfiguration"] == [
        "customer_id",
        "conversion_action",
        "credential_ref",
        "ad_user_data_consent_granted",
    ]


def test_denied_consent_never_produces_dispatchable_identifiers() -> None:
    envelope = build_provider_feedback_envelope(
        provider="google_ads",
        conversion_event="purchase",
        source_event_id=uuid4(),
        happened_at=datetime(2026, 7, 21, 12, 30, tzinfo=UTC),
        value=4000,
        currency="BRL",
        match_keys={"gclid": "EAIaIQobChMI1234567890"},
        workspace_id=uuid4(),
        client_id=uuid4(),
        email="lead@example.com",
        phone="+5511998765432",
        config={
            "customerId": "1234567890",
            "conversionAction": "customers/123/conversionActions/456",
            "credentialRef": "secret/google",
            "adUserDataConsent": "GRANTED",
        },
        ad_data_consent="DENIED",
        consent_evidence_ref="consent-ledger:evt-denied",
    ).public_dict()

    payload = envelope["providerPayload"]["events"][0]
    assert envelope["readyForDispatch"] is False
    assert envelope["matchingMode"] == "unmatched"
    assert "subject_ad_data_consent_granted" in envelope["missingConfiguration"]
    assert payload["user_identifiers"] == []
    assert "gclid" not in payload


def _meta_envelope_args(**overrides: Any) -> dict[str, Any]:
    base = {
        "provider": "meta_ads",
        "conversion_event": "contract_created",
        "source_event_id": uuid4(),
        "happened_at": datetime(2026, 7, 21, 12, 30, tzinfo=UTC),
        "value": 4800,
        "currency": "BRL",
        "workspace_id": uuid4(),
        "client_id": uuid4(),
        "email": "lead@example.com",
        "phone": "+5511998765432",
        "config": {
            "datasetId": "dataset-1",
            "credentialRef": "secret/meta",
            "consentPolicyRef": "policy-1",
        },
        "ad_data_consent": "GRANTED",
        "consent_evidence_ref": "consent-ledger:evt-123",
    }
    base.update(overrides)
    return base


def test_meta_business_messaging_variant_differs_from_standard_web_envelope() -> None:
    shared_source_event_id = uuid4()
    web_envelope = build_provider_feedback_envelope(
        **_meta_envelope_args(
            source_event_id=shared_source_event_id,
            match_keys={
                "fbc": "fb.1.1721565000000.ABCdef1234567890",
                "fbp": "fb.1.1721565000000.1098115397",
            },
        )
    ).public_dict()
    whatsapp_envelope = build_provider_feedback_envelope(
        **_meta_envelope_args(
            source_event_id=shared_source_event_id,
            match_keys={"ctwa_clid": "ARDp1234567890abcdefWA"},
        )
    ).public_dict()

    web_event = web_envelope["providerPayload"]["data"][0]
    wa_event = whatsapp_envelope["providerPayload"]["data"][0]

    # Standard web/system envelope: no business messaging fields.
    assert web_event["action_source"] == "system_generated"
    assert "messaging_channel" not in web_event
    assert "ctwa_clid" not in web_event["user_data"]

    # Business messaging (CTWA/WhatsApp) envelope carries the dedicated
    # action_source + messaging_channel + ctwa_clid contract required by the
    # official Conversions API for Business Messaging documentation.
    assert wa_event["action_source"] == "business_messaging"
    assert wa_event["messaging_channel"] == "whatsapp"
    assert wa_event["user_data"]["ctwa_clid"] == "ARDp1234567890abcdefWA"

    # Hashing/event_id contract is unchanged between the two formats.
    assert web_event["event_id"] == wa_event["event_id"]
    assert web_event["event_name"] == wa_event["event_name"]
    assert set(web_event["custom_data"]) == set(wa_event["custom_data"])


def test_meta_business_messaging_variant_for_messenger_psid() -> None:
    """Mirrors the ctwa_clid/WhatsApp contract for Messenger touches: PSID goes
    into user_data and messaging_channel becomes "messenger"."""
    envelope = build_provider_feedback_envelope(
        **_meta_envelope_args(match_keys={"psid": "1234567890123456"})
    ).public_dict()
    event = envelope["providerPayload"]["data"][0]

    assert event["action_source"] == "business_messaging"
    assert event["messaging_channel"] == "messenger"
    assert event["user_data"]["psid"] == "1234567890123456"
    assert "ctwa_clid" not in event["user_data"]
    assert "igsid" not in event["user_data"]


def test_meta_business_messaging_variant_for_instagram_igsid() -> None:
    """Same contract for Instagram DM touches: IGSID goes into user_data and
    messaging_channel becomes "instagram"."""
    envelope = build_provider_feedback_envelope(
        **_meta_envelope_args(match_keys={"igsid": "9876543210987654"})
    ).public_dict()
    event = envelope["providerPayload"]["data"][0]

    assert event["action_source"] == "business_messaging"
    assert event["messaging_channel"] == "instagram"
    assert event["user_data"]["igsid"] == "9876543210987654"
    assert "ctwa_clid" not in event["user_data"]
    assert "psid" not in event["user_data"]


def test_meta_psid_igsid_rejected_when_not_purely_numeric() -> None:
    from app.services.ads_conversion_feedback_service import validated_match_keys

    valid, rejected = validated_match_keys(
        "meta_ads", {"psid": "not-a-numeric-id", "igsid": "5566778899001122"}
    )
    assert "psid" not in valid
    assert "psid" in rejected
    assert valid["igsid"] == "5566778899001122"


def test_provider_contract_shapes_follow_official_api_fields() -> None:
    common = {
        "conversion_event": "purchase",
        "source_event_id": uuid4(),
        "happened_at": datetime(2026, 7, 21, 12, 30, tzinfo=UTC),
        "value": 4800,
        "currency": "BRL",
        "workspace_id": uuid4(),
        "client_id": uuid4(),
        "email": "lead@example.com",
        "phone": "+5511998765432",
        "ad_data_consent": "GRANTED",
        "consent_evidence_ref": "consent-ledger:evt-123",
    }
    google = build_provider_feedback_envelope(
        provider="google_ads",
        match_keys={"gclid": "EAIaIQobChMI1234567890"},
        config={
            "customerId": "123",
            "conversionAction": "customers/123/conversionActions/456",
            "credentialRef": "secret/google",
            "adUserDataConsent": "GRANTED",
        },
        **common,
    ).public_dict()
    assert google["providerPayload"]["validateOnly"] is False
    assert google["destination"]["validateBeforeDispatch"] is True

    tiktok = build_provider_feedback_envelope(
        provider="tiktok_ads",
        match_keys={"ttclid": "Cj0KCQjw1234567890abcdef"},
        config={
            "pixelCode": "pixel-1",
            "eventSourceUrl": "https://klmadvogados.com/saude",
            "credentialRef": "secret/tiktok",
            "consentPolicyRef": "policy-1",
        },
        **common,
    ).public_dict()
    item = tiktok["providerPayload"]["data"][0]
    assert item["user"]["ttclid"] == "Cj0KCQjw1234567890abcdef"
    assert isinstance(item["user"]["email"], list)
    assert item["page"]["url"] == "https://klmadvogados.com/saude"
    assert "context" not in item

    linkedin = build_provider_feedback_envelope(
        provider="linkedin_ads",
        match_keys={"li_fat_id": "123e4567-e89b-12d3-a456-426614174000"},
        config={
            "accountId": "account-1",
            "conversionUrn": "urn:lla:llaPartnerConversion:123",
            "credentialRef": "secret/linkedin",
            "consentPolicyRef": "policy-1",
        },
        **common,
    ).public_dict()
    user = linkedin["providerPayload"]["elements"][0]["user"]
    assert user["externalIds"]
    assert all(row["idType"] != "EXTERNAL_ID" for row in user["userIds"])


def test_free_text_click_id_is_rejected_before_privacy_claim() -> None:
    envelope = build_provider_feedback_envelope(
        provider="google_ads",
        conversion_event="purchase",
        source_event_id=uuid4(),
        happened_at=datetime(2026, 7, 21, 12, 30, tzinfo=UTC),
        value=4000,
        currency="BRL",
        match_keys={"gclid": "diagnostico-oncologico"},
        workspace_id=uuid4(),
        client_id=None,
        email=None,
        phone=None,
        config={
            "customerId": "123",
            "conversionAction": "customers/123/conversionActions/456",
            "credentialRef": "secret/google",
            "adUserDataConsent": "GRANTED",
        },
        ad_data_consent="GRANTED",
        consent_evidence_ref="consent-ledger:evt-123",
    ).public_dict()

    assert envelope["readyForDispatch"] is False
    assert envelope["rejectedMatchKeyNames"] == ["gclid"]
    assert "diagnostico-oncologico" not in json.dumps(envelope)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Lead@Example.COM ", "lead@example.com"),
        ("invalid", None),
    ],
)
def test_email_normalization(raw: str, expected: str | None) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+55 (11) 99876-5432", "+5511998765432"),
        ("123", None),
    ],
)
def test_phone_normalization(raw: str, expected: str | None) -> None:
    assert normalize_phone(raw) == expected
