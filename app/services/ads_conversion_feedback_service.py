"""Provider-specific, privacy-bounded conversion feedback envelopes.

The builders in this module do not perform network calls. They produce the exact
local envelope that a later, approved dispatcher can submit to each ads API.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

IDENTITY_MATCH_PROVIDERS = frozenset(
    {"meta_ads", "google_ads", "tiktok_ads", "linkedin_ads", "microsoft_ads"}
)

_PROVIDER_MATCH_KEYS = {
    # psid/igsid: page-scoped user IDs the social capture records for Messenger
    # / Instagram DM touches (see `social_event_adapter_service.py`, which
    # persists them as `external_user_id` on the canonical thread identity).
    # They ride the same match-key channel as `ctwa_clid` for WhatsApp: a
    # business-messaging conversation identifier, not a click ID, but validated
    # and carried the same way end to end.
    "meta_ads": ("fbc", "fbp", "ctwa_clid", "psid", "igsid"),
    "google_ads": ("gclid", "gbraid", "wbraid"),
    "tiktok_ads": ("ttclid",),
    "linkedin_ads": ("li_fat_id",),
    "microsoft_ads": ("msclkid",),
}
_OPAQUE_CLICK_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{10,256}$")
_MSCLKID_RE = re.compile(r"^[A-Fa-f0-9]{32}$")
# PSID/IGSID are purely numeric page-scoped user IDs (Meta docs: up to ~19
# digits); they never contain letters, so they fail `_OPAQUE_CLICK_ID_RE`'s
# alpha+digit requirement and need their own shape check.
_SCOPED_USER_ID_RE = re.compile(r"^[0-9]{5,32}$")

_META_EVENT_NAMES = {
    "client": "CompleteRegistration",
    "contract_created": "Lead",
    "payment_confirmed": "Purchase",
    "purchase": "Purchase",
}

_TIKTOK_EVENT_NAMES = {
    "client": "CompleteRegistration",
    "contract_created": "PlaceAnOrder",
    "payment_confirmed": "CompletePayment",
    "purchase": "CompletePayment",
}


def _normalized_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_email(value: str | None) -> str | None:
    candidate = str(value or "").strip().casefold()
    if not candidate or candidate.count("@") != 1:
        return None
    local, domain = candidate.split("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    return candidate


def normalize_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D+", "", str(value or ""))
    if not 10 <= len(digits) <= 15:
        return None
    return f"+{digits}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_hashed_identifiers(
    *,
    workspace_id: UUID,
    client_id: UUID | None,
    email: str | None,
    phone: str | None,
) -> dict[str, str]:
    """Return only normalized SHA-256 identifiers; raw contact data never escapes."""

    identifiers: dict[str, str] = {}
    normalized_email = normalize_email(email)
    normalized_phone = normalize_phone(phone)
    if normalized_email:
        identifiers["sha256_email"] = _sha256(normalized_email)
    if normalized_phone:
        identifiers["sha256_phone"] = _sha256(normalized_phone)
    if client_id:
        identifiers["sha256_external_id"] = _sha256(
            f"klm:{workspace_id}:{client_id}"
        )
    return identifiers


def has_usable_first_party_identity(email: str | None, phone: str | None) -> bool:
    return bool(normalize_email(email) or normalize_phone(phone))


def provider_feedback_config(
    workspace_settings: Mapping[str, Any] | None,
    provider: str,
) -> dict[str, Any]:
    settings = workspace_settings or {}
    root = settings.get("adsConversionFeedback") or settings.get(
        "ads_conversion_feedback"
    )
    if not isinstance(root, Mapping):
        return {}
    config = root.get(provider)
    return dict(config) if isinstance(config, Mapping) else {}


def _config_value(config: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _event_configuration(
    config: Mapping[str, Any],
    key: str,
    conversion_event: str,
) -> str | None:
    mapped = config.get(key)
    if isinstance(mapped, Mapping):
        value = mapped.get(conversion_event) or mapped.get("default")
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def canonical_payload_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProviderFeedbackEnvelope:
    provider: str
    provider_event_id: str
    conversion_event: str
    destination: dict[str, Any]
    provider_payload: dict[str, Any]
    match_key_names: tuple[str, ...]
    matching_mode: str
    consent_state: str
    consent_evidence_ref: str | None
    ready_for_dispatch: bool
    missing_configuration: tuple[str, ...]
    rejected_match_key_names: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        envelope = {
            "schemaVersion": "klm.ads.provider-envelope.v2",
            "provider": self.provider,
            "providerEventId": self.provider_event_id,
            "conversionEvent": self.conversion_event,
            "destination": self.destination,
            "providerPayload": self.provider_payload,
            "matchKeyNames": list(self.match_key_names),
            "matchingMode": self.matching_mode,
            "consentState": self.consent_state,
            "consentEvidenceRef": self.consent_evidence_ref,
            "readyForDispatch": self.ready_for_dispatch,
            "missingConfiguration": list(self.missing_configuration),
            "rejectedMatchKeyNames": list(self.rejected_match_key_names),
            "privacyGuard": {
                "rawContactDataIncluded": False,
                "sensitiveMatterDataIncluded": False,
                "clickIdsFormatValidated": True,
                "subjectConsentRequired": True,
                "allowedDataClasses": [
                    "provider_click_id",
                    "sha256_contact_identifier",
                    "conversion_event",
                    "conversion_value",
                ],
                "excludedDataClasses": [
                    "cpf",
                    "legal_demand",
                    "health_data",
                    "documents",
                    "conversation_content",
                    "internal_notes",
                ],
            },
        }
        envelope["payloadSha256"] = canonical_payload_sha256(envelope)
        return envelope


def _matching_mode(match_keys: Mapping[str, str], hashes: Mapping[str, str]) -> str:
    has_click = bool(match_keys)
    has_hash = bool(hashes.get("sha256_email") or hashes.get("sha256_phone"))
    if has_click and has_hash:
        return "click_id_and_hashed_first_party"
    if has_click:
        return "click_id"
    if has_hash:
        return "hashed_first_party"
    return "unmatched"


def _normalized_consent_state(value: str | None) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if candidate in {"GRANTED", "DENIED"} else "UNKNOWN"


def _looks_like_opaque_click_id(key: str, value: str) -> bool:
    if key == "msclkid":
        return bool(_MSCLKID_RE.fullmatch(value))
    if key in ("psid", "igsid"):
        return bool(_SCOPED_USER_ID_RE.fullmatch(value))
    if not _OPAQUE_CLICK_ID_RE.fullmatch(value):
        return False
    # Provider click IDs are opaque tokens. Requiring both classes rejects prose,
    # labels and most accidentally copied business or health data.
    return any(char.isalpha() for char in value) and any(char.isdigit() for char in value)


def validated_match_keys(
    provider: str, match_keys: Mapping[str, str]
) -> tuple[dict[str, str], tuple[str, ...]]:
    allowed = set(_PROVIDER_MATCH_KEYS.get(provider, ()))
    valid: dict[str, str] = {}
    rejected: list[str] = []
    for raw_key, raw_value in match_keys.items():
        key = str(raw_key).strip().casefold()
        value = str(raw_value or "").strip()
        if key not in allowed or not _looks_like_opaque_click_id(key, value):
            rejected.append(key or "unknown")
            continue
        valid[key] = value
    return valid, tuple(sorted(set(rejected)))


def _meta_envelope(
    *,
    conversion_event: str,
    provider_event_id: str,
    happened_at: datetime,
    value: float | Decimal | None,
    currency: str,
    match_keys: Mapping[str, str],
    hashes: Mapping[str, str],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    dataset_id = _config_value(config, "datasetId", "dataset_id", "pixelId", "pixel_id")
    credential_ref = _config_value(config, "credentialRef", "credential_ref")
    consent_policy_ref = _config_value(config, "consentPolicyRef", "consent_policy_ref")
    missing = tuple(
        key
        for key, present in (
            ("dataset_id", dataset_id),
            ("credential_ref", credential_ref),
            ("consent_policy_ref", consent_policy_ref),
        )
        if not present
    )
    user_data: dict[str, Any] = {}
    if match_keys.get("fbc"):
        user_data["fbc"] = match_keys["fbc"]
    if match_keys.get("fbp"):
        user_data["fbp"] = match_keys["fbp"]
    if match_keys.get("ctwa_clid"):
        # Conversions API for Business Messaging matches CTWA conversations
        # exclusively by ctwa_clid inside user_data.
        user_data["ctwa_clid"] = match_keys["ctwa_clid"]
    if match_keys.get("psid"):
        # Same business-messaging envelope, mirrored for Messenger touches:
        # the page-scoped user ID is the conversation identifier.
        user_data["psid"] = match_keys["psid"]
    if match_keys.get("igsid"):
        # Mirrored again for Instagram DM touches (IG-scoped user ID).
        user_data["igsid"] = match_keys["igsid"]
    if hashes.get("sha256_email"):
        user_data["em"] = [hashes["sha256_email"]]
    if hashes.get("sha256_phone"):
        user_data["ph"] = [hashes["sha256_phone"]]
    if hashes.get("sha256_external_id"):
        user_data["external_id"] = [hashes["sha256_external_id"]]
    custom_data: dict[str, Any] = {"currency": currency}
    if value is not None:
        custom_data["value"] = float(value)
    # Conversions API for Business Messaging requires its own
    # action_source/messaging_channel combination whenever the touch carries
    # a WhatsApp (ctwa_clid), Messenger (psid) or Instagram (igsid) business
    # messaging identifier; every other case keeps the standard web/system
    # envelope. Priority mirrors ingest order: a touch only ever carries one
    # of these three per channel.
    if user_data.get("ctwa_clid"):
        messaging_channel: str | None = "whatsapp"
    elif user_data.get("psid"):
        messaging_channel = "messenger"
    elif user_data.get("igsid"):
        messaging_channel = "instagram"
    else:
        messaging_channel = None
    is_business_messaging = messaging_channel is not None
    event: dict[str, Any] = {
        "event_name": _META_EVENT_NAMES.get(conversion_event, "Purchase"),
        "event_time": int(happened_at.timestamp()),
        "event_id": provider_event_id,
        "action_source": (
            "business_messaging"
            if is_business_messaging
            else _config_value(config, "actionSource", "action_source")
            or "system_generated"
        ),
        "user_data": user_data,
        "custom_data": custom_data,
    }
    if is_business_messaging:
        event["messaging_channel"] = messaging_channel
    payload = {"data": [event]}
    return (
        {
            "datasetId": dataset_id,
            "credentialRef": credential_ref,
            "consentPolicyRef": consent_policy_ref,
        },
        payload,
        missing,
    )


def _google_envelope(
    *,
    conversion_event: str,
    provider_event_id: str,
    happened_at: datetime,
    value: float | Decimal | None,
    currency: str,
    match_keys: Mapping[str, str],
    hashes: Mapping[str, str],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    """Build the offline-conversion envelope for the Google **Data Manager
    API**.

    Since 15/06/2026, offline conversion upload and Enhanced Conversions for
    Leads were migrated off the Google Ads API's ``ClickConversionService``
    and are BLOCKED there — new server-side conversion code must target the
    Data Manager API instead (verified in
    `docs/GOOGLE_TRACKING_KNOWLEDGE_2026-07.md` §1). The event contract keeps
    the literal field names ``gclid`` / ``conversion_action`` /
    ``conversion_date_time`` (snake_case, per the Data Manager API's event
    schema), not the old camelCase ``conversionAction``/``conversionDateTime``/
    ``orderId``/``conversions[]`` shape this module used to emit.
    """
    customer_id = _config_value(config, "customerId", "customer_id")
    conversion_action = _event_configuration(
        config, "conversionActions", conversion_event
    ) or _config_value(config, "conversionAction", "conversion_action")
    credential_ref = _config_value(config, "credentialRef", "credential_ref")
    consent = _config_value(config, "adUserDataConsent", "ad_user_data_consent")
    missing = tuple(
        key
        for key, present in (
            ("customer_id", customer_id),
            ("conversion_action", conversion_action),
            ("credential_ref", credential_ref),
            ("ad_user_data_consent_granted", consent == "GRANTED"),
        )
        if not present
    )
    user_identifiers = []
    if hashes.get("sha256_email"):
        user_identifiers.append({"hashedEmail": hashes["sha256_email"]})
    if hashes.get("sha256_phone"):
        user_identifiers.append({"hashedPhoneNumber": hashes["sha256_phone"]})
    event: dict[str, Any] = {
        "conversion_action": conversion_action,
        "conversion_date_time": happened_at.strftime("%Y-%m-%d %H:%M:%S+00:00"),
        "currency_code": currency,
        "order_id": provider_event_id,
        "user_identifiers": user_identifiers,
        "consent": {"ad_user_data": consent or "UNSPECIFIED"},
    }
    if value is not None:
        event["conversion_value"] = float(value)
    # gclid é o identificador primário do contrato pedido; gbraid/wbraid
    # (app/rede de parceiros) só entram como fallback quando não há gclid.
    if match_keys.get("gclid"):
        event["gclid"] = match_keys["gclid"]
    else:
        for key in ("gbraid", "wbraid"):
            if match_keys.get(key):
                event[key] = match_keys[key]
                break
    return (
        {
            "api": "data_manager_v1",
            "endpoint": (
                f"https://datamanager.googleapis.com/v1/accounts/{customer_id}:ingestEvents"
                if customer_id
                else None
            ),
            "customerId": customer_id,
            "credentialRef": credential_ref,
            "partialFailure": True,
            "validateBeforeDispatch": True,
            "dispatchValidateOnly": False,
        },
        {"events": [event], "partialFailure": True, "validateOnly": False},
        missing,
    )


def _tiktok_envelope(
    *,
    conversion_event: str,
    provider_event_id: str,
    happened_at: datetime,
    value: float | Decimal | None,
    currency: str,
    match_keys: Mapping[str, str],
    hashes: Mapping[str, str],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    pixel_code = _config_value(config, "pixelCode", "pixel_code")
    advertiser_id = _config_value(config, "advertiserId", "advertiser_id")
    event_source_url = _config_value(config, "eventSourceUrl", "event_source_url")
    credential_ref = _config_value(config, "credentialRef", "credential_ref")
    consent_policy_ref = _config_value(config, "consentPolicyRef", "consent_policy_ref")
    missing = tuple(
        key
        for key, present in (
            ("pixel_code", pixel_code),
            ("event_source_url", event_source_url),
            ("credential_ref", credential_ref),
            ("consent_policy_ref", consent_policy_ref),
        )
        if not present
    )
    user: dict[str, Any] = {}
    if hashes.get("sha256_email"):
        user["email"] = [hashes["sha256_email"]]
    if hashes.get("sha256_phone"):
        user["phone"] = [hashes["sha256_phone"]]
    if match_keys.get("ttclid"):
        user["ttclid"] = match_keys["ttclid"]
    properties: dict[str, Any] = {"currency": currency}
    if value is not None:
        properties["value"] = float(value)
    item: dict[str, Any] = {
        "event": _TIKTOK_EVENT_NAMES.get(conversion_event, "CompletePayment"),
        "event_time": int(happened_at.timestamp()),
        "event_id": provider_event_id,
        "user": user,
        "properties": properties,
        "page": {"url": event_source_url},
    }
    return (
        {
            "pixelCode": pixel_code,
            "advertiserId": advertiser_id,
            "eventSourceUrl": event_source_url,
            "endpoint": "/open_api/v1.3/event/track/",
            "credentialRef": credential_ref,
            "consentPolicyRef": consent_policy_ref,
        },
        {"event_source": "web", "event_source_id": pixel_code, "data": [item]},
        missing,
    )


def _linkedin_envelope(
    *,
    conversion_event: str,
    provider_event_id: str,
    happened_at: datetime,
    value: float | Decimal | None,
    currency: str,
    match_keys: Mapping[str, str],
    hashes: Mapping[str, str],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    account_id = _config_value(config, "accountId", "account_id")
    conversion_urn = _event_configuration(
        config, "conversionUrns", conversion_event
    ) or _config_value(config, "conversionUrn", "conversion_urn")
    credential_ref = _config_value(config, "credentialRef", "credential_ref")
    consent_policy_ref = _config_value(config, "consentPolicyRef", "consent_policy_ref")
    missing = tuple(
        key
        for key, present in (
            ("account_id", account_id),
            ("conversion_urn", conversion_urn),
            ("credential_ref", credential_ref),
            ("consent_policy_ref", consent_policy_ref),
        )
        if not present
    )
    user_ids = []
    if hashes.get("sha256_email"):
        user_ids.append(
            {"idType": "SHA256_EMAIL", "idValue": hashes["sha256_email"]}
        )
    if match_keys.get("li_fat_id"):
        user_ids.append(
            {
                "idType": "LINKEDIN_FIRST_PARTY_ADS_TRACKING_UUID",
                "idValue": match_keys["li_fat_id"],
            }
        )
    user: dict[str, Any] = {"userIds": user_ids}
    if hashes.get("sha256_external_id"):
        user["externalIds"] = [hashes["sha256_external_id"]]
    element: dict[str, Any] = {
        "conversion": conversion_urn,
        "conversionHappenedAt": int(happened_at.timestamp() * 1000),
        "eventId": provider_event_id,
        "user": user,
    }
    if value is not None:
        element["conversionValue"] = {
            "currencyCode": currency,
            "amount": str(Decimal(str(value)).quantize(Decimal("0.01"))),
        }
    return (
        {
            "accountId": account_id,
            "conversionUrn": conversion_urn,
            "credentialRef": credential_ref,
            "consentPolicyRef": consent_policy_ref,
        },
        {"elements": [element]},
        missing,
    )


def _microsoft_envelope(
    *,
    conversion_event: str,
    provider_event_id: str,
    happened_at: datetime,
    value: float | Decimal | None,
    currency: str,
    match_keys: Mapping[str, str],
    hashes: Mapping[str, str],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    customer_account_id = _config_value(
        config, "customerAccountId", "customer_account_id", "accountId", "account_id"
    )
    customer_id = _config_value(config, "customerId", "customer_id")
    conversion_name = _event_configuration(
        config, "conversionNames", conversion_event
    ) or _config_value(config, "conversionName", "conversion_name")
    credential_ref = _config_value(config, "credentialRef", "credential_ref")
    consent_policy_ref = _config_value(config, "consentPolicyRef", "consent_policy_ref")
    missing = tuple(
        key
        for key, present in (
            ("customer_account_id", customer_account_id),
            ("customer_id", customer_id),
            ("conversion_name", conversion_name),
            ("credential_ref", credential_ref),
            ("consent_policy_ref", consent_policy_ref),
        )
        if not present
    )
    conversion: dict[str, Any] = {
        "ConversionName": conversion_name,
        "ConversionTime": happened_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "ConversionCurrencyCode": currency,
    }
    if value is not None:
        conversion["ConversionValue"] = float(value)
    if match_keys.get("msclkid"):
        conversion["MicrosoftClickId"] = match_keys["msclkid"]
    if hashes.get("sha256_email"):
        conversion["HashedEmailAddress"] = hashes["sha256_email"]
    if hashes.get("sha256_phone"):
        conversion["HashedPhoneNumber"] = hashes["sha256_phone"]
    return (
        {
            "operation": "ApplyOfflineConversions",
            "apiVersion": "13",
            "customerAccountId": customer_account_id,
            "customerId": customer_id,
            "credentialRef": credential_ref,
            "consentPolicyRef": consent_policy_ref,
            "providerEventId": provider_event_id,
        },
        {"OfflineConversions": [conversion]},
        missing,
    )


def build_provider_feedback_envelope(
    *,
    provider: str,
    conversion_event: str,
    source_event_id: UUID,
    happened_at: datetime,
    value: float | Decimal | None,
    currency: str,
    match_keys: Mapping[str, str],
    workspace_id: UUID,
    client_id: UUID | None,
    email: str | None,
    phone: str | None,
    config: Mapping[str, Any] | None = None,
    ad_data_consent: str | None = None,
    consent_evidence_ref: str | None = None,
) -> ProviderFeedbackEnvelope:
    moment = _normalized_datetime(happened_at)
    provider_event_id = f"klm-{source_event_id}"
    consent_state = _normalized_consent_state(ad_data_consent)
    normalized_consent_evidence_ref = str(consent_evidence_ref or "").strip() or None
    hashes = normalized_hashed_identifiers(
        workspace_id=workspace_id,
        client_id=client_id,
        email=email,
        phone=phone,
    )
    valid_match_keys, rejected_match_keys = validated_match_keys(provider, match_keys)
    if consent_state != "GRANTED":
        # Retain no provider identifiers in a dispatchable payload unless the
        # specific conversion carries affirmative, auditable consent.
        hashes = {}
        valid_match_keys = {}
    builder = {
        "meta_ads": _meta_envelope,
        "google_ads": _google_envelope,
        "tiktok_ads": _tiktok_envelope,
        "linkedin_ads": _linkedin_envelope,
        "microsoft_ads": _microsoft_envelope,
    }.get(provider)
    if builder is None:
        raise ValueError(f"Provedor de feedback não suportado: {provider}")
    destination, payload, missing = builder(
        conversion_event=conversion_event,
        provider_event_id=provider_event_id,
        happened_at=moment,
        value=value,
        currency=currency,
        match_keys=valid_match_keys,
        hashes=hashes,
        config=config or {},
    )
    consent_missing = []
    if consent_state != "GRANTED":
        consent_missing.append("subject_ad_data_consent_granted")
    if not normalized_consent_evidence_ref:
        consent_missing.append("subject_consent_evidence_ref")
    missing = tuple(dict.fromkeys((*missing, *consent_missing)))
    destination["subjectConsent"] = {
        "state": consent_state,
        "evidenceRef": normalized_consent_evidence_ref,
    }
    match_key_names = tuple(sorted({*valid_match_keys, *hashes}))
    matching_mode = _matching_mode(valid_match_keys, hashes)
    return ProviderFeedbackEnvelope(
        provider=provider,
        provider_event_id=provider_event_id,
        conversion_event=conversion_event,
        destination=destination,
        provider_payload=payload,
        match_key_names=match_key_names,
        matching_mode=matching_mode,
        consent_state=consent_state,
        consent_evidence_ref=normalized_consent_evidence_ref,
        ready_for_dispatch=not missing and matching_mode != "unmatched",
        missing_configuration=missing,
        rejected_match_key_names=rejected_match_keys,
    )


__all__ = [
    "IDENTITY_MATCH_PROVIDERS",
    "ProviderFeedbackEnvelope",
    "build_provider_feedback_envelope",
    "canonical_payload_sha256",
    "has_usable_first_party_identity",
    "normalize_email",
    "normalize_phone",
    "normalized_hashed_identifiers",
    "provider_feedback_config",
    "validated_match_keys",
]
