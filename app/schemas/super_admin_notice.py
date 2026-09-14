"""Contratos da Central de Avisos do Super Admin."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.super_admin_notice import (
    SuperAdminNoticeAudienceKind,
    SuperAdminNoticeAuditAction,
    SuperAdminNoticeChannel,
    SuperAdminNoticeReceiptState,
    SuperAdminNoticeStatus,
)
from app.models.user import UserRole
from app.models.workspace_membership import WorkspaceRole
from app.schemas.base import CamelCaseSchema, WriteSchema


class SuperAdminNoticeRuleField(enum.StrEnum):
    user_role = "user_role"
    workspace_role = "workspace_role"


class SuperAdminNoticeSimpleRule(WriteSchema):
    field: SuperAdminNoticeRuleField
    operator: Literal["in"] = "in"
    values: list[str] = Field(min_length=1, max_length=20)

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: list[str], info) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("A regra precisa conter ao menos um valor.")
        field = info.data.get("field")
        if field == SuperAdminNoticeRuleField.user_role:
            allowed = {item.value for item in UserRole}
        elif field == SuperAdminNoticeRuleField.workspace_role:
            allowed = {item.value for item in WorkspaceRole}
        else:
            allowed = set()
        invalid = sorted(set(normalized) - allowed)
        if invalid:
            raise ValueError(f"Valores invalidos para {field}: {', '.join(invalid)}")
        return normalized


class SuperAdminNoticeAudience(WriteSchema):
    kind: SuperAdminNoticeAudienceKind
    workspace_ids: list[UUID] = Field(default_factory=list, max_length=500)
    user_ids: list[UUID] = Field(default_factory=list, max_length=5_000)
    rule: SuperAdminNoticeSimpleRule | None = None

    @field_validator("workspace_ids", "user_ids")
    @classmethod
    def deduplicate_ids(cls, values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_shape(self) -> SuperAdminNoticeAudience:
        if self.kind == SuperAdminNoticeAudienceKind.global_:
            if self.workspace_ids or self.user_ids or self.rule is not None:
                raise ValueError("Publico global nao aceita filtros adicionais.")
        elif self.kind == SuperAdminNoticeAudienceKind.workspaces:
            if not self.workspace_ids or self.user_ids or self.rule is not None:
                raise ValueError("Publico por workspace exige somente workspaceIds.")
        elif self.kind == SuperAdminNoticeAudienceKind.users:
            if not self.user_ids or self.workspace_ids or self.rule is not None:
                raise ValueError("Publico por usuario exige somente userIds.")
        elif self.kind == SuperAdminNoticeAudienceKind.rule:
            if self.rule is None or self.workspace_ids or self.user_ids:
                raise ValueError("Publico por regra exige somente rule.")
        return self


class SuperAdminNoticeCreate(WriteSchema):
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=50_000)
    audience: SuperAdminNoticeAudience
    channels: list[SuperAdminNoticeChannel] = Field(min_length=1, max_length=2)
    requires_acknowledgement: bool = False

    @field_validator("title", "body")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("O texto nao pode ficar vazio.")
        return normalized

    @field_validator("channels")
    @classmethod
    def deduplicate_channels(
        cls,
        channels: list[SuperAdminNoticeChannel],
    ) -> list[SuperAdminNoticeChannel]:
        return list(dict.fromkeys(channels))


class SuperAdminNoticeUpdate(WriteSchema):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    body: str | None = Field(default=None, min_length=1, max_length=50_000)
    audience: SuperAdminNoticeAudience | None = None
    channels: list[SuperAdminNoticeChannel] | None = Field(
        default=None,
        min_length=1,
        max_length=2,
    )
    requires_acknowledgement: bool | None = None

    @field_validator("title", "body")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("O texto nao pode ficar vazio.")
        return normalized

    @field_validator("channels")
    @classmethod
    def deduplicate_optional_channels(
        cls,
        channels: list[SuperAdminNoticeChannel] | None,
    ) -> list[SuperAdminNoticeChannel] | None:
        return list(dict.fromkeys(channels)) if channels is not None else None

    @model_validator(mode="after")
    def require_change(self) -> SuperAdminNoticeUpdate:
        mutable = {
            "title",
            "body",
            "audience",
            "channels",
            "requires_acknowledgement",
        }
        if not (self.model_fields_set & mutable):
            raise ValueError("Informe ao menos um campo para atualizar.")
        if any(getattr(self, field) is None for field in self.model_fields_set & mutable):
            raise ValueError("Campos de atualizacao nao aceitam null.")
        return self


class SuperAdminNoticeSchedule(WriteSchema):
    scheduled_at: datetime


class SuperAdminNoticeRead(CamelCaseSchema):
    id: UUID
    title: str
    body: str
    status: SuperAdminNoticeStatus
    audience: SuperAdminNoticeAudience
    channels: list[SuperAdminNoticeChannel]
    requires_acknowledgement: bool
    email_delivery_mode: Literal["local_queue_only"] | None
    scheduled_at: datetime | None
    published_at: datetime | None
    cancelled_at: datetime | None
    created_by_user_id: UUID | None
    updated_by_user_id: UUID | None
    published_by_user_id: UUID | None
    cancelled_by_user_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


class SuperAdminNoticeListRead(CamelCaseSchema):
    items: list[SuperAdminNoticeRead]
    total: int


class SuperAdminNoticeRecipientRead(CamelCaseSchema):
    user_id: UUID
    name: str
    email_hint: str | None
    matched_by: str
    workspace_ids: list[UUID]


class SuperAdminNoticeRecipientPreviewRead(CamelCaseSchema):
    notice_id: UUID
    total_recipients: int
    email_eligible: int
    email_blocked_missing_address: int
    sample: list[SuperAdminNoticeRecipientRead]
    evaluated_at: datetime


class SuperAdminNoticeReceiptRead(CamelCaseSchema):
    id: UUID
    notice_id: UUID
    recipient_user_id: UUID
    recipient_name: str
    channel: SuperAdminNoticeChannel
    state: SuperAdminNoticeReceiptState
    delivery_mode: Literal["in_app_local", "local_queue_only"]
    external_delivery_attempted: Literal[False] = False
    recipient_address_hint: str | None
    matched_by: str
    scope_all_workspaces: bool
    workspace_ids: list[UUID]
    available_at: datetime | None
    queued_at: datetime | None
    read_at: datetime | None
    acknowledged_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SuperAdminNoticeReceiptListRead(CamelCaseSchema):
    items: list[SuperAdminNoticeReceiptRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class SuperAdminNoticePublicationRead(CamelCaseSchema):
    notice: SuperAdminNoticeRead
    recipient_count: int
    receipt_count: int
    receipts_by_state: dict[str, int]
    email_delivery_mode: Literal["local_queue_only"] | None


class SuperAdminNoticeDispatchRead(CamelCaseSchema):
    processed_count: int
    publications: list[SuperAdminNoticePublicationRead]


class SuperAdminNoticeInboxItemRead(CamelCaseSchema):
    receipt_id: UUID
    notice_id: UUID
    title: str
    body: str
    requires_acknowledgement: bool
    published_at: datetime
    read_at: datetime | None
    acknowledged_at: datetime | None


class SuperAdminNoticeInboxRead(CamelCaseSchema):
    items: list[SuperAdminNoticeInboxItemRead]
    total: int
    unread_count: int
    pending_acknowledgement_count: int


class SuperAdminNoticeAuditEventRead(CamelCaseSchema):
    id: UUID
    notice_id: UUID
    actor_user_id: UUID | None
    actor_user_name: str | None
    action: SuperAdminNoticeAuditAction
    details: dict
    occurred_at: datetime


class SuperAdminNoticeAuditEventPageRead(CamelCaseSchema):
    items: list[SuperAdminNoticeAuditEventRead]
    total: int
    limit: int
    offset: int
    has_more: bool
