"""Contracts for a governed WhatsApp draft handoff from a legal task."""

from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema


class TaskWhatsAppContact(CamelCaseSchema):
    contact_ref: str
    label: str
    masked_address: str | None
    consent_state: str
    service_window_state: str
    service_window_expires_at: datetime | None
    send_eligible: bool
    gate_reasons: list[str]


class TaskWhatsAppDraftPreviewRequest(WriteSchema):
    contact_ref: str = Field(min_length=32, max_length=1024)
    share_id: str = Field(min_length=1, max_length=180)
    share_url: str = Field(min_length=64, max_length=2048)
    message_body: str = Field(min_length=1, max_length=4096)


class TaskWhatsAppDraftConfirmRequest(TaskWhatsAppDraftPreviewRequest):
    preview_fingerprint: str = Field(min_length=64, max_length=64)
    confirmed: bool


class TaskWhatsAppDraftPreview(CamelCaseSchema):
    contact: TaskWhatsAppContact
    message_body: str
    preview_fingerprint: str
    delivery_mode: str
    external_actions_executed: int
    human_confirmation_required: bool


class TaskWhatsAppDraftPrepared(CamelCaseSchema):
    contact: TaskWhatsAppContact
    message_body: str
    delivery_status: str
    delivery_mode: str
    external_actions_executed: int
    conversation_path: str
    replayed: bool = False
