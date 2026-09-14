from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.process_monitoring import ProcessPublication, PublicationJourneyState

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
    / "d3f7a9c1b2e4_add_publication_journey_state.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("publication_journey_state_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "process_publications",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("fonte", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("numero_cnj", sa.String(25), nullable=False),
        sa.Column("conteudo", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(sa.text("INSERT INTO workspaces (id) VALUES ('workspace-a')"))
    connection.execute(
        sa.text(
            "INSERT INTO process_publications "
            "(id, workspace_id, fonte, external_id, numero_cnj, conteudo, published_at) "
            "VALUES ('pub-a', 'workspace-a', 'djen', 'ext-1', "
            "'0001234-56.2026.8.07.0001', 'texto', '2026-07-26')"
        )
    )


def _install(connection: sa.Connection):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_publication_journey_state_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_schema(connection)
        migration = _install(connection)
        assert migration.revision == "d3f7a9c1b2e4"
        assert migration.down_revision == "a04b6e6f22b4"

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {col["name"] for col in inspector.get_columns("process_publications")}
        assert {"journey_state", "journey_updated_at"} <= columns

        # server_default aplica-se às linhas pré-existentes.
        assert connection.execute(
            sa.text("SELECT journey_state FROM process_publications WHERE id = 'pub-a'")
        ).scalar_one() == "capturada"

        indexes = {ix["name"] for ix in inspector.get_indexes("process_publications")}
        assert "ix_process_publication_workspace_journey_state" in indexes

        # CHECK constraint fail-closed: valor fora do enum é rejeitado.
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO process_publications "
                    "(id, workspace_id, fonte, external_id, numero_cnj, conteudo, "
                    "published_at, journey_state) "
                    "VALUES ('pub-b', 'workspace-a', 'djen', 'ext-2', "
                    "'0001234-56.2026.8.07.0001', 'texto', '2026-07-26', 'estado_invalido')"
                )
            )

        migration.downgrade()

        remaining = {col["name"] for col in sa.inspect(connection).get_columns("process_publications")}
        assert "journey_state" not in remaining
        assert "journey_updated_at" not in remaining
    engine.dispose()


def test_publication_journey_state_covers_all_19_states() -> None:
    from app.services.legal_journey_service import VALID_TRANSITIONS

    assert len(PublicationJourneyState) == 19
    assert set(VALID_TRANSITIONS.keys()) == set(PublicationJourneyState)


def test_process_publication_model_compiles_for_postgresql_with_journey_state() -> None:
    ddl = str(
        sa.schema.CreateTable(ProcessPublication.__table__).compile(dialect=postgresql.dialect())
    )
    assert "journey_state" in ddl
    assert "publication_journey_state_enum" in ddl
    assert "journey_updated_at" in ddl
