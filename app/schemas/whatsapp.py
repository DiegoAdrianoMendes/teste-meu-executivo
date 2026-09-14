"""
Schemas Pydantic para a integração WhatsApp Business.
"""
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.models.whatsapp import (
    ConnectionType,
    HumanControlState,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WaQueueStatus,
    WhatsAppConnectionTier,
)
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.services.whatsapp.helpers import normalize_phone_number

# ── Conexão ───────────────────────────────────────────────────────────────

class WhatsAppConnectionRead(CamelCaseSchema):
    id: UUID
    connectionType: ConnectionType
    transportMode: str
    isDefault: bool
    phoneNumberId: Optional[str]
    phoneNumber: Optional[str]
    displayPhoneNumber: Optional[str]
    displayName: Optional[str]
    businessAccountId: Optional[str]
    wabaId: Optional[str]
    instanceId: Optional[str]
    qrProvider: Optional[str]
    qrStatus: Optional[str]
    status: WaConnectionStatus
    webhookRegistered: bool
    connectedAt: Optional[datetime]
    disconnectedAt: Optional[datetime]
    updatedAt: datetime
    lastError: Optional[str] = None
    appSecretConfigured: bool = False
    coexistenceSyncStatus: str = "not_applicable"  # noqa: N815 - stable frontend contract
    coexistenceContactsSyncRequestId: str | None = None  # noqa: N815 - stable frontend contract
    coexistenceHistorySyncRequestId: str | None = None  # noqa: N815 - stable frontend contract
    coexistenceSyncRequestedAt: datetime | None = None  # noqa: N815 - stable frontend contract
    tokenExpiresAt: datetime | None = None  # noqa: N815 - stable frontend contract
    tokenLifecycleState: str = "not_applicable"  # noqa: N815 - stable frontend contract
    tokenExpiresInDays: int | None = None  # noqa: N815 - stable frontend contract

class WhatsAppWebhookInfoRead(CamelCaseSchema):
    """Dados que o escritório precisa colar na tela de Webhooks do app Meta (modo B1/manual)."""
    webhookUrl: str
    verifyToken: Optional[str] = None
    verifyTokenConfigured: bool
    appSecretConfigured: bool
    instructions: List[str]
    recommended_fields: list[str]


class WhatsAppMetaWebhookFieldRead(CamelCaseSchema):
    field: str
    category: str
    severity: str
    implementation: str
    recommended: bool
    sprint: int
    destination: str
    description: str
    operator_action: str
    observation_state: str
    event_count: int
    last_observed_at: datetime | None = None
    last_summary: dict[str, Any] | str | None = None
    resource_type: str | None = None
    app_href: str | None = None
    blackbox_href: str | None = None
    state_code: str | None = None
    receipt_counts: dict[str, int] = Field(default_factory=dict)
    desired_subscription: bool = True
    subscription_count: int = 0
    applicability: str = "waba_level"
    provider_states: list[str] = Field(default_factory=lambda: ["unknown"])
    automation: dict[str, Any] = Field(default_factory=dict)


class WhatsAppMetaOperationalEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID | None = None
    connection_id: UUID | None = None
    field: str
    category: str
    severity: str
    implementation: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class WhatsAppMetaOperationsRead(CamelCaseSchema):
    catalog: list[WhatsAppMetaWebhookFieldRead]
    events: list[WhatsAppMetaOperationalEventRead] = Field(default_factory=list)
    event_count: int = 0
    latest_event_at: datetime | None = None
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    receipt_count: int = 0
    latest_receipt_at: datetime | None = None


class WhatsAppMetaAutomationPolicyUpdate(WriteSchema):
    enabled: bool
    mode: str = Field(
        pattern=r"^(observe_only|suggest_action|auto_reversible|approval_each_time)$"
    )
    minimum_severity: str = Field(
        default="warning", pattern=r"^(info|warning|high|critical)$"
    )
    expected_version: int = Field(ge=1)


