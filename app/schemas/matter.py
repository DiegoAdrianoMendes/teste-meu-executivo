"""
Schemas Pydantic para Matter (MatterCard).
Compatíveis com a interface MatterCard do frontend TypeScript.

Serialização: snake_case no banco → camelCase no JSON (via CamelCaseSchema).
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field, field_serializer, model_validator

from app.models.matter import (
    ClientClassification, HealthSubtheme, KanbanColumnId, MicroStatus, PracticeArea, Priority, Urgency
)
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.client import ClientRead, ClientUpdate
from app.schemas.card_dossier import (
    AuditLogRead,
    CampaignAttributionRead,
    CardFieldChangeRead,
    CardScoreRead,
    ConversationInsightRead,
    HonorariumEstimateRead,
    RevenueProjectionRead,
)
from app.schemas.agent_runtime import AgentRunRead, HumanOverrideRead
from app.schemas.knowledge_chunk import KnowledgeChunkRead
from app.schemas.medication import MatterTreatmentEstimateRead
from app.schemas.task import TaskRead
from app.schemas.document import DocumentRead
from app.schemas.timeline_event import TimelineEventRead
from app.schemas.conversation import ConversationRead


class MatterCreate(WriteSchema):
    client_id: UUID
    practice_area: PracticeArea = PracticeArea.trabalhista
    demand_type: str
    lead_source: Optional[str] = None
    tags: List[str] = Field(default_factory=list, max_length=30)
    column_id: KanbanColumnId = KanbanColumnId.entrada
    micro_status: MicroStatus = MicroStatus.lead_novo
    priority: Priority = Priority.media
    urgency: Urgency = Urgency.normal
    classification: ClientClassification = ClientClassification.Bom
    responsible_human_id: Optional[UUID] = None
    claim_value: Optional[float] = None
    case_number: Optional[str] = None
    executive_summary: Optional[str] = None
    legal_area_key: Optional[str] = Field(default=None, max_length=80)
    legal_product_key: Optional[str] = Field(default=None, max_length=100)
    legal_product_custom: Optional[str] = Field(default=None, max_length=180)
    legal_object_name: Optional[str] = Field(default=None, max_length=300)
    counterparty_name: Optional[str] = Field(default=None, max_length=240)
    legal_issue_summary: Optional[str] = None
    legal_context: dict = Field(default_factory=dict)
    health_subtheme: Optional[HealthSubtheme] = None
    health_subtheme_custom: Optional[str] = Field(default=None, max_length=160)
    health_object_name: Optional[str] = None
    health_insurer: Optional[str] = None
    health_denial_reason: Optional[str] = None
    medical_urgency_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    economic_value_annual: Optional[float] = Field(default=None, ge=0)
    lead_score: int = Field(default=0, ge=0, le=100)
    next_action: Optional[str] = None
    next_deadline: Optional[str] = None
    strategy_notes: Optional[str] = None
    risks: List[str] = Field(default_factory=list)
    blocks: List[str] = Field(default_factory=list)


class MatterUpdate(WriteSchema):
    """Atualização parcial de campos do card."""
    demand_type: Optional[str] = None
    lead_source: Optional[str] = None
    tags: Optional[List[str]] = Field(default=None, max_length=30)
    column_id: Optional[KanbanColumnId] = None
    micro_status: Optional[MicroStatus] = None
    priority: Optional[Priority] = None
    urgency: Optional[Urgency] = None
    classification: Optional[ClientClassification] = None
    stars_rating: Optional[int] = None
    next_action: Optional[str] = None
    next_deadline: Optional[str] = None
    next_hearing: Optional[str] = None
    is_blocked: Optional[bool] = None
    needs_approval: Optional[bool] = None
    doc_score: Optional[int] = None
    production_score: Optional[int] = None
    strategy_notes: Optional[str] = None
    risks: Optional[List[str]] = None
    blocks: Optional[List[str]] = None
    responsible_human_id: Optional[UUID] = None
    current_agent_id: Optional[UUID] = None
    claim_value: Optional[float] = None
    case_number: Optional[str] = None
    executive_summary: Optional[str] = None
    practice_area: Optional[PracticeArea] = None
    legal_area_key: Optional[str] = Field(default=None, max_length=80)
    legal_product_key: Optional[str] = Field(default=None, max_length=100)
    legal_product_custom: Optional[str] = Field(default=None, max_length=180)
    legal_object_name: Optional[str] = Field(default=None, max_length=300)
    counterparty_name: Optional[str] = Field(default=None, max_length=240)
    legal_issue_summary: Optional[str] = None
    legal_context: Optional[dict] = None
    health_subtheme: Optional[HealthSubtheme] = None
    health_subtheme_custom: Optional[str] = Field(default=None, max_length=160)
    health_object_name: Optional[str] = None
    health_insurer: Optional[str] = None
    health_denial_reason: Optional[str] = None
    medical_urgency_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    economic_value_annual: Optional[float] = Field(default=None, ge=0)
    lead_score: Optional[int] = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def reject_stage_changes(self) -> "MatterUpdate":
        """Force Kanban moves through the audited, versioned move command."""
        stage_fields = {"column_id", "micro_status"}
        if stage_fields.intersection(self.model_fields_set):
            raise ValueError(
                "Etapa e microstatus devem ser alterados pelo comando de movimentação do Kanban."
            )
        if self.tags is not None:
            normalized = []
            seen: set[str] = set()
            for raw_tag in self.tags:
                tag = raw_tag.strip()[:60]
                key = tag.casefold()
                if tag and key not in seen:
                    seen.add(key)
                    normalized.append(tag)
            self.tags = normalized
        if self.health_subtheme_custom is not None:
            self.health_subtheme_custom = self.health_subtheme_custom.strip() or None
        for field_name in (
            "legal_area_key",
            "legal_product_key",
            "legal_product_custom",
            "legal_object_name",
            "counterparty_name",
            "legal_issue_summary",
        ):
            value = getattr(self, field_name)
            if isinstance(value, str):
                setattr(self, field_name, value.strip() or None)
        return self


class MatterMoveRequest(WriteSchema):
    """Payload para mover um card no Kanban."""
    column_id: KanbanColumnId
    micro_status: Optional[MicroStatus] = None


class Case360AttributionUpdate(WriteSchema):
    """Manual correction of the commercial attribution shown in Case 360."""

    source: Optional[str] = Field(default=None, max_length=100)
    medium: Optional[str] = Field(default=None, max_length=100)
    campaign: Optional[str] = Field(default=None, max_length=150)
    content: Optional[str] = Field(default=None, max_length=150)
    term: Optional[str] = Field(default=None, max_length=150)
    channel: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = None


class Case360Update(WriteSchema):
    """Atomic quick-edit payload used by the conversation-side case dossier."""

    client: Optional[ClientUpdate] = None
    attribution: Optional[Case360AttributionUpdate] = None
    matter: MatterUpdate


class MatterRead(CamelCaseSchema):
    """
    Schema completo do MatterCard — espelha a interface MatterCard do frontend.
    Inclui todos os dados aninhados para popular o DetailDrawer.

    Campos mapeados (backend → frontend):
      practice_area     → practiceArea
      column_id         → columnId
      micro_status      → microStatus
      stars_rating      → starsRating
      assigned_agent_ids → assignedAgents (lista de UUIDs como string)
      next_action       → nextAction
      current_agent_id  → currentAgent
      responsible_human_id → responsibleHuman
      last_interaction  → lastInteraction
      unread_messages   → unreadMessages
      open_tasks        → openTasks
      is_blocked        → isBlocked
      needs_approval    → needsApproval
      doc_score         → docScore
      production_score  → productionScore
      strategy_notes    → strategyNotes
      next_deadline     → nextDeadline
      next_hearing      → nextHearing
      claim_value       → claimValue
      created_at        → createdAt
      updated_at        → updatedAt
    """
    id: UUID
    practice_area: PracticeArea
    case_number: Optional[str] = None
    column_id: KanbanColumnId
    micro_status: MicroStatus
    priority: Priority
    urgency: Urgency
    classification: ClientClassification
    demand_type: str
    lead_source: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    stars_rating: int
    executive_summary: Optional[str] = None
    legal_area_key: Optional[str] = None
    legal_product_key: Optional[str] = None
    legal_product_custom: Optional[str] = None
    legal_object_name: Optional[str] = None
    counterparty_name: Optional[str] = None
    legal_issue_summary: Optional[str] = None
    legal_context: dict = Field(default_factory=dict)
    health_subtheme: Optional[HealthSubtheme] = None
    health_subtheme_custom: Optional[str] = None
    health_object_name: Optional[str] = None
    health_insurer: Optional[str] = None
    health_denial_reason: Optional[str] = None
    medical_urgency_hours: Optional[int] = None
    economic_value_annual: Optional[float] = None
    lead_score: int = 0
    manual_context_revision: int = 0
    manual_context_updated_at: Optional[datetime] = None
    next_action: Optional[str] = None
    # Retornados como UUID (serializado automaticamente para string pelo Pydantic)
    current_agent_id: Optional[UUID] = None
    responsible_human_id: Optional[UUID] = None
    last_interaction: Optional[str] = None
    unread_messages: int
    open_tasks: int
    is_blocked: bool
    needs_approval: bool
    doc_score: int
    production_score: int
    strategy_notes: Optional[str] = None
    risks: List[str] = Field(default_factory=list)
    blocks: List[str] = Field(default_factory=list)
    # Lista de UUIDs (serializado automaticamente para string pelo Pydantic)
    assigned_agent_ids: List[UUID] = Field(default_factory=list)
    next_deadline: Optional[str] = None
    next_hearing: Optional[str] = None
    claim_value: Optional[float] = None
    card_order: int = 0
    created_at: datetime
    updated_at: datetime

    # Dados aninhados — nomes alinhados com o frontend
    client: ClientRead
    tasks: List[TaskRead] = Field(default_factory=list)
    documents: List[DocumentRead] = Field(default_factory=list)
    # frontend: timeline: TimelineEvent[]
    timeline: List[TimelineEventRead] = Field(default_factory=list)
    # frontend: conversations: ConversationItem[]
    conversations: List[ConversationRead] = Field(default_factory=list)
    campaign_attribution: Optional[CampaignAttributionRead] = None
    revenue_projection: Optional[RevenueProjectionRead] = None
    honorarium_estimate: Optional[HonorariumEstimateRead] = None
    treatment_estimates: List[MatterTreatmentEstimateRead] = Field(default_factory=list)
    card_score: Optional[CardScoreRead] = None
    field_changes: List[CardFieldChangeRead] = Field(default_factory=list)
    audit_logs: List[AuditLogRead] = Field(default_factory=list)
    agent_runs: List[AgentRunRead] = Field(default_factory=list)
    human_overrides: List[HumanOverrideRead] = Field(default_factory=list)
    knowledge_chunks: List[KnowledgeChunkRead] = Field(default_factory=list)
    conversation_insight: Optional[ConversationInsightRead] = None
    zapsign_doc_token: Optional[str] = None
    zapsign_sign_url: Optional[str] = None
    zapsign_status: Optional[str] = None


class MatterListItem(CamelCaseSchema):
    """
    Versão compacta do card para listagem no Kanban e na Lista.
    Evita carregar dados pesados desnecessariamente.
    """
    id: UUID
    practice_area: PracticeArea
    case_number: Optional[str] = None
    column_id: KanbanColumnId
    micro_status: MicroStatus
    priority: Priority
    urgency: Urgency
    classification: ClientClassification
    demand_type: str
    lead_source: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    stars_rating: int
    executive_summary: Optional[str] = None
    legal_area_key: Optional[str] = None
    legal_product_key: Optional[str] = None
    legal_product_custom: Optional[str] = None
    legal_object_name: Optional[str] = None
    counterparty_name: Optional[str] = None
    legal_issue_summary: Optional[str] = None
    health_subtheme: Optional[HealthSubtheme] = None
    health_subtheme_custom: Optional[str] = None
    health_object_name: Optional[str] = None
    health_insurer: Optional[str] = None
    medical_urgency_hours: Optional[int] = None
    economic_value_annual: Optional[float] = None
    lead_score: int = 0
    manual_context_revision: int = 0
    manual_context_updated_at: Optional[datetime] = None
    next_action: Optional[str] = None
    last_interaction: Optional[str] = None
    unread_messages: int
    open_tasks: int
    is_blocked: bool
    needs_approval: bool
    doc_score: int
    production_score: int
    next_deadline: Optional[str] = None
    claim_value: Optional[float] = None
    card_order: int = 0
    created_at: datetime
    updated_at: datetime
    client: ClientRead
    card_score: Optional[CardScoreRead] = None
    conversation_insight: Optional[ConversationInsightRead] = None
