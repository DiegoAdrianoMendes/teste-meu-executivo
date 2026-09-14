"""
Schemas da API externa oficial por workspace.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.matter import MatterRead
from app.schemas.whatsapp import ConversationRead, MessageTemplateRead, WhatsAppContactRead
from app.schemas.whatsapp_campaign import CampaignCreate, CampaignRead
from app.schemas.whatsapp_collaboration import ConversationInternalNoteRead
from app.services.whatsapp.helpers import normalize_phone_number


def _normalize_optional_meta_phone(value: str | None) -> str | None:
    if not str(value or "").strip():
        return None
    normalized = normalize_phone_number(value)
    if normalized is None:
        raise ValueError(
            "Informe DDI, DDD e número válidos. Exemplo aceito pela Meta: 5561999999999"
        )
    return normalized


PublicApiScope = Literal[
    "contacts:read",
    "conversations:read",
    "leads:write",
    "matters:write",
    "notes:write",
    "templates:send",
    "campaigns:write",
]


class WorkspacePublicApiTokenCreate(WriteSchema):
    label: str = Field(..., min_length=1, max_length=160)
    scopes: list[PublicApiScope] = Field(min_length=1)
    rate_limit_per_minute: int = Field(default=120, ge=10, le=2000)
    expires_at: Optional[datetime] = None


class WorkspacePublicApiTokenRead(CamelCaseSchema):
    id: UUID
    label: str
    token_prefix: str
    scopes: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int
    is_active: bool
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class WorkspacePublicApiTokenCreateResponse(CamelCaseSchema):
    token: WorkspacePublicApiTokenRead
    plain_text_token: str


class PublicApiRequestLogRead(CamelCaseSchema):
    id: UUID
    token_id: UUID
    path: str
    method: str
    scope: str
    status_code: int
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    requested_at: datetime


class PublicApiLeadCreate(WriteSchema):
    name: str = Field(..., min_length=1, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[str] = Field(default=None, max_length=255)
    cpf: Optional[str] = Field(default=None, max_length=20)
    demand_type: str = Field(..., min_length=1, max_length=255)
    practice_area: str = "trabalhista"
    lead_source: Optional[str] = None
    executive_summary: Optional[str] = None
    responsible_human_id: Optional[UUID] = None
    next_action: Optional[str] = None
    claim_value: Optional[float] = None

    _canonical_phone = field_validator("phone")(_normalize_optional_meta_phone)


class PublicApiLeadCreateResponse(CamelCaseSchema):
    matter: MatterRead
    contact: Optional[WhatsAppContactRead] = None


class PublicApiConversationNoteCreate(WriteSchema):
    body: str = Field(..., min_length=1, max_length=4000)


class PublicApiTemplateSendRequest(WriteSchema):
    meta_template_id: UUID
    contact_id: Optional[UUID] = None
    phone: Optional[str] = None
    whatsapp_connection_id: Optional[UUID] = None
    header_parameters: list[str] = Field(default_factory=list)
    body_parameters: list[str] = Field(default_factory=list)

    _canonical_phone = field_validator("phone")(_normalize_optional_meta_phone)


class PublicApiTemplateSendResponse(CamelCaseSchema):
    wamid: Optional[str] = None
    phone: str
    template: Optional[MessageTemplateRead] = None


class PublicApiCampaignCreateRequest(CampaignCreate):
    launch_now: bool = False


class PublicApiConversationRead(CamelCaseSchema):
    conversation: ConversationRead


class PublicApiNoteCreateResponse(CamelCaseSchema):
    note: ConversationInternalNoteRead


class PublicApiCampaignCreateResponse(CamelCaseSchema):
    campaign: CampaignRead
