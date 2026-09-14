from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.identity import (
    AuthRateLimitBucket,
    IdentityIdempotencyRecord,
    IdentityOutboxMessage,
    SignupVerification,
    UserSession,
    WorkspaceInvitation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "f8c1d2e3a407_add_identity_lifecycle.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("identity_lifecycle_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_tables(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("hashed_password", sa.Text(), nullable=True),
        sa.Column("agent_type", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    workspaces = sa.Table(
        "workspaces",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "workspace_memberships",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.ForeignKey(workspaces.c.id), nullable=False),
        sa.Column("user_id", sa.ForeignKey(users.c.id), nullable=False),
    )
    sa.Table(
        "auth_rate_limit_events",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.CheckConstraint(
            "scope IN ('login', 'forgot_password', 'reset_password')",
            name="auth_rate_limit_scope_enum",
        ),
    )
    sa.Table(
        "password_reset_tokens",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.ForeignKey(users.c.id, ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    metadata.create_all(connection)


def test_identity_lifecycle_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "f8c1d2e3a407"
    assert migration.down_revision == "e7a9b1c3d206"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_tables(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        inspector = sa.inspect(connection)
        identity_tables = {
            "auth_rate_limit_buckets",
            "identity_idempotency_records",
            "identity_outbox_messages",
            "signup_verifications",
            "user_sessions",
            "workspace_invitations",
        }
        assert identity_tables <= set(inspector.get_table_names())
        assert "email_verified_at" in {
            column["name"] for column in inspector.get_columns("users")
        }
        reset_columns = {
            column["name"] for column in inspector.get_columns("password_reset_tokens")
        }
        assert {"code_hash", "last_delivery_id", "revoked_at"} <= reset_columns
        assert "uq_password_reset_active_user" in {
            index["name"] for index in inspector.get_indexes("password_reset_tokens")
        }
        assert "token" not in {
            column["name"] for column in inspector.get_columns("user_sessions")
        }

        connection.execute(
            sa.text(
                "INSERT INTO auth_rate_limit_events (id, scope) "
                "VALUES ('event-1', 'register')"
            )
        )

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert identity_tables.isdisjoint(inspector.get_table_names())
        assert "email_verified_at" not in {
            column["name"] for column in inspector.get_columns("users")
        }


def test_identity_lifecycle_models_compile_for_postgresql() -> None:
    tables = (
        AuthRateLimitBucket,
        IdentityIdempotencyRecord,
        IdentityOutboxMessage,
        SignupVerification,
        UserSession,
        WorkspaceInvitation,
    )
    ddl = "\n".join(
        str(sa.schema.CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        for model in tables
    )

    assert "UUID" in ddl
    assert "JSONB" in ddl
    assert "auth_rate_limit_buckets" in ddl
    assert "refresh_jti_digest" in ddl
    assert "payload_ciphertext" in ddl
    assert "refresh_token" not in ddl
