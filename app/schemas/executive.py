"""Public contracts for the consultative C-level council.

These schemas deliberately contain no execution/tool contract.  In v1 the
executives can analyse account evidence and propose human-reviewed next steps,
but cannot mutate workspace state or call external integrations.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema


class ExecutiveKey(str, Enum):
    ceo = "ceo"
    coo = "coo"
    cfo = "cfo"
    cro = "cro"
    cpo = "cpo"
    cto = "cto"
    cio = "cio"
    chro = "chro"


class ExecutiveThreadMode(str, Enum):
    direct = "direct"
    roundtable = "roundtable"


class ExecutiveThreadVisibility(str, Enum):
    private = "private"
    shared = "shared"


class ExecutiveThreadStatus(str, Enum):
    active = "active"
    archived = "archived"
    deleted = "deleted"


class ExecutiveRoundStatus(str, Enum):
    received = "received"
    context_planning = "context_planning"
    retrieving_context = "retrieving_context"
    executive_selection = "executive_selection"
    executives_responding = "executives_responding"
    ceo_synthesizing = "ceo_synthesizing"
    completed = "completed"
    partial_failure = "partial_failure"
    failed = "failed"
    cancelled = "cancelled"


class ExecutiveAuthorType(str, Enum):
    human = "human"
    executive = "executive"
    system = "system"


class ExecutiveMessageKind(str, Enum):
    question = "question"
    response = "response"
    synthesis = "synthesis"
    notice = "notice"
    error = "error"


class ExecutiveMessageStatus(str, Enum):
    pending = "pending"
    streaming = "streaming"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ExecutiveReportStatus(str, Enum):
    scheduled = "scheduled"
    collecting_data = "collecting_data"
    generating = "generating"
    published = "published"
    published_with_gaps = "published_with_gaps"
    partial_failure = "partial_failure"
    failed = "failed"
    superseded = "superseded"


class ExecutiveGapType(str, Enum):
    data = "data"
    configuration = "configuration"
    operational = "operational"
    evidence = "evidence"


class ExecutiveProfileRead(CamelCaseSchema):
    id: UUID | None = None
    workspace_id: UUID | None = None
    key: ExecutiveKey
    display_name: str
    mention: str
    role: str
    description: str
    domain_manifest: dict[str, Any]
    allowed_read_capabilities: tuple[str, ...]
    forbidden_capabilities: tuple[str, ...]
    prompt_version: str
    status: str = "active"
    consultative_only: bool = True


class ExecutiveContextReference(WriteSchema):
    scope_type: str = Field(min_length=1, max_length=40)
    scope_id: str | None = Field(default=None, max_length=160)
    label: str | None = Field(default=None, max_length=255)


class ExecutiveThreadCreate(WriteSchema):
    title: str | None = Field(default=None, max_length=160)
    mode: ExecutiveThreadMode = ExecutiveThreadMode.direct
    selected_executives: list[ExecutiveKey] = Field(default_factory=lambda: [ExecutiveKey.ceo])
    visibility: ExecutiveThreadVisibility = ExecutiveThreadVisibility.private

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class ExecutiveThreadUpdate(WriteSchema):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: ExecutiveThreadStatus | None = None
    visibility: ExecutiveThreadVisibility | None = None
    selected_executives: list[ExecutiveKey] | None = None

    @field_validator("title")
    @classmethod
    def clean_required_title(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ExecutiveThreadRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    title: str | None = None
    visibility: ExecutiveThreadVisibility
    status: ExecutiveThreadStatus
    mode: ExecutiveThreadMode
    selected_executives: tuple[ExecutiveKey, ...]
    ceo_always_present: bool = True
    last_message_at: datetime | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


class ExecutiveThreadShareCreate(WriteSchema):
    user_id: UUID
    permission: str = Field(default="view_and_reply", pattern="^view_and_reply$")


class ExecutiveThreadShareRead(CamelCaseSchema):
    thread_id: UUID
    user_id: UUID
    granted_by_user_id: UUID = Field(
        validation_alias=AliasChoices("granted_by_user_id", "granted_by", "grantedBy")
    )
    granted_at: datetime
    revoked_at: datetime | None = None
    permission: str = "view_and_reply"


class ExecutiveMessageCreate(WriteSchema):
    body: str = Field(min_length=1, max_length=30_000)
    requested_executives: list[ExecutiveKey] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "requested_executives",
            "requestedExecutives",
            "mentioned_executives",
            "mentionedExecutives",
        ),
    )
    prevent_additional_invites: bool = Field(
        default=False,
        validation_alias=AliasChoices("prevent_additional_invites", "preventAdditionalInvites"),
    )
    context: dict[str, Any] = Field(default_factory=dict)
    context_references: list[ExecutiveContextReference] = Field(default_factory=list, max_length=20)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)

    @field_validator("body")
    @classmethod
    def reject_blank_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A mensagem não pode ser vazia")
        return value


class ExecutiveMessageRead(CamelCaseSchema):
    id: UUID
    thread_id: UUID
    round_id: UUID | None = None
    author_type: ExecutiveAuthorType
    author_user_id: UUID | None = None
    executive_key: ExecutiveKey | None = None
    body: str
    message_kind: ExecutiveMessageKind
    sequence: int = Field(ge=0)
    status: ExecutiveMessageStatus
    source_receipt_id: UUID | None = None
    model_run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_author_identity(self) -> ExecutiveMessageRead:
        if self.author_type == ExecutiveAuthorType.executive and self.executive_key is None:
            raise ValueError("Mensagem executiva exige executive_key")
        if self.author_type == ExecutiveAuthorType.human and self.author_user_id is None:
            raise ValueError("Mensagem humana exige author_user_id")
        return self


class ExecutiveRoundRead(CamelCaseSchema):
    id: UUID
    thread_id: UUID
    user_message_id: UUID | None = None
    status: ExecutiveRoundStatus
    requested_executives: tuple[ExecutiveKey, ...]
    selected_by_ceo: tuple[ExecutiveKey, ...]
    selection_reasons: dict[str, str] = Field(default_factory=dict)
    context_scope: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    error_summary: str | None = None


class ExecutiveGapRead(CamelCaseSchema):
    type: ExecutiveGapType
    severity: str
    description: str
    evidence: list[str] = Field(default_factory=list)
    impact: str | None = None
    suggested_owner: str | None = None
    related_module: str | None = None
    recommendation: str | None = None
    detected_at: datetime
    status: str
    confidence: float = Field(ge=0, le=1)


class ExecutiveSourceRead(CamelCaseSchema):
    source_type: str
    source_ref: str
    title: str
    excerpt: str | None = None
    occurred_at: datetime | None = None
    indexed_at: datetime | None = None
    score: float | None = None
    stale: bool = False


class ExecutiveSourceReceiptRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID | None = None
    round_id: UUID | None = None
    executive_key: ExecutiveKey | None = None
    pipeline_version: str
    query_hash: str
    period: dict[str, Any] = Field(default_factory=dict)
    structured_queries: list[dict[str, Any]] = Field(default_factory=list)
    selected_sources: list[ExecutiveSourceRead] = Field(default_factory=list)
    rejected_sources: list[dict[str, Any]] = Field(default_factory=list)
    insufficiency_reasons: list[str] = Field(default_factory=list)
    generated_at: datetime


class ExecutiveReportRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    period_start: date
    period_end: date
    status: ExecutiveReportStatus
    title: str
    body: str | None = None
    executive_participants: tuple[ExecutiveKey, ...]
    source_receipt_id: UUID | None = None
    generation_key: str
    published_at: datetime | None = None
    superseded_by_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("superseded_by_id", "superseded_by", "supersededBy"),
    )
    created_at: datetime
    updated_at: datetime


class ExecutiveProposedActionRead(CamelCaseSchema):
    """Display-only proposal. There is intentionally no executable payload."""

    action_type: str
    description: str
    module: str | None = None
    impact: str | None = None
    required_data: tuple[str, ...] = ()
    risk: str
    approval_required: bool = True
    executable: bool = False
