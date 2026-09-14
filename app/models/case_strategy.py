"""
Modelos: Matriz Estratégia Fato–Prova–Pedido (Onda 5 — Caso 360, Plano
Mestre §3 item 8).

A espinha da estratégia jurídica de uma `LegalDemand`: todo fato alegado
(`CaseFact`) deveria ter prova que o sustente (`CaseProofLink`), e todo
pedido (`CasePleading`) deveria se apoiar em fatos provados. A matriz cruza
fato → prova(s) → pedido(s) e evidencia LACUNAS — fato controvertido sem
prova "tem", pedido sem fato de sustentação — sem nunca fabricar uma prova
que não existe (ver `app/services/case_strategy_service.py`).

NÃO duplica o que já existe:
  * `LegalDemand` já é a demanda canônica — as três tabelas aqui só
    adicionam a camada de estratégia, ligada por `legal_demand_id`;
  * a evidência/documento em si continua morando em `Document`
    (app/models/document.py) — `CaseProofLink.document_id` só referencia,
    nunca copia metadado de arquivo;
  * a pendência gerada por uma lacuna abre item na Central de Exceções já
    existente (`LegalException`, `ExceptionKind.documento_indispensavel_ausente`
    — app/models/legal_exception.py), não nasce um novo tipo de exceção.

RLS direta por `workspace_id`, mesmo padrão de `legal_exceptions`/
`distribution_rules` (ver app/db/rls.py::CASE_STRATEGY_DIRECT_TENANT_TABLES).
`legal_demand_id` é denormalizado nas três tabelas (em vez de só herdar via
`fact_id`) para que RLS e as queries da matriz nunca precisem de um JOIN
extra até `case_facts` — mesmo racional de `DistributionDecision.legal_demand_id`
em app/models/legal_controladoria.py.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.legal_demand import LegalDemand
    from app.models.user import User
    from app.models.workspace import Workspace


class ProofForce(StrEnum):
    """Força probatória do vínculo fato↔prova — nunca inferida, sempre
    informada por quem registra a prova (advogado da matéria)."""

    plena = "plena"
    robusta = "robusta"
    mediana = "mediana"
    indiciaria = "indiciaria"
    fragil = "fragil"


class ProofStatus(StrEnum):
    """Estado do vínculo fato↔prova. `falta`/`solicitada` são as duas faces
    de uma prova ainda ausente — `solicitada` marca que já foi pedida ao
    cliente/caso (ver CaseStrategyService.request_proof)."""

    tem = "tem"
    falta = "falta"
    solicitada = "solicitada"


def _proof_force_enum() -> Enum:
    return Enum(
        ProofForce,
        name="case_proof_force_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _proof_status_enum() -> Enum:
    return Enum(
        ProofStatus,
        name="case_proof_status_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class CaseFact(UUIDMixin, TimestampMixin, Base):
    """Fato alegado no contexto de uma demanda jurídica."""

    __tablename__ = "case_facts"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Cronologia do fato — quando ocorreu (não quando foi registrado; isso é
    # `created_at`, herdado de TimestampMixin).
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Fato controvertido = a parte contrária nega ou impugna. Só fatos
    # controvertidos entram na varredura de lacuna "sem prova" — fato
    # incontroverso (CPC art. 374, III) dispensa prova.
    is_controverted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    # De onde veio o relato do fato — texto livre (ex.: "cliente",
    # "testemunha Fulano", "confissão da parte contrária em audiência").
    origin: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    legal_demand: Mapped[LegalDemand] = relationship("LegalDemand")
    created_by: Mapped[User | None] = relationship("User")
    proof_links: Mapped[list[CaseProofLink]] = relationship(
        "CaseProofLink", back_populates="fact", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<CaseFact id={self.id} controverted={self.is_controverted}>"


class CaseProofLink(UUIDMixin, TimestampMixin, Base):
    """Vínculo fato → prova. `document_id` é opcional de propósito: uma
    prova "solicitada"/"falta" ainda não tem documento anexado — o
    registro existe para tornar a lacuna rastreável antes de resolvida."""

    __tablename__ = "case_proof_links"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_id: Mapped[UUID] = mapped_column(
        ForeignKey("case_facts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A prova em si — referencia o Document já existente (app/models/
    # document.py), nunca duplica metadado de arquivo. NULL enquanto a
    # prova ainda não foi anexada (status='falta'/'solicitada').
    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    force: Mapped[ProofForce] = mapped_column(_proof_force_enum(), nullable=False)
    status: Mapped[ProofStatus] = mapped_column(
        _proof_status_enum(), nullable=False, default=ProofStatus.falta, index=True
    )
    # O que a prova é/seria (ex.: "extrato bancário de jan/2026",
    # "testemunha presencial do acidente") — obrigatório em espírito quando
    # status != 'tem', mas não forçado no schema para não travar migração
    # de dado legado.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    obtained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    legal_demand: Mapped[LegalDemand] = relationship("LegalDemand")
    fact: Mapped[CaseFact] = relationship("CaseFact", back_populates="proof_links")
    document: Mapped[Document | None] = relationship("Document")
    created_by: Mapped[User | None] = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<CaseProofLink id={self.id} fact_id={self.fact_id} status={self.status}>"


class CasePleading(UUIDMixin, TimestampMixin, Base):
    """Pedido feito na demanda, com o fundamento e os fatos que o sustentam."""

    __tablename__ = "case_pleadings"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    legal_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lista de strings UUID de CaseFact — mesmo padrão de
    # DistributionRule.candidate_user_ids (FlexibleJSON não valida FK; o
    # service valida que cada id existe e pertence à mesma demanda antes de
    # persistir). Lista vazia é o próprio sinal de lacuna "pedido sem fato".
    supporting_fact_ids: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    legal_demand: Mapped[LegalDemand] = relationship("LegalDemand")
    created_by: Mapped[User | None] = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<CasePleading id={self.id} demand_id={self.legal_demand_id}>"
