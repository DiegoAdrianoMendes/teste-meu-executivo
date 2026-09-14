"""Safe operational projection for the Blackbox task center.

The source domain remains authoritative.  This table contains only routing metadata
that may be rebuilt by the reconciliation service; it must never contain message or
document bodies, credentials, or other private source payloads.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformWorkItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_work_items"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_platform_work_items_dedupe_key"),
        Index("ix_platform_work_items_state_priority_due", "state", "priority", "due_at"),
        Index("ix_platform_work_items_assignee_state_due", "assigned_user_id", "state", "due_at"),
        Index("ix_platform_work_items_team_state_due", "assigned_team_id", "state", "due_at"),
        Index("ix_platform_work_items_source", "source_type", "source_id"),
        Index("ix_platform_work_items_workspace_state", "workspace_id", "state"),
        Index("ix_platform_work_items_cursor", "source_updated_at", "id"),
    )

    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    source_version: Mapped[str] = mapped_column(String(96), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(320), nullable=False)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(2), nullable=False)

    workspace_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # PlatformTeam is introduced by the dashboard foundation.  Until then this is a
    # validated opaque reference and deliberately has no premature foreign key.
    assigned_team_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    requester_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    safe_title: Mapped[str] = mapped_column(String(240), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, nullable=False)
    href: Mapped[str] = mapped_column(String(500), nullable=False)
    required_module: Mapped[str] = mapped_column(String(80), nullable=False)
    required_action: Mapped[str] = mapped_column(String(80), nullable=False)
    allowed_commands: Mapped[list[str]] = mapped_column(
        FlexibleJSON(), nullable=False, default=list
    )

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    projected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_safe: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
