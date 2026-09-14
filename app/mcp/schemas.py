"""Strict tool inputs for the KLM legal CRM MCP surface."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.approval import ApprovalPriority
from app.models.matter import ClientClassification, Priority, Urgency
from app.models.task import TaskPriority


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PaginationInput(ToolInput):
    query: str = Field(default="", max_length=160)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)


class ListClientsInput(PaginationInput):
    pass


class GetClientInput(ToolInput):
    client_id: UUID


class ListMattersInput(PaginationInput):
    client_id: UUID | None = None
    column_id: str | None = Field(default=None, max_length=80)
    classification: ClientClassification | None = None


class GetMatterInput(ToolInput):
    matter_id: UUID


class ConfirmedMutationInput(ToolInput):
    confirm: Literal[True]
    idempotency_key: str = Field(
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$",
    )


class QualifyMatterInput(ConfirmedMutationInput):
    matter_id: UUID
    lead_score: int | None = Field(default=None, ge=0, le=100)
    classification: ClientClassification | None = None
    priority: Priority | None = None
    urgency: Urgency | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    next_action: str | None = Field(default=None, min_length=1, max_length=1000)
    qualification_summary: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tag in value:
            tag = raw_tag.strip()
            if len(tag) < 2 or len(tag) > 40:
                raise ValueError("Each tag must contain between 2 and 40 characters")
            key = tag.casefold()
            if key not in seen:
                normalized.append(tag)
                seen.add(key)
        return normalized

    @model_validator(mode="after")
    def require_change(self) -> QualifyMatterInput:
        writable = (
            self.lead_score,
            self.classification,
            self.priority,
            self.urgency,
            self.tags,
            self.next_action,
            self.qualification_summary,
        )
        if all(value is None for value in writable):
            raise ValueError("At least one qualification field is required")
        return self


class CreateCaseNoteInput(ConfirmedMutationInput):
    matter_id: UUID
    note: str = Field(min_length=1, max_length=4000)
    visibility: Literal["internal"] = "internal"


class CreateTaskInput(ConfirmedMutationInput):
    matter_id: UUID
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    assigned_to: UUID | None = None
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    priority: TaskPriority | None = None


class RequestHandoffInput(ConfirmedMutationInput):
    matter_id: UUID
    reason: str = Field(min_length=3, max_length=4000)
    priority: ApprovalPriority = ApprovalPriority.alta


class RequestApprovalInput(ConfirmedMutationInput):
    matter_id: UUID
    approval_type: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    title: str = Field(min_length=3, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    priority: ApprovalPriority = ApprovalPriority.media
    document_version_url: str | None = Field(default=None, max_length=2000)

    @field_validator("document_version_url")
    @classmethod
    def validate_document_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme == "https" and parsed.netloc:
            return value
        if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
            return value
        raise ValueError("document_version_url must use HTTPS or a loopback HTTP URL")
