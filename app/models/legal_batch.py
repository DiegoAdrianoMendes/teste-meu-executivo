"""
Modelo: LegalBatch / LegalBatchItem — Operação Massificada / Lotes
(Onda 8 — Escala e gestão, Plano Mestre §7).

Um `LegalBatch` agrupa N `LegalBatchItem` por um CRITÉRIO declarado
(cliente/carteira/tribunal/matéria/tipo de publicação/fase/tese/
responsável/prazo/playbook) — nunca é um "job" opaco: o lote em si só
guarda o critério e metadados; cada item mantém ESTADO INDIVIDUAL, nunca
mascarado pelo status agregado do lote (ver LegalBatchService.get_panel,
que sempre conta itens reais, nunca um contador só incrementado).

Vocabulário de estado do item (fail-closed, cada transição é uma ação
serviço, nunca um UPDATE solto):

    pendente -> classificado -> vinculado -> rascunho -> revisado -> concluido
                     -> bloqueado / baixa_confianca / divergente / aguardando_humano

`bloqueado`/`baixa_confianca`/`divergente`/`aguardando_humano` são estados
de PARADA — uma falha ou incerteza em UM item nunca é escondida atrás do
sucesso dos demais (processamento é item a item, com captura de exceção
por item; ver LegalBatchService.process_all).

`decision_kind` classifica o tipo de decisão que o item representa. Os
quatro valores em `PROTECTED_DECISION_KINDS` (tese, prazo_fatal,
texto_final, protocolo) NUNCA podem ser aprovados em lote — mesmo com o
lote inteiro em `aguardando_humano`, `LegalBatchService.approve_all` pula
esses itens e devolve a lista de pulados; só `approve_item` (item a item,
sempre humano) os move para `concluido`.

`legal_demand_id` é o ponteiro opcional para a `LegalDemand` (Onda 2) que
o item classifica/enriquece — reusa o Caso 360 e a jornada (Onda 4/5) em
vez de duplicar dado jurídico aqui. O item também pode nascer sem
`legal_demand_id` (ex.: publicação ainda não vinculada a nenhuma demanda)
e ganhar o vínculo como uma transição própria (`vincular`).
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.legal_demand import LegalDemand
    from app.models.user import User
    from app.models.workspace import Workspace


class LegalBatchCriterion(str, enum.Enum):
    """Critério de agrupamento do lote (Plano Mestre §7)."""

    cliente = "cliente"
    carteira = "carteira"
    tribunal = "tribunal"
    materia = "materia"
    tipo_publicacao = "tipo_publicacao"
    fase = "fase"
    tese = "tese"
    responsavel = "responsavel"
    prazo = "prazo"
    playbook = "playbook"


class LegalBatchStatus(str, enum.Enum):
    """Status agregado do lote — SEMPRE derivado dos itens (nunca a fonte
    de verdade em si; ver LegalBatchService.get_panel/_recompute_status).
    """

    aberto = "aberto"
    processando = "processando"
    com_pendencias = "com_pendencias"
    concluido = "concluido"


class LegalBatchItemState(str, enum.Enum):
    """Vocabulário fail-closed de estado individual do item."""

    pendente = "pendente"
    classificado = "classificado"
    vinculado = "vinculado"
    rascunho = "rascunho"
    revisado = "revisado"
    bloqueado = "bloqueado"
    baixa_confianca = "baixa_confianca"
    divergente = "divergente"
    aguardando_humano = "aguardando_humano"
    concluido = "concluido"


# Estados de PARADA — sinalizam falha/incerteza/necessidade humana, nunca
# são silenciosamente promovidos a `concluido` por um processamento em lote.
LEGAL_BATCH_ITEM_BLOCKING_STATES = frozenset(
    {
        LegalBatchItemState.bloqueado,
        LegalBatchItemState.baixa_confianca,
        LegalBatchItemState.divergente,
        LegalBatchItemState.aguardando_humano,
    }
)

LEGAL_BATCH_ITEM_TERMINAL_STATES = frozenset({LegalBatchItemState.concluido})


class LegalBatchAction(str, enum.Enum):
    """Ação autônoma que a IA pode executar sobre um item — sempre
    respeitando a autonomia A0-A4 (Onda 2, `AutonomyPolicyService`) e os
    gates de `agent_governance_service`."""

    classificar = "classificar"
    agrupar = "agrupar"
    vincular = "vincular"
    sugerir_tarefa = "sugerir_tarefa"
    preencher_variavel = "preencher_variavel"
    gerar_rascunho = "gerar_rascunho"
    revisar = "revisar"
    pedir_documento = "pedir_documento"
    organizar = "organizar"


class LegalBatchDecisionKind(str, enum.Enum):
    """Natureza da decisão representada pelo item — usada só para aplicar
    a REGRA DURA de `PROTECTED_DECISION_KINDS` abaixo; não substitui
    `action` (uma mesma ação pode produzir decisões de natureza distinta,
    ex.: `gerar_rascunho` produzindo um `texto_final`)."""

    classificacao = "classificacao"
    agrupamento = "agrupamento"
    vinculo = "vinculo"
    tarefa_sugerida = "tarefa_sugerida"
    variavel_preenchida = "variavel_preenchida"
    rascunho = "rascunho"
    revisao = "revisao"
    documento_solicitado = "documento_solicitado"
    organizacao = "organizacao"
    # ── decisões críticas — NUNCA aprováveis em lote ──
    tese = "tese"
    prazo_fatal = "prazo_fatal"
    texto_final = "texto_final"
    protocolo = "protocolo"


# REGRA DURA (Plano Mestre §7): nenhum 'aprovar tudo' cobre estes quatro —
# exigem aprovação item a item, sempre humana
# (ver LegalBatchService.approve_all / approve_item).
PROTECTED_DECISION_KINDS = frozenset(
    {
        LegalBatchDecisionKind.tese,
        LegalBatchDecisionKind.prazo_fatal,
        LegalBatchDecisionKind.texto_final,
        LegalBatchDecisionKind.protocolo,
    }
)


class LegalBatch(UUIDMixin, TimestampMixin, Base):
    """Lote de operação massificada — agrupa itens por um critério
    declarado; RLS direta por workspace_id (ver
    app/db/rls.py::LEGAL_BATCH_DIRECT_TENANT_TABLES)."""

    __tablename__ = "legal_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('aberto','processando','com_pendencias','concluido')",
            name="ck_legal_batch_status_values",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Chave de idempotência opcional fornecida pelo chamador — mesmo padrão
    # de LegalDemand.external_ref: duas criações com o mesmo external_ref
    # no mesmo workspace devolvem o lote já existente (ver service).
    external_ref: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)

    criterion_type: Mapped[LegalBatchCriterion] = mapped_column(
        SAEnum(LegalBatchCriterion, name="legal_batch_criterion_enum"),
        nullable=False,
        index=True,
    )
    # Valor concreto do critério (ex.: {"clientId": "..."} ou
    # {"tribunal": "TJDFT"}) — molde livre por tipo de critério.
    criterion_value: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    playbook_ref: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    default_action: Mapped[Optional[LegalBatchAction]] = mapped_column(
        SAEnum(LegalBatchAction, name="legal_batch_action_enum"), nullable=True
    )

    # Status agregado — cache recomputado por `LegalBatchService`
    # (nunca a fonte de verdade; a fonte é sempre a contagem dos itens).
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LegalBatchStatus.aberto.value, index=True
    )

    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    created_by: Mapped[Optional["User"]] = relationship("User")
    items: Mapped[list["LegalBatchItem"]] = relationship(
        "LegalBatchItem", back_populates="batch", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<LegalBatch id={self.id} criterion={self.criterion_type.value} "
            f"status={self.status}>"
        )


class LegalBatchItem(UUIDMixin, TimestampMixin, Base):
    """Item individual do lote — ESTADO PRÓPRIO, nunca escondido pelo
    agregado do lote. RLS direta por workspace_id."""

    __tablename__ = "legal_batch_items"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pendente','classificado','vinculado','rascunho','revisado',"
            "'bloqueado','baixa_confianca','divergente','aguardando_humano','concluido')",
            name="ck_legal_batch_item_state_values",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_legal_batch_item_confidence_range",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Referência de idempotência do item DENTRO do lote (ex.: id externo da
    # publicação/registro de origem) — reprocessar o mesmo item_ref no
    # mesmo lote nunca cria uma segunda linha (ver service).
    item_ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)

    legal_demand_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="SET NULL"), nullable=True, index=True
    )

    action: Mapped[LegalBatchAction] = mapped_column(
        SAEnum(LegalBatchAction, name="legal_batch_item_action_enum"), nullable=False
    )
    decision_kind: Mapped[LegalBatchDecisionKind] = mapped_column(
        SAEnum(LegalBatchDecisionKind, name="legal_batch_item_decision_kind_enum"),
        nullable=False,
    )

    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LegalBatchItemState.pendente.value, index=True
    )
    # Pilha de estados anteriores — [{"state": "...", "at": "...", "byUserId": "..."}] —
    # usada por `LegalBatchService.rollback_item` para voltar exatamente ao
    # estado anterior, nunca "adivinhado".
    state_history: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)

    # Dado de entrada do item (o que a IA recebeu para processar) e
    # resultado produzido (o que a IA propôs) — nunca sobrescreve o
    # original: cada chamada de processamento anexa a `state_history`.
    input_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    result_payload: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    # Preenchido SEMPRE que o item para num estado de bloqueio — nunca um
    # estado de parada sem motivo explícito (fail-closed em auditabilidade).
    error_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── autonomia aplicada (Plano Mestre §14) ──
    autonomy_level_applied: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    autonomy_policy_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_autonomy_policies.id", ondelete="SET NULL"), nullable=True
    )
    requires_human_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    approved_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    estimated_cost: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    estimated_time_saved_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    responsible_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    batch: Mapped["LegalBatch"] = relationship("LegalBatch", back_populates="items")
    legal_demand: Mapped[Optional["LegalDemand"]] = relationship("LegalDemand")
    approved_by: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys="LegalBatchItem.approved_by_user_id"
    )
    responsible_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys="LegalBatchItem.responsible_user_id"
    )

    def __repr__(self) -> str:
        return (
            f"<LegalBatchItem id={self.id} state={self.state} "
            f"decision={self.decision_kind.value}>"
        )
