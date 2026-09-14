"""Programa de indicacao SaaS, beneficios, Partner e cupons da plataforma."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin


class ReferralPolicyStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class SaasReferralStatus(str, enum.Enum):
    registered = "registered"
    qualified = "qualified"
    payment_hold = "payment_hold"
    eligible = "eligible"
    rewarded = "rewarded"
    reversed = "reversed"


class BenefitUnit(str, enum.Enum):
    days = "days"
    months = "months"
    tokens = "tokens"
    percentage = "percentage"
    cents = "cents"


class BenefitStatus(str, enum.Enum):
    pending = "pending"
    available = "available"
    applied = "applied"
    reversed = "reversed"
    expired = "expired"


class PartnerStatus(str, enum.Enum):
    candidate = "candidate"
    active = "active"
    suspended = "suspended"
    expired = "expired"


class CouponStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    paused = "paused"
    expired = "expired"
    archived = "archived"


class CouponRedemptionStatus(str, enum.Enum):
    available = "available"
    applied = "applied"
    reversed = "reversed"


def _enum(enum_type: type[enum.Enum], name: str, length: int = 32) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        validate_strings=True,
        length=length,
    )


class ReferralProgramPolicy(UUIDMixin, Base):
    """Snapshot versionado das regras comerciais do programa de indicacao."""

    __tablename__ = "referral_program_policies"
    __table_args__ = (
        UniqueConstraint("version", name="uq_referral_program_policies_version"),
        Index("ix_referral_program_policies_status_effective", "status", "effective_at"),
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ReferralPolicyStatus] = mapped_column(
        _enum(ReferralPolicyStatus, "referral_policy_status_enum"),
        nullable=False,
        default=ReferralPolicyStatus.draft,
    )
    rules_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    effective_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SaasReferralProfile(UUIDMixin, TimestampMixin, Base):
    """Link de indicacao pertencente ao workspace, visivel somente ao owner."""

    __tablename__ = "saas_referral_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_saas_referral_profiles_workspace"),
        UniqueConstraint("code", name="uq_saas_referral_profiles_code"),
        Index("ix_saas_referral_profiles_active", "active"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SaasReferralAttribution(UUIDMixin, TimestampMixin, Base):
    """Vinculo entre o escritorio indicador e o novo workspace indicado."""

    __tablename__ = "saas_referral_attributions"
    __table_args__ = (
        UniqueConstraint("referred_workspace_id", name="uq_saas_referral_attributions_referred"),
        UniqueConstraint("idempotency_key", name="uq_saas_referral_attributions_idempotency"),
        CheckConstraint(
            "referrer_workspace_id <> referred_workspace_id",
            name="ck_saas_referral_attributions_distinct_workspaces",
        ),
        Index("ix_saas_referral_attributions_referrer_status", "referrer_workspace_id", "status"),
        Index("ix_saas_referral_attributions_hold", "status", "hold_until"),
    )

    referral_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("saas_referral_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    referrer_workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    referred_workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SaasReferralStatus] = mapped_column(
        _enum(SaasReferralStatus, "saas_referral_status_enum"),
        nullable=False,
        default=SaasReferralStatus.registered,
    )
    plan_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    first_payment_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    hold_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    qualified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rewarded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="referral_link")
    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class BenefitGrant(UUIDMixin, TimestampMixin, Base):
    """Concessao auditavel; o beneficio sempre pertence ao workspace."""

    __tablename__ = "benefit_grants"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_benefit_grants_idempotency"),
        CheckConstraint("amount > 0", name="ck_benefit_grants_amount_positive"),
        Index("ix_benefit_grants_workspace_status", "workspace_id", "status"),
        Index("ix_benefit_grants_source", "source_type", "source_ref"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    benefit_type: Mapped[str] = mapped_column(String(48), nullable=False)
    unit: Mapped[BenefitUnit] = mapped_column(
        _enum(BenefitUnit, "benefit_unit_enum"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[BenefitStatus] = mapped_column(
        _enum(BenefitStatus, "benefit_status_enum"),
        nullable=False,
        default=BenefitStatus.pending,
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_ref: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    ticket_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class BenefitLedgerEntry(UUIDMixin, Base):
    """Razao append-only de toda alteracao de um beneficio."""

    __tablename__ = "benefit_ledger_entries"
    __table_args__ = (
        UniqueConstraint("grant_id", "event_type", name="uq_benefit_ledger_grant_event"),
        Index("ix_benefit_ledger_entries_workspace_created", "workspace_id", "created_at"),
        Index("ix_benefit_ledger_entries_grant", "grant_id"),
    )

    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("benefit_grants.id", ondelete="RESTRICT"), nullable=False
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    unit: Mapped[BenefitUnit] = mapped_column(
        _enum(BenefitUnit, "benefit_ledger_unit_enum"), nullable=False
    )
    delta: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    actor_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ticket_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PartnerMembership(UUIDMixin, TimestampMixin, Base):
    """Elegibilidade Partner reavaliada trimestralmente."""

    __tablename__ = "partner_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_partner_memberships_workspace"),
        Index("ix_partner_memberships_status_review", "status", "next_review_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[PartnerStatus] = mapped_column(
        _enum(PartnerStatus, "partner_status_enum"),
        nullable=False,
        default=PartnerStatus.candidate,
    )
    tier: Mapped[str] = mapped_column(String(40), nullable=False, default="partner")
    active_referrals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    referred_mrr_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    exemption_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class Coupon(UUIDMixin, TimestampMixin, Base):
    """Cupom comercial versionado e limitado pelo Studio de Cupons."""

    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("code", name="uq_coupons_code"),
        CheckConstraint("amount > 0", name="ck_coupons_amount_positive"),
        CheckConstraint("total_limit IS NULL OR total_limit > 0", name="ck_coupons_total_limit"),
        CheckConstraint("per_workspace_limit > 0", name="ck_coupons_workspace_limit"),
        Index("ix_coupons_status_window", "status", "starts_at", "ends_at"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[CouponStatus] = mapped_column(
        _enum(CouponStatus, "coupon_status_enum"),
        nullable=False,
        default=CouponStatus.draft,
    )
    benefit_type: Mapped[str] = mapped_column(String(48), nullable=False)
    unit: Mapped[BenefitUnit] = mapped_column(
        _enum(BenefitUnit, "coupon_benefit_unit_enum"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    allowed_plan_codes: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    total_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    per_workspace_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    redeemed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_apply: Mapped[bool] = mapped_column(nullable=False, default=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)


class CouponRedemption(UUIDMixin, Base):
    """Resgate unico por workspace, ligado ao beneficio concedido."""

    __tablename__ = "coupon_redemptions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_coupon_redemptions_idempotency"),
        Index("ix_coupon_redemptions_coupon_workspace", "coupon_id", "workspace_id"),
        Index("ix_coupon_redemptions_workspace", "workspace_id", "redeemed_at"),
    )

    coupon_id: Mapped[UUID] = mapped_column(
        ForeignKey("coupons.id", ondelete="RESTRICT"), nullable=False
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    benefit_grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("benefit_grants.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[CouponRedemptionStatus] = mapped_column(
        _enum(CouponRedemptionStatus, "coupon_redemption_status_enum"),
        nullable=False,
        default=CouponRedemptionStatus.applied,
    )
    redeemed_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CouponAuditEvent(UUIDMixin, Base):
    """Historico append-only do Studio de Cupons."""

    __tablename__ = "coupon_audit_events"
    __table_args__ = (
        Index("ix_coupon_audit_events_coupon_created", "coupon_id", "created_at"),
    )

    coupon_id: Mapped[UUID] = mapped_column(
        ForeignKey("coupons.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ticket_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    before_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    after_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


for _immutable_model in (
    ReferralProgramPolicy,
    BenefitLedgerEntry,
    CouponAuditEvent,
):
    register_append_only_model(_immutable_model)


__all__ = [
    "BenefitGrant",
    "BenefitLedgerEntry",
    "BenefitStatus",
    "BenefitUnit",
    "Coupon",
    "CouponAuditEvent",
    "CouponRedemption",
    "CouponRedemptionStatus",
    "CouponStatus",
    "PartnerMembership",
    "PartnerStatus",
    "ReferralPolicyStatus",
    "ReferralProgramPolicy",
    "SaasReferralAttribution",
    "SaasReferralProfile",
    "SaasReferralStatus",
]
