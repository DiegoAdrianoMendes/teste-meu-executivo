"""
Schemas: Controladoria jurídica — distribuição + SLA + painel (Onda 4).
Ver app/models/legal_controladoria.py e
app/services/legal_distribution_service.py / legal_sla_service.py /
legal_controladoria_panel_service.py.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models.legal_controladoria import (
    DistributionStrategy,
    DistributionTrigger,
    SlaEscalationLevel,
)
from app.schemas.base import CamelCaseSchema, WriteSchema

# ── Distribuição ─────────────────────────────────────────────────────────


class DistributionRuleCreate(WriteSchema):
    name: str
    description: str | None = None
    active: bool = True
    priority: int = 100
    match_natures: list[str] = Field(default_factory=list)
    match_areas: list[str] = Field(default_factory=list)
    match_client_ids: list[UUID] = Field(default_factory=list)
    match_risks: list[str] = Field(default_factory=list)
    match_units: list[str] = Field(default_factory=list)
    match_teams: list[str] = Field(default_factory=list)
    strategy: DistributionStrategy = DistributionStrategy.round_robin
    candidate_user_ids: list[UUID] = Field(default_factory=list)
    max_active_per_candidate: int | None = None
    respect_availability: bool = True


class DistributionRuleUpdate(WriteSchema):
    name: str | None = None
    description: str | None = None
    active: bool | None = None
    priority: int | None = None
    match_natures: list[str] | None = None
    match_areas: list[str] | None = None
    match_client_ids: list[UUID] | None = None
    match_risks: list[str] | None = None
    match_units: list[str] | None = None
    match_teams: list[str] | None = None
    strategy: DistributionStrategy | None = None
    candidate_user_ids: list[UUID] | None = None
    max_active_per_candidate: int | None = None
    respect_availability: bool | None = None


class DistributionRuleRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None = None
    active: bool
    priority: int
    match_natures: list[str]
    match_areas: list[str]
    match_client_ids: list[str]
    match_risks: list[str]
    match_units: list[str]
    match_teams: list[str]
    strategy: DistributionStrategy
    candidate_user_ids: list[str]
    max_active_per_candidate: int | None = None
    respect_availability: bool
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class DistributionRuleListRead(CamelCaseSchema):
    items: list[DistributionRuleRead]
    total: int


class DistributionAssignRequest(WriteSchema):
    """Sem `forced_user_id`: escolhe via a primeira regra ativa que casa
    com a demanda. Com `forced_user_id`: redistribuição manual — `reason`
    é obrigatório."""

    forced_user_id: UUID | None = None
    reason: str | None = None


class DistributionDecisionRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    legal_demand_id: UUID
    rule_id: UUID | None = None
    trigger: DistributionTrigger
    previous_user_id: UUID | None = None
    new_user_id: UUID | None = None
    reason: str
    decided_by_user_id: UUID | None = None
    decided_at: datetime


class DistributionDecisionListRead(CamelCaseSchema):
    items: list[DistributionDecisionRead]


# ── SLA ──────────────────────────────────────────────────────────────────


class LegalDemandSlaUpdate(WriteSchema):
    prazo_legal_at: datetime | None = None
    prazo_fatal_at: datetime | None = None
    prazo_interno_at: datetime | None = None
    sla_cliente_at: datetime | None = None
    prazo_playbook_at: datetime | None = None
    margem_seguranca_dias: int | None = None


class LegalDemandSlaRead(CamelCaseSchema):
    legal_demand_id: UUID
    prazo_legal_at: datetime | None = None
    prazo_fatal_at: datetime | None = None
    prazo_interno_at: datetime | None = None
    sla_cliente_at: datetime | None = None
    prazo_playbook_at: datetime | None = None
    margem_seguranca_dias: int | None = None
    sla_effective_at: datetime | None = None
    sla_effective_layer: str | None = None
    sla_escalation_level: str | None = None


class SlaEscalationLogRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    legal_demand_id: UUID
    level: SlaEscalationLevel
    layer: str | None = None
    due_at: datetime
    escalated_to_user_id: UUID | None = None
    channel_results: dict
    acknowledged_by_user_id: UUID | None = None
    acknowledged_at: datetime | None = None
    created_at: datetime


class SlaEscalationLogListRead(CamelCaseSchema):
    items: list[SlaEscalationLogRead]


class SlaAcknowledgeRequest(WriteSchema):
    pass


# ── Painel ───────────────────────────────────────────────────────────────


class DeadlineWindowCountsRead(CamelCaseSchema):
    d7: int
    d3: int
    d1: int
    d0: int
    atrasados: int


class ControladoriaPainelRead(CamelCaseSchema):
    generated_at: datetime
    publicacoes_nao_tratadas: int
    prazos_sugeridos: int
    prazos_fatais: int
    prazos_por_janela: DeadlineWindowCountsRead
    tarefas_sem_responsavel: int
    processos_sem_movimentacao: int
    audiencias_proximas: int
    documentos_pendentes: int
    minutas_bloqueadas: int
    excecoes_de_agentes: int
    clientes_sem_atualizacao: int
