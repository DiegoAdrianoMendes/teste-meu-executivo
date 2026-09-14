"""Módulo processual contratável à parte — monitoramento DJEN/DataJud.

`MonitoredProcess` é o processo cadastrado para acompanhamento (quota
`EntitlementKey.monitored_processes`, ver app/services/entitlement_service.py).
`ProcessPublication` é cada publicação/andamento ingerido de forma idempotente
(dedupe por `workspace_id + fonte + external_id`) — quando ela é uma
intimação com prazo, nasce com `prazo_status=sugerido` e memória de cálculo
auditável (app/services/prazo_calculator.py); NUNCA se torna prazo fatal sem
confirmação humana explícita (ver app/services/process_monitoring_service.py
e o dossiê app/agents/knowledge/processual_monitoramento.py, regra 10).
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class MonitoredProcessStatus(StrEnum):
    ativo = "ativo"
    pausado = "pausado"
    erro = "erro"


class ProcessPublicationSource(StrEnum):
    djen = "djen"
    datajud = "datajud"


class DeadlineCountingType(StrEnum):
    dias_uteis = "dias_uteis"
    dias_corridos = "dias_corridos"


class DeadlineStatus(StrEnum):
    prazo_sugerido = "prazo_sugerido"
    confirmado = "confirmado"
    dispensado = "dispensado"


class PublicationJourneyState(StrEnum):
    """Os 19 estados da jornada publicação→pasta (Plano Mestre §6).

    Máquina MONOTÔNICA — ver `app/services/legal_journey_service.py` para o
    grafo de transições válidas (`VALID_TRANSITIONS`). Nasce sempre em
    `capturada`; `protocolo_registrado` só é alcançável por ação HUMANA
    explícita, nunca por agente (regra dura do serviço, não só convenção).
    """

    capturada = "capturada"
    deduplicada = "deduplicada"
    vinculo_sugerido = "vinculo_sugerido"
    vinculada = "vinculada"
    triagem_concluida = "triagem_concluida"
    prazo_sugerido = "prazo_sugerido"
    prazo_confirmado = "prazo_confirmado"
    trabalho_distribuido = "trabalho_distribuido"
    documentos_solicitados = "documentos_solicitados"
    estrategia_preparada = "estrategia_preparada"
    minuta_em_producao = "minuta_em_producao"
    revisao_juridica = "revisao_juridica"
    correcoes_pendentes = "correcoes_pendentes"
    aprovada = "aprovada"
    pasta_em_preparacao = "pasta_em_preparacao"
    pasta_validada = "pasta_validada"
    aguardando_protocolo_humano = "aguardando_protocolo_humano"
    protocolo_registrado = "protocolo_registrado"
    acompanhamento_criado = "acompanhamento_criado"


def _monitored_process_status_enum() -> Enum:
    return Enum(
        MonitoredProcessStatus,
        name="monitored_process_status_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _publication_source_enum() -> Enum:
    return Enum(
        ProcessPublicationSource,
        name="process_publication_source_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _deadline_counting_type_enum() -> Enum:
    return Enum(
        DeadlineCountingType,
        name="process_deadline_counting_type_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _deadline_status_enum() -> Enum:
    return Enum(
        DeadlineStatus,
        name="process_deadline_status_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


def _journey_state_enum() -> Enum:
    return Enum(
        PublicationJourneyState,
        name="publication_journey_state_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class MonitoredProcess(UUIDMixin, TimestampMixin, Base):
    """Processo cadastrado para monitoramento DJEN/DataJud (add-on pago)."""

    __tablename__ = "monitored_processes"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "numero_cnj", name="uq_monitored_process_workspace_numero_cnj"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_monitored_process_workspace_matter",
            ondelete="CASCADE",
        ),
        Index("ix_monitored_process_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[Optional[UUID]] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    numero_cnj: Mapped[str] = mapped_column(String(25), nullable=False)
    tribunal: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[MonitoredProcessStatus] = mapped_column(
        _monitored_process_status_enum(),
        nullable=False,
        default=MonitoredProcessStatus.ativo,
    )
    oab_vinculada: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    responsible_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    last_movement_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Sem relationship ORM para `matter`: a FK é composta (workspace_id,
    # matter_id) só para integridade referencial; o serviço busca o Matter
    # por id diretamente quando precisa (matter_id já é globalmente único).
    workspace: Mapped["Workspace"] = relationship("Workspace")
    responsible_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[responsible_user_id]
    )
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_user_id])
    publications: Mapped[list["ProcessPublication"]] = relationship(
        "ProcessPublication",
        back_populates="monitored_process",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return f"<MonitoredProcess id={self.id} numero_cnj={self.numero_cnj} status={self.status}>"


class ProcessPublication(UUIDMixin, TimestampMixin, Base):
    """Publicação/andamento ingerido de DJEN ou DataJud (dedupe idempotente)."""

    __tablename__ = "process_publications"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "fonte",
            "external_id",
            name="uq_process_publication_workspace_fonte_external_id",
        ),
        CheckConstraint(
            "(prazo_status IS NULL) = (dias IS NULL)",
            name="ck_process_publication_prazo_requires_dias",
        ),
        CheckConstraint(
            "prazo_status <> 'confirmado' OR "
            "(confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_process_publication_confirmed_requires_actor",
        ),
        CheckConstraint(
            "data_fatal IS NULL OR dias IS NOT NULL",
            name="ck_process_publication_data_fatal_requires_dias",
        ),
        Index("ix_process_publication_workspace_urgencia", "workspace_id", "urgencia"),
        Index("ix_process_publication_workspace_prazo_status", "workspace_id", "prazo_status"),
        Index("ix_process_publication_monitored_process", "monitored_process_id"),
        Index("ix_process_publication_workspace_journey_state", "workspace_id", "journey_state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitored_process_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("monitored_processes.id", ondelete="SET NULL"), nullable=True
    )
    fonte: Mapped[ProcessPublicationSource] = mapped_column(
        _publication_source_enum(), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    numero_cnj: Mapped[str] = mapped_column(String(25), nullable=False)
    conteudo: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tipo: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    urgencia: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # ── Prazo (sempre `prazo_sugerido` ao nascer; nunca fatal sem confirmação) ──
    dias: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tipo_contagem: Mapped[Optional[DeadlineCountingType]] = mapped_column(
        _deadline_counting_type_enum(), nullable=True
    )
    data_fatal: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    memoria_calculo: Mapped[Optional[list]] = mapped_column(FlexibleJSON(), nullable=True)
    prazo_status: Mapped[Optional[DeadlineStatus]] = mapped_column(
        _deadline_status_enum(), nullable=True
    )
    confirmed_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Jornada publicação→pasta (19 estados, Plano Mestre §6) ──
    # Ver app/services/legal_journey_service.py — máquina de estados
    # monotônica, transições explícitas, auditada via KlmAuditEvent.
    journey_state: Mapped[PublicationJourneyState] = mapped_column(
        _journey_state_enum(),
        nullable=False,
        default=PublicationJourneyState.capturada,
        server_default=PublicationJourneyState.capturada.value,
    )
    journey_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    monitored_process: Mapped[Optional["MonitoredProcess"]] = relationship(
        "MonitoredProcess", back_populates="publications"
    )
    confirmed_by: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[confirmed_by_user_id]
    )

    def __repr__(self) -> str:  # pragma: no cover — debug convenience only
        return (
            f"<ProcessPublication id={self.id} fonte={self.fonte} "
            f"prazo_status={self.prazo_status}>"
        )