class WhatsAppConnectRequest(WriteSchema):
    """Payload para conectar/configurar o número WhatsApp."""
    access_token: str = Field(..., description="Token de acesso permanente da System User")
    phone_number_id: str = Field(..., description="Phone Number ID do Meta for Developers")
    business_account_id: str = Field(..., description="WhatsApp Business Account ID")


class WhatsAppConnectionCreateManual(WriteSchema):
    access_token: str = Field(..., description="Token de acesso permanente da System User")
    phone_number_id: str = Field(..., description="Phone Number ID do Meta for Developers")
    business_account_id: str = Field(..., description="WhatsApp Business Account ID")
    registration_pin: Optional[str] = Field(default=None, min_length=6, max_length=6)
    transport_mode: str = Field(default="official_api", pattern=r"^(official_api|coexistence)$")
    is_default: bool = True
    app_secret: Optional[str] = Field(
        default=None,
        min_length=8,
        description=(
            "App Secret do app Meta do próprio escritório (BYOA), copiado da "
            "tela Configurações Básicas do app em developers.facebook.com. "
            "Usado para validar a assinatura do webhook desta conexão "
            "(HMAC); nunca é devolvido em claro depois de salvo."
        ),
    )


class WhatsAppConnectionCreateQr(WriteSchema):
    display_name: Optional[str] = None
    provider: str = "evolution"
    is_default: bool = False


class TechProviderCallbackRequest(WriteSchema):
    code: str
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    registration_pin: Optional[str] = Field(default=None, min_length=6, max_length=6)
    transport_mode: str = Field(default="official_api", pattern=r"^(official_api|coexistence)$")
    is_default: bool = True


class TechProviderConfigRead(CamelCaseSchema):
    available: bool
    manualCloudApiAvailable: bool  # noqa: N815 - stable frontend contract
    coexistenceAvailable: bool  # noqa: N815 - stable frontend contract
    techProviderReady: bool  # noqa: N815 - stable frontend contract
    appId: str | None = None  # noqa: N815 - stable frontend contract
    configId: str | None = None  # noqa: N815 - stable frontend contract
    solutionId: str | None = None  # noqa: N815 - stable frontend contract
    graphApiVersion: str  # noqa: N815 - stable frontend contract
    featureType: str = "whatsapp_business_app_onboarding"  # noqa: N815
    missingConfiguration: list[str] = Field(default_factory=list)  # noqa: N815
    missingTechProviderConfiguration: list[str] = Field(default_factory=list)  # noqa: N815


class WhatsAppConnectionStatus(CamelCaseSchema):
    status: WaConnectionStatus
    connectionId: Optional[UUID] = None
    connectionType: Optional[ConnectionType] = None
    phoneNumberId: Optional[str] = None
    phoneNumber: Optional[str]
    displayName: Optional[str]
    webhookUrl: str
    verifyToken: str
    webhookRegistered: bool
    lastError: Optional[str] = None


class ProviderMediaUploadRead(CamelCaseSchema):
    upload_intent_id: UUID
    receipt_id: UUID
    provider_media_id: str
    connection_id: UUID
    media_type: str
    source_sha256: str
    source_size_bytes: int
    source_content_type: str
    uploaded_at: datetime
    expires_at: Optional[datetime] = None
    deduplicated: bool = False


class ProviderMediaUploadIntentRead(CamelCaseSchema):
    upload_intent_id: UUID
    connection_id: UUID
    state: str
    attempt_count: int
    media_type: str
    source_sha256: str
    source_size_bytes: int
    source_content_type: str
    provider_media_id: Optional[str] = None
    receipt_id: Optional[UUID] = None
    request_started_at: Optional[datetime] = None
    provider_accepted_at: Optional[datetime] = None
    delivery_uncertain_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error_code: Optional[str] = None
    last_error_message: Optional[str] = None
    reconciliation_decision: Optional["ProviderMediaReconciliationDecision"] = None
    reconciliation_reason: Optional[str] = None
    reconciled_by_user_id: Optional[UUID] = None
    reconciled_at: Optional[datetime] = None


