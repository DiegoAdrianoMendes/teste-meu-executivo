"""
Modelo: Growth CRM da plataforma (funil de aquisição B2B dos escritórios-clientes).

"Casa de ferreiro, espeto de pau": o KLM Hub vende CRM para escritórios de
advocacia mas não tinha CRM próprio para captar esses escritórios. Este
módulo vive só no Super Admin (staff-only, require_platform_root) e não tem
relação nenhuma com o CRM que o tenant usa para os próprios clientes dele.

  - GrowthProspect: escritório-alvo, com estágio de funil
    (prospect -> contatado -> demo_agendada -> trial -> proposta -> pagante | perdido)
  - GrowthActivity: toques/atividades feitos com o prospect (ligação, e-mail,
    whatsapp, demo, nota)
  - GrowthStageEvent: auditoria append-only de toda mudança de estágio

Distinto de WorkspaceSubscription/SubscriptionEvent (app/models/subscription_commercial.py),
que é a assinatura comercial JÁ ativa de um workspace existente. Aqui é o
pipeline PRÉ-venda: um prospect só ganha workspace_id quando vira cliente.
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class GrowthFunnelStage(str, enum.Enum):
    prospect = "prospect"
    contatado = "contatado"
    demo_agendada = "demo_agendada"
    trial = "trial"
    proposta = "proposta"
    pagante = "pagante"
    perdido = "perdido"


class GrowthAcquisitionChannel(str, enum.Enum):
    indicacao = "indicacao"
    outbound = "outbound"
    inbound_site = "inbound_site"
    evento = "evento"
    rede_propria = "rede_propria"
    parceria = "parceria"
    anuncio = "anuncio"
    backfill = "backfill"
    outro = "outro"


class GrowthActivityType(str, enum.Enum):
    ligacao = "ligacao"
    email = "email"
    whatsapp = "whatsapp"
    demo = "demo"
    nota = "nota"


_FUNNEL_STAGES = [s.value for s in GrowthFunnelStage]
_ACQUISITION_CHANNELS = [c.value for c in GrowthAcquisitionChannel]
_ACTIVITY_TYPES = [t.value for t in GrowthActivityType]


def _stage_enum() -> Enum:
    return Enum(
        GrowthFunnelStage,
        name="growth_funnel_stage_enum",
        native_enum=False,
        validate_strings=True,
        length=20,
    )


def _channel_enum() -> Enum:
    return Enum(
        GrowthAcquisitionChannel,
        name="growth_acquisition_channel_enum",
        native_enum=False,
        validate_strings=True,
        length=20,
    )


def _activity_type_enum() -> Enum:
    return Enum(
        GrowthActivityType,
        name="growth_activity_type_enum",
        native_enum=False,
        validate_strings=True,
        length=20,
    )


# Estágios considerados "abertos" (ainda no funil, não terminal) para métricas.
OPEN_STAGES = (
    GrowthFunnelStage.prospect,
    GrowthFunnelStage.contatado,
    GrowthFunnelStage.demo_agendada,
    GrowthFunnelStage.trial,
    GrowthFunnelStage.proposta,
)
WON_STAGE = GrowthFunnelStage.pagante
LOST_STAGE = GrowthFunnelStage.perdido


class GrowthProspect(UUIDMixin, TimestampMixin, Base):
    """Escritório-alvo no funil de aquisição B2B da plataforma."""

    __tablename__ = "growth_prospects"
    __table_args__ = (
        CheckConstraint(
            f"estagio IN ({', '.join(repr(s) for s in _FUNNEL_STAGES)})",
            name="ck_growth_prospects_estagio",
        ),
        CheckConstraint(
            f"origem IN ({', '.join(repr(c) for c in _ACQUISITION_CHANNELS)})",
            name="ck_growth_prospects_origem",
        ),
        CheckConstraint(
            "valor_estimado_cents >= 0", name="ck_growth_prospects_valor_nonneg"
        ),
        Index("ix_growth_prospects_estagio", "estagio"),
        Index("ix_growth_prospects_origem", "origem"),
        Index("ix_growth_prospects_owner", "owner_user_id"),
        Index("ix_growth_prospects_workspace", "workspace_id"),
    )

    nome_escritorio: Mapped[str] = mapped_column(String(200), nullable=False)
    contato_nome: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    contato_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contato_telefone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    cidade: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    uf: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    tamanho_estimado: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    origem: Mapped[GrowthAcquisitionChannel] = mapped_column(
        _channel_enum(), nullable=False, default=GrowthAcquisitionChannel.outro
    )
    estagio: Mapped[GrowthFunnelStage] = mapped_column(
        _stage_enum(), nullable=False, default=GrowthFunnelStage.prospect
    )
    valor_estimado_cents: Mapped[int] = mapped_column(nullable=False, default=0)
    motivo_perda: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owner_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )

    stage_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    won_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lost_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    metadata_json: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)

    owner: Mapped[Optional["User"]] = relationship("User", foreign_keys=[owner_user_id])
    workspace: Mapped[Optional["Workspace"]] = relationship("Workspace")
    activities: Mapped[list["GrowthActivity"]] = relationship(
        "GrowthActivity",
        back_populates="prospect",
        cascade="all, delete-orphan",
        order_by="GrowthActivity.occurred_at.desc()",
    )
    stage_events: Mapped[list["GrowthStageEvent"]] = relationship(
        "GrowthStageEvent",
        back_populates="prospect",
        cascade="all, delete-orphan",
        order_by="GrowthStageEvent.occurred_at.desc()",
    )


class GrowthActivity(UUIDMixin, Base):
    """Um toque/atividade de vendas registrado com um prospect."""

    __tablename__ = "growth_activities"
    __table_args__ = (
        CheckConstraint(
            f"tipo IN ({', '.join(repr(t) for t in _ACTIVITY_TYPES)})",
            name="ck_growth_activities_tipo",
        ),
        Index("ix_growth_activities_prospect_occurred", "prospect_id", "occurred_at"),
    )

    prospect_id: Mapped[UUID] = mapped_column(
        ForeignKey("growth_prospects.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[GrowthActivityType] = mapped_column(_activity_type_enum(), nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(120), nullable=False, default="sistema")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    prospect: Mapped["GrowthProspect"] = relationship(
        "GrowthProspect", back_populates="activities"
    )
    actor: Mapped[Optional["User"]] = relationship("User", foreign_keys=[actor_user_id])


class GrowthStageEvent(UUIDMixin, Base):
    """Auditoria append-only de toda mudança de estágio de um prospect."""

    __tablename__ = "growth_stage_events"
    __table_args__ = (
        CheckConstraint(
            f"from_estagio IS NULL OR from_estagio IN ({', '.join(repr(s) for s in _FUNNEL_STAGES)})",
            name="ck_growth_stage_events_from",
        ),
        CheckConstraint(
            f"to_estagio IN ({', '.join(repr(s) for s in _FUNNEL_STAGES)})",
            name="ck_growth_stage_events_to",
        ),
        Index("ix_growth_stage_events_prospect_occurred", "prospect_id", "occurred_at"),
    )

    prospect_id: Mapped[UUID] = mapped_column(
        ForeignKey("growth_prospects.id", ondelete="CASCADE"), nullable=False
    )
    from_estagio: Mapped[Optional[GrowthFunnelStage]] = mapped_column(
        _stage_enum(), nullable=True
    )
    to_estagio: Mapped[GrowthFunnelStage] = mapped_column(_stage_enum(), nullable=False)
    actor_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(120), nullable=False, default="sistema")
    motivo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    prospect: Mapped["GrowthProspect"] = relationship(
        "GrowthProspect", back_populates="stage_events"
    )
    actor: Mapped[Optional["User"]] = relationship("User", foreign_keys=[actor_user_id])
