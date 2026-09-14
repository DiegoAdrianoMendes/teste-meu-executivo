"""Contratos da API operacional KLM consumidos pela SPA."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.core.tenant_cost_privacy import strip_internal_provider_cost_fields
from app.models.conversation_collaboration import (
    ConversationMentionType,
    ConversationReferenceType,
)
from app.models.klm_operations import (
    AreaBlueprintStatus,
    ExternalActionIntentStatus,
    FollowUpStatus,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    TechnicalRequestSeverity,
    TechnicalRequestStatus,
    ThreadStatus,
)
from app.models.matter import KanbanColumnId, MicroStatus
from app.schemas.agent_runtime import AgentRunRead
from app.schemas.base import CamelCaseSchema, WriteSchema


class RuntimeStatusRead(CamelCaseSchema):
    mode: str
    local_only: bool
    local_infrastructure: bool
    external_actions_enabled: bool
    aws_enabled: bool
    model_apis_enabled: bool
    online_approved: bool
    local_bootstrap_enabled: bool
    providers: dict[str, str]


DiagnosticStatus = Literal["healthy", "degraded"]


class LocalDiagnosticCheckRead(CamelCaseSchema):
    key: str
    label: str
    status: DiagnosticStatus
    state: str
    detail: str
    checked_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)


class LocalDiagnosticSummaryRead(CamelCaseSchema):
    status: DiagnosticStatus
    total: int = Field(ge=0)
    healthy: int = Field(ge=0)
    degraded: int = Field(ge=0)
    unavailable: int = Field(ge=0)


class LocalDiagnosticsRead(CamelCaseSchema):
    run_id: UUID
    status: DiagnosticStatus
    checked_at: datetime
    summary: LocalDiagnosticSummaryRead
    checks: list[LocalDiagnosticCheckRead]


class LocalVerificationCleanupRead(CamelCaseSchema):
    artifacts_removed: int
    acquisition_events: int = 0
    external_intents: int = 0
    omnichannel_messages: int = 0
    email_receipts: int = 0
    agent_runs: int = 0
    approvals: int = 0
    audit_events: int = 0
    timeline_events: int = 0
    conversations: int = 0
    knowledge_chunks: int = 0
    documents: int = 0
    booking_links: int = 0
    meetings: int = 0
    flows: int = 0
    follow_ups: int = 0
    threads: int = 0
    matters: int = 0
    clients: int = 0
    identity_idempotency_records: int = 0
    auth_security_events: int = 0
    auth_rate_limit_events: int = 0
    auth_rate_limit_buckets: int = 0
    workspace_invitations: int = 0
    workspace_memberships: int = 0
    qa_users: int = 0
    identity_outbox_messages: int = 0
    restored_matters: int = 0


class LocalFinanceE2ESeedRequest(WriteSchema):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{4,40}$")
    record_count: int = Field(default=55, ge=51, le=120)


class LocalFinanceE2ESeedRead(CamelCaseSchema):
    run_id: str
    record_count: int
    cost_center_id: UUID
    client_id: UUID
    contract_id: UUID
    matter_id: UUID
    finance_entry_id: UUID
    search_prefix: str


class LocalAgentBudgetE2ESeedRequest(WriteSchema):
    deployment_id: UUID
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{4,40}$")


class LocalAgentBudgetE2ESeedRead(CamelCaseSchema):
    run_id: str
    deployment_id: UUID
    agent_run_id: UUID
    consumed_tokens: int = Field(ge=1)


class LocalSourceBindingRead(CamelCaseSchema):
    scope: Literal["klm-local-source-binding"]
    runtime_source_digest: str = Field(min_length=64, max_length=64)
    runtime_source_file_count: int = Field(ge=1)
    runtime_source_matches_current: bool


class AreaBlueprintTaxonomy(WriteSchema):
    subthemes: list[str] = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)

    @field_validator("subthemes", "required_fields")
    @classmethod
    def validate_entries(cls, value: list[str]) -> list[str]:
        normalized = [entry.strip() for entry in value]
        if any(not entry for entry in normalized):
            raise ValueError("entries must not be blank")
        if len({entry.casefold() for entry in normalized}) != len(normalized):
            raise ValueError("entries must be unique")
        return normalized


class AreaBlueprintGates(WriteSchema):
    human_request_agent: str = Field(min_length=1, max_length=100)
    human_handoff_coordinator: Literal["orquestrador"] = "orquestrador"

    @field_validator("human_handoff_coordinator", mode="before")
    @classmethod
    def migrate_legacy_handoff_coordinator(cls, value: Any) -> str:
        # Dados persistidos do modelo anterior usavam a secretaria como
        # coordenadora da fila humana; o modelo vigente centraliza no
        # orquestrador (mesmo padrão do PlaybookHandoffPolicy).
        if value == "secretaria":
            return "orquestrador"
        return str(value or "orquestrador")

    @field_validator("human_request_agent", mode="before")
    @classmethod
    def migrate_legacy_request_agent(cls, value: Any) -> str:
        if value in {"hermes", "secretaria"}:
            return "orquestrador"
        return str(value or "orquestrador")

    petition_always_requires_approval: Literal[True]
    external_actions_disabled: Literal[True]
    audio_requires_repeated_request: Literal[True]

    @field_validator("human_request_agent")
    @classmethod
    def validate_human_request_agent(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("human request agent must not be blank")
        return normalized


class AreaBlueprintProduct(WriteSchema):
    key: str = Field(min_length=2, max_length=100)
    label: str = Field(min_length=2, max_length=180)
    description: str = Field(min_length=2)
    keywords: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    specialist_agent_key: str = Field(min_length=2, max_length=100)
    closer_agent_key: str = Field(min_length=2, max_length=100)
    contract_template_key: str = Field(min_length=2, max_length=140)
    playbook_scope: str = Field(min_length=2, max_length=160)
    audience: str = Field(min_length=2, max_length=220)

    @field_validator("key", "specialist_agent_key", "closer_agent_key", "contract_template_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("key must not be blank")
        return normalized

    @field_validator("keywords", "required_documents")
    @classmethod
    def normalize_lists(cls, value: list[str]) -> list[str]:
        normalized = [entry.strip() for entry in value if entry.strip()]
        return list(dict.fromkeys(normalized))


class AreaBlueprintSubagent(WriteSchema):
    key: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=2, max_length=180)
    role: str = Field(min_length=2, max_length=180)
    parent_agent_key: str = Field(min_length=2, max_length=100)
    product_key: str | None = Field(default=None, min_length=2, max_length=100)
    description: str = Field(min_length=2)
    task_class: Literal["simple", "medium", "complex"]
    tools: list[str] = Field(default_factory=list)
    gate: str = Field(min_length=2)


class AreaBlueprintRouting(WriteSchema):
    coordination_agent_key: str = Field(min_length=2, max_length=100)
    owner_bridge_agent_key: str = Field(min_length=2, max_length=100)
    owner_bridge_delegator_key: Literal["secretaria"] = "secretaria"
    sdr_agent_key: str = Field(min_length=2, max_length=100)
    specialist_agent_key: str = Field(min_length=2, max_length=100)
    closer_agent_key: str = Field(min_length=2, max_length=100)
    fallback_agent_key: str = Field(min_length=2, max_length=100)
    manual_override_wins: Literal[True]
    product_keyword_routing: bool = True


class AreaAgentGovernance(WriteSchema):
    policy_version: Literal["agent-governance-v1.4.0"] = "agent-governance-v1.4.0"
    max_delegation_depth: Literal[4] = 4
    enforce_parent_chain: Literal[True] = True
    manual_override_wins: Literal[True] = True
    hermes_cannot_orchestrate: Literal[True] = True
    cross_area_access_disabled: Literal[True] = True
    cross_product_access_disabled: Literal[True] = True
    cross_workspace_access_denied: Literal[True] = True
    cross_client_access_denied: Literal[True] = True
    least_privilege_tools: Literal[True] = True
    independent_review_required: Literal[True] = True
    external_actions_disabled: Literal[True] = True


class AreaBlueprintContract(WriteSchema):
    key: str = Field(min_length=2, max_length=140)
    label: str = Field(min_length=2, max_length=220)
    product_key: str = Field(min_length=2, max_length=100)
    playbook_scope: str = Field(min_length=2, max_length=160)


class AreaBlueprintPlaybook(WriteSchema):
    onboarding_required: bool = True
    sections: list[str] = Field(min_length=1)


class AreaCommunicationProfile(WriteSchema):
    tone: Literal["formal", "balanced", "conversational"] = "balanced"
    formality: Literal["formal", "neutral", "informal"] = "formal"
    neutral_language: bool = True
    plain_language: bool = True
    signature_style: Literal["office", "professional", "agent"] = "office"
    preferred_terms: list[str] = Field(default_factory=list)
    avoided_terms: list[str] = Field(default_factory=list)
    custom_instructions: str | None = None


class AreaBlueprintDefinition(WriteSchema):
    catalog_version: str | None = Field(default=None, max_length=40)
    catalog_managed: bool = False
    taxonomy: AreaBlueprintTaxonomy
    products: list[AreaBlueprintProduct] = Field(default_factory=list)
    documents: list[str] = Field(min_length=1)
    funnel: list[str] = Field(min_length=2)
    agent_team: list[str] = Field(min_length=1)
    subagents: list[AreaBlueprintSubagent] = Field(default_factory=list)
    routing: AreaBlueprintRouting
    agent_governance: AreaAgentGovernance = Field(default_factory=AreaAgentGovernance)
    contracts: list[AreaBlueprintContract] = Field(default_factory=list)
    playbook: AreaBlueprintPlaybook | None = None
    communication_profile: AreaCommunicationProfile | None = None
    audiences: list[str] = Field(default_factory=list)
    gates: AreaBlueprintGates

    @field_validator("documents", "funnel", "agent_team")
    @classmethod
    def validate_entries(cls, value: list[str]) -> list[str]:
        normalized = [entry.strip() for entry in value]
        if any(not entry for entry in normalized):
            raise ValueError("entries must not be blank")
        if len({entry.casefold() for entry in normalized}) != len(normalized):
            raise ValueError("entries must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_human_gate(self) -> "AreaBlueprintDefinition":
        if self.gates.human_request_agent not in self.agent_team:
            raise ValueError("humanRequestAgent must belong to agentTeam")
        if self.gates.human_handoff_coordinator not in self.agent_team:
            raise ValueError("humanHandoffCoordinator must belong to agentTeam")
        agent_keys = set(self.agent_team)
        product_keys = [product.key for product in self.products]
        if len(set(product_keys)) != len(product_keys):
            raise ValueError("product keys must be unique")
        subagent_keys = [agent.key for agent in self.subagents]
        if len(set(subagent_keys)) != len(subagent_keys):
            raise ValueError("subagent keys must be unique")
        for agent in self.subagents:
            if agent.key not in agent_keys:
                raise ValueError(f"subagent '{agent.key}' must belong to agentTeam")
            if agent.parent_agent_key not in agent_keys:
                raise ValueError(
                    f"parentAgentKey '{agent.parent_agent_key}' must belong to agentTeam"
                )
            if agent.parent_agent_key == agent.key:
                raise ValueError(f"subagent '{agent.key}' cannot be its own parent")
            if agent.parent_agent_key == "hermes":
                raise ValueError("Hermes cannot orchestrate subagents")
            if agent.product_key and agent.product_key not in set(product_keys):
                raise ValueError(
                    f"subagent productKey '{agent.product_key}' must exist in products"
                )
        parent_by_key = {agent.key: agent.parent_agent_key for agent in self.subagents}
        for key in parent_by_key:
            visited: set[str] = set()
            cursor: str | None = key
            while cursor in parent_by_key:
                if cursor in visited:
                    raise ValueError(f"agent hierarchy contains a cycle at '{key}'")
                visited.add(cursor)
                cursor = parent_by_key[cursor]
        for product in self.products:
            if product.specialist_agent_key not in agent_keys:
                raise ValueError(
                    f"specialistAgentKey '{product.specialist_agent_key}' must belong to agentTeam"
                )
            if product.closer_agent_key not in agent_keys:
                raise ValueError(
                    f"closerAgentKey '{product.closer_agent_key}' must belong to agentTeam"
                )
        if self.routing.coordination_agent_key != "orquestrador":
            raise ValueError("coordinationAgentKey must be orquestrador")
        if self.routing.owner_bridge_agent_key != "hermes":
            raise ValueError("ownerBridgeAgentKey must be hermes")
        if self.routing.owner_bridge_delegator_key != "secretaria":
            raise ValueError("ownerBridgeDelegatorKey must be secretaria")
        if self.gates.human_request_agent != self.routing.coordination_agent_key:
            raise ValueError("only coordinationAgentKey can request a human")
        route_agents = {
            self.routing.coordination_agent_key,
            self.routing.owner_bridge_agent_key,
            self.routing.owner_bridge_delegator_key,
            self.routing.sdr_agent_key,
            self.routing.specialist_agent_key,
            self.routing.closer_agent_key,
            self.routing.fallback_agent_key,
        }
        missing = sorted(route_agents.difference(agent_keys))
        if missing:
            raise ValueError(f"routing agents must belong to agentTeam: {', '.join(missing)}")
        known_products = set(product_keys)
        for contract in self.contracts:
            if contract.product_key not in known_products:
                raise ValueError(
                    f"contract productKey '{contract.product_key}' must exist in products"
                )
        return self


class AreaBlueprintCreate(WriteSchema):
    area_key: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=180)
    description: str | None = None
    status: AreaBlueprintStatus = AreaBlueprintStatus.draft
    version: str = "v0.1.0-local"
    readiness: int = Field(default=0, ge=0, le=100)
    blueprint: dict[str, Any] = Field(default_factory=dict)


class AreaBlueprintUpdate(WriteSchema):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = None
    status: AreaBlueprintStatus | None = None
    version: str | None = None
    readiness: int | None = Field(default=None, ge=0, le=100)
    blueprint: dict[str, Any] | None = None


class AreaActivationUpdate(WriteSchema):
    enabled: bool
    force_over_plan: bool = False


class AreaBlueprintRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    area_key: str
    name: str
    description: str | None = None
    status: AreaBlueprintStatus
    version: str
    readiness: int
    is_active: bool
    blueprint: dict[str, Any]
    validated_at: datetime | None = None
    validated_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("validated_at", "created_at", "updated_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class OmnichannelThreadCreate(WriteSchema):
    identity_id: UUID | None = None
    matter_id: UUID | None = None
    client_id: UUID | None = None
    primary_channel: OmnichannelChannel
    subject: str | None = None
    object_name: str | None = None
    owner_agent_key: str | None = None
    score: int = Field(default=0, ge=0, le=100)
    queue: str = Field(default="new", min_length=1, max_length=60)
    priority: int = Field(default=0, ge=0, le=100)
    preferred_channel: str | None = Field(default=None, max_length=40)
    assigned_team_id: UUID | None = None
    sla_due_at: datetime | None = None
    thread_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("thread_metadata", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any) -> Any:
        return strip_internal_provider_cost_fields(value, parent_key="thread_metadata")


class OmnichannelThreadUpdate(WriteSchema):
    status: ThreadStatus | None = None
    assigned_human_id: UUID | None = None
    assigned_team_id: UUID | None = None
    owner_agent_key: str | None = None
    subject: str | None = None
    object_name: str | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    queue: str | None = Field(default=None, min_length=1, max_length=60)
    priority: int | None = Field(default=None, ge=0, le=100)
    is_favorite: bool | None = None
    preferred_channel: str | None = Field(default=None, max_length=40)
    expected_version: int | None = Field(default=None, ge=1)
    closed_reason: str | None = Field(default=None, max_length=180)
    sla_due_at: datetime | None = None
    thread_metadata: dict[str, Any] | None = None

    @field_validator("thread_metadata", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any) -> Any:
        return strip_internal_provider_cost_fields(value, parent_key="thread_metadata")


class OmnichannelThreadRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID | None = None
    client_id: UUID | None = None
    identity_id: UUID | None = None
    assigned_human_id: UUID | None = None
    assigned_team_id: UUID | None = None
    primary_channel: OmnichannelChannel
    status: ThreadStatus
    subject: str | None = None
    object_name: str | None = None
    owner_agent_key: str | None = None
    queue: str = "new"
    priority: int = 0
    is_favorite: bool = False
    preferred_channel: str | None = None
    version: int = 1
    score: int
    unread_count: int
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    sla_due_at: datetime | None = None
    first_response_due_at: datetime | None = None
    resolution_due_at: datetime | None = None
    closed_at: datetime | None = None
    closed_reason: str | None = None
    policy_state: dict[str, Any] = Field(default_factory=dict)
    cost_state: dict[str, Any] = Field(default_factory=dict)
    thread_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    sensitive_data_hidden: bool = False

    @field_validator("policy_state", "cost_state", "thread_metadata", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any, info) -> Any:
        return strip_internal_provider_cost_fields(value, parent_key=info.field_name)


class OmnichannelMessageMentionCreate(WriteSchema):
    mention_type: ConversationMentionType
    target_id: UUID


class OmnichannelMessageCollaborationCreate(WriteSchema):
    """Validated collaboration envelope carried by a canonical message command."""

    schema_version: Literal["klm.conversation-collaboration.v1"] = (
        "klm.conversation-collaboration.v1"
    )
    conversation_kind: Literal["direct", "group"] | None = None
    reply_to_message_id: UUID | None = None
    reference_type: ConversationReferenceType = ConversationReferenceType.reply
    mentions: list[OmnichannelMessageMentionCreate] = Field(
        default_factory=list,
        max_length=50,
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=3,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$",
    )

    @model_validator(mode="after")
    def validate_collaboration(self) -> "OmnichannelMessageCollaborationCreate":
        targets = [(item.mention_type.value, item.target_id) for item in self.mentions]
        if len(targets) != len(set(targets)):
            raise ValueError("Uma pessoa ou equipe pode ser mencionada apenas uma vez.")
        return self


def normalize_message_collaboration(
    value: Any,
) -> OmnichannelMessageCollaborationCreate | None:
    if value is None:
        return None
    if isinstance(value, OmnichannelMessageCollaborationCreate):
        return value
    if not isinstance(value, dict):
        raise ValueError("collaboration precisa ser um objeto estruturado.")
    return OmnichannelMessageCollaborationCreate.model_validate(value)


class OmnichannelMessageCreate(WriteSchema):
    endpoint_id: UUID | None = None
    binding_id: UUID | None = None
    channel: OmnichannelChannel
    direction: MessageDirection
    author_type: MessageAuthorType
    author_label: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1)
    audience: str = Field(default="client_private", min_length=1, max_length=40)
    content_type: str = Field(default="text", min_length=1, max_length=40)
    language: str | None = Field(default=None, max_length=16)
    provider_status: str | None = Field(default=None, max_length=80)
    transport: str = Field(default="local", min_length=1, max_length=60)
    window_expires_at: datetime | None = None
    template_id: str | None = Field(default=None, max_length=180)
    idempotency_key: str = Field(min_length=3, max_length=160)
    external_message_id: str | None = None
    delivery_status: MessageDeliveryStatus | None = None
    occurred_at: datetime | None = None
    scheduled_at: datetime | None = Field(default=None, exclude=True)
    timezone: str = Field(
        default="America/Sao_Paulo",
        min_length=1,
        max_length=80,
        exclude=True,
    )
    collaboration: OmnichannelMessageCollaborationCreate | None = Field(
        default=None,
        exclude=True,
    )
    message_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message_metadata", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any) -> Any:
        return strip_internal_provider_cost_fields(value, parent_key="message_metadata")

    @model_validator(mode="after")
    def validate_rich_content(self) -> "OmnichannelMessageCreate":
        metadata = dict(self.message_metadata or {})
        try:
            metadata_size = len(
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Os metadados da mensagem precisam ser serializáveis.") from error
        if metadata_size > 65_536:
            raise ValueError("Os metadados da mensagem excedem o limite de 64 KiB.")

        raw_attachments = metadata.get("attachments") or []
        if not isinstance(raw_attachments, list) or any(
            not isinstance(item, dict) for item in raw_attachments
        ):
            raise ValueError("attachments precisa ser uma lista de objetos.")
        if len(raw_attachments) > 10:
            raise ValueError("Uma mensagem aceita no máximo 10 anexos.")

        had_structured_content = "structuredContent" in metadata
        raw_structured = metadata.get("structuredContent") or {}
        if not isinstance(raw_structured, dict):
            raise ValueError("structuredContent precisa ser um objeto.")

        collaboration_candidates = [
            candidate
            for candidate in (
                self.collaboration,
                metadata.get("collaboration"),
                raw_structured.get("collaboration"),
            )
            if candidate is not None
        ]
        normalized_collaboration = (
            normalize_message_collaboration(collaboration_candidates[0])
            if collaboration_candidates
            else None
        )
        for candidate in collaboration_candidates[1:]:
            alternate = normalize_message_collaboration(candidate)
            if alternate != normalized_collaboration:
                raise ValueError(
                    "Os contextos de colaboração informados para a mensagem são divergentes."
                )
        if normalized_collaboration is not None:
            self.collaboration = normalized_collaboration
            metadata["collaboration"] = normalized_collaboration.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
        raw_structured = dict(raw_structured)
        raw_structured.pop("collaboration", None)
        if had_structured_content or raw_structured:
            metadata["structuredContent"] = raw_structured
        else:
            metadata.pop("structuredContent", None)
        self.message_metadata = metadata

        if self.scheduled_at is not None:
            if self.direction != MessageDirection.outbound:
                raise ValueError("Somente mensagens de saída podem ser agendadas.")
            if self.channel == OmnichannelChannel.internal:
                raise ValueError("Notas internas não podem ser agendadas para entrega externa.")
            try:
                ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError:
                raise ValueError("Fuso horário do agendamento é inválido.") from None
            metadata["scheduling"] = {
                "scheduledAt": self.scheduled_at.isoformat(),
                "timezone": self.timezone,
            }
            self.message_metadata = metadata

        final_metadata_size = len(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        )
        if final_metadata_size > 65_536:
            raise ValueError("Os metadados normalizados da mensagem excedem o limite de 64 KiB.")

        rich_types = {
            "attachment",
            "media",
            "template",
            "interactive_buttons",
            "reply_buttons",
            "interactive_list",
            "list",
            "whatsapp_flow",
            "flow",
        }
        structured_kind = str(raw_structured.get("kind") or "").strip().lower()
        has_rich_content = bool(
            self.template_id
            or raw_attachments
            or structured_kind not in {"", "text"}
            or self.content_type in rich_types
        )

        if self.direction == MessageDirection.internal and has_rich_content:
            raise ValueError(
                "Notas internas aceitam somente texto; anexos privados exigem um fluxo explícito."
            )
        if self.direction != MessageDirection.outbound or not has_rich_content:
            return self

        if self.content_type == "template" and not (
            self.template_id or structured_kind == "template"
        ):
            raise ValueError("Mensagem com modelo exige templateId.")
        if self.content_type in {"attachment", "media"} and not (
            raw_attachments or structured_kind == "media"
        ):
            raise ValueError("Mensagem com mídia exige ao menos um anexo.")
        if (
            self.content_type
            in {
                "interactive_buttons",
                "reply_buttons",
                "interactive_list",
                "list",
                "whatsapp_flow",
                "flow",
            }
            and self.channel != OmnichannelChannel.whatsapp
        ):
            raise ValueError("Este conteúdo interativo exige uma rota WhatsApp.")

        if self.channel != OmnichannelChannel.whatsapp:
            return self
        if len(raw_attachments) > 1:
            raise ValueError(
                "O WhatsApp oficial aceita uma mídia por mensagem; "
                "envie os demais arquivos separadamente."
            )

        kind_by_content_type = {
            "attachment": "media",
            "media": "media",
            "template": "template",
            "interactive_buttons": "reply_buttons",
            "reply_buttons": "reply_buttons",
            "interactive_list": "list",
            "list": "list",
            "whatsapp_flow": "flow",
            "flow": "flow",
        }
        structured_payload = dict(raw_structured)
        if not structured_payload.get("kind"):
            structured_payload["kind"] = kind_by_content_type.get(
                self.content_type,
                "template" if self.template_id else "media" if raw_attachments else "text",
            )

        from app.services.whatsapp.cloud_payload import (
            WhatsAppPayloadError,
            normalize_internal_payload,
        )

        try:
            normalized = normalize_internal_payload(
                structured_payload,
                body=self.body,
                template_id=self.template_id,
                attachments=raw_attachments,
            )
        except WhatsAppPayloadError as error:
            raise ValueError(str(error)) from error

        canonical_type = {
            "media": "media",
            "template": "template",
            "reply_buttons": "interactive_buttons",
            "list": "interactive_list",
            "flow": "whatsapp_flow",
        }.get(str(normalized.get("kind")), self.content_type)
        metadata["structuredContent"] = normalized
        self.message_metadata = metadata
        self.content_type = canonical_type
        if canonical_type == "template":
            self.template_id = str(normalized["name"])
            self.language = str(normalized["language"])
        return self


class InternalOmnichannelMessageCreate(OmnichannelMessageCreate):
    """Trusted message command with internal provider cost projections."""

    cost_amount: Decimal | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, min_length=3, max_length=3)

    @field_validator("cost_currency")
    @classmethod
    def normalize_cost_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @model_validator(mode="after")
    def validate_cost_pair(self) -> "InternalOmnichannelMessageCreate":
        if (self.cost_amount is None) != (self.cost_currency is None):
            raise ValueError("Custo interno de mensagem exige amount e currency.")
        return self


class OmnichannelMessageRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    matter_id: UUID | None = None
    endpoint_id: UUID | None = None
    binding_id: UUID | None = None
    connection_id: UUID | None = None
    author_user_id: UUID | None = None
    channel: OmnichannelChannel
    direction: MessageDirection
    author_type: MessageAuthorType
    author_label: str
    body: str
    audience: str = "client_private"
    content_type: str = "text"
    language: str | None = None
    provider_status: str | None = None
    provider: str | None = None
    transport: str = "local"
    window_expires_at: datetime | None = None
    template_id: str | None = None
    external_message_id: str | None = None
    idempotency_key: str
    delivery_status: MessageDeliveryStatus
    occurred_at: datetime
    content_lifecycle_state: Literal["original", "edited", "deleted", "provider_unsupported"] = (
        "original"
    )
    evidence_event_count: int = 0
    evidence_revision_count: int = 0
    evidence_chain_sha256: str | None = None
    content_edited_at: datetime | None = None
    content_deleted_at: datetime | None = None
    message_metadata: dict[str, Any]
    sensitive_data_hidden: bool = False

    @field_validator("message_metadata", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any) -> Any:
        return strip_internal_provider_cost_fields(value, parent_key="message_metadata")


class MessageEvidenceMediaRead(CamelCaseSchema):
    storage_object_id: UUID | None = None
    object_id: UUID | None = None
    storage_object_version: int | None = None
    sha256: str | None = None
    byte_size: int | None = None
    mime_type: str | None = None
    filename: str | None = None
    provider_media_id: str | None = None


class MessageEvidenceEventRead(CamelCaseSchema):
    id: UUID
    chain_id: UUID
    revision: int
    content_revision: int | None = None
    message_id: UUID | None = None
    thread_id: UUID | None = None
    event_type: Literal[
        "captured",
        "edited",
        "deleted",
        "provider_receipt",
        "media_captured",
        "media_unavailable",
        "provider_unsupported",
    ]
    observation_state: Literal["observed", "provider_signal", "inferred", "unavailable"]
    provider: str
    connection_key: str
    external_message_id: str
    content_type: str | None = None
    body_snapshot: str | None = None
    structured_content: dict[str, Any] = Field(default_factory=dict)
    event_payload: dict[str, Any] = Field(default_factory=dict)
    event_payload_sha256: str
    source_envelope_sha256: str | None = None
    signature_sha256: str | None = None
    previous_evidence_sha256: str | None = None
    evidence_sha256: str
    media: MessageEvidenceMediaRead
    provider_occurred_at: datetime | None = None
    received_at: datetime

    @field_validator("structured_content", "event_payload", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any, info) -> Any:
        return strip_internal_provider_cost_fields(value, parent_key=info.field_name)


class MessageEvidenceChainRead(CamelCaseSchema):
    message_id: UUID
    thread_id: UUID
    chain_id: UUID | None = None
    content_lifecycle_state: Literal["original", "edited", "deleted", "provider_unsupported"]
    event_count: int
    content_revision_count: int
    integrity_status: Literal["verified", "failed", "unavailable"]
    chain_head_sha256: str | None = None
    original_snapshot: MessageEvidenceEventRead | None = None
    current_snapshot: MessageEvidenceEventRead | None = None
    events: list[MessageEvidenceEventRead] = Field(default_factory=list)
    provider_notes: list[str] = Field(default_factory=list)
    sensitive_data_hidden: bool = False
    generated_at: datetime


class MessageEvidenceExportRead(CamelCaseSchema):
    schema_version: str
    exported_at: datetime
    chain: MessageEvidenceChainRead
    raw_provider_envelope_included: bool
    raw_provider_envelope_note: str
    manifest_sha256: str


class InternalMessageFeedEntryRead(CamelCaseSchema):
    message: OmnichannelMessageRead
    thread: OmnichannelThreadRead


class EmailAttachmentInput(WriteSchema):
    name: str = Field(min_length=1, max_length=500)
    mime_type: str | None = None
    content_text: str | None = None
    document_type: str = "outro"


class EmailIngestRequest(WriteSchema):
    message_id: str = Field(min_length=3, max_length=180)
    sender_email: str = Field(min_length=3, max_length=255)
    sender_name: str | None = None
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)
    matter_id: UUID | None = None
    client_id: UUID | None = None
    received_at: datetime | None = None
    attachments: list[EmailAttachmentInput] = Field(default_factory=list)


class EmailIngestRead(CamelCaseSchema):
    thread: OmnichannelThreadRead
    message: OmnichannelMessageRead
    client_id: UUID
    matter_id: UUID
    document_ids: list[UUID] = Field(default_factory=list)
    knowledge_chunk_ids: list[UUID] = Field(default_factory=list)
    deduplicated: bool = False


class FollowUpCreate(WriteSchema):
    matter_id: UUID | None = None
    thread_id: UUID | None = None
    channel: OmnichannelChannel
    due_at: datetime
    max_attempts: int = Field(default=8, ge=1, le=30)
    policy_key: str = "lead_never_unanswered"
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class FollowUpUpdate(WriteSchema):
    due_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=2000)
    status: FollowUpStatus | None = None

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("A data do follow-up deve conter fuso horário.")
        return value

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_update(self) -> "FollowUpUpdate":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos uma alteração para o follow-up.")
        if "due_at" in self.model_fields_set and self.due_at is None:
            raise ValueError("A data do follow-up não pode ser removida.")
        return self


class FollowUpRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID | None = None
    thread_id: UUID | None = None
    channel: OmnichannelChannel
    status: FollowUpStatus
    due_at: datetime
    attempt: int
    max_attempts: int
    policy_key: str
    reason: str | None = None
    last_error: str | None = None
    payload: dict[str, Any]
    last_notified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FollowUpSuppressRequest(WriteSchema):
    reason: str = Field(min_length=3)
    blocked_by_client: bool = True


class AcquisitionEventCreate(WriteSchema):
    matter_id: UUID | None = None
    client_id: UUID | None = None
    event_name: str = Field(min_length=1, max_length=100)
    channel: str = Field(min_length=1, max_length=80)
    source: str | None = None
    medium: str | None = None
    campaign: str | None = None
    event_id: str | None = None
    dedupe_key: str = Field(min_length=3, max_length=180)
    happened_at: datetime | None = None
    value: float | None = Field(default=None, ge=0)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    click_ids: dict[str, str] = Field(default_factory=dict)
    event_metadata: dict[str, Any] = Field(default_factory=dict)


class CtwaBackfillRequest(WriteSchema):
    dry_run: bool = True
    batch_size: int = Field(default=500, ge=1, le=5000)
    max_batches: int | None = Field(default=None, ge=1, le=1000)


class CtwaBackfillReport(CamelCaseSchema):
    scanned: int
    with_referral: int
    events_created: int
    skipped_existing: int
    unresolved: int
    dry_run: bool
    sample_dedupe_keys: list[str] = Field(default_factory=list)


class AcquisitionEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID | None = None
    client_id: UUID | None = None
    event_name: str
    channel: str
    source: str | None = None
    medium: str | None = None
    campaign: str | None = None
    event_id: str | None = None
    dedupe_key: str
    happened_at: datetime
    value: float | None = None
    currency: str
    click_ids: dict[str, str]
    event_metadata: dict[str, Any]


class TechnicalRequestCreate(WriteSchema):
    title: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=3)
    route: str | None = None
    severity: TechnicalRequestSeverity = TechnicalRequestSeverity.medium
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class TechnicalRequestUpdate(WriteSchema):
    status: TechnicalRequestStatus | None = None
    severity: TechnicalRequestSeverity | None = None
    assigned_to_user_id: UUID | None = None
    resolution: str | None = None
    evidence: list[dict[str, Any]] | None = None


class TechnicalRequestRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    assigned_to_user_id: UUID | None = None
    title: str
    description: str
    route: str | None = None
    severity: TechnicalRequestSeverity
    status: TechnicalRequestStatus
    evidence: list[dict[str, Any]]
    resolution: str | None = None
    created_at: datetime
    updated_at: datetime


LegalArtifactKind = Literal[
    "proposal", "contract", "power_of_attorney", "petition", "client_update"
]
LegalArtifactStatus = Literal[
    "draft", "review_pending", "approved", "rejected", "changes_requested", "archived"
]
LegalArtifactAuthorType = Literal["human", "agent"]
LegalArtifactProtocolabilityState = Literal["bloqueado", "pendente", "protocolavel"]

# Kinds de LegalArtifact que o editor jurídico consegue gerar a partir de um
# office_template (Onda 12) — "client_update" não é um documento timbrado,
# é uma mensagem; generate_artifact recusa (fail-closed) esse kind.
LEGAL_ARTIFACT_OFFICE_TEMPLATE_KINDS: tuple[LegalArtifactKind, ...] = (
    "proposal",
    "contract",
    "power_of_attorney",
    "petition",
)


class LegalArtifactVersionCreate(WriteSchema):
    matter_id: UUID
    kind: LegalArtifactKind
    title: str = Field(min_length=3, max_length=300)
    content: dict[str, Any] = Field(min_length=1)
    artifact_key: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)
    # ── Editor jurídico (Onda 6, §9 item 1) — todos opcionais e
    # retrocompatíveis; quem cria uma versão manual (ou o round-trip .docx
    # em legal_docx_service.py) continua funcionando sem informar nada
    # disto, e o autor vira "human" por padrão.
    legal_demand_id: UUID | None = None
    author_type: LegalArtifactAuthorType = "human"
    author_agent_key: str | None = Field(default=None, max_length=120)
    office_template_key: str | None = Field(default=None, max_length=60)
    office_template_object_id: UUID | None = None
    office_template_object_version: int | None = Field(default=None, ge=1)
    protocolability_state: LegalArtifactProtocolabilityState = "bloqueado"

    @model_validator(mode="after")
    def _author_and_template_pairs_are_consistent(self) -> "LegalArtifactVersionCreate":
        if self.author_type == "agent" and not self.author_agent_key:
            raise ValueError("author_agent_key is required when author_type is 'agent'.")
        if (self.office_template_object_id is None) != (
            self.office_template_object_version is None
        ):
            raise ValueError(
                "office_template_object_id and office_template_object_version must be "
                "provided together."
            )
        return self


class LegalArtifactVersionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    legal_demand_id: UUID | None = None
    artifact_key: UUID
    version: int
    kind: LegalArtifactKind
    title: str
    status: LegalArtifactStatus
    content: dict[str, Any]
    source_snapshot: dict[str, Any]
    created_by_user_id: UUID
    author_type: LegalArtifactAuthorType = "human"
    author_agent_key: str | None = None
    office_template_key: str | None = None
    office_template_object_id: UUID | None = None
    office_template_object_version: int | None = None
    protocolability_state: LegalArtifactProtocolabilityState = "bloqueado"
    approval_item_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    sensitive_data_hidden: bool = False


class LegalArtifactSubmitRead(CamelCaseSchema):
    artifact: LegalArtifactVersionRead
    approval_id: UUID


class LegalArtifactGenerateRequest(WriteSchema):
    """Gera o artefato integral a partir do office_template do playbook
    (Onda 12) + variáveis do Caso 360 — ver app/services/legal_editor_service.py.
    Sem modelo do tipo no playbook: erro claro (fail-closed), nunca um
    modelo genérico."""

    legal_demand_id: UUID
    kind: LegalArtifactKind
    title: str = Field(min_length=3, max_length=300)
    office_template_key: str | None = Field(default=None, max_length=60)
    artifact_key: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)
    author_type: LegalArtifactAuthorType = "human"
    author_agent_key: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _author_pair_is_consistent(self) -> "LegalArtifactGenerateRequest":
        if self.author_type == "agent" and not self.author_agent_key:
            raise ValueError("author_agent_key is required when author_type is 'agent'.")
        return self


class LegalArtifactSectionGenerateRequest(WriteSchema):
    """Regenera UMA seção de uma versão existente a partir das variáveis do
    Caso 360 atuais, e persiste isso como versão nova (nunca sobrescreve)."""

    section_id: str = Field(min_length=1, max_length=160)
    expected_version: int = Field(ge=1)


class LegalArtifactChecklistItemRead(CamelCaseSchema):
    item: str
    detail: str


class LegalArtifactChecklistRead(CamelCaseSchema):
    has_placeholder: bool
    missing_variables: list[str]
    has_procuracao: bool
    has_custas_pagas: bool | None = None
    pending_items: list[LegalArtifactChecklistItemRead]
    protocolability_state: LegalArtifactProtocolabilityState


class ProcessMonitoringCreate(WriteSchema):
    matter_id: UUID
    case_number: str = Field(min_length=5, max_length=80)
    court: str | None = Field(default=None, max_length=240)
    status: str = Field(default="cadastrado", min_length=2, max_length=80)
    next_deadline_at: datetime | None = None
    last_event_summary: str | None = Field(default=None, max_length=4000)

    @field_validator("case_number", "court", "status", "last_event_summary")
    @classmethod
    def strip_process_fields(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProcessMonitoringUpdate(WriteSchema):
    court: str | None = Field(default=None, max_length=240)
    status: str | None = Field(default=None, min_length=2, max_length=80)
    next_deadline_at: datetime | None = None
    last_event_summary: str | None = Field(default=None, max_length=4000)

    @field_validator("court", "status", "last_event_summary")
    @classmethod
    def strip_process_fields(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProcessMonitoringEventCreate(WriteSchema):
    occurred_at: datetime | None = None
    title: str = Field(min_length=2, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    source: Literal["manual_local", "court_connector_draft", "client_update_draft"] = "manual_local"

    @field_validator("title", "description")
    @classmethod
    def strip_event_fields(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProcessMonitoringRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    matter_id: UUID
    created_by_user_id: UUID
    case_number: str
    court: str | None = None
    status: str
    next_deadline_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_event_summary: str | None = None
    source_state: str
    events: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    sensitive_data_hidden: bool = False


class ExternalActionIntentCreate(WriteSchema):
    matter_id: UUID | None = None
    action_type: str = Field(min_length=2, max_length=120)
    provider: str | None = None
    idempotency_key: str = Field(min_length=3, max_length=180)
    payload: dict[str, Any] = Field(default_factory=dict)


class ExternalActionIntentRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    requested_by_user_id: UUID | None = None
    matter_id: UUID | None = None
    action_type: str
    provider: str | None = None
    idempotency_key: str
    status: ExternalActionIntentStatus
    payload: dict[str, Any]
    reason: str
    created_at: datetime
    updated_at: datetime


class AuditEventRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    actor_user_id: UUID | None = None
    actor_type: str
    actor_label: str
    event_type: str
    entity_type: str
    entity_id: UUID | None = None
    summary: str
    correlation_id: str | None = None
    before_data: dict[str, Any] | None = None
    after_data: dict[str, Any] | None = None
    occurred_at: datetime

    @field_validator("before_data", "after_data", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any) -> Any:
        return strip_internal_provider_cost_fields(value)


class TrackingAggregateRead(CamelCaseSchema):
    events: int = 0
    leads: int = 0
    qualified_leads: int = 0
    meetings: int = 0
    clients: int = 0
    ad_spend: float = 0.0
    attributable_revenue: float = 0.0
    ignored_value_events: int = 0
    conversion_rate: float | None = None
    cac: float | None = None
    roas: float | None = None


class TrackingChannelRead(TrackingAggregateRead):
    channel: str
    value: float = Field(
        default=0.0,
        description="Deprecated revenue-only alias retained for API compatibility.",
    )


class TrackingEventCoverageRead(CamelCaseSchema):
    event: str
    count: int = 0
    covered: bool = False


class TrackingTrendPointRead(TrackingAggregateRead):
    date: date


class TrackingJourneyRead(TrackingAggregateRead):
    journey_id: str
    identity_type: str
    event_path: list[str]
    channels: list[str]
    started_at: datetime
    last_activity_at: datetime
    converted: bool = False


class TrackingRemarketingRead(CamelCaseSchema):
    stage: str
    channel: str
    audience_size: int
    last_activity_at: datetime


class TrackingAttributionCoverageRead(CamelCaseSchema):
    conversations: int = 0
    tracked_conversations: int = 0
    untracked_conversations: int = 0
    tracking_rate: float | None = None
    conversion_events: int = 0
    attributed_conversions: int = 0
    unattributed_conversions: int = 0
    attributed_conversion_rate: float | None = None
    feedback_eligible_conversions: int = 0
    feedback_blocked_no_evidence: int = 0
    business_duplicates_suppressed: int = 0
    full_hierarchy_touches: int = 0
    partial_hierarchy_touches: int = 0


class TrackingAttributionRead(TrackingAggregateRead):
    provider: str | None = None
    provider_label: str
    channel: str
    source: str | None = None
    medium: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None
    ad_set_id: str | None = None
    ad_set_name: str | None = None
    ad_id: str | None = None
    ad_name: str | None = None
    creative_id: str | None = None
    creative_name: str | None = None
    form_id: str | None = None
    form_name: str | None = None
    placement: str | None = None
    landing_page_url: str | None = None
    source_url: str | None = None
    click_id_keys: list[str] = Field(default_factory=list)
    evidence_level: Literal["exact", "named", "unattributed"]
    evidence_reason: str
    hierarchy_completeness: int = Field(ge=0, le=100)
    missing_levels: list[str] = Field(default_factory=list)
    provider_console_url: str | None = None
    attribution_model: Literal["last_paid_touch"] = "last_paid_touch"
    first_touch_at: datetime
    last_touch_at: datetime
    touch_count: int = 0
    journey_count: int = 0
    sample_journey_id: str | None = None
    sample_thread_id: UUID | None = None
    sample_matter_id: UUID | None = None


class TrackingFunnelStepRead(CamelCaseSchema):
    stage: str
    journeys: int = 0
    share_of_start: float | None = None
    conversion_from_previous: float | None = None
    drop_off_from_previous: int = 0
    drop_off_rate: float | None = None


class TrackingInsightRead(CamelCaseSchema):
    key: str
    severity: Literal["info", "warning", "critical", "opportunity"]
    title: str
    detail: str
    recommended_action: str
    metric: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


class TrackingServiceQualityRead(CamelCaseSchema):
    findings: int = 0
    active_findings: int = 0
    critical_findings: int = 0
    unanswered_leads: int = 0
    delivery_mismatches: int = 0
    duplicate_outbound: int = 0
    resolved_findings: int = 0
    audited_threads: int = 0
    quality_score: int = Field(default=100, ge=0, le=100)


class TrackingConversionFeedbackRead(CamelCaseSchema):
    prepared: int = 0
    blocked_external: int = 0
    awaiting_approval: int = 0
    approved_local: int = 0
    rejected: int = 0
    duplicate_prevented: int = 0
    payload_ready: int = 0
    configuration_blocked: int = 0
    prepared_proofs: int = 0
    provider_receipts: int = 0
    failed_receipts: int = 0
    providers: dict[str, int] = Field(default_factory=dict)


class TrackingConversionFeedbackEventRead(CamelCaseSchema):
    intent_id: UUID
    provider: str
    source_event_id: UUID
    source_touch_event_id: UUID
    conversion_event: str
    happened_at: datetime
    value: float | None = None
    currency: str = "BRL"
    status: str
    provider_event_id: str
    match_key_names: list[str] = Field(default_factory=list)
    payload_schema_version: str = ""
    matching_mode: str = "unmatched"
    ready_for_dispatch: bool = False
    missing_configuration: list[str] = Field(default_factory=list)
    privacy_guard: dict[str, Any] = Field(default_factory=dict)
    payload_sha256: str = ""
    proof_state: str = "missing"
    receipt_state: str | None = None
    receipt_count: int = 0
    dedupe_key: str
    external_actions_executed: int = 0


class TrackingDailyChannelRead(CamelCaseSchema):
    date: date
    channel: str
    events: int = 0
    leads: int = 0
    clients: int = 0
    attributable_revenue: float = 0.0


class TrackingConversionFeedbackPrepareRead(CamelCaseSchema):
    scanned: int = 0
    prepared: int = 0
    deduplicated: int = 0
    skipped_without_evidence: int = 0
    suppressed_business_duplicates: int = 0
    providers: dict[str, int] = Field(default_factory=dict)
    external_actions_executed: int = 0


class AdsMeasurementConfigUpdate(CamelCaseSchema):
    dataset_id: str | None = None
    pixel_id: str | None = None
    access_token: str | None = None


class AdsMeasurementConfigRead(CamelCaseSchema):
    configured: bool = False
    dataset_id: str | None = None
    pixel_id: str | None = None
    access_token_configured: bool = False
    access_token_last4: str | None = None
    updated_at: datetime | None = None


class AdsFeedbackDispatchRead(CamelCaseSchema):
    prepared_processed: int = 0
    already_dispatched: int = 0
    blocked_simulated: int = 0
    dispatched_real: int = 0
    failed: int = 0
    external_actions_enabled: bool = False


class TrackingSummaryRead(CamelCaseSchema):
    period_days: int | None = None
    period_start: datetime | None = None
    total_events: int = 0
    leads: int = 0
    qualified_leads: int = 0
    meetings: int = 0
    clients: int = 0
    ad_spend: float = 0.0
    attributable_revenue: float = 0.0
    ignored_value_events: int = 0
    conversion_rate: float | None = None
    cac: float | None = None
    roas: float | None = None
    identified_journeys: int = 0
    by_channel: list[TrackingChannelRead] = Field(default_factory=list)
    event_coverage: list[TrackingEventCoverageRead] = Field(default_factory=list)
    time_trend: list[TrackingTrendPointRead] = Field(default_factory=list)
    journeys: list[TrackingJourneyRead] = Field(default_factory=list)
    remarketing: list[TrackingRemarketingRead] = Field(default_factory=list)
    attribution_coverage: TrackingAttributionCoverageRead = Field(
        default_factory=TrackingAttributionCoverageRead
    )
    attribution_rows: list[TrackingAttributionRead] = Field(default_factory=list)
    funnel_steps: list[TrackingFunnelStepRead] = Field(default_factory=list)
    insights: list[TrackingInsightRead] = Field(default_factory=list)
    service_quality: TrackingServiceQualityRead = Field(default_factory=TrackingServiceQualityRead)
    conversion_feedback: TrackingConversionFeedbackRead = Field(
        default_factory=TrackingConversionFeedbackRead
    )
    feedback_events: list[TrackingConversionFeedbackEventRead] = Field(default_factory=list)
    daily_channels: list[TrackingDailyChannelRead] = Field(default_factory=list)
    contains_local_fixture_data: bool = False
    generated_at: datetime


class DashboardRead(CamelCaseSchema):
    metrics: dict[str, Any]
    funnel: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    runtime: RuntimeStatusRead


class FunnelMoveRequest(WriteSchema):
    column_id: KanbanColumnId
    micro_status: MicroStatus | None = None
    operation_id: UUID
    expected_board_version: int = Field(ge=0)
    source_column_id: KanbanColumnId | None = None
    before_id: UUID | None = None
    after_id: UUID | None = None
    position: int = Field(default=0, ge=0)


class FunnelRead(CamelCaseSchema):
    board_version: int
    stages: list[dict[str, Any]]
    cards: list[dict[str, Any]]
    metrics: dict[str, Any]


class FunnelMoveRead(CamelCaseSchema):
    matter_id: UUID
    column_id: KanbanColumnId
    micro_status: MicroStatus
    card_order: int
    board_version: int
    operation_id: UUID
    agent_dispatched: bool = False
    deduplicated: bool = False
    automation_status: Literal[
        "not_started", "queued", "processing", "completed", "waiting_human", "failed"
    ] = "not_started"
    agents_triggered: list[str] = Field(default_factory=list)
    agent_run_ids: list[UUID] = Field(default_factory=list)
    approval_id: UUID | None = None
    automation_message: str | None = None


class MatterFinancialProjectionUpdate(WriteSchema):
    fixed_fee: float = Field(ge=0)
    success_percentage: float = Field(ge=0, le=100)
    economic_value_annual: float = Field(ge=0)
    probability: float = Field(ge=0, le=100)
    economic_obligation: bool = True
    payment_mode: Literal["initial_plus_success", "success_only"] = "initial_plus_success"
    success_base_type: Literal["economic_obligation", "moral_damages"] = "economic_obligation"
    moral_damages_estimate: float | None = Field(default=None, ge=0)
    injunction_milestone_fee: float = Field(default=0, ge=0)
    obligation_fee: float = Field(default=0, ge=0)
    review_status: str = Field(
        default="rascunho", pattern="^(rascunho|revisado|aprovado|bloqueado)$"
    )

    @model_validator(mode="after")
    def validate_success_base(self) -> "MatterFinancialProjectionUpdate":
        if self.success_base_type == "moral_damages" and self.moral_damages_estimate is None:
            raise ValueError(
                "Informe a estimativa de dano moral para projetar honorários nessa base."
            )
        return self


class MatterFinancialProjectionRead(CamelCaseSchema):
    matter_id: UUID
    fixed_fee: float
    success_percentage: float
    economic_value_annual: float
    probability: float
    economic_obligation: bool
    payment_mode: str
    success_base_type: str
    moral_damages_estimate: float | None
    injunction_milestone_fee: float
    obligation_fee: float
    review_status: str
    conservative: float
    likely: float
    aggressive: float
    without_economic_value: float
    projection_version: int
    updated_at: datetime


class WorkspacePerformanceRowRead(CamelCaseSchema):
    id: str
    name: str
    scope_name: str
    role: str | None = None
    primary_count: int = 0
    completed_count: int = 0
    pending_count: int = 0
    quality_value: float | None = None
    score: float = 0


class WorkspacePerformanceRead(CamelCaseSchema):
    audience: Literal["workspace_team"] = "workspace_team"
    period_days: int
    generated_at: datetime
    score_formula: str
    rows: list[WorkspacePerformanceRowRead]


class KpiRead(CamelCaseSchema):
    top_metrics: list[dict[str, Any]]
    families: list[dict[str, Any]]
    channels: list[dict[str, Any]]
    period_days: int | None = None
    subtheme: str | None = None
    legal_area_key: str | None = None
    legal_product_key: str | None = None
    generated_at: datetime
    source: str
    trend: dict[str, list[dict[str, Any]]] = {}
    team_performance: WorkspacePerformanceRead


class AdvancedKpiMetricRead(CamelCaseSchema):
    key: str
    label: str
    category: str
    category_label: str
    value: float | None = None
    previous_value: float | None = None
    delta_pct: float | None = None
    polarity: Literal["positive", "negative", "neutral"] = "neutral"
    unit: str
    has_data: bool
    description: str
    source: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    note: str | None = None


class AdvancedKpiCategoryRead(CamelCaseSchema):
    key: str
    label: str
    metric_count: int
    metrics_with_data: int


class AdvancedKpiFilterOptionRead(CamelCaseSchema):
    value: str
    label: str
    parent_value: str | None = None


class AdvancedKpiReportRead(CamelCaseSchema):
    generated_at: datetime
    period_days: int
    previous_period_days: int
    catalog_total: int
    total_rows: int
    rows_with_data: int
    coverage_total: int
    coverage_with_data: int
    truncated: bool = False
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    categories: list[AdvancedKpiCategoryRead] = Field(default_factory=list)
    options: dict[str, list[AdvancedKpiFilterOptionRead]] = Field(default_factory=dict)
    rows: list[AdvancedKpiMetricRead] = Field(default_factory=list)


class QualityRead(CamelCaseSchema):
    score: int
    metrics: dict[str, Any]
    dimensions: list[dict[str, Any]]
    review_queue: list[dict[str, Any]]
    generated_at: datetime


class AudioRequestCreate(WriteSchema):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    transcript: str = Field(min_length=1)
    duration_seconds: int = Field(ge=1, le=3600)
    summary: str | None = None
    transcription_enabled: bool = True
    summary_enabled: bool = True
    voice_reply_requested: bool = False
    idempotency_key: str = Field(min_length=3, max_length=140)


class AudioRequestRead(CamelCaseSchema):
    message: OmnichannelMessageRead
    request_count: int
    gate_passed: bool
    external_intent: ExternalActionIntentRead | None = None
    provider_state: str


class ToolCatalogItemRead(CamelCaseSchema):
    key: str
    name: str
    category: str
    state: str
    external: bool
    human_gate: bool
    capabilities: list[str]


class AgentDefinitionRead(CamelCaseSchema):
    key: str
    version: int = 1
    name: str
    role: str
    group: str
    state: str
    quality_score: int
    queue_size: int
    description: str
    tools: list[str]
    gate: str
    domain: str | None = None
    purpose: str | None = None
    default_autonomy: str = "A1"
    can_delegate: bool = False
    can_write_local: bool = False
    can_prepare_external_action: bool = False
    allowed_models: list[str] = Field(default_factory=list)
    permissions: dict[str, bool]
    model_policy: dict[str, Any]
    area_key: str | None = None
    product_key: str | None = None
    parent_agent_key: str | None = None
    area_enabled: bool | None = None


class AgentRegistryRead(CamelCaseSchema):
    policy_version: str
    governance: dict[str, Any] = Field(default_factory=dict)
    runtime: str
    agents: list[AgentDefinitionRead]
    specialized_agents: list[AgentDefinitionRead] = Field(default_factory=list)
    metrics: dict[str, Any]


class AgentStateUpdate(WriteSchema):
    state: str = Field(pattern="^(active|paused)$")
    reason: str | None = Field(default=None, min_length=3, max_length=500)


class AgentToolsUpdate(WriteSchema):
    tools: list[str] = Field(default_factory=list, max_length=80)
    reason: str | None = Field(default=None, min_length=3, max_length=500)
    expected_etag: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("tools")
    @classmethod
    def normalize_tools(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("tool keys must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("tool keys must be unique")
        return normalized


class AgentAutonomySettingRead(CamelCaseSchema):
    agent_key: str
    autonomy_mode: str
    negotiation_floor_pct: float | None = None
    negotiation_band_pct: float | None = None
    etag: str | None = None


class AgentAutonomyUpdate(WriteSchema):
    # hil_total: aprova cada resposta externa; hil_marcos: aprova só marcos
    # (proposta, contrato, desconto); autonomo: conduz até o aceite do
    # contrato sozinho, dentro da banda pré-aprovada abaixo.
    autonomy_mode: str = Field(pattern="^(hil_total|hil_marcos|autonomo)$")
    negotiation_floor_pct: float | None = Field(default=None, ge=0, le=1)
    negotiation_band_pct: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = Field(default=None, min_length=3, max_length=500)
    expected_etag: str | None = Field(default=None, min_length=8, max_length=128)


_AUTONOMY_LEVEL_PATTERN = "^(A0|A1|A2|A3|A4)$"
_AUTONOMY_RISK_PATTERN = "^(baixo|medio|alto)$"


class AgentAutonomyPolicyRead(CamelCaseSchema):
    """Plano Mestre §14 — política de autonomia por evento. Distinta de
    `AgentAutonomySettingRead` acima (modo HIL simples do agente): esta é a
    regra fina que decide, para um evento concreto, qual nível A0-A4 vale."""

    id: UUID
    name: str
    description: str | None = None
    event_type: str
    conditions: dict[str, Any] = Field(default_factory=dict)
    area_key: str | None = None
    client_id: UUID | None = None
    portfolio_key: str | None = None
    agent_key: str | None = None
    action: str
    autonomy_level: str
    capability_key: str | None = None
    risk_level: str
    min_confidence: float | None = None
    budget_limit: Decimal | None = None
    budget_currency: str
    sla_minutes: int | None = None
    idempotency_key_template: str | None = None
    rollback_strategy: str | None = None
    approval_required: bool
    escalation_target_agent_key: str | None = None
    priority: int
    is_active: bool
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AgentAutonomyPolicyCreate(WriteSchema):
    name: str = Field(min_length=3, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    event_type: str = Field(min_length=2, max_length=120)
    conditions: dict[str, Any] = Field(default_factory=dict)
    area_key: str | None = Field(default=None, max_length=80)
    client_id: UUID | None = None
    portfolio_key: str | None = Field(default=None, max_length=120)
    agent_key: str | None = Field(default=None, max_length=80)
    action: str = Field(min_length=2, max_length=120)
    autonomy_level: str = Field(pattern=_AUTONOMY_LEVEL_PATTERN)
    capability_key: str | None = Field(default=None, max_length=120)
    risk_level: str = Field(default="medio", pattern=_AUTONOMY_RISK_PATTERN)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    budget_limit: Decimal | None = Field(default=None, ge=0)
    budget_currency: str = Field(default="BRL", min_length=3, max_length=3)
    sla_minutes: int | None = Field(default=None, gt=0)
    idempotency_key_template: str | None = Field(default=None, max_length=200)
    rollback_strategy: str | None = Field(default=None, max_length=2000)
    approval_required: bool = True
    escalation_target_agent_key: str | None = Field(default=None, max_length=80)
    priority: int = Field(default=100, ge=0, le=10_000)
    is_active: bool = True


class AgentAutonomyPolicyUpdate(WriteSchema):
    name: str | None = Field(default=None, min_length=3, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    event_type: str | None = Field(default=None, min_length=2, max_length=120)
    conditions: dict[str, Any] | None = None
    area_key: str | None = Field(default=None, max_length=80)
    client_id: UUID | None = None
    portfolio_key: str | None = Field(default=None, max_length=120)
    agent_key: str | None = Field(default=None, max_length=80)
    action: str | None = Field(default=None, min_length=2, max_length=120)
    autonomy_level: str | None = Field(default=None, pattern=_AUTONOMY_LEVEL_PATTERN)
    capability_key: str | None = Field(default=None, max_length=120)
    risk_level: str | None = Field(default=None, pattern=_AUTONOMY_RISK_PATTERN)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    budget_limit: Decimal | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)
    sla_minutes: int | None = Field(default=None, gt=0)
    idempotency_key_template: str | None = Field(default=None, max_length=200)
    rollback_strategy: str | None = Field(default=None, max_length=2000)
    approval_required: bool | None = None
    escalation_target_agent_key: str | None = Field(default=None, max_length=80)
    priority: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None
    reason: str | None = Field(default=None, min_length=3, max_length=500)


class AgentExecutionCreate(WriteSchema):
    agent_key: str = Field(min_length=2, max_length=80)
    matter_id: UUID
    thread_id: UUID | None = None
    request_human: bool = False
    instructions: str | None = Field(default=None, max_length=1500)
    idempotency_key: str = Field(min_length=3, max_length=120)


class AgentHealthSimulationCreate(WriteSchema):
    matter_id: UUID
    thread_id: UUID | None = None
    idempotency_key: str = Field(min_length=3, max_length=120)


class AgentBillingReconciliationCreate(WriteSchema):
    decision: Literal["charged", "not_charged"]
    actual_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=6)
    evidence_reference: str = Field(min_length=3, max_length=200)
    reason: str = Field(min_length=3, max_length=500)
    provider_request_id: str | None = Field(default=None, min_length=3, max_length=200)

    @model_validator(mode="after")
    def validate_decision_cost(self) -> "AgentBillingReconciliationCreate":
        if self.decision == "charged" and self.actual_cost_usd is None:
            raise ValueError("actualCostUsd é obrigatório quando o provider confirmou cobrança.")
        if self.decision == "not_charged" and self.actual_cost_usd not in (None, Decimal("0")):
            raise ValueError("actualCostUsd deve ser zero quando não houve cobrança.")
        return self


class AgentBillingReconciliationPendingPage(CamelCaseSchema):
    """Read-only page of agent runs whose provider billing is still uncertain."""

    total: int
    limit: int
    offset: int
    items: list[AgentRunRead]


class AgentExecutionRead(CamelCaseSchema):
    runs: list[AgentRunRead]
    message_ids: list[UUID]
    approval_id: UUID | None = None
    deduplicated: bool
    external_actions_executed: int
    runtime: str
    policy_version: str
