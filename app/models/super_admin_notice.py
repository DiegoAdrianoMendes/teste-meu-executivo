"""Persistencia da Central de Avisos administrada pela plataforma."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


def _string_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class SuperAdminNoticeStatus(enum.StrEnum):
    draft = "draft"
    scheduled = "scheduled"
    published = "published"
    cancelled = "cancelled"


class SuperAdminNoticeAudienceKind(enum.StrEnum):
    global_ = "global"
    workspaces = "workspaces"
    users = "users"
    rule = "rule"


class SuperAdminNoticeChannel(enum.StrEnum):
    in_app = "in_app"
    email = "email"


class SuperAdminNoticeReceiptState(enum.StrEnum):
    in_app_available = "in_app_available"
    email_queued_local = "email_queued_local"
    email_blocked_missing_address = "email_blocked_missing_address"
    cancelled = "cancelled"


class SuperAdminNoticeAuditAction(enum.StrEnum):
    created = "created"
    updated = "updated"
    recipient_previewed = "recipient_previewed"
    scheduled = "scheduled"
    published = "published"
    cancelled = "cancelled"
    read = "read"
    acknowledged = "acknowledged"


class SuperAdminNotice(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "super_admin_notices"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_super_admin_notices_version_positive"),
        Index("ix_super_admin_notices_status_schedule", "status", "scheduled_at"),
    )

    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SuperAdminNoticeStatus] = mapped_column(
        _string_enum(SuperAdminNoticeStatus, "super_admin_notice_status_enum"),
        nullable=False,
        default=SuperAdminNoticeStatus.draft,
    )
    audience_kind: Mapped[SuperAdminNoticeAudienceKind] = mapped_column(
        _string_enum(
            SuperAdminNoticeAudienceKind,
            "super_admin_notice_audience_kind_enum",
        ),
        nullable=False,
    )
    audience_definition: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    channels: Mapped[list[str]] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=list,
    )
    requires_acknowledgement: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )


class SuperAdminNoticeReceipt(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "super_admin_notice_receipts"
    __table_args__ = (
        CheckConstraint(
            "external_delivery_attempted = false",
            name="ck_super_admin_notice_receipts_local_delivery_only",
        ),
        UniqueConstraint(
            "notice_id",
            "recipient_user_id",
            "channel",
            name="uq_super_admin_notice_receipt_recipient_channel",
        ),
        Index(
            "ix_super_admin_notice_receipts_user_channel_created",
            "recipient_user_id",
            "channel",
            "created_at",
        ),
        Index(
            "ix_super_admin_notice_receipts_notice_state",
            "notice_id",
            "state",
        ),
    )

    notice_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("super_admin_notices.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel: Mapped[SuperAdminNoticeChannel] = mapped_column(
        _string_enum(SuperAdminNoticeChannel, "super_admin_notice_channel_enum"),
        nullable=False,
    )
    state: Mapped[SuperAdminNoticeReceiptState] = mapped_column(
        _string_enum(
            SuperAdminNoticeReceiptState,
            "super_admin_notice_receipt_state_enum",
        ),
        nullable=False,
    )
    recipient_address_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_address_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_by: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_all_workspaces: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    external_delivery_attempted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class SuperAdminNoticeReceiptWorkspace(Base):
    __tablename__ = "super_admin_notice_receipt_workspaces"
    __table_args__ = (
        PrimaryKeyConstraint(
            "receipt_id",
            "workspace_id",
            name="pk_super_admin_notice_receipt_workspaces",
        ),
        Index(
            "ix_super_admin_notice_receipt_workspaces_scope",
            "workspace_id",
            "receipt_id",
        ),
    )

    receipt_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("super_admin_notice_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )


class SuperAdminNoticeAuditEvent(UUIDMixin, Base):
    __tablename__ = "super_admin_notice_audit_events"
    __table_args__ = (
        Index(
            "ix_super_admin_notice_audit_notice_occurred",
            "notice_id",
            "occurred_at",
        ),
        Index(
            "ix_super_admin_notice_audit_actor_occurred",
            "actor_user_id",
            "occurred_at",
        ),
    )

    notice_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("super_admin_notices.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[SuperAdminNoticeAuditAction] = mapped_column(
        _string_enum(
            SuperAdminNoticeAuditAction,
            "super_admin_notice_audit_action_enum",
        ),
        nullable=False,
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class SuperAdminNoticeIdempotencyRecord(UUIDMixin, Base):
    __tablename__ = "super_admin_notice_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id",
            "operation",
            "key_digest",
            name="uq_super_admin_notice_idempotency_actor_operation_key",
        ),
        Index("ix_super_admin_notice_idempotency_expires", "expires_at"),
    )

    actor_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_data: Mapped[dict[str, Any]] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


register_append_only_model(SuperAdminNoticeAuditEvent)
