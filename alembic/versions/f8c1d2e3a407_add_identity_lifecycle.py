"""Add the complete local identity lifecycle.

Revision ID: f8c1d2e3a407
Revises: e7a9b1c3d206
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "f8c1d2e3a407"
down_revision: str | Sequence[str] | None = "e7a9b1c3d206"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTH_RATE_LIMIT_SCOPES = (
    "login",
    "register",
    "verify_signup",
    "invitation",
    "accept_invitation",
    "forgot_password",
    "reset_password",
)
LEGACY_AUTH_RATE_LIMIT_SCOPES = (
    "login",
    "forgot_password",
    "reset_password",
)

auth_rate_limit_bucket_scope_enum = sa.Enum(
    *AUTH_RATE_LIMIT_SCOPES,
    name="auth_rate_limit_bucket_scope_enum",
    native_enum=False,
    create_constraint=True,
)
identity_outbox_purpose_enum = sa.Enum(
    "signup_verification",
    "workspace_invitation",
    "password_reset",
    name="identity_outbox_purpose_enum",
    native_enum=False,
    create_constraint=True,
)
workspace_invitation_status_enum = sa.Enum(
    "pending",
    "accepted",
    "revoked",
    "expired",
    name="workspace_invitation_status_enum",
    native_enum=False,
    create_constraint=True,
)


def _scope_check(values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"scope IN ({quoted})"


def _replace_event_scope_constraint(values: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("auth_rate_limit_events") as batch_op:
            batch_op.drop_constraint("auth_rate_limit_scope_enum", type_="check")
            batch_op.create_check_constraint(
                "auth_rate_limit_scope_enum",
                _scope_check(values),
            )
        return
    op.drop_constraint(
        "auth_rate_limit_scope_enum",
        "auth_rate_limit_events",
        type_="check",
    )
    op.create_check_constraint(
        "auth_rate_limit_scope_enum",
        "auth_rate_limit_events",
        _scope_check(values),
    )


def upgrade() -> None:
    _replace_event_scope_constraint(AUTH_RATE_LIMIT_SCOPES)

    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE users "
            "SET email_verified_at = COALESCE(created_at, CURRENT_TIMESTAMP) "
            "WHERE agent_type = 'human' "
            "AND email IS NOT NULL "
            "AND hashed_password IS NOT NULL"
        )
    )

    op.create_table(
        "identity_idempotency_records",
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("response_data", FlexibleJSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation",
            "key_digest",
            name="uq_identity_idempotency_operation_key",
        ),
    )
    op.create_index(
        "ix_identity_idempotency_expires",
        "identity_idempotency_records",
        ["expires_at"],
    )

    op.create_table(
        "identity_outbox_messages",
        sa.Column("purpose", identity_outbox_purpose_enum, nullable=False),
        sa.Column("recipient_digest", sa.String(length=64), nullable=False),
        sa.Column("recipient_hint", sa.String(length=255), nullable=False),
        sa.Column("payload_ciphertext", sa.Text(), nullable=False),
        sa.Column("related_type", sa.String(length=80), nullable=False),
        sa.Column("related_id", FlexibleUUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_outbox_expires",
        "identity_outbox_messages",
        ["expires_at"],
    )
    op.create_index(
        "ix_identity_outbox_related",
        "identity_outbox_messages",
        ["related_type", "related_id"],
    )

    op.create_table(
        "signup_verifications",
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("plan_tier", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivery_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["last_delivery_id"],
            ["identity_outbox_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_signup_verification_token"),
    )
    op.create_index(
        "ix_signup_verification_workspace",
        "signup_verifications",
        ["workspace_id"],
    )
    op.create_index(
        "uq_signup_verification_active_user",
        "signup_verifications",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND revoked_at IS NULL"),
        sqlite_where=sa.text("consumed_at IS NULL AND revoked_at IS NULL"),
    )

    op.create_table(
        "workspace_invitations",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("membership_id", FlexibleUUID(), nullable=False),
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("invited_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("invited_email", sa.String(length=255), nullable=False),
        sa.Column("invited_name", sa.String(length=200), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("status", workspace_invitation_status_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_count", sa.Integer(), nullable=False),
        sa.Column("last_delivery_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["workspace_memberships.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_delivery_id"],
            ["identity_outbox_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_workspace_invitation_token"),
    )
    op.create_index(
        "ix_workspace_invitation_membership",
        "workspace_invitations",
        ["membership_id"],
    )
    op.create_index(
        "uq_workspace_invitation_active_email",
        "workspace_invitations",
        ["workspace_id", "invited_email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "user_sessions",
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("refresh_jti_digest", sa.String(length=64), nullable=False),
        sa.Column("device_label", sa.String(length=160), nullable=False),
        sa.Column("device_type", sa.String(length=40), nullable=False),
        sa.Column("browser", sa.String(length=80), nullable=False),
        sa.Column("operating_system", sa.String(length=80), nullable=False),
        sa.Column("user_agent_digest", sa.String(length=64), nullable=True),
        sa.Column("network_digest", sa.String(length=64), nullable=True),
        sa.Column("last_workspace_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=120), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["last_workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_jti_digest", name="uq_user_session_refresh_jti"),
    )
    op.create_index(
        "ix_user_sessions_user_expires",
        "user_sessions",
        ["user_id", "expires_at"],
    )
    op.create_index(
        "ix_user_sessions_user_last_seen",
        "user_sessions",
        ["user_id", "last_seen_at"],
    )

    op.create_table(
        "auth_rate_limit_buckets",
        sa.Column("scope", auth_rate_limit_bucket_scope_enum, nullable=False),
        sa.Column("key_type", sa.String(length=20), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("scope", "key_type", "key_digest"),
    )
    op.create_index(
        "ix_auth_rate_limit_buckets_updated",
        "auth_rate_limit_buckets",
        ["updated_at"],
    )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("password_reset_tokens") as batch_op:
            batch_op.add_column(sa.Column("code_hash", sa.String(length=64), nullable=True))
            batch_op.add_column(sa.Column("last_delivery_id", FlexibleUUID(), nullable=True))
            batch_op.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.create_foreign_key(
                "fk_password_reset_tokens_last_delivery_id_identity_outbox",
                "identity_outbox_messages",
                ["last_delivery_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column(
            "password_reset_tokens",
            sa.Column("code_hash", sa.String(length=64), nullable=True),
        )
        op.add_column(
            "password_reset_tokens",
            sa.Column("last_delivery_id", FlexibleUUID(), nullable=True),
        )
        op.add_column(
            "password_reset_tokens",
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_password_reset_tokens_last_delivery_id_identity_outbox",
            "password_reset_tokens",
            "identity_outbox_messages",
            ["last_delivery_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "uq_password_reset_active_user",
        "password_reset_tokens",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
        sqlite_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_password_reset_active_user", table_name="password_reset_tokens")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("password_reset_tokens") as batch_op:
            batch_op.drop_constraint(
                "fk_password_reset_tokens_last_delivery_id_identity_outbox",
                type_="foreignkey",
            )
            batch_op.drop_column("revoked_at")
            batch_op.drop_column("last_delivery_id")
            batch_op.drop_column("code_hash")
    else:
        op.drop_constraint(
            "fk_password_reset_tokens_last_delivery_id_identity_outbox",
            "password_reset_tokens",
            type_="foreignkey",
        )
        op.drop_column("password_reset_tokens", "revoked_at")
        op.drop_column("password_reset_tokens", "last_delivery_id")
        op.drop_column("password_reset_tokens", "code_hash")

    op.drop_table("auth_rate_limit_buckets")
    op.drop_table("user_sessions")
    op.drop_table("workspace_invitations")
    op.drop_table("signup_verifications")
    op.drop_table("identity_outbox_messages")
    op.drop_table("identity_idempotency_records")

    op.drop_column("users", "email_verified_at")
    op.execute(
        sa.text(
            "DELETE FROM auth_rate_limit_events "
            "WHERE scope IN ('register', 'verify_signup', 'invitation', 'accept_invitation')"
        )
    )
    _replace_event_scope_constraint(LEGACY_AUTH_RATE_LIMIT_SCOPES)
