"""HTTP contracts for the Blackbox task center."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.schemas.base import CamelCaseSchema, WriteSchema

WorkItemKind = Literal["task", "approval", "alert"]
WorkItemState = Literal[
    "open", "in_progress", "waiting_external", "waiting_customer",
    "blocked", "done", "cancelled", "stale",
]
WorkItemPriority = Literal["P0", "P1", "P2", "P3"]


class PlatformWorkItemRead(CamelCaseSchema):
    id: UUID
    source_type: str
    source_id: UUID
    source_version: str
    kind: WorkItemKind
    state: WorkItemState
    priority: WorkItemPriority
    workspace_id: UUID | None = None
    assigned_user_id: UUID | None = None
    assigned_team_id: UUID | None = None
    requester_user_id: UUID | None = None
    reviewer_user_id: UUID | None = None
    safe_title: str
    safe_summary: str
    href: str
    required_module: str
    required_action: str
    allowed_commands: list[str]
    opened_at: datetime
    due_at: datetime | None = None
    snoozed_until: datetime | None = None
    closed_at: datetime | None = None
    source_updated_at: datetime
    projected_at: datetime
    projection_version: int
    payload_safe: dict


class PlatformWorkItemPage(CamelCaseSchema):
    items: list[PlatformWorkItemRead]
    next_cursor: str | None = None
    has_more: bool
    generated_at: datetime


class PlatformWorkItemCounts(CamelCaseSchema):
    total: int
    open: int
    overdue: int
    unassigned: int
    by_priority: dict[str, int]
    by_source: dict[str, int]
    generated_at: datetime


class PlatformWorkItemAssignRequest(WriteSchema):
    model_config = ConfigDict(extra="forbid")
    assigned_user_id: UUID | None = None
    expected_projection_version: int = Field(ge=1)


class PlatformWorkItemSnoozeRequest(WriteSchema):
    model_config = ConfigDict(extra="forbid")
    snoozed_until: datetime
    reason: str = Field(min_length=8, max_length=500)
    expected_projection_version: int = Field(ge=1)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class PlatformWorkItemCommandRequest(WriteSchema):
    model_config = ConfigDict(extra="forbid")
    expected_source_version: str = Field(min_length=1, max_length=96)
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class PlatformWorkItemCommandResult(CamelCaseSchema):
    work_item: PlatformWorkItemRead
    source_type: str
    source_id: UUID
    command: str
    previous_source_version: str
    source_version: str


class PlatformWorkItemReconcileRequest(WriteSchema):
    model_config = ConfigDict(extra="forbid")
    source_types: list[str] | None = Field(default=None, max_length=9)


class PlatformWorkItemAdapterResult(CamelCaseSchema):
    source_type: str
    status: Literal["available", "partial", "unavailable", "failed"]
    projected: int = 0
    unchanged: int = 0
    reason_code: str | None = None


class PlatformWorkItemReconcileResult(CamelCaseSchema):
    adapters: list[PlatformWorkItemAdapterResult]
    projected_at: datetime
