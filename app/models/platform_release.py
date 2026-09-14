"""Canonical release records used by the Blackbox control plane."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformRelease(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_releases"
    __table_args__ = (
        UniqueConstraint("environment", "version", name="uq_platform_release_environment_version"),
        Index("ix_platform_releases_environment_deployed", "environment", "deployed_at"),
        Index("ix_platform_releases_status_deployed", "status", "deployed_at"),
    )

    version: Mapped[str] = mapped_column(String(120), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="manual_blackbox")
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    previous_release_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_releases.id", ondelete="SET NULL"), nullable=True
    )
    rollback_target_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_releases.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
