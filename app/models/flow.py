"""
Flow Builder models — Automações jurídicas
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, FlexibleUUID

FLOW_REVISION_SNAPSHOT_SCHEMA_VERSION = 1
FLOW_REVISION_FIELDS = (
    "name",
    "description",
    "practice_area",
    "is_template",
    "is_active",
    "trigger",
    "trigger_config",
    "nodes",
    "edges",
)


def _snapshot_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def build_flow_revision_snapshot(
    flow: Flow,
    *,
    revision_id: uuid.UUID,
    revision_number: int,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete, detached point-in-time representation of a flow."""
    if flow.id is None:
        raise ValueError("Flow precisa estar persistido antes de criar uma revisão")

    values = {field: getattr(flow, field) for field in FLOW_REVISION_FIELDS}
    metadata_values: dict[str, Any] = {
        "run_count": flow.run_count or 0,
        "created_at": flow.created_at,
        "updated_at": flow.updated_at,
        "deleted_at": flow.deleted_at,
    }
    for field, value in (overrides or {}).items():
        if field in values:
            values[field] = value
        elif field in metadata_values:
            metadata_values[field] = value

    snapshot: dict[str, Any] = {
        "schema_version": FLOW_REVISION_SNAPSHOT_SCHEMA_VERSION,
        "revision_id": str(revision_id),
        "revision": revision_number,
        "version": revision_number,
        "flow_id": str(flow.id),
        "workspace_id": str(flow.workspace_id) if flow.workspace_id else None,
        **values,
        "run_count": metadata_values["run_count"],
        "created_at": _snapshot_datetime(metadata_values["created_at"]),
        "updated_at": _snapshot_datetime(metadata_values["updated_at"]),
        "deleted_at": _snapshot_datetime(metadata_values["deleted_at"]),
    }
    return copy.deepcopy(snapshot)


def flow_revision_snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verified_flow_revision_snapshot(revision: FlowRevision) -> dict[str, Any]:
    """Return a detached snapshot after validating identity and integrity."""
    snapshot = copy.deepcopy(revision.snapshot or {})
    if flow_revision_snapshot_hash(snapshot) != revision.snapshot_hash:
        raise ValueError("Snapshot da revisão do flow falhou na verificação de integridade")
    if snapshot.get("flow_id") != str(revision.flow_id):
        raise ValueError("Snapshot da revisão pertence a outro flow")
    if snapshot.get("revision_id") != str(revision.id):
        raise ValueError("Snapshot da revisão possui identificador inconsistente")
    if snapshot.get("revision") != revision.revision_number:
        raise ValueError("Snapshot da revisão possui número inconsistente")
    return snapshot


