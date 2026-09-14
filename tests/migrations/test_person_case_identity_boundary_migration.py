from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

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
    / "fa2e4c6a8b31_separate_person_identity_from_case_scope.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("person_case_identity_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_v6_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    clients = sa.Table(
        "clients",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
    )
    matters = sa.Table(
        "matters",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.ForeignKeyConstraint(["client_id"], [clients.c.id]),
    )
    identities = sa.Table(
        "klm_omnichannel_identities",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=True),
        sa.Column("matter_id", sa.String(36), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("canonical_phone", sa.String(32), nullable=True),
        sa.Column("canonical_email", sa.String(255), nullable=True),
        sa.Column("identity_state", sa.String(32), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("merge_version", sa.Integer(), nullable=False),
        sa.Column("identity_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            [clients.c.workspace_id, clients.c.id],
            name="fk_klm_identity_workspace_client",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            [matters.c.workspace_id, matters.c.id],
            name="fk_klm_identity_workspace_matter",
        ),
    )
    threads = sa.Table(
        "klm_omnichannel_threads",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("identity_id", sa.String(36), nullable=True),
        sa.Column("client_id", sa.String(36), nullable=True),
        sa.Column("matter_id", sa.String(36), nullable=True),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(["workspace_id"], [workspaces.c.id]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            [identities.c.workspace_id, identities.c.id],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            [matters.c.workspace_id, matters.c.id],
        ),
    )
    metadata.create_all(connection)
    sa.Index(
        "ix_klm_omnichannel_identities_matter_id", identities.c.matter_id
    ).create(connection)
    sa.Index(
        "uq_klm_thread_workspace_identity",
        threads.c.workspace_id,
        threads.c.identity_id,
        unique=True,
        sqlite_where=threads.c.identity_id.is_not(None),
    ).create(connection)


def test_person_case_identity_migration_preserves_data_and_scopes_threads(
    monkeypatch,
) -> None:
    migration = _load_migration_module()
    assert migration.revision == "fa2e4c6a8b31"
    assert migration.down_revision == "f9d1e3a5c720"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_v6_schema(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))

        workspace_id = str(uuid4())
        user_id = str(uuid4())
        client_id = str(uuid4())
        matter_a = str(uuid4())
        matter_b = str(uuid4())
        identity_id = str(uuid4())
        thread_a = str(uuid4())
        connection.execute(
            sa.text("INSERT INTO workspaces (id) VALUES (:id)"),
            {"id": workspace_id},
        )
        connection.execute(sa.text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id})
        connection.execute(
            sa.text("INSERT INTO clients (id, workspace_id) VALUES (:id, :workspace)"),
            {"id": client_id, "workspace": workspace_id},
        )
        for matter_id in (matter_a, matter_b):
            connection.execute(
                sa.text(
                    "INSERT INTO matters (id, workspace_id, client_id) "
                    "VALUES (:id, :workspace, :client)"
                ),
                {"id": matter_id, "workspace": workspace_id, "client": client_id},
            )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_identities "
                "(id, workspace_id, client_id, matter_id, display_name, identity_state, source, "
                "merge_version, identity_metadata, created_at, updated_at) VALUES "
                "(:id, :workspace, :client, :matter, 'Pessoa A', 'verified', 'test', 1, '{}', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": identity_id,
                "workspace": workspace_id,
                "client": client_id,
                "matter": matter_a,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_threads "
                "(id, workspace_id, identity_id, client_id, matter_id) "
                "VALUES (:id, :workspace, :identity, :client, NULL)"
            ),
            {
                "id": thread_a,
                "workspace": workspace_id,
                "identity": identity_id,
                "client": client_id,
            },
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert set(migration.TABLES) <= set(inspector.get_table_names())
        # SQLite keeps the retired nullable column because rebuilding a referenced
        # table with FK enforcement enabled is unsafe. PostgreSQL drops it.
        assert "ix_klm_omnichannel_identities_matter_id" not in {
            index["name"]
            for index in inspector.get_indexes("klm_omnichannel_identities")
        }
        assert connection.execute(
            sa.text("SELECT matter_id FROM klm_omnichannel_threads WHERE id = :id"),
            {"id": thread_a},
        ).scalar_one() == matter_a

        thread_b = str(uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_threads "
                "(id, workspace_id, identity_id, client_id, matter_id) "
                "VALUES (:id, :workspace, :identity, :client, :matter)"
            ),
            {
                "id": thread_b,
                "workspace": workspace_id,
                "identity": identity_id,
                "client": client_id,
                "matter": matter_b,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_identity_reviews "
                "(id, workspace_id, identity_id, thread_id, matter_id, created_by_user_id, "
                "review_type, reason_code, idempotency_key) VALUES "
                "(:id, :workspace, :identity, :thread, :matter, :user, "
                "'possible_duplicate_matter', 'test', 'review:test:1')"
            ),
            {
                "id": str(uuid4()),
                "workspace": workspace_id,
                "identity": identity_id,
                "thread": thread_b,
                "matter": matter_b,
                "user": user_id,
            },
        )
        unscoped = {
            "workspace": workspace_id,
            "identity": identity_id,
            "client": client_id,
        }
        connection.execute(
            sa.text(
                "INSERT INTO klm_omnichannel_threads "
                "(id, workspace_id, identity_id, client_id, matter_id) "
                "VALUES (:id, :workspace, :identity, :client, NULL)"
            ),
            {"id": str(uuid4()), **unscoped},
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO klm_omnichannel_threads "
                        "(id, workspace_id, identity_id, client_id, matter_id) "
                        "VALUES (:id, :workspace, :identity, :client, NULL)"
                    ),
                    {"id": str(uuid4()), **unscoped},
                )

        with pytest.raises(RuntimeError, match="múltiplas conversas"):
            migration.downgrade()
