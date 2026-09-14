"""Trilha canônica e imutável das operações administrativas da plataforma."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, event, func
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, UUIDMixin


class PlatformAuditEvent(UUIDMixin, Base):
    __tablename__ = "platform_audit_events"
    __table_args__ = (
        Index("ix_platform_audit_occurred_id", "occurred_at", "id"),
        Index("ix_platform_audit_actor_occurred", "actor_id", "occurred_at"),
        Index("ix_platform_audit_workspace_occurred", "workspace_id", "occurred_at"),
        Index("ix_platform_audit_resource", "resource_type", "resource_id"),
        Index("ix_platform_audit_correlation", "correlation_id"),
        Index("ix_platform_audit_operation", "operation_id"),
    )

    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    actor_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_role_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    is_platform_root: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subject_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    workspace_label_snapshot: Mapped[str | None] = mapped_column(String(240), nullable=True)
    administrative_session_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("administrative_sessions.id", ondelete="SET NULL"), nullable=True
    )
    session_type: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    module: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resource_label_snapshot: Mapped[str | None] = mapped_column(String(240), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    before: Mapped[dict | list | None] = mapped_column(FlexibleJSON(), nullable=True)
    after: Mapped[dict | list | None] = mapped_column(FlexibleJSON(), nullable=True)
    changed_paths: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reason_source: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_event_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_audit_events.id", ondelete="RESTRICT"), nullable=True
    )
    bulk_job_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    restoration_of_event_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("platform_audit_events.id", ondelete="RESTRICT"), nullable=True
    )
    resource_version_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource_version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    route: Mapped[str] = mapped_column(String(320), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _immutable(_: Mapper, __: object, ___: PlatformAuditEvent) -> None:
    raise ValueError("PlatformAuditEvent é append-only e não pode ser alterado ou removido.")


event.listen(PlatformAuditEvent, "before_update", _immutable)
event.listen(PlatformAuditEvent, "before_delete", _immutable)