class Flow(Base):
    """
    Fluxo de automação. Pode ser um template de biblioteca (is_template=True)
    ou um fluxo personalizado do workspace.
    """

    __tablename__ = "flows"

    id: Mapped[uuid.UUID] = mapped_column(FlexibleUUID(), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    practice_area: Mapped[str] = mapped_column(String(100), nullable=False, default="universal")
    # Templates de biblioteca têm workspace_id=None e is_template=True
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Trigger: new_lead | column_moved | message_received | manual | scheduled
    trigger: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    trigger_config: Mapped[dict[str, Any] | None] = mapped_column(FlexibleJSON(), nullable=True)
    # Nodes e edges como JSONB (estrutura do ReactFlow)
    nodes: Mapped[list | None] = mapped_column(FlexibleJSON(), nullable=True, default=list)
    edges: Mapped[list | None] = mapped_column(FlexibleJSON(), nullable=True, default=list)
    # Stats
    run_count: Mapped[int] = mapped_column(default=0)
    # Ponteiro autoritativo para a definição imutável corrente.
    current_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey(
            "flow_revisions.id",
            name="fk_flows_current_revision_id_flow_revisions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    runs: Mapped[list[FlowRun]] = relationship(
        "FlowRun",
        back_populates="flow",
        foreign_keys="FlowRun.flow_id",
        lazy="select",
    )
    revisions: Mapped[list[FlowRevision]] = relationship(
        "FlowRevision",
        back_populates="flow",
        foreign_keys="FlowRevision.flow_id",
        order_by="FlowRevision.revision_number",
        lazy="select",
        passive_deletes=True,
    )
    current_revision_record: Mapped[FlowRevision | None] = relationship(
        "FlowRevision",
        foreign_keys=[current_revision_id],
        post_update=True,
        lazy="select",
    )

    __table_args__ = (
        CheckConstraint("current_revision >= 0", name="ck_flows_current_revision_nonnegative"),
        Index("ix_flows_current_revision_id", "current_revision_id"),
    )

    @property
    def version(self) -> int:
        return self.current_revision


class FlowRevision(Base):
    """Append-only snapshot of a Flow definition."""

    __tablename__ = "flow_revisions"

    id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    flow_id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey(
            "flows.id",
            name="fk_flow_revisions_flow_id_flows",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=FLOW_REVISION_SNAPSHOT_SCHEMA_VERSION,
        server_default=str(FLOW_REVISION_SNAPSHOT_SCHEMA_VERSION),
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(FlexibleJSON(), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        FlexibleUUID(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    flow: Mapped[Flow] = relationship(
        "Flow",
        back_populates="revisions",
        foreign_keys=[flow_id],
    )
    runs: Mapped[list[FlowRun]] = relationship(
        "FlowRun",
        back_populates="revision",
        foreign_keys="FlowRun.revision_id",
        lazy="select",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "flow_id",
            "revision_number",
            name="uq_flow_revisions_flow_revision_number",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_flow_revisions_revision_number_positive",
        ),
        Index("ix_flow_revisions_flow_created_at", "flow_id", "created_at"),
    )


@event.listens_for(FlowRevision, "before_update")
@event.listens_for(FlowRevision, "before_delete")
def _prevent_flow_revision_mutation(_mapper, _connection, revision: FlowRevision) -> None:
    raise RuntimeError(f"FlowRevision {revision.id} é imutável; crie uma nova revisão")


class FlowRun(Base):
    """
    Instância de execução de um Flow para um matter específico.
    """

    __tablename__ = "flow_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_flow_runs_workspace_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "application_idempotency_key_hash",
            name="uq_flow_runs_workspace_application_idempotency",
        ),
        Index("ix_flow_runs_workspace_task_started", "workspace_id", "task_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(FlexibleUUID(), primary_key=True, default=uuid.uuid4)
    flow_id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("flows.id", ondelete="CASCADE")
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey(
            "flow_revisions.id",
            name="fk_flow_runs_revision_id_flow_revisions",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    matter_id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("matters.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    application_idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Status: active | paused | completed | failed | waiting_human
    status: Mapped[str] = mapped_column(String(50), default="active")
    current_node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Variáveis do contexto: {nome, telefone, prazo, ...}
    context: Mapped[dict[str, Any] | None] = mapped_column(
        FlexibleJSON(), nullable=True, default=dict
    )
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Timestamps
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    flow: Mapped[Flow] = relationship(
        "Flow",
        back_populates="runs",
        foreign_keys=[flow_id],
    )
    revision: Mapped[FlowRevision] = relationship(
        "FlowRevision",
        back_populates="runs",
        foreign_keys=[revision_id],
    )
    scheduled_messages: Mapped[list[ScheduledMessage]] = relationship(
        "ScheduledMessage", back_populates="flow_run", lazy="select"
    )


class ScheduledMessage(Base):
    """
    Mensagem agendada para envio via WhatsApp em data/hora específica.
    """

    __tablename__ = "scheduled_messages"
    __table_args__ = (
        Index(
            "ix_scheduled_messages_processing_lease",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ux_scheduled_messages_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_scheduled_messages_flow_stage",
            "flow_run_id",
            "stage_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(FlexibleUUID(), primary_key=True, default=uuid.uuid4)
    flow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("flow_runs.id", ondelete="SET NULL"), nullable=True
    )
    # Sequence stage metadata is denormalized here deliberately: the flow
    # revision remains the source of truth, while a queued delivery keeps the
    # exact contract it was created with after a later flow edit.
    stage_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("whatsapp_meta_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    whatsapp_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("whatsapp_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    template_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    template_bindings: Mapped[list | None] = mapped_column(FlexibleJSON(), nullable=True)
    delay_value: Mapped[float | None] = mapped_column(nullable=True)
    delay_unit: Mapped[str | None] = mapped_column(String(12), nullable=True)
    send_now: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    interrupt_on_reply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    pending_grace_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pending_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tariff_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unverified", server_default="unverified"
    )
    tariff_amount: Mapped[float | None] = mapped_column(nullable=True)
    matter_id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("matters.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    contact_phone: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # pending | processing | sent | failed | delivery_uncertain | cancelled | skipped
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_supports_idempotency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_uncertain_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    flow_run: Mapped[FlowRun | None] = relationship("FlowRun", back_populates="scheduled_messages")
