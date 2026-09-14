"""Persisted, private administrative export jobs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformExportJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_export_jobs"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "idempotency_key", name="uq_platform_export_actor_idempotency"),
        Index("ix_platform_export_actor_created", "actor_user_id", "created_at"),
        Index("ix_platform_export_status_created", "status", "created_at"),
        Index("ix_platform_export_expires", "expires_at"),
    )

    actor_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    filters: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    columns: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    selection_set_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_selection_sets.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
