"""
Schemas: Caso 360 canônico — Onda 5 (Plano Mestre §5).
Ver app/services/case_360_service.py e
app/api/v1/endpoints/legal_demands.py::get_case_360.

Reusa integralmente os schemas de leitura já existentes por domínio
(LegalDemandRead, ClientRead, DocumentRead, FinanceEntryRead,
FeeArrangementRead, ConversationRead, ExceptionCenterRead,
TimelineEventRead, DistributionDecisionRead, SlaEscalationLogRead,
CaseStrategyMatrixRead) — só os blocos novos do agregado (oportunidade,
processo+jornada, financeiro-subledger, comunicações, responsável) ganham
schema próprio aqui.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.matter import ClientClassification, KanbanColumnId, MicroStatus, Priority, Urgency
from app.models.process_monitoring import (
    DeadlineStatus,
    MonitoredProcessStatus,
    PublicationJourneyState,
)
from app.schemas.base import CamelCaseSchema
from app.schemas.case_party import CasePartyRead
from app.schemas.case_strategy import CaseStrategyMatrixRead
from app.schemas.client import ClientRead
from app.schemas.conversation import ConversationRead
from app.schemas.document import DocumentRead
from app.schemas.exception_center import ExceptionCenterRead
from app.schemas.fee_arrangement import FeeArrangementRead
from app.schemas.finance import FinanceEntryRead
from app.schemas.legal_controladoria import (
    DistributionDecisionRead,
    LegalDemandSlaRead,
    SlaEscalationLogRead,
)
from app.schemas.legal_demand import LegalDemandRead
from app.schemas.timeline_event import TimelineEventRead


class ResponsavelRead(CamelCaseSchema):
    id: UUID
    name: str
    email: Optional[str] = None


class OportunidadeRead(CamelCaseSchema):
    """Bloco CRM/funil — SEPARADO da demanda contratada. Só existe quando a
    demanda tem um `Matter` (operação Kanban) vinculado."""

    matter_id: UUID
    case_number: Optional[str] = None
    column_id: KanbanColumnId
    micro_status: MicroStatus
    priority: Priority
    urgency: Urgency
    classification: ClientClassification
    lead_source: Optional[str] = None
    claim_value: Optional[Decimal] = None
    stars_rating: int
    card_order: int


class PublicacaoResumoRead(CamelCaseSchema):
    id: UUID
    published_at: datetime
    tipo: Optional[str] = None
    urgencia: Optional[str] = None
    prazo_status: Optional[DeadlineStatus] = None
    data_fatal: Optional[date] = None
    journey_state: PublicationJourneyState


class ProcessoRead(CamelCaseSchema):
    id: UUID
    numero_cnj: str
    tribunal: Optional[str] = None
    status: MonitoredProcessStatus
    last_movement_at: Optional[datetime] = None
    publicacoes: list[PublicacaoResumoRead] = Field(default_factory=list)


class TrabalhoDistribuidoRead(CamelCaseSchema):
    responsible_user: Optional[ResponsavelRead] = None
    last_decision: Optional[DistributionDecisionRead] = None
    open_escalations: list[SlaEscalationLogRead] = Field(default_factory=list)


class FinanceTotalsRead(CamelCaseSchema):
    total_receivable: Decimal
    total_paid: Decimal
    total_pending: Decimal
    total_overdue: Decimal


class FinanceiroSubledgerRead(CamelCaseSchema):
    """Subledger real do caso — SEMPRE filtrado por matter_id, nunca o
    financeiro do workspace inteiro. Vazio e honesto quando não há Matter."""

    entries: list[FinanceEntryRead] = Field(default_factory=list)
    totals: FinanceTotalsRead
    fee_arrangements: list[FeeArrangementRead] = Field(default_factory=list)


class ComunicacoesRead(CamelCaseSchema):
    total_count: int
    recent: list[ConversationRead] = Field(default_factory=list)


class Case360Read(CamelCaseSchema):
    generated_at: datetime
    demand_id: UUID
    workspace_id: UUID

    # ── Demanda contratada × oportunidade (nunca confundidos) ──
    demanda_contratada: LegalDemandRead
    oportunidade: Optional[OportunidadeRead] = None

    cliente: ClientRead
    responsavel: Optional[ResponsavelRead] = None
    partes: list[CasePartyRead] = Field(default_factory=list)

    processos: list[ProcessoRead] = Field(default_factory=list)
    prazos: LegalDemandSlaRead
    trabalho_distribuido: TrabalhoDistribuidoRead

    documentos: list[DocumentRead] = Field(default_factory=list)
    financeiro: FinanceiroSubledgerRead
    comunicacoes: ComunicacoesRead

    estrategia: Optional[str] = None
    provas: CaseStrategyMatrixRead

    excecoes_abertas: list[ExceptionCenterRead] = Field(default_factory=list)
    timeline: list[TimelineEventRead] = Field(default_factory=list)
