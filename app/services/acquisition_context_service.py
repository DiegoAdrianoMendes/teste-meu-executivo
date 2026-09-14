"""Compact, tenant-scoped acquisition context for AI workloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_attribution import CampaignAttribution
from app.models.klm_operations import AcquisitionEvent

ACQUISITION_CONTEXT_QUERY_LIMIT = 16
ACQUISITION_CONTEXT_SCHEMA_VERSION = "klm.acquisition-context.v1"
_MAX_TEXT_LENGTH = 240
_SAFE_METADATA_KEYS = (
    "adId",
    "adName",
    "adSetId",
    "adSetName",
    "campaignId",
    "campaignName",
    "contentId",
    "formId",
    "formName",
    "landingPage",
    "placement",
    "postId",
    "referrer",
    "sourceUrl",
    "utmCampaign",
    "utmContent",
    "utmMedium",
    "utmSource",
    "utmTerm",
)


def _text(value: object | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:_MAX_TEXT_LENGTH] or None


def _timestamp(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _text(value)
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _money(value: Decimal | float | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)), ".2f")


def _safe_metadata(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    flattened: dict[str, object] = {}

    def collect(value: object, *, depth: int = 0) -> None:
        if not isinstance(value, dict) or depth > 2:
            return
        for key, nested_value in value.items():
            normalized_key = "".join(
                character for character in str(key).casefold() if character.isalnum()
            )
            if isinstance(nested_value, dict):
                collect(nested_value, depth=depth + 1)
            elif normalized_key not in flattened:
                flattened[normalized_key] = nested_value

    collect(raw)
    output: dict[str, str] = {}
    for key in _SAFE_METADATA_KEYS:
        normalized_key = "".join(character for character in key.casefold() if character.isalnum())
        value = flattened.get(normalized_key)
        normalized = _text(value)
        if normalized:
            output[key] = normalized
    return output


def _unique_text(values: list[object | None], *, limit: int = 12) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _text(value)
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        output.append(normalized)
        if len(output) >= limit:
            break
    return output


@dataclass(frozen=True, slots=True)
class AcquisitionContextSnapshot:
    card_data: dict[str, Any]
    evidence_content: str | None
    source_id: str
    source_version: str | None
    query_terms: tuple[str, ...]
    event_count: int

    @property
    def has_context(self) -> bool:
        return bool(
            self.card_data.get("declared_source") or self.event_count or self.evidence_content
        )

    def enrich_query(self, query_text: str, *, max_length: int = 2_000) -> str:
        """Add compact acquisition hints without exposing raw tracking identifiers."""
        parts = [str(query_text or "").strip(), *self.query_terms]
        return " ".join(part for part in parts if part).strip()[:max_length]


class AcquisitionContextService:
    """Build one bounded source of truth for attribution-aware agents."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build_for_matter(self, matter: Any) -> AcquisitionContextSnapshot:
        return await self.build(
            workspace_id=matter.workspace_id,
            matter_id=matter.id,
            client_id=getattr(matter, "client_id", None),
            declared_source=getattr(matter, "lead_source", None),
            campaign_attribution=getattr(matter, "campaign_attribution", None),
            manual_context_revision=getattr(matter, "manual_context_revision", 0),
        )

    async def build(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        client_id: UUID | None,
        declared_source: str | None,
        campaign_attribution: CampaignAttribution | None,
        manual_context_revision: int = 0,
    ) -> AcquisitionContextSnapshot:
        identity_filter = AcquisitionEvent.matter_id == matter_id
        if client_id is not None:
            identity_filter = or_(
                identity_filter,
                and_(
                    AcquisitionEvent.matter_id.is_(None),
                    AcquisitionEvent.client_id == client_id,
                ),
            )
        filters = [
            AcquisitionEvent.workspace_id == workspace_id,
            identity_filter,
        ]
        records = list(
            (
                await self.db.scalars(
                    select(AcquisitionEvent)
                    .where(*filters)
                    .order_by(
                        desc(AcquisitionEvent.happened_at),
                        desc(AcquisitionEvent.id),
                    )
                    .limit(ACQUISITION_CONTEXT_QUERY_LIMIT)
                )
            ).all()
        )
        records.reverse()

        journey = [self._event_payload(record) for record in records]
        measured_sources = _unique_text(
            [getattr(campaign_attribution, "source", None)] + [record.source for record in records]
        )
        measured_channels = _unique_text(
            [getattr(campaign_attribution, "channel", None)]
            + [record.channel for record in records]
        )
        measured_campaigns = _unique_text(
            [getattr(campaign_attribution, "campaign", None)]
            + [record.campaign for record in records]
        )
        attribution = self._attribution_payload(campaign_attribution)
        first_touch = journey[0]["happened_at"] if journey else attribution.get("first_touch_at")
        last_touch = journey[-1]["happened_at"] if journey else attribution.get("last_touch_at")
        declared = _text(declared_source)
        primary_source = declared or (measured_sources[0] if measured_sources else None)

        card_data: dict[str, Any] = {
            "schema_version": ACQUISITION_CONTEXT_SCHEMA_VERSION,
            "source_precedence": "declared_crm_source_over_measured_attribution",
            "declared_source": declared,
            "primary_source": primary_source,
            "manual_context_revision": max(int(manual_context_revision or 0), 0),
            "measured": {
                "sources": measured_sources,
                "channels": measured_channels,
                "campaigns": measured_campaigns,
                "first_touch_at": first_touch,
                "last_touch_at": last_touch,
                "touch_count": len(journey),
                "last_event": journey[-1]["event_name"] if journey else None,
            },
        }
        evidence_payload = {
            "attribution": attribution,
            "journey": journey,
            "scope": {
                "workspace_id": str(workspace_id),
                "matter_id": str(matter_id),
                "client_fallback_only_for_unbound_events": True,
            },
            "source_policy": {
                "crm_declared_source_is_authoritative_for_agent_actions": True,
                "measured_attribution_is_preserved_for_analytics": True,
            },
        }
        has_evidence = bool(journey or any(attribution.values()))
        evidence_content = (
            json.dumps(
                evidence_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if has_evidence
            else None
        )
        digest_material = evidence_content or json.dumps(
            card_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_version = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
        query_terms = _unique_text(
            [
                primary_source,
                *measured_sources,
                *measured_channels,
                *measured_campaigns,
                *[item["event_name"] for item in journey],
                *[value for item in journey for value in item.get("metadata", {}).values()],
            ],
            limit=16,
        )
        return AcquisitionContextSnapshot(
            card_data=card_data,
            evidence_content=evidence_content,
            source_id=f"matter:{matter_id}:acquisition",
            source_version=source_version,
            query_terms=tuple(query_terms),
            event_count=len(journey),
        )

    @staticmethod
    def _attribution_payload(
        attribution: CampaignAttribution | None,
    ) -> dict[str, str | None]:
        return {
            "source": _text(getattr(attribution, "source", None)),
            "medium": _text(getattr(attribution, "medium", None)),
            "campaign": _text(getattr(attribution, "campaign", None)),
            "content": _text(getattr(attribution, "content", None)),
            "term": _text(getattr(attribution, "term", None)),
            "channel": _text(getattr(attribution, "channel", None)),
            "first_touch_at": _timestamp(getattr(attribution, "first_touch_at", None)),
            "last_touch_at": _timestamp(getattr(attribution, "last_touch_at", None)),
        }

    @staticmethod
    def _event_payload(event: AcquisitionEvent) -> dict[str, Any]:
        click_ids = event.click_ids if isinstance(event.click_ids, dict) else {}
        payload: dict[str, Any] = {
            "id": str(event.id),
            "event_name": _text(event.event_name),
            "channel": _text(event.channel),
            "source": _text(event.source),
            "medium": _text(event.medium),
            "campaign": _text(event.campaign),
            "happened_at": _timestamp(event.happened_at),
            "value": _money(event.value),
            "currency": _text(event.currency),
            "click_id_providers": sorted(
                str(key)[:60] for key, value in click_ids.items() if _text(value)
            ),
            "metadata": _safe_metadata(event.event_metadata),
        }
        return {key: value for key, value in payload.items() if value not in (None, {}, [])}
