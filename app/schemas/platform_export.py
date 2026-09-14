"""Contracts for private asynchronous Blackbox exports."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema

PlatformExportDataset = Literal[
    "accounts", "users", "kpis", "billing", "tickets", "leads", "audit", "bulk_result"
]
PlatformExportFormat = Literal["csv", "xlsx", "ndjson"]


class PlatformExportCreate(WriteSchema):
    dataset: PlatformExportDataset
    format: PlatformExportFormat = "csv"
    filters: dict = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list, max_length=100)
    selection_set_id: UUID | None = None


class PlatformExportRead(CamelCaseSchema):
    id: UUID
    actor_user_id: UUID
    dataset: str
    format: str
    filters: dict
    columns: list[str]
    selection_set_id: UUID | None
    status: str
    processed_rows: int
    row_count: int | None
    byte_size: int | None
    checksum_sha256: str | None
    error_code: str | None
    error_detail: str | None
    correlation_id: str
    cancel_requested_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PlatformExportPage(CamelCaseSchema):
    items: list[PlatformExportRead]
    total: int
    limit: int
    offset: int
