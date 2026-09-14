"""Validated API contracts for native staff availability."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Literal, Self, TypeAlias
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.models.staff_availability import (
    AvailabilityAuditAction,
    AvailabilityChangeType,
    AvailabilityExceptionKind,
    AvailabilityExceptionScope,
    AvailabilityMode,
    AvailabilityRedirectKind,
    AvailabilityRequestStatus,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


def _require_aware(value: datetime | None, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must include a UTC offset")


def _validate_window(
    starts_at: datetime | None,
    ends_at: datetime | None,
    *,
    field_prefix: str,
    require_pair: bool = False,
) -> None:
    _require_aware(starts_at, f"{field_prefix}_starts_at")
    _require_aware(ends_at, f"{field_prefix}_ends_at")
    if require_pair and ((starts_at is None) != (ends_at is None)):
        raise ValueError(f"{field_prefix} requires both starts_at and ends_at")
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise ValueError(f"{field_prefix}_ends_at must be after {field_prefix}_starts_at")


def _normalize_optional_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    return normalized or None


class RedirectTargetWrite(WriteSchema):
    kind: AvailabilityRedirectKind
    user_id: UUID | None = None
    team_id: UUID | None = None

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if self.kind is AvailabilityRedirectKind.user:
            if self.user_id is None or self.team_id is not None:
                raise ValueError("A user redirect requires only user_id")
        elif self.team_id is None or self.user_id is not None:
            raise ValueError("A team redirect requires only team_id")
        return self


class RecurringAvailabilitySlotWrite(WriteSchema):
    weekday: int = Field(ge=0, le=6, description="Monday=0, Sunday=6")
    starts_at: time
    ends_at: time
    valid_from: date | None = None
    valid_until: date | None = None
    label: str | None = Field(default=None, max_length=120)

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.starts_at.tzinfo is not None or self.ends_at.tzinfo is not None:
            raise ValueError("Recurring slot times must be local wall-clock times")
        if self.ends_at <= self.starts_at:
            raise ValueError("Recurring slots cannot be empty or span midnight")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("valid_until must be on or after valid_from")
        return self


class ReplaceRecurringScheduleChange(WriteSchema):
    change_type: Literal[AvailabilityChangeType.replace_recurring_schedule] = (
        AvailabilityChangeType.replace_recurring_schedule
    )
    timezone: str = Field(min_length=1, max_length=64)
    slots: list[RecurringAvailabilitySlotWrite] = Field(default_factory=list, max_length=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return normalized

    @model_validator(mode="after")
    def reject_overlapping_slots(self) -> Self:
        for index, left in enumerate(self.slots):
            for right in self.slots[index + 1 :]:
                if left.weekday != right.weekday:
                    continue
                left_from = left.valid_from or date.min
                left_until = left.valid_until or date.max
                right_from = right.valid_from or date.min
                right_until = right.valid_until or date.max
                date_ranges_overlap = left_from <= right_until and right_from <= left_until
                time_ranges_overlap = (
                    left.starts_at < right.ends_at and right.starts_at < left.ends_at
                )
                if date_ranges_overlap and time_ranges_overlap:
                    raise ValueError("Recurring slots cannot overlap for the same weekday")
        return self


class ConfigurationTimeWindowWrite(WriteSchema):
    start: time
    end: time

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("Configuration windows must use local wall-clock times")
        if self.end <= self.start:
            raise ValueError("Configuration windows cannot be empty or span midnight")
        return self


class ConfigurationWeekdayWrite(WriteSchema):
    weekday: int = Field(ge=0, le=6)
    enabled: bool
    windows: list[ConfigurationTimeWindowWrite] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_windows(self) -> Self:
        if self.enabled and not self.windows:
            raise ValueError("Enabled weekdays require at least one time window")
        ordered = sorted(self.windows, key=lambda item: item.start)
        if any(left.end > right.start for left, right in zip(ordered, ordered[1:])):
            raise ValueError("Configuration windows cannot overlap")
        return self


class ConfigurationActivationWrite(WriteSchema):
    mode: Literal["now", "scheduled"]
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def validate_activation(self) -> Self:
        if self.mode == "now":
            if self.starts_at is not None or self.ends_at is not None:
                raise ValueError("Immediate activation cannot include a time window")
            return self
        if self.starts_at is None or self.ends_at is None:
            raise ValueError("Scheduled activation requires starts_at and ends_at")
        starts_aware = self.starts_at.tzinfo is not None and self.starts_at.utcoffset() is not None
        ends_aware = self.ends_at.tzinfo is not None and self.ends_at.utcoffset() is not None
        if starts_aware != ends_aware:
            raise ValueError("Activation timestamps must use the same timezone form")
        if self.ends_at <= self.starts_at:
            raise ValueError("Activation ends_at must be after starts_at")
        return self


class ConfigurationAbsenceMessageWrite(WriteSchema):
    enabled: bool
    text: str = Field(default="", max_length=800)

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_message(self) -> Self:
        if self.enabled and len(self.text) < 3:
            raise ValueError("Enabled absence messages require at least 3 characters")
        return self


class ConfigurationRedirectWrite(WriteSchema):
    kind: Literal["none", "user", "team"]
    target_id: UUID | None = None

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if self.kind == "none" and self.target_id is not None:
            raise ValueError("A disabled redirect cannot include target_id")
        if self.kind != "none" and self.target_id is None:
            raise ValueError("An enabled redirect requires target_id")
        return self


class ConfigurationExceptionWrite(WriteSchema):
    id: str = Field(min_length=1, max_length=180)
    kind: Literal["vacation", "holiday", "exception"]
    name: str = Field(min_length=1, max_length=200)
    starts_on: date
    ends_on: date
    mode: Literal["unavailable", "custom_hours"]
    windows: list[ConfigurationTimeWindowWrite] = Field(default_factory=list, max_length=8)

    @field_validator("id", "name", mode="before")
    @classmethod
    def normalize_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_exception(self) -> Self:
        if self.ends_on < self.starts_on:
            raise ValueError("Exception ends_on must be on or after starts_on")
        if (self.ends_on - self.starts_on).days > 366:
            raise ValueError("Configuration exceptions cannot span more than 367 days")
        if self.mode == "custom_hours" and not self.windows:
            raise ValueError("Custom-hours exceptions require at least one window")
        ordered = sorted(self.windows, key=lambda item: item.start)
        if any(left.end > right.start for left, right in zip(ordered, ordered[1:])):
            raise ValueError("Custom-hours exception windows cannot overlap")
        return self


class ReplaceAvailabilityConfigurationChange(WriteSchema):
    """Atomic contract matching the approved staff-availability sheet."""

    change_type: Literal[AvailabilityChangeType.replace_configuration] = (
        AvailabilityChangeType.replace_configuration
    )
    mode: AvailabilityMode
    timezone: str = Field(min_length=1, max_length=64)
    weekly_availability: list[ConfigurationWeekdayWrite] = Field(
        min_length=7,
        max_length=7,
    )
    exceptions: list[ConfigurationExceptionWrite] = Field(default_factory=list, max_length=100)
    absence_message: ConfigurationAbsenceMessageWrite
    activation: ConfigurationActivationWrite
    redirect: ConfigurationRedirectWrite
    automatic_swap: bool

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return normalized

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        weekdays = [item.weekday for item in self.weekly_availability]
        if set(weekdays) != set(range(7)):
            raise ValueError("weekly_availability must contain every weekday exactly once")
        if not any(item.enabled for item in self.weekly_availability):
            raise ValueError("At least one weekday must be enabled")
        exception_ids = [item.id for item in self.exceptions]
        if len(exception_ids) != len(set(exception_ids)):
            raise ValueError("Configuration exception ids must be unique")
        derived_rows = sum(
            1
            + (
                ((item.ends_on - item.starts_on).days + 1) * len(item.windows)
                if item.mode == "custom_hours"
                else 0
            )
            for item in self.exceptions
        )
        if derived_rows > 1000:
            raise ValueError("Configuration expands to more than 1000 exception intervals")
        if self.automatic_swap and self.redirect.kind == "none":
            raise ValueError("automatic_swap requires a redirect target")
        return self


class _ExceptionValues(WriteSchema):
    kind: AvailabilityExceptionKind
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    ends_at: datetime
    all_day: bool = False
    absence_message: str | None = Field(default=None, max_length=4000)
    redirect: RedirectTargetWrite | None = None
    auto_reassign: bool | None = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("absence_message", mode="before")
    @classmethod
    def normalize_absence_message(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_exception_values(self) -> Self:
        _validate_window(
            self.starts_at,
            self.ends_at,
            field_prefix="exception",
            require_pair=True,
        )
        if self.kind is AvailabilityExceptionKind.available_override and (
            self.absence_message is not None
            or self.redirect is not None
            or self.auto_reassign is not None
        ):
            raise ValueError("Available overrides cannot carry absence routing")
        if self.auto_reassign is True and self.redirect is None:
            raise ValueError("auto_reassign requires a redirect target")
        return self


class CreateAvailabilityExceptionChange(_ExceptionValues):
    change_type: Literal[AvailabilityChangeType.create_exception] = (
        AvailabilityChangeType.create_exception
    )
    scope: AvailabilityExceptionScope = AvailabilityExceptionScope.staff

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if self.scope is AvailabilityExceptionScope.workspace:
            if self.kind is not AvailabilityExceptionKind.holiday:
                raise ValueError("Workspace-wide exceptions must be holidays")
            if self.redirect is not None or self.auto_reassign is not None:
                raise ValueError("Workspace-wide holidays cannot define a shared redirect")
        return self


class UpdateAvailabilityExceptionChange(_ExceptionValues):
    change_type: Literal[AvailabilityChangeType.update_exception] = (
        AvailabilityChangeType.update_exception
    )
    exception_id: UUID
    expected_exception_version: int = Field(ge=1)


class DeleteAvailabilityExceptionChange(WriteSchema):
    change_type: Literal[AvailabilityChangeType.delete_exception] = (
        AvailabilityChangeType.delete_exception
    )
    exception_id: UUID
    expected_exception_version: int = Field(ge=1)


class SetAbsenceRoutingChange(WriteSchema):
    change_type: Literal[AvailabilityChangeType.set_absence_routing] = (
        AvailabilityChangeType.set_absence_routing
    )
    absence_message: str | None = Field(default=None, max_length=4000)
    absence_message_starts_at: datetime | None = None
    absence_message_ends_at: datetime | None = None
    redirect: RedirectTargetWrite | None = None
    redirect_starts_at: datetime | None = None
    redirect_ends_at: datetime | None = None
    auto_reassign_enabled: bool = False

    @field_validator("absence_message", mode="before")
    @classmethod
    def normalize_absence_message(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_routing(self) -> Self:
        _validate_window(
            self.absence_message_starts_at,
            self.absence_message_ends_at,
            field_prefix="absence_message",
        )
        if self.absence_message is None and (
            self.absence_message_starts_at is not None or self.absence_message_ends_at is not None
        ):
            raise ValueError("An absence message window requires absence_message")
        _validate_window(
            self.redirect_starts_at,
            self.redirect_ends_at,
            field_prefix="redirect",
            require_pair=self.redirect is not None,
        )
        if self.redirect is None and (
            self.redirect_starts_at is not None
            or self.redirect_ends_at is not None
            or self.auto_reassign_enabled
        ):
            raise ValueError("Redirect timing and auto reassignment require a redirect target")
        return self


StaffAvailabilityChangePayload: TypeAlias = Annotated[
    ReplaceAvailabilityConfigurationChange
    | ReplaceRecurringScheduleChange
    | CreateAvailabilityExceptionChange
    | UpdateAvailabilityExceptionChange
    | DeleteAvailabilityExceptionChange
    | SetAbsenceRoutingChange,
    Field(discriminator="change_type"),
]


class StaffAvailabilityChangeRequestCreate(WriteSchema):
    target_user_id: UUID | None = None
    proposed_change: StaffAvailabilityChangePayload
    expected_version: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class StaffAvailabilityDecisionWrite(WriteSchema):
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note", mode="before")
    @classmethod
    def normalize_note(cls, value: object) -> object:
        return _normalize_optional_text(value)


class StaffAvailabilityProfileRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    staff_user_id: UUID
    timezone: str
    schedule_configured: bool
    availability_mode: AvailabilityMode
    availability_starts_at: datetime | None = None
    availability_ends_at: datetime | None = None
    version: int
    absence_message: str | None = None
    absence_message_starts_at: datetime | None = None
    absence_message_ends_at: datetime | None = None
    redirect_kind: AvailabilityRedirectKind | None = None
    redirect_user_id: UUID | None = None
    redirect_team_id: UUID | None = None
    redirect_starts_at: datetime | None = None
    redirect_ends_at: datetime | None = None
    auto_reassign_enabled: bool
    updated_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class RecurringAvailabilitySlotRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    profile_id: UUID
    weekday: int
    starts_at: time
    ends_at: time
    valid_from: date | None = None
    valid_until: date | None = None
    label: str | None = None
    source_request_id: UUID
    created_at: datetime
    updated_at: datetime


class StaffAvailabilityExceptionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    scope: AvailabilityExceptionScope
    staff_user_id: UUID | None = None
    kind: AvailabilityExceptionKind
    title: str
    starts_at: datetime
    ends_at: datetime
    all_day: bool
    absence_message: str | None = None
    redirect_kind: AvailabilityRedirectKind | None = None
    redirect_user_id: UUID | None = None
    redirect_team_id: UUID | None = None
    auto_reassign: bool | None = None
    version: int
    source_request_id: UUID
    updated_by_user_id: UUID | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class StaffAvailabilityStateRead(CamelCaseSchema):
    workspace_id: UUID
    staff_user_id: UUID
    profile: StaffAvailabilityProfileRead | None = None
    recurring_slots: list[RecurringAvailabilitySlotRead] = Field(default_factory=list)
    exceptions: list[StaffAvailabilityExceptionRead] = Field(default_factory=list)


class StaffAvailabilityChangeRequestRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    target_user_id: UUID | None = None
    requested_by_user_id: UUID
    resolved_by_user_id: UUID | None = None
    change_type: AvailabilityChangeType
    status: AvailabilityRequestStatus
    proposed_change: dict[str, Any]
    target_entity_id: UUID | None = None
    base_version: int
    applied_version: int | None = None
    idempotency_key: str
    reason: str
    decision_note: str | None = None
    decision_policy: dict[str, Any] | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EffectiveStaffAvailabilityRead(CamelCaseSchema):
    workspace_id: UUID
    staff_user_id: UUID
    evaluated_at: datetime
    timezone: str
    configured: bool
    is_available: bool
    reason: str
    active_exception_id: UUID | None = None
    active_exception_kind: AvailabilityExceptionKind | None = None
    absence_message: str | None = None
    redirect_kind: AvailabilityRedirectKind | None = None
    redirect_user_id: UUID | None = None
    redirect_team_id: UUID | None = None
    auto_reassign_enabled: bool = False


class ResponsibilityResolutionRead(CamelCaseSchema):
    workspace_id: UUID
    requested_user_id: UUID
    effective_user_id: UUID | None = None
    effective_team_id: UUID | None = None
    evaluated_at: datetime
    changed: bool
    reason: str
    path: list[UUID] = Field(default_factory=list)


class StaffAvailabilityAuditEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    staff_user_id: UUID | None = None
    request_id: UUID | None = None
    actor_user_id: UUID | None = None
    action: AvailabilityAuditAction
    entity_type: str
    entity_id: UUID | None = None
    summary: str
    before_data: dict[str, Any] | None = None
    after_data: dict[str, Any] | None = None
    correlation_id: str | None = None
    occurred_at: datetime


def utc_now() -> datetime:
    """Schema-adjacent clock helper used by thin endpoint defaults."""

    return datetime.now(UTC)


__all__ = [
    "ConfigurationAbsenceMessageWrite",
    "ConfigurationActivationWrite",
    "ConfigurationExceptionWrite",
    "ConfigurationRedirectWrite",
    "ConfigurationTimeWindowWrite",
    "ConfigurationWeekdayWrite",
    "CreateAvailabilityExceptionChange",
    "DeleteAvailabilityExceptionChange",
    "EffectiveStaffAvailabilityRead",
    "RecurringAvailabilitySlotRead",
    "RecurringAvailabilitySlotWrite",
    "RedirectTargetWrite",
    "ReplaceAvailabilityConfigurationChange",
    "ReplaceRecurringScheduleChange",
    "ResponsibilityResolutionRead",
    "SetAbsenceRoutingChange",
    "StaffAvailabilityAuditEventRead",
    "StaffAvailabilityChangePayload",
    "StaffAvailabilityChangeRequestCreate",
    "StaffAvailabilityChangeRequestRead",
    "StaffAvailabilityDecisionWrite",
    "StaffAvailabilityExceptionRead",
    "StaffAvailabilityProfileRead",
    "StaffAvailabilityStateRead",
    "UpdateAvailabilityExceptionChange",
    "utc_now",
]
