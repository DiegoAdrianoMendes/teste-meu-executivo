"""Contracts for preview-first contextual restoration."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema


class PlatformRestorationPreviewCreate(WriteSchema):
    source_audit_event_id: UUID | None = None
    resource_type: str | None = Field(default=None, max_length=80)
    resource_id: str | None = Field(default=None, max_length=160)
    target_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_audit_event_id is None and not (self.resource_type and self.resource_id):
            raise ValueError("Informe o evento ou o recurso a restaurar.")
        return self


class PlatformRestorationPreviewRead(CamelCaseSchema):
    id: UUID
    source_audit_event_id: UUID | None
    resource_type: str
    resource_id: str
    current_version: int
    current_hash: str
    target_version: int | None
    target_hash: str
    diff: list
    impact: dict
    warnings: list
    eligible: bool
    ineligible_reasons: list
    expires_at: datetime
    consumed_at: datetime | None


class PlatformRestorationCreate(WriteSchema):
    preview_id: UUID
    expected_resource_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)
    confirmation: str = Field(min_length=8, max_length=160)


class PlatformRestorationJobRead(CamelCaseSchema):
    id: UUID
    preview_id: UUID
    actor_user_id: UUID
    status: str
    reason: str
    correlation_id: str
    error_code: str | None
    error_detail: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
