"""Persisted control-plane primitives for Blackbox bulk operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformSelectionSet(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_selection_sets"
    __table_args__ = (
        Index("ix_platform_selection_sets_actor_expires", "actor_user_id", "expires_at"),
        Index("ix_platform_selection_sets_status_expires", "status", "expires_at"),
    )

    actor_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    filters: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    sort: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    excluded_ids: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    filter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlatformSelectionItem(UUIDMixin, Base):
    __tablename__ = "platform_selection_items"
    __table_args__ = (
        UniqueConstraint(
            "selection_set_id", "resource_id", name="uq_platform_selection_item_resource"
        ),
        UniqueConstraint("selection_set_id", "ordinal", name="uq_platform_selection_item_ordinal"),
        Index("ix_platform_selection_items_selection_ordinal", "selection_set_id", "ordinal"),
    )

    selection_set_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("platform_selection_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    initially_eligible: Mapped[bool] = mapped_column(nullable=False, default=True)


class PlatformBulkPreview(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_bulk_previews"
    __table_args__ = (
        Index("ix_platform_bulk_previews_actor_expires", "actor_user_id", "expires_at"),
        Index("ix_platform_bulk_previews_selection", "selection_set_id"),
    )

    selection_set_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("platform_selection_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    parameters: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    no_op_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    blockers: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    impact_summary: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    sample: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    eligible_targets: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    confirmation_phrase: Mapped[str | None] = mapped_column(String(160), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformBulkJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_bulk_jobs"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id", "idempotency_key", name="uq_platform_bulk_job_actor_idempotency"
        ),
        Index("ix_platform_bulk_jobs_actor_created", "actor_user_id", "created_at"),
        Index("ix_platform_bulk_jobs_status_created", "status", "created_at"),
    )

    preview_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_bulk_previews.id", ondelete="RESTRICT"), nullable=False
    )
    selection_set_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("platform_selection_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    parameters: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflicted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformBulkJobItem(UUIDMixin, Base):
    __tablename__ = "platform_bulk_job_items"
    __table_args__ = (
        UniqueConstraint("job_id", "resource_id", name="uq_platform_bulk_job_item_resource"),
        UniqueConstraint("job_id", "ordinal", name="uq_platform_bulk_job_item_ordinal"),
        Index("ix_platform_bulk_job_items_job_status", "job_id", "status", "ordinal"),
    )

    job_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_bulk_jobs.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    before: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    after: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    target: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
