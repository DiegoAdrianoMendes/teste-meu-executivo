"""Contratos do estúdio assistido da Central Blackbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema

StudioKind = Literal["agent", "agent_package", "flow", "experience"]


class StudioAssistRequest(WriteSchema):
    kind: StudioKind
    prompt: str = Field(min_length=8, max_length=8_000)
    title: str | None = Field(default=None, max_length=160)
    current_payload: dict[str, Any] = Field(default_factory=dict)
    attachment_names: list[str] = Field(default_factory=list, max_length=20)


class StudioProposalRead(CamelCaseSchema):
    kind: StudioKind
    title: str
    key: str
    payload: dict[str, Any]
    summary: str
    suggestions: list[str]
    warnings: list[str]
    approval_required: bool = True
    assistant_mode: str = "local_structured"


class StudioDraftCreate(WriteSchema):
    kind: StudioKind
    title: str = Field(min_length=2, max_length=160)
    key: str = Field(min_length=2, max_length=80)
    payload: dict[str, Any]
    reason: str | None = Field(default=None, max_length=240)


class StudioDraftUpdate(WriteSchema):
    title: str | None = Field(default=None, min_length=2, max_length=160)
    payload: dict[str, Any]
    reason: str | None = Field(default=None, max_length=240)
    expected_etag: str | None = Field(default=None, max_length=96)


class StudioDraftRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    kind: StudioKind
    key: str
    title: str
    status: str
    payload: dict[str, Any]
    current_version: int
    etag: str
    updated_at: datetime
    publication: dict[str, Any] | None = None


class StudioDraftPageRead(CamelCaseSchema):
    items: list[StudioDraftRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class StudioPublishRequest(WriteSchema):
    expected_etag: str | None = Field(default=None, max_length=96)
    reason: str | None = Field(default=None, max_length=240)


class StudioRestoreRequest(WriteSchema):
    target_version: int = Field(ge=1)
    expected_etag: str | None = Field(default=None, max_length=96)
    reason: str = Field(min_length=3, max_length=240)


class StudioVersionRead(CamelCaseSchema):
    version: int
    payload: dict[str, Any]
    reason: str | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime


class StudioVersionPageRead(CamelCaseSchema):
    items: list[StudioVersionRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class StudioPublishResult(CamelCaseSchema):
    draft: StudioDraftRead
    target_type: str
    target_id: str
    target_version: int
    already_published: bool = False


class StudioWorkspaceOptionRead(CamelCaseSchema):
    id: UUID
    name: str
    slug: str
    can_read: bool
    can_write: bool
    expires_at: datetime | None = None
    read_expires_at: datetime | None = None
    write_expires_at: datetime | None = None
