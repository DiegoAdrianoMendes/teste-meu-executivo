"""Regras versionadas e outbox transacional de lembretes de tarefa."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
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
from app.models.task import UTCDateTime

if TYPE_CHECKING:
    from app.models.notification import Notification
    from app.models.task import Task


def _enum_values(enum_type: type[enum.Enum]) -> list[str]:
    return [str(item.value) for item in enum_type]


def _string_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=_enum_values,
    )


class TaskReminderRelativeTo(str, enum.Enum):
    general_due = "general_due"
    personal_due = "personal_due"
    review_due = "review_due"


class TaskReminderChannel(str, enum.Enum):
    in_app = "in_app"
    email = "email"
    whatsapp = "whatsapp"


class TaskReminderRecipient(str, enum.Enum):
    owner = "owner"
    assignees = "assignees"
    required_assignees = "required_assignees"
    reviewer = "reviewer"
    specific_user = "specific_user"


class TaskReminderRuleState(str, enum.Enum):
    scheduled = "scheduled"
    pending_due = "pending_due"
    pending_recipient = "pending_recipient"
    unavailable = "unavailable"
    disabled = "disabled"


class TaskReminderDeliveryStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    delivered = "delivered"
    failed = "failed"
    cancelled = "cancelled"
    unavailable = "unavailable"


class TaskReminderEventType(str, enum.Enum):
    created = "created"
    updated = "updated"
    deleted = "deleted"
    rescheduled = "rescheduled"
    cancelled = "cancelled"
    delivered = "delivered"
    delivery_unavailable = "delivery_unavailable"


class TaskReminderRule(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_reminder_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_reminder_rules_matter_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_reminder_rules_workspace_matter",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "offset_minutes_before >= 0",
            name="ck_task_reminder_rules_offset_nonnegative",
        ),
        CheckConstraint("version >= 1", name="ck_task_reminder_rules_version_positive"),
        CheckConstraint(
            "(recipient = 'specific_user' AND recipient_user_id IS NOT NULL) OR "
            "(recipient <> 'specific_user' AND recipient_user_id IS NULL)",
            name="ck_task_reminder_rules_specific_recipient",
        ),
        UniqueConstraint(
            "workspace_id",
            "matter_id",
            "task_id",
            "id",
            name="uq_task_reminder_rules_tenant_binding",
        ),
        Index(
            "ux_task_reminder_rules_active_semantic",
            "task_id",
            "relative_to",
            "offset_minutes_before",
            "channel",
            "recipient_key",
            unique=True,
            postgresql_where=text("active = true"),
            sqlite_where=text("active = 1"),
        ),
        Index("ix_task_reminder_rules_workspace_task", "workspace_id", "task_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    relative_to: Mapped[TaskReminderRelativeTo] = mapped_column(
        _string_enum(TaskReminderRelativeTo, "task_reminder_relative_to_enum"),
        nullable=False,
    )
    offset_minutes_before: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[TaskReminderChannel] = mapped_column(
        _string_enum(TaskReminderChannel, "task_reminder_channel_enum"), nullable=False
    )
    recipient: Mapped[TaskReminderRecipient] = mapped_column(
        _string_enum(TaskReminderRecipient, "task_reminder_recipient_enum"), nullable=False
    )
    recipient_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    # Avoids NULL-distinct behaviour in the semantic unique index.
    recipient_key: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[TaskReminderRuleState] = mapped_column(
        _string_enum(TaskReminderRuleState, "task_reminder_rule_state_enum"),
        nullable=False,
        default=TaskReminderRuleState.pending_due,
        server_default=TaskReminderRuleState.pending_due.value,
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    deleted_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    task: Mapped[Task] = relationship("Task")
    deliveries: Mapped[list[TaskReminderOutbox]] = relationship(
        "TaskReminderOutbox",
        back_populates="rule",
        passive_deletes=True,
        order_by="TaskReminderOutbox.created_at.desc()",
    )
    events: Mapped[list[TaskReminderEvent]] = relationship(
        "TaskReminderEvent",
        back_populates="rule",
        passive_deletes=True,
        order_by="TaskReminderEvent.occurred_at.asc()",
    )


class TaskReminderOutbox(UUIDMixin, TimestampMixin, Base):
    """One durable, idempotent delivery intent per resolved recipient."""

    __tablename__ = "task_reminder_outbox"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_reminder_outbox_matter_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_reminder_outbox_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id", "task_id", "rule_id"],
            [
                "task_reminder_rules.workspace_id",
                "task_reminder_rules.matter_id",
                "task_reminder_rules.task_id",
                "task_reminder_rules.id",
            ],
            name="fk_task_reminder_outbox_tenant_rule",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "matter_id",
            "task_id",
            "rule_id",
            "id",
            name="uq_task_reminder_outbox_tenant_binding",
        ),
        UniqueConstraint(
            "workspace_id", "dedupe_key", name="uq_task_reminder_outbox_dedupe"
        ),
        CheckConstraint("attempts >= 0", name="ck_task_reminder_outbox_attempts_nonnegative"),
        Index(
            "ix_task_reminder_outbox_due",
            "status",
            "scheduled_for",
            "available_at",
        ),
        Index(
            "ix_task_reminder_outbox_workspace_due",
            "workspace_id",
            "status",
            "scheduled_for",
            "available_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    rule_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    task_version: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    deadline_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    channel: Mapped[TaskReminderChannel] = mapped_column(
        _string_enum(TaskReminderChannel, "task_reminder_outbox_channel_enum"), nullable=False
    )
    status: Mapped[TaskReminderDeliveryStatus] = mapped_column(
        _string_enum(TaskReminderDeliveryStatus, "task_reminder_delivery_status_enum"),
        nullable=False,
        default=TaskReminderDeliveryStatus.pending,
        server_default=TaskReminderDeliveryStatus.pending.value,
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String(240), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    available_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    claim_token: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notification_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True
    )

    rule: Mapped[TaskReminderRule] = relationship(
        "TaskReminderRule", back_populates="deliveries"
    )
    notification: Mapped[Notification | None] = relationship("Notification")


class TaskReminderEvent(UUIDMixin, Base):
    """Append-only rule and delivery history."""

    __tablename__ = "task_reminder_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_reminder_events_matter_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_reminder_events_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id", "task_id", "rule_id"],
            [
                "task_reminder_rules.workspace_id",
                "task_reminder_rules.matter_id",
                "task_reminder_rules.task_id",
                "task_reminder_rules.id",
            ],
            name="fk_task_reminder_events_tenant_rule",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id", "task_id", "rule_id", "outbox_id"],
            [
                "task_reminder_outbox.workspace_id",
                "task_reminder_outbox.matter_id",
                "task_reminder_outbox.task_id",
                "task_reminder_outbox.rule_id",
                "task_reminder_outbox.id",
            ],
            name="fk_task_reminder_events_tenant_outbox",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_task_reminder_events_workspace_rule_occurred",
            "workspace_id",
            "rule_id",
            "occurred_at",
        ),
        Index("ix_task_reminder_events_correlation", "correlation_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    rule_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    outbox_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[TaskReminderEventType] = mapped_column(
        _string_enum(TaskReminderEventType, "task_reminder_event_type_enum"), nullable=False
    )
    before_data: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    after_data: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    session_type: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    administrative_session_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    support_grant_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    resource_version_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource_version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=func.now(), index=True
    )

    rule: Mapped[TaskReminderRule] = relationship("TaskReminderRule", back_populates="events")


class TaskReminderCommandReceipt(UUIDMixin, Base):
    """Immutable response receipt for Idempotency-Key protected mutations."""

    __tablename__ = "task_reminder_command_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_reminder_receipts_matter_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_reminder_receipts_workspace_matter",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id", "task_id", "rule_id"],
            [
                "task_reminder_rules.workspace_id",
                "task_reminder_rules.matter_id",
                "task_reminder_rules.task_id",
                "task_reminder_rules.id",
            ],
            name="fk_task_reminder_receipts_tenant_rule",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_task_reminder_command_receipt_key"
        ),
        Index("ix_task_reminder_command_receipts_task", "workspace_id", "task_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    rule_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_data: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=func.now()
    )


register_append_only_model(TaskReminderEvent)
register_append_only_model(TaskReminderCommandReceipt)


__all__ = [
    "TaskReminderChannel",
    "TaskReminderCommandReceipt",
    "TaskReminderDeliveryStatus",
    "TaskReminderEvent",
    "TaskReminderEventType",
    "TaskReminderOutbox",
    "TaskReminderRecipient",
    "TaskReminderRelativeTo",
    "TaskReminderRule",
    "TaskReminderRuleState",
]
