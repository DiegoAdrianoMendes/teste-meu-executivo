"""Add persistent local auth hardening, simulated MFA and admin sessions.

Revision ID: d6f8a0c2e105
Revises: d5f7a9c1e004
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "d6f8a0c2e105"
down_revision: str | Sequence[str] | None = "d5f7a9c1e004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

rate_limit_scope_enum = sa.Enum(
    "login",
    "forgot_password",
    "reset_password",
    name="auth_rate_limit_scope_enum",
    native_enum=False,
    create_constraint=True,
)
mfa_challenge_purpose_enum = sa.Enum(
    "enrollment",
    "login",
    "admin_session",
    name="mfa_challenge_purpose_enum",
    native_enum=False,
    create_constraint=True,
)
mfa_verification_method_enum = sa.Enum(
    "challenge",
    "recovery",
    name="mfa_verification_method_enum",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "auth_rate_limit_events",
        sa.Column("scope", rate_limit_scope_enum, nullable=False),
        sa.Column("identifier_digest", sa.String(length=64), nullable=False),
        sa.Column("network_digest", sa.String(length=64), nullable=False),
        sa.Column("was_limited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_rate_limit_scope_identifier_occurred",
        "auth_rate_limit_events",
        ["scope", "identifier_digest", "occurred_at"],
    )
    op.create_index(
        "ix_auth_rate_limit_scope_network_occurred",
        "auth_rate_limit_events",
        ["scope", "network_digest", "occurred_at"],
    )

    op.create_table(
        "mfa_credentials",
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_mfa_credentials_user"),
    )

    op.create_table(
        "mfa_challenges",
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=True),
        sa.Column("purpose", mfa_challenge_purpose_enum, nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("bound_token_digest", sa.String(length=64), nullable=True),
        sa.Column("attempts_remaining", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "attempts_remaining >= 0",
            name="ck_mfa_challenges_attempts_nonnegative",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mfa_challenges_user_purpose_created",
        "mfa_challenges",
        ["user_id", "purpose", "created_at"],
    )

    op.create_table(
        "mfa_recovery_codes",
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_digest", name="uq_mfa_recovery_code_digest"),
    )
    op.create_index(
        "ix_mfa_recovery_user_generation",
        "mfa_recovery_codes",
        ["user_id", "generation"],
    )

    op.create_table(
        "administrative_sessions",
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("challenge_id", FlexibleUUID(), nullable=True),
        sa.Column("token_jti", sa.String(length=240), nullable=False),
        sa.Column("mfa_method", mfa_verification_method_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("revocation_reason", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["mfa_challenges.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_jti",
            name="uq_administrative_session_token_jti",
        ),
    )
    op.create_index(
        "ix_administrative_sessions_user_expires",
        "administrative_sessions",
        ["user_id", "expires_at"],
    )

    op.create_table(
        "auth_security_events",
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("subject_user_id", FlexibleUUID(), nullable=True),
        sa.Column("challenge_id", FlexibleUUID(), nullable=True),
        sa.Column("administrative_session_id", FlexibleUUID(), nullable=True),
        sa.Column("network_digest", sa.String(length=64), nullable=True),
        sa.Column("user_agent_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "details",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["mfa_challenges.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["administrative_session_id"],
            ["administrative_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_security_events_subject_occurred",
        "auth_security_events",
        ["subject_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_auth_security_events_type_occurred",
        "auth_security_events",
        ["event_type", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("auth_security_events")
    op.drop_table("administrative_sessions")
    op.drop_table("mfa_recovery_codes")
    op.drop_table("mfa_challenges")
    op.drop_table("mfa_credentials")
    op.drop_table("auth_rate_limit_events")
