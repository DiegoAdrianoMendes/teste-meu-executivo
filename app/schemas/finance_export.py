from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class FinanceExportCreate(WriteSchema):
    format: str = Field(pattern="^(pdf|xlsx|docx)$")
    drill_context: dict = Field(default_factory=dict)


class FinanceExportRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    actor_user_id: UUID
    format: str
    status: str
    drill_context: dict
    expires_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error_detail: str | None = None
    checksum_sha256: str | None = None
    progress: int = 0
    row_count: int | None = None
    snapshot_at: datetime | None = None
