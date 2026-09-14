"""Public KLM contracts for native capabilities and product experiences."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, SecretStr, field_validator, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema

KLMNativeDomain = Literal["communications", "collaboration", "automation", "crm"]
KLMFunctionMaturity = Literal[
    "discovered",
    "mapped",
    "native_shadow",
    "native_primary",
    "native_verified",
    "blocked_proprietary",
    "blocked_license",
]
KLMCapabilityDisposition = Literal[
    "discovered",
    "approved_native",
    "approved_embedded",
    "approved_full_app",
    "implemented",
    "not_applicable",
    "blocked_license",
    "blocked_dependency",
    "blocked_external_gate",
]
KLMCapabilityMode = Literal[
    "local_canonical",
    "local_equivalent",
    "local_reconciled",
    "local_command",
    "controlled_configuration",
    "responsive_substitution",
]
KLMExecutionMode = Literal[
    "disabled",
    "local_native",
    "external_equivalent",
    "enterprise_equivalent",
]

_SENSITIVE_PUBLIC_KEYS = {
    "authorization",
    "backupcode",
    "clientsecret",
    "credential",
    "onetimecode",
    "otpcode",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "token",
    "verificationcode",
}
_SENSITIVE_PUBLIC_KEY_FRAGMENTS = (
    "accesskey",
    "accesstoken",
    "apikey",
    "clientsecret",
    "otpcode",
    "privatekey",
    "refreshtoken",
    "secretkey",
    "sessiontoken",
)


def _contains_sensitive_key(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if (
                normalized in _SENSITIVE_PUBLIC_KEYS
                or normalized.endswith("token")
                or any(fragment in normalized for fragment in _SENSITIVE_PUBLIC_KEY_FRAGMENTS)
                or _contains_sensitive_key(child)
            ):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_key(child) for child in value)
    return False


class KLMStrictRead(CamelCaseSchema):
    model_config = ConfigDict(extra="forbid")


class KLMStrictWrite(WriteSchema):
    model_config = ConfigDict(extra="forbid")


class KLMCapabilityFunctionRead(KLMStrictRead):
    id: str = Field(pattern=r"^fu_[0-9a-f]{24}$")
    domain: KLMNativeDomain
    name: str
    category: str
    capability: str
    maturity: KLMFunctionMaturity
    disposition: KLMFunctionMaturity
    summary: str


class KLMCapabilityFunctionSummaryRead(KLMStrictRead):
    total: int = Field(ge=0)
    available: int = Field(ge=0)
    blocked: int = Field(ge=0)
    verified: int = Field(ge=0)
    remaining: int = Field(ge=0)
    by_domain: dict[KLMNativeDomain, int]
    by_category: dict[str, int]
    by_disposition: dict[str, int]


class KLMCapabilityFunctionFiltersRead(KLMStrictRead):
    domains: list[KLMNativeDomain]
    categories: list[str]
    dispositions: list[KLMFunctionMaturity]
    capabilities: list[str]


class KLMCapabilityFunctionListRead(KLMStrictRead):
    schema_version: Literal["1.0"] = "1.0"
    scope: Literal["klm-capability-functions"] = "klm-capability-functions"
    summary: KLMCapabilityFunctionSummaryRead
    filters: KLMCapabilityFunctionFiltersRead
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)
    items: list[KLMCapabilityFunctionRead]


class KLMCapabilityFunctionRuntimeStateRead(KLMStrictRead):
    maturity: KLMFunctionMaturity
    available_modes: list[Literal["dry-run", "execute", "configure"]]
    audit_event_count: int = Field(ge=0)
    last_activity_at: datetime | None = None
    external_effects_enabled: Literal[False] = False


class KLMCapabilityFunctionRuntimeRead(KLMStrictRead):
    function: KLMCapabilityFunctionRead
    state: KLMCapabilityFunctionRuntimeStateRead


class KLMCapabilityFunctionActionWrite(KLMStrictWrite):
    mode: Literal["dry-run", "execute", "configure"] = "dry-run"
    idempotency_key: str = Field(min_length=8, max_length=180)
    resource_id: str | None = Field(default=None, max_length=180)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class KLMCapabilityFunctionActionRead(KLMStrictRead):
    status: Literal["succeeded", "configured", "simulated", "blocked_external"]
    replayed: bool
    external_effect_executed: Literal[False] = False
    summary: str


class KLMCapabilityRead(KLMStrictRead):
    id: str
    domain: KLMNativeDomain
    key: str
    label: str
    category: str
    description: str
    disposition: KLMCapabilityDisposition
    exposure: Literal["none", "bridge", "native", "microfrontend", "full_app"]
    mode: KLMCapabilityMode
    surface: str
    related_routes: list[str] = Field(default_factory=list)
    native_route: str
    contract_eligible: bool
    verified: bool
    external_effect_verified: bool
    reason: str


class KLMCapabilityCatalogSummaryRead(KLMStrictRead):
    total: int = Field(ge=0)
    available: int = Field(ge=0)
    verified: int = Field(ge=0)
    external_effect_verified: int = Field(ge=0)
    by_domain: dict[KLMNativeDomain, int]
    by_disposition: dict[str, int]


class KLMCapabilityVerificationRead(KLMStrictRead):
    state: Literal["verified", "partial", "unverified"]
    reason: str
    checks: int = Field(ge=0)


class KLMCapabilityCatalogRead(KLMStrictRead):
    schema_version: str
    scope: Literal["klm-native-capabilities"] = "klm-native-capabilities"
    capabilities: list[KLMCapabilityRead]
    summary: KLMCapabilityCatalogSummaryRead
    verification: KLMCapabilityVerificationRead


class KLMCapabilityConfigWrite(KLMStrictWrite):
    expected_version: int = Field(ge=0)
    configuration: dict[str, JsonValue] | None = None
    execution_mode: KLMExecutionMode | None = None

    @model_validator(mode="after")
    def require_change(self) -> KLMCapabilityConfigWrite:
        if self.configuration is None and self.execution_mode is None:
            raise ValueError("Informe configuration ou executionMode.")
        return self


class KLMCapabilityCommandWrite(KLMStrictWrite):
    command_type: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_.:-]*$")
    idempotency_key: str = Field(min_length=8, max_length=180)
    expected_version: int = Field(ge=0)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class KLMCapabilityDocumentActionWrite(KLMStrictWrite):
    action_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_.:-]*$")
    values: dict[str, JsonValue] = Field(default_factory=dict)
    transient_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    mode: Literal["execute", "dry-run", "configure"] = "execute"
    version: str | int = "0"
    idempotency_key: str = Field(min_length=8, max_length=180)
    context: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("values", "context")
    @classmethod
    def reject_durable_credentials(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if _contains_sensitive_key(value):
            raise ValueError("Credenciais devem usar exclusivamente o canal transitório.")
        return value


class KLMCapabilityStateRead(KLMStrictRead):
    domain: KLMNativeDomain
    capability: str
    configured: bool
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    config_version: int = Field(ge=0)
    execution_mode: KLMExecutionMode
    updated_at: datetime | None = None


class KLMCapabilityCommandRead(KLMStrictRead):
    id: str
    domain: KLMNativeDomain
    capability: str
    command_type: str
    status: str
    effect_state: str
    is_success: bool
    is_terminal: bool
    requires_reconciliation: bool
    deduplicated: bool
    result: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    external_network_allowed: bool = False
    created_at: datetime
    updated_at: datetime


class KLMCapabilityAuditRead(KLMStrictRead):
    id: str
    action: str
    summary: str
    occurred_at: datetime


class KLMCapabilitySnapshotRead(KLMStrictRead):
    state: KLMCapabilityStateRead
    commands: list[KLMCapabilityCommandRead] = Field(default_factory=list)
    audit_trail: list[KLMCapabilityAuditRead] = Field(default_factory=list)


class KLMDocumentDomainRead(KLMStrictRead):
    id: KLMNativeDomain
    label: str


class KLMDocumentCapabilityRead(KLMStrictRead):
    id: str
    label: str
    description: str | None = None
    category: str | None = None


class KLMDocumentHealthRead(KLMStrictRead):
    state: str
    label: str | None = None
    message: str | None = None
    last_sync_at: str | None = None
    latency_ms: float | None = None


class KLMDocumentAccessRead(KLMStrictRead):
    state: str
    reason: str | None = None


class KLMDocumentColumnRead(KLMStrictRead):
    key: str
    label: str
    format: str | None = None
    width: str | None = None
    hidden_on_mobile: bool = False


class KLMDocumentChartPointRead(KLMStrictRead):
    id: str
    label: str
    value: float
    display_value: str | None = None
    format: str | None = None
    detail: str | None = None


class KLMDocumentSectionRead(KLMStrictRead):
    id: str
    type: str
    title: str
    description: str | None = None
    action_ids: list[str] = Field(default_factory=list)
    columns: list[KLMDocumentColumnRead] = Field(default_factory=list)
    rows: list[dict[str, JsonValue]] = Field(default_factory=list)
    data: JsonValue | None = None
    fields: list[KLMDocumentColumnRead] = Field(default_factory=list)
    items: list[JsonValue] = Field(default_factory=list)
    points: list[KLMDocumentChartPointRead] = Field(default_factory=list)
    tone: str | None = None
    body: str | None = None
    id_field: str | None = None
    searchable: bool = False
    search_keys: list[str] = Field(default_factory=list)
    page_size: int | None = Field(default=None, ge=1)
    row_action_ids: list[str] = Field(default_factory=list)
    title_field: str | None = None
    description_field: str | None = None
    status_field: str | None = None


class KLMDocumentTabRead(KLMStrictRead):
    id: str
    label: str
    description: str | None = None
    count: int | None = Field(default=None, ge=0)
    sections: list[KLMDocumentSectionRead] = Field(default_factory=list)


class KLMDocumentKPIRead(KLMStrictRead):
    id: str
    label: str
    value: JsonValue
    display_value: str | None = None
    format: str | None = None
    delta: str | float | None = None
    trend: Literal["up", "down", "neutral"] = "neutral"
    description: str | None = None


class KLMDocumentActionOptionRead(KLMStrictRead):
    value: JsonValue
    label: str
    description: str | None = None


class KLMDocumentActionConditionRead(KLMStrictRead):
    field: str
    equals: JsonValue | None = None
    not_equals: JsonValue | None = None


class KLMDocumentActionFieldRead(KLMStrictRead):
    name: str
    label: str
    type: str
    description: str | None = None
    placeholder: str | None = None
    required: bool = False
    read_only: bool = False
    default_value: JsonValue | None = None
    options: list[KLMDocumentActionOptionRead] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)
    pattern: str | None = None
    context_key: str | None = None
    one_of_group: str | None = None
    visible_when: KLMDocumentActionConditionRead | None = None


class KLMDocumentPermissionRequirementRead(KLMStrictRead):
    module: str
    action: str


class KLMDocumentPermissionRead(KLMStrictRead):
    allowed: bool
    reason: str | None = None
    required: list[KLMDocumentPermissionRequirementRead] = Field(default_factory=list)


class KLMDocumentGateRead(KLMStrictRead):
    state: str
    reason: str | None = None
    allow_dry_run: bool = False
    allow_configure: bool = False


class KLMDocumentConfirmationRead(KLMStrictRead):
    title: str | None = None
    body: str | None = None
    confirm_label: str | None = None


class KLMDocumentActionRead(KLMStrictRead):
    id: str
    label: str
    description: str | None = None
    intent: str = "secondary"
    method: str = "POST"
    execution: str = "local"
    fields: list[KLMDocumentActionFieldRead] = Field(default_factory=list)
    permission: KLMDocumentPermissionRead | None = None
    gate: KLMDocumentGateRead | None = None
    supports_dry_run: bool = False
    confirmation: KLMDocumentConfirmationRead | None = None


class KLMDocumentAuditRead(KLMStrictRead):
    id: str
    title: str
    description: str | None = None
    timestamp: str | None = None
    actor: str | None = None
    status: str | None = None


class KLMDocumentNavigationRead(KLMStrictRead):
    label: str
    url: str
    available: bool


class KLMDocumentAvailabilityRead(KLMStrictRead):
    state: str
    reconciled: bool
    diagnostics_available: bool


class KLMCapabilityDocumentRead(KLMStrictRead):
    schema_version: str
    domain: KLMDocumentDomainRead
    capability: KLMDocumentCapabilityRead
    health: KLMDocumentHealthRead
    access: KLMDocumentAccessRead
    version: str | int
    updated_at: str | None = None
    kpis: list[KLMDocumentKPIRead] = Field(default_factory=list)
    tabs: list[KLMDocumentTabRead] = Field(default_factory=list)
    actions: list[KLMDocumentActionRead] = Field(default_factory=list)
    audit: list[KLMDocumentAuditRead] = Field(default_factory=list)
    navigation: KLMDocumentNavigationRead | None = None
    availability: KLMDocumentAvailabilityRead | None = None


class KLMCapabilityActionRead(KLMStrictRead):
    status: str
    message: str
    version: str | int
    document: KLMCapabilityDocumentRead | None = None
    result: JsonValue = None
    replayed: bool = False
    refresh_pending: bool = False


class KLMCommunicationRoutingWrite(KLMStrictWrite):
    status: Literal["open", "resolved", "pending", "snoozed"] | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    assignee_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    labels: list[str] | None = Field(default=None, max_length=50)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [label.strip() for label in value]
        if any(not label or len(label) > 100 for label in normalized):
            raise ValueError("Cada label deve conter de 1 a 100 caracteres.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Labels duplicadas não são permitidas.")
        return normalized

    @model_validator(mode="after")
    def validate_change(self) -> KLMCommunicationRoutingWrite:
        if not self.model_fields_set:
            raise ValueError("Informe ao menos uma alteração de roteamento.")
        return self


class KLMNamedNumericRead(KLMStrictRead):
    id: int
    name: str


class KLMCommunicationConversationRead(KLMStrictRead):
    id: str
    status: str
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    assignee: KLMNamedNumericRead | None = None
    team: KLMNamedNumericRead | None = None
    inbox: KLMNamedNumericRead | None = None
    external_network_executed: Literal[False] = False


class KLMCommunicationLabelRead(KLMStrictRead):
    id: int
    title: str


class KLMCommunicationAgentRead(KLMStrictRead):
    id: int
    name: str
    role: str
    availability: str


class KLMCommunicationResponseRead(KLMStrictRead):
    id: int
    short_code: str
    content: str


class KLMCommunicationProjectionRead(KLMStrictRead):
    total_messages: int = Field(ge=0)
    projected_messages: int = Field(ge=0)
    received_messages: int = Field(ge=0)
    pending_messages: int = Field(ge=0)
    failed_messages: int = Field(ge=0)
    last_synced_at: str | None = None
    external_channel_delivery_enabled: Literal[False] = False


class KLMCommunicationThreadRead(KLMStrictRead):
    linked: bool
    thread_id: str
    conversation_id: str | None = None
    conversation: KLMCommunicationConversationRead | None = None
    inboxes: list[KLMNamedNumericRead] = Field(default_factory=list)
    teams: list[KLMNamedNumericRead] = Field(default_factory=list)
    labels: list[KLMCommunicationLabelRead] = Field(default_factory=list)
    agents: list[KLMCommunicationAgentRead] = Field(default_factory=list)
    canned_responses: list[KLMCommunicationResponseRead] = Field(default_factory=list)
    projection: KLMCommunicationProjectionRead


class KLMCommunicationSyncRead(KLMCommunicationProjectionRead):
    status: Literal["synced"]
    thread_id: str
    conversation_id: str
    projected_now: int = Field(ge=0)
    contact_created: bool
    rate_limited: bool


class KLMCollaborationRoomRead(KLMStrictRead):
    id: str
    name: str
    display_name: str


class KLMCollaborationMemberRead(KLMStrictRead):
    id: str
    name: str
    username: str
    status: str
    role: str


class KLMCollaborationAttachmentRead(KLMStrictRead):
    name: str
    media_type: str


class KLMCollaborationReactionRead(KLMStrictRead):
    emoji: str
    count: int = Field(ge=0)


class KLMCollaborationAuthorRead(KLMStrictRead):
    id: str
    name: str
    username: str


class KLMCollaborationMessageRead(KLMStrictRead):
    id: str
    body: str
    author: KLMCollaborationAuthorRead
    occurred_at: str | None = None
    attachments: list[KLMCollaborationAttachmentRead] = Field(default_factory=list)
    reactions: list[KLMCollaborationReactionRead] = Field(default_factory=list)
    reply_count: int = Field(default=0, ge=0)


class KLMCollaborationDiscussionRead(KLMStrictRead):
    id: str
    name: str
    message_count: int = Field(ge=0)
    created_at: str | None = None
    updated_at: str | None = None


class KLMCollaborationThreadRead(KLMStrictRead):
    linked: bool
    thread_id: str
    room_id: str | None = None
    room_type: str | None = None
    room: KLMCollaborationRoomRead | None = None
    members: list[KLMCollaborationMemberRead] = Field(default_factory=list)
    messages: list[KLMCollaborationMessageRead] = Field(default_factory=list)
    discussions: list[KLMCollaborationDiscussionRead] = Field(default_factory=list)
    sensitive_data_hidden: bool


class KLMCollaborationSyncRead(KLMStrictRead):
    status: Literal["synced"]
    thread_id: str
    room_id: str
    messages_seen: int = Field(ge=0)
    messages_projected: int = Field(ge=0)
    external_network_executed: Literal[False] = False


class KLMAutomationAgentRead(KLMStrictRead):
    id: str
    name: str
    display_name: str
    description: str
    is_active: bool
    state: str
    quality_score: float
    queue_size: int = Field(ge=0)
    transfer_to_human: bool


class KLMAutomationWorkflowRead(KLMStrictRead):
    id: str
    name: str
    description: str | None = None
    status: str
    revision: int = Field(ge=0)
    node_count: int = Field(ge=0)
    connection_count: int = Field(ge=0)
    run_count: int = Field(ge=0)


class KLMAutomationAnalyticsRead(KLMStrictRead):
    runs: int = Field(ge=0)
    by_status: dict[str, int]
    external_actions_executed: Literal[0] = 0


class KLMAutomationPerformanceRead(KLMStrictRead):
    id: str
    name: str
    quality_score: float
    queue_size: int = Field(ge=0)
    total_chats: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    waiting_human_runs: int = Field(ge=0)


class KLMAutomationPersonRead(KLMStrictRead):
    id: str
    name: str
    stage: str
    active_matters: int = Field(ge=0)


class KLMAutomationPeopleRead(KLMStrictRead):
    items: list[KLMAutomationPersonRead] = Field(default_factory=list)
    total: int = Field(ge=0)


class KLMAutomationPeopleStatsRead(KLMStrictRead):
    total_people: int = Field(ge=0)
    active_matters: int = Field(ge=0)
    new_leads_7d: int = Field(ge=0)


class KLMAutomationToolRead(KLMStrictRead):
    id: int
    name: str
    status: str
    external: Literal[False] = False


class KLMAutomationGroupRead(KLMStrictRead):
    id: str
    name: str
    member_count: int = Field(ge=0)


class KLMAutomationNotificationRead(KLMStrictRead):
    id: str
    title: str
    message: str
    created_at: str | None = None


class KLMAutomationKnowledgeItemRead(KLMStrictRead):
    id: str
    name: str
    document_type: str
    title: str
    summary: str
    matter_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class KLMAutomationKnowledgeRead(KLMStrictRead):
    items: list[KLMAutomationKnowledgeItemRead] = Field(default_factory=list)
    total: int = Field(ge=0)
    queue_size: int = Field(default=0, ge=0)


class KLMAutomationContextRead(KLMStrictRead):
    linked: bool
    agents: list[KLMAutomationAgentRead] = Field(default_factory=list)
    workflows: list[KLMAutomationWorkflowRead] = Field(default_factory=list)
    analytics: KLMAutomationAnalyticsRead
    performance: list[KLMAutomationPerformanceRead] = Field(default_factory=list)
    people: KLMAutomationPeopleRead
    people_stats: KLMAutomationPeopleStatsRead
    tools: list[KLMAutomationToolRead] = Field(default_factory=list)
    groups: list[KLMAutomationGroupRead] = Field(default_factory=list)
    notifications: list[KLMAutomationNotificationRead] = Field(default_factory=list)
    knowledge: KLMAutomationKnowledgeRead
    time_range: Literal["24h", "7d", "30d", "90d"]
    sensitive_data_hidden: bool
    external_execution_enabled: Literal[False] = False


class KLMAutomationSyncRead(KLMStrictRead):
    status: Literal["synced"]
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    adopted: int = Field(ge=0)
    conflicts: int = Field(ge=0)
    seen: int = Field(ge=0)


class KLMFlowContextRead(KLMStrictRead):
    linked: bool
    flow_id: str
    revision: int = Field(ge=0)
    workflow_status: str | None = None
    node_count: int = Field(ge=0)
    connection_count: int = Field(ge=0)
    digest_matches: bool
    graph_matches: bool
    stale_link: bool
    external_execution_enabled: Literal[False] = False


class KLMFlowSyncRead(KLMStrictRead):
    status: Literal["synced"]
    flow_id: str
    graph_action: Literal["replaced", "unchanged"]
    node_count: int = Field(ge=0)
    connection_count: int = Field(ge=0)
    revision: int = Field(ge=0)
    external_execution_enabled: Literal[False] = False


class KLMCRMEntityCountsRead(KLMStrictRead):
    contacts: int = Field(ge=0)
    leads: int = Field(ge=0)
    opportunities: int = Field(ge=0)
    cases: int = Field(ge=0)
    tasks: int = Field(ge=0)
    finance_entries: int = Field(ge=0)


class KLMCRMProjectionRead(KLMStrictRead):
    linked: bool
    total_records: int = Field(ge=0)
    available_records: int = Field(ge=0)
    failed_records: int = Field(ge=0)
    stale_records: int = Field(ge=0)
    by_entity_type: KLMCRMEntityCountsRead
    updated_at: str | None = None
    external_execution_enabled: Literal[False] = False


class KLMCRMItemRead(KLMStrictRead):
    id: str
    entity_type: str
    name: str
    stage: str | None = None
    status: str | None = None
    priority: str | None = None
    amount: float | None = None
    currency: str = "BRL"
    probability: int | None = Field(default=None, ge=0, le=100)
    days_in_stage: int | None = Field(default=None, ge=0)
    aging_status: str | None = None
    due_date: str | None = None
    next_follow_up: str | None = None
    overdue: bool = False
    sla_breached: bool = False
    escalation_count: int = Field(default=0, ge=0)


class KLMCRMRecordRead(KLMCRMItemRead):
    sync_state: str = "synced"
    last_synced_at: str | None = None


class KLMCRMColumnRead(KLMStrictRead):
    id: str
    name: str
    order: int = Field(ge=0)
    color: str | None = None
    stage_type: str | None = None
    wip_limit: int | None = Field(default=None, ge=0)
    item_count: int = Field(ge=0)
    items: list[KLMCRMItemRead] = Field(default_factory=list)


class KLMCRMFunnelCountsRead(KLMStrictRead):
    accounts: int = Field(ge=0)
    contacts: int = Field(ge=0)
    leads: int = Field(ge=0)
    opportunities: int = Field(ge=0)


class KLMCRMUrgentCountsRead(KLMStrictRead):
    overdue_tasks: int = Field(ge=0)
    followups_today: int = Field(ge=0)
    hot_leads: int = Field(ge=0)


class KLMCRMPipelineStageRead(KLMStrictRead):
    id: str
    label: str
    count: int = Field(ge=0)
    value: float


class KLMCRMRevenueRead(KLMStrictRead):
    total_pipeline: float
    weighted_pipeline: float
    conversion_rate: float
    currency: str


class KLMCRMAgingConfigRead(KLMStrictRead):
    stage: str
    expected_days: int = Field(ge=0)
    warning_days: int | None = Field(default=None, ge=0)


class KLMCRMGoalRead(KLMStrictRead):
    id: str
    name: str
    target: float
    achieved: float
    percent: float
    active: bool


class KLMCRMFunnelRead(KLMStrictRead):
    projection: KLMCRMProjectionRead
    counts: KLMCRMFunnelCountsRead
    urgent_counts: KLMCRMUrgentCountsRead
    pipeline: list[KLMCRMPipelineStageRead] = Field(default_factory=list)
    revenue: KLMCRMRevenueRead
    opportunity_columns: list[KLMCRMColumnRead] = Field(default_factory=list)
    lead_columns: list[KLMCRMColumnRead] = Field(default_factory=list)
    case_columns: list[KLMCRMColumnRead] = Field(default_factory=list)
    goals: list[KLMCRMGoalRead] = Field(default_factory=list)
    aging_config: list[KLMCRMAgingConfigRead] = Field(default_factory=list)
    notifications_unread: int = Field(ge=0)
    sensitive_data_hidden: bool


class KLMCRMMatterRead(KLMStrictRead):
    projection: KLMCRMProjectionRead
    matter_id: str
    records: list[KLMCRMRecordRead] = Field(default_factory=list)
    sensitive_data_hidden: bool


class KLMCRMBoardRead(KLMStrictRead):
    id: str
    name: str
    description: str | None = None
    column_count: int = Field(ge=0)


class KLMCRMWorkRead(KLMStrictRead):
    projection: KLMCRMProjectionRead
    task_columns: list[KLMCRMColumnRead] = Field(default_factory=list)
    tasks: list[KLMCRMItemRead] = Field(default_factory=list)
    boards: list[KLMCRMBoardRead] = Field(default_factory=list)
    notifications_unread: int = Field(ge=0)
    sensitive_data_hidden: bool


class KLMCRMFinanceItemRead(KLMStrictRead):
    id: str
    entity_type: str
    name: str
    status: str | None = None
    amount: float | None = None
    amount_due: float | None = None
    currency: str
    issue_date: str | None = None
    due_date: str | None = None
    valid_until: str | None = None
    category: str | None = None
    active: bool


class KLMCRMFinanceSummaryRead(KLMStrictRead):
    total_invoiced: float
    total_paid: float
    total_due: float
    currency: str


class KLMCRMAmountRead(KLMStrictRead):
    count: int = Field(ge=0)
    amount: float


class KLMCRMActivityRead(KLMStrictRead):
    entries: int = Field(ge=0)
    paid: int = Field(ge=0)
    pending: int = Field(ge=0)


class KLMCRMEstimateSummaryRead(KLMStrictRead):
    count: int = Field(ge=0)
    active: int = Field(ge=0)


class KLMCRMAgingRead(KLMStrictRead):
    bucket: str
    count: int = Field(ge=0)
    amount: float


class KLMCRMRevenueSeriesRead(KLMStrictRead):
    period: str | None = None
    revenue: float
    count: int = Field(ge=0)


class KLMCRMFinanceRead(KLMStrictRead):
    projection: KLMCRMProjectionRead
    summary: KLMCRMFinanceSummaryRead
    status_counts: dict[str, int]
    overdue: KLMCRMAmountRead
    recent_activity: KLMCRMActivityRead
    estimate_summary: KLMCRMEstimateSummaryRead
    invoices: list[KLMCRMFinanceItemRead] = Field(default_factory=list)
    estimates: list[KLMCRMFinanceItemRead] = Field(default_factory=list)
    products: list[KLMCRMFinanceItemRead] = Field(default_factory=list)
    aging: list[KLMCRMAgingRead] = Field(default_factory=list)
    revenue_series: list[KLMCRMRevenueSeriesRead] = Field(default_factory=list)
    notifications_unread: int = Field(ge=0)
    sensitive_data_hidden: bool
