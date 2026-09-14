"""
Modelos: FinanceRecurrence / FinanceRecurrenceRun
Recorrência e parcelamento do módulo financeiro (F3) — ver
docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seção 5 (F3) e 4.2.

`FinanceRecurrence` guarda o "molde" de um lançamento que se repete em um
ciclo (assinatura no gateway ou geração agendada local). `FinanceRecurrenceRun`
é a tabela de controle de idempotência: uma linha por competência efetivamente
processada (`UNIQUE(recurrence_id, competence)`), garantindo que rodar o tick
N vezes na mesma competência gere exatamente 1 FinanceEntry/FinanceCharge.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin
from app.models.finance import FinanceEntryType

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.finance import FinanceEntry
    from app.models.finance_charge import FinanceCharge
    from app.models.matter import Matter
    from app.models.workspace import Workspace


class FinanceRecurrenceKind(str, enum.Enum):
    assinatura_gateway = "assinatura_gateway"
    geracao_programada = "geracao_programada"


class FinanceRecurrenceCycle(str, enum.Enum):
    """Ciclo de repetição — mapeável ao enum de `cycle` do Asaas
    (`SubscriptionRequest.cycle`, ver app/payments/port.py)."""

    semanal = "semanal"
    quinzenal = "quinzenal"
    mensal = "mensal"
    bimestral = "bimestral"
    trimestral = "trimestral"
    semestral = "semestral"
    anual = "anual"


# Mapa de ciclo interno -> `cycle` do Asaas (plano §3.4).
ASAAS_CYCLE_BY_RECURRENCE_CYCLE: dict[FinanceRecurrenceCycle, str] = {
    FinanceRecurrenceCycle.semanal: "WEEKLY",
    FinanceRecurrenceCycle.quinzenal: "BIWEEKLY",
    FinanceRecurrenceCycle.mensal: "MONTHLY",
    FinanceRecurrenceCycle.bimestral: "BIMONTHLY",
    FinanceRecurrenceCycle.trimestral: "QUARTERLY",
    FinanceRecurrenceCycle.semestral: "SEMIANNUALLY",
    FinanceRecurrenceCycle.anual: "YEARLY",
}


class FinanceRecurrenceStatus(str, enum.Enum):
    ativa = "ativa"
    pausada = "pausada"
    encerrada = "encerrada"


class FinanceRecurrence(UUIDMixin, TimestampMixin, Base):
    """Molde de lançamento recorrente (assinatura no gateway ou geração
    agendada local pela task `finance_tasks.finance_recurrence_workspace`, despachada
    pelo tick de descoberta)."""

    __tablename__ = "finance_recurrences"
    __table_args__ = (
        Index("ix_finance_recurrences_workspace_status", "workspace_id", "status"),
        Index("ix_finance_recurrences_workspace_next_run", "workspace_id", "next_run"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Molde do lançamento gerado a cada competência.
    entry_title: Mapped[str] = mapped_column(String(200), nullable=False)
    entry_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entry_value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    entry_type: Mapped[FinanceEntryType] = mapped_column(
        Enum(FinanceEntryType, name="finance_entry_type_enum"),
        nullable=False,
        default=FinanceEntryType.receivable,
    )

    kind: Mapped[FinanceRecurrenceKind] = mapped_column(
        Enum(FinanceRecurrenceKind, name="finance_recurrence_kind_enum"),
        nullable=False,
        default=FinanceRecurrenceKind.geracao_programada,
    )
    cycle: Mapped[FinanceRecurrenceCycle] = mapped_column(
        Enum(FinanceRecurrenceCycle, name="finance_recurrence_cycle_enum"),
        nullable=False,
        default=FinanceRecurrenceCycle.mensal,
    )
    billing_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    next_run: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    max_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    generated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[FinanceRecurrenceStatus] = mapped_column(
        Enum(FinanceRecurrenceStatus, name="finance_recurrence_status_enum"),
        nullable=False,
        default=FinanceRecurrenceStatus.ativa,
        index=True,
    )
    gateway_subscription_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    recurrence_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    client: Mapped["Client"] = relationship("Client")
    matter: Mapped[Optional["Matter"]] = relationship("Matter")
    runs: Mapped[list["FinanceRecurrenceRun"]] = relationship(
        "FinanceRecurrenceRun",
        back_populates="recurrence",
        cascade="all, delete-orphan",
        order_by="FinanceRecurrenceRun.competence.asc()",
    )


class FinanceRecurrenceRun(UUIDMixin, TimestampMixin, Base):
    """Uma linha por competência efetivamente processada por uma recorrência.

    `UNIQUE(recurrence_id, competence)` é a trava de idempotência: rodar o
    tick 10x na mesma competência só consegue inserir esta linha uma vez —
    as demais tentativas colidem e são tratadas como no-op pelo worker.
    """

    __tablename__ = "finance_recurrence_runs"
    __table_args__ = (
        UniqueConstraint(
            "recurrence_id", "competence", name="uq_finance_recurrence_runs_recurrence_competence"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recurrence_id: Mapped[UUID] = mapped_column(
        ForeignKey("finance_recurrences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    competence: Mapped[date] = mapped_column(Date, nullable=False)
    entry_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("finance_entries.id", ondelete="SET NULL"), nullable=True
    )
    charge_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("finance_charges.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    recurrence: Mapped["FinanceRecurrence"] = relationship("FinanceRecurrence", back_populates="runs")
    entry: Mapped[Optional["FinanceEntry"]] = relationship("FinanceEntry")
    charge: Mapped[Optional["FinanceCharge"]] = relationship("FinanceCharge")
