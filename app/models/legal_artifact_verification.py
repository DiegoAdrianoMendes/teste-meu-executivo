"""
Modelos: Verificação de citações + Revisão adversarial de artefato jurídico
(Onda 6 — Produção jurídica, §9/§3 item 20).

Regra da casa (CLAUDE.md do usuário): nenhum precedente citado numa peça
pode ir a protocolo sem conferência na fonte. As duas tabelas aqui dão
persistência a essa regra:

  * `LegalCitationVerification` — uma linha por citação (súmula/tema/
    REsp/lei) extraída do conteúdo de uma `LegalArtifactVersion`
    (app/models/klm_operations.py, Onda 2/5), com o veredito da
    conferência (`verificada`/`nao_verificada`/`nao_localizada`) e a
    trilha de auditoria de COMO se chegou a esse veredito — nunca um
    booleano solto. Ver app/services/citation_verification_service.py
    para o porquê de `nao_verificada` ser o default honesto (gate
    fechado/sem provedor de fonte real integrado NUNCA vira "verificada"
    por omissão).
  * `LegalReviewFinding` — um achado por execução da revisão adversarial
    (`app/services/legal_review_service.py`), com severidade
    (`critica`/`alta`/`media`/`baixa`). Um achado `critica` marca
    `is_protocolable=False`; `ApprovalService.resolve` (Onda 2) consulta
    essa mesma tabela para rejeitar a aprovação de um `legal_artifact:*`
    com achado crítico aberto — o veredito nunca é copiado para uma coluna
    em `LegalArtifactVersion` (a tabela é `register_append_only_model`,
    UPDATE via ORM é rejeitado por `app/db/append_only.py`; ver
    docstring de `app/services/legal_review_service.py`).

NÃO duplica o que já existe:
  * a versão do artefato em si continua em `LegalArtifactVersion`
    (imutável, evidence_sha256 já travado) — as duas tabelas aqui só
    ANOTAM um veredito sobre um `artifact_version_id` existente, nunca
    copiam `content`;
  * a lacuna "pedido sem fato de sustentação" já é detectada por
    `CaseStrategyService.get_matrix` (Onda 5) — `LegalReviewFinding`
    reusa esse mesmo cálculo (cruza com `CasePleading.supporting_fact_ids`)
    em vez de reimplementar a regra.

RLS direta por workspace_id nas duas tabelas, mesmo padrão de
`case_facts`/`case_proof_links`/`case_pleadings` (ver app/db/rls.py::
LEGAL_PRODUCTION_DIRECT_TENANT_TABLES). `legal_demand_id` é denormalizado
pelo mesmo motivo de `CaseFact.legal_demand_id`: RLS e as queries do
endpoint `/legal/demands/{id}/artifacts/{aid}/...` nunca precisam de um
JOIN extra até `klm_legal_artifact_versions` só para filtrar por demanda.
"""
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.legal_demand import LegalDemand
    from app.models.user import User
    from app.models.workspace import Workspace


class CitationVerificationStatus(StrEnum):
    """Veredito de uma citação. `nao_verificada` é o default fail-closed —
    nunca é inferido como `verificada` sem uma fonte confirmada (ver
    docstring do módulo e de CitationVerificationService)."""

    verificada = "verificada"
    nao_verificada = "nao_verificada"
    nao_localizada = "nao_localizada"


class CitationKind(StrEnum):
    """Vocabulário fechado do §9: súmula/tema/REsp (acórdão)/lei."""

    sumula = "sumula"
    tema = "tema"
    resp = "resp"
    lei = "lei"


class LegalReviewSeverity(StrEnum):
    """Severidade de um achado da revisão adversarial. `critica` é a única
    que bloqueia protocolabilidade (ver LegalReviewService.review)."""

    critica = "critica"
    alta = "alta"
    media = "media"
    baixa = "baixa"


class LegalReviewFindingKind(StrEnum):
    """Vocabulário fechado dos achados cobertos pela revisão adversarial."""

    placeholder = "placeholder"
    citacao_insegura = "citacao_insegura"
    pedido_sem_fato = "pedido_sem_fato"
    pedido_sem_fundamento = "pedido_sem_fundamento"


class LegalCitationVerification(UUIDMixin, TimestampMixin, Base):
    """Veredito persistido de uma citação extraída de uma versão de artefato."""

    __tablename__ = "legal_citation_verifications"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "artifact_version_id",
            "citation_text",
            name="uq_legal_citation_verification_artifact_text",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("klm_legal_artifact_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    citation_text: Mapped[str] = mapped_column(String(500), nullable=False)
    citation_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CitationVerificationStatus.nao_verificada.value,
        index=True,
    )
    # Trecho/URL da fonte que confirmou a citação — só preenchido quando
    # status == 'verificada'. NUNCA fabricado: ver CitationVerificationService.
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Trilha honesta de como se chegou ao veredito — provedor consultado (ou
    # "sem provedor configurado"), estado do gate, nota do provedor.
    trail: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    checked_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    legal_demand: Mapped[LegalDemand] = relationship("LegalDemand")
    checked_by: Mapped[User | None] = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<LegalCitationVerification id={self.id} status={self.status}>"


class LegalReviewFinding(UUIDMixin, TimestampMixin, Base):
    """Achado de uma execução da revisão adversarial sobre uma versão de
    artefato. Cada chamada de `LegalReviewService.review` substitui o lote
    anterior desta mesma `artifact_version_id` (revisão é sempre sobre o
    estado ATUAL do artefato + da matriz fato-prova-pedido, nunca soma
    achados obsoletos)."""

    __tablename__ = "legal_review_findings"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("klm_legal_artifact_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    severity: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Ponteiro opcional para o que originou o achado — {"pleadingId": ...},
    # {"citationId": ...} — nunca copia o conteúdo em si, só referencia.
    reference: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    legal_demand: Mapped[LegalDemand] = relationship("LegalDemand")
    created_by: Mapped[User | None] = relationship("User")

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<LegalReviewFinding id={self.id} severity={self.severity} kind={self.kind}>"
