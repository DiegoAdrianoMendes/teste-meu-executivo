"""Typed contracts for versioned, executable commercial playbooks."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.tenant_cost_privacy import strip_internal_provider_cost_fields
from app.models.playbook import PlaybookLifecycleState, PlaybookSimulationStatus
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.services.agent_governance_service import (
    CORE_PARENT_BY_KEY,
    INDEPENDENT_CONTROL_AGENTS,
)

PlaybookAutonomyMode = Literal[
    "human_only",
    "ai_triage_human_meeting",
    "ai_close_guarded",
    "ai_autonomous_supervised",
]

_TEMPLATE_VARIABLE_PATTERN = re.compile(r"{{\s*([a-z][a-z0-9_.-]{1,119})\s*}}")


def _clean_list(values: list[str], *, field_name: str) -> list[str]:
    cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if any(any(ord(character) < 32 for character in value) for value in cleaned):
        raise ValueError(f"{field_name} contains control characters.")
    return cleaned


class PlaybookScope(WriteSchema):
    areas: list[str] = Field(min_length=1, max_length=50)
    channels: list[str] = Field(default_factory=list, max_length=30)
    stages: list[str] = Field(default_factory=list, max_length=50)
    agents: list[str] = Field(default_factory=list, max_length=50)
    offers: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("areas", "channels", "stages", "agents", "offers")
    @classmethod
    def normalize_values(cls, values: list[str], info) -> list[str]:
        return _clean_list(values, field_name=info.field_name)


class PlaybookVoicePolicy(WriteSchema):
    positioning: str = Field(min_length=1, max_length=2000)
    tone: str = Field(min_length=1, max_length=1200)
    vocabulary: list[str] = Field(default_factory=list, max_length=100)
    forbidden_vocabulary: list[str] = Field(default_factory=list, max_length=100)
    formality: Literal["conversational", "balanced", "formal"] = "balanced"
    max_message_characters: int = Field(default=700, ge=80, le=4096)
    greeting: str = Field(min_length=1, max_length=1000)
    closing: str = Field(min_length=1, max_length=1000)
    allowed_claims: list[str] = Field(default_factory=list, max_length=100)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=100)
    examples: list[dict[str, str]] = Field(default_factory=list, max_length=50)


class PlaybookAudiencePolicy(WriteSchema):
    personas: list[str] = Field(default_factory=list, max_length=100)
    pains: list[str] = Field(default_factory=list, max_length=100)
    urgency_signals: list[str] = Field(default_factory=list, max_length=100)
    intent_signals: list[str] = Field(default_factory=list, max_length=100)
    eligibility_signals: list[str] = Field(default_factory=list, max_length=100)
    exclusion_signals: list[str] = Field(default_factory=list, max_length=100)


class PlaybookDiscoveryQuestion(WriteSchema):
    id: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=2000)
    required: bool = True
    condition: str | None = Field(default=None, max_length=1000)
    do_not_repeat_when: str = Field(min_length=1, max_length=1200)
    confirmation: str = Field(min_length=1, max_length=1200)


class PlaybookTriagePolicy(WriteSchema):
    subthemes: list[str] = Field(default_factory=list, max_length=100)
    required_documents: list[str] = Field(default_factory=list, max_length=100)
    sufficiency_rule: str = Field(min_length=1, max_length=4000)
    legal_consult_triggers: list[str] = Field(default_factory=list, max_length=100)
    urgency_rules: list[str] = Field(default_factory=list, max_length=100)
    denial_evidence: list[str] = Field(default_factory=list, max_length=100)
    medication_fields: list[str] = Field(default_factory=list, max_length=100)


class PlaybookScoreRule(WriteSchema):
    label: str = Field(min_length=1, max_length=240)
    points: int = Field(ge=-100, le=100)
    evidence: str = Field(min_length=1, max_length=1000)


class PlaybookQualificationPolicy(WriteSchema):
    score_threshold: int = Field(default=70, ge=0, le=100)
    score_rules: list[PlaybookScoreRule] = Field(default_factory=list, max_length=100)
    required_fields: list[str] = Field(default_factory=list, max_length=100)
    next_action_rules: list[str] = Field(default_factory=list, max_length=100)
    disqualification_reasons: list[str] = Field(default_factory=list, max_length=100)


class PlaybookCommercialPolicy(WriteSchema):
    minimum_initial_fee_brl: float = Field(default=4000, ge=4000)
    maximum_initial_fee_brl: float | None = Field(default=None, ge=4000)
    success_only_allowed: bool = True
    success_only_rule: str = Field(min_length=1, max_length=4000)
    intermediate_fee_allowed: bool = True
    obligation_to_do_fee_rule: str = Field(min_length=1, max_length=4000)
    success_only_moral_damages_percentage: float = Field(default=100, ge=0, le=100)
    oncology_obligation_fee_minimum_brl: float = Field(default=100_000, ge=0)
    oncology_obligation_fee_maximum_brl: float = Field(default=300_000, ge=0)
    require_treatment_cycle_for_medication_value: bool = True
    require_human_review: bool = True
    policy_note: str = Field(
        default=(
            "Entrada abaixo do mínimo somente no modo integralmente no êxito; "
            "valores de obrigação de fazer dependem do custo prescrito e de aprovação humana."
        ),
        max_length=2000,
    )
    discount_limit_percent: float = Field(default=10, ge=0, le=100)
    proposal_validity_days: int = Field(default=5, ge=1, le=90)
    human_approval_above_brl: float = Field(default=50000, ge=0)
    payment_options: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def maximum_not_below_minimum(self):
        if (
            self.maximum_initial_fee_brl is not None
            and self.maximum_initial_fee_brl < self.minimum_initial_fee_brl
        ):
            raise ValueError("maximum_initial_fee_brl cannot be below the minimum.")
        if self.oncology_obligation_fee_maximum_brl < self.oncology_obligation_fee_minimum_brl:
            raise ValueError("oncology_obligation_fee_maximum_brl cannot be below the minimum.")
        return self


class PlaybookObjection(WriteSchema):
    id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=240)
    diagnosis: str = Field(min_length=1, max_length=3000)
    confirmation_question: str = Field(min_length=1, max_length=2000)
    allowed_response: str = Field(min_length=1, max_length=5000)
    proof: str = Field(min_length=1, max_length=3000)
    next_best_action: str = Field(min_length=1, max_length=2000)
    max_attempts: int = Field(default=2, ge=1, le=5)
    escalation_criteria: str = Field(min_length=1, max_length=3000)


class PlaybookProposalPolicy(WriteSchema):
    desktop_template: str = Field(min_length=1, max_length=240)
    whatsapp_template: str = Field(min_length=1, max_length=240)
    required_blocks: list[str] = Field(default_factory=list, max_length=50)
    call_to_action: str = Field(min_length=1, max_length=2000)
    approval_required: bool = True
    analytics_events: list[str] = Field(default_factory=list, max_length=100)


class PlaybookCadenceStep(WriteSchema):
    id: str = Field(min_length=1, max_length=160)
    delay_minutes: int = Field(ge=0, le=525600)
    channel: str = Field(min_length=1, max_length=80)
    message_intent: str = Field(min_length=1, max_length=2000)
    typing_delay_seconds: int = Field(default=8, ge=0, le=300)
    stop_when: list[str] = Field(default_factory=list, max_length=50)


class PlaybookFollowUpPolicy(WriteSchema):
    lead_never_unanswered: bool = True
    business_hours: str = Field(min_length=1, max_length=160)
    max_messages_per_day: int = Field(default=3, ge=1, le=20)
    anti_spam_window_minutes: int = Field(default=180, ge=1, le=10080)
    cadences: dict[str, list[PlaybookCadenceStep]] = Field(default_factory=dict)


class PlaybookHandoffDestination(WriteSchema):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=240)
    sla_minutes: int = Field(ge=1, le=10080)


class PlaybookHandoffPolicy(WriteSchema):
    normal_dispatcher: Literal["orquestrador"] = "orquestrador"
    fallback_queue: str = Field(min_length=1, max_length=160)
    required_summary_fields: list[str] = Field(default_factory=list, max_length=100)
    destinations: list[PlaybookHandoffDestination] = Field(default_factory=list, max_length=100)

    @field_validator("normal_dispatcher", mode="before")
    @classmethod
    def migrate_legacy_dispatcher(cls, value: Any) -> str:
        if value == "hermes":
            return "orquestrador"
        return str(value or "orquestrador")


class PlaybookAgentContract(WriteSchema):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=3000)
    parent_agent_key: str | None = Field(default=None, max_length=120)
    independent_control: bool = False
    inputs: list[str] = Field(default_factory=list, max_length=100)
    tools: list[str] = Field(default_factory=list, max_length=100)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    sla_minutes: int = Field(ge=1, le=10080)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    prohibitions: list[str] = Field(default_factory=list, max_length=100)
    handoffs: list[str] = Field(default_factory=list, max_length=100)
    fallback: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def enforce_canonical_parent(self):
        if self.key not in CORE_PARENT_BY_KEY:
            if self.independent_control:
                raise ValueError("Independent control is reserved for platform agents.")
            if self.parent_agent_key is None:
                raise ValueError(
                    f"Custom agent '{self.key}' must report to a registered parent."
                )
            return self
        expected_parent = CORE_PARENT_BY_KEY[self.key]
        if self.parent_agent_key is None and expected_parent is not None:
            self.parent_agent_key = expected_parent
        if self.parent_agent_key != expected_parent:
            raise ValueError(
                f"Agent '{self.key}' must report to '{expected_parent or 'system'}'."
            )
        expected_independent = self.key in INDEPENDENT_CONTROL_AGENTS
        if self.independent_control not in {False, expected_independent}:
            raise ValueError(f"Agent '{self.key}' has an invalid independent-control flag.")
        self.independent_control = expected_independent
        return self


class PlaybookPolicy(WriteSchema):
    autonomy_mode: PlaybookAutonomyMode = "ai_triage_human_meeting"
    stage_modes: dict[str, PlaybookAutonomyMode] = Field(default_factory=dict)
    hard_gates: list[str] = Field(default_factory=list, max_length=100)
    kill_switch_enabled: bool = True
    minimum_confidence: float = Field(default=0.85, ge=0.5, le=1)


class InternalPlaybookPolicy(PlaybookPolicy):
    """Persisted policy fields reserved for trusted platform code."""

    daily_budget_brl: float = Field(default=50, ge=0, le=1_000_000)


class PlaybookExperiment(WriteSchema):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    variant: str = Field(max_length=3000)
    control: str = Field(max_length=3000)
    metric: str = Field(max_length=500)
    stop_criteria: str = Field(max_length=2000)


class PlaybookBrandKit(WriteSchema):
    brand_name: str = Field(default="Meu Executivo", min_length=1, max_length=160)
    logo_object_id: UUID | None = None
    logo_object_version: int | None = Field(default=None, ge=1)
    logo_filename: str | None = Field(default=None, max_length=255)
    primary_color: str = Field(default="#050505", pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: str = Field(default="#B58A35", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str = Field(default="#0B7435", pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str = Field(default="#111318", pattern=r"^#[0-9A-Fa-f]{6}$")
    background_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    heading_font: str = Field(default="Manrope", min_length=1, max_length=120)
    body_font: str = Field(default="Inter", min_length=1, max_length=120)
    letterhead_enabled: bool = True
    letterhead_header: str = Field(
        default="Meu Executivo | Direito da Saúde",
        max_length=1000,
    )
    letterhead_footer: str = Field(
        default="Atendimento on-line em todo o Brasil",
        max_length=1000,
    )
    contact_lines: list[str] = Field(default_factory=list, max_length=20)
    signature_name: str = Field(default="Meu Executivo", max_length=240)
    signature_title: str = Field(default="Equipe jurídica", max_length=240)
    disclaimer: str = Field(default="", max_length=3000)
    page_size: Literal["A4", "letter"] = "A4"

    @model_validator(mode="after")
    def logo_reference_is_version_pinned(self):
        if (self.logo_object_id is None) != (self.logo_object_version is None):
            raise ValueError("logo_object_id and logo_object_version must be provided together.")
        if self.logo_object_id is None and self.logo_filename:
            raise ValueError("logo_filename requires a version-pinned logo object.")
        return self


class PlaybookDocumentVariable(WriteSchema):
    key: str = Field(min_length=2, max_length=120, pattern=r"^[a-z][a-z0-9_.-]+$")
    label: str = Field(min_length=1, max_length=240)
    source: Literal[
        "brand",
        "client",
        "case",
        "conversation",
        "document",
        "financial",
        "meeting",
        "manual",
        "system",
    ]
    source_path: str = Field(min_length=1, max_length=240)
    required: bool = False
    sensitive: bool = False
    allow_agent_write: bool = False
    fallback_policy: Literal["hard_gate", "human_input", "omit"] = "human_input"
    fallback_text: str | None = Field(default=None, max_length=1000)


class PlaybookDocumentSection(WriteSchema):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=30000)
    required: bool = True


class PlaybookDocumentTemplate(WriteSchema):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    document_type: Literal[
        "proposal",
        "contract",
        "power_of_attorney",
        "petition",
        "client_update",
        "meeting_brief",
        "email",
        "whatsapp_pdf",
        "invoice",
        "other",
    ] = "other"
    channel: Literal["document", "whatsapp", "email", "internal"] = "document"
    output_format: Literal["pdf", "docx", "google_doc", "html", "message"] = "pdf"
    layout: Literal[
        "slides_16_9",
        "whatsapp_vertical",
        "letterhead_a4",
        "document_a4",
        "message",
    ] = "document_a4"
    template_version: int = Field(default=1, ge=1, le=10000)
    status: Literal["draft", "approved", "archived"] = "draft"
    sections: list[PlaybookDocumentSection] = Field(default_factory=list, max_length=100)
    required_variables: list[str] = Field(default_factory=list, max_length=200)
    applicable_stages: list[str] = Field(default_factory=list, max_length=100)
    applicable_subthemes: list[str] = Field(default_factory=list, max_length=100)
    agent_keys: list[str] = Field(default_factory=list, max_length=100)
    source_object_id: UUID | None = None
    source_object_version: int | None = Field(default=None, ge=1)
    source_filename: str | None = Field(default=None, max_length=255)
    approval_required: bool = True
    active: bool = True

    @field_validator(
        "required_variables",
        "applicable_stages",
        "applicable_subthemes",
        "agent_keys",
    )
    @classmethod
    def normalize_lists(cls, values: list[str], info) -> list[str]:
        return _clean_list(values, field_name=info.field_name)

    @model_validator(mode="after")
    def source_reference_is_version_pinned(self):
        if (self.source_object_id is None) != (self.source_object_version is None):
            raise ValueError(
                "source_object_id and source_object_version must be provided together."
            )
        if self.source_object_id is None and self.source_filename:
            raise ValueError("source_filename requires a version-pinned source object.")
        section_ids = [section.id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError(f"Document template {self.id} contains duplicate section ids.")
        return self


PlaybookOfficeTemplateKey = Literal[
    "contrato_honorarios",
    "peticao_inicial",
    "procuracao",
    "proposta",
    "outro",
]

OFFICE_TEMPLATE_LABELS: dict[str, str] = {
    "contrato_honorarios": "Contrato de honorários",
    "peticao_inicial": "Petição inicial",
    "procuracao": "Procuração",
    "proposta": "Proposta comercial",
    "outro": "Outro modelo",
}


class PlaybookOfficeTemplate(WriteSchema):
    """One firm-branded source document (already lettered/formatted) that every
    generation of that document type must use as its base — never a generic
    fallback. Pins to an immutable, version-scoped `StorageObject` uploaded
    through the existing document-upload flow, the same way `brand_kit.logo`
    and `PlaybookDocumentTemplate.source_object_id` are pinned."""

    key: PlaybookOfficeTemplateKey
    label: str = Field(min_length=1, max_length=240)
    document_object_id: UUID | None = None
    document_object_version: int | None = Field(default=None, ge=1)
    document_filename: str | None = Field(default=None, max_length=255)
    formato: Literal["docx"] = "docx"
    obrigatorio: bool = True

    @model_validator(mode="after")
    def document_reference_is_version_pinned(self):
        if (self.document_object_id is None) != (self.document_object_version is None):
            raise ValueError(
                "document_object_id and document_object_version must be provided together."
            )
        if self.document_object_id is None and self.document_filename:
            raise ValueError("document_filename requires a version-pinned document object.")
        return self

    @property
    def enviado(self) -> bool:
        return self.document_object_id is not None


class PlaybookFaqEntry(WriteSchema):
    id: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=2000)
    approved_answer: str = Field(min_length=1, max_length=8000)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    allowed_channels: list[str] = Field(default_factory=list, max_length=30)
    applicable_stages: list[str] = Field(default_factory=list, max_length=100)
    agent_keys: list[str] = Field(default_factory=list, max_length=100)
    confidence_threshold: float = Field(default=0.9, ge=0.5, le=1)
    requires_human_review: bool = False
    active: bool = True

    @field_validator(
        "keywords",
        "source_refs",
        "allowed_channels",
        "applicable_stages",
        "agent_keys",
    )
    @classmethod
    def normalize_lists(cls, values: list[str], info) -> list[str]:
        return _clean_list(values, field_name=info.field_name)


class PlaybookResponseTemplate(WriteSchema):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    intent: str = Field(min_length=1, max_length=160)
    channel: Literal["whatsapp", "instagram", "messenger", "email", "internal"]
    stage: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=8000)
    variables: list[str] = Field(default_factory=list, max_length=100)
    agent_keys: list[str] = Field(default_factory=list, max_length=100)
    approval_required: bool = False
    active: bool = True

    @field_validator("variables", "agent_keys")
    @classmethod
    def normalize_lists(cls, values: list[str], info) -> list[str]:
        return _clean_list(values, field_name=info.field_name)


class PlaybookContent(WriteSchema):
    identity_voice: PlaybookVoicePolicy
    audience_intent: PlaybookAudiencePolicy
    discovery: list[PlaybookDiscoveryQuestion] = Field(default_factory=list, max_length=100)
    legal_triage: PlaybookTriagePolicy
    qualification: PlaybookQualificationPolicy
    commercial: PlaybookCommercialPolicy
    objections: list[PlaybookObjection] = Field(default_factory=list, max_length=100)
    proposals: PlaybookProposalPolicy
    brand_kit: PlaybookBrandKit = Field(default_factory=PlaybookBrandKit)
    document_templates: list[PlaybookDocumentTemplate] = Field(
        default_factory=list,
        max_length=200,
    )
    office_templates: list[PlaybookOfficeTemplate] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Modelos timbrados do próprio escritório (um por tipo canônico de "
            "documento). Toda geração daquele tipo usa este arquivo como base; "
            "sem modelo enviado a geração fica bloqueada (nunca cai num modelo "
            "genérico)."
        ),
    )
    faqs: list[PlaybookFaqEntry] = Field(default_factory=list, max_length=500)
    response_templates: list[PlaybookResponseTemplate] = Field(
        default_factory=list,
        max_length=500,
    )
    document_variables: list[PlaybookDocumentVariable] = Field(
        default_factory=list,
        max_length=500,
    )
    follow_up: PlaybookFollowUpPolicy
    handoff: PlaybookHandoffPolicy
    post_sale: list[str] = Field(default_factory=list, max_length=100)
    experiments: list[PlaybookExperiment] = Field(default_factory=list, max_length=100)
    agents: list[PlaybookAgentContract] = Field(default_factory=list, max_length=100)
    policy: PlaybookPolicy

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_human_queue_handoff(cls, data):
        # Documentos persistidos do modelo anterior colocavam a transferência
        # para a fila humana (`fila_humana`) em agentes que não o orquestrador.
        # O modelo vigente centraliza isso no orquestrador; migra o dado legado
        # ao carregar para não quebrar a validação (mesmo padrão dos gates).
        if not isinstance(data, dict):
            return data
        agents = data.get("agents")
        if not isinstance(agents, list):
            return data
        moved = False
        for agent in agents:
            if not isinstance(agent, dict) or agent.get("key") == "orquestrador":
                continue
            handoffs = agent.get("handoffs")
            if isinstance(handoffs, list) and "fila_humana" in handoffs:
                agent["handoffs"] = [h for h in handoffs if h != "fila_humana"]
                moved = True
        if moved:
            for agent in agents:
                if isinstance(agent, dict) and agent.get("key") == "orquestrador":
                    handoffs = agent.setdefault("handoffs", [])
                    if isinstance(handoffs, list) and "fila_humana" not in handoffs:
                        handoffs.append("fila_humana")
                    break
        return data

    @model_validator(mode="after")
    def validate_studio_library(self):
        collections = {
            "document_templates": self.document_templates,
            "faqs": self.faqs,
            "response_templates": self.response_templates,
            "document_variables": self.document_variables,
        }
        for label, values in collections.items():
            identifiers = [
                value.key if label == "document_variables" else value.id for value in values
            ]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} contains duplicate identifiers.")

        office_keys = [template.key for template in self.office_templates]
        if len(office_keys) != len(set(office_keys)):
            raise ValueError("office_templates contains duplicate keys.")

        catalog = {variable.key for variable in self.document_variables}
        for template in self.document_templates:
            rendered_text = "\n".join(
                [template.name]
                + [section.title for section in template.sections]
                + [section.body for section in template.sections]
            )
            referenced = set(_TEMPLATE_VARIABLE_PATTERN.findall(rendered_text))
            referenced.update(template.required_variables)
            unknown = sorted(referenced - catalog)
            if unknown:
                raise ValueError(
                    f"Document template {template.id} references unknown variables: "
                    + ", ".join(unknown)
                )
        for template in self.response_templates:
            referenced = set(_TEMPLATE_VARIABLE_PATTERN.findall(template.body))
            referenced.update(template.variables)
            unknown = sorted(referenced - catalog)
            if unknown:
                raise ValueError(
                    f"Response template {template.id} references unknown variables: "
                    + ", ".join(unknown)
                )

        agent_by_key = {agent.key: agent for agent in self.agents}
        if len(agent_by_key) != len(self.agents):
            raise ValueError("agents contains duplicate identifiers.")
        for agent in self.agents:
            if agent.parent_agent_key and agent.parent_agent_key not in agent_by_key:
                raise ValueError(
                    f"Agent '{agent.key}' references an absent parent: "
                    f"{agent.parent_agent_key}."
                )
            if agent.parent_agent_key == "hermes":
                raise ValueError("Hermes cannot orchestrate agents or subagents.")
            if agent.parent_agent_key in INDEPENDENT_CONTROL_AGENTS:
                raise ValueError("Independent control agents cannot own delegates.")
            for target in agent.handoffs:
                if target == "fila_humana":
                    if agent.key != "orquestrador":
                        raise ValueError(
                            "Somente o Orquestrador pode colocar uma conversa na fila humana."
                        )
                    continue
                target_agent = agent_by_key.get(target)
                if target_agent is None:
                    raise ValueError(
                        f"Agent '{agent.key}' references an unknown handoff target: '{target}'."
                    )
                if target_agent.parent_agent_key != agent.key:
                    raise ValueError(
                        f"Direct handoff '{agent.key}' → '{target}' violates the agent hierarchy."
                    )

        for key in agent_by_key:
            visited: set[str] = set()
            cursor: str | None = key
            while cursor is not None and cursor in agent_by_key:
                if cursor in visited:
                    raise ValueError(f"Agent hierarchy contains a cycle at '{key}'.")
                visited.add(cursor)
                cursor = agent_by_key[cursor].parent_agent_key
            if len(visited) - 1 > 4:
                raise ValueError(f"Agent '{key}' exceeds the maximum delegation depth.")
        return self


class InternalPlaybookContent(PlaybookContent):
    """Stored content with platform-only operational controls."""

    policy: InternalPlaybookPolicy


class PlaybookDocumentPayload(WriteSchema):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=1, max_length=600)
    scope: PlaybookScope
    content: PlaybookContent
    revision_nonce: str = Field(min_length=8, max_length=80)


class InternalPlaybookDocumentPayload(PlaybookDocumentPayload):
    """Version payload retained internally and narrowed at tenant serialization."""

    content: InternalPlaybookContent


class PlaybookCreate(WriteSchema):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=1, max_length=600)
    scope: PlaybookScope
    autonomy_mode: PlaybookAutonomyMode = "ai_triage_human_meeting"
    clone_from_id: UUID | None = None


class PlaybookClone(WriteSchema):
    name: str = Field(min_length=2, max_length=160)


class PlaybookDraftWrite(WriteSchema):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=1, max_length=600)
    scope: PlaybookScope
    content: PlaybookContent
    reason: str = Field(min_length=1, max_length=500)
    expected_etag: str = Field(min_length=1, max_length=128)


class PlaybookActionWrite(WriteSchema):
    reason: str = Field(min_length=1, max_length=500)


class PlaybookSimulationWrite(WriteSchema):
    scenario: str = Field(min_length=1, max_length=240)
    customer_message: str = Field(min_length=1, max_length=4096)
    context: dict[str, Any] = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)

    @field_validator("context", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any) -> Any:
        return strip_internal_provider_cost_fields(value)


class PlaybookVersionRead(CamelCaseSchema):
    id: UUID
    version: int
    state: PlaybookLifecycleState
    payload_hash: str
    reason: str | None = None
    created_by_user_id: UUID | None = None
    reviewed_by_user_id: UUID | None = None
    approved_by_user_id: UUID | None = None
    published_by_user_id: UUID | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    approved_at: datetime | None = None
    published_at: datetime | None = None


class PlaybookSummaryRead(CamelCaseSchema):
    id: UUID
    name: str
    slug: str
    description: str
    state: PlaybookLifecycleState
    current_version: int
    published_version: int | None = None
    etag: str
    scope: PlaybookScope
    autonomy_mode: PlaybookAutonomyMode
    structural_coverage: float
    simulation_pass_rate: float
    simulations: int
    updated_at: datetime


class PlaybookDetailRead(PlaybookSummaryRead):
    content: PlaybookContent
    versions: list[PlaybookVersionRead]


class PlaybookMetricsRead(CamelCaseSchema):
    simulations: int
    pass_rate: float
    structural_coverage: float
    handoff_rate: float
    autonomous_resolution_rate: float
    average_tokens: float
    objections_covered: int
    agents_covered: int
    last_evaluated_at: datetime | None = None


class PlaybookPolicyTraceRead(CamelCaseSchema):
    rule: str
    outcome: str
    reason: str


class PlaybookSimulationRead(CamelCaseSchema):
    id: UUID
    playbook_id: UUID
    version: int
    status: PlaybookSimulationStatus
    scenario: str
    customer_message: str
    response: str
    next_action: str
    policy_trace: list[PlaybookPolicyTraceRead]
    sources: list[str]
    autonomy_mode: PlaybookAutonomyMode
    human_approval_required: bool
    external_actions_executed: int
    prompt_tokens: int
    completion_tokens: int
    created_at: datetime


class PlaybookDiffEntryRead(CamelCaseSchema):
    path: str
    before: Any = None
    after: Any = None
    change: Literal["added", "removed", "changed"]

    @field_validator("before", "after", mode="before")
    @classmethod
    def hide_internal_provider_costs(cls, value: Any) -> Any:
        return strip_internal_provider_cost_fields(value)


class PlaybookDiffRead(CamelCaseSchema):
    from_version: int
    to_version: int
    entries: list[PlaybookDiffEntryRead]
