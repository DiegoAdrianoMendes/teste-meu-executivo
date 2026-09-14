"""Version-safe contextual restoration previews and jobs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformRestorationPreview(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_restoration_previews"
    __table_args__ = (
        Index("ix_platform_restore_preview_actor_expires", "actor_user_id", "expires_at"),
        Index("ix_platform_restore_preview_resource", "resource_type", "resource_id"),
    )

    actor_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_audit_event_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_audit_events.id", ondelete="RESTRICT"), nullable=True
    )
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(160), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False)
    diff: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    impact: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    warnings: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    eligible: Mapped[bool] = mapped_column(nullable=False, default=False)
    ineligible_reasons: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformRestorationJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_restoration_jobs"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "idempotency_key", name="uq_platform_restore_actor_idempotency"),
        Index("ix_platform_restore_job_status_created", "status", "created_at"),
    )

    preview_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_restoration_previews.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
