from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.auth_security import AuthSecurityEvent, MfaChallenge

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
    / "d6f8a0c2e105_add_local_auth_hardening.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("auth_hardening_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_tables(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    metadata.create_all(connection)


def test_auth_hardening_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "d6f8a0c2e105"
    assert migration.down_revision == "d5f7a9c1e004"

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
        expected_tables = {
            "administrative_sessions",
            "auth_rate_limit_events",
            "auth_security_events",
            "mfa_challenges",
            "mfa_credentials",
            "mfa_recovery_codes",
        }
        assert expected_tables <= set(inspector.get_table_names())
        rate_columns = {
            column["name"]
            for column in inspector.get_columns("auth_rate_limit_events")
        }
        assert "identifier_digest" in rate_columns
        assert "network_digest" in rate_columns
        assert "email" not in rate_columns
        assert {
            index["name"]
            for index in inspector.get_indexes("administrative_sessions")
        } == {"ix_administrative_sessions_user_expires"}

        migration.downgrade()
        assert expected_tables.isdisjoint(sa.inspect(connection).get_table_names())


def test_auth_hardening_models_compile_for_postgresql() -> None:
    challenge_ddl = str(
        sa.schema.CreateTable(MfaChallenge.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    audit_ddl = str(
        sa.schema.CreateTable(AuthSecurityEvent.__table__).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "UUID" in challenge_ddl
    assert "mfa_challenge_purpose_enum" in challenge_ddl
    assert "JSONB" in audit_ddl
    assert "network_digest" in audit_ddl
