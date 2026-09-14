"""HTTP contracts for task reminder rules and their durable deliveries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from app.models.task_reminder import (
    TaskReminderChannel,
    TaskReminderDeliveryStatus,
    TaskReminderEventType,
    TaskReminderRecipient,
    TaskReminderRelativeTo,
    TaskReminderRuleState,
)
from app.schemas.base import CamelCaseSchema, WriteSchema

MAX_REMINDER_OFFSET_MINUTES = 5 * 366 * 24 * 60


class TaskReminderRuleCreate(WriteSchema):
    # On create, expectedVersion is the current Task.version.
    expected_version: int = Field(ge=1)
    relative_to: TaskReminderRelativeTo
    offset_minutes_before: int = Field(ge=0, le=MAX_REMINDER_OFFSET_MINUTES)
    channel: TaskReminderChannel = TaskReminderChannel.in_app
    recipient: TaskReminderRecipient
    recipient_user_id: UUID | None = None

    @model_validator(mode="after")
    def validate_specific_recipient(self) -> TaskReminderRuleCreate:
        if (self.recipient == TaskReminderRecipient.specific_user) != (
            self.recipient_user_id is not None
        ):
            raise ValueError(
                "recipientUserId é obrigatório somente quando recipient=specific_user."
            )
        return self


class TaskReminderRuleUpdate(WriteSchema):
    # On update, expectedVersion is the current reminder rule version.
    expected_version: int = Field(ge=1)
    relative_to: TaskReminderRelativeTo | None = None
    offset_minutes_before: int | None = Field(
        default=None, ge=0, le=MAX_REMINDER_OFFSET_MINUTES
    )
    channel: TaskReminderChannel | None = None
    recipient: TaskReminderRecipient | None = None
    recipient_user_id: UUID | None = None

    @model_validator(mode="after")
    def validate_update_shape(self) -> TaskReminderRuleUpdate:
        mutation_fields = self.model_fields_set - {"expected_version"}
        if not mutation_fields:
            raise ValueError("Informe ao menos um campo para atualizar.")
        if (
            "recipient_user_id" in self.model_fields_set
            and "recipient" not in self.model_fields_set
        ):
            raise ValueError("Altere recipient junto com recipientUserId.")
        if self.recipient is not None:
            if (self.recipient == TaskReminderRecipient.specific_user) != (
                self.recipient_user_id is not None
            ):
                raise ValueError(
                    "recipientUserId é obrigatório somente quando recipient=specific_user."
                )
        return self


class TaskReminderDeliveryRead(CamelCaseSchema):
    id: UUID
    recipient_user_id: UUID
    deadline_at: datetime
    scheduled_for: datetime
    channel: TaskReminderChannel
    status: TaskReminderDeliveryStatus
    attempts: int
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None
    last_error_code: str | None = None
    notification_id: UUID | None = None


class TaskReminderRuleRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    task_id: UUID
    relative_to: TaskReminderRelativeTo
    offset_minutes_before: int
    channel: TaskReminderChannel
    recipient: TaskReminderRecipient
    recipient_user_id: UUID | None = None
    state: TaskReminderRuleState
    active: bool
    version: int
    delivery_available: bool
    unavailability_reason: str | None = None
    next_scheduled_at: datetime | None = None
    deliveries: list[TaskReminderDeliveryRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class TaskReminderDeleteRead(CamelCaseSchema):
    id: UUID
    task_id: UUID
    deleted: bool = True
    version: int


class TaskReminderEventRead(CamelCaseSchema):
    id: UUID
    rule_id: UUID
    outbox_id: UUID | None = None
    actor_user_id: UUID | None = None
    event_type: TaskReminderEventType
    before_data: dict | None = None
    after_data: dict | None = None
    reason: str | None = None
    correlation_id: str
    resource_version_before: int | None = None
    resource_version_after: int | None = None
    occurred_at: datetime


class TaskReminderEventPage(CamelCaseSchema):
    items: list[TaskReminderEventRead]
    total: int


__all__ = [
    "TaskReminderDeleteRead",
    "TaskReminderDeliveryRead",
    "TaskReminderEventPage",
    "TaskReminderEventRead",
    "TaskReminderRuleCreate",
    "TaskReminderRuleRead",
    "TaskReminderRuleUpdate",
]
