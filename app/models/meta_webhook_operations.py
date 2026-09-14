"""Durable, sanitized control-plane state for Meta webhook capabilities."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class MetaWebhookReceipt(UUIDMixin, Base):
    __tablename__ = "meta_webhook_receipts"
    __table_args__ = (
        UniqueConstraint(
            "provider_envelope_id",
            "field",
            "item_fingerprint",
            name="uq_meta_webhook_receipt_delivery_item",
        ),
        Index("ix_meta_webhook_receipts_workspace_status", "workspace_id", "status"),
        Index("ix_meta_webhook_receipts_connection_field", "connection_id", "field"),
        Index("ix_meta_webhook_receipts_observed", "observed_at", "id"),
    )

    provider_envelope_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("klm_omnichannel_provider_webhook_envelopes.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("whatsapp_connections.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(80), nullable=False)
    item_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    handler_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="received")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    projection_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    projection_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    sanitized_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MetaWebhookOperationalState(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "meta_webhook_operational_states"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "field",
            "resource_type",
            "resource_ref",
            name="uq_meta_webhook_operational_resource",
        ),
        Index("ix_meta_webhook_states_workspace_field", "workspace_id", "field"),
        Index("ix_meta_webhook_states_connection_severity", "connection_id", "severity"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("whatsapp_connections.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state_code: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    sanitized_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    provider_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    projected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MetaWebhookSubscriptionState(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "meta_webhook_subscription_states"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "field", name="uq_meta_webhook_subscription_connection_field"
        ),
        Index("ix_meta_webhook_subscriptions_workspace_state", "workspace_id", "provider_state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("whatsapp_connections.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(80), nullable=False)
    desired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    applicability: Mapped[str] = mapped_column(String(32), nullable=False, default="waba_level")
    provider_state: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sanitized_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    evidence_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)


class MetaWebhookAutomationPolicy(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "meta_webhook_automation_policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "field", name="uq_meta_webhook_policy_workspace_field"),
        Index("ix_meta_webhook_policies_workspace_mode", "workspace_id", "mode"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="observe_only")
    action_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    minimum_severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    owner_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_accepted_by: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MetaWebhookSubscriptionJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "meta_webhook_subscription_jobs"
    __table_args__ = (
        Index("ix_meta_webhook_subscription_jobs_workspace_status", "workspace_id", "status"),
        Index("ix_meta_webhook_subscription_jobs_created", "created_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    attempted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_summary: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    sanitized_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
