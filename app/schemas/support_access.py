"""HTTP contracts for local support break-glass access."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_serializer, field_validator

from app.models.support_access import (
    SUPPORT_ACCESS_MAX_DURATION_MINUTES,
    SUPPORT_ACCESS_MIN_DURATION_MINUTES,
    SupportAccessScope,
    SupportAccessState,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


def _normalize_scopes(scopes: list[SupportAccessScope]) -> list[SupportAccessScope]:
    return sorted(set(scopes), key=lambda scope: scope.value)


def _serialize_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat()


class SupportAccessRequestCreate(WriteSchema):
    workspace_id: UUID
    reason: str = Field(min_length=15, max_length=500)
    ticket_reference: str = Field(min_length=3, max_length=120)
    requested_scopes: list[SupportAccessScope] = Field(
        min_length=1,
        max_length=len(SupportAccessScope),
    )
    duration_minutes: int = Field(
        ge=SUPPORT_ACCESS_MIN_DURATION_MINUTES,
        le=SUPPORT_ACCESS_MAX_DURATION_MINUTES,
    )

    @field_validator("reason", "ticket_reference")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("O campo não pode ser vazio.")
        return normalized

    @field_validator("requested_scopes")
    @classmethod
    def normalize_requested_scopes(
        cls,
        value: list[SupportAccessScope],
    ) -> list[SupportAccessScope]:
        return _normalize_scopes(value)


class RootDirectAccessRequest(WriteSchema):
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_root_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 8:
            raise ValueError(
                "O motivo do acesso direto deve ter ao menos 8 caracteres úteis."
            )
        return normalized


class SupportAccessApproval(WriteSchema):
    approved_scopes: list[SupportAccessScope] = Field(
        min_length=1,
        max_length=len(SupportAccessScope),
    )
    duration_minutes: int = Field(ge=1, le=SUPPORT_ACCESS_MAX_DURATION_MINUTES)

    @field_validator("approved_scopes")
    @classmethod
    def normalize_approved_scopes(
        cls,
        value: list[SupportAccessScope],
    ) -> list[SupportAccessScope]:
        return _normalize_scopes(value)


class SupportAccessDecision(WriteSchema):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("O motivo não pode ser vazio.")
        return normalized


class SupportAccessEventRead(CamelCaseSchema):
    id: UUID
    grant_id: UUID
    workspace_id: UUID
    actor_user_id: UUID | None
    event_type: str
    from_state: str | None
    to_state: str | None
    details: dict
    occurred_at: datetime

    @field_serializer("occurred_at")
    def serialize_occurred_at(self, value: datetime) -> str:
        return _serialize_utc(value) or ""


class SupportAccessEventPageRead(CamelCaseSchema):
    items: list[SupportAccessEventRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class SupportAccessGrantRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    requester_user_id: UUID
    reason: str
    ticket_reference: str
    requested_scopes: list[SupportAccessScope]
    requested_duration_minutes: int
    state: SupportAccessState
    requested_at: datetime
    approver_user_id: UUID | None
    approved_scopes: list[SupportAccessScope] | None
    approved_duration_minutes: int | None
    approved_at: datetime | None
    activated_at: datetime | None
    expires_at: datetime | None
    expired_at: datetime | None
    rejected_at: datetime | None
    rejection_reason: str | None
    revoked_at: datetime | None
    revocation_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "requested_at",
        "approved_at",
        "activated_at",
        "expires_at",
        "expired_at",
        "rejected_at",
        "revoked_at",
        "created_at",
        "updated_at",
    )
    def serialize_timestamps(self, value: datetime | None) -> str | None:
        return _serialize_utc(value)


class SupportAccessTokenRead(CamelCaseSchema):
    support_access_token: str
    token_type: Literal["support_access"] = "support_access"
    expires_in: int
    grant: SupportAccessGrantRead
