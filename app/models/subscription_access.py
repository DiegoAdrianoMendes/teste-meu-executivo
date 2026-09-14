"""Local, auditable subscription access projection for each workspace."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleUUID, TimestampMixin, UUIDMixin


class SubscriptionAccessStatus(enum.StrEnum):
    """Provider-neutral states used to decide SaaS access."""

    trial = "trial"
    active = "active"
    grace = "grace"
    past_due = "past_due"
    canceled = "canceled"
    suspended = "suspended"


def _status_enum() -> Enum:
    return Enum(
        SubscriptionAccessStatus,
        name="subscription_access_status_enum",
        native_enum=False,
        validate_strings=True,
        length=16,
    )


class SubscriptionAccessState(UUIDMixin, TimestampMixin, Base):
    """Current access projection; billing provider state remains in ``Subscription``."""

    __tablename__ = "subscription_access_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_subscription_access_workspace"),
        CheckConstraint(
            "status IN ('trial', 'active', 'grace', 'past_due', 'canceled', 'suspended')",
            name="ck_subscription_access_status",
        ),
        CheckConstraint("state_version >= 1", name="ck_subscription_access_version_positive"),
        CheckConstraint(
            "((status = 'trial' AND trial_ends_at IS NOT NULL) OR "
            "(status <> 'trial' AND trial_ends_at IS NULL))",
            name="ck_subscription_access_trial_window",
        ),
        CheckConstraint(
            "((status = 'grace' AND grace_started_at IS NOT NULL "
            "AND grace_ends_at IS NOT NULL AND grace_ends_at > grace_started_at) OR "
            "(status <> 'grace' AND grace_started_at IS NULL AND grace_ends_at IS NULL))",
            name="ck_subscription_access_grace_window",
        ),
        Index("ix_subscription_access_status", "status"),
        Index("ix_subscription_access_subscription_id", "subscription_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[SubscriptionAccessStatus] = mapped_column(_status_enum(), nullable=False)
    state_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SubscriptionAccessTransition(UUIDMixin, Base):
    """Append-only domain event for an attempted access-state transition."""

    __tablename__ = "subscription_access_transitions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_subscription_access_transition_idempotency",
        ),
        CheckConstraint(
            "from_status IS NULL OR "
            "from_status IN ('trial', 'active', 'grace', 'past_due', 'canceled', 'suspended')",
            name="ck_subscription_access_transition_from_status",
        ),
        CheckConstraint(
            "to_status IN ('trial', 'active', 'grace', 'past_due', 'canceled', 'suspended')",
            name="ck_subscription_access_transition_to_status",
        ),
        CheckConstraint(
            "((applied = true AND to_version = from_version + 1) OR "
            "(applied = false AND to_version = from_version))",
            name="ck_subscription_access_transition_version_step",
        ),
        Index(
            "ix_subscription_access_transition_workspace_occurred",
            "workspace_id",
            "occurred_at",
        ),
        Index(
            "ix_subscription_access_transition_state_version",
            "access_state_id",
            "to_version",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    access_state_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("subscription_access_states.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[SubscriptionAccessStatus | None] = mapped_column(
        _status_enum(), nullable=True
    )
    to_status: Mapped[SubscriptionAccessStatus] = mapped_column(_status_enum(), nullable=False)
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
