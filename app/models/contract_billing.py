"""
Modelo: ContractBillingProposal (F11)
Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção F11.

O sistema lê o contrato de honorários do cliente, extrai um plano de
cobrança estruturado via IA e persiste como PROPOSTA (`status=draft`).
Nenhum `FinanceEntry`/`FinanceRecurrence`/`FeeArrangement` nasce aqui —
só depois que o financeiro aprova (`ContractBillingService.approve_proposal`),
via `ApprovalQueueItem` (mesmo padrão human-in-the-loop de F9).

`items` é a lista de itens extraídos (JSON): cada item tem
`kind` (fixo|entrada|parcela|mensalidade|exito|por_hora), `valor`/`percentual`,
`vencimento`/`ciclo`, `descricao`, `confidence` e `needsReview`. Valores
extraídos por IA são sempre PROPOSTA — nunca fato até aprovação humana.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.workspace import Workspace


class ContractBillingProposalStatus(str, enum.Enum):
    draft = "draft"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"


class ContractBillingProposal(UUIDMixin, TimestampMixin, Base):
    """Plano de cobrança extraído de um contrato — sempre nasce em draft."""

    __tablename__ = "contract_billing_proposals"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_document_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Lista de itens {kind, valor, percentual, vencimento, ciclo, descricao,
    # confidence, needsReview} — nunca vira lançamento antes da aprovação.
    items: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    status: Mapped[ContractBillingProposalStatus] = mapped_column(
        Enum(ContractBillingProposalStatus, name="contract_billing_proposal_status_enum"),
        nullable=False,
        default=ContractBillingProposalStatus.draft,
        index=True,
    )
    extraction_model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # Preenchido em submit_for_approval; liga a proposta ao item da fila humana.
    approval_id: Mapped[Optional[UUID]] = mapped_column(
        FlexibleUUID(),
        ForeignKey("approval_queue_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    matter: Mapped["Matter"] = relationship("Matter")

    def __repr__(self) -> str:
        return (
            f"<ContractBillingProposal id={self.id} matter={self.matter_id} "
            f"status={self.status}>"
        )
