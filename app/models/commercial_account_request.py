"""Tenant-originated commercial change requests.

The request is an auditable intent, never an implicit contract mutation.  A
platform operator must still review and apply the canonical contract change.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class CommercialAccountRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "commercial_account_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_commercial_account_request_idempotency"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_commercial_account_request_workspace"),
        Index(
            "ix_commercial_account_requests_workspace_status",
            "workspace_id",
            "status",
            "created_at",
        ),
        Index("ix_commercial_account_requests_status_created", "status", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    request_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    preview_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
