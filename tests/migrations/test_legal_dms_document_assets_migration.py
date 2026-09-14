from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.document_asset import (
    DocumentAsset,
    DocumentAssetReference,
    DocumentShareDownload,
    DocumentShareLink,
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
    PROJECT_ROOT / "alembic" / "versions" / "4ce67f6149cd_add_legal_dms_document_assets.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("legal_dms_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "matters",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_matter_workspace_id"),
    )
    sa.Table(
        "clients",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_client_workspace_id"),
    )
    sa.Table(
        "storage_objects",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_storage_object_workspace_id"),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(sa.text("INSERT INTO users (id) VALUES ('user-a')"))
    connection.execute(
        sa.text("INSERT INTO matters (id, workspace_id) VALUES ('matter-a', 'workspace-a')")
    )
    connection.execute(
        sa.text(
            "INSERT INTO storage_objects (id, workspace_id) VALUES ('storage-a', 'workspace-a')"
        )
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_legal_dms_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "4ce67f6149cd"
        assert migration.down_revision == "a3f9c1e6b204"

        migration.upgrade()

        table_names = set(sa.inspect(connection).get_table_names())
        assert {
            "document_assets",
            "document_asset_references",
            "document_share_links",
            "document_share_downloads",
        } <= table_names

        asset_index_names = {
            index["name"] for index in sa.inspect(connection).get_indexes("document_assets")
        }
        assert "ix_document_asset_workspace_type" in asset_index_names
        assert "ix_document_asset_workspace_matter" in asset_index_names

        connection.execute(
            sa.text(
                "INSERT INTO document_assets "
                "(id, workspace_id, storage_object_id, content_sha256, original_filename, "
                "document_type, classification_method, ocr_status, created_at, updated_at) "
                "VALUES ('asset-a', 'workspace-a', 'storage-a', "
                "'0000000000000000000000000000000000000000000000000000000000000000', "
                "'peticao.pdf', 'nao_classificado', 'indisponivel', 'ocr_pendente', "
                "'2026-07-27', '2026-07-27')"
            )
        )
        # Dedup: mesmo hash no mesmo workspace não pode duplicar o asset canônico.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO document_assets "
                    "(id, workspace_id, storage_object_id, content_sha256, original_filename, "
                    "document_type, classification_method, ocr_status, created_at, updated_at) "
                    "VALUES ('asset-b', 'workspace-a', 'storage-a', "
                    "'0000000000000000000000000000000000000000000000000000000000000000', "
                    "'duplicata.pdf', 'nao_classificado', 'indisponivel', 'ocr_pendente', "
                    "'2026-07-27', '2026-07-27')"
                )
            )

        connection.execute(
            sa.text(
                "INSERT INTO document_asset_references "
                "(id, workspace_id, document_asset_id, source, original_filename, external_ref, "
                "created_at, updated_at) "
                "VALUES ('ref-a', 'workspace-a', 'asset-a', 'email', 'peticao.pdf', 'msg-1', "
                "'2026-07-27', '2026-07-27')"
            )
        )
        # Idempotência de ingestão automatizada: mesmo (source, external_ref) não duplica.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO document_asset_references "
                    "(id, workspace_id, document_asset_id, source, original_filename, "
                    "external_ref, created_at, updated_at) "
                    "VALUES ('ref-b', 'workspace-a', 'asset-a', 'email', 'peticao.pdf', "
                    "'msg-1', '2026-07-27', '2026-07-27')"
                )
            )
        # Uma segunda referência SEM external_ref (upload manual repetido) é permitida.
        connection.execute(
            sa.text(
                "INSERT INTO document_asset_references "
                "(id, workspace_id, document_asset_id, source, original_filename, "
                "created_at, updated_at) "
                "VALUES ('ref-c', 'workspace-a', 'asset-a', 'upload_manual', 'peticao.pdf', "
                "'2026-07-27', '2026-07-27')"
            )
        )

        connection.execute(
            sa.text(
                "INSERT INTO document_share_links "
                "(id, workspace_id, document_asset_id, token_hash, expires_at, "
                "watermark_enabled, download_count, created_at, updated_at) "
                "VALUES ('link-a', 'workspace-a', 'asset-a', "
                "'a' || substr(hex(randomblob(30)), 1, 62), "
                "'2026-08-27', 0, 0, '2026-07-27', '2026-07-27')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO document_share_downloads "
                "(id, workspace_id, share_link_id, downloaded_at, created_at, updated_at) "
                "VALUES ('dl-a', 'workspace-a', 'link-a', '2026-07-27', '2026-07-27', '2026-07-27')"
            )
        )
        assert (
            connection.execute(
                sa.text("SELECT COUNT(*) FROM document_share_downloads")
            ).scalar_one()
            == 1
        )

        migration.downgrade()
        remaining = set(sa.inspect(connection).get_table_names())
        assert "document_assets" not in remaining
        assert "document_asset_references" not in remaining
        assert "document_share_links" not in remaining
        assert "document_share_downloads" not in remaining
    engine.dispose()


def test_legal_dms_migration_skips_postgres_only_statements_on_sqlite() -> None:
    """RLS e o ALTER do CHECK constraint de `legal_exceptions` são no-ops fora do Postgres."""
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        # Não deve lançar mesmo sem a tabela `legal_exceptions` existir no fixture —
        # o guard `dialect.name != "postgresql"` retorna antes de qualquer DDL nela.
        migration.upgrade()
        migration.downgrade()
    engine.dispose()


def test_legal_dms_models_compile_for_postgresql() -> None:
    for model in (
        DocumentAsset,
        DocumentAssetReference,
        DocumentShareLink,
        DocumentShareDownload,
    ):
        ddl = str(sa.schema.CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert "UUID" in ddl
