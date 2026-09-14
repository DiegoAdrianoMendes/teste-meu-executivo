"""
Modelo: LegalDemand (Demanda jurídica) — Onda 2 (Fundação jurídica).

Camada canônica que unifica as 7 naturezas de demanda jurídica do
escritório (judicial, administrativo, consultivo, contrato, acordo,
serviço recorrente, massificado). NÃO duplica o que já existe em
`Matter`/`FinanceEntry`/`ContractBillingProposal`: quando a demanda tem um
caso operacional correspondente no Kanban, `matter_id` referencia o
`Matter` existente — os dados operacionais (cliente, timeline, documentos,
lançamentos financeiros) continuam morando lá. `LegalDemand` adiciona só a
camada jurídica que faltava: natureza, área/subárea, partes, unidade/equipe,
responsável, estratégia, risco, valor, fase, SLA, playbook e os documentos
exigidos — com ponteiros opcionais para o contrato (`contract_ref`) e o
lançamento financeiro (`finance_ref`) quando existentes.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.contract_billing import ContractBillingProposal
    from app.models.finance import FinanceEntry
    from app.models.matter import Matter
    from app.models.user import User
    from app.models.workspace import Workspace


class LegalDemandNature(str, enum.Enum):
    """As 7 naturezas de demanda jurídica atendidas pelo escritório."""

    judicial = "judicial"
    administrativo = "administrativo"
    consultivo = "consultivo"
    contrato = "contrato"
    acordo = "acordo"
    servico_recorrente = "servico_recorrente"
    massificado = "massificado"


class LegalDemandRisk(str, enum.Enum):
    baixo = "baixo"
    medio = "medio"
    alto = "alto"
    critico = "critico"


class LegalDemandPhase(str, enum.Enum):
    triagem = "triagem"
    em_andamento = "em_andamento"
    suspenso = "suspenso"
    concluido = "concluido"
    encerrado = "encerrado"


# Fases terminais — uma demanda encerrada/concluída só volta a tramitar via
# reabertura explícita (LegalDemandService trata isso como transição válida,
# não como erro; ver services/legal_demand_service.py).
LEGAL_DEMAND_TERMINAL_PHASES = frozenset({LegalDemandPhase.concluido, LegalDemandPhase.encerrado})

# Campos obrigatórios por natureza — vocabulário de domínio usado pelo
# service para derivar/validar na criação (LegalDemandService).
REQUIRED_FIELDS_BY_NATURE: dict[LegalDemandNature, tuple[str, ...]] = {
    LegalDemandNature.judicial: ("area",),
    LegalDemandNature.administrativo: ("area",),
    LegalDemandNature.consultivo: ("area",),
    LegalDemandNature.contrato: ("contract_ref",),
    LegalDemandNature.acordo: ("value",),
    LegalDemandNature.servico_recorrente: ("sla",),
    LegalDemandNature.massificado: ("documents_required",),
}


class LegalDemand(UUIDMixin, TimestampMixin, Base):
    """Entidade canônica que unifica as 7 naturezas de demanda jurídica."""

    __tablename__ = "legal_demands"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "external_ref",
            name="uq_legal_demands_workspace_external_ref",
        ),
        # Ancora composta para FKs [workspace_id, legal_demand_id] de outras
        # tabelas nativas (ex.: LegalArtifactVersion.legal_demand_id) —
        # mesmo padrão de Matter (ver app/models/matter.py), garante que a
        # integridade tenant seja verificada no schema, não só em runtime.
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_legal_demands_workspace_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Chave de idempotência opcional fornecida pelo chamador — mesmo padrão de
    # FinanceEntry.external_ref. Duas criações com o mesmo external_ref no
    # mesmo workspace devolvem a demanda já existente (ver service).
    external_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)

    nature: Mapped[LegalDemandNature] = mapped_column(
        SAEnum(LegalDemandNature, name="legal_demand_nature_enum"), nullable=False, index=True
    )
    area: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    subarea: Mapped[str | None] = mapped_column(String(100), nullable=True)

    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Partes envolvidas além do cliente titular (contraparte, terceiros,
    # litisconsortes) — [{name, role, docNumber?}], nunca fato financeiro.
    parties: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)

    unit: Mapped[str | None] = mapped_column(String(120), nullable=True)
    team: Mapped[str | None] = mapped_column(String(120), nullable=True)
    responsible_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk: Mapped[LegalDemandRisk] = mapped_column(
        SAEnum(LegalDemandRisk, name="legal_demand_risk_enum"),
        nullable=False,
        default=LegalDemandRisk.medio,
    )
    value: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    phase: Mapped[LegalDemandPhase] = mapped_column(
        SAEnum(LegalDemandPhase, name="legal_demand_phase_enum"),
        nullable=False,
        default=LegalDemandPhase.triagem,
        index=True,
    )
    # {targetDays?, startedAt?, dueAt?, ...} — molde livre por natureza,
    # controladoria (Onda 4) lê daqui em vez de reinventar um campo próprio.
    sla: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    # ── SLA em camadas (Onda 4 — Controladoria, Plano Mestre §8) ──
    # Cada camada é opcional e progressivamente mais restritiva que a
    # anterior (prazo legal < prazo fatal < prazo interno < SLA do cliente
    # < prazo do playbook < margem de segurança). `LegalSlaService`
    # (app/services/legal_sla_service.py) calcula o mais restritivo dentre
    # as datas preenchidas e aplica a margem de segurança por cima —
    # NUNCA infere data que não esteja aqui. `prazo_fatal_at` normalmente
    # espelha `ProcessPublication.data_fatal` já confirmada (módulo
    # processual) quando a demanda tem processo monitorado vinculado.
    prazo_legal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prazo_fatal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prazo_interno_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_cliente_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prazo_playbook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Dias subtraídos da data mais restritiva encontrada acima, para chegar
    # ao prazo efetivo de trabalho (a "margem de segurança" propriamente dita).
    margem_seguranca_dias: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Cache do resultado de LegalSlaService.compute_effective_deadline —
    # recalculado a cada mudança de camada ou na varredura periódica.
    sla_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_effective_layer: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Último nível de escalonamento já disparado (ver SlaEscalationLog) —
    # evita reescalar o mesmo nível a cada varredura.
    sla_escalation_level: Mapped[str | None] = mapped_column(String(20), nullable=True)

    playbook_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Lista de tipos de documento exigidos por esta demanda (ex.: ["procuracao",
    # "contrato_social"]) — checklist declarativo, não um upload.
    documents_required: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)

    # ── Não duplica: referencia o que já existe ──
    matter_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contract_ref: Mapped[UUID | None] = mapped_column(
        ForeignKey("contract_billing_proposals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    finance_ref: Mapped[UUID | None] = mapped_column(
        ForeignKey("finance_entries.id", ondelete="SET NULL"), nullable=True, index=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    client: Mapped[Client] = relationship("Client")
    responsible_user: Mapped[User | None] = relationship("User")
    matter: Mapped[Matter | None] = relationship("Matter")
    contract_proposal: Mapped[ContractBillingProposal | None] = relationship(
        "ContractBillingProposal"
    )
    finance_entry: Mapped[FinanceEntry | None] = relationship("FinanceEntry")

    def __repr__(self) -> str:
        return f"<LegalDemand id={self.id} nature={self.nature.value} phase={self.phase.value}>"
