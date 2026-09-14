"""API contracts for safe external task sharing."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelCaseSchema, WriteSchema


class TaskExternalSharePreviewRequest(WriteSchema):
    expires_at: datetime
    safe_context: str | None = Field(default=None, max_length=1000)

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("A validade deve incluir timezone explícito.")
        now = datetime.now(UTC)
        normalized = value.astimezone(UTC)
        if normalized <= now:
            raise ValueError("A validade deve estar no futuro.")
        if normalized > now + timedelta(days=30):
            raise ValueError("A validade máxima é de 30 dias.")
        return normalized


class TaskExternalShareCreateRequest(TaskExternalSharePreviewRequest):
    preview_fingerprint: str = Field(min_length=64, max_length=64)


class TaskExternalSharePreview(CamelCaseSchema):
    title: str
    status: str
    due_at: datetime | None
    safe_context: str | None
    expires_at: datetime
    preview_fingerprint: str
    excluded_content: list[str]


class TaskExternalShareIssued(CamelCaseSchema):
    id: UUID
    url: str
    expires_at: datetime
    created_at: datetime


class TaskExternalShareRead(CamelCaseSchema):
    id: UUID
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime


class PublicTaskExternalShare(CamelCaseSchema):
    title: str
    status: str
    due_at: datetime | None
    safe_context: str | None
    expires_at: datetime
