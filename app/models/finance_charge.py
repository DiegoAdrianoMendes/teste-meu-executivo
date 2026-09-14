"""
Modelo: FinanceCharge
Cobrança em gateway externo (Asaas etc.) vinculada a uma FinanceEntry.
Fundação transacional do módulo financeiro (F1) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 4.2.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.finance import FinanceEntry
    from app.models.workspace import Workspace


class FinanceChargeGateway(str, enum.Enum):
    asaas = "asaas"


class FinanceChargeKind(str, enum.Enum):
    avulsa = "avulsa"
    parcela = "parcela"
    recorrencia = "recorrencia"


class FinanceChargeStatus(str, enum.Enum):
    rascunho = "rascunho"
    criada = "criada"
    visualizada = "visualizada"
    confirmada = "confirmada"
    recebida = "recebida"
    vencida = "vencida"
    estornada = "estornada"
    chargeback = "chargeback"
    cancelada = "cancelada"


class FinanceCharge(UUIDMixin, TimestampMixin, Base):
    """Cobrança criada no gateway de pagamento para uma FinanceEntry.

    Relação 1:1 com FinanceEntry (uma parcela/série vira N entries, cada uma
    com sua própria charge). O estado interno é mapeado a partir dos eventos
    de webhook recebidos do gateway (ver app/models/asaas_webhook_receipt.py).
    """

    __tablename__ = "finance_charges"
    __table_args__ = (
        UniqueConstraint("entry_id", name="uq_finance_charges_entry_id"),
        Index("ix_finance_charges_workspace_status", "workspace_id", "status"),
        Index("ix_finance_charges_workspace_due_date", "workspace_id", "due_date"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gateway: Mapped[FinanceChargeGateway] = mapped_column(
        Enum(FinanceChargeGateway, name="finance_charge_gateway_enum"),
        nullable=False,
        default=FinanceChargeGateway.asaas,
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    billing_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    charge_kind: Mapped[FinanceChargeKind] = mapped_column(
        Enum(FinanceChargeKind, name="finance_charge_kind_enum"),
        nullable=False,
        default=FinanceChargeKind.avulsa,
    )
    status: Mapped[FinanceChargeStatus] = mapped_column(
        Enum(FinanceChargeStatus, name="finance_charge_status_enum"),
        nullable=False,
        default=FinanceChargeStatus.rascunho,
        index=True,
    )
    value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    net_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    fee_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    invoice_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    bank_slip_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pix_payload: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    installment_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    subscription_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    payload_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    entry: Mapped["FinanceEntry"] = relationship("FinanceEntry")
