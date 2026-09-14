"""Read-only, least-privilege external shares for legal tasks."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleUUID, TimestampMixin, UUIDMixin


class TaskExternalShare(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_external_shares"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_task_external_shares_token_hash"),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key_hash",
            name="uq_task_external_shares_workspace_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_external_shares_workspace_task",
            ondelete="CASCADE",
        ),
        Index("ix_task_external_shares_workspace_task", "workspace_id", "task_id"),
        Index("ix_task_external_shares_expires_at", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(500), nullable=False)
    status_snapshot: Mapped[str] = mapped_column(String(32), nullable=False)
    due_at_snapshot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safe_context_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
