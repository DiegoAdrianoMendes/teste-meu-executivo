"""
Modelo: Conciliação Bancária (F7) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F7 / §3.8.

`BankTransaction` é a linha crua importada de OFX/CSV ou puxada do extrato
Asaas (`GET /v3/financialTransactions`). `external_fingerprint` é único por
workspace — reimportar o mesmo arquivo/extrato nunca duplica linhas (fitid
do OFX, ou hash determinístico de (data, valor, descrição) para CSV/Asaas).

`ReconciliationMatch` é 1:1 com `BankTransaction` (UNIQUE em
`bank_transaction_id`) e guarda a sugestão/confirmação de conciliação contra
uma cobrança nossa (`charge`), um lançamento financeiro (`entry`) ou uma
despesa (`expense` — também um `FinanceEntry`, ver F8). Nada além do match
determinístico de cobrança nossa se aplica sem confirmação humana.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.workspace import Workspace


class BankTransactionSource(str, enum.Enum):
    ofx = "ofx"
    csv = "csv"
    asaas = "asaas"


class ReconciliationTargetKind(str, enum.Enum):
    charge = "charge"
    entry = "entry"
    expense = "expense"


class ReconciliationMethod(str, enum.Enum):
    deterministico = "deterministico"
    ia = "ia"
    manual = "manual"


class ReconciliationStatus(str, enum.Enum):
    sugerido = "sugerido"
    confirmado = "confirmado"
    rejeitado = "rejeitado"


class BankTransaction(UUIDMixin, TimestampMixin, Base):
    """Linha crua de extrato bancário/gateway (F7)."""

    __tablename__ = "bank_transactions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "external_fingerprint",
            name="uq_bank_transactions_workspace_fingerprint",
        ),
        Index("ix_bank_transactions_workspace_date", "workspace_id", "date"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[BankTransactionSource] = mapped_column(
        Enum(BankTransactionSource, name="bank_transaction_source_enum"), nullable=False
    )
    external_fingerprint: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    raw: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    match: Mapped[Optional["ReconciliationMatch"]] = relationship(
        "ReconciliationMatch",
        back_populates="bank_transaction",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ReconciliationMatch(UUIDMixin, TimestampMixin, Base):
    """Sugestão/confirmação de conciliação de uma `BankTransaction` (F7)."""

    __tablename__ = "reconciliation_matches"
    __table_args__ = (
        UniqueConstraint(
            "bank_transaction_id", name="uq_reconciliation_matches_bank_transaction_id"
        ),
        Index("ix_reconciliation_matches_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bank_transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("bank_transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_kind: Mapped[Optional[ReconciliationTargetKind]] = mapped_column(
        Enum(ReconciliationTargetKind, name="reconciliation_target_kind_enum"), nullable=True
    )
    target_id: Mapped[Optional[UUID]] = mapped_column(nullable=True, index=True)
    method: Mapped[ReconciliationMethod] = mapped_column(
        Enum(ReconciliationMethod, name="reconciliation_method_enum"), nullable=False
    )
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    suggested_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    suggested_tags: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    status: Mapped[ReconciliationStatus] = mapped_column(
        Enum(ReconciliationStatus, name="reconciliation_status_enum"),
        nullable=False,
        default=ReconciliationStatus.sugerido,
        index=True,
    )
    confirmed_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_snapshot: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    bank_transaction: Mapped["BankTransaction"] = relationship(
        "BankTransaction", back_populates="match"
    )
