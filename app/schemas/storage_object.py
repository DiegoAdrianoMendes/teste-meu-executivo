"""API contracts for tenant-safe local object storage."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.models.storage_object import StorageAVStatus, StorageObjectState
from app.schemas.base import CamelCaseSchema, WriteSchema

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class StorageUploadRequest(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=127)
    byte_size: int | None = Field(default=None, ge=0)
    checksum_sha256: str | None = None
    retention_until: datetime | None = None
    expires_in: int = Field(default=300, ge=30, le=900)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        cleaned = value.strip()
        if (
            cleaned in {".", ".."}
            or "/" in cleaned
            or "\\" in cleaned
            or any(ord(character) < 32 for character in cleaned)
        ):
            raise ValueError("filename must be a plain file name without path components")
        return cleaned

    @field_validator("content_type")
    @classmethod
    def normalize_content_type(cls, value: str) -> str:
        normalized = value.split(";", 1)[0].strip().lower()
        if (
            not normalized
            or "/" not in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("content_type must be a valid MIME type")
        return normalized

    @field_validator("checksum_sha256")
    @classmethod
    def normalize_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("checksum_sha256 must contain 64 hexadecimal characters")
        return normalized


class StorageRetentionUpdate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    retention_until: datetime


class StorageObjectRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    object_id: UUID
    version: int
    original_filename: str
    declared_content_type: str
    detected_content_type: str | None = None
    expected_byte_size: int | None = None
    byte_size: int | None = None
    expected_sha256: str | None = None
    checksum_sha256: str | None = None
    state: StorageObjectState
    av_status: StorageAVStatus
    quarantine_reason: str | None = None
    retention_until: datetime | None = None
    uploaded_at: datetime | None = None
    scanned_at: datetime | None = None
    deleted_at: datetime | None = None
    restored_at: datetime | None = None
    created_by_user_id: UUID | None = None
    deleted_by_user_id: UUID | None = None
    restored_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class StorageUploadTicketRead(CamelCaseSchema):
    object: StorageObjectRead
    upload_url: str
    expires_at: datetime


class StorageDownloadTicketRead(CamelCaseSchema):
    object_id: UUID
    version: int
    download_url: str
    expires_at: datetime


class StorageExportRead(CamelCaseSchema):
    object_id: UUID
    version_count: int
    checksum_verified: bool = True
