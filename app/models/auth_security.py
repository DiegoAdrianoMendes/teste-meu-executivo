"""Persistência local para proteção de identidade, MFA e sessões administrativas."""
from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin


class AuthRateLimitScope(enum.StrEnum):
    login = "login"
    register = "register"
    verify_signup = "verify_signup"
    invitation = "invitation"
    accept_invitation = "accept_invitation"
    forgot_password = "forgot_password"
    reset_password = "reset_password"


class MfaChallengePurpose(enum.StrEnum):
    enrollment = "enrollment"
    login = "login"
    admin_session = "admin_session"


class MfaVerificationMethod(enum.StrEnum):
    challenge = "challenge"
    recovery = "recovery"


def _string_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class AuthRateLimitEvent(UUIDMixin, Base):
    __tablename__ = "auth_rate_limit_events"
    __table_args__ = (
        Index(
            "ix_auth_rate_limit_scope_identifier_occurred",
            "scope",
            "identifier_digest",
            "occurred_at",
        ),
        Index(
            "ix_auth_rate_limit_scope_network_occurred",
            "scope",
            "network_digest",
            "occurred_at",
        ),
    )

    scope: Mapped[AuthRateLimitScope] = mapped_column(
        _string_enum(AuthRateLimitScope, "auth_rate_limit_scope_enum"),
        nullable=False,
    )
    identifier_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    network_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    was_limited: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MfaCredential(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "mfa_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_mfa_credentials_user"),
    )

    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    recovery_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_totp_counter: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MfaChallenge(UUIDMixin, Base):
    __tablename__ = "mfa_challenges"
    __table_args__ = (
        CheckConstraint(
            "attempts_remaining >= 0",
            name="ck_mfa_challenges_attempts_nonnegative",
        ),
        Index(
            "ix_mfa_challenges_user_purpose_created",
            "user_id",
            "purpose",
            "created_at",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    purpose: Mapped[MfaChallengePurpose] = mapped_column(
        _string_enum(MfaChallengePurpose, "mfa_challenge_purpose_enum"),
        nullable=False,
    )
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    bound_token_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MfaRecoveryCode(UUIDMixin, Base):
    __tablename__ = "mfa_recovery_codes"
    __table_args__ = (
        UniqueConstraint("code_digest", name="uq_mfa_recovery_code_digest"),
        Index(
            "ix_mfa_recovery_user_generation",
            "user_id",
            "generation",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AdministrativeSession(UUIDMixin, Base):
    __tablename__ = "administrative_sessions"
    __table_args__ = (
        UniqueConstraint("token_jti", name="uq_administrative_session_token_jti"),
        Index(
            "ix_administrative_sessions_user_expires",
            "user_id",
            "expires_at",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    challenge_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("mfa_challenges.id", ondelete="SET NULL"),
        nullable=True,
    )
    token_jti: Mapped[str] = mapped_column(String(240), nullable=False)
    mfa_method: Mapped[MfaVerificationMethod] = mapped_column(
        _string_enum(MfaVerificationMethod, "mfa_verification_method_enum"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revocation_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AuthSecurityEvent(UUIDMixin, Base):
    __tablename__ = "auth_security_events"
    __table_args__ = (
        Index(
            "ix_auth_security_events_subject_occurred",
            "subject_user_id",
            "occurred_at",
        ),
        Index(
            "ix_auth_security_events_type_occurred",
            "event_type",
            "occurred_at",
        ),
    )

    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    subject_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    challenge_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("mfa_challenges.id", ondelete="SET NULL"),
        nullable=True,
    )
    administrative_session_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("administrative_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    network_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=dict,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
