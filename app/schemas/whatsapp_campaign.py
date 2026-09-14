"""
Schemas de campanhas, templates Meta, opt-in e supressão.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    MetaTemplateStatus,
    OptInStatus,
    WhatsAppTemplateIntent,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


class CampaignAudienceFilter(WriteSchema):
    contact_ids: list[UUID] = Field(default_factory=list)
    audience_list_ids: list[UUID] = Field(default_factory=list)
    include_crm_contacts: bool = True
    column_ids: list[str] = Field(default_factory=list)
    practice_areas: list[str] = Field(default_factory=list)
    lead_sources: list[str] = Field(default_factory=list)
    assigned_user_ids: list[UUID] = Field(default_factory=list)
    classifications: list[str] = Field(default_factory=list)
    queue_statuses: list[str] = Field(default_factory=list)
    created_from: datetime | None = None
    created_to: datetime | None = None
    has_open_tasks: bool | None = None
    include_unlinked_contacts: bool = True
    include_blocked: bool = False
    search: str | None = None


class MetaTemplateRead(CamelCaseSchema):
    id: UUID
    whatsapp_connection_id: UUID
    external_template_id: str | None = None
    name: str
    category: str | None = None
    intent: WhatsAppTemplateIntent | None = None
    language: str
    status: MetaTemplateStatus
    quality_rating: str | None = None
    components: list[dict[str, Any]] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    last_synced_at: datetime
    removed_at_source_at: datetime | None = None
    updated_at: datetime


# ── Approved-template picker (Fase 1) ──────────────────────────────────────


class WhatsAppTemplateHeaderRead(CamelCaseSchema):
    type: str
    text: str | None = None


class WhatsAppTemplateButtonRead(CamelCaseSchema):
    type: str
    label: str
    url: str | None = None
    has_dynamic_suffix: bool = False


class WhatsAppTemplateVariableBindingRead(CamelCaseSchema):
    kind: Literal["field", "prompt"]
    field_key: str | None = None


class WhatsAppTemplateVariableRead(CamelCaseSchema):
    position: int
    scope: Literal["header", "body", "button"]
    binding: WhatsAppTemplateVariableBindingRead
    example: str | None = None


class WhatsAppTemplateCardRead(CamelCaseSchema):
    id: UUID
    meta_template_id: str | None = None
    name: str
    language: str
    intent: WhatsAppTemplateIntent | None = None
    sector: str | None = None
    meta_category: str | None = None
    status: Literal["approved", "pending"]
    header: WhatsAppTemplateHeaderRead | None = None
    body_text: str
    footer_text: str | None = None
    buttons: list[WhatsAppTemplateButtonRead] = Field(default_factory=list)
    variables: list[WhatsAppTemplateVariableRead] = Field(default_factory=list)
    is_sequence: bool = False
    sequence_step_count: int | None = None
    usage_count30d: int = Field(default=0, alias="usageCount30d")
    last_used_at: datetime | None = None
    last_sent_to_contact_at: datetime | None = None


class WhatsAppTemplateSuggestionRead(CamelCaseSchema):
    template_id: UUID
    reason_code: Literal[
        "conversation_cold",
        "matter_awaiting_documents",
        "receivable_open",
        "hearing_upcoming",
    ]
    reason_text: str
    rank: int


class WhatsAppTemplatePickerRead(CamelCaseSchema):
    cards: list[WhatsAppTemplateCardRead] = Field(default_factory=list)
    # ``cards`` is intentionally approved-only for quick-send.  Sequence
    # authoring receives this separate, non-sendable catalog so pending Meta
    # templates remain visible with their approval consequence.
    sequence_templates: list[WhatsAppTemplateCardRead] = Field(default_factory=list)
    suggestions: list[WhatsAppTemplateSuggestionRead] = Field(default_factory=list)
    sector_mode: Literal["condensed", "by_sector"]
    user_sector: str | None = None
    refreshed_at: datetime


class WhatsAppVariableFieldRead(CamelCaseSchema):
    field_key: str
    label: str
    resolved_value: str | None = None


class WhatsAppVariableGroupRead(CamelCaseSchema):
    key: str
    label: str
    values: list[WhatsAppVariableFieldRead] = Field(default_factory=list)


class WhatsAppVariablesRead(CamelCaseSchema):
    groups: list[WhatsAppVariableGroupRead] = Field(default_factory=list)


class WhatsAppButtonParameter(WriteSchema):
    """Dynamic URL suffix at its real Meta BUTTONS-array index."""

    index: int = Field(..., ge=0)
    value: str = Field(..., min_length=1)


class WhatsAppQuickSendCreate(WriteSchema):
    # `templateId`/`connectionId` are the conversation surface contract;
    # the longer names remain accepted for direct API callers.
    template_id: UUID | None = None
    meta_template_id: UUID | None = None
    connection_id: UUID | None = None
    whatsapp_connection_id: UUID | None = None
    contact_id: UUID | None = None
    thread_id: UUID | None = None
    matter_id: UUID | None = None
    header_parameters: list[str] = Field(default_factory=list)
    body_parameters: list[str] = Field(default_factory=list)
    button_parameters: list[str | WhatsAppButtonParameter] = Field(default_factory=list)
    title: str | None = Field(default=None, max_length=255)
    bindings: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=180)


class WhatsAppSequenceBindingIn(WriteSchema):
    """A durable, field-backed value used by a sequence stage.

    ``value`` is accepted for backwards-compatible previews but is never the
    source of truth for a delayed stage; the server resolves ``field_key``
    against the contact/case before persisting the flow.
    """

    field_key: str = Field(..., min_length=1, max_length=160)
    value: str | None = Field(default=None, max_length=4096)


class WhatsAppSequenceStageIn(WriteSchema):
    id: str | None = Field(default=None, max_length=160)
    template_id: UUID | None = None
    template_name: str | None = Field(default=None, max_length=255)
    template_language: str | None = Field(default=None, max_length=20)
    body: str | None = Field(default=None, max_length=4096)
    status: Literal["approved", "pending"] | None = None
    delay_value: float | None = Field(default=None, gt=0)
    delay_unit: Literal["hours", "days"] | None = None
    bindings: list[WhatsAppSequenceBindingIn] = Field(default_factory=list)

class WhatsAppSequenceStartCreate(WriteSchema):
    """Start a reviewed picker sequence for exactly one conversation contact."""

    thread_id: UUID | None = None
    contact_id: UUID | None = None
    matter_id: UUID | None = None
    connection_id: UUID | None = None
    template_id: UUID | None = None
    stages: list[WhatsAppSequenceStageIn] = Field(
        ..., min_length=1, max_length=50, validation_alias="stages"
    )
    interrupt_on_reply: bool = True
    title: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=180)


class WhatsAppSequenceStartRead(CamelCaseSchema):
    campaign_id: UUID
    flow_id: UUID | None = None
    first_recipient_id: UUID
    dispatch_state: Literal["accepted"]
    code: Literal["WA-SEQUENCE-DISPATCH-ACCEPTED"]
    status: str
    message: str

class MetaTemplateSyncRequest(WriteSchema):
    connection_id: UUID | None = None


class MetaTemplateAnalysisRequest(WriteSchema):
    template_id: UUID | None = None
    name: str = Field(default="", max_length=512)
    category_intent: Literal["utility", "marketing", "authentication"]
    body: str = Field(..., min_length=10, max_length=4096)
    sample_variables: str = Field(default="", max_length=2000)
    practice_area: str | None = Field(default=None, max_length=255)


class MetaTemplateOABRiskRead(CamelCaseSchema):
    level: Literal["low", "medium", "high"] = "low"
    signals: list[str] = Field(default_factory=list)
    requires_acknowledgement: bool = False


class MetaTemplateAnalysisRead(CamelCaseSchema):
    predicted_category: Literal["utility", "marketing", "authentication"]
    approval_likelihood: Literal["high", "medium", "low"]
    confidence: float = Field(..., ge=0, le=1)
    cost_band: str
    recommendation: str
    summary: str
    signals: list[str] = Field(default_factory=list)
    meta_category_suggested: Literal["utility", "marketing", "authentication"] | None = None
    marketing_signals: list[str] = Field(default_factory=list)
    oab_risk: MetaTemplateOABRiskRead = Field(default_factory=MetaTemplateOABRiskRead)
    provider: str
    model_used: str


class TemplateSubmitRequest(WriteSchema):
    oab_risk_acknowledged: bool = False


# --- Template Management (W2.2): rascunho local + submissão gated -----------------

TemplateCategoryLiteral = Literal["marketing", "utility", "authentication"]
TemplateLocalStatus = Literal[
    "draft",
    "submission_prepared",
    "pending_internal_approval",
    "catalog_queued",
    "catalog_pending_gate",
    "catalog_failed",
    "catalog_name_reserved",
]


class TemplateButtonIn(WriteSchema):
    type: Literal["QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE"]
    text: str
    url: str | None = None
    phone_number: str | None = None
    example: list[str] | None = None


class TemplateComponentsIn(WriteSchema):
    """Corpo dos componentes de um rascunho de template, no formato do builder."""

    header_type: Literal["NONE", "TEXT", "IMAGE", "VIDEO", "DOCUMENT"] | None = "NONE"
    header_text: str | None = None
    header_example: str | None = None
    body_text: str
    body_examples: list[str] = Field(default_factory=list)
    footer_text: str | None = None
    buttons: list[TemplateButtonIn] = Field(default_factory=list)


def _lowercase_category(value: object) -> object:
    """V31: a Meta escreve UTILITY/MARKETING; o literal do esquema é minúsculo."""
    return value.strip().lower() if isinstance(value, str) else value


class TemplateDraftCreate(WriteSchema):
    whatsapp_connection_id: UUID
    name: str
    language: str = "pt_BR"
    category: TemplateCategoryLiteral
    components: TemplateComponentsIn

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: object) -> object:
        return _lowercase_category(value)


class TemplateDraftUpdate(WriteSchema):
    category: TemplateCategoryLiteral | None = None
    language: str | None = None
    components: TemplateComponentsIn | None = None

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: object) -> object:
        return _lowercase_category(value)


class TemplateDraftRead(CamelCaseSchema):
    id: UUID
    whatsapp_connection_id: UUID
    external_template_id: str | None = None
    name: str
    category: str | None = None
    language: str
    status: MetaTemplateStatus
    quality_rating: str | None = None
    components: list[dict[str, Any]] = Field(default_factory=list)
    is_draft: bool
    local_status: TemplateLocalStatus | None = None
    validation_errors: list[str] = Field(default_factory=list)
    meta_submission_available: bool = False
    # W2.2 — UX de rejeição/recategorização: motivo cru da Meta (traduzido no
    # frontend) e categoria anterior quando o webhook `template_category_update`
    # já foi processado. Ambos lidos de raw_payload — nenhuma migration.
    rejected_reason: str | None = None
    previous_category: str | None = None
    created_at: datetime
    updated_at: datetime


class UnifiedTemplateRead(TemplateDraftRead):
    """Item da listagem unificada: rascunho local OU template sincronizado da Meta."""

    last_synced_at: datetime | None = None
    removed_at_source_at: datetime | None = None


class TemplateSubmitResult(CamelCaseSchema):
    template: UnifiedTemplateRead
    submitted_to_meta: bool
    message: str


class UtilityTemplateVariableRead(CamelCaseSchema):
    position: int
    key: str
    example: str


class UtilityTemplateCatalogItemRead(CamelCaseSchema):
    key: str
    catalog_version: str
    item_version: int
    meta_name: str
    category: Literal["utility"] = "utility"
    language: str = "pt_BR"
    body: str
    variables: list[UtilityTemplateVariableRead] = Field(default_factory=list)
    practice_areas: list[str] = Field(default_factory=list)
    template_id: UUID | None = None
    external_template_id: str | None = None
    status: MetaTemplateStatus | None = None
    provisioning_status: str
    rejection_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt: int = 0
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    can_retry: bool = False


class UtilityTemplateCatalogRead(CamelCaseSchema):
    catalog_version: str
    connection_id: UUID | None = None
    gate_open: bool
    items: list[UtilityTemplateCatalogItemRead] = Field(default_factory=list)


class OptInRecordRead(CamelCaseSchema):
    id: UUID
    contact_id: UUID | None = None
    phone: str
    channel: str
    status: OptInStatus
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    granted_at: datetime
    revoked_at: datetime | None = None
    updated_at: datetime

class OptInRecordCreate(WriteSchema):
    contact_id: UUID | None = None
    phone: str
    channel: str = "whatsapp"
    status: OptInStatus = OptInStatus.opted_in
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SuppressionEntryRead(CamelCaseSchema):
    id: UUID
    contact_id: UUID | None = None
    phone: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    created_at: datetime
    updated_at: datetime

class SuppressionEntryCreate(WriteSchema):
    contact_id: UUID | None = None
    phone: str
    reason: str = "manual"
    details: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class AudienceListMemberRead(CamelCaseSchema):
    id: UUID
    contact_id: UUID | None = None
    matter_id: UUID | None = None
    phone: str
    name: str | None = None
    email: str | None = None
    status: str
    source_row: int | None = None
    member_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AudienceListRead(CamelCaseSchema):
    id: UUID
    created_by_id: UUID | None = None
    name: str
    description: str | None = None
    source: str
    status: str
    member_count: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    import_metadata: dict[str, Any] = Field(default_factory=dict)
    members: list[AudienceListMemberRead] = Field(default_factory=list)
    sensitive_data_hidden: bool = False
    created_at: datetime
    updated_at: datetime


class AudienceListCreate(WriteSchema):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    source: Literal["manual", "csv", "crm", "flow"] = "manual"


class AudienceListUpdate(WriteSchema):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None


class AudienceListMemberCreate(WriteSchema):
    phone: str
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    contact_id: UUID | None = None
    matter_id: UUID | None = None
    member_metadata: dict[str, Any] = Field(default_factory=dict)


class AudienceListImportResult(CamelCaseSchema):
    audience_list: AudienceListRead
    imported: int
    updated: int
    invalid: int
    duplicates: int
    total_rows: int
    opt_ins_recorded: int = 0
    normalized: int = 0


class CampaignTemplateSnapshotRead(CamelCaseSchema):
    id: UUID
    source_template_id: UUID | None = None
    template_name: str
    category: str | None = None
    language: str
    status: MetaTemplateStatus
    header_parameters: list[str] = Field(default_factory=list)
    body_parameters: list[str] = Field(default_factory=list)
    button_parameters: list[str | WhatsAppButtonParameter] = Field(default_factory=list)
    components: list[dict[str, Any]] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

class CampaignAudienceRead(CamelCaseSchema):
    id: UUID
    filters: dict[str, Any] = Field(default_factory=dict)
    estimated_recipients: int = 0
    sample_recipients: list[dict[str, Any]] = Field(default_factory=list)

class CampaignRecipientRead(CamelCaseSchema):
    id: UUID
    contact_id: UUID | None = None
    matter_id: UUID | None = None
    phone: str
    name: str | None = None
    wamid: str | None = None
    flow_run_id: UUID | None = None
    flow_trigger_event: str | None = None
    flow_started_at: datetime | None = None
    status: CampaignRecipientStatus
    last_error: str | None = None
    send_context: dict[str, Any] = Field(default_factory=dict)
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None

class CampaignRead(CamelCaseSchema):
    id: UUID
    whatsapp_connection_id: UUID
    created_by_id: UUID | None = None
    follow_up_flow_id: UUID | None = None
    flow_start_mode: str = "manual"
    title: str
    description: str | None = None
    status: CampaignStatus
    scheduled_at: datetime | None = None
    launched_at: datetime | None = None
    completed_at: datetime | None = None
    paused_at: datetime | None = None
    total_recipients: int = 0
    eligible_recipients: int = 0
    blocked_recipients: int = 0
    sent_count: int = 0
    delivered_count: int = 0
    read_count: int = 0
    failed_count: int = 0
    audience: CampaignAudienceRead | None = None
    template_snapshot: CampaignTemplateSnapshotRead | None = None
    sample_recipients: list[CampaignRecipientRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

class CampaignCreate(WriteSchema):
    whatsapp_connection_id: UUID
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    meta_template_id: UUID
    header_parameters: list[str] = Field(default_factory=list)
    body_parameters: list[str] = Field(default_factory=list)
    button_parameters: list[str | WhatsAppButtonParameter] = Field(default_factory=list)
    audience_filter: CampaignAudienceFilter = Field(default_factory=CampaignAudienceFilter)
    scheduled_at: datetime | None = None
    follow_up_flow_id: UUID | None = None
    flow_start_mode: Literal["manual", "on_delivery", "on_reply"] = "manual"


class CampaignUpdate(WriteSchema):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    meta_template_id: UUID | None = None
    header_parameters: list[str] | None = None
    body_parameters: list[str] | None = None
    button_parameters: list[str | WhatsAppButtonParameter] | None = None
    audience_filter: CampaignAudienceFilter | None = None
    scheduled_at: datetime | None = None
    follow_up_flow_id: UUID | None = None
    flow_start_mode: Literal["manual", "on_delivery", "on_reply"] | None = None


class CampaignLaunchRequest(WriteSchema):
    dry_run: bool = False
    sample_size: int = Field(default=5, ge=1, le=25)
