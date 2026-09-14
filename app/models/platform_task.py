"""Authoritative internal tasks for employees operating the SaaS.

``PlatformTask`` is intentionally different from both tenant ``Task`` records and
the rebuildable ``PlatformWorkItem`` projection.  It stores only work created for
the platform team and may *link* to safe control-plane resources without copying
tenant descriptions, comments, documents, or messages.
"""

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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


def _enum(enum_type: type[enum.StrEnum], name: str, *, length: int = 40) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [value.value for value in values],
        length=length,
    )


class PlatformTaskStatus(enum.StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    blocked = "blocked"
    done = "done"
    cancelled = "cancelled"


class PlatformTaskPriority(enum.StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class PlatformTaskReviewState(enum.StrEnum):
    not_required = "not_required"
    draft = "draft"
    pending = "pending"
    approved = "approved"
    changes_requested = "changes_requested"
    rejected = "rejected"


class PlatformTaskParticipantRole(enum.StrEnum):
    assignee = "assignee"
    observer = "observer"


class PlatformTaskParticipantState(enum.StrEnum):
    assigned = "assigned"
    in_progress = "in_progress"
    completed = "completed"
    removed = "removed"


class PlatformTaskReviewRoundState(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    changes_requested = "changes_requested"
    rejected = "rejected"
    cancelled = "cancelled"


class PlatformTaskEventType(enum.StrEnum):
    created = "created"
    updated = "updated"
    participants_changed = "participants_changed"
    started = "started"
    blocked = "blocked"
    review_submitted = "review_submitted"
    review_approved = "review_approved"
    review_changes_requested = "review_changes_requested"
    review_rejected = "review_rejected"
    completed = "completed"
    reopened = "reopened"
    cancelled = "cancelled"
    comment_added = "comment_added"


class PlatformTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_tasks"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_platform_tasks_version_positive"),
        CheckConstraint(
            "review_round_number >= 0",
            name="ck_platform_tasks_review_round_nonnegative",
        ),
        CheckConstraint(
            "reviewer_user_id IS NULL OR reviewer_user_id <> owner_user_id",
            name="ck_platform_tasks_reviewer_distinct_owner",
        ),
        CheckConstraint(
            "(review_required = true AND reviewer_user_id IS NOT NULL "
            "AND review_state <> 'not_required') OR "
            "(review_required = false AND reviewer_user_id IS NULL "
            "AND review_state = 'not_required')",
            name="ck_platform_tasks_review_contract",
        ),
        CheckConstraint(
            "due_at IS NULL OR owner_due_at IS NULL OR owner_due_at <= due_at",
            name="ck_platform_tasks_owner_deadline_within_task",
        ),
        CheckConstraint(
            "due_at IS NULL OR review_due_at IS NULL OR review_due_at <= due_at",
            name="ck_platform_tasks_review_deadline_within_task",
        ),
        Index("ix_platform_tasks_status_due", "status", "due_at"),
        Index("ix_platform_tasks_owner_status_due", "owner_user_id", "status", "due_at"),
        Index(
            "ix_platform_tasks_reviewer_state_due",
            "reviewer_user_id",
            "review_state",
            "review_due_at",
        ),
        Index("ix_platform_tasks_workspace_status", "workspace_id", "status"),
        Index("ix_platform_tasks_cursor", "updated_at", "id"),
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PlatformTaskStatus] = mapped_column(
        _enum(PlatformTaskStatus, "platform_task_status_enum"),
        nullable=False,
        default=PlatformTaskStatus.pending,
        server_default=PlatformTaskStatus.pending.value,
        index=True,
    )
    priority: Mapped[PlatformTaskPriority] = mapped_column(
        _enum(PlatformTaskPriority, "platform_task_priority_enum", length=2),
        nullable=False,
        default=PlatformTaskPriority.P2,
        server_default=PlatformTaskPriority.P2.value,
        index=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    review_state: Mapped[PlatformTaskReviewState] = mapped_column(
        _enum(PlatformTaskReviewState, "platform_task_review_state_enum"),
        nullable=False,
        default=PlatformTaskReviewState.not_required,
        server_default=PlatformTaskReviewState.not_required.value,
        index=True,
    )
    review_round_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reviewer_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Safe optional relations.  They store identifiers only; tenant content is
    # never copied into the internal task or its event history.
    workspace_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    linked_ticket_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("support_tickets.id", ondelete="SET NULL"), nullable=True
    )
    linked_incident_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_incidents.id", ondelete="SET NULL"), nullable=True
    )
    linked_lead_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("growth_prospects.id", ondelete="SET NULL"), nullable=True
    )
    linked_release_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_releases.id", ondelete="SET NULL"), nullable=True
    )

    participants: Mapped[list[PlatformTaskParticipant]] = relationship(
        "PlatformTaskParticipant",
        back_populates="task",
        cascade="save-update, merge",
        primaryjoin=(
            "and_(PlatformTask.id == PlatformTaskParticipant.task_id, "
            "PlatformTaskParticipant.active.is_(True))"
        ),
        order_by="PlatformTaskParticipant.created_at.asc()",
        lazy="selectin",
    )
    review_rounds: Mapped[list[PlatformTaskReviewRound]] = relationship(
        "PlatformTaskReviewRound",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="PlatformTaskReviewRound.round_number.asc()",
        lazy="selectin",
    )
    events: Mapped[list[PlatformTaskEvent]] = relationship(
        "PlatformTaskEvent",
        back_populates="task",
        passive_deletes=True,
        order_by="PlatformTaskEvent.occurred_at.asc()",
        lazy="raise",
    )


