from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
    / "b7e4c1d9a603_enforce_global_storage_object_versions.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "global_storage_object_versions_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint_names(connection: sa.Connection) -> set[str | None]:
    return {
        constraint["name"]
        for constraint in sa.inspect(connection).get_unique_constraints(
            "storage_objects"
        )
    }


def test_global_storage_object_versions_migration_round_trip() -> None:
    metadata = sa.MetaData()
    storage_objects = sa.Table(
        "storage_objects",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("object_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "object_id",
            "version",
            name="uq_storage_object_workspace_object_version",
        ),
    )
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata.create_all(connection)
        migration = _load_migration_module()
        migration.op = Operations(MigrationContext.configure(connection))
        assert migration.revision == "b7e4c1d9a603"
        assert migration.down_revision == "a9d2e4f6b508"

        migration.upgrade()
        assert "uq_storage_object_object_version" in _constraint_names(connection)
        connection.execute(
            storage_objects.insert(),
            {
                "id": "version-a",
                "workspace_id": "workspace-a",
                "object_id": "shared-object",
                "version": 1,
            },
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                storage_objects.insert(),
                {
                    "id": "version-b",
                    "workspace_id": "workspace-b",
                    "object_id": "shared-object",
                    "version": 1,
                },
            )

        migration.downgrade()
        assert "uq_storage_object_object_version" not in _constraint_names(connection)
        assert "uq_storage_object_workspace_object_version" in _constraint_names(connection)
    engine.dispose()


def test_global_storage_object_versions_migration_reports_legacy_duplicates() -> None:
    metadata = sa.MetaData()
    storage_objects = sa.Table(
        "storage_objects",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("object_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "object_id",
            "version",
            name="uq_storage_object_workspace_object_version",
        ),
    )
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            storage_objects.insert(),
            [
                {
                    "id": "version-a",
                    "workspace_id": "workspace-a",
                    "object_id": "shared-object",
                    "version": 1,
                },
                {
                    "id": "version-b",
                    "workspace_id": "workspace-b",
                    "object_id": "shared-object",
                    "version": 1,
                },
            ],
        )
        migration = _load_migration_module()
        migration.op = Operations(MigrationContext.configure(connection))

        with pytest.raises(
            RuntimeError,
            match=r"duplicate_groups=1.*object_id=shared-object:version=1:rows=2",
        ):
            migration.upgrade()

        assert "uq_storage_object_object_version" not in _constraint_names(connection)
    engine.dispose()
