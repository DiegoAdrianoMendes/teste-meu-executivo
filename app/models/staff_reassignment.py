"""Continuidade auditável de responsáveis nos dois domínios do produto."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class StaffReassignmentMode(enum.StrEnum):
    temporary = "temporary"
    permanent = "permanent"


class StaffReassignmentStatus(enum.StrEnum):
    applied = "applied"
    restored = "restored"
    partially_restored = "partially_restored"
    cancelled = "cancelled"


class StaffReassignmentItemStatus(enum.StrEnum):
    applied = "applied"
    restored = "restored"
    conflict = "conflict"


class WorkspaceStaffReassignmentPlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workspace_staff_reassignment_plans"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "requested_by_user_id",
            "idempotency_key",
            name="uq_workspace_staff_reassignment_idempotency",
        ),
        CheckConstraint(
            "source_user_id <> target_user_id",
            name="ck_workspace_staff_reassignment_distinct_users",
        ),
        CheckConstraint(
            "mode IN ('temporary', 'permanent')",
            name="ck_workspace_staff_reassignment_mode",
        ),
        CheckConstraint(
            "(mode = 'temporary' AND ends_at IS NOT NULL) OR "
            "(mode = 'permanent' AND ends_at IS NULL)",
            name="ck_workspace_staff_reassignment_window",
        ),
        CheckConstraint(
            "status IN ('applied', 'restored', 'partially_restored', 'cancelled')",
            name="ck_workspace_staff_reassignment_status",
        ),
        Index(
            "ix_workspace_staff_reassignment_source_status",
            "workspace_id",
            "source_user_id",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=StaffReassignmentStatus.applied.value,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    tag_label: Mapped[str] = mapped_column(String(120), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    assignment_summary: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restore_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkspaceStaffReassignmentItem(UUIDMixin, Base):
    __tablename__ = "workspace_staff_reassignment_items"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "entity_type",
            "entity_id",
            "assignment_field",
            name="uq_workspace_staff_reassignment_item",
        ),
        CheckConstraint(
            "status IN ('applied', 'restored', 'conflict')",
            name="ck_workspace_staff_reassignment_item_status",
        ),
        Index(
            "ix_workspace_staff_reassignment_item_plan_status",
            "workspace_id",
            "plan_id",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspace_staff_reassignment_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    assignment_field: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_user_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    assigned_user_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=StaffReassignmentItemStatus.applied.value,
    )
    tag_label: Mapped[str] = mapped_column(String(120), nullable=False)
    snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceStaffReassignmentEvent(UUIDMixin, Base):
    """Evidência append-only do ciclo de vida de uma transferência da conta."""

    __tablename__ = "workspace_staff_reassignment_events"
    __table_args__ = (
        Index(
            "ix_workspace_staff_reassignment_event_history",
            "workspace_id",
            "plan_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspace_staff_reassignment_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    details: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class PlatformStaffReassignmentPlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_staff_reassignment_plans"
    __table_args__ = (
        UniqueConstraint(
            "requested_by_user_id",
            "idempotency_key",
            name="uq_platform_staff_reassignment_idempotency",
        ),
        CheckConstraint(
            "source_user_id <> target_user_id",
            name="ck_platform_staff_reassignment_distinct_users",
        ),
        CheckConstraint(
            "mode IN ('temporary', 'permanent')",
            name="ck_platform_staff_reassignment_mode",
        ),
        CheckConstraint(
            "(mode = 'temporary' AND ends_at IS NOT NULL) OR "
            "(mode = 'permanent' AND ends_at IS NULL)",
            name="ck_platform_staff_reassignment_window",
        ),
        CheckConstraint(
            "status IN ('applied', 'restored', 'partially_restored', 'cancelled')",
            name="ck_platform_staff_reassignment_status",
        ),
        Index(
            "ix_platform_staff_reassignment_source_status",
            "source_user_id",
            "status",
        ),
    )

    source_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=StaffReassignmentStatus.applied.value,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    tag_label: Mapped[str] = mapped_column(String(120), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    assignment_summary: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict
    )
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restore_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlatformStaffReassignmentItem(UUIDMixin, Base):
    __tablename__ = "platform_staff_reassignment_items"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "entity_type",
            "entity_id",
            "assignment_field",
            name="uq_platform_staff_reassignment_item",
        ),
        CheckConstraint(
            "status IN ('applied', 'restored', 'conflict')",
            name="ck_platform_staff_reassignment_item_status",
        ),
        Index(
            "ix_platform_staff_reassignment_item_plan_status",
            "plan_id",
            "status",
        ),
    )

    plan_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("platform_staff_reassignment_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    assignment_field: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_user_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    assigned_user_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=StaffReassignmentItemStatus.applied.value,
    )
    tag_label: Mapped[str] = mapped_column(String(120), nullable=False)
    snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformStaffReassignmentEvent(UUIDMixin, Base):
    """Evidência append-only do ciclo de vida de uma transferência Blackbox."""

    __tablename__ = "platform_staff_reassignment_events"
    __table_args__ = (
        Index(
            "ix_platform_staff_reassignment_event_history",
            "plan_id",
            "occurred_at",
        ),
    )

    plan_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("platform_staff_reassignment_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    details: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


register_append_only_model(WorkspaceStaffReassignmentEvent)
register_append_only_model(PlatformStaffReassignmentEvent)


__all__ = [
    "PlatformStaffReassignmentEvent",
    "PlatformStaffReassignmentItem",
    "PlatformStaffReassignmentPlan",
    "StaffReassignmentItemStatus",
    "StaffReassignmentMode",
    "StaffReassignmentStatus",
    "WorkspaceStaffReassignmentEvent",
    "WorkspaceStaffReassignmentItem",
    "WorkspaceStaffReassignmentPlan",
]
