"""Contratos públicos da trilha canônica de auditoria."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class PlatformAuditEventCreate(WriteSchema):
    actor_id: UUID | None = None
    actor_role_snapshot: str
    is_platform_root: bool = False
    subject_user_id: UUID | None = None
    workspace_id: UUID | None = None
    workspace_label_snapshot: str | None = None
    administrative_session_id: UUID | None = None
    session_type: Literal["normal", "root_direct", "support_approved", "system"] = "normal"
    module: str
    action: str
    resource_type: str
    resource_id: str | None = None
    resource_label_snapshot: str | None = None
    result: Literal["success", "partial", "denied", "failed", "compensated"]
    before: Any | None = None
    after: Any | None = None
    reason: str = Field(min_length=1, max_length=500)
    reason_source: Literal["user", "system"] = "system"
    correlation_id: str
    operation_id: str
    parent_event_id: UUID | None = None
    bulk_job_id: UUID | None = None
    restoration_of_event_id: UUID | None = None
    resource_version_before: int | None = None
    resource_version_after: int | None = None
    error_code: str | None = None
    route: str
    method: str


class PlatformAuditFieldDiff(CamelCaseSchema):
    path: str
    label: str
    change_type: Literal["added", "removed", "changed"]
    before: Any | None
    after: Any | None
    sensitive: bool = False


class PlatformAuditEventRead(CamelCaseSchema):
    id: UUID
    schema_version: int
    actor_id: UUID | None
    actor_role_snapshot: str
    is_platform_root: bool
    subject_user_id: UUID | None
    workspace_id: UUID | None
    workspace_label_snapshot: str | None
    administrative_session_id: UUID | None
    session_type: str
    module: str
    action: str
    resource_type: str
    resource_id: str | None
    resource_label_snapshot: str | None
    result: str
    before: Any | None
    after: Any | None
    changed_paths: list[str]
    field_diffs: list[PlatformAuditFieldDiff] = Field(default_factory=list)
    restorable: bool = False
    reason: str
    reason_source: str
    correlation_id: str
    operation_id: str
    parent_event_id: UUID | None
    bulk_job_id: UUID | None
    restoration_of_event_id: UUID | None
    resource_version_before: int | None
    resource_version_after: int | None
    error_code: str | None
    route: str
    method: str
    occurred_at: datetime


class PlatformAuditPage(CamelCaseSchema):
    items: list[PlatformAuditEventRead]
    next_cursor: str | None = None
    has_more: bool
