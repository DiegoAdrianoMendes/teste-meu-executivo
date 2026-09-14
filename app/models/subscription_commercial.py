"""
Modelo local-first de assinatura comercial (Sprint B1 — Centro de Comando).

Distinto do par `Plan`/`Subscription` em `app/models/billing.py` (que espelha o
Stripe em inglês e será a fonte de verdade quando o webhook Stripe for ligado).
Aqui vive a camada comercial local, com vocabulário em português usado pelo
Super Admin e pelo dono da conta, e o log de auditoria de toda mudança feita
por um operador de plataforma. Ver docs/PRICING_BILLING_POLITICA_2026-07.md.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class WorkspacePlanCode(str, enum.Enum):
    """Planos comerciais publicados em PRICING_BILLING_POLITICA_2026-07.md."""

    essencial = "essencial"
    escritorio = "escritorio"
    performance = "performance"
    custom = "custom"


class WorkspaceSubscriptionStatus(str, enum.Enum):
    trial = "trial"
    ativa = "ativa"
    inadimplente = "inadimplente"
    pausada = "pausada"
    cancelada = "cancelada"


class SubscriptionEventType(str, enum.Enum):
    criada = "criada"
    plano_alterado = "plano_alterado"
    desconto_aplicado = "desconto_aplicado"
    trial_estendido = "trial_estendido"
    beneficio_aplicado = "beneficio_aplicado"
    beneficio_revertido = "beneficio_revertido"
    suspensa = "suspensa"
    reativada = "reativada"
    cancelada = "cancelada"
    backfill = "backfill"


def _plan_enum() -> Enum:
    return Enum(
        WorkspacePlanCode,
        name="workspace_plan_code_enum",
        native_enum=False,
        validate_strings=True,
        length=20,
    )


def _status_enum() -> Enum:
    return Enum(
        WorkspaceSubscriptionStatus,
        name="workspace_subscription_status_enum",
        native_enum=False,
        validate_strings=True,
        length=20,
    )


def _event_type_enum() -> Enum:
    return Enum(
        SubscriptionEventType,
        name="subscription_event_type_enum",
        native_enum=False,
        validate_strings=True,
        length=20,
    )


class WorkspaceSubscription(UUIDMixin, TimestampMixin, Base):
    """Assinatura comercial local de um workspace (fonte para o Centro de Comando)."""

    __tablename__ = "workspace_subscriptions"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_workspace_subscriptions_workspace"),
        CheckConstraint(
            "plano IN ('essencial', 'escritorio', 'performance', 'custom')",
            name="ck_workspace_subscriptions_plano",
        ),
        CheckConstraint(
            "status IN ('trial', 'ativa', 'inadimplente', 'pausada', 'cancelada')",
            name="ck_workspace_subscriptions_status",
        ),
        CheckConstraint("valor_mensal_cents >= 0", name="ck_workspace_subscriptions_valor_nonneg"),
        Index("ix_workspace_subscriptions_status", "status"),
        Index("ix_workspace_subscriptions_plano", "plano"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    plano: Mapped[WorkspacePlanCode] = mapped_column(_plan_enum(), nullable=False)
    status: Mapped[WorkspaceSubscriptionStatus] = mapped_column(
        _status_enum(), nullable=False, default=WorkspaceSubscriptionStatus.trial
    )
    valor_mensal_cents: Mapped[int] = mapped_column(nullable=False, default=0)
    ciclo: Mapped[str] = mapped_column(String(20), nullable=False, default="mensal")
    desconto_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    cortesia: Mapped[bool] = mapped_column(nullable=False, default=False)
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    origem: Mapped[str] = mapped_column(String(40), nullable=False, default="backfill")
    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    workspace: Mapped["Workspace"] = relationship("Workspace")
    events: Mapped[list["SubscriptionEvent"]] = relationship(
        "SubscriptionEvent",
        back_populates="subscription",
        cascade="all, delete-orphan",
        order_by="SubscriptionEvent.occurred_at.desc()",
    )

    @property
    def mrr_cents(self) -> int:
        if self.status not in (
            WorkspaceSubscriptionStatus.trial,
            WorkspaceSubscriptionStatus.ativa,
            WorkspaceSubscriptionStatus.inadimplente,
        ):
            return 0
        base = self.valor_mensal_cents
        if self.ciclo == "anual":
            base = base // 12
        if self.desconto_pct:
            base = int(base * (Decimal("100") - self.desconto_pct) / Decimal("100"))
        return base


class SubscriptionEvent(UUIDMixin, Base):
    """Auditoria append-only de toda mudança feita numa assinatura comercial."""

    __tablename__ = "subscription_events"
    __table_args__ = (
        Index("ix_subscription_events_subscription_occurred", "subscription_id", "occurred_at"),
        Index("ix_subscription_events_workspace", "workspace_id"),
    )

    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[SubscriptionEventType] = mapped_column(_event_type_enum(), nullable=False)
    actor_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(120), nullable=False, default="sistema")
    motivo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    after_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    subscription: Mapped["WorkspaceSubscription"] = relationship(
        "WorkspaceSubscription", back_populates="events"
    )
    actor: Mapped[Optional["User"]] = relationship("User")
