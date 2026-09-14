"""HTTP contracts for authoritative Blackbox employee tasks."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.platform_task import (
    PlatformTaskEventType,
    PlatformTaskParticipantRole,
    PlatformTaskParticipantState,
    PlatformTaskPriority,
    PlatformTaskReviewRoundState,
    PlatformTaskReviewState,
    PlatformTaskStatus,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


def _aware_datetime(value: datetime | None) -> datetime | None:
    if value is not None and value.utcoffset() is None:
        raise ValueError("Prazos devem incluir timezone explícito.")
    return value


class PlatformTaskCommand(enum.StrEnum):
    start = "start"
    block = "block"
    submit_for_review = "submit-for-review"
    approve = "approve"
    request_changes = "request-changes"
    reject = "reject"
    complete = "complete"
    reopen = "reopen"
    cancel = "cancel"


class PlatformTaskParticipantWrite(WriteSchema):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    user_id: UUID
    role: PlatformTaskParticipantRole
    personal_due_at: datetime | None = None
    required_for_completion: bool = True

    @field_validator("personal_due_at")
    @classmethod
    def validate_personal_due_at(cls, value: datetime | None) -> datetime | None:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def observer_is_non_blocking(self) -> PlatformTaskParticipantWrite:
        if self.role == PlatformTaskParticipantRole.observer:
            if self.personal_due_at is not None:
                raise ValueError("Observador não possui prazo individual.")
            if "required_for_completion" not in self.model_fields_set:
                self.required_for_completion = False
            elif self.required_for_completion:
                raise ValueError("Observador nunca pode bloquear a conclusão.")
        return self


class PlatformTaskParticipantRead(CamelCaseSchema):
    id: UUID
    user_id: UUID
    role: PlatformTaskParticipantRole
    personal_due_at: datetime | None = None
    state: PlatformTaskParticipantState
    required_for_completion: bool
    label_snapshot: str
    active: bool
    removed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PlatformTaskReviewRoundRead(CamelCaseSchema):
    id: UUID
    round_number: int
    reviewer_user_id: UUID
    state: PlatformTaskReviewRoundState
    review_due_at: datetime | None = None
    submitted_by_user_id: UUID
    submitted_at: datetime
    task_version_reviewed: int
    resolved_by_user_id: UUID | None = None
    resolved_at: datetime | None = None
    decision_reason: str | None = None


class _PlatformTaskAssignmentMixin(WriteSchema):
    owner_user_id: UUID
    owner_due_at: datetime | None = None
    reviewer_user_id: UUID | None = None
    review_due_at: datetime | None = None
    participants: list[PlatformTaskParticipantWrite] = Field(default_factory=list, max_length=200)

    @field_validator("owner_due_at", "review_due_at")
    @classmethod
    def validate_assignment_due_at(cls, value: datetime | None) -> datetime | None:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def validate_assignment_invariants(self):
        seen: set[UUID] = set()
        for participant in self.participants:
            if participant.user_id in seen:
                raise ValueError("Cada funcionário pode ocupar somente um papel participante.")
            seen.add(participant.user_id)
        if self.owner_user_id in seen:
            raise ValueError("O owner não deve ser repetido entre executores ou observadores.")
        if self.reviewer_user_id is not None:
            if self.reviewer_user_id == self.owner_user_id:
                raise ValueError("O reviewer deve ser diferente do owner.")
            if self.reviewer_user_id in seen:
                raise ValueError("O reviewer deve ser diferente de executores e observadores.")
        return self


class PlatformTaskCreate(_PlatformTaskAssignmentMixin):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    priority: PlatformTaskPriority = PlatformTaskPriority.P2
    due_at: datetime | None = None
    review_required: bool = False
    workspace_id: UUID | None = None
    linked_ticket_id: UUID | None = None
    linked_incident_id: UUID | None = None
    linked_lead_id: UUID | None = None
    linked_release_id: UUID | None = None
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("title", "reason", mode="before")
    @classmethod
    def strip_required_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, value: datetime | None) -> datetime | None:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def validate_deadlines_and_review(self) -> PlatformTaskCreate:
        if self.review_required and self.reviewer_user_id is None:
            raise ValueError("Tarefa com revisão exige reviewer.")
        if self.reviewer_user_id is not None:
            self.review_required = True
        deadlines = [self.owner_due_at, self.review_due_at]
        deadlines.extend(item.personal_due_at for item in self.participants)
        if self.due_at is not None and any(
            deadline is not None and deadline > self.due_at for deadline in deadlines
        ):
            raise ValueError("Prazo individual ou de revisão não pode superar o prazo geral.")
        return self


class PlatformTaskUpdate(WriteSchema):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    priority: PlatformTaskPriority | None = None
    due_at: datetime | None = None
    workspace_id: UUID | None = None
    linked_ticket_id: UUID | None = None
    linked_incident_id: UUID | None = None
    linked_lead_id: UUID | None = None
    linked_release_id: UUID | None = None
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("title", "reason", mode="before")
    @classmethod
    def strip_update_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, value: datetime | None) -> datetime | None:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def reject_null_for_required_columns(self) -> PlatformTaskUpdate:
        for field_name in ("title", "priority"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} não pode ser nulo.")
        return self


class PlatformTaskAssignmentsReplace(_PlatformTaskAssignmentMixin):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class PlatformTaskParticipantStateUpdate(WriteSchema):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_version: int = Field(ge=1)
    state: PlatformTaskParticipantState
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class PlatformTaskCommandRequest(WriteSchema):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=8, max_length=500)
    comment: str | None = Field(default=None, min_length=1, max_length=20_000)

    @field_validator("reason", "comment", mode="before")
    @classmethod
    def strip_optional_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class PlatformTaskCommentCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_version: int = Field(ge=1)
    comment: str = Field(min_length=1, max_length=20_000)

    @field_validator("comment", mode="before")
    @classmethod
    def strip_comment(cls, value):
        return value.strip() if isinstance(value, str) else value


class PlatformTaskRead(CamelCaseSchema):
    id: UUID
    title: str
    description: str | None = None
    status: PlatformTaskStatus
    priority: PlatformTaskPriority
    due_at: datetime | None = None
    owner_due_at: datetime | None = None
    review_due_at: datetime | None = None
    review_required: bool
    review_state: PlatformTaskReviewState
    review_round_number: int
    version: int
    owner_user_id: UUID
    reviewer_user_id: UUID | None = None
    created_by_user_id: UUID
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None
    blocked_reason: str | None = None
    workspace_id: UUID | None = None
    linked_ticket_id: UUID | None = None
    linked_incident_id: UUID | None = None
    linked_lead_id: UUID | None = None
    linked_release_id: UUID | None = None
    participants: list[PlatformTaskParticipantRead] = Field(default_factory=list)
    review_rounds: list[PlatformTaskReviewRoundRead] = Field(default_factory=list)
    allowed_commands: list[PlatformTaskCommand] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PlatformTaskPage(CamelCaseSchema):
    items: list[PlatformTaskRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class PlatformTaskEventRead(CamelCaseSchema):
    id: UUID
    task_id: UUID
    actor_user_id: UUID | None = None
    actor_label_snapshot: str
    actor_role_snapshot: str
    is_platform_root: bool
    event_type: PlatformTaskEventType
    before_data: dict | None = None
    after_data: dict | None = None
    changed_paths: list[str]
    comment: str | None = None
    reason: str
    correlation_id: str
    administrative_session_id: UUID | None = None
    session_type: str
    resource_version_before: int | None = None
    resource_version_after: int | None = None
    occurred_at: datetime


class PlatformTaskEventPage(CamelCaseSchema):
    items: list[PlatformTaskEventRead]
    total: int


__all__ = [name for name in globals() if name.startswith("PlatformTask")]
