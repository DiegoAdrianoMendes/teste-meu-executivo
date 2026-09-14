"""Política de cadência humana para mensagens automatizadas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema


class MessagingCadenceSettingsRead(CamelCaseSchema):
    enabled: bool = True
    show_typing_indicator: bool = True
    timezone: str = "America/Sao_Paulo"
    pre_delay_min_ms: int = Field(default=800, ge=0, le=60_000)
    pre_delay_max_ms: int = Field(default=2_400, ge=0, le=60_000)
    characters_per_second: float = Field(default=5.0, ge=1.0, le=20.0)
    typing_min_ms: int = Field(default=1_200, ge=0, le=120_000)
    typing_max_ms: int = Field(default=18_000, ge=0, le=120_000)
    jitter_percent: int = Field(default=18, ge=0, le=50)
    inter_message_min_ms: int = Field(default=2_500, ge=0, le=300_000)
    max_messages_per_minute: int = Field(default=6, ge=1, le=60)
    # Teto le=1_000: mantido para não quebrar payloads existentes (canais não-Meta).
    # Isto NÃO é o cap de 200/h por conexão do Instagram (Meta Policy Gate, achado
    # ALTO da auditoria de 23/07/2026): aquele é aplicado em runtime por
    # `app.services.meta_policy_gate.MetaPolicyGate` + contador dedicado
    # (`MessagingCadenceService.count_recent_agent_messages_for_connection`),
    # independente deste teto de workspace.
    max_messages_per_hour: int = Field(default=40, ge=1, le=1_000)
    cooldown_seconds: int = Field(default=120, ge=1, le=86_400)
    quiet_hours_enabled: bool = True
    quiet_hours_start: int = Field(default=20, ge=0, le=23)
    quiet_hours_end: int = Field(default=8, ge=0, le=23)
    max_consecutive_without_reply: int = Field(default=3, ge=1, le=20)
    stop_on_block_or_opt_out: bool = True
    official_api_only: bool = True
    require_valid_consent: bool = True

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Fuso horário inválido.") from None
        return value

    @model_validator(mode="after")
    def validate_ranges(self) -> "MessagingCadenceSettingsRead":
        if self.pre_delay_max_ms < self.pre_delay_min_ms:
            raise ValueError("O atraso máximo deve ser maior ou igual ao mínimo.")
        if self.typing_max_ms < self.typing_min_ms:
            raise ValueError("O tempo máximo de digitação deve ser maior ou igual ao mínimo.")
        if self.max_messages_per_hour < self.max_messages_per_minute:
            raise ValueError("O limite por hora não pode ser menor que o limite por minuto.")
        return self


class MessagingCadenceSettingsUpdate(WriteSchema):
    enabled: bool = True
    show_typing_indicator: bool = True
    timezone: str = "America/Sao_Paulo"
    pre_delay_min_ms: int = Field(default=800, ge=0, le=60_000)
    pre_delay_max_ms: int = Field(default=2_400, ge=0, le=60_000)
    characters_per_second: float = Field(default=5.0, ge=1.0, le=20.0)
    typing_min_ms: int = Field(default=1_200, ge=0, le=120_000)
    typing_max_ms: int = Field(default=18_000, ge=0, le=120_000)
    jitter_percent: int = Field(default=18, ge=0, le=50)
    inter_message_min_ms: int = Field(default=2_500, ge=0, le=300_000)
    max_messages_per_minute: int = Field(default=6, ge=1, le=60)
    max_messages_per_hour: int = Field(default=40, ge=1, le=1_000)
    cooldown_seconds: int = Field(default=120, ge=1, le=86_400)
    quiet_hours_enabled: bool = True
    quiet_hours_start: int = Field(default=20, ge=0, le=23)
    quiet_hours_end: int = Field(default=8, ge=0, le=23)
    max_consecutive_without_reply: int = Field(default=3, ge=1, le=20)
    stop_on_block_or_opt_out: bool = True
    official_api_only: bool = True
    require_valid_consent: bool = True

    @model_validator(mode="after")
    def validate_settings(self) -> "MessagingCadenceSettingsUpdate":
        MessagingCadenceSettingsRead.model_validate(self.model_dump())
        return self


class MessagingCadencePreviewRequest(WriteSchema):
    text: str = Field(min_length=1, max_length=10_000)
    idempotency_key: str = Field(default="cadence-preview", min_length=3, max_length=160)
    thread_id: UUID | None = None
    now: datetime | None = None


class MessagingCadencePlanRead(CamelCaseSchema):
    enabled: bool
    show_typing_indicator: bool
    pre_delay_ms: int
    typing_duration_ms: int
    total_delay_ms: int
    planned_at: datetime
    typing_starts_at: datetime
    not_before: datetime
    state: str
    reason: str
    minute_count: int = 0
    hour_count: int = 0
    max_messages_per_minute: int
    max_messages_per_hour: int
    max_consecutive_without_reply: int
    official_api_only: bool
    require_valid_consent: bool
    stop_on_block_or_opt_out: bool
