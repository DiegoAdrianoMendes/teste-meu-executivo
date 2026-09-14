"""add knowledge chunks

Revision ID: b7a2d1c5e443
Revises: 9f2e6d7c1a88
Create Date: 2026-04-03 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7a2d1c5e443"
down_revision: Union[str, None] = "9f2e6d7c1a88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            source_type VARCHAR(50) NOT NULL,
            source_ref VARCHAR(120),
            title VARCHAR(255),
            content_text TEXT NOT NULL,
            summary TEXT,
            content_hash VARCHAR(64) NOT NULL,
            rank_hint INTEGER NOT NULL DEFAULT 0,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            chunk_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_knowledge_chunks_source UNIQUE (workspace_id, matter_id, source_type, source_ref)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_workspace_id ON knowledge_chunks (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_matter_id ON knowledge_chunks (matter_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_task_id ON knowledge_chunks (task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_source_type ON knowledge_chunks (source_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_content_hash ON knowledge_chunks (content_hash)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_content_hash")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_source_type")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_task_id")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_matter_id")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_workspace_id")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks CASCADE")
