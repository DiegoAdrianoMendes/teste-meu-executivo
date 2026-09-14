"""Contratos HTTP do SAC nativo."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.storage_object import StorageAVStatus, StorageObjectState
from app.models.support_ticket import (
    SupportMessageVisibility,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.storage_object import StorageUploadRequest


class SupportTechnicalContext(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    page: str | None = Field(default=None, max_length=500)
    action: str | None = Field(default=None, max_length=200)
    frontend_version: str | None = Field(default=None, max_length=80)
    browser: str | None = Field(default=None, max_length=120)
    device: str | None = Field(default=None, max_length=120)
    viewport: str | None = Field(default=None, max_length=40)
    occurred_at: datetime | None = None
    realtime_status: str | None = Field(default=None, max_length=40)
    failed_request_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator(
        "page",
        "action",
        "frontend_version",
        "browser",
        "device",
        "viewport",
        "realtime_status",
    )
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        return value.strip() if value is not None and value.strip() else None

    @field_validator("failed_request_ids")
    @classmethod
    def sanitize_request_ids(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values[:20]:
            value = raw.strip()
            if value and len(value) <= 120 and all(
                character.isalnum() or character in "-_:." for character in value
            ):
                cleaned.append(value)
        return cleaned


class SupportTicketCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    ticket_type: SupportTicketType
    subject: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=10, max_length=20_000)
    priority: SupportTicketPriority = SupportTicketPriority.normal
    severity: SupportTicketSeverity = SupportTicketSeverity.medium
    technical_context: SupportTechnicalContext = Field(
        default_factory=SupportTechnicalContext
    )

    @field_validator("subject", "description")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return value.strip()


class PlatformSupportTicketCreate(SupportTicketCreate):
    """Chamado aberto pela equipe do SaaS em nome de um usuário da conta."""

    workspace_id: UUID
    reporter_user_id: UUID
    queue: SupportTicketQueue | None = None


class SupportMessageCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=20_000)
    visibility: SupportMessageVisibility = SupportMessageVisibility.public

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        return value.strip()


class SupportTicketUpdate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    status: SupportTicketStatus | None = None
    priority: SupportTicketPriority | None = None
    severity: SupportTicketSeverity | None = None
    queue: SupportTicketQueue | None = None
    assigned_to_user_id: UUID | None = None
    linked_error_log_id: UUID | None = None
    linked_incident_id: UUID | None = None
    linked_release_id: UUID | None = None
    support_access_request_id: UUID | None = None

    @model_validator(mode="after")
    def reject_null_for_non_nullable_fields(self):
        for field_name in ("status", "priority", "severity", "queue"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} não aceita valor nulo.")
        return self


class SupportRatingCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2_000)

    @field_validator("comment")
    @classmethod
    def strip_comment(cls, value: str | None) -> str | None:
        return value.strip() if value is not None and value.strip() else None


class SupportAttachmentCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    upload: StorageUploadRequest
    message_id: UUID | None = None


class SupportCollaboratorCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID


class SupportSavedFilterCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    filters: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class SupportAvailabilityWeekday(StrEnum):
    monday = "monday"
    tuesday = "tuesday"
    wednesday = "wednesday"
    thursday = "thursday"
    friday = "friday"
    saturday = "saturday"
    sunday = "sunday"


class SupportAvailabilityDay(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    day: SupportAvailabilityWeekday
    enabled: bool = False
    start_time: str = "09:00"
    end_time: str = "18:00"

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        normalized = value.strip()
        try:
            parsed = datetime.strptime(normalized, "%H:%M")
        except ValueError as error:
            raise ValueError("Horário deve usar o formato HH:MM.") from error
        return parsed.strftime("%H:%M")

    @model_validator(mode="after")
    def validate_window(self):
        if self.enabled and self.start_time >= self.end_time:
            raise ValueError("Horário final deve ser posterior ao horário inicial.")
        return self


def _default_availability_days() -> list[SupportAvailabilityDay]:
    return [
        SupportAvailabilityDay(
            day=day,
            enabled=day
            in {
                SupportAvailabilityWeekday.monday,
                SupportAvailabilityWeekday.tuesday,
                SupportAvailabilityWeekday.wednesday,
                SupportAvailabilityWeekday.thursday,
                SupportAvailabilityWeekday.friday,
            },
        )
        for day in SupportAvailabilityWeekday
    ]


class SupportAvailabilitySettings(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timezone: str = Field(default="America/Sao_Paulo", min_length=1, max_length=80)
    days: list[SupportAvailabilityDay] = Field(
        default_factory=_default_availability_days,
        min_length=7,
        max_length=7,
    )
    offline_message: str = Field(
        default=(
            "Estamos fora do horário de atendimento ao vivo. "
            "Envie sua solicitação e responderemos no próximo período."
        ),
        min_length=3,
        max_length=500,
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("Timezone IANA inválido.") from error
        return normalized

    @field_validator("offline_message")
    @classmethod
    def strip_offline_message(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("A mensagem fora do horário deve ter ao menos 3 caracteres.")
        return normalized

    @model_validator(mode="after")
    def validate_days(self):
        configured = [item.day for item in self.days]
        if len(set(configured)) != len(SupportAvailabilityWeekday):
            raise ValueError("Informe cada dia da semana exatamente uma vez.")
        if set(configured) != set(SupportAvailabilityWeekday):
            raise ValueError("A grade semanal precisa conter os sete dias.")
        return self


class SupportAvailabilitySettingsUpdate(SupportAvailabilitySettings):
    expected_etag: str | None


class SupportAvailabilitySettingsRead(CamelCaseSchema):
    enabled: bool
    timezone: str
    days: list[SupportAvailabilityDay]
    offline_message: str
    etag: str | None = None
    version: int
    updated_at: datetime | None = None
    current_state: Literal["online", "offline"]
    next_change_at: datetime | None = None


class SupportAvailabilityStatusRead(CamelCaseSchema):
    state: Literal["online", "offline"]
    message: str


_DEFAULT_ESCALATION_BUSINESS_DAYS = (
    SupportAvailabilityWeekday.monday,
    SupportAvailabilityWeekday.tuesday,
    SupportAvailabilityWeekday.wednesday,
    SupportAvailabilityWeekday.thursday,
    SupportAvailabilityWeekday.friday,
)


class SupportEscalationSettings(WriteSchema):
    """Configuração de 'Falar com um humano' (agendamento + WhatsApp)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    scheduling_url: str | None = Field(default=None, max_length=500)
    whatsapp_number: str | None = Field(default=None, max_length=32)
    business_days: list[SupportAvailabilityWeekday] = Field(
        default_factory=lambda: list(_DEFAULT_ESCALATION_BUSINESS_DAYS),
        min_length=1,
        max_length=7,
    )
    business_hours_start: str = Field(default="09:00")
    business_hours_end: str = Field(default="18:00")

    @field_validator("scheduling_url")
    @classmethod
    def validate_scheduling_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL de agendamento deve ser http(s) válida.")
        return normalized

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp_number(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        digits = re.sub(r"\D", "", normalized)
        if not (8 <= len(digits) <= 15):
            raise ValueError("Número de WhatsApp inválido.")
        return normalized

    @field_validator("business_hours_start", "business_hours_end")
    @classmethod
    def validate_business_hours(cls, value: str) -> str:
        normalized = value.strip()
        try:
            parsed = datetime.strptime(normalized, "%H:%M")
        except ValueError as error:
            raise ValueError("Horário deve usar o formato HH:MM.") from error
        return parsed.strftime("%H:%M")

    @model_validator(mode="after")
    def validate_window(self):
        if self.business_hours_start >= self.business_hours_end:
            raise ValueError("Horário final deve ser posterior ao horário inicial.")
        if len(set(self.business_days)) != len(self.business_days):
            raise ValueError("Cada dia útil pode ser informado apenas uma vez.")
        return self


class SupportEscalationSettingsUpdate(SupportEscalationSettings):
    expected_etag: str | None = None


class SupportEscalationSettingsRead(CamelCaseSchema):
    enabled: bool
    scheduling_url: str | None = None
    whatsapp_number: str | None = None
    business_days: list[SupportAvailabilityWeekday]
    business_hours_start: str
    business_hours_end: str
    etag: str | None = None
    version: int
    updated_at: datetime | None = None


class SupportEscalationStatusRead(CamelCaseSchema):
    available_now: bool
    scheduling_url: str | None = None
    whatsapp_link: str | None = None
    next_window: datetime | None = None


class SupportUserRead(CamelCaseSchema):
    id: UUID
    name: str
    avatar_url: str | None = None


class SupportAttachmentRead(CamelCaseSchema):
    id: UUID
    message_id: UUID | None = None
    storage_object_version_id: UUID
    original_filename: str
    content_type: str
    byte_size: int | None = None
    state: StorageObjectState
    av_status: StorageAVStatus
    created_at: datetime


class SupportMessageRead(CamelCaseSchema):
    id: UUID
    author_user_id: UUID | None = None
    author_label: str
    visibility: SupportMessageVisibility
    body: str
    created_at: datetime


class SupportEventRead(CamelCaseSchema):
    id: UUID
    actor_user_id: UUID | None = None
    event_type: str
    summary: str
    before_data: dict[str, Any]
    after_data: dict[str, Any]
    created_at: datetime


class SupportTicketRead(CamelCaseSchema):
    id: UUID
    public_number: str
    workspace_id: UUID
    workspace_name: str | None = None
    reporter_user_id: UUID
    assigned_to_user_id: UUID | None = None
    ticket_type: SupportTicketType
    priority: SupportTicketPriority
    severity: SupportTicketSeverity
    queue: SupportTicketQueue
    status: SupportTicketStatus
    subject: str
    description: str
    route: str | None = None
    technical_context: dict[str, Any]
    error_fingerprint: str | None = None
    linked_error_log_id: UUID | None = None
    linked_incident_id: UUID | None = None
    linked_release_id: UUID | None = None
    support_access_request_id: UUID | None = None
    duplicate_of_ticket_id: UUID | None = None
    version: int
    first_response_due_at: datetime
    resolution_due_at: datetime
    first_responded_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    reopened_at: datetime | None = None
    satisfaction_score: int | None = None
    satisfaction_comment: str | None = None
    created_at: datetime
    updated_at: datetime
    reporter: SupportUserRead | None = None
    assignee: SupportUserRead | None = None


class SupportTicketDetail(SupportTicketRead):
    messages: list[SupportMessageRead] = Field(default_factory=list)
    attachments: list[SupportAttachmentRead] = Field(default_factory=list)
    events: list[SupportEventRead] = Field(default_factory=list)
    collaborator_ids: list[UUID] = Field(default_factory=list)


class SupportTicketPage(CamelCaseSchema):
    items: list[SupportTicketRead]
    next_cursor: str | None = None


class SupportUploadAuthorizationRead(CamelCaseSchema):
    attachment_id: UUID
    storage_object_version_id: UUID
    upload_url: str | None
    upload_required: bool = True
    upload_headers: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime


class SupportAttachmentDownloadRead(CamelCaseSchema):
    attachment_id: UUID
    download_url: str
    expires_at: datetime


class SupportSavedFilterRead(CamelCaseSchema):
    id: UUID
    owner_user_id: UUID
    name: str
    filters: dict[str, Any]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class SupportMetricDistribution(CamelCaseSchema):
    label: str
    value: int


class SupportDurationMetric(CamelCaseSchema):
    average_seconds: float
    median_seconds: float
    p90_seconds: float
    p95_seconds: float


class SupportMetricsRead(CamelCaseSchema):
    period_days: int
    previous_period_change_pct: dict[str, float]
    new: int
    open: int
    resolved: int
    reopened: int
    overdue: int
    near_due: int
    reopen_rate_pct: float
    satisfaction_average: float | None = None
    first_response: SupportDurationMetric
    resolution: SupportDurationMetric
    backlog_by_queue: list[SupportMetricDistribution]
    backlog_by_severity: list[SupportMetricDistribution]
    backlog_by_age: list[SupportMetricDistribution]
    performance_by_queue: list[SupportMetricDistribution]
    performance_by_assignee: list[SupportMetricDistribution]
    instability_by_version: list[SupportMetricDistribution]
    instability_by_page: list[SupportMetricDistribution]
    instability_by_browser: list[SupportMetricDistribution]
    suggestions_by_destination: list[SupportMetricDistribution]
    recurrence_by_workspace: list[SupportMetricDistribution]
    related_incidents: int
