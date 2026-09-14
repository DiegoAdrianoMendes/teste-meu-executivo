"""Local, temporary and auditable support access grants."""
from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

SUPPORT_ACCESS_MIN_DURATION_MINUTES = 5
SUPPORT_ACCESS_MAX_DURATION_MINUTES = 30


class SupportAccessState(enum.StrEnum):
    requested = "requested"
    approved = "approved"
    active = "active"
    expired = "expired"
    revoked = "revoked"
    rejected = "rejected"


class SupportAccessScope(enum.StrEnum):
    workspace_read = "workspace:read"
    workspace_write = "workspace:write"
    members_read = "members:read"
    members_write = "members:write"
    clients_read = "clients:read"
    clients_write = "clients:write"
    matters_read = "matters:read"
    matters_write = "matters:write"
    tasks_read = "tasks:read"
    tasks_write = "tasks:write"
    meetings_read = "meetings:read"
    meetings_write = "meetings:write"
    documents_read = "documents:read"
    finance_read = "finance:read"
    finance_write = "finance:write"
    reports_read = "reports:read"
    configurations_read = "configurations:read"
    configurations_write = "configurations:write"
    lead_data_read = "lead_data:read"


def _string_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class SupportAccessGrant(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "support_access_grants"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "requester_user_id",
            "ticket_reference",
            name="uq_support_access_grant_request_ticket",
        ),
        UniqueConstraint(
            "active_token_jti",
            name="uq_support_access_grant_active_token_jti",
        ),
        CheckConstraint(
            f"requested_duration_minutes BETWEEN {SUPPORT_ACCESS_MIN_DURATION_MINUTES} "
            f"AND {SUPPORT_ACCESS_MAX_DURATION_MINUTES}",
            name="ck_support_access_requested_duration",
        ),
        CheckConstraint(
            "approved_duration_minutes IS NULL OR "
            "(approved_duration_minutes >= 1 AND "
            "approved_duration_minutes <= requested_duration_minutes)",
            name="ck_support_access_approved_duration",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 15",
            name="ck_support_access_reason_required",
        ),
        CheckConstraint(
            "length(trim(ticket_reference)) >= 3",
            name="ck_support_access_ticket_required",
        ),
        Index(
            "ix_support_access_workspace_state_requested",
            "workspace_id",
            "state",
            "requested_at",
        ),
        Index(
            "ix_support_access_requester_state_requested",
            "requester_user_id",
            "state",
            "requested_at",
        ),
        Index(
            "ix_support_access_state_expires",
            "state",
            "expires_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requester_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    requested_scopes: Mapped[list[str]] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=list,
    )
    requested_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[SupportAccessState] = mapped_column(
        _string_enum(SupportAccessState, "support_access_state_enum"),
        nullable=False,
        default=SupportAccessState.requested,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    approver_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_scopes: Mapped[list[str] | None] = mapped_column(
        FlexibleJSON(),
        nullable=True,
    )
    approved_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    activated_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    activation_admin_session_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("administrative_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    active_token_jti: Mapped[str | None] = mapped_column(String(240), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rejected_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SupportAccessEvent(UUIDMixin, Base):
    """Append-only security trail; the migration rejects UPDATE and DELETE."""

    __tablename__ = "support_access_events"
    __table_args__ = (
        Index(
            "ix_support_access_events_grant_occurred",
            "grant_id",
            "occurred_at",
        ),
        Index(
            "ix_support_access_events_workspace_occurred",
            "workspace_id",
            "occurred_at",
        ),
    )

    grant_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("support_access_grants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    details: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
