"""
Modelo: CaseParty — Onda 5 (Caso 360 canônico, Plano Mestre §5).

Parte do caso além do cliente titular — polo processual, papel e
qualificação — ligada a UMA `LegalDemand`.

Por que não reaproveitar `LegalDemand.parties` (JSON): aquele campo é um
resumo livre (`[{name, role, docNumber?}]`) da Onda 2. `CaseParty` é a
versão de primeira classe — permite consultar/filtrar por polo processual
e manter qualificação estruturada. `LegalDemand.parties` continua
existindo (não é migrado nem removido) — `Case360Service` expõe os dois:
o resumo legado E as partes de primeira classe.

Não duplica a prova/evidência: isso já existe em `CaseProofLink`
(app/models/case_strategy.py — vínculo fato→prova, com força probatória e
referência a `Document`) — o Caso 360 reusa `CaseProofLink`/`CaseFact`
para o bloco "provas" do agregado, não recria uma tabela de evidência própria.

RLS direta por workspace_id, mesmo padrão de `legal_exceptions`/
`distribution_decisions` (ver app/db/rls.py::CASE_360_DIRECT_TENANT_TABLES).
"""
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.legal_demand import LegalDemand
    from app.models.workspace import Workspace


class CasePartyPole(StrEnum):
    """Polo processual (CPC) — terceiro_interessado cobre assistentes,
    litisconsortes eventuais e outros terceiros sem polo próprio."""

    ativo = "ativo"
    passivo = "passivo"
    terceiro_interessado = "terceiro_interessado"


def _case_party_pole_enum() -> Enum:
    return Enum(
        CasePartyPole,
        name="case_party_pole_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class CaseParty(UUIDMixin, TimestampMixin, Base):
    """Parte do caso além do cliente titular — polo, papel, qualificação."""

    __tablename__ = "case_parties"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_demand_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_demands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    pole: Mapped[CasePartyPole] = mapped_column(
        _case_party_pole_enum(), nullable=False, index=True
    )
    # Papel específico dentro do polo (ex.: "autor", "réu", "assistente
    # técnico", "testemunha") — vocabulário livre, o polo já classifica.
    role: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # {docNumber?, docType?, civilStatus?, profession?, address?, ...} —
    # molde livre; qualificação civil completa não é vocabulário fechado.
    qualification: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    legal_demand: Mapped["LegalDemand"] = relationship("LegalDemand")

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<CaseParty id={self.id} demand={self.legal_demand_id} pole={self.pole}>"
