"""Contratos da agenda, briefings e página pública de agendamento."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.models.meeting import MeetingEventType, MeetingNature
from app.schemas.base import CamelCaseSchema, WriteSchema


class CalendarProvider(StrEnum):
    manual = "manual"
    google_meet = "google_meet"
    microsoft_teams = "microsoft_teams"


class CalendarConnectionStatus(StrEnum):
    offline = "offline"
    manual_ready = "manual_ready"
    awaiting_api = "awaiting_api"
    connected = "connected"


class AvailabilityWindow(CamelCaseSchema):
    start: str
    end: str

    @model_validator(mode="after")
    def validate_window(self) -> AvailabilityWindow:
        try:
            start_hour, start_minute = (int(part) for part in self.start.split(":"))
            end_hour, end_minute = (int(part) for part in self.end.split(":"))
        except (TypeError, ValueError):
            raise ValueError("Use horários no formato HH:MM.") from None
        start_total = start_hour * 60 + start_minute
        end_total = end_hour * 60 + end_minute
        if not (0 <= start_total < 24 * 60 and 0 < end_total <= 24 * 60):
            raise ValueError("O horário informado está fora do intervalo diário.")
        if start_total >= end_total:
            raise ValueError("O fim da disponibilidade deve ser posterior ao início.")
        self.start = f"{start_hour:02d}:{start_minute:02d}"
        self.end = f"{end_hour:02d}:{end_minute:02d}"
        return self


class DayAvailability(CamelCaseSchema):
    weekday: int = Field(ge=0, le=6)
    enabled: bool = True
    windows: list[AvailabilityWindow] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_overlaps(self) -> DayAvailability:
        ordered = sorted(self.windows, key=lambda item: item.start)
        for previous, current in zip(ordered, ordered[1:]):
            if current.start < previous.end:
                raise ValueError("As faixas de disponibilidade não podem se sobrepor.")
        return self


def default_weekly_availability() -> list[DayAvailability]:
    business_windows = [
        AvailabilityWindow(start="09:00", end="12:00"),
        AvailabilityWindow(start="14:00", end="18:00"),
    ]
    return [
        DayAvailability(
            weekday=weekday,
            enabled=weekday < 5,
            windows=[window.model_copy() for window in business_windows] if weekday < 5 else [],
        )
        for weekday in range(7)
    ]


class BookingPageSettings(CamelCaseSchema):
    enabled: bool = True
    title: str = Field(default="Converse com a equipe KLM", min_length=3, max_length=120)
    description: str = Field(
        default="Escolha um horário para sua primeira reunião on-line.",
        min_length=3,
        max_length=500,
    )
    confirmation_message: str = Field(
        default="Agendamento confirmado. Nosso WhatsApp permanece disponível para você.",
        min_length=3,
        max_length=500,
    )
    show_office_phone: bool = True
    office_phone: str | None = Field(default=None, max_length=30)


class CalendarSettingsRead(CamelCaseSchema):
    timezone: str = "America/Sao_Paulo"
    provider: CalendarProvider = CalendarProvider.manual
    connection_status: CalendarConnectionStatus = CalendarConnectionStatus.offline
    account_email: str | None = None
    calendar_id: str | None = None
    default_meeting_url: str | None = None
    default_duration_minutes: int = Field(default=45, ge=15, le=240)
    slot_interval_minutes: int = Field(default=30, ge=15, le=120)
    minimum_notice_hours: int = Field(default=4, ge=0, le=720)
    booking_horizon_days: int = Field(default=30, ge=1, le=180)
    buffer_before_minutes: int = Field(default=0, ge=0, le=120)
    buffer_after_minutes: int = Field(default=15, ge=0, le=120)
    weekly_availability: list[DayAvailability] = Field(default_factory=default_weekly_availability)
    booking_page: BookingPageSettings = Field(default_factory=BookingPageSettings)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Fuso horário inválido.") from None
        return value

    @model_validator(mode="after")
    def validate_weekdays(self) -> CalendarSettingsRead:
        weekdays = [item.weekday for item in self.weekly_availability]
        if len(weekdays) != len(set(weekdays)):
            raise ValueError("Cada dia da semana deve aparecer apenas uma vez.")
        return self


class CalendarSettingsUpdate(WriteSchema):
    timezone: str = "America/Sao_Paulo"
    provider: CalendarProvider = CalendarProvider.manual
    account_email: str | None = None
    calendar_id: str | None = None
    default_meeting_url: str | None = None
    default_duration_minutes: int = Field(default=45, ge=15, le=240)
    slot_interval_minutes: int = Field(default=30, ge=15, le=120)
    minimum_notice_hours: int = Field(default=4, ge=0, le=720)
    booking_horizon_days: int = Field(default=30, ge=1, le=180)
    buffer_before_minutes: int = Field(default=0, ge=0, le=120)
    buffer_after_minutes: int = Field(default=15, ge=0, le=120)
    weekly_availability: list[DayAvailability] = Field(default_factory=default_weekly_availability)
    booking_page: BookingPageSettings = Field(default_factory=BookingPageSettings)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return CalendarSettingsRead.validate_timezone(value)

    @model_validator(mode="after")
    def validate_weekdays(self) -> CalendarSettingsUpdate:
        CalendarSettingsRead(
            **self.model_dump(),
            connection_status=CalendarConnectionStatus.offline,
        )
        return self


class BookingLinkCreate(WriteSchema):
    matter_id: UUID | None = None
    responsible_user_id: UUID | None = None
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    duration_minutes: int = Field(default=45, ge=15, le=240)
    meeting_provider: CalendarProvider | None = None
    meeting_url: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=1, ge=1, le=1000)


class BookingLinkRead(CamelCaseSchema):
    id: UUID
    matter_id: UUID | None = None
    responsible_user_id: UUID | None = None
    matter_label: str | None = None
    title: str
    description: str | None = None
    duration_minutes: int
    meeting_provider: CalendarProvider
    expires_at: datetime
    max_uses: int | None = None
    use_count: int
    is_active: bool
    booking_path: str
    created_at: datetime


class BookingSlotRead(CamelCaseSchema):
    starts_at: datetime
    ends_at: datetime


class AvailabilityPreviewRead(CamelCaseSchema):
    timezone: str
    duration_minutes: int
    meeting_provider: CalendarProvider
    connection_status: CalendarConnectionStatus
    meeting_url: str | None = None
    slots: list[BookingSlotRead] = Field(default_factory=list)


class InternalBookingCreate(WriteSchema):
    matter_id: UUID
    responsible_user_id: UUID | None = None
    title: str = Field(min_length=3, max_length=200)
    starts_at: datetime
    duration_minutes: int = Field(default=45, ge=15, le=240)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("starts_at")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("O horário do agendamento deve conter fuso horário.")
        return value


class PublicBookingPageRead(CamelCaseSchema):
    office_name: str
    title: str
    description: str
    duration_minutes: int
    timezone: str
    meeting_provider: CalendarProvider
    show_office_phone: bool
    office_phone: str | None = None
    slots: list[BookingSlotRead] = Field(default_factory=list)


class PublicBookingCreate(WriteSchema):
    starts_at: datetime
    name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_contact(self) -> PublicBookingCreate:
        if not (self.phone or self.email):
            raise ValueError("Informe telefone ou e-mail para confirmar o agendamento.")
        if self.starts_at.tzinfo is None:
            raise ValueError("O horário do agendamento deve conter fuso horário.")
        return self


class PublicBookingConfirmationRead(CamelCaseSchema):
    confirmation_id: UUID
    starts_at: datetime
    ends_at: datetime | None = None
    timezone: str
    location: str
    meeting_url: str | None = None
    message: str


class MeetingBriefingRead(CamelCaseSchema):
    meeting_id: UUID
    matter_id: UUID | None = None
    linked_to_matter: bool
    client_name: str
    contact: str | None = None
    demand_type: str | None = None
    health_subtheme: str | None = None
    health_object_name: str | None = None
    health_insurer: str | None = None
    medical_urgency_hours: int | None = None
    lead_score: int | None = None
    document_count: int = 0
    ready_document_count: int = 0
    no_show_risk: str | None = None
    summary: str
    insights: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_action: str | None = None
    meeting_url: str | None = None
    room_provider: str | None = None
    generation_mode: str = "local_context"
    generated_by: str = "Hermes Agenda"
    generated_at: datetime


class DailyBriefingItemRead(CamelCaseSchema):
    meeting_id: UUID
    starts_at: datetime
    title: str
    client_name: str
    matter_id: UUID | None = None
    demand_type: str | None = None
    no_show_risk: str | None = None
    readiness: str
    meeting_url: str | None = None
    warnings: list[str] = Field(default_factory=list)


class DailyBriefingRead(CamelCaseSchema):
    day: date
    timezone: str
    total: int
    linked_count: int
    orphan_count: int
    missing_room_count: int
    summary: str
    priorities: list[str] = Field(default_factory=list)
    items: list[DailyBriefingItemRead] = Field(default_factory=list)
    generation_mode: str = "local_context"
    generated_by: str = "Hermes Agenda"
    generated_at: datetime


class AgendaUnifiedEventRead(CamelCaseSchema):
    """Um item da agenda unificada (`GET /agenda`) — comercial e jurídico
    convivem na mesma lista, distinguidos por `natureza`/`event_type`.

    `source` identifica de onde o evento veio:
      * `meeting` — qualquer `Meeting` (comercial OU jurídico: reunião,
        audiência, sustentação, prazo interno, revisão, personalizado).
      * `process_publication` — prazo fatal já CONFIRMADO do módulo de
        controladoria processual (`ProcessPublication.data_fatal`); é
        sempre `natureza=juridico`, `eventType=prazo_fatal`,
        `isInviolable=true`, e reusa a contagem/memória já calculadas por
        `app/services/prazo_calculator.py` — nunca recalculado aqui.
      * `task_due` — prazo geral da tarefa.
      * `task_participant_due` — prazo individual de um participante.
      * `task_review_due` — prazo geral de revisão da tarefa.
    """

    id: str
    source: str
    source_id: UUID
    natureza: MeetingNature
    event_type: MeetingEventType
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    matter_id: UUID | None = None
    matter_label: str | None = None
    client_label: str | None = None
    task_id: UUID | None = None
    participant_role: str | None = None
    responsible_label: str | None = None
    responsible_user_id: UUID | None = None
    status: str | None = None
    is_inviolable: bool = False
    location: str | None = None
    meeting_url: str | None = None
    dias: int | None = None
    memoria_calculo: list | None = None


__all__ = [
    "AgendaUnifiedEventRead",
    "BookingLinkCreate",
    "BookingLinkRead",
    "BookingPageSettings",
    "BookingSlotRead",
    "CalendarConnectionStatus",
    "CalendarProvider",
    "CalendarSettingsRead",
    "CalendarSettingsUpdate",
    "DailyBriefingRead",
    "MeetingBriefingRead",
    "PublicBookingConfirmationRead",
    "PublicBookingCreate",
    "PublicBookingPageRead",
    "default_weekly_availability",
]
