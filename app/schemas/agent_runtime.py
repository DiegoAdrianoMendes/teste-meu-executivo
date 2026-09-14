"""
Schemas do runtime de agentes.
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_serializer, field_validator

from app.core.ai_cost_privacy import strip_internal_ai_cost_fields
from app.models.agent_run import AgentRunStatus
from app.models.human_override import HumanOverrideDecision
from app.schemas.base import CamelCaseSchema, WriteSchema


class HumanOverrideCreate(WriteSchema):
    decision: HumanOverrideDecision
    notes: str | None = None
    corrected_payload: dict[str, Any] | None = None


class HumanOverrideRead(CamelCaseSchema):
    id: UUID
    matter_id: UUID
    agent_run_id: UUID
    decided_by_user_id: UUID | None = None
    decision: HumanOverrideDecision
    notes: str | None = None
    before_payload: dict[str, Any] | None = None
    after_payload: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("matter_id", "agent_run_id", "decided_by_user_id")
    def serialize_uuid(self, value: UUID | None) -> str | None:
        return str(value) if value else None

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        return value.isoformat()


class AgentRunRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID | None = None
    context_type: str | None = None
    context_id: UUID | None = None
    triggered_by_user_id: UUID | None = None
    agent_name: str
    trigger_source: str
    thread_id: str | None = None
    dedupe_key: str | None = None
    celery_task_id: str | None = None
    status: AgentRunStatus
    input_payload: dict[str, Any] | None = None
    output_payload: dict[str, Any] | None = None
    error_message: str | None = None
    attempt_count: int
    engine_name: str | None = Field(
        default=None,
        validation_alias="model_used",
        serialization_alias="modelUsed",
    )
    ai_provider: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    overrides: list[HumanOverrideRead] = Field(default_factory=list)

    @field_validator("input_payload", "output_payload", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any) -> Any:
        return strip_internal_ai_cost_fields(value)

    @field_serializer(
        "id",
        "workspace_id",
        "matter_id",
        "context_id",
        "triggered_by_user_id",
    )
    def serialize_uuid(self, value: UUID | None) -> str | None:
        return str(value) if value else None

    @field_serializer("started_at", "completed_at", "created_at", "updated_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class AgentRunRetryResponse(CamelCaseSchema):
    matter_id: UUID
    agent_run_id: UUID
    status: str
    message: str

    @field_serializer("matter_id", "agent_run_id")
    def serialize_uuid(self, value: UUID) -> str:
        return str(value)
