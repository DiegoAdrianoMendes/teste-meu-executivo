from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.legal_batch import LegalBatch, LegalBatchItem

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "46cd1d7279bf_add_legal_batch_operation.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("legal_batch_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "legal_demands",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    sa.Table(
        "agent_autonomy_policies",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
    connection.execute(
        sa.text(
            "INSERT INTO legal_demands (id, workspace_id) VALUES ('demand-a', 'workspace-a')"
        )
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_legal_batch_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "46cd1d7279bf"
        assert migration.down_revision == "b6b46ea00c28"

        migration.upgrade()

        table_names = set(sa.inspect(connection).get_table_names())
        assert "legal_batches" in table_names
        assert "legal_batch_items" in table_names

        batch_index_names = {
            index["name"] for index in sa.inspect(connection).get_indexes("legal_batches")
        }
        assert "ix_legal_batches_workspace_id" in batch_index_names
        assert "ix_legal_batches_status" in batch_index_names
        item_index_names = {
            index["name"] for index in sa.inspect(connection).get_indexes("legal_batch_items")
        }
        assert "ix_legal_batch_items_workspace_id" in item_index_names
        assert "ix_legal_batch_items_state" in item_index_names

        connection.execute(
            sa.text(
                "INSERT INTO legal_batches "
                "(id, workspace_id, name, criterion_type, criterion_value, status, "
                "created_at, updated_at) "
                "VALUES ('batch-a', 'workspace-a', 'Lote publicações TJDFT', 'tribunal', "
                "'{}', 'aberto', '2026-07-27', '2026-07-27')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO legal_batch_items "
                "(id, workspace_id, batch_id, legal_demand_id, action, decision_kind, state, "
                "state_history, input_payload, result_payload, requires_human_approval, "
                "created_at, updated_at) "
                "VALUES ('item-a', 'workspace-a', 'batch-a', 'demand-a', 'classificar', "
                "'classificacao', 'pendente', '[]', '{}', '{}', 0, "
                "'2026-07-27', '2026-07-27')"
            )
        )

        # `state` fora do vocabulário fechado é rejeitado pelo CHECK constraint.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO legal_batch_items "
                    "(id, workspace_id, batch_id, action, decision_kind, state, "
                    "state_history, input_payload, result_payload, requires_human_approval, "
                    "created_at, updated_at) "
                    "VALUES ('item-b', 'workspace-a', 'batch-a', 'classificar', "
                    "'classificacao', 'pronto', '[]', '{}', '{}', 0, "
                    "'2026-07-27', '2026-07-27')"
                )
            )

        # `status` do lote fora do vocabulário fechado também é rejeitado.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO legal_batches "
                    "(id, workspace_id, name, criterion_type, criterion_value, status, "
                    "created_at, updated_at) "
                    "VALUES ('batch-b', 'workspace-a', 'Lote inválido', 'tribunal', "
                    "'{}', 'inexistente', '2026-07-27', '2026-07-27')"
                )
            )

        # `confidence` fora de [0, 1] é rejeitado.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO legal_batch_items "
                    "(id, workspace_id, batch_id, action, decision_kind, state, confidence, "
                    "state_history, input_payload, result_payload, requires_human_approval, "
                    "created_at, updated_at) "
                    "VALUES ('item-c', 'workspace-a', 'batch-a', 'classificar', "
                    "'classificacao', 'pendente', 1.5, '[]', '{}', '{}', 0, "
                    "'2026-07-27', '2026-07-27')"
                )
            )

        # Excluir o lote apaga em cascata os itens (nunca deixa item órfão).
        connection.execute(sa.text("DELETE FROM legal_batches WHERE id = 'batch-a'"))
        remaining_items = connection.execute(
            sa.text("SELECT COUNT(*) FROM legal_batch_items")
        ).scalar_one()
        assert remaining_items == 0

        migration.downgrade()
        remaining = set(sa.inspect(connection).get_table_names())
        assert "legal_batches" not in remaining
        assert "legal_batch_items" not in remaining
    engine.dispose()


def test_legal_batch_migration_skips_postgres_only_statements_on_sqlite() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        migration.upgrade()
        migration.downgrade()
    engine.dispose()


def test_legal_batch_models_compile_for_postgresql() -> None:
    batch_ddl = str(
        sa.schema.CreateTable(LegalBatch.__table__).compile(dialect=postgresql.dialect())
    )
    assert "UUID" in batch_ddl
    item_ddl = str(
        sa.schema.CreateTable(LegalBatchItem.__table__).compile(dialect=postgresql.dialect())
    )
    assert "UUID" in item_ddl
