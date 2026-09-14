"""Contratos seguros para busca global e seletores do Blackbox."""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelCaseSchema, WriteSchema


class PlatformSearchEntityType(enum.StrEnum):
    accounts = "accounts"
    users = "users"
    staff = "staff"
    tickets = "tickets"
    leads = "leads"
    conversations = "conversations"
    incidents = "incidents"
    errors = "errors"
    support_access = "support_access"
    releases = "releases"
    subscriptions = "subscriptions"
    workspace_invitations = "workspace_invitations"
    staff_invitations = "staff_invitations"


class PlatformCommandKind(enum.StrEnum):
    navigation = "navigation"
    create = "create"
    mutation = "mutation"


class PlatformCommandRisk(enum.StrEnum):
    none = "none"
    low = "low"
    high = "high"
    critical = "critical"


class PlatformCommandDefinition(CamelCaseSchema):
    id: str
    label: str
    description: str
    kind: PlatformCommandKind
    scope: str
    module: str
    action: str
    href: str | None = None
    entity_types: list[PlatformSearchEntityType] = Field(default_factory=list)
    risk: PlatformCommandRisk = PlatformCommandRisk.none
    reason_required: bool = False
    confirmation_required: bool = False


class PlatformCommandCatalogResponse(CamelCaseSchema):
    commands: list[PlatformCommandDefinition] = Field(default_factory=list)


class PlatformSearchRequest(WriteSchema):
    query: str = Field(min_length=2, max_length=120)
    types: list[PlatformSearchEntityType] | None = None
    limit_per_type: int = Field(default=8, ge=1, le=20)
    include_inactive: bool = False

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("A busca exige ao menos dois caracteres úteis.")
        return normalized

    @field_validator("types")
    @classmethod
    def deduplicate_types(
        cls,
        value: list[PlatformSearchEntityType] | None,
    ) -> list[PlatformSearchEntityType] | None:
        if value is None:
            return None
        return list(dict.fromkeys(value))


class PlatformSearchResult(CamelCaseSchema):
    id: UUID
    entity_type: PlatformSearchEntityType
    label: str
    secondary_label: str | None = None
    public_identifier: str | None = None
    workspace_id: UUID | None = None
    workspace_label: str | None = None
    status: str | None = None
    masked_email: str | None = None
    badges: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    allowed_commands: list[str] = Field(default_factory=list)
    href: str


class PlatformSearchGroup(CamelCaseSchema):
    entity_type: PlatformSearchEntityType
    state: str = "ready"
    items: list[PlatformSearchResult] = Field(default_factory=list)
    error_code: str | None = None


class PlatformSearchResponse(CamelCaseSchema):
    groups: list[PlatformSearchGroup] = Field(default_factory=list)


class PlatformLookupQueryRequest(WriteSchema):
    entity_type: PlatformSearchEntityType
    purpose: str = Field(min_length=1, max_length=80)
    query: str = Field(default="", max_length=120)
    workspace_id: UUID | None = None
    status: str | None = Field(default=None, max_length=40)
    cursor: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def normalize_lookup_query(cls, value: str) -> str:
        return " ".join(value.split())


class PlatformLookupResolveRequest(WriteSchema):
    entity_type: PlatformSearchEntityType
    purpose: str = Field(min_length=1, max_length=80)
    ids: list[UUID] = Field(min_length=1, max_length=100)
    workspace_id: UUID | None = None

    @field_validator("ids")
    @classmethod
    def deduplicate_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class PlatformLookupResponse(CamelCaseSchema):
    entity_type: PlatformSearchEntityType
    state: str = "ready"
    items: list[PlatformSearchResult] = Field(default_factory=list)
    next_cursor: str | None = None
    error_code: str | None = None
