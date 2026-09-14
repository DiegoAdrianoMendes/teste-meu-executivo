from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

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
    / "c8f5d2a9b714_consolidate_identity_security.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("identity_security_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_parent_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=True),
    )
    workspaces = sa.Table(
        "workspaces",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    memberships = sa.Table(
        "workspace_memberships",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.ForeignKey(workspaces.c.id), nullable=False),
        sa.Column("user_id", sa.ForeignKey(users.c.id), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_membership_workspace_user",
        ),
    )
    outbox = sa.Table(
        "identity_outbox_messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("related_type", sa.String(80), nullable=False),
        sa.Column("related_id", sa.String(36), nullable=True),
    )
    sa.Table(
        "signup_verifications",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.ForeignKey(users.c.id), nullable=False),
        sa.Column("workspace_id", sa.ForeignKey(workspaces.c.id), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("last_delivery_id", sa.ForeignKey(outbox.c.id), nullable=True),
    )
    sa.Table(
        "workspace_invitations",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.ForeignKey(workspaces.c.id), nullable=False),
        sa.Column("membership_id", sa.ForeignKey(memberships.c.id), nullable=False),
        sa.Column("user_id", sa.ForeignKey(users.c.id), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
    )
    metadata.create_all(connection)


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection))


def _seed_consistent_identity(connection: sa.Connection) -> None:
    connection.execute(
        sa.text("INSERT INTO users (id, email) VALUES ('user-1', 'owner@example.com')")
    )
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-1')"))
    connection.execute(
        sa.text(
            "INSERT INTO workspace_memberships (id, workspace_id, user_id) "
            "VALUES ('membership-1', 'workspace-1', 'user-1')"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO signup_verifications "
            "(id, user_id, workspace_id, token_digest) "
            "VALUES ('verification-1', 'user-1', 'workspace-1', 'signup-token')"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO workspace_invitations "
            "(id, workspace_id, membership_id, user_id, token_digest) "
            "VALUES ("
            "'invitation-1', 'workspace-1', 'membership-1', 'user-1', 'invite-token'"
            ")"
        )
    )


def test_identity_security_migration_round_trip(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "c8f5d2a9b714"
    assert migration.down_revision == "b7e4c1d9a603"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_schema(connection)
        _seed_consistent_identity(connection)
        monkeypatch.setattr(migration, "op", _operations(connection))

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "auth_revocation_intents" in inspector.get_table_names()
        assert "auth_epoch" in {column["name"] for column in inspector.get_columns("users")}
        assert "request_id" in {
            column["name"]
            for column in inspector.get_columns("identity_outbox_messages")
        }
        assert {"failed_attempt_count", "locked_at"} <= {
            column["name"]
            for column in inspector.get_columns("workspace_invitations")
        }
        invitation_foreign_keys = inspector.get_foreign_keys("workspace_invitations")
        assert any(
            foreign_key["constrained_columns"]
            == ["membership_id", "workspace_id", "user_id"]
            for foreign_key in invitation_foreign_keys
        )
        signup_foreign_keys = inspector.get_foreign_keys("signup_verifications")
        assert any(
            foreign_key["constrained_columns"] == ["workspace_id", "user_id"]
            for foreign_key in signup_foreign_keys
        )
        lower_email_index = connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'index' AND name = 'uq_users_email_lower'"
            )
        )
        assert lower_email_index == 1
        assert any(
            constraint["name"] == "ck_users_email_canonical_ascii_lowercase"
            for constraint in inspector.get_check_constraints("users")
        )

        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO users (id, email, auth_epoch) "
                    "VALUES ('user-uppercase', 'Owner2@example.com', 0)"
                )
            )

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "auth_revocation_intents" not in inspector.get_table_names()
        assert "auth_epoch" not in {
            column["name"] for column in inspector.get_columns("users")
        }
        assert all(
            constraint["name"] != "ck_users_email_canonical_ascii_lowercase"
            for constraint in inspector.get_check_constraints("users")
        )
        assert "request_id" not in {
            column["name"]
            for column in inspector.get_columns("identity_outbox_messages")
        }
        assert {"failed_attempt_count", "locked_at"}.isdisjoint(
            column["name"]
            for column in inspector.get_columns("workspace_invitations")
        )


def test_identity_security_upgrade_rejects_casefold_duplicates(monkeypatch) -> None:
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_schema(connection)
        connection.execute(
            sa.text(
                "INSERT INTO users (id, email) VALUES "
                "('user-1', 'Owner@Example.com'), "
                "('user-2', 'owner@example.com')"
            )
        )
        monkeypatch.setattr(migration, "op", _operations(connection))

        with pytest.raises(RuntimeError, match="users.email_casefold=1"):
            migration.upgrade()


@pytest.mark.parametrize("email", ["Owner@example.com", "usér@example.com", " user@example.com"])
def test_identity_security_upgrade_rejects_noncanonical_email(
    monkeypatch,
    email: str,
) -> None:
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_schema(connection)
        connection.execute(
            sa.text("INSERT INTO users (id, email) VALUES ('user-1', :email)"),
            {"email": email},
        )
        monkeypatch.setattr(migration, "op", _operations(connection))

        with pytest.raises(RuntimeError, match="users.email_canonical_ascii=1"):
            migration.upgrade()


def test_identity_security_downgrade_preserves_revocation_state(monkeypatch) -> None:
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_parent_schema(connection)
        _seed_consistent_identity(connection)
        monkeypatch.setattr(migration, "op", _operations(connection))
        migration.upgrade()
        connection.execute(sa.text("UPDATE users SET auth_epoch = 1 WHERE id = 'user-1'"))

        with pytest.raises(RuntimeError, match="users.auth_epoch=1"):
            migration.downgrade()
