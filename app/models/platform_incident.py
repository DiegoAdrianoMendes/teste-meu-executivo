"""Incidentes operacionais administrados pela Central Blackbox."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformIncident(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_incidents"
    __table_args__ = (
        Index("ix_platform_incident_status_severity", "status", "severity"),
        Index("ix_platform_incident_started_at", "started_at"),
    )

    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="investigating")
    components: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updates: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
