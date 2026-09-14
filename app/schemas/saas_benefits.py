"""Contratos da API do programa SaaS de indicacoes, beneficios e cupons."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.saas_benefits import BenefitStatus, BenefitUnit, CouponStatus
from app.schemas.base import CamelCaseSchema, WriteSchema


class _Write(WriteSchema):
    model_config = ConfigDict(**WriteSchema.model_config, extra="forbid")


class _Read(CamelCaseSchema):
    model_config = ConfigDict(**CamelCaseSchema.model_config, extra="forbid")


class ReferralPolicyWrite(_Write):
    reward_months_by_plan: dict[str, int] = Field(
        default_factory=lambda: {
            "essencial": 1,
            "escritorio": 2,
            "performance": 3,
            "custom": 3,
        }
    )
    welcome_benefit: Literal["days", "tokens"] = "days"
    welcome_amount: int = Field(default=14, gt=0)
    hold_days_after_first_payment: int = Field(default=30, ge=1, le=365)
    partner_min_active_referrals: int = Field(default=3, ge=1)
    partner_min_referred_mrr_cents: int = Field(default=0, ge=0)
    partner_review_months: int = Field(default=3, ge=1, le=12)

    @field_validator("reward_months_by_plan")
    @classmethod
    def validate_plan_rewards(cls, value: dict[str, int]) -> dict[str, int]:
        normalized = {str(key).strip().lower(): int(amount) for key, amount in value.items()}
        if not normalized or any(amount < 1 or amount > 3 for amount in normalized.values()):
            raise ValueError("Cada plano deve conceder entre um e tres meses.")
        return normalized


class ReferralPolicyPublishRequest(_Write):
    rules: ReferralPolicyWrite
    reason: str = Field(min_length=8, max_length=500)


class ReferralClaimRequest(_Write):
    code: str = Field(min_length=4, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()


class ReferralFirstPaymentRequest(_Write):
    paid_at: datetime
    plan_code: str = Field(min_length=2, max_length=32)
    payment_reference: str = Field(min_length=3, max_length=160)

    @field_validator("paid_at")
    @classmethod
    def require_paid_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("O horario do pagamento precisa conter fuso horario.")
        return value


class BenefitGrantCreateRequest(_Write):
    workspace_id: UUID
    benefit_type: str = Field(min_length=2, max_length=48)
    unit: BenefitUnit
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=8, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=160)
    ticket_id: UUID | None = None
    source_type: str = Field(default="manual", min_length=2, max_length=40)
    source_ref: str | None = Field(default=None, max_length=160)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def require_expiry_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("A expiracao do beneficio precisa conter fuso horario.")
        return value


class BenefitDecisionRequest(_Write):
    reason: str = Field(min_length=8, max_length=500)
    ticket_id: UUID | None = None


class CouponWrite(_Write):
    code: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2_000)
    benefit_type: str = Field(min_length=2, max_length=48)
    unit: BenefitUnit
    amount: Decimal = Field(gt=0)
    allowed_plan_codes: list[str] = Field(default_factory=list, max_length=20)
    total_limit: int | None = Field(default=None, gt=0)
    per_workspace_limit: int = Field(default=1, gt=0, le=10)
    auto_apply: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    reason: str = Field(min_length=8, max_length=500)
    ticket_id: UUID | None = None

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        cleaned = "-".join(value.strip().upper().split())
        if not cleaned.replace("-", "").isalnum():
            raise ValueError("Use somente letras, numeros e hifens no codigo.")
        return cleaned

    @field_validator("allowed_plan_codes")
    @classmethod
    def normalize_plans(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))

    @model_validator(mode="after")
    def validate_window(self) -> "CouponWrite":
        for value in (self.starts_at, self.ends_at):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("As datas do cupom precisam conter fuso horario.")
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("A data final deve ser posterior a data inicial.")
        return self


class CouponUpdateRequest(CouponWrite):
    expected_version: int = Field(ge=1)


class CouponStatusRequest(_Write):
    status: CouponStatus
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)
    ticket_id: UUID | None = None


class CouponRedeemRequest(_Write):
    code: str = Field(min_length=3, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()


class ReferralProfileRead(_Read):
    id: UUID
    workspace_id: UUID
    code: str
    active: bool
    link: str
    created_at: datetime


class BenefitGrantRead(_Read):
    id: UUID
    workspace_id: UUID
    benefit_type: str
    unit: BenefitUnit
    amount: Decimal
    status: BenefitStatus
    source_type: str
    source_ref: str | None
    reason: str
    ticket_id: UUID | None
    approved_at: datetime | None
    applied_at: datetime | None
    expires_at: datetime | None
    reversed_at: datetime | None
    created_at: datetime


class BenefitGrantPageRead(_Read):
    items: list[BenefitGrantRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict[str, str]] = Field(default_factory=list)


class BenefitLedgerEntryRead(_Read):
    id: UUID
    grant_id: UUID
    workspace_id: UUID
    event_type: str
    unit: BenefitUnit
    delta: Decimal
    reason: str
    ticket_id: UUID | None
    actor_user_id: UUID | None
    created_at: datetime


class BenefitLedgerPageRead(_Read):
    items: list[BenefitLedgerEntryRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict[str, str]] = Field(default_factory=list)


class WorkspaceBenefitsOverviewRead(_Read):
    profile: ReferralProfileRead | None
    referrals: list[dict[str, Any]]
    benefits: list[BenefitGrantRead]
    ledger: list[dict[str, Any]]
    partner: dict[str, Any] | None
    coupon_redemptions: list[dict[str, Any]]
    policy: ReferralPolicyWrite


class CouponRead(_Read):
    id: UUID
    code: str
    name: str
    description: str
    status: CouponStatus
    benefit_type: str
    unit: BenefitUnit
    amount: Decimal
    allowed_plan_codes: list[str]
    total_limit: int | None
    per_workspace_limit: int
    redeemed_count: int
    auto_apply: bool
    starts_at: datetime | None
    ends_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class CouponPageRead(_Read):
    items: list[CouponRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict[str, str]] = Field(default_factory=list)


class CouponRedemptionRead(_Read):
    id: UUID
    coupon_id: UUID
    coupon_code: str
    workspace_id: UUID
    workspace_name: str
    status: str
    redeemed_at: datetime


class CouponRedemptionPageRead(_Read):
    items: list[CouponRedemptionRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict[str, str]] = Field(default_factory=list)


class ReferralPolicyHistoryRead(_Read):
    id: UUID
    version: int
    status: str
    rules: dict[str, Any]
    reason: str | None
    effective_at: datetime | None
    created_by_user_id: UUID | None
    created_at: datetime


class ReferralPolicyHistoryPageRead(_Read):
    items: list[ReferralPolicyHistoryRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict[str, str]] = Field(default_factory=list)


class PlatformReferralRead(_Read):
    id: UUID
    referrer_workspace_id: UUID
    referred_workspace_id: UUID
    referred_workspace_name: str
    status: str
    plan_code: str | None
    first_payment_at: datetime | None
    hold_until: datetime | None
    rewarded_at: datetime | None


class PlatformReferralPageRead(_Read):
    items: list[PlatformReferralRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict[str, str]] = Field(default_factory=list)


class PartnerMembershipRead(_Read):
    workspace_id: UUID
    workspace_name: str
    status: str
    active_referrals: int
    referred_mrr_cents: int
    next_review_at: datetime | None
    exemption_ends_at: datetime | None


class PartnerMembershipPageRead(_Read):
    items: list[PartnerMembershipRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict[str, str]] = Field(default_factory=list)


class CouponAuditEntryRead(_Read):
    id: UUID
    action: str
    actor_user_id: UUID | None
    ticket_id: UUID | None
    reason: str
    before: dict[str, Any]
    after: dict[str, Any]
    created_at: datetime


class CouponAuditPageRead(_Read):
    items: list[CouponAuditEntryRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    sort: list[dict[str, str]] = Field(default_factory=list)