class ProviderMediaReconciliationDecision(StrEnum):
    provider_accepted = "provider_accepted"
    retry_authorized = "retry_authorized"


class ProviderMediaReconciliationRequest(WriteSchema):
    decision: ProviderMediaReconciliationDecision
    reason: str = Field(min_length=3, max_length=1000)
    provider_media_id: Optional[str] = Field(default=None, max_length=255)
    provider_receipt: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("reason must contain at least 3 non-whitespace characters")
        return normalized

    @field_validator("provider_media_id")
    @classmethod
    def normalize_provider_media_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.decision == ProviderMediaReconciliationDecision.provider_accepted:
            if not self.provider_media_id or not self.provider_receipt:
                raise ValueError(
                    "provider acceptance requires provider_media_id and provider_receipt"
                )
            if str(self.provider_receipt.get("id") or "").strip() != self.provider_media_id:
                raise ValueError("provider_receipt.id must match provider_media_id")
        elif self.provider_media_id is not None or self.provider_receipt:
            raise ValueError("retry authorization must not include provider evidence")
        return self


# ── Contatos ──────────────────────────────────────────────────────────────

class WhatsAppContactIdentityRead(CamelCaseSchema):
    """Só os identificadores do contato, para a tela de conversas.

    Payload deliberadamente mínimo. Existe para a tela mostrar quem é e se dá
    para responder SEM alterar o payload da conversa, que já funciona e é
    central — acréscimo que não arrisca o que está de pé.
    """

    id: UUID
    phone: Optional[str] = None
    # Diz que o número existe mas está mascarado, para a tela não confundir
    # "não tem telefone" com "você não pode ver o telefone". São coisas
    # diferentes: a primeira impede responder, a segunda não.
    phoneMasked: bool = False
    bsuid: Optional[str] = None
    parentBsuid: Optional[str] = None
    username: Optional[str] = None
    addressable: bool


class WhatsAppContactRead(CamelCaseSchema):
    id: UUID
    whatsappConnectionId: UUID
    # Opcional desde 2026: o usuário que adota username tem o telefone omitido
    # pela Meta. Enquanto isto era `str`, um único contato sem número derrubava
    # a serialização da LISTA INTEIRA de contatos — a tela não abriria mais.
    phone: Optional[str] = None
    # `user_id` no webhook. Identificador estável dentro do portfólio.
    bsuid: Optional[str] = None
    parentBsuid: Optional[str] = None
    # Exibição apenas. O `@` nunca endereça: por ele só o cliente encontra a
    # empresa, jamais o contrário.
    username: Optional[str] = None
    name: Optional[str]
    avatarUrl: Optional[str]
    matterId: Optional[UUID]
    lastSeen: Optional[datetime]
    isBlocked: bool
    unreadCount: int = 0
    lastMessage: Optional[str] = None
    lastMessageTime: Optional[datetime] = None
    assignedUserId: Optional[UUID] = None
    assignedUserName: Optional[str] = None
    queueStatus: WaQueueStatus = WaQueueStatus.ai
    humanControlState: HumanControlState = HumanControlState.copilot
    activeFlowTemplateId: Optional[str] = None
    activeFlowLabel: Optional[str] = None
    nextFollowUpAt: Optional[datetime] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def addressable(self) -> bool:
        """Se dá para INICIAR conversa com este contato.

        Precisa de telefone ou BSUID. O username não conta, e essa é a
        pegadinha da mudança de 2026: um contato pode ter `@` visível e mesmo
        assim ser inalcançável, porque pelo `@` apenas o cliente encontra a
        empresa.

        Existe para a tela dizer isso ANTES da tentativa. Hoje o advogado
        descobre pela falha do envio, que é tarde.
        """
        return bool(self.phone or self.bsuid)
    sensitiveDataHidden: bool = False

# ── Mensagens ─────────────────────────────────────────────────────────────

