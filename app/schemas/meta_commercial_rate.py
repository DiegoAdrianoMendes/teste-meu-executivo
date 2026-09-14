"""API contracts for Meta commercial rate cards and tenant charge summaries."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.meta_commercial_rate import META_PRICING_SOURCE_URL
from app.schemas.base import CamelCaseSchema, WriteSchema

CountryResolution = Literal[
    "send_context",
    "phone_prefix_55",
    "global_fallback",
    "invalid_send_context_fallback",
    "campaign_aggregate_fallback",
]
RateMatch = Literal["exact", "global_fallback"]
MissingRateReason = Literal[
    "missing_template_category",
    "no_effective_rate",
    "ambiguous_currency",
    "overlapping_effective_rates",
]
ChargeStage = Literal["estimated", "confirmed"]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Use data e hora com fuso horário explícito.")
    return value.astimezone(UTC)


class MetaCommercialRateCardUpsert(WriteSchema):
    category: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    country_code: str = Field(min_length=1, max_length=2)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    commercial_rate: Decimal = Field(ge=0, max_digits=20, decimal_places=8)
    effective_from: datetime
    effective_to: datetime | None = None
    source_url: str = Field(min_length=1, max_length=500)
    checked_at: datetime
    is_active: bool = True

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "*" and (len(normalized) != 2 or not normalized.isalpha()):
            raise ValueError("countryCode deve ser ISO alpha-2 ou '*'.")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        if value.strip() != META_PRICING_SOURCE_URL:
            raise ValueError("sourceUrl deve apontar para a documentação oficial de preços da Meta.")
        return META_PRICING_SOURCE_URL

    @field_validator("effective_from", "effective_to", "checked_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_effective_window(self) -> MetaCommercialRateCardUpsert:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effectiveTo deve ser posterior a effectiveFrom.")
        return self


class MetaCommercialRateCardRead(CamelCaseSchema):
    id: UUID
    rate_card_key: str
    category: str
    country_code: str
    currency: str
    commercial_rate: Decimal
    effective_from: datetime
    effective_to: datetime | None
    source_url: str
    checked_at: datetime
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CommercialChargeCurrencyTotalRead(CamelCaseSchema):
    currency: str
    estimated_recipient_count: int = Field(ge=0)
    confirmed_recipient_count: int = Field(ge=0)
    estimated_commercial_charge: Decimal = Field(ge=0)
    confirmed_commercial_charge: Decimal = Field(ge=0)


class CommercialChargeLineRead(CamelCaseSchema):
    rate_card_key: str
    template_category: str
    recipient_country_code: str
    country_resolution: CountryResolution
    rate_country_code: str
    rate_match: RateMatch
    currency: str
    commercial_rate: Decimal = Field(ge=0)
    rate_effective_from: datetime
    rate_effective_to: datetime | None
    rate_source_url: str
    rate_checked_at: datetime
    estimated_recipient_count: int = Field(ge=0)
    confirmed_recipient_count: int = Field(ge=0)
    estimated_commercial_charge: Decimal = Field(ge=0)
    confirmed_commercial_charge: Decimal = Field(ge=0)


class MissingCommercialRateRead(CamelCaseSchema):
    campaign_id: UUID
    template_category: str | None
    recipient_country_code: str
    country_resolution: CountryResolution
    charge_stage: ChargeStage
    recipient_count: int = Field(gt=0)
    rate_reference_from: datetime
    rate_reference_to: datetime
    reason: MissingRateReason
    candidate_rate_card_keys: list[str] = Field(default_factory=list)


class CampaignCommercialChargeRead(CamelCaseSchema):
    campaign_id: UUID
    title: str
    template_category: str | None
    eligible_recipients: int = Field(ge=0)
    confirmed_recipients: int = Field(ge=0)
    commercial_charge_lines: list[CommercialChargeLineRead] = Field(default_factory=list)
    totals_by_currency: list[CommercialChargeCurrencyTotalRead] = Field(default_factory=list)
    missing_rates: list[MissingCommercialRateRead] = Field(default_factory=list)


class WhatsAppCommercialChargeSummaryRead(CamelCaseSchema):
    workspace_id: UUID
    requested_campaign_id: UUID | None
    generated_at: datetime
    charge_basis: Literal["delivered_messages"] = "delivered_messages"
    internal_provider_cost_exposed: Literal[False] = False
    campaigns: list[CampaignCommercialChargeRead] = Field(default_factory=list)
    totals_by_currency: list[CommercialChargeCurrencyTotalRead] = Field(default_factory=list)
    missing_rates: list[MissingCommercialRateRead] = Field(default_factory=list)


__all__ = [
    "CampaignCommercialChargeRead",
    "CommercialChargeCurrencyTotalRead",
    "CommercialChargeLineRead",
    "META_PRICING_SOURCE_URL",
    "MetaCommercialRateCardRead",
    "MetaCommercialRateCardUpsert",
    "MissingCommercialRateRead",
    "WhatsAppCommercialChargeSummaryRead",
]
