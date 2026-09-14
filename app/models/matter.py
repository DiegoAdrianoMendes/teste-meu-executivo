"""
Modelo: Matter (Caso/Demanda) — O Aggregate Root central do sistema.
Representa um caso jurídico com todo o seu contexto operacional.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    FlexibleJSON,
    FlexibleUUID,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
)

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.approval import ApprovalQueueItem
    from app.models.campaign_attribution import CampaignAttribution
    from app.models.card_field_change import CardFieldChange
    from app.models.card_score import CardScore
    from app.models.client import Client
    from app.models.conversation import Conversation
    from app.models.conversation_insight import ConversationInsight
    from app.models.document import Document
    from app.models.finance import FinanceEntry
    from app.models.honorarium_estimate import HonorariumEstimate
    from app.models.human_override import HumanOverride
    from app.models.knowledge_chunk import KnowledgeChunk
    from app.models.medication import MatterTreatmentEstimate
    from app.models.meeting import Meeting
    from app.models.revenue_projection import RevenueProjection
    from app.models.task import Task
    from app.models.timeline_event import TimelineEvent
    from app.models.user import User
    from app.models.workspace import Workspace


# ── Enums de Domínio ──


class PracticeArea(str, enum.Enum):
    saude_suplementar = "saude_suplementar"
    erro_medico = "erro_medico"
    bancario = "bancario"
    trabalhista = "trabalhista"
    previdenciario = "previdenciario"
    civel = "civel"
    consumidor = "consumidor"
    digital = "digital"
    propriedade_intelectual = "propriedade_intelectual"
    seguros = "seguros"
    tributario = "tributario"
    empresarial = "empresarial"
    familia = "familia"
    imobiliario = "imobiliario"


class HealthSubtheme(str, enum.Enum):
    medicamento_alto_custo = "medicamento_alto_custo"
    tratamento = "tratamento"
    home_care = "home_care"
    cirurgia = "cirurgia"
    exame = "exame"
    opme = "opme"
    terapia = "terapia"
    outro = "outro"


class Priority(str, enum.Enum):
    alta = "alta"
    media = "media"
    baixa = "baixa"


class Urgency(str, enum.Enum):
    urgente = "urgente"
    normal = "normal"
    baixa = "baixa"


class ClientClassification(str, enum.Enum):
    Premium = "Premium"
    Forte = "Forte"
    Bom = "Bom"
    Frio = "Frio"


class KanbanColumnId(str, enum.Enum):
    novo_lead = "novo_lead"
    triagem_sdr = "triagem_sdr"
    documentos = "documentos"
    reuniao = "reuniao"
    proposta = "proposta"
    contrato = "contrato"
    pagamento = "pagamento"
    ganho = "ganho"
    perdido = "perdido"
    entrada = "entrada"
    qualificacao = "qualificacao"
    proposta_fechamento = "proposta_fechamento"
    coleta_dados = "coleta_dados"
    triagem_juridica = "triagem_juridica"
    pesquisa_validacao = "pesquisa_validacao"
    redacao_peca = "redacao_peca"
    aprovacao_pendente = "aprovacao_pendente"
    pronto_protocolo = "pronto_protocolo"
    migrar_sistema = "migrar_sistema"
    assinado = "assinado"


class MicroStatus(str, enum.Enum):
    lead_novo = "lead_novo"
    sem_resposta = "sem_resposta"
    triagem_incompleta = "triagem_incompleta"
    qualificado = "qualificado"
    proposta_enviada = "proposta_enviada"
    assinatura_pendente = "assinatura_pendente"
    pagamento_pendente = "pagamento_pendente"
    aguardando_docs = "aguardando_docs"
    docs_parciais = "docs_parciais"
    docs_invalidos = "docs_invalidos"
    docs_completos = "docs_completos"
    tese_preliminar = "tese_preliminar"
    risco_probatorio = "risco_probatorio"
    pesquisa_em_andamento = "pesquisa_em_andamento"
    validacao_aprovada = "validacao_aprovada"
    validacao_reprovada = "validacao_reprovada"
    minuta_em_elaboracao = "minuta_em_elaboracao"
    revisao_interna = "revisao_interna"
    ajustes_solicitados = "ajustes_solicitados"
    pdf_gerado = "pdf_gerado"
    checklist_final = "checklist_final"
    migracao_pendente = "migracao_pendente"
    contrato_assinado = "contrato_assinado"
    reuniao_agendada = "reuniao_agendada"
    reuniao_confirmada = "reuniao_confirmada"
    reuniao_realizada = "reuniao_realizada"
    no_show = "no_show"
    proposta_em_revisao = "proposta_em_revisao"
    contrato_enviado = "contrato_enviado"
    pagamento_confirmado = "pagamento_confirmado"
    perdido_com_motivo = "perdido_com_motivo"


class Matter(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    Entidade Matter — O card central do Kanban.
    Contém o estado operacional completo de um caso jurídico.
    """

    __tablename__ = "matters"
    __table_args__ = (
        # Mantém no metadata a constraint histórica usada por FKs que referenciam
        # (matter_id, workspace_id). A ordem inversa abaixo atende os vínculos
        # tenant-first mais novos; ambas existem no schema PostgreSQL atual.
        UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_matters_id_workspace",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_matters_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_matters_workspace_client",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_matters_workspace_case_number",
            "workspace_id",
            "case_number",
            unique=True,
            postgresql_where=text("case_number IS NOT NULL"),
        ),
    )

    # ── Relacionamentos de Identidade ──
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        nullable=False,
        index=True,
    )
    responsible_human_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    current_agent_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # ── Dados do Caso ──
    practice_area: Mapped[PracticeArea] = mapped_column(
        Enum(PracticeArea, name="practice_area_enum"),
        nullable=False,
        default=PracticeArea.trabalhista,
    )
    case_number: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    demand_type: Mapped[str] = mapped_column(String(300), nullable=False)
    lead_source: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    tags: Mapped[list[str]] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    claim_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    executive_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Taxonomia jurídica extensível (área padrão ou criada pelo escritório) ──
    legal_area_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    legal_product_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    legal_product_custom: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    legal_object_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    counterparty_name: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    legal_issue_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    legal_context: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    # ── Saúde suplementar: taxonomia operacional de primeira classe ──
    health_subtheme: Mapped[Optional[HealthSubtheme]] = mapped_column(
        Enum(HealthSubtheme, name="health_subtheme_enum"), nullable=True, index=True
    )
    health_subtheme_custom: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    health_object_name: Mapped[Optional[str]] = mapped_column(
        String(300), nullable=True, index=True
    )
    health_insurer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    health_denial_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    medical_urgency_hours: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    economic_value_annual: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    lead_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    manual_context_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual_context_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Estado do Kanban ──
    column_id: Mapped[KanbanColumnId] = mapped_column(
        Enum(KanbanColumnId, name="kanban_column_id_enum"),
        nullable=False,
        default=KanbanColumnId.entrada,
        index=True,
    )
    micro_status: Mapped[MicroStatus] = mapped_column(
        Enum(MicroStatus, name="micro_status_enum"),
        nullable=False,
        default=MicroStatus.lead_novo,
        index=True,
    )

    # ── Classificação e Prioridade ──
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, name="priority_enum"), nullable=False, default=Priority.media, index=True
    )
    urgency: Mapped[Urgency] = mapped_column(
        Enum(Urgency, name="urgency_enum"), nullable=False, default=Urgency.normal
    )
    classification: Mapped[ClientClassification] = mapped_column(
        Enum(ClientClassification, name="client_classification_enum"),
        nullable=False,
        default=ClientClassification.Bom,
        index=True,
    )
    stars_rating: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Controle Operacional ──
    next_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_interaction: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    next_deadline: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    next_hearing: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    unread_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Ordenação no Kanban (drag-and-drop vertical) ──
    card_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)

    # ── Scores ──
    doc_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    production_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Estratégia (JSONB para flexibilidade) ──
    strategy_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risks: Mapped[Optional[list]] = mapped_column(FlexibleJSON(), nullable=True, default=list)
    blocks: Mapped[Optional[list]] = mapped_column(FlexibleJSON(), nullable=True, default=list)

    # ── Agentes Atribuídos (JSONB: lista de UUIDs) ──
    assigned_agent_ids: Mapped[Optional[list]] = mapped_column(
        FlexibleJSON(), nullable=True, default=list
    )

    # ── SDR — Qualificação estruturada (JSONB) ──
    # Preenchido pelo KLM SDR Agent após qualificação do lead via WhatsApp.
    sdr_qualification: Mapped[Optional[dict]] = mapped_column(
        FlexibleJSON(), nullable=True, default=None
    )

    # ── ZapSign ──
    zapsign_doc_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zapsign_sign_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    zapsign_status: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # pending | signed | refused | expired

    # ── Relacionamentos ORM ──
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="matters")
    client: Mapped["Client"] = relationship(
        "Client",
        back_populates="matters",
        foreign_keys=[client_id],
    )
    responsible_human: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[responsible_human_id], back_populates="responsible_matters"
    )
    current_agent: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[current_agent_id], back_populates="current_agent_matters"
    )
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="matter", cascade="all, delete-orphan"
    )
    documents: Mapped[List["Document"]] = relationship(
        "Document", back_populates="matter", cascade="all, delete-orphan"
    )
    timeline_events: Mapped[List["TimelineEvent"]] = relationship(
        "TimelineEvent",
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="TimelineEvent.timestamp.desc()",
    )
    conversations: Mapped[List["Conversation"]] = relationship(
        "Conversation", back_populates="matter", cascade="all, delete-orphan"
    )
    finance_entries: Mapped[List["FinanceEntry"]] = relationship(
        "FinanceEntry",
        back_populates="matter",
    )
    approval_items: Mapped[List["ApprovalQueueItem"]] = relationship(
        "ApprovalQueueItem", back_populates="matter"
    )
    campaign_attribution: Mapped[Optional["CampaignAttribution"]] = relationship(
        "CampaignAttribution",
        back_populates="matter",
        cascade="all, delete-orphan",
        uselist=False,
    )
    revenue_projection: Mapped[Optional["RevenueProjection"]] = relationship(
        "RevenueProjection",
        back_populates="matter",
        cascade="all, delete-orphan",
        uselist=False,
    )
    honorarium_estimate: Mapped[Optional["HonorariumEstimate"]] = relationship(
        "HonorariumEstimate",
        back_populates="matter",
        cascade="all, delete-orphan",
        uselist=False,
    )
    treatment_estimates: Mapped[List["MatterTreatmentEstimate"]] = relationship(
        "MatterTreatmentEstimate",
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="MatterTreatmentEstimate.updated_at.desc()",
    )
    card_score: Mapped[Optional["CardScore"]] = relationship(
        "CardScore",
        back_populates="matter",
        cascade="all, delete-orphan",
        uselist=False,
    )
    field_changes: Mapped[List["CardFieldChange"]] = relationship(
        "CardFieldChange",
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="CardFieldChange.created_at.desc()",
    )
    agent_runs: Mapped[List["AgentRun"]] = relationship(
        "AgentRun",
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="AgentRun.created_at.desc()",
    )
    human_overrides: Mapped[List["HumanOverride"]] = relationship(
        "HumanOverride",
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="HumanOverride.created_at.desc()",
    )
    knowledge_chunks: Mapped[List["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.updated_at.desc()",
    )
    conversation_insight: Mapped[Optional["ConversationInsight"]] = relationship(
        "ConversationInsight",
        back_populates="matter",
        cascade="all, delete-orphan",
        uselist=False,
    )
    meetings: Mapped[List["Meeting"]] = relationship(
        "Meeting",
        back_populates="matter",
        primaryjoin=(
            "and_(Matter.id == foreign(Meeting.matter_id), "
            "Matter.workspace_id == Meeting.workspace_id)"
        ),
        foreign_keys="Meeting.matter_id",
        order_by="Meeting.starts_at.asc()",
    )

    def __repr__(self) -> str:
        return f"<Matter id={self.id} column={self.column_id} status={self.micro_status}>"

    @property
    def timeline(self):
        return self.timeline_events

    @property
    def audit_logs(self):
        logs = []
        for change in self.field_changes:
            logs.append(
                {
                    "id": change.id,
                    "action": f"{change.change_source or 'field_change'}:{change.field_name}",
                    "entity_type": "matter",
                    "entity_id": self.id,
                    "agent_id": change.changed_by_user_id,
                    "timestamp": change.created_at,
                    "description": change.summary or f"Campo '{change.field_name}' atualizado.",
                    "before": change.old_value,
                    "after": change.new_value,
                }
            )
        return logs
