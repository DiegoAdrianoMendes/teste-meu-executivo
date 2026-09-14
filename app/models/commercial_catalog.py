"""Canonical commercial catalog, multiproduct contracts, and exceptions.

The catalog is deliberately provider-agnostic.  Stripe (or another billing
provider) may mirror a contract later, but it is never the source of truth for
the capabilities contracted by a workspace.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin
from app.models.entitlement import EntitlementKey


class CommercialOfferFamily(StrEnum):
    crm = "crm"
    legal = "legal"
    internal = "internal"


class CommercialOfferKind(StrEnum):
    base_plan = "base_plan"
    solution = "solution"


class CommercialBillingInterval(StrEnum):
    monthly = "monthly"
    annual = "annual"


class CommercialContractStatus(StrEnum):
    draft = "draft"
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    paused = "paused"
    cancelled = "cancelled"


class CommercialContractLineStatus(StrEnum):
    scheduled = "scheduled"
    active = "active"
    suspended = "suspended"
    cancelled = "cancelled"


class CommercialAdjustmentType(StrEnum):
    percentage_discount = "percentage_discount"
    fixed_discount = "fixed_discount"
    negotiated_price = "negotiated_price"
    courtesy_period = "courtesy_period"
    invoice_credit = "invoice_credit"


class CommercialAdjustmentSource(StrEnum):
    manual = "manual"
    coupon = "coupon"
    benefit = "benefit"
    referral = "referral"
    campaign = "campaign"


class CommercialRecordStatus(StrEnum):
    scheduled = "scheduled"
    active = "active"
    expired = "expired"
    revoked = "revoked"


class CommercialPortability(StrEnum):
    preserve = "preserve"
    expire_on_offer_change = "expire_on_offer_change"
    recalculate = "recalculate"
    manual_review = "manual_review"


class EntitlementBonusMode(StrEnum):
    quantity_delta = "quantity_delta"
    feature_grant = "feature_grant"


class EntitlementBonusSource(StrEnum):
    manual = "manual"
    benefit = "benefit"
    campaign = "campaign"
    compensation = "compensation"


class CommercialProvider(StrEnum):
    stripe = "stripe"
    asaas = "asaas"
    mercado_pago = "mercado_pago"


class CommercialProviderBindingStatus(StrEnum):
    active = "active"
    inactive = "inactive"


class CommercialCheckoutIntentStatus(StrEnum):
    prepared = "prepared"
    session_created = "session_created"
    completed = "completed"
    reconciliation_required = "reconciliation_required"
    failed = "failed"
    expired = "expired"
    cancelled = "cancelled"


def _string_enum(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class CommercialOffer(UUIDMixin, Base):
    """Stable, immutable identity for one commercial product.

    Names, prices, visibility and policies live in immutable versions.  This
    keeps historical contracts meaningful when marketing changes a label.
    """

    __tablename__ = "commercial_offers"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_commercial_offer_sku"),
        UniqueConstraint("id", "family", name="uq_commercial_offer_id_family"),
        Index("ix_commercial_offer_family", "family", "kind"),
    )

    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    family: Mapped[CommercialOfferFamily] = mapped_column(
        _string_enum(CommercialOfferFamily, "commercial_offer_family_enum"),
        nullable=False,
    )
    kind: Mapped[CommercialOfferKind] = mapped_column(
        _string_enum(CommercialOfferKind, "commercial_offer_kind_enum"),
        nullable=False,
    )
    legacy_tier: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommercialOfferVersion(UUIDMixin, Base):
    """Immutable offer and entitlement policy published as one revision."""

    __tablename__ = "commercial_offer_versions"
    __table_args__ = (
        UniqueConstraint("offer_id", "version", name="uq_commercial_offer_version"),
        UniqueConstraint("id", "offer_id", name="uq_commercial_offer_version_offer_binding"),
        CheckConstraint("version > 0", name="ck_commercial_offer_version_positive"),
        CheckConstraint(
            "price_monthly_cents IS NULL OR price_monthly_cents >= 0",
            name="ck_commercial_offer_monthly_price_nonnegative",
        ),
        CheckConstraint(
            "price_annual_cents IS NULL OR price_annual_cents >= 0",
            name="ck_commercial_offer_annual_price_nonnegative",
        ),
        CheckConstraint(
            "length(trim(publication_reason)) >= 3",
            name="ck_commercial_offer_publication_reason",
        ),
        CheckConstraint(
            "currency = 'BRL'",
            name="ck_commercial_offer_currency_brl",
        ),
        Index("ix_commercial_offer_version_latest", "offer_id", "version"),
    )

    offer_id: Mapped[UUID] = mapped_column(
        ForeignKey("commercial_offers.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="3.0")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_quote_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    available_intervals: Mapped[list[str]] = mapped_column(FlexibleJSON(), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    price_monthly_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_annual_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cta: Mapped[str] = mapped_column(String(120), nullable=False)
    entitlement_policy: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_reason: Mapped[str] = mapped_column(Text, nullable=False)
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkspaceCommercialContract(UUIDMixin, TimestampMixin, Base):
    """One versioned commercial agreement for one workspace."""

    __tablename__ = "workspace_commercial_contracts"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_workspace_commercial_contract"),
        CheckConstraint("version > 0", name="ck_workspace_contract_version_positive"),
        CheckConstraint(
            "currency = 'BRL'",
            name="ck_workspace_contract_currency_brl",
        ),
        Index("ix_workspace_contract_status", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[CommercialContractStatus] = mapped_column(
        _string_enum(CommercialContractStatus, "commercial_contract_status_enum"),
        nullable=False,
        default=CommercialContractStatus.draft,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class WorkspaceContractLine(UUIDMixin, TimestampMixin, Base):
    """A priced CRM or Legal line fixed to one immutable policy revision."""

    __tablename__ = "workspace_contract_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_version_id", "offer_id"],
            ["commercial_offer_versions.id", "commercial_offer_versions.offer_id"],
            ondelete="RESTRICT",
            name="fk_contract_line_policy_offer",
        ),
        ForeignKeyConstraint(
            ["offer_id", "offer_family"],
            ["commercial_offers.id", "commercial_offers.family"],
            ondelete="RESTRICT",
            name="fk_contract_line_offer_family",
        ),
        UniqueConstraint("id", "contract_id", name="uq_workspace_contract_line_contract_binding"),
        CheckConstraint("quantity > 0", name="ck_workspace_contract_line_quantity"),
        CheckConstraint(
            "list_price_cents >= 0 AND contracted_price_cents >= 0",
            name="ck_workspace_contract_line_prices_nonnegative",
        ),
        CheckConstraint("version > 0", name="ck_workspace_contract_line_version_positive"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_workspace_contract_line_effective_window",
        ),
        Index("ix_workspace_contract_line_contract", "contract_id", "status"),
        Index(
            "uq_workspace_active_contract_line_family",
            "contract_id",
            "offer_family",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_commercial_contracts.id", ondelete="CASCADE"),
        nullable=False,
    )
    offer_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    offer_family: Mapped[CommercialOfferFamily] = mapped_column(
        _string_enum(CommercialOfferFamily, "contract_line_offer_family_enum"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    list_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contracted_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    billing_interval: Mapped[CommercialBillingInterval] = mapped_column(
        _string_enum(CommercialBillingInterval, "commercial_billing_interval_enum"),
        nullable=False,
        default=CommercialBillingInterval.monthly,
    )
    # Negotiated Legal 360 limits are frozen with the line.  Values use the
    # same complete setting shape as the immutable offer policy.
    entitlement_overrides: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[CommercialContractLineStatus] = mapped_column(
        _string_enum(
            CommercialContractLineStatus,
            "commercial_contract_line_status_enum",
        ),
        nullable=False,
        default=CommercialContractLineStatus.active,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class CommercialProviderPriceBinding(UUIDMixin, TimestampMixin, Base):
    """Safe mapping from an immutable offer version to a provider price."""

    __tablename__ = "commercial_provider_price_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["offer_version_id", "offer_id"],
            ["commercial_offer_versions.id", "commercial_offer_versions.offer_id"],
            ondelete="RESTRICT",
            name="fk_commercial_provider_price_offer_version",
        ),
        CheckConstraint("version > 0", name="ck_commercial_provider_price_version"),
        CheckConstraint(
            "length(trim(provider_price_id)) >= 3",
            name="ck_commercial_provider_price_id",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 8",
            name="ck_commercial_provider_price_reason",
        ),
        Index(
            "uq_commercial_provider_price_active",
            "provider",
            "offer_version_id",
            "billing_interval",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "uq_commercial_provider_price_id_active",
            "provider",
            "provider_price_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    offer_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    offer_version_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    provider: Mapped[CommercialProvider] = mapped_column(
        _string_enum(CommercialProvider, "commercial_provider_enum"), nullable=False
    )
    billing_interval: Mapped[CommercialBillingInterval] = mapped_column(
        _string_enum(
            CommercialBillingInterval,
            "commercial_provider_billing_interval_enum",
        ),
        nullable=False,
    )
    provider_price_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[CommercialProviderBindingStatus] = mapped_column(
        _string_enum(
            CommercialProviderBindingStatus,
            "commercial_provider_binding_status_enum",
        ),
        nullable=False,
        default=CommercialProviderBindingStatus.active,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    configured_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class CommercialContractProviderBinding(UUIDMixin, TimestampMixin, Base):
    """Provider identifiers are projections; the canonical contract remains authoritative."""

    __tablename__ = "commercial_contract_provider_bindings"
    __table_args__ = (
        UniqueConstraint(
            "contract_id", "provider", name="uq_commercial_contract_provider"
        ),
        Index(
            "uq_commercial_provider_customer",
            "provider",
            "provider_customer_id",
            unique=True,
            postgresql_where=text("provider_customer_id IS NOT NULL"),
            sqlite_where=text("provider_customer_id IS NOT NULL"),
        ),
        Index(
            "uq_commercial_provider_subscription",
            "provider",
            "provider_subscription_id",
            unique=True,
            postgresql_where=text("provider_subscription_id IS NOT NULL"),
            sqlite_where=text("provider_subscription_id IS NOT NULL"),
        ),
        CheckConstraint("version > 0", name="ck_commercial_contract_provider_version"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_commercial_contracts.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[CommercialProvider] = mapped_column(
        _string_enum(CommercialProvider, "commercial_contract_provider_enum"), nullable=False
    )
    provider_customer_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_event_created: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CommercialCheckoutIntent(UUIDMixin, TimestampMixin, Base):
    """Durable, tenant-scoped intent created before opening hosted checkout."""

    __tablename__ = "commercial_checkout_intents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_commercial_checkout_idempotency"
        ),
        UniqueConstraint(
            "provider", "provider_session_id", name="uq_commercial_checkout_provider_session"
        ),
        CheckConstraint("expected_contract_version > 0", name="ck_checkout_contract_version"),
        CheckConstraint("version > 0", name="ck_checkout_intent_version"),
        CheckConstraint("length(trim(reason)) >= 8", name="ck_checkout_intent_reason"),
        Index("ix_commercial_checkout_workspace_status", "workspace_id", "status"),
        Index("ix_commercial_checkout_contract", "contract_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_commercial_contracts.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[CommercialProvider] = mapped_column(
        _string_enum(CommercialProvider, "commercial_checkout_provider_enum"), nullable=False
    )
    target_sku: Mapped[str] = mapped_column(String(80), nullable=False)
    target_offer_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("commercial_offer_versions.id", ondelete="RESTRICT"), nullable=False
    )
    expected_contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_lines: Mapped[list[dict[str, Any]]] = mapped_column(FlexibleJSON(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CommercialCheckoutIntentStatus] = mapped_column(
        _string_enum(
            CommercialCheckoutIntentStatus,
            "commercial_checkout_intent_status_enum",
        ),
        nullable=False,
        default=CommercialCheckoutIntentStatus.prepared,
    )
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CommercialAdjustment(UUIDMixin, TimestampMixin, Base):
    """Price-only adjustment; it never grants a module or quota."""

    __tablename__ = "commercial_adjustments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_line_id", "contract_id"],
            ["workspace_contract_lines.id", "workspace_contract_lines.contract_id"],
            ondelete="CASCADE",
            name="fk_commercial_adjustment_line_contract",
        ),
        CheckConstraint("value >= 0", name="ck_commercial_adjustment_value_nonnegative"),
        CheckConstraint(
            "type <> 'percentage_discount' OR value <= 10000",
            name="ck_commercial_adjustment_percentage_bps",
        ),
        CheckConstraint(
            "remaining_cycles IS NULL OR remaining_cycles >= 0",
            name="ck_commercial_adjustment_remaining_cycles_nonnegative",
        ),
        CheckConstraint("version > 0", name="ck_commercial_adjustment_version_positive"),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_commercial_adjustment_window",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 3",
            name="ck_commercial_adjustment_reason",
        ),
        Index("ix_commercial_adjustment_contract_status", "contract_id", "status"),
        Index("ix_commercial_adjustment_line_status", "contract_line_id", "status"),
    )

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_commercial_contracts.id", ondelete="CASCADE"),
        nullable=False,
    )
    contract_line_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    type: Mapped[CommercialAdjustmentType] = mapped_column(
        _string_enum(CommercialAdjustmentType, "commercial_adjustment_type_enum"),
        nullable=False,
    )
    # Percentage values use basis points (10_000 == 100%). Other monetary
    # values use BRL cents; courtesy_period uses the fixed marker 1 and its
    # effective dates define the finite full-courtesy interval.
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remaining_cycles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stack_group: Mapped[str] = mapped_column(String(80), nullable=False)
    stackable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    portability: Mapped[CommercialPortability] = mapped_column(
        _string_enum(CommercialPortability, "commercial_adjustment_portability_enum"),
        nullable=False,
        default=CommercialPortability.preserve,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[CommercialAdjustmentSource] = mapped_column(
        _string_enum(CommercialAdjustmentSource, "commercial_adjustment_source_enum"),
        nullable=False,
    )
    status: Mapped[CommercialRecordStatus] = mapped_column(
        _string_enum(CommercialRecordStatus, "commercial_adjustment_status_enum"),
        nullable=False,
        default=CommercialRecordStatus.active,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class EntitlementBonus(UUIDMixin, TimestampMixin, Base):
    """Capacity-only exception; it never changes contracted price."""

    __tablename__ = "entitlement_bonuses"
    __table_args__ = (
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_bonus_quantity"),
        CheckConstraint(
            "(mode = 'quantity_delta' AND quantity IS NOT NULL) OR "
            "(mode = 'feature_grant' AND quantity IS NULL)",
            name="ck_bonus_mode_quantity",
        ),
        CheckConstraint("version > 0", name="ck_bonus_version_positive"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_bonus_effective_window"),
        CheckConstraint("length(trim(reason)) >= 3", name="ck_bonus_reason"),
        Index("ix_entitlement_bonus_workspace_status", "workspace_id", "status"),
        Index("ix_entitlement_bonus_workspace_key", "workspace_id", "entitlement"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    entitlement: Mapped[EntitlementKey] = mapped_column(
        _string_enum(EntitlementKey, "commercial_bonus_entitlement_enum"),
        nullable=False,
    )
    mode: Mapped[EntitlementBonusMode] = mapped_column(
        _string_enum(EntitlementBonusMode, "entitlement_bonus_mode_enum"),
        nullable=False,
    )
    quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renews_each_period: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    portability: Mapped[CommercialPortability] = mapped_column(
        _string_enum(CommercialPortability, "entitlement_bonus_portability_enum"),
        nullable=False,
        default=CommercialPortability.preserve,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[EntitlementBonusSource] = mapped_column(
        _string_enum(EntitlementBonusSource, "entitlement_bonus_source_enum"),
        nullable=False,
    )
    status: Mapped[CommercialRecordStatus] = mapped_column(
        _string_enum(CommercialRecordStatus, "entitlement_bonus_status_enum"),
        nullable=False,
        default=CommercialRecordStatus.active,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class CommercialCommandReceipt(UUIDMixin, Base):
    """Immutable durable response for a mutating idempotent command."""

    __tablename__ = "commercial_command_receipts"
    __table_args__ = (
        UniqueConstraint(
            "operation",
            "scope_key",
            "idempotency_key",
            name="uq_commercial_command_idempotency",
        ),
        Index("ix_commercial_command_receipt_created", "created_at"),
    )

    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommercialCatalogEvent(UUIDMixin, Base):
    """Append-only domain evidence; platform audit may mirror its correlation."""

    __tablename__ = "commercial_catalog_events"
    __table_args__ = (
        Index("ix_commercial_event_resource", "resource_type", "resource_id"),
        Index("ix_commercial_event_workspace", "workspace_id", "occurred_at"),
        Index("ix_commercial_event_correlation", "correlation_id"),
    )

    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=True
    )
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(FlexibleJSON(), nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(FlexibleJSON(), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


for _immutable_model in (
    CommercialOffer,
    CommercialOfferVersion,
    CommercialCommandReceipt,
    CommercialCatalogEvent,
):
    register_append_only_model(_immutable_model)


__all__ = [
    "CommercialAdjustment",
    "CommercialAdjustmentSource",
    "CommercialAdjustmentType",
    "CommercialBillingInterval",
    "CommercialCatalogEvent",
    "CommercialCommandReceipt",
    "CommercialContractLineStatus",
    "CommercialContractStatus",
    "CommercialOffer",
    "CommercialOfferFamily",
    "CommercialOfferKind",
    "CommercialOfferVersion",
    "CommercialProvider",
    "CommercialProviderBindingStatus",
    "CommercialProviderPriceBinding",
    "CommercialContractProviderBinding",
    "CommercialCheckoutIntent",
    "CommercialCheckoutIntentStatus",
    "CommercialPortability",
    "CommercialRecordStatus",
    "EntitlementBonus",
    "EntitlementBonusMode",
    "EntitlementBonusSource",
    "WorkspaceCommercialContract",
    "WorkspaceContractLine",
]
