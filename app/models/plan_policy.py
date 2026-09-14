"""Versioned plan-package policies and immutable workspace assignments."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin


class PlanPolicyRolloutMode(StrEnum):
    """Supported cohort-selection semantics for one published revision."""

    existing_and_new = "existing_and_new"
    new_only = "new_only"
    scheduled = "scheduled"
    rule = "rule"


def _rollout_mode_enum() -> Enum:
    return Enum(
        PlanPolicyRolloutMode,
        name="plan_policy_rollout_mode_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class PlanPolicyDraft(UUIDMixin, TimestampMixin, Base):
    """Mutable editor state; published revisions never point back to this row."""

    __tablename__ = "plan_policy_drafts"
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_plan_policy_draft_plan"),
        CheckConstraint("revision > 0", name="ck_plan_policy_draft_revision_positive"),
    )

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class PlanPolicyVersion(UUIDMixin, Base):
    """An immutable, complete plan-package snapshot."""

    __tablename__ = "plan_policy_versions"
    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uq_plan_policy_version_number"),
        UniqueConstraint("id", "plan_id", name="uq_plan_policy_version_plan_binding"),
        CheckConstraint("version > 0", name="ck_plan_policy_version_positive"),
        Index(
            "ix_plan_policy_version_effective",
            "plan_id",
            "effective_at",
            "version",
        ),
    )

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    snapshot: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rollout_mode: Mapped[PlanPolicyRolloutMode] = mapped_column(
        _rollout_mode_enum(), nullable=False
    )
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rollout_rule: Mapped[dict[str, Any] | None] = mapped_column(FlexibleJSON(), nullable=True)
    publication_reason: Mapped[str] = mapped_column(Text, nullable=False)
    published_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkspacePlanPolicyAssignment(UUIDMixin, Base):
    """Append-only evidence that a workspace resolved to one policy revision."""

    __tablename__ = "workspace_plan_policy_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_version_id", "plan_id"],
            ["plan_policy_versions.id", "plan_policy_versions.plan_id"],
            ondelete="RESTRICT",
            name="fk_workspace_plan_policy_version_plan",
        ),
        UniqueConstraint(
            "workspace_id",
            "policy_version_id",
            name="uq_workspace_plan_policy_assignment",
        ),
        Index(
            "ix_workspace_plan_policy_current",
            "workspace_id",
            "plan_id",
            "assigned_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(nullable=False)
    assignment_reason: Mapped[str] = mapped_column(String(40), nullable=False)
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlanPolicyAuditEvent(UUIDMixin, Base):
    """Append-only audit trail for editing, publishing, and assignment decisions."""

    __tablename__ = "plan_policy_audit_events"
    __table_args__ = (
        Index("ix_plan_policy_audit_plan_time", "plan_id", "occurred_at"),
        Index(
            "ix_plan_policy_audit_workspace_time",
            "workspace_id",
            "occurred_at",
        ),
    )

    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    policy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("plan_policy_versions.id", ondelete="RESTRICT"), nullable=True
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    event_data: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlanPolicyIdempotencyRecord(UUIDMixin, Base):
    """Durable replay receipt for publication commands."""

    __tablename__ = "plan_policy_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "operation",
            "plan_id",
            "idempotency_key",
            name="uq_plan_policy_idempotency_scope",
        ),
        Index("ix_plan_policy_idempotency_created", "created_at"),
    )

    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("plan_policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    response_payload: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


for _append_only_model in (
    PlanPolicyVersion,
    WorkspacePlanPolicyAssignment,
    PlanPolicyAuditEvent,
    PlanPolicyIdempotencyRecord,
):
    register_append_only_model(_append_only_model)


__all__ = [
    "PlanPolicyAuditEvent",
    "PlanPolicyDraft",
    "PlanPolicyIdempotencyRecord",
    "PlanPolicyRolloutMode",
    "PlanPolicyVersion",
    "WorkspacePlanPolicyAssignment",
]
