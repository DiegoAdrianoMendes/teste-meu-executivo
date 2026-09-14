"""Native, tenant-scoped staff availability and approval records."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class AvailabilityExceptionScope(StrEnum):
    staff = "staff"
    workspace = "workspace"


class AvailabilityExceptionKind(StrEnum):
    unavailable = "unavailable"
    available_override = "available_override"
    vacation = "vacation"
    holiday = "holiday"


class AvailabilityRedirectKind(StrEnum):
    user = "user"
    team = "team"


class AvailabilityMode(StrEnum):
    available = "available"
    away = "away"


class AvailabilityChangeType(StrEnum):
    replace_configuration = "replace_configuration"
    replace_recurring_schedule = "replace_recurring_schedule"
    create_exception = "create_exception"
    update_exception = "update_exception"
    delete_exception = "delete_exception"
    set_absence_routing = "set_absence_routing"


class AvailabilityRequestStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class AvailabilityAuditAction(StrEnum):
    request_submitted = "request_submitted"
    request_approved = "request_approved"
    request_rejected = "request_rejected"
    request_cancelled = "request_cancelled"
    configuration_replaced = "configuration_replaced"
    schedule_replaced = "schedule_replaced"
    absence_routing_changed = "absence_routing_changed"
    exception_created = "exception_created"
    exception_updated = "exception_updated"
    exception_deleted = "exception_deleted"


def _portable_enum(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


_REDIRECT_TARGET_CHECK = """
(
    redirect_kind IS NULL
    AND redirect_user_id IS NULL
    AND redirect_team_id IS NULL
)
OR (
    redirect_kind = 'user'
    AND redirect_user_id IS NOT NULL
    AND redirect_team_id IS NULL
)
OR (
    redirect_kind = 'team'
    AND redirect_user_id IS NULL
    AND redirect_team_id IS NOT NULL
)
"""


class StaffAvailabilityProfile(UUIDMixin, TimestampMixin, Base):
    """Current approved availability settings for one workspace member."""

    __tablename__ = "staff_availability_profiles"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "staff_user_id",
            name="uq_staff_availability_profile_member",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_staff_availability_profile_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "staff_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_profile_member",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "redirect_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_profile_redirect_user",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "redirect_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_staff_availability_profile_redirect_team",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 0", name="ck_staff_availability_profile_version"),
        CheckConstraint(
            _REDIRECT_TARGET_CHECK,
            name="ck_staff_availability_profile_redirect_target",
        ),
        CheckConstraint(
            "absence_message_starts_at IS NULL "
            "OR absence_message_ends_at IS NULL "
            "OR absence_message_ends_at > absence_message_starts_at",
            name="ck_staff_availability_profile_message_window",
        ),
        CheckConstraint(
            "redirect_starts_at IS NULL "
            "OR redirect_ends_at IS NULL "
            "OR redirect_ends_at > redirect_starts_at",
            name="ck_staff_availability_profile_redirect_window",
        ),
        CheckConstraint(
            "availability_starts_at IS NULL "
            "OR availability_ends_at IS NULL "
            "OR availability_ends_at > availability_starts_at",
            name="ck_staff_availability_profile_state_window",
        ),
        Index(
            "ix_staff_availability_profile_workspace_staff",
            "workspace_id",
            "staff_user_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    staff_user_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="UTC",
        server_default="UTC",
    )
    schedule_configured: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    availability_mode: Mapped[AvailabilityMode] = mapped_column(
        _portable_enum(AvailabilityMode, "staff_availability_mode"),
        nullable=False,
        default=AvailabilityMode.available,
        server_default=AvailabilityMode.available.value,
    )
    availability_starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    availability_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    absence_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    absence_message_starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    absence_message_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redirect_kind: Mapped[AvailabilityRedirectKind | None] = mapped_column(
        _portable_enum(AvailabilityRedirectKind, "staff_availability_redirect_kind"),
        nullable=True,
    )
    redirect_user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    redirect_team_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    redirect_starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redirect_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auto_reassign_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class StaffRecurringAvailability(UUIDMixin, TimestampMixin, Base):
    """An approved local-time interval repeated weekly for a profile."""

    __tablename__ = "staff_recurring_availability"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "profile_id",
            "weekday",
            "starts_at",
            "ends_at",
            "valid_from",
            "valid_until",
            name="uq_staff_recurring_availability_slot",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "profile_id"],
            ["staff_availability_profiles.workspace_id", "staff_availability_profiles.id"],
            name="fk_staff_recurring_availability_profile",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_request_id"],
            [
                "staff_availability_change_requests.workspace_id",
                "staff_availability_change_requests.id",
            ],
            name="fk_staff_recurring_availability_request",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_staff_recurring_availability_weekday",
        ),
        CheckConstraint(
            "ends_at > starts_at",
            name="ck_staff_recurring_availability_time_range",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from",
            name="ck_staff_recurring_availability_date_range",
        ),
        Index(
            "ix_staff_recurring_availability_lookup",
            "workspace_id",
            "profile_id",
            "weekday",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    ends_at: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_request_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)


class StaffAvailabilityException(UUIDMixin, TimestampMixin, Base):
    """Approved interval overriding the weekly schedule for staff or the workspace."""

    __tablename__ = "staff_availability_exceptions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_staff_availability_exception_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "staff_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_exception_member",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "redirect_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_exception_redirect_user",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "redirect_team_id"],
            ["klm_workspace_teams.workspace_id", "klm_workspace_teams.id"],
            name="fk_staff_availability_exception_redirect_team",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_request_id"],
            [
                "staff_availability_change_requests.workspace_id",
                "staff_availability_change_requests.id",
            ],
            name="fk_staff_availability_exception_request",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(scope = 'staff' AND staff_user_id IS NOT NULL) "
            "OR (scope = 'workspace' AND staff_user_id IS NULL AND kind = 'holiday')",
            name="ck_staff_availability_exception_scope",
        ),
        CheckConstraint(
            "ends_at > starts_at",
            name="ck_staff_availability_exception_time_range",
        ),
        CheckConstraint("version >= 1", name="ck_staff_availability_exception_version"),
        CheckConstraint(
            _REDIRECT_TARGET_CHECK,
            name="ck_staff_availability_exception_redirect_target",
        ),
        Index(
            "ix_staff_availability_exception_lookup",
            "workspace_id",
            "staff_user_id",
            "starts_at",
            "ends_at",
        ),
        Index(
            "ix_staff_availability_exception_workspace_holiday",
            "workspace_id",
            "starts_at",
            "ends_at",
            postgresql_where=text("scope = 'workspace' AND deleted_at IS NULL"),
            sqlite_where=text("scope = 'workspace' AND deleted_at IS NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[AvailabilityExceptionScope] = mapped_column(
        _portable_enum(AvailabilityExceptionScope, "staff_availability_exception_scope"),
        nullable=False,
        default=AvailabilityExceptionScope.staff,
        server_default=AvailabilityExceptionScope.staff.value,
    )
    staff_user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    kind: Mapped[AvailabilityExceptionKind] = mapped_column(
        _portable_enum(AvailabilityExceptionKind, "staff_availability_exception_kind"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    absence_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    redirect_kind: Mapped[AvailabilityRedirectKind | None] = mapped_column(
        _portable_enum(AvailabilityRedirectKind, "staff_availability_exception_redirect_kind"),
        nullable=True,
    )
    redirect_user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    redirect_team_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    auto_reassign: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    source_request_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class StaffAvailabilityChangeRequest(UUIDMixin, TimestampMixin, Base):
    """Pending proposal; effective tables change only after an authorized decision."""

    __tablename__ = "staff_availability_change_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_staff_availability_request_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "requested_by_user_id",
            "idempotency_key",
            name="uq_staff_availability_request_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "requested_by_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_request_requester",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "target_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_request_target",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "resolved_by_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_staff_availability_request_resolver",
            ondelete="RESTRICT",
        ),
        CheckConstraint("base_version >= 0", name="ck_staff_availability_request_base_version"),
        CheckConstraint(
            "(status = 'pending' AND resolved_by_user_id IS NULL AND resolved_at IS NULL) "
            "OR (status <> 'pending' AND resolved_by_user_id IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_staff_availability_request_resolution",
        ),
        CheckConstraint(
            "(status = 'approved' AND applied_version IS NOT NULL) "
            "OR (status <> 'approved' AND applied_version IS NULL)",
            name="ck_staff_availability_request_applied_version",
        ),
        Index(
            "ix_staff_availability_request_queue",
            "workspace_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_staff_availability_request_target",
            "workspace_id",
            "target_user_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    change_type: Mapped[AvailabilityChangeType] = mapped_column(
        _portable_enum(AvailabilityChangeType, "staff_availability_change_type"),
        nullable=False,
        index=True,
    )
    status: Mapped[AvailabilityRequestStatus] = mapped_column(
        _portable_enum(AvailabilityRequestStatus, "staff_availability_request_status"),
        nullable=False,
        default=AvailabilityRequestStatus.pending,
        server_default=AvailabilityRequestStatus.pending.value,
        index=True,
    )
    proposed_change: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    target_entity_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    decision_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decision_policy: Mapped[dict[str, Any] | None] = mapped_column(FlexibleJSON(), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StaffAvailabilityAuditEvent(UUIDMixin, Base):
    """Append-only evidence for proposal decisions and approved state transitions."""

    __tablename__ = "staff_availability_audit_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_staff_availability_audit_workspace_id",
        ),
        Index(
            "ix_staff_availability_audit_history",
            "workspace_id",
            "staff_user_id",
            "occurred_at",
        ),
        Index(
            "ix_staff_availability_audit_request",
            "workspace_id",
            "request_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    staff_user_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    request_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[AvailabilityAuditAction] = mapped_column(
        _portable_enum(AvailabilityAuditAction, "staff_availability_audit_action"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(FlexibleJSON(), nullable=True)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(FlexibleJSON(), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


register_append_only_model(StaffAvailabilityAuditEvent)


__all__ = [
    "AvailabilityAuditAction",
    "AvailabilityChangeType",
    "AvailabilityExceptionKind",
    "AvailabilityExceptionScope",
    "AvailabilityMode",
    "AvailabilityRedirectKind",
    "AvailabilityRequestStatus",
    "StaffAvailabilityAuditEvent",
    "StaffAvailabilityChangeRequest",
    "StaffAvailabilityException",
    "StaffAvailabilityProfile",
    "StaffRecurringAvailability",
]
