"""Contratos da API de continuidade de funcionários."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.staff_reassignment import (
    StaffReassignmentMode,
    StaffReassignmentStatus,
)
from app.schemas.base import CamelCaseSchema, WriteSchema

WorkspaceReassignmentScope = Literal[
    "matters",
    "tasks",
    "meetings",
    "booking_links",
    "omnichannel_threads",
    "whatsapp_contacts",
    "social_conversations",
]
PlatformReassignmentScope = Literal["support_tickets", "growth_prospects"]


class StaffReassignmentPreviewRequest(WriteSchema):
    source_user_id: UUID
    target_user_id: UUID
    scopes: list[str] = Field(default_factory=list, min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_users(self) -> Self:
        if self.source_user_id == self.target_user_id:
            raise ValueError("A origem e o destino devem ser usuários diferentes.")
        self.scopes = list(dict.fromkeys(self.scopes))
        return self


class StaffReassignmentCreate(StaffReassignmentPreviewRequest):
    mode: StaffReassignmentMode
    reason: str = Field(min_length=8, max_length=500)
    starts_at: datetime
    ends_at: datetime | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("As datas devem incluir fuso horário.")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.mode is StaffReassignmentMode.temporary:
            if self.ends_at is None:
                raise ValueError("Uma transferência temporária exige data de término.")
            if self.ends_at <= self.starts_at:
                raise ValueError("A data final deve ser posterior à data inicial.")
        elif self.ends_at is not None:
            raise ValueError("Uma transferência definitiva não possui data de término.")
        return self


class StaffReassignmentPreviewRead(CamelCaseSchema):
    source_user_id: UUID
    source_name: str
    target_user_id: UUID
    target_name: str
    counts: dict[str, int]
    total: int


class StaffReassignmentAuditEventRead(CamelCaseSchema):
    id: UUID
    actor_user_id: UUID | None = None
    action: str
    details: dict
    occurred_at: datetime


class StaffReassignmentPlanRead(CamelCaseSchema):
    id: UUID
    surface: Literal["workspace", "platform"]
    workspace_id: UUID | None = None
    source_user_id: UUID
    source_name: str
    target_user_id: UUID
    target_name: str
    mode: StaffReassignmentMode
    status: StaffReassignmentStatus
    reason: str
    tag_label: str
    scopes: list[str]
    assignment_summary: dict[str, int]
    conflict_count: int
    starts_at: datetime
    ends_at: datetime | None = None
    applied_at: datetime
    restored_at: datetime | None = None
    restore_reason: str | None = None
    requested_by_user_id: UUID
    approved_by_user_id: UUID
    version: int
    created_at: datetime
    updated_at: datetime
    audit_events: list[StaffReassignmentAuditEventRead] = Field(default_factory=list)


class StaffReassignmentPlanPageRead(CamelCaseSchema):
    items: list[StaffReassignmentPlanRead]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool


class StaffReassignmentRestore(WriteSchema):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)


__all__ = [
    "PlatformReassignmentScope",
    "StaffReassignmentAuditEventRead",
    "StaffReassignmentCreate",
    "StaffReassignmentPlanPageRead",
    "StaffReassignmentPlanRead",
    "StaffReassignmentPreviewRead",
    "StaffReassignmentPreviewRequest",
    "StaffReassignmentRestore",
    "WorkspaceReassignmentScope",
]
