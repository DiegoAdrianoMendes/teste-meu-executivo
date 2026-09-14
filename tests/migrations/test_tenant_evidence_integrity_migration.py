from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.klm_operations import legal_artifact_evidence_sha256

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
    / "d4f6a8c0e215_enforce_tenant_evidence_integrity.py"
)


def _load_migration_module():
    original_path = sys.path[:]
    sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
    try:
        spec = importlib.util.spec_from_file_location(
            "tenant_evidence_integrity_migration",
            MIGRATION_PATH,
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path = original_path


def _legacy_table(
    metadata: sa.MetaData,
    name: str,
    *columns: sa.Column,
    foreign_keys: tuple[sa.ForeignKeyConstraint, ...] = (),
    uniques: tuple[sa.UniqueConstraint, ...] = (),
) -> sa.Table:
    return sa.Table(
        name,
        metadata,
        *columns,
        sa.Column("id", sa.String(36), primary_key=True),
        *foreign_keys,
        *uniques,
    )


def _simple_tenant_child(
    metadata: sa.MetaData,
    name: str,
    references: tuple[tuple[str, str, bool], ...],
) -> sa.Table:
    columns = [sa.Column("workspace_id", sa.String(36), nullable=False)]
    foreign_keys = []
    for child_column, parent_table, nullable in references:
        columns.append(sa.Column(child_column, sa.String(36), nullable=nullable))
        foreign_keys.append(
            sa.ForeignKeyConstraint(
                [child_column],
                [f"{parent_table}.id"],
                name=f"fk_legacy_{name}_{child_column}",
                ondelete=("SET NULL" if nullable else "CASCADE"),
            )
        )
    return _legacy_table(
        metadata,
        name,
        *columns,
        foreign_keys=tuple(foreign_keys),
    )


def _create_legacy_schema(connection: sa.Connection) -> dict[str, sa.Table]:
    metadata = sa.MetaData()
    tables: dict[str, sa.Table] = {}
    tables["workspaces"] = _legacy_table(metadata, "workspaces")
    tables["clients"] = _legacy_table(
        metadata,
        "clients",
        sa.Column("workspace_id", sa.String(36), nullable=False),
        uniques=(
            sa.UniqueConstraint("workspace_id", "id", name="uq_clients_workspace_id"),
        ),
    )
    tables["matters"] = _legacy_table(
        metadata,
        "matters",
        sa.Column("workspace_id", sa.String(36), nullable=False),
        uniques=(
            sa.UniqueConstraint("workspace_id", "id", name="uq_matters_workspace_id"),
        ),
    )
    tables["approval_queue_items"] = _legacy_table(
        metadata,
        "approval_queue_items",
        sa.Column("matter_id", sa.String(36), nullable=False),
        foreign_keys=(
            sa.ForeignKeyConstraint(
                ["matter_id"],
                ["matters.id"],
                name="fk_legacy_approval_matter",
                ondelete="CASCADE",
            ),
        ),
    )

    tables["klm_omnichannel_threads"] = _simple_tenant_child(
        metadata,
        "klm_omnichannel_threads",
        (("matter_id", "matters", True), ("client_id", "clients", True)),
    )
    tables["klm_omnichannel_messages"] = _simple_tenant_child(
        metadata,
        "klm_omnichannel_messages",
        (
            ("thread_id", "klm_omnichannel_threads", False),
            ("matter_id", "matters", True),
        ),
    )
    tables["klm_follow_up_tasks"] = _simple_tenant_child(
        metadata,
        "klm_follow_up_tasks",
        (("matter_id", "matters", True), ("thread_id", "klm_omnichannel_threads", True)),
    )
    tables["klm_acquisition_events"] = _simple_tenant_child(
        metadata,
        "klm_acquisition_events",
        (("matter_id", "matters", True), ("client_id", "clients", True)),
    )
    tables["klm_process_monitoring_records"] = _simple_tenant_child(
        metadata,
        "klm_process_monitoring_records",
        (("matter_id", "matters", False),),
    )
    tables["klm_external_action_intents"] = _simple_tenant_child(
        metadata,
        "klm_external_action_intents",
        (("matter_id", "matters", True),),
    )

    artifact = _legacy_table(
        metadata,
        "klm_legal_artifact_versions",
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("matter_id", sa.String(36), nullable=False),
        sa.Column("artifact_key", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("approval_item_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        foreign_keys=(
            sa.ForeignKeyConstraint(
                ["matter_id"],
                ["matters.id"],
                name="fk_legacy_artifact_matter",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["approval_item_id"],
                ["approval_queue_items.id"],
                name="fk_legacy_artifact_approval",
                ondelete="SET NULL",
            ),
        ),
        uniques=(
            sa.UniqueConstraint(
                "workspace_id",
                "artifact_key",
                "version",
                name="uq_klm_legal_artifact_version",
            ),
        ),
    )
    sa.Index(
        "ix_klm_legal_artifact_workspace_status",
        artifact.c.workspace_id,
        artifact.c.status,
    )
    sa.Index(
        "ix_klm_legal_artifact_approval_item_id",
        artifact.c.approval_item_id,
    )
    tables[artifact.name] = artifact

    tables["oss_native_capability_states"] = _legacy_table(
        metadata,
        "oss_native_capability_states",
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    tables["oss_native_commands"] = _simple_tenant_child(
        metadata,
        "oss_native_commands",
        (("state_id", "oss_native_capability_states", True),),
    )
    tables["oss_native_records"] = _simple_tenant_child(
        metadata,
        "oss_native_records",
        (("last_command_id", "oss_native_commands", True),),
    )
    tables["oss_native_audit_events"] = _simple_tenant_child(
        metadata,
        "oss_native_audit_events",
        (
            ("state_id", "oss_native_capability_states", True),
            ("command_id", "oss_native_commands", True),
        ),
    )
    tables["oss_function_unit_events"] = _legacy_table(
        metadata,
        "oss_function_unit_events",
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    tables["klm_audit_events"] = _legacy_table(
        metadata,
        "klm_audit_events",
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
    )

    metadata.create_all(connection)
    now = datetime.now(UTC)
    connection.execute(
        tables["workspaces"].insert(),
        ({"id": "workspace-a"}, {"id": "workspace-b"}),
    )
    connection.execute(
        tables["clients"].insert(),
        (
            {"id": "client-a", "workspace_id": "workspace-a"},
            {"id": "client-b", "workspace_id": "workspace-b"},
        ),
    )
    connection.execute(
        tables["matters"].insert(),
        (
            {"id": "matter-a", "workspace_id": "workspace-a"},
            {"id": "matter-b", "workspace_id": "workspace-b"},
        ),
    )
    connection.execute(
        tables["approval_queue_items"].insert(),
        (
            {"id": "approval-a", "matter_id": "matter-a"},
            {"id": "approval-b", "matter_id": "matter-b"},
        ),
    )
    connection.execute(
        tables["klm_omnichannel_threads"].insert(),
        (
            {
                "id": "thread-a",
                "workspace_id": "workspace-a",
                "matter_id": "matter-a",
                "client_id": "client-a",
            },
            {
                "id": "thread-b",
                "workspace_id": "workspace-b",
                "matter_id": "matter-b",
                "client_id": "client-b",
            },
        ),
    )
    for table_name, values in (
        (
            "klm_omnichannel_messages",
            {"thread_id": "thread-a", "matter_id": "matter-a"},
        ),
        (
            "klm_follow_up_tasks",
            {"matter_id": "matter-a", "thread_id": "thread-a"},
        ),
        (
            "klm_acquisition_events",
            {"matter_id": "matter-a", "client_id": "client-a"},
        ),
        ("klm_process_monitoring_records", {"matter_id": "matter-a"}),
        ("klm_external_action_intents", {"matter_id": "matter-a"}),
    ):
        connection.execute(
            tables[table_name].insert(),
            {"id": f"{table_name}-a", "workspace_id": "workspace-a", **values},
        )

    connection.execute(
        artifact.insert(),
        {
            "id": "artifact-a",
            "workspace_id": "workspace-a",
            "matter_id": "matter-a",
            "artifact_key": "artifact-key-a",
            "version": 1,
            "kind": "proposal",
            "title": "Proposta imutável",
            "status": "review_pending",
            "content": {"body": "conteúdo"},
            "source_snapshot": {"matterId": "matter-a"},
            "created_by_user_id": "user-a",
            "approval_item_id": "approval-a",
            "created_at": now,
            "updated_at": now,
        },
    )
    connection.execute(
        tables["oss_native_capability_states"].insert(),
        (
            {"id": "state-a", "workspace_id": "workspace-a"},
            {"id": "state-b", "workspace_id": "workspace-b"},
        ),
    )
    connection.execute(
        tables["oss_native_commands"].insert(),
        (
            {"id": "command-a", "workspace_id": "workspace-a", "state_id": "state-a"},
            {"id": "command-b", "workspace_id": "workspace-b", "state_id": "state-b"},
        ),
    )
    connection.execute(
        tables["oss_native_records"].insert(),
        {
            "id": "record-a",
            "workspace_id": "workspace-a",
            "last_command_id": "command-a",
        },
    )
    connection.execute(
        tables["oss_native_audit_events"].insert(),
        {
            "id": "oss-audit-a",
            "workspace_id": "workspace-a",
            "state_id": "state-a",
            "command_id": "command-a",
        },
    )
    connection.execute(
        tables["oss_function_unit_events"].insert(),
        {"id": "function-event-a", "workspace_id": "workspace-a"},
    )
    connection.execute(
        tables["klm_audit_events"].insert(),
        {
            "id": "audit-a",
            "workspace_id": "workspace-a",
            "event_type": "seeded",
            "entity_type": "matter",
            "entity_id": "matter-a",
            "summary": "Seeded evidence",
        },
    )
    return tables


def _install_migration(connection: sa.Connection):
    migration = _load_migration_module()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def _foreign_keys(inspector: sa.Inspector, table_name: str):
    return {
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
        )
        for foreign_key in inspector.get_foreign_keys(table_name)
    }


def _sqlite_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite:///:memory:")

    @sa.event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.mark.parametrize(
    ("table_name", "column_name", "row_id", "foreign_id"),
    (
        ("klm_omnichannel_threads", "matter_id", "thread-a", "matter-b"),
        ("klm_omnichannel_threads", "client_id", "thread-a", "client-b"),
        (
            "klm_omnichannel_messages",
            "thread_id",
            "klm_omnichannel_messages-a",
            "thread-b",
        ),
        (
            "klm_omnichannel_messages",
            "matter_id",
            "klm_omnichannel_messages-a",
            "matter-b",
        ),
        ("klm_follow_up_tasks", "matter_id", "klm_follow_up_tasks-a", "matter-b"),
        ("klm_follow_up_tasks", "thread_id", "klm_follow_up_tasks-a", "thread-b"),
        (
            "klm_acquisition_events",
            "matter_id",
            "klm_acquisition_events-a",
            "matter-b",
        ),
        (
            "klm_acquisition_events",
            "client_id",
            "klm_acquisition_events-a",
            "client-b",
        ),
        ("klm_legal_artifact_versions", "matter_id", "artifact-a", "matter-b"),
        (
            "klm_process_monitoring_records",
            "matter_id",
            "klm_process_monitoring_records-a",
            "matter-b",
        ),
        (
            "klm_external_action_intents",
            "matter_id",
            "klm_external_action_intents-a",
            "matter-b",
        ),
        ("oss_native_commands", "state_id", "command-a", "state-b"),
        ("oss_native_records", "last_command_id", "record-a", "command-b"),
        ("oss_native_audit_events", "state_id", "oss-audit-a", "state-b"),
        ("oss_native_audit_events", "command_id", "oss-audit-a", "command-b"),
    ),
)
def test_migration_preflight_fails_before_ddl_for_cross_workspace_rows(
    table_name: str,
    column_name: str,
    row_id: str,
    foreign_id: str,
) -> None:
    engine = _sqlite_engine()
    with engine.begin() as connection:
        tables = _create_legacy_schema(connection)
        connection.execute(
            tables[table_name]
            .update()
            .where(tables[table_name].c.id == row_id)
            .values({column_name: foreign_id})
        )
        migration = _install_migration(connection)

        with pytest.raises(
            RuntimeError,
            match=rf"{table_name}\.{column_name}=1",
        ):
            migration.upgrade()

        inspector = sa.inspect(connection)
        assert "klm_legal_artifact_states" not in inspector.get_table_names()
        assert "uq_klm_omnichannel_thread_workspace_id" not in {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "klm_omnichannel_threads"
            )
        }
    engine.dispose()


def test_migration_preflight_rejects_invalid_native_approval_consumption() -> None:
    engine = _sqlite_engine()
    with engine.begin() as connection:
        tables = _create_legacy_schema(connection)
        consumed = {
            "workspace_id": "workspace-a",
            "event_type": "native_capability_human_approval_consumed",
            "entity_type": "native_capability_approval",
            "entity_id": "approval-token-a",
            "summary": "Consumed",
        }
        connection.execute(
            tables["klm_audit_events"].insert(),
            (
                {"id": "consumed-a", **consumed},
                {"id": "consumed-b", **consumed},
                {"id": "consumed-without-entity", **consumed, "entity_id": None},
            ),
        )
        migration = _install_migration(connection)

        with pytest.raises(
            RuntimeError,
            match=r"missing_entity_id=1, duplicate_keys=1",
        ):
            migration.upgrade()

        assert "klm_legal_artifact_states" not in sa.inspect(connection).get_table_names()
    engine.dispose()


def test_migration_round_trip_constraints_hashes_and_sqlite_guards() -> None:
    engine = _sqlite_engine()
    with engine.begin() as connection:
        _create_legacy_schema(connection)
        migration = _install_migration(connection)
        assert migration.down_revision == "c9e2f4a6b803"
        assert len(migration.TENANT_FOREIGN_KEYS) == 15
        assert all(
            len(f"{constraint_name}_legacy") <= 63
            for *_prefix, constraint_name in migration.TENANT_FOREIGN_KEYS
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        for table_name, child_column, parent_table, _ondelete, _name in (
            migration.TENANT_FOREIGN_KEYS
        ):
            assert (
                ("workspace_id", child_column),
                parent_table,
                ("workspace_id", "id"),
            ) in _foreign_keys(inspector, table_name)

        artifact_columns = {
            column["name"]
            for column in inspector.get_columns("klm_legal_artifact_versions")
        }
        assert {"evidence_schema_version", "evidence_sha256"} <= artifact_columns
        assert {"status", "approval_item_id", "updated_at"}.isdisjoint(
            artifact_columns
        )
        state = connection.execute(
            sa.text(
                "SELECT * FROM klm_legal_artifact_states "
                "WHERE artifact_version_id = 'artifact-a'"
            )
        ).mappings().one()
        assert state["status"] == "review_pending"
        assert state["approval_item_id"] == "approval-a"
        artifact = connection.execute(
            sa.text(
                "SELECT * FROM klm_legal_artifact_versions WHERE id = 'artifact-a'"
            )
        ).mappings().one()
        assert artifact["evidence_schema_version"] == 1
        assert artifact["evidence_sha256"] == legal_artifact_evidence_sha256(
            evidence_schema_version=1,
            workspace_id="workspace-a",
            matter_id="matter-a",
            artifact_key="artifact-key-a",
            version=1,
            kind="proposal",
            title="Proposta imutável",
            content={"body": "conteúdo"},
            source_snapshot={"matterId": "matter-a"},
            created_by_user_id="user-a",
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO klm_follow_up_tasks "
                    "(id, workspace_id, matter_id, thread_id) "
                    "VALUES ('cross-follow-up', 'workspace-a', 'matter-b', NULL)"
                )
            )

        consumed = {
            "workspace_id": "workspace-a",
            "event_type": "native_capability_human_approval_consumed",
            "entity_type": "native_capability_approval",
            "entity_id": "approval-token-a",
            "summary": "Consumed once",
        }
        connection.execute(
            sa.text(
                "INSERT INTO klm_audit_events "
                "(id, workspace_id, event_type, entity_type, entity_id, summary) "
                "VALUES (:id, :workspace_id, :event_type, :entity_type, :entity_id, :summary)"
            ),
            {"id": "consumed-a", **consumed},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO klm_audit_events "
                    "(id, workspace_id, event_type, entity_type, entity_id, summary) "
                    "VALUES (:id, :workspace_id, :event_type, :entity_type, :entity_id, :summary)"
                ),
                {"id": "consumed-b", **consumed},
            )

        for table_name, row_id in (
            ("klm_audit_events", "audit-a"),
            ("oss_native_audit_events", "oss-audit-a"),
            ("oss_function_unit_events", "function-event-a"),
            ("klm_legal_artifact_versions", "artifact-a"),
        ):
            with pytest.raises(DBAPIError, match="immutable"):
                connection.execute(
                    sa.text(f'UPDATE "{table_name}" SET id = id WHERE id = :row_id'),
                    {"row_id": row_id},
                )
            with pytest.raises(DBAPIError, match="immutable"):
                connection.execute(
                    sa.text(f'DELETE FROM "{table_name}" WHERE id = :row_id'),
                    {"row_id": row_id},
                )

        migration.downgrade()

        inspector = sa.inspect(connection)
        assert "klm_legal_artifact_states" not in inspector.get_table_names()
        artifact_columns = {
            column["name"]
            for column in inspector.get_columns("klm_legal_artifact_versions")
        }
        assert {"status", "approval_item_id", "updated_at"} <= artifact_columns
        assert {"evidence_schema_version", "evidence_sha256"}.isdisjoint(
            artifact_columns
        )
        restored = connection.execute(
            sa.text(
                "SELECT status, approval_item_id FROM klm_legal_artifact_versions "
                "WHERE id = 'artifact-a'"
            )
        ).mappings().one()
        assert restored == {
            "status": "review_pending",
            "approval_item_id": "approval-a",
        }
        connection.execute(
            sa.text(
                "UPDATE klm_audit_events SET summary = 'downgraded' "
                "WHERE id = 'audit-a'"
            )
        )
    engine.dispose()