class WhatsAppMessageRead(CamelCaseSchema):
    id: UUID
    contactId: UUID
    whatsappConnectionId: UUID
    wamid: Optional[str]
    direction: WaMessageDirection
    status: WaMessageStatus
    body: str
    mediaUrl: Optional[str]
    mediaType: Optional[str]
    transcript: Optional[str] = None
    imageTranscript: Optional[str] = None
    sentById: Optional[UUID]
    timestamp: datetime
    deliveredAt: Optional[datetime]
    readAt: Optional[datetime]
    messageMetadata: Dict[str, Any] = Field(default_factory=dict)
    sensitiveDataHidden: bool = False


class InboundReconciliationDecision(StrEnum):
    delivered = "delivered"
    not_delivered = "not_delivered"


class InboundReconciliationResolveRequest(WriteSchema):
    decision: InboundReconciliationDecision
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("reason must contain at least 3 non-whitespace characters")
        return normalized


class InboundReconciliationRead(CamelCaseSchema):
    message_id: UUID
    decision: InboundReconciliationDecision
    stage: str
    state: str
    resumed: bool
    resolved_at: datetime


def _normalize_outbound_idempotency_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) < 8:
        raise ValueError("idempotency_key must contain at least 8 non-whitespace characters")
    return normalized


class SendMessageRequest(WriteSchema):
    """Payload para enviar uma mensagem de texto ao contato."""
    contact_id: UUID
    connection_id: Optional[UUID] = None
    body: str = Field(..., min_length=1, max_length=4096)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_outbound_idempotency_key(value)


class SendDocumentRequest(WriteSchema):
    contact_id: UUID
    connection_id: Optional[UUID] = None
    url: str = Field(..., min_length=1, max_length=2048)
    filename: str = Field(..., min_length=1, max_length=255)
    caption: Optional[str] = Field(default=None, max_length=1024)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_outbound_idempotency_key(value)


