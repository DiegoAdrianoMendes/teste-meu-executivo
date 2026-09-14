"""
Schemas das automações simples do inbox do WhatsApp.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.matter import KanbanColumnId
from app.models.whatsapp import WaQueueStatus
from app.schemas.base import CamelCaseSchema, WriteSchema


class ConversationAutomationRuleRead(CamelCaseSchema):
    id: str
    label: str
    enabled: bool = False
    trigger_type: str
    keyword: str | None = None
    reply_text: str | None = None
    flow_template_id: UUID | None = None
    target_queue_status: WaQueueStatus | None = None
    target_column_id: KanbanColumnId | None = None
    escalate_to_human: bool = False
    only_first_message: bool = False
    silence_window_hours: int = Field(default=0, ge=0, le=168)


class ConversationAutomationRuleUpdate(WriteSchema):
    id: str | None = None
    label: str
    enabled: bool = False
    trigger_type: str
    keyword: str | None = None
    reply_text: str | None = None
    flow_template_id: UUID | None = None
    target_queue_status: WaQueueStatus | None = None
    target_column_id: KanbanColumnId | None = None
    escalate_to_human: bool = False
    only_first_message: bool = False
    silence_window_hours: int = Field(default=0, ge=0, le=168)

    @model_validator(mode="after")
    def validate_off_hours_silence(self):
        if self.trigger_type == "off_hours" and self.silence_window_hours < 1:
            if "silence_window_hours" not in self.model_fields_set:
                self.silence_window_hours = 12
                return self
            raise ValueError(
                "A resposta fora do horário exige janela de silêncio de ao menos 1 hora."
            )
        return self


class WhatsAppAutomationSettingsRead(CamelCaseSchema):
    enabled: bool = False
    onboarding_completed: bool = False
    timezone: str = "America/Sao_Paulo"
    business_hours_start: int = Field(default=8, ge=0, le=23)
    business_hours_end: int = Field(default=19, ge=0, le=23)
    rules: list[ConversationAutomationRuleRead] = Field(default_factory=list)


class WhatsAppAutomationSettingsUpdate(WriteSchema):
    enabled: bool = False
    onboarding_completed: bool = False
    timezone: str = "America/Sao_Paulo"
    business_hours_start: int = Field(default=8, ge=0, le=23)
    business_hours_end: int = Field(default=19, ge=0, le=23)
    rules: list[ConversationAutomationRuleUpdate] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Fuso horário inválido.") from exc
        return value

    @model_validator(mode="after")
    def validate_business_hours(self):
        if self.business_hours_start == self.business_hours_end:
            raise ValueError("Início e fim do horário comercial devem ser diferentes.")
        return self
