"""
Modelos: FeeArrangement / TimeEntry
Honorários jurídicos (F9) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F9).

`FeeArrangement` é o "molde" contratual de honorários de um caso (fixo,
entrada + parcelas, mensalidade/retainer, por hora, êxito, quota litis ou
híbrido). `TimeEntry` é o lançamento de horas trabalhadas usado pelo modelo
POR_HORA — cada lançamento vira `billed=True` e ganha `invoice_entry_id`
quando consolidado em uma fatura mensal por `FeeService.close_monthly_invoice`
(idempotente por competência via `FinanceEntry.external_ref`).

`fixed_value`/`percent` NUNCA são gerados por IA — são decisão humana
registrada no contrato (mesmo princípio de F13: "percentuais são decisão
humana, o sistema registra e executa").
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.finance import FinanceEntry
    from app.models.matter import Matter
    from app.models.user import User
    from app.models.workspace import Workspace


class FeeArrangementKind(str, enum.Enum):
    fixo = "fixo"
    entrada_exito = "entrada_exito"
    mensalidade = "mensalidade"
    por_hora = "por_hora"
    exito = "exito"
    quota_litis = "quota_litis"
    hibrido = "hibrido"


class FeeArrangementStatus(str, enum.Enum):
    ativo = "ativo"
    encerrado = "encerrado"


class FeeArrangement(UUIDMixin, TimestampMixin, Base):
    """Molde contratual de honorários de um caso."""

    __tablename__ = "fee_arrangements"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[FeeArrangementKind] = mapped_column(
        Enum(FeeArrangementKind, name="fee_arrangement_kind_enum"), nullable=False
    )
    # Nunca gerados por IA — decisão humana registrada no contrato de honorários.
    fixed_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    percent: Mapped[Optional[float]] = mapped_column(Numeric(6, 3), nullable=True)
    base_calculo_exito: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hourly_rate: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[FeeArrangementStatus] = mapped_column(
        Enum(FeeArrangementStatus, name="fee_arrangement_status_enum"),
        nullable=False,
        default=FeeArrangementStatus.ativo,
        index=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    matter: Mapped["Matter"] = relationship("Matter")
    time_entries: Mapped[list["TimeEntry"]] = relationship(
        "TimeEntry", back_populates="arrangement", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<FeeArrangement id={self.id} kind={self.kind} matter={self.matter_id}>"


class TimeEntry(UUIDMixin, TimestampMixin, Base):
    """Lançamento de horas trabalhadas (modelo POR_HORA)."""

    __tablename__ = "time_entries"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    arrangement_id: Mapped[UUID] = mapped_column(
        ForeignKey("fee_arrangements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hourly_rate: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    billed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    invoice_entry_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("finance_entries.id", ondelete="SET NULL"), nullable=True, index=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    arrangement: Mapped["FeeArrangement"] = relationship(
        "FeeArrangement", back_populates="time_entries"
    )
    matter: Mapped["Matter"] = relationship("Matter")
    user: Mapped["User"] = relationship("User")
    invoice_entry: Mapped[Optional["FinanceEntry"]] = relationship("FinanceEntry")

    def __repr__(self) -> str:
        return f"<TimeEntry id={self.id} matter={self.matter_id} minutes={self.minutes} billed={self.billed}>"
