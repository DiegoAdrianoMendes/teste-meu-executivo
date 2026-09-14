"""Persistencia do lifecycle local de identidades humanas."""
from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, UUIDMixin
from app.models.auth_security import AuthRateLimitScope


def _string_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class IdentityOutboxPurpose(enum.StrEnum):
    signup_verification = "signup_verification"
    workspace_invitation = "workspace_invitation"
    password_reset = "password_reset"


class IdentityDeliveryStatus(enum.StrEnum):
    pending = "pending"
    sending = "sending"
    sent = "sent"
    failed = "failed"
    blocked = "blocked"


class InvitationStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"
    expired = "expired"


class AuthRevocationIntentStatus(enum.StrEnum):
    pending = "pending"
    completed = "completed"


class IdentityIdempotencyRecord(UUIDMixin, Base):
    __tablename__ = "identity_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "operation",
            "key_digest",
            name="uq_identity_idempotency_operation_key",
        ),
        Index("ix_identity_idempotency_expires", "expires_at"),
    )

    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_data: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityOutboxMessage(UUIDMixin, Base):
    __tablename__ = "identity_outbox_messages"
    __table_args__ = (
        Index("ix_identity_outbox_expires", "expires_at"),
        Index("ix_identity_outbox_related", "related_type", "related_id"),
        Index("ix_identity_outbox_request", "request_id"),
        Index("ix_identity_outbox_delivery", "delivery_status", "last_attempt_at"),
    )

    purpose: Mapped[IdentityOutboxPurpose] = mapped_column(
        _string_enum(IdentityOutboxPurpose, "identity_outbox_purpose_enum"),
        nullable=False,
    )
    recipient_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_hint: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    related_type: Mapped[str] = mapped_column(String(80), nullable=False)
    related_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_status: Mapped[IdentityDeliveryStatus] = mapped_column(
        _string_enum(IdentityDeliveryStatus, "identity_delivery_status_enum"),
        nullable=False,
        default=IdentityDeliveryStatus.pending,
        server_default=IdentityDeliveryStatus.pending.value,
    )
    delivery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_message_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_delivery_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SignupVerification(UUIDMixin, Base):
    __tablename__ = "signup_verifications"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_signup_verification_token"),
        Index(
            "uq_signup_verification_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_signup_verification_workspace", "workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_signup_verification_workspace_user_membership",
            ondelete="CASCADE",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_delivery_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("identity_outbox_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class WorkspaceInvitation(UUIDMixin, Base):
    __tablename__ = "workspace_invitations"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_workspace_invitation_token"),
        Index(
            "uq_workspace_invitation_active_email",
            "workspace_id",
            "invited_email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_workspace_invitation_membership", "membership_id"),
        CheckConstraint(
            "failed_attempt_count >= 0",
            name="ck_workspace_invitation_failed_attempt_count_nonnegative",
        ),
        ForeignKeyConstraint(
            ["membership_id", "workspace_id", "user_id"],
            [
                "workspace_memberships.id",
                "workspace_memberships.workspace_id",
                "workspace_memberships.user_id",
            ],
            name="fk_workspace_invitation_membership_identity_binding",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    membership_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspace_memberships.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    invited_by_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    invited_email: Mapped[str] = mapped_column(String(255), nullable=False)
    invited_name: Mapped[str] = mapped_column(String(200), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        _string_enum(InvitationStatus, "workspace_invitation_status_enum"),
        nullable=False,
        default=InvitationStatus.pending,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    send_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failed_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_delivery_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("identity_outbox_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class UserSession(UUIDMixin, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("refresh_jti_digest", name="uq_user_session_refresh_jti"),
        Index("ix_user_sessions_user_last_seen", "user_id", "last_seen_at"),
        Index("ix_user_sessions_user_expires", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    refresh_jti_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    device_label: Mapped[str] = mapped_column(String(160), nullable=False)
    device_type: Mapped[str] = mapped_column(String(40), nullable=False)
    browser: Mapped[str] = mapped_column(String(80), nullable=False)
    operating_system: Mapped[str] = mapped_column(String(80), nullable=False)
    user_agent_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    network_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_workspace_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)


class AuthRateLimitBucket(Base):
    __tablename__ = "auth_rate_limit_buckets"
    __table_args__ = (
        Index("ix_auth_rate_limit_buckets_updated", "updated_at"),
    )

    scope: Mapped[AuthRateLimitScope] = mapped_column(
        _string_enum(AuthRateLimitScope, "auth_rate_limit_bucket_scope_enum"),
        primary_key=True,
    )
    key_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AuthRevocationIntent(UUIDMixin, Base):
    """Durable source for retrying best-effort cache and WebSocket fanout."""

    __tablename__ = "auth_revocation_intents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "auth_epoch",
            name="uq_auth_revocation_intent_user_epoch",
        ),
        Index("ix_auth_revocation_intent_retry", "status", "next_attempt_at"),
        CheckConstraint("attempt_count >= 0", name="ck_auth_revocation_attempt_nonnegative"),
    )

    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    auth_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[AuthRevocationIntentStatus] = mapped_column(
        _string_enum(AuthRevocationIntentStatus, "auth_revocation_intent_status_enum"),
        nullable=False,
        default=AuthRevocationIntentStatus.pending,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
