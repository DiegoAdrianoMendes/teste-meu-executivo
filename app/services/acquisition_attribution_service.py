"""Canonical, provider-neutral acquisition attribution for KLM tracking."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from app.models.klm_operations import AcquisitionEvent
from app.services.ads_conversion_feedback_service import validated_match_keys

PROVIDER_LABELS = {
    "meta_ads": "Meta Ads",
    "google_ads": "Google Ads",
    "tiktok_ads": "TikTok Ads",
    "linkedin_ads": "LinkedIn Ads",
    "microsoft_ads": "Microsoft Ads",
}

PROVIDER_CLICK_IDS = {
    # psid/igsid: page-scoped Messenger/Instagram user IDs the social capture
    # records as the DM thread identity (see `social_event_adapter_service.py`
    # and `ads_conversion_feedback_service.py` for the business-messaging
    # envelope built from them, mirroring the ctwa_clid/WhatsApp pattern).
    "meta_ads": ("fbc", "fbp", "fbclid", "ctwa_clid", "psid", "igsid"),
    "google_ads": ("gclid", "gbraid", "wbraid"),
    "tiktok_ads": ("ttclid",),
    "linkedin_ads": ("li_fat_id",),
    "microsoft_ads": ("msclkid",),
}

CLICK_ID_PROVIDER = {
    key: provider
    for provider, keys in PROVIDER_CLICK_IDS.items()
    for key in keys
}

PROVIDER_ALIASES = {
    "meta": "meta_ads",
    "meta_ads": "meta_ads",
    "meta_lead_ads": "meta_ads",
    "facebook": "meta_ads",
    "facebook_ads": "meta_ads",
    "instagram": "meta_ads",
    "instagram_ads": "meta_ads",
    "google": "google_ads",
    "google_ads": "google_ads",
    "google_search": "google_ads",
    "tiktok": "tiktok_ads",
    "tiktok_ads": "tiktok_ads",
    "tiktok_lead_ads": "tiktok_ads",
    "linkedin": "linkedin_ads",
    "linkedin_ads": "linkedin_ads",
    "microsoft": "microsoft_ads",
    "microsoft_ads": "microsoft_ads",
    "bing_ads": "microsoft_ads",
}


def _normalized_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _first(mapping: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _metadata_sources(metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = [metadata]
    for key in ("attribution", "acquisition", "tracking", "utm"):
        nested = metadata.get(key)
        if isinstance(nested, Mapping):
            sources.append(nested)
    references = metadata.get("providerReferences") or metadata.get("provider_references")
    if isinstance(references, Mapping):
        sources.append(references)
    return sources


def _first_from_sources(sources: list[Mapping[str, Any]], *keys: str) -> str | None:
    for source in sources:
        value = _first(source, *keys)
        if value:
            return value
    return None


def _provider_from_event(event: AcquisitionEvent) -> tuple[str | None, tuple[str, ...], str]:
    metadata = event.event_metadata or {}
    sources = _metadata_sources(metadata)
    click_ids = {
        _normalized_token(key): str(value).strip()
        for key, value in (event.click_ids or {}).items()
        if str(value or "").strip()
    }
    click_providers = {
        CLICK_ID_PROVIDER[key]
        for key in click_ids
        if key in CLICK_ID_PROVIDER
    }
    explicit_raw = _first_from_sources(sources, "provider", "adsProvider", "ads_provider")
    explicit = PROVIDER_ALIASES.get(_normalized_token(explicit_raw)) if explicit_raw else None
    if explicit and click_providers and click_providers != {explicit}:
        return None, tuple(sorted(click_ids)), "conflicting_provider_evidence"
    if len(click_providers) > 1:
        return None, tuple(sorted(click_ids)), "multiple_provider_click_ids"
    if explicit:
        return explicit, tuple(sorted(click_ids)), "explicit_provider"
    if len(click_providers) == 1:
        return next(iter(click_providers)), tuple(sorted(click_ids)), "provider_click_id"
    for candidate in (event.source, event.channel):
        provider = PROVIDER_ALIASES.get(_normalized_token(candidate))
        if provider:
            return provider, tuple(sorted(click_ids)), "explicit_source_or_channel"
    return None, tuple(sorted(click_ids)), "provider_not_identified"


def _entity_name(
    names: Mapping[tuple[str, str, str], str],
    provider: str | None,
    entity_type: str,
    external_id: str | None,
) -> str | None:
    if not provider or not external_id:
        return None
    return names.get((provider, entity_type, external_id))


def _provider_console_url(
    provider: str | None,
    *,
    account_id: str | None,
    campaign_id: str | None,
) -> str | None:
    if provider == "meta_ads":
        params: dict[str, str] = {}
        if account_id:
            params["act"] = account_id
        if campaign_id:
            params["selected_campaign_ids"] = campaign_id
        suffix = f"?{urlencode(params)}" if params else ""
        return f"https://www.facebook.com/adsmanager/manage/campaigns{suffix}"
    if provider == "google_ads":
        params = {"campaignId": campaign_id} if campaign_id else {}
        suffix = f"?{urlencode(params)}" if params else ""
        return f"https://ads.google.com/aw/campaigns{suffix}"
    if provider == "tiktok_ads":
        params = {"aadvid": account_id} if account_id else {}
        suffix = f"?{urlencode(params)}" if params else ""
        return f"https://ads.tiktok.com/i18n/perf/campaign{suffix}"
    if provider == "linkedin_ads":
        if account_id and campaign_id:
            return (
                "https://www.linkedin.com/campaignmanager/accounts/"
                f"{account_id}/campaigns/{campaign_id}"
            )
        if account_id:
            return f"https://www.linkedin.com/campaignmanager/accounts/{account_id}"
        return "https://www.linkedin.com/campaignmanager"
    if provider == "microsoft_ads":
        return "https://ads.microsoft.com/"
    return None


@dataclass(frozen=True)
class AttributionEvidence:
    provider: str | None
    channel: str
    source: str | None
    medium: str | None
    account_id: str | None
    account_name: str | None
    campaign_id: str | None
    campaign_name: str | None
    ad_set_id: str | None
    ad_set_name: str | None
    ad_id: str | None
    ad_name: str | None
    creative_id: str | None
    creative_name: str | None
    form_id: str | None
    form_name: str | None
    placement: str | None
    landing_page_url: str | None
    source_url: str | None
    click_id_keys: tuple[str, ...]
    evidence_level: str
    evidence_reason: str
    hierarchy_completeness: int
    missing_levels: tuple[str, ...]
    provider_console_url: str | None

    @property
    def group_key(self) -> tuple[str, ...]:
        return (
            self.provider or "unattributed",
            self.channel,
            self.account_id or self.account_name or "",
            self.campaign_id or self.campaign_name or "",
            self.ad_set_id or self.ad_set_name or "",
            self.ad_id or self.ad_name or "",
            self.creative_id or self.creative_name or "",
            self.form_id or self.form_name or "",
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_label": PROVIDER_LABELS.get(self.provider or "", "Nao atribuido"),
            "channel": self.channel,
            "source": self.source,
            "medium": self.medium,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "ad_set_id": self.ad_set_id,
            "ad_set_name": self.ad_set_name,
            "ad_id": self.ad_id,
            "ad_name": self.ad_name,
            "creative_id": self.creative_id,
            "creative_name": self.creative_name,
            "form_id": self.form_id,
            "form_name": self.form_name,
            "placement": self.placement,
            "landing_page_url": self.landing_page_url,
            "source_url": self.source_url,
            "click_id_keys": list(self.click_id_keys),
            "evidence_level": self.evidence_level,
            "evidence_reason": self.evidence_reason,
            "hierarchy_completeness": self.hierarchy_completeness,
            "missing_levels": list(self.missing_levels),
            "provider_console_url": self.provider_console_url,
        }


def extract_attribution(
    event: AcquisitionEvent,
    *,
    entity_names: Mapping[tuple[str, str, str], str] | None = None,
) -> AttributionEvidence:
    names = entity_names or {}
    metadata = event.event_metadata or {}
    sources = _metadata_sources(metadata)
    provider, click_id_keys, evidence_reason = _provider_from_event(event)
    account_id = _first_from_sources(sources, "accountId", "account_id", "advertiserId")
    campaign_id = _first_from_sources(sources, "campaignId", "campaign_id", "utmId")
    ad_set_id = _first_from_sources(
        sources, "adSetId", "adsetId", "ad_set_id", "adGroupId", "adgroup_id"
    )
    ad_id = _first_from_sources(sources, "adId", "ad_id")
    creative_id = _first_from_sources(sources, "creativeId", "creative_id")
    form_id = _first_from_sources(sources, "formId", "form_id", "contentId")
    account_name = _first_from_sources(sources, "accountName", "account_name") or _entity_name(
        names, provider, "account", account_id
    )
    campaign_name = (
        _first_from_sources(sources, "campaignName", "campaign_name")
        or event.campaign
        or _entity_name(names, provider, "campaign", campaign_id)
    )
    ad_set_name = _first_from_sources(
        sources, "adSetName", "adsetName", "ad_set", "adSet", "adgroupName"
    ) or _entity_name(names, provider, "ad_set", ad_set_id)
    ad_name = _first_from_sources(sources, "adName", "ad_name", "ad") or _entity_name(
        names, provider, "ad", ad_id
    )
    creative_name = _first_from_sources(
        sources,
        "creativeName",
        "creative_name",
        "creative",
        "content",
        "utmContent",
        "utm_content",
    ) or _entity_name(names, provider, "creative", creative_id)
    form_name = _first_from_sources(sources, "formName", "form_name") or _entity_name(
        names, provider, "form", form_id
    )
    placement = _first_from_sources(sources, "placement", "publisherPlatform", "position")
    landing_page_url = _first_from_sources(
        sources, "landingPage", "landingPageUrl", "landing_page_url"
    )
    source_url = _first_from_sources(sources, "sourceUrl", "source_url", "referrer")

    stable_ids = (account_id, campaign_id, ad_set_id, ad_id, creative_id, form_id)
    if provider and (any(stable_ids) or any(key in CLICK_ID_PROVIDER for key in click_id_keys)):
        evidence_level = "exact"
    elif provider or any((campaign_name, ad_set_name, ad_name, creative_name)):
        evidence_level = "named"
    else:
        evidence_level = "unattributed"

    hierarchy = {
        "campaign": bool(campaign_id or campaign_name),
        "ad_set": bool(ad_set_id or ad_set_name),
        "ad": bool(ad_id or ad_name),
        "creative": bool(creative_id or creative_name),
    }
    missing_levels = tuple(key for key, present in hierarchy.items() if not present)
    hierarchy_completeness = round(
        (sum(1 for present in hierarchy.values() if present) / len(hierarchy)) * 100
    )
    return AttributionEvidence(
        provider=provider,
        channel=(event.channel or "unknown").strip() or "unknown",
        source=event.source,
        medium=event.medium,
        account_id=account_id,
        account_name=account_name,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        ad_set_id=ad_set_id,
        ad_set_name=ad_set_name,
        ad_id=ad_id,
        ad_name=ad_name,
        creative_id=creative_id,
        creative_name=creative_name,
        form_id=form_id,
        form_name=form_name,
        placement=placement,
        landing_page_url=landing_page_url,
        source_url=source_url,
        click_id_keys=click_id_keys,
        evidence_level=evidence_level,
        evidence_reason=evidence_reason,
        hierarchy_completeness=hierarchy_completeness,
        missing_levels=missing_levels,
        provider_console_url=_provider_console_url(
            provider,
            account_id=account_id,
            campaign_id=campaign_id,
        ),
    )


def feedback_click_ids(event: AcquisitionEvent, provider: str) -> dict[str, str]:
    values = {
        _normalized_token(key): str(value).strip()
        for key, value in (event.click_ids or {}).items()
        if str(value or "").strip()
    }
    allowed = PROVIDER_CLICK_IDS.get(provider, ())
    if provider == "meta_ads":
        result: dict[str, str] = {}
        if values.get("fbc"):
            result["fbc"] = values["fbc"]
        elif values.get("fbclid"):
            timestamp_ms = int(attribution_event_timestamp(event).timestamp() * 1000)
            result["fbc"] = f"fb.1.{timestamp_ms}.{values['fbclid']}"
        if values.get("fbp"):
            result["fbp"] = values["fbp"]
        if values.get("ctwa_clid"):
            # Click-to-WhatsApp conversations match by ctwa_clid on the
            # business-messaging envelope (Conversions API for BM).
            result["ctwa_clid"] = values["ctwa_clid"]
        if values.get("psid"):
            # Messenger DM touches match by page-scoped user ID, same envelope.
            result["psid"] = values["psid"]
        if values.get("igsid"):
            # Instagram DM touches match by IG-scoped user ID, same envelope.
            result["igsid"] = values["igsid"]
        return validated_match_keys(provider, result)[0]
    if provider == "google_ads":
        # Google requires one click identifier per conversion. Preserve a stable priority.
        for key in allowed:
            if key in values:
                return validated_match_keys(provider, {key: values[key]})[0]
        return {}
    result = {key: values[key] for key in allowed if key in values}
    return validated_match_keys(provider, result)[0]


def feedback_eligibility(
    evidence: AttributionEvidence,
    event: AcquisitionEvent,
    *,
    has_user_identifiers: bool = False,
) -> tuple[bool, str]:
    if evidence.provider not in PROVIDER_LABELS:
        return False, "provider_not_supported"
    click_ids = feedback_click_ids(event, evidence.provider)
    if click_ids:
        return True, "provider_click_id"
    if has_user_identifiers and evidence.provider in {
        "meta_ads",
        "google_ads",
        "tiktok_ads",
        "linkedin_ads",
        "microsoft_ads",
    }:
        return True, "hashed_first_party_identity"
    # Campaign and creative IDs prove reporting attribution, but do not identify a
    # person. A canonical email or phone must exist before hashed matching is allowed.
    return False, "missing_provider_match_key"


def attribution_event_timestamp(event: AcquisitionEvent) -> datetime:
    value = event.happened_at
    return value if value.tzinfo else value.replace(tzinfo=datetime.now().astimezone().tzinfo)


__all__ = [
    "AttributionEvidence",
    "PROVIDER_CLICK_IDS",
    "PROVIDER_LABELS",
    "attribution_event_timestamp",
    "extract_attribution",
    "feedback_click_ids",
    "feedback_eligibility",
]
