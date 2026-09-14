from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.migration_job import MigrationDivergence, MigrationJob, MigrationStagingRecord

PROJECT_ROOT = Path(__file__).resolve().parents[2]
original_path = sys.path[:]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
try:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
finally:
    sys.path = original_path

MIGRATION_PATH = PROJECT_ROOT / "alembic" / "versions" / "a04b6e6f22b4_add_migration_jobs.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_jobs_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_migration_jobs_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "a04b6e6f22b4"
        assert migration.down_revision == "3040c77e8efa"

        migration.upgrade()

        inspector = sa.inspect(connection)
        table_names = set(inspector.get_table_names())
        expected_tables = {"migration_jobs", "migration_staging_records", "migration_divergences"}
        assert expected_tables <= table_names

        job_index_names = {index["name"] for index in inspector.get_indexes("migration_jobs")}
        assert "ix_migration_jobs_workspace_id" in job_index_names
        assert "ix_migration_jobs_status" in job_index_names

        staging_index_names = {
            index["name"] for index in inspector.get_indexes("migration_staging_records")
        }
        assert "ix_migration_staging_workspace_job_domain" in staging_index_names
        staging_unique_names = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("migration_staging_records")
        }
        assert "uq_migration_staging_job_domain_dedupe" in staging_unique_names

        connection.execute(
            sa.text(
                "INSERT INTO migration_jobs "
                "(id, workspace_id, source_system, status, source_manifest, field_mapping, "
                "preview_summary, reconciliation_summary, created_by, created_at, updated_at) "
                "VALUES ('job-a', 'workspace-a', 'universal_csv', 'recebido', '{}', '{}', "
                "'{}', '{}', 'user-a', '2026-07-27', '2026-07-27')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO migration_staging_records "
                "(id, workspace_id, job_id, domain, raw, normalized, dedupe_key, weak_identity, "
                "conflict, record_status, created_at, updated_at) "
                "VALUES ('rec-a', 'workspace-a', 'job-a', 'clientes', '{}', '{}', 'doc:123', 0, "
                "'none', 'pendente', '2026-07-27', '2026-07-27')"
            )
        )

        # Idempotência de staging: mesma (job, domain, dedupe_key) não duplica.
        with sa_raises_integrity_error():
            connection.execute(
                sa.text(
                    "INSERT INTO migration_staging_records "
                    "(id, workspace_id, job_id, domain, raw, normalized, dedupe_key, "
                    "weak_identity, conflict, record_status, created_at, updated_at) "
                    "VALUES ('rec-b', 'workspace-a', 'job-a', 'clientes', '{}', '{}', 'doc:123', "
                    "0, 'none', 'pendente', '2026-07-27', '2026-07-27')"
                )
            )

        connection.execute(
            sa.text(
                "INSERT INTO migration_divergences "
                "(id, workspace_id, job_id, domain, tipo, severity, detalhe, resolvido, "
                "created_at, updated_at) "
                "VALUES ('div-a', 'workspace-a', 'job-a', 'processos', 'processo_sem_vinculo', "
                "'critica', 'sem vinculo', 0, '2026-07-27', '2026-07-27')"
            )
        )
        assert connection.execute(
            sa.text("SELECT tipo FROM migration_divergences WHERE id = 'div-a'")
        ).scalar_one() == "processo_sem_vinculo"

        migration.downgrade()

        remaining = set(sa.inspect(connection).get_table_names())
        assert "migration_jobs" not in remaining
        assert "migration_staging_records" not in remaining
        assert "migration_divergences" not in remaining
    engine.dispose()


def sa_raises_integrity_error():
    import pytest

    return pytest.raises(sa.exc.IntegrityError)


def test_migration_models_compile_for_postgresql() -> None:
    for model in (MigrationJob, MigrationStagingRecord, MigrationDivergence):
        ddl = str(sa.schema.CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert "UUID" in ddl
    job_ddl = str(
        sa.schema.CreateTable(MigrationJob.__table__).compile(dialect=postgresql.dialect())
    )
    assert "migration_source_system_enum" in job_ddl
    assert "migration_job_status_enum" in job_ddl
    staging_ddl = str(
        sa.schema.CreateTable(MigrationStagingRecord.__table__).compile(dialect=postgresql.dialect())
    )
    assert "migration_domain_enum" in staging_ddl
    assert "migration_conflict_enum" in staging_ddl
    assert "migration_record_status_enum" in staging_ddl
