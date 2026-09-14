"""Modelos: Controladoria jurídica — distribuição + SLA (Onda 4, Plano Mestre §8).

Duas responsabilidades novas, sobre a fundação já existente (Onda 2/3):

1. `DistributionRule` + `DistributionDecision` — distribuição configurável
   de trabalho jurídico (aqui, `LegalDemand`) para um responsável, seguindo
   critérios do workspace (área/classe/cliente/complexidade/carteira/
   capacidade/disponibilidade/senioridade/unidade). NÃO recria campos que
   já existem em `LegalDemand` (area, nature, client_id, risk, unit, team) —
   a regra referencia esses campos por valor. "Capacidade" é contada ao
   vivo (quantas demandas ativas o candidato já tem); "disponibilidade" e
   "substituição" (férias) são resolvidas via
   `StaffAvailabilityService.resolve_responsibility` (app/services/
   staff_availability_service.py), NUNCA reimplementadas aqui.
   `DistributionDecision` é o histórico: toda (re)distribuição — automática
   ou manual — grava uma linha com o motivo, nunca silenciosa.

2. `SlaEscalationLog` — histórico de escalonamento de SLA em camadas
   (prazo legal < prazo fatal < prazo interno < SLA do cliente < prazo do
   playbook < margem de segurança — cada camada mais restritiva que a
   anterior; ver `LegalDemand.sla_effective_at` e
   `app/services/legal_sla_service.py`). Cada nível (D-7/D-3/D-1/D-0/
   atrasado) só escala uma vez por demanda — `acknowledged_at` é a
   confirmação de leitura humana.

RLS direta por `workspace_id`, mesmo padrão de `legal_demands`/
`legal_exceptions` (ver app/db/rls.py::LEGAL_CONTROLADORIA_DIRECT_TENANT_TABLES).
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.legal_demand import LegalDemand
    from app.models.user import User
    from app.models.workspace import Workspace


class DistributionStrategy(StrEnum):
    """Como escolher UM responsável dentre os candidatos elegíveis de uma regra."""

    round_robin = "round_robin"
    least_load = "least_load"
    seniority = "seniority"
    fixed = "fixed"


class DistributionTrigger(StrEnum):
    """Origem de uma (re)distribuição — sempre registrada, nunca silenciosa."""

    regra_automatica = "regra_automatica"
    redistribuicao_manual = "redistribuicao_manual"
    ausencia_substituicao = "ausencia_substituicao"
    rebalanceamento_capacidade = "rebalanceamento_capacidade"


class SlaEscalationLevel(StrEnum):
    d7 = "d7"
    d3 = "d3"
    d1 = "d1"
    d0 = "d0"
    atrasado = "atrasado"


def _distribution_strategy_enum() -> Enum:
    return Enum(
        DistributionStrategy,
        name="distribution_strategy_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _distribution_trigger_enum() -> Enum:
    return Enum(
        DistributionTrigger,
        name="distribution_trigger_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _sla_escalation_level_enum() -> Enum:
    return Enum(
        SlaEscalationLevel,
        name="sla_escalation_level_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class DistributionRule(UUIDMixin, TimestampMixin, Base):
    """Regra configurável de distribuição de trabalho jurídico do workspace."""

    __tablename__ = "distribution_rules"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_distribution_rule_workspace_name"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    # Menor valor = avaliada primeiro; empate resolvido por created_at.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    # ── Critérios de correspondência — listas vazias/None casam com tudo ──
    # "classe" (natureza da demanda).
    match_natures: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    match_areas: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    match_client_ids: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    # "complexidade" — usa LegalDemand.risk como proxy de complexidade.
    match_risks: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    match_units: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    # "carteira" — usa LegalDemand.team como proxy de carteira/equipe.
    match_teams: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)

    strategy: Mapped[DistributionStrategy] = mapped_column(
        _distribution_strategy_enum(), nullable=False, default=DistributionStrategy.round_robin
    )
    # Candidatos elegíveis, em ordem — a ordem É a senioridade para
    # strategy=seniority e a ordem de rodízio para strategy=round_robin.
    # Lista de strings UUID (FlexibleJSON não valida FK; o service valida
    # membership ativa no workspace antes de atribuir).
    candidate_user_ids: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    # "capacidade" — máximo de demandas ativas simultâneas por candidato;
    # None = sem limite (o service ainda assim prefere o menos carregado
    # em least_load).
    max_active_per_candidate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "disponibilidade"/"substituição" — quando True, o service resolve o
    # responsável efetivo via StaffAvailabilityService.resolve_responsibility
    # antes de fechar a atribuição (férias/redirect).
    respect_availability: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    created_by: Mapped[User | None] = relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<DistributionRule id={self.id} name={self.name!r} active={self.active}>"


class DistributionDecision(UUIDMixin, TimestampMixin, Base):
    """Histórico de (re)distribuição — toda linha carrega motivo, nunca silenciosa."""

    __tablename__ = "distribution_decisions"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("distribution_rules.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[DistributionTrigger] = mapped_column(
        _distribution_trigger_enum(), nullable=False, index=True
    )
    previous_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    new_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Sempre obrigatório — "nunca redistribui sem registrar" (Plano Mestre §8).
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # None = decisão do sistema (regra automática); caso contrário, o
    # humano autenticado que forçou a redistribuição manual.
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    legal_demand: Mapped[LegalDemand] = relationship("LegalDemand")
    rule: Mapped[DistributionRule | None] = relationship("DistributionRule")
    previous_user: Mapped[User | None] = relationship("User", foreign_keys=[previous_user_id])
    new_user: Mapped[User | None] = relationship("User", foreign_keys=[new_user_id])
    decided_by: Mapped[User | None] = relationship("User", foreign_keys=[decided_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return (
            f"<DistributionDecision id={self.id} demand_id={self.legal_demand_id} "
            f"trigger={self.trigger}>"
        )


class SlaEscalationLog(UUIDMixin, TimestampMixin, Base):
    """Histórico de escalonamento de SLA — um registro por (demanda, nível)."""

    __tablename__ = "sla_escalation_logs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "legal_demand_id",
            "level",
            name="uq_sla_escalation_workspace_demand_level",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: Mapped[SlaEscalationLevel] = mapped_column(
        _sla_escalation_level_enum(), nullable=False, index=True
    )
    # Qual camada (prazo_legal/prazo_fatal/prazo_interno/sla_cliente/
    # prazo_playbook) produziu a data mais restritiva no momento do escalonamento.
    layer: Mapped[str | None] = mapped_column(String(40), nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    escalated_to_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # {"email": bool, "whatsapp": bool} — resultado real do disparo
    # (OperationalAlertService), fail-closed (ver app/core/klm_runtime.py).
    channel_results: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # Confirmação de leitura humana — None enquanto ninguém confirmou.
    acknowledged_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    legal_demand: Mapped[LegalDemand] = relationship("LegalDemand")
    escalated_to: Mapped[User | None] = relationship(
        "User", foreign_keys=[escalated_to_user_id]
    )
    acknowledged_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[acknowledged_by_user_id]
    )

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return (
            f"<SlaEscalationLog id={self.id} demand_id={self.legal_demand_id} "
            f"level={self.level} acknowledged={self.acknowledged_at is not None}>"
        )