class PlatformTaskParticipant(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_task_participants"
    __table_args__ = (
        CheckConstraint(
            "required_for_completion = false OR role = 'assignee'",
            name="ck_platform_task_participants_required_assignee",
        ),
        CheckConstraint(
            "role <> 'observer' OR personal_due_at IS NULL",
            name="ck_platform_task_participants_observer_without_deadline",
        ),
        CheckConstraint(
            "(active = true AND state <> 'removed' AND removed_at IS NULL) OR "
            "(active = false AND state = 'removed' AND removed_at IS NOT NULL)",
            name="ck_platform_task_participants_lifecycle",
        ),
        Index(
            "ux_platform_task_participants_active_user",
            "task_id",
            "user_id",
            unique=True,
            postgresql_where=text("active = true"),
            sqlite_where=text("active = 1"),
        ),
        Index("ix_platform_task_participants_user_state", "user_id", "state"),
        Index("ix_platform_task_participants_task_active", "task_id", "active"),
    )

    task_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[PlatformTaskParticipantRole] = mapped_column(
        _enum(PlatformTaskParticipantRole, "platform_task_participant_role_enum"), nullable=False
    )
    personal_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[PlatformTaskParticipantState] = mapped_column(
        _enum(PlatformTaskParticipantState, "platform_task_participant_state_enum"),
        nullable=False,
        default=PlatformTaskParticipantState.assigned,
        server_default=PlatformTaskParticipantState.assigned.value,
    )
    required_for_completion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    label_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    task: Mapped[PlatformTask] = relationship("PlatformTask", back_populates="participants")


class PlatformTaskReviewRound(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "platform_task_review_rounds"
    __table_args__ = (
        UniqueConstraint("task_id", "round_number", name="uq_platform_task_review_round_number"),
        CheckConstraint(
            "round_number > 0",
            name="ck_platform_task_review_round_number_positive",
        ),
        CheckConstraint(
            "task_version_reviewed > 0",
            name="ck_platform_task_review_version_positive",
        ),
        CheckConstraint(
            "(state = 'pending' AND resolved_at IS NULL AND resolved_by_user_id IS NULL) OR "
            "(state <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_platform_task_review_resolution",
        ),
        Index("ix_platform_task_review_round_state_due", "state", "review_due_at"),
    )

    task_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_tasks.id", ondelete="CASCADE"), nullable=False
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[PlatformTaskReviewRoundState] = mapped_column(
        _enum(PlatformTaskReviewRoundState, "platform_task_review_round_state_enum"),
        nullable=False,
        default=PlatformTaskReviewRoundState.pending,
        server_default=PlatformTaskReviewRoundState.pending.value,
    )
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    task_version_reviewed: Mapped[int] = mapped_column(Integer, nullable=False)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped[PlatformTask] = relationship("PlatformTask", back_populates="review_rounds")


class PlatformTaskEvent(UUIDMixin, Base):
    """Append-only history for internal tasks."""

    __tablename__ = "platform_task_events"
    __table_args__ = (
        Index("ix_platform_task_events_task_occurred", "task_id", "occurred_at", "id"),
        Index("ix_platform_task_events_correlation", "correlation_id"),
    )

    task_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_tasks.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    actor_role_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    is_platform_root: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_type: Mapped[PlatformTaskEventType] = mapped_column(
        _enum(PlatformTaskEventType, "platform_task_event_type_enum"), nullable=False
    )
    before_data: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    after_data: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    changed_paths: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    administrative_session_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("administrative_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_type: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    resource_version_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource_version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[PlatformTask] = relationship("PlatformTask", back_populates="events")


register_append_only_model(PlatformTaskEvent)


__all__ = [
    "PlatformTask",
    "PlatformTaskEvent",
    "PlatformTaskEventType",
    "PlatformTaskParticipant",
    "PlatformTaskParticipantRole",
    "PlatformTaskParticipantState",
    "PlatformTaskPriority",
    "PlatformTaskReviewRound",
    "PlatformTaskReviewRoundState",
    "PlatformTaskReviewState",
    "PlatformTaskStatus",
]
