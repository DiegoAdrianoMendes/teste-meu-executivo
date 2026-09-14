"""
Modelo: FinanceDunningLog — régua de cobrança (dunning), F4.

Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F4.

Idempotência absoluta: `UNIQUE(workspace_id, charge_id, step)` garante que um
degrau da régua só é registrado (e, portanto, só dispara) uma única vez por
cobrança, mesmo que a task `dunning_workspace` rode 10x sobre a mesma janela.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.finance_charge import FinanceCharge
    from app.models.workspace import Workspace


class FinanceDunningStep(str, enum.Enum):
    d1_lembrete = "d1_lembrete"
    d7_segunda_via = "d7_segunda_via"
    d15_formal = "d15_formal"


class FinanceDunningLogStatus(str, enum.Enum):
    sent = "sent"
    local_draft = "local_draft"
    pending_approval = "pending_approval"
    blocked_gate_closed = "blocked_gate_closed"


class FinanceDunningLog(UUIDMixin, TimestampMixin, Base):
    """Registro de execução (ou tentativa) de um degrau da régua de cobrança."""

    __tablename__ = "finance_dunning_log"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "charge_id", "step", name="uq_finance_dunning_log_charge_step"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    charge_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_charges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step: Mapped[FinanceDunningStep] = mapped_column(
        Enum(FinanceDunningStep, name="finance_dunning_step_enum"), nullable=False
    )
    status: Mapped[FinanceDunningLogStatus] = mapped_column(
        Enum(FinanceDunningLogStatus, name="finance_dunning_log_status_enum"),
        nullable=False,
        default=FinanceDunningLogStatus.local_draft,
        index=True,
    )
    reason_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    viewed_before_dispatch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    charge: Mapped["FinanceCharge"] = relationship("FinanceCharge")