class SendStructuredMessageRequest(WriteSchema):
    contact_id: UUID
    connection_id: Optional[UUID] = None
    message_payload: Dict[str, Any]
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=160)

    @field_validator("message_payload")
    @classmethod
    def normalize_message_payload(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.whatsapp.cloud_payload import normalize_internal_payload

        return normalize_internal_payload(value)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_outbound_idempotency_key(value)


class StartWhatsAppConversationRequest(WriteSchema):
    """Inicia uma conversa oficial fora da janela usando template aprovado."""

    connection_id: UUID
    meta_template_id: UUID
    phone: str = Field(..., min_length=8, max_length=32)
    contact_name: str | None = Field(default=None, max_length=255)
    header_parameters: list[str] = Field(default_factory=list, max_length=20)
    body_parameters: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        normalized = normalize_phone_number(value)
        if normalized is None:
            raise ValueError(
                "Informe DDI, DDD e número válidos. Exemplo aceito pela Meta: 5561999999999"
            )
        return normalized

    @field_validator("contact_name")
    @classmethod
    def normalize_contact_name(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None

    @field_validator("header_parameters", "body_parameters")
    @classmethod
    def validate_template_parameters(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 1024 for value in normalized):
            raise ValueError("template parameters must contain 1 to 1024 characters")
        return normalized


class WhatsAppConnectionStatusRead(CamelCaseSchema):
    id: UUID
    status: WaConnectionStatus
    connectionType: ConnectionType
    transportMode: str
    qrStatus: Optional[str] = None
    webhookRegistered: bool
    lastError: Optional[str] = None
    connectedAt: Optional[datetime] = None
    disconnectedAt: Optional[datetime] = None
    displayPhoneNumber: Optional[str] = None
    phoneNumber: Optional[str] = None
    phoneNumberId: Optional[str] = None
    instanceId: Optional[str] = None


class WhatsAppConnectionHealthRead(CamelCaseSchema):
    """
    Painel de saúde do número (W5.6): tier de envio, quota diária (janela
    rolante 24h), quality rating e estágio de rampa. `syncedFromMeta` e
    `honest` deixam explícito quando os dados são apenas locais, enquanto
    a sincronização real com a Meta estiver com o gate fechado.
    """
    connectionId: UUID
    tier: WhatsAppConnectionTier
    dailyLimit: Optional[int] = None
    dailyUsed: int
    dailyRemaining: Optional[int] = None
    windowStartedAt: datetime
    qualityRating: Optional[str] = None
    rampUpStage: int
    lastSyncedAt: Optional[datetime] = None
    syncedFromMeta: bool
    syncMessage: str
    honest: bool = True


class SendMessageResponse(CamelCaseSchema):
    messageId: UUID
    wamid: Optional[str]
    status: WaMessageStatus
    idempotencyKey: str


# ── Conversa (lista de mensagens de um contato) ───────────────────────────

class ConversationRead(CamelCaseSchema):
    contact: WhatsAppContactRead
    messages: List[WhatsAppMessageRead]


class ConversationAssigneeRead(CamelCaseSchema):
    id: UUID
    name: str
    email: str
    phone: Optional[str] = None
    role: str


class ConversationAssignRequest(WriteSchema):
    user_id: Optional[UUID] = None


class ConversationHandoffRequest(WriteSchema):
    queue_status: WaQueueStatus
    human_control_state: HumanControlState = HumanControlState.copilot


class ApplyFlowRequest(WriteSchema):
    template_id: UUID
    custom_name: Optional[str] = None
    move_to_follow_up: bool = False
    next_follow_up_at: Optional[datetime] = None


class ScheduleConversationMessageRequest(WriteSchema):
    connection_id: Optional[UUID] = None
    scheduled_at: datetime
    body: str = Field(..., min_length=1, max_length=4096)


class ScheduleConversationMeetingRequest(WriteSchema):
    title: str = Field(..., min_length=1, max_length=255)
    starts_at: datetime
    ends_at: Optional[datetime] = None
    responsible_user_id: Optional[UUID] = None
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    notes: Optional[str] = None


class ConversationDocumentSendRequest(WriteSchema):
    connection_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    url: Optional[str] = None
    filename: Optional[str] = None
    caption: Optional[str] = None


class MessageTemplateRead(CamelCaseSchema):
    id: str
    source: str
    kind: str
    category: str
    label: str
    body: str
    format: str = "text"
    favorite: bool = False
    title: Optional[str] = None
    footer: Optional[str] = None


class MessageTemplateUpdate(WriteSchema):
    id: Optional[str] = None
    kind: str = "message"
    category: str = "geral"
    label: str
    body: str
    format: str = "text"
    favorite: bool = False
    title: Optional[str] = None
    footer: Optional[str] = None

    @field_validator("body", "title", "footer")
    @classmethod
    def validate_safe_placeholders(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            from app.core.template_renderer import validate_template

            validate_template(value)
        return value


class MessageTemplateSettingsRead(CamelCaseSchema):
    system_templates: List[MessageTemplateRead] = Field(default_factory=list)
    workspace_templates: List[MessageTemplateRead] = Field(default_factory=list)
    proposal_templates: List[MessageTemplateRead] = Field(default_factory=list)
    contract_templates: List[MessageTemplateRead] = Field(default_factory=list)


class MessageTemplateSettingsUpdate(WriteSchema):
    workspace_templates: List[MessageTemplateUpdate] = Field(default_factory=list)
    proposal_templates: List[MessageTemplateUpdate] = Field(default_factory=list)
    contract_templates: List[MessageTemplateUpdate] = Field(default_factory=list)


class SendProposalRequest(WriteSchema):
    connection_id: Optional[UUID] = None
    template_id: str
    format: Optional[str] = None
    custom_message: Optional[str] = None


class SendContractRequest(WriteSchema):
    connection_id: Optional[UUID] = None
    template_id: str
    auth_mode: Optional[str] = None
    custom_message: Optional[str] = None


# ── Webhook Meta (payload de entrada) ────────────────────────────────────

class MetaWebhookPayload(BaseModel):
    """Payload bruto recebido do webhook da Meta."""
    object: str
    entry: List[Dict[str, Any]]
