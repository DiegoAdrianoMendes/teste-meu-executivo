"""Typed contracts for tenant-scoped conversation response metrics."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema


class ConversationResponseState(str, enum.Enum):
    cliente_aguardando_escritorio = "cliente_aguardando_escritorio"
    escritorio_aguardando_cliente = "escritorio_aguardando_cliente"
    respondido = "respondido"
    sem_mensagem = "sem_mensagem"


class ConversationResponseSlaStatus(str, enum.Enum):
    within_sla = "within_sla"
    overdue = "overdue"
    without_sla = "without_sla"
    not_applicable = "not_applicable"


class ConversationResponsePeriod(str, enum.Enum):
    all_time = "all_time"
    last_24_hours = "last_24_hours"
    last_7_days = "last_7_days"
    last_30_days = "last_30_days"
    last_90_days = "last_90_days"
    custom = "custom"


class ConversationResponseFilterPreset(str, enum.Enum):
    all = "all"
    pending = "pending"
    overdue = "overdue"
    waiting_for_client = "waiting_for_client"
    responded = "responded"
    without_messages = "without_messages"


class ConversationResponseSort(str, enum.Enum):
    duration = "duration"
    started_at = "started_at"
    last_activity = "last_activity"
    sla_due_at = "sla_due_at"
    first_response = "first_response"
    attendant = "attendant"
    team = "team"
    status = "status"


class ConversationResponseSortDirection(str, enum.Enum):
    asc = "asc"
    desc = "desc"


class ConversationResponseMetricRead(CamelCaseSchema):
    thread_id: UUID
    thread_status: str
    primary_channel: str
    state: ConversationResponseState
    state_started_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    last_client_facing_activity_at: datetime | None = None
    assigned_team_id: UUID | None = None
    assigned_team_name: str | None = None
    assigned_attendant_id: UUID | None = None
    assigned_attendant_name: str | None = None
    sla_due_at: datetime | None = None
    sla_status: ConversationResponseSlaStatus
    is_overdue: bool
    overdue_seconds: int = Field(ge=0)
    first_client_message_at: datetime | None = None
    first_response_at: datetime | None = None
    first_response_seconds: int | None = Field(default=None, ge=0)
    first_response_within_sla: bool | None = None


class ConversationResponseKpiRead(CamelCaseSchema):
    conversations: int = Field(ge=0)
    pending: int = Field(ge=0)
    average_wait_seconds: float | None = Field(default=None, ge=0)
    median_wait_seconds: float | None = Field(default=None, ge=0)
    p90_wait_seconds: float | None = Field(default=None, ge=0)
    longest_wait_seconds: int | None = Field(default=None, ge=0)
    average_first_response_seconds: float | None = Field(default=None, ge=0)
    within_sla_rate: float | None = Field(default=None, ge=0, le=100)
    sla_evaluated: int = Field(ge=0)
    within_sla: int = Field(ge=0)


class ConversationResponseAggregateRead(CamelCaseSchema):
    group_id: UUID | None = None
    group_name: str
    metrics: ConversationResponseKpiRead


class ConversationResponsePeriodRead(CamelCaseSchema):
    preset: ConversationResponsePeriod
    started_at: datetime | None = None
    ended_at: datetime
    as_of: datetime


class ConversationResponseFiltersRead(CamelCaseSchema):
    preset: ConversationResponseFilterPreset
    attendant_ids: list[UUID] = Field(default_factory=list)
    team_ids: list[UUID] = Field(default_factory=list)
    statuses: list[ConversationResponseState] = Field(default_factory=list)
    sla_statuses: list[ConversationResponseSlaStatus] = Field(default_factory=list)
    sort_by: ConversationResponseSort
    sort_direction: ConversationResponseSortDirection


class ConversationResponseMetricsRead(CamelCaseSchema):
    generated_at: datetime
    period: ConversationResponsePeriodRead
    filters: ConversationResponseFiltersRead
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    has_more: bool
    items: list[ConversationResponseMetricRead]
    account: ConversationResponseAggregateRead
    by_team: list[ConversationResponseAggregateRead]
    by_attendant: list[ConversationResponseAggregateRead]
