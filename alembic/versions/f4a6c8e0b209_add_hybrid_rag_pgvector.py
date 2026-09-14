"""Add deterministic embeddings and PostgreSQL hybrid-search indexes.

Revision ID: f4a6c8e0b209
Revises: e0b1c2d3f408
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON

revision: str = "f4a6c8e0b209"
down_revision: str | Sequence[str] | None = "e0b1c2d3f408"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "embedding_data",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    if op.get_bind().dialect.name != "postgresql":
        return

    op.create_unique_constraint(
        "uq_matters_id_workspace",
        "matters",
        ["id", "workspace_id"],
    )
    op.create_foreign_key(
        "fk_knowledge_chunks_matter_workspace",
        "knowledge_chunks",
        "matters",
        ["matter_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="CASCADE",
    )
    op.execute(
        """
        ALTER TABLE knowledge_chunks
        ADD COLUMN search_document tsvector
        GENERATED ALWAYS AS (
            to_tsvector(
                'portuguese',
                coalesce(title, '') || ' ' ||
                coalesce(summary, '') || ' ' ||
                coalesce(content_text, '')
            )
        ) STORED
        """
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_scope_source_ref "
        "ON knowledge_chunks(workspace_id, matter_id, source_type, source_ref)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_search_document_gin "
        "ON knowledge_chunks USING GIN(search_document)"
    )
    bind = op.get_bind()
    bind.exec_driver_sql("SAVEPOINT klm_pgvector_extension")
    try:
        bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception:
        bind.exec_driver_sql("ROLLBACK TO SAVEPOINT klm_pgvector_extension")
    finally:
        bind.exec_driver_sql("RELEASE SAVEPOINT klm_pgvector_extension")
    vector_enabled = bool(
        bind.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        ).scalar()
    )
    if not vector_enabled:
        return

    op.execute(
        "ALTER TABLE knowledge_chunks "
        "ADD COLUMN embedding_vector vector(384)"
    )
    op.execute(
        """
        CREATE FUNCTION sync_knowledge_chunk_embedding_vector()
        RETURNS trigger AS $$
        BEGIN
            IF jsonb_typeof(NEW.embedding_data::jsonb) = 'array'
               AND jsonb_array_length(NEW.embedding_data::jsonb) = 384 THEN
                NEW.embedding_vector := NEW.embedding_data::text::vector;
            ELSE
                NEW.embedding_vector := NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_knowledge_chunks_embedding_vector
        BEFORE INSERT OR UPDATE OF embedding_data ON knowledge_chunks
        FOR EACH ROW EXECUTE FUNCTION sync_knowledge_chunk_embedding_vector()
        """
    )
    op.execute(
        """
        UPDATE knowledge_chunks
        SET embedding_data = embedding_data
        WHERE jsonb_typeof(embedding_data::jsonb) = 'array'
          AND jsonb_array_length(embedding_data::jsonb) = 384
        """
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding_vector vector_cosine_ops) "
        "WHERE embedding_vector IS NOT NULL"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "fk_knowledge_chunks_matter_workspace",
            "knowledge_chunks",
            type_="foreignkey",
        )
        op.drop_constraint(
            "uq_matters_id_workspace",
            "matters",
            type_="unique",
        )
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_scope_source_ref")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_search_document_gin")
        op.execute(
            "DROP TRIGGER IF EXISTS trg_knowledge_chunks_embedding_vector "
            "ON knowledge_chunks"
        )
        op.execute("DROP FUNCTION IF EXISTS sync_knowledge_chunk_embedding_vector()")
        op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS search_document")
        op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding_vector")
    op.drop_column("knowledge_chunks", "embedding_data")
