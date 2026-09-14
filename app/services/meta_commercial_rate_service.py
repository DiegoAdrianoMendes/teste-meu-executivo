"""Commercial Meta rate administration and tenant-safe campaign summaries."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.meta_commercial_rate import MetaCommercialRateCard
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
)
from app.schemas.meta_commercial_rate import (
    CampaignCommercialChargeRead,
    CommercialChargeCurrencyTotalRead,
    CommercialChargeLineRead,
    MetaCommercialRateCardUpsert,
    MissingCommercialRateRead,
    WhatsAppCommercialChargeSummaryRead,
)

_RATE_CARD_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,119}$")
_DELIVERED_STATUSES = {
    CampaignRecipientStatus.delivered.value,
    CampaignRecipientStatus.read.value,
}
_ZERO = Decimal("0")


class MetaCommercialRateConflictError(ValueError):
    """The requested rate-card version conflicts with commercial history."""


class WhatsAppCommercialCampaignNotFoundError(LookupError):
    """The requested campaign does not belong to the active workspace."""


@dataclass(frozen=True)
class _Country:
    code: str
    resolution: str


@dataclass(frozen=True)
class _ResolvedRate:
    card: MetaCommercialRateCard | None
    rate_match: str | None
    missing_reason: str | None
    candidate_keys: tuple[str, ...] = ()


@dataclass
class _ChargeLineAccumulator:
    card: MetaCommercialRateCard
    template_category: str
    recipient_country_code: str
    country_resolution: str
    rate_match: str
    estimated_recipient_count: int = 0
    confirmed_recipient_count: int = 0

    def add(self, stage: str, count: int) -> None:
        if stage == "estimated":
            self.estimated_recipient_count += count
        else:
            self.confirmed_recipient_count += count

    def to_schema(self) -> CommercialChargeLineRead:
        commercial_rate = Decimal(self.card.commercial_rate)
        return CommercialChargeLineRead(
            rate_card_key=self.card.rate_card_key,
            template_category=self.template_category,
            recipient_country_code=self.recipient_country_code,
            country_resolution=self.country_resolution,
            rate_country_code=self.card.country_code,
            rate_match=self.rate_match,
            currency=self.card.currency,
            commercial_rate=commercial_rate,
            rate_effective_from=_utc(self.card.effective_from),
            rate_effective_to=_utc_optional(self.card.effective_to),
            rate_source_url=self.card.source_url,
            rate_checked_at=_utc(self.card.checked_at),
            estimated_recipient_count=self.estimated_recipient_count,
            confirmed_recipient_count=self.confirmed_recipient_count,
            estimated_commercial_charge=(
                commercial_rate * self.estimated_recipient_count
            ),
            confirmed_commercial_charge=(
                commercial_rate * self.confirmed_recipient_count
            ),
        )


@dataclass
class _MissingRateAccumulator:
    campaign_id: UUID
    template_category: str | None
    recipient_country_code: str
    country_resolution: str
    charge_stage: str
    reason: str
    candidate_rate_card_keys: tuple[str, ...]
    rate_reference_from: datetime
    rate_reference_to: datetime
    recipient_count: int = 0

    def add(self, count: int, reference_at: datetime) -> None:
        self.recipient_count += count
        self.rate_reference_from = min(self.rate_reference_from, reference_at)
        self.rate_reference_to = max(self.rate_reference_to, reference_at)

    def to_schema(self) -> MissingCommercialRateRead:
        return MissingCommercialRateRead(
            campaign_id=self.campaign_id,
            template_category=self.template_category,
            recipient_country_code=self.recipient_country_code,
            country_resolution=self.country_resolution,
            charge_stage=self.charge_stage,
            recipient_count=self.recipient_count,
            rate_reference_from=self.rate_reference_from,
            rate_reference_to=self.rate_reference_to,
            reason=self.reason,
            candidate_rate_card_keys=list(self.candidate_rate_card_keys),
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_optional(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None


def _status_value(value: object) -> str:
    return str(getattr(value, "value", value))


class MetaCommercialRateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_rate_cards(
        self,
        *,
        include_inactive: bool = True,
    ) -> list[MetaCommercialRateCard]:
        statement = select(MetaCommercialRateCard)
        if not include_inactive:
            statement = statement.where(MetaCommercialRateCard.is_active.is_(True))
        statement = statement.order_by(
            MetaCommercialRateCard.category.asc(),
            MetaCommercialRateCard.country_code.asc(),
            MetaCommercialRateCard.currency.asc(),
            MetaCommercialRateCard.effective_from.desc(),
            MetaCommercialRateCard.rate_card_key.asc(),
        )
        return list((await self.db.scalars(statement)).all())

    async def upsert_rate_card(
        self,
        *,
        rate_card_key: str,
        payload: MetaCommercialRateCardUpsert,
    ) -> MetaCommercialRateCard:
        normalized_key = rate_card_key.strip().lower()
        if not _RATE_CARD_KEY_PATTERN.fullmatch(normalized_key):
            raise MetaCommercialRateConflictError(
                "rateCardKey deve ter de 3 a 120 caracteres e usar apenas letras "
                "minúsculas, números, _ ou -."
            )

        card = await self.db.scalar(
            select(MetaCommercialRateCard)
            .where(MetaCommercialRateCard.rate_card_key == normalized_key)
            .with_for_update()
        )
        values = payload.model_dump()
        await self._ensure_non_overlapping(
            category=values["category"],
            country_code=values["country_code"],
            currency=values["currency"],
            effective_from=values["effective_from"],
            effective_to=values["effective_to"],
            exclude_id=card.id if card is not None else None,
        )

        if card is None:
            card = MetaCommercialRateCard(
                rate_card_key=normalized_key,
                **values,
            )
            self.db.add(card)
        else:
            for field_name, value in values.items():
                setattr(card, field_name, value)
        await self.db.flush()
        await self.db.refresh(card)
        return card

    async def summarize_workspace_campaigns(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID | None = None,
    ) -> WhatsAppCommercialChargeSummaryRead:
        campaign_statement = (
            select(WhatsAppCampaign)
            .where(WhatsAppCampaign.workspace_id == workspace_id)
            .options(
                selectinload(WhatsAppCampaign.template_snapshot),
                selectinload(WhatsAppCampaign.recipients),
            )
            .order_by(WhatsAppCampaign.created_at.desc(), WhatsAppCampaign.id.desc())
        )
        if campaign_id is not None:
            campaign_statement = campaign_statement.where(WhatsAppCampaign.id == campaign_id)
        campaigns = list((await self.db.scalars(campaign_statement)).unique().all())
        if campaign_id is not None and not campaigns:
            raise WhatsAppCommercialCampaignNotFoundError(
                "Campanha oficial não encontrada no workspace ativo."
            )

        rate_cards = list(
            (
                await self.db.scalars(
                    select(MetaCommercialRateCard).where(
                        MetaCommercialRateCard.is_active.is_(True)
                    )
                )
            ).all()
        )
        generated_at = datetime.now(UTC)
        campaign_summaries = [
            self._summarize_campaign(campaign, rate_cards, generated_at)
            for campaign in campaigns
        ]
        all_lines = [
            line
            for campaign_summary in campaign_summaries
            for line in campaign_summary.commercial_charge_lines
        ]
        all_missing = [
            item
            for campaign_summary in campaign_summaries
            for item in campaign_summary.missing_rates
        ]
        return WhatsAppCommercialChargeSummaryRead(
            workspace_id=workspace_id,
            requested_campaign_id=campaign_id,
            generated_at=generated_at,
            campaigns=campaign_summaries,
            totals_by_currency=self._totals_by_currency(all_lines),
            missing_rates=all_missing,
        )

    async def _ensure_non_overlapping(
        self,
        *,
        category: str,
        country_code: str,
        currency: str,
        effective_from: datetime,
        effective_to: datetime | None,
        exclude_id: UUID | None,
    ) -> None:
        statement = select(MetaCommercialRateCard.rate_card_key).where(
            MetaCommercialRateCard.category == category,
            MetaCommercialRateCard.country_code == country_code,
            MetaCommercialRateCard.currency == currency,
            or_(
                MetaCommercialRateCard.effective_to.is_(None),
                MetaCommercialRateCard.effective_to > effective_from,
            ),
        )
        if effective_to is not None:
            statement = statement.where(
                MetaCommercialRateCard.effective_from < effective_to
            )
        if exclude_id is not None:
            statement = statement.where(MetaCommercialRateCard.id != exclude_id)
        conflicting_key = await self.db.scalar(statement.limit(1))
        if conflicting_key is not None:
            raise MetaCommercialRateConflictError(
                "A vigência se sobrepõe ao rate card "
                f"'{conflicting_key}' para category+countryCode+currency."
            )

    def _summarize_campaign(
        self,
        campaign: WhatsAppCampaign,
        rate_cards: list[MetaCommercialRateCard],
        generated_at: datetime,
    ) -> CampaignCommercialChargeRead:
        raw_category = (
            campaign.template_snapshot.category
            if campaign.template_snapshot is not None
            else None
        )
        category = raw_category.strip().lower() if raw_category and raw_category.strip() else None
        recipients = list(campaign.recipients or [])
        eligible_recipients = max(int(campaign.eligible_recipients or 0), len(recipients))
        estimate_at = self._estimate_reference_at(campaign, generated_at)
        line_accumulators: dict[tuple[object, ...], _ChargeLineAccumulator] = {}
        missing_accumulators: dict[tuple[object, ...], _MissingRateAccumulator] = {}

        for recipient in recipients:
            country = self._recipient_country(recipient)
            self._apply_charge_stage(
                campaign_id=campaign.id,
                category=category,
                country=country,
                stage="estimated",
                count=1,
                reference_at=estimate_at,
                rate_cards=rate_cards,
                line_accumulators=line_accumulators,
                missing_accumulators=missing_accumulators,
            )
            if _status_value(recipient.status) in _DELIVERED_STATUSES:
                self._apply_charge_stage(
                    campaign_id=campaign.id,
                    category=category,
                    country=country,
                    stage="confirmed",
                    count=1,
                    reference_at=self._confirmed_reference_at(recipient, generated_at),
                    rate_cards=rate_cards,
                    line_accumulators=line_accumulators,
                    missing_accumulators=missing_accumulators,
                )

        aggregate_remainder = eligible_recipients - len(recipients)
        if aggregate_remainder > 0:
            self._apply_charge_stage(
                campaign_id=campaign.id,
                category=category,
                country=_Country("*", "campaign_aggregate_fallback"),
                stage="estimated",
                count=aggregate_remainder,
                reference_at=estimate_at,
                rate_cards=rate_cards,
                line_accumulators=line_accumulators,
                missing_accumulators=missing_accumulators,
            )

        charge_lines = sorted(
            (item.to_schema() for item in line_accumulators.values()),
            key=lambda item: (
                item.currency,
                item.rate_card_key,
                item.recipient_country_code,
                item.country_resolution,
            ),
        )
        missing_rates = sorted(
            (item.to_schema() for item in missing_accumulators.values()),
            key=lambda item: (
                item.charge_stage,
                item.recipient_country_code,
                item.country_resolution,
                item.reason,
            ),
        )
        confirmed_recipients = sum(
            1
            for recipient in recipients
            if _status_value(recipient.status) in _DELIVERED_STATUSES
        )
        return CampaignCommercialChargeRead(
            campaign_id=campaign.id,
            title=campaign.title,
            template_category=category,
            eligible_recipients=eligible_recipients,
            confirmed_recipients=confirmed_recipients,
            commercial_charge_lines=charge_lines,
            totals_by_currency=self._totals_by_currency(charge_lines),
            missing_rates=missing_rates,
        )

    def _apply_charge_stage(
        self,
        *,
        campaign_id: UUID,
        category: str | None,
        country: _Country,
        stage: str,
        count: int,
        reference_at: datetime,
        rate_cards: list[MetaCommercialRateCard],
        line_accumulators: dict[tuple[object, ...], _ChargeLineAccumulator],
        missing_accumulators: dict[tuple[object, ...], _MissingRateAccumulator],
    ) -> None:
        resolved = self._resolve_rate(
            category=category,
            country_code=country.code,
            reference_at=reference_at,
            rate_cards=rate_cards,
        )
        if resolved.card is not None and resolved.rate_match is not None and category is not None:
            line_key = (
                resolved.card.id,
                category,
                country.code,
                country.resolution,
                resolved.rate_match,
            )
            accumulator = line_accumulators.get(line_key)
            if accumulator is None:
                accumulator = _ChargeLineAccumulator(
                    card=resolved.card,
                    template_category=category,
                    recipient_country_code=country.code,
                    country_resolution=country.resolution,
                    rate_match=resolved.rate_match,
                )
                line_accumulators[line_key] = accumulator
            accumulator.add(stage, count)
            return

        reason = resolved.missing_reason or "no_effective_rate"
        missing_key = (
            campaign_id,
            category,
            country.code,
            country.resolution,
            stage,
            reason,
            resolved.candidate_keys,
        )
        missing = missing_accumulators.get(missing_key)
        if missing is None:
            missing = _MissingRateAccumulator(
                campaign_id=campaign_id,
                template_category=category,
                recipient_country_code=country.code,
                country_resolution=country.resolution,
                charge_stage=stage,
                reason=reason,
                candidate_rate_card_keys=resolved.candidate_keys,
                rate_reference_from=reference_at,
                rate_reference_to=reference_at,
            )
            missing_accumulators[missing_key] = missing
        missing.add(count, reference_at)

    def _resolve_rate(
        self,
        *,
        category: str | None,
        country_code: str,
        reference_at: datetime,
        rate_cards: list[MetaCommercialRateCard],
    ) -> _ResolvedRate:
        if category is None:
            return _ResolvedRate(
                card=None,
                rate_match=None,
                missing_reason="missing_template_category",
            )
        effective = [
            card
            for card in rate_cards
            if card.category == category
            and _utc(card.effective_from) <= reference_at
            and (card.effective_to is None or reference_at < _utc(card.effective_to))
        ]
        exact = [card for card in effective if card.country_code == country_code]
        candidates = exact or [card for card in effective if card.country_code == "*"]
        if not candidates:
            return _ResolvedRate(
                card=None,
                rate_match=None,
                missing_reason="no_effective_rate",
            )
        if len(candidates) > 1:
            currencies = {card.currency for card in candidates}
            return _ResolvedRate(
                card=None,
                rate_match=None,
                missing_reason=(
                    "ambiguous_currency"
                    if len(currencies) > 1
                    else "overlapping_effective_rates"
                ),
                candidate_keys=tuple(sorted(card.rate_card_key for card in candidates)),
            )
        card = candidates[0]
        return _ResolvedRate(
            card=card,
            rate_match="global_fallback" if card.country_code == "*" else "exact",
            missing_reason=None,
        )

    @staticmethod
    def _recipient_country(recipient: WhatsAppCampaignRecipient) -> _Country:
        send_context = recipient.send_context if isinstance(recipient.send_context, dict) else {}
        raw_country = send_context.get("countryCode")
        if raw_country is not None:
            normalized = str(raw_country).strip().upper()
            if normalized == "*" or (len(normalized) == 2 and normalized.isalpha()):
                return _Country(normalized, "send_context")
            return _Country("*", "invalid_send_context_fallback")
        if str(recipient.phone or "").strip().startswith("+55"):
            return _Country("BR", "phone_prefix_55")
        return _Country("*", "global_fallback")

    @staticmethod
    def _estimate_reference_at(
        campaign: WhatsAppCampaign,
        generated_at: datetime,
    ) -> datetime:
        return _utc(
            campaign.scheduled_at
            or campaign.launched_at
            or campaign.created_at
            or generated_at
        )

    @staticmethod
    def _confirmed_reference_at(
        recipient: WhatsAppCampaignRecipient,
        generated_at: datetime,
    ) -> datetime:
        return _utc(
            recipient.delivered_at
            or recipient.read_at
            or recipient.updated_at
            or generated_at
        )

    @staticmethod
    def _totals_by_currency(
        lines: list[CommercialChargeLineRead],
    ) -> list[CommercialChargeCurrencyTotalRead]:
        totals: dict[str, dict[str, int | Decimal]] = {}
        for line in lines:
            total = totals.setdefault(
                line.currency,
                {
                    "estimated_recipient_count": 0,
                    "confirmed_recipient_count": 0,
                    "estimated_commercial_charge": _ZERO,
                    "confirmed_commercial_charge": _ZERO,
                },
            )
            total["estimated_recipient_count"] += line.estimated_recipient_count
            total["confirmed_recipient_count"] += line.confirmed_recipient_count
            total["estimated_commercial_charge"] += line.estimated_commercial_charge
            total["confirmed_commercial_charge"] += line.confirmed_commercial_charge
        return [
            CommercialChargeCurrencyTotalRead(currency=currency, **totals[currency])
            for currency in sorted(totals)
        ]


__all__ = [
    "MetaCommercialRateConflictError",
    "MetaCommercialRateService",
    "WhatsAppCommercialCampaignNotFoundError",
]
