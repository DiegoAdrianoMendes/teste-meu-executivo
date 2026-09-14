"""Typed API contracts for persisted Blackbox selection and bulk jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema

PlatformBulkResourceType = Literal[
    "workspace",
    "customer_user",
    "staff",
    "lead",
    "ticket",
    "subscription",
    "workspace_invitation",
    "staff_invitation",
]


class PlatformSortWrite(WriteSchema):
    field: Literal["createdAt", "name", "id"] = "createdAt"
    direction: Literal["asc", "desc"] = "desc"


class PlatformSelectionCreate(WriteSchema):
    resource_type: PlatformBulkResourceType = "workspace"
    mode: Literal["explicit", "filter"]
    ids: list[UUID] = Field(default_factory=list, max_length=50_000)
    filters: dict = Field(default_factory=dict)
    sort: list[PlatformSortWrite] = Field(default_factory=list, max_length=3)
    excluded_ids: list[UUID] = Field(default_factory=list, max_length=50_000)

    @model_validator(mode="after")
    def validate_mode_payload(self):
        if self.mode == "explicit" and not self.ids:
            raise ValueError("Explicit selections require at least one id.")
        if self.mode == "filter" and self.ids:
            raise ValueError("Filter selections cannot include explicit ids.")
        return self


class PlatformSelectionRead(CamelCaseSchema):
    id: UUID
    actor_user_id: UUID
    resource_type: str
    mode: str
    filters: dict
    sort: list
    excluded_ids: list
    filter_hash: str
    target_count: int
    status: str
    created_at: datetime
    expires_at: datetime


class PlatformBulkPreviewCreate(WriteSchema):
    selection_set_id: UUID
    action: str = Field(min_length=2, max_length=48)
    parameters: dict = Field(default_factory=dict)


class PlatformBulkPreviewRead(CamelCaseSchema):
    id: UUID
    selection_set_id: UUID
    resource_type: PlatformBulkResourceType
    action: str
    status: str
    risk: str
    target_count: int
    eligible_count: int
    no_op_count: int
    blocked_count: int
    warnings: list
    blockers: list
    impact_summary: dict
    sample: list
    confirmation_phrase: str | None
    created_at: datetime
    expires_at: datetime


class PlatformBulkJobCreate(WriteSchema):
    preview_id: UUID
    reason: str = Field(min_length=8, max_length=500)
    confirmation_phrase: str | None = Field(default=None, max_length=160)


class PlatformBulkRestorationCreate(WriteSchema):
    preview_id: UUID
    reason: str = Field(min_length=8, max_length=500)
    confirmation_phrase: str = Field(min_length=8, max_length=160)


class PlatformBulkJobItemRead(CamelCaseSchema):
    id: UUID
    resource_id: UUID
    ordinal: int
    status: str
    attempts: int
    before: dict | None
    after: dict | None
    error_code: str | None
    error_detail: str | None
    started_at: datetime | None
    processed_at: datetime | None


class PlatformBulkJobRead(CamelCaseSchema):
    id: UUID
    preview_id: UUID
    selection_set_id: UUID
    resource_type: PlatformBulkResourceType
    action: str
    parameters: dict
    reason: str
    status: str
    risk: str
    requested_count: int
    eligible_count: int
    processed_count: int
    succeeded_count: int
    failed_count: int
    conflicted_count: int
    skipped_count: int
    cancelled_count: int
    progress_percent: float
    correlation_id: str
    operation_id: str
    cancel_requested_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None
    created_at: datetime
    items: list[PlatformBulkJobItemRead] = Field(default_factory=list)


class PlatformBulkJobPage(CamelCaseSchema):
    items: list[PlatformBulkJobRead]
    total: int
    limit: int
    offset: int
