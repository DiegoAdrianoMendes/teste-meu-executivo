"""add conversation insights

Revision ID: c6e1a9d4f102
Revises: b7a2d1c5e443
Create Date: 2026-04-03 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c6e1a9d4f102"
down_revision: Union[str, None] = "b7a2d1c5e443"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_insights (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            source_channel VARCHAR(50) NOT NULL DEFAULT 'whatsapp',
            summary TEXT,
            funnel_signal VARCHAR(50),
            close_intent VARCHAR(20),
            document_readiness VARCHAR(20),
            urgency_level VARCHAR(20),
            sentiment VARCHAR(20),
            trust_level VARCHAR(20),
            no_show_risk VARCHAR(20),
            objections JSONB NOT NULL DEFAULT '[]'::jsonb,
            requested_documents JSONB NOT NULL DEFAULT '[]'::jsonb,
            missing_information JSONB NOT NULL DEFAULT '[]'::jsonb,
            recommended_action TEXT,
            next_best_message TEXT,
            estimated_conversion_value DOUBLE PRECISION,
            closed_signal BOOLEAN NOT NULL DEFAULT FALSE,
            conversion_platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_message_at VARCHAR(40),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_conversation_insights_matter_id UNIQUE (matter_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversation_insights_workspace_id ON conversation_insights (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversation_insights_matter_id ON conversation_insights (matter_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversation_insights_close_intent ON conversation_insights (close_intent)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversation_insights_closed_signal ON conversation_insights (closed_signal)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_conversation_insights_closed_signal")
    op.execute("DROP INDEX IF EXISTS ix_conversation_insights_close_intent")
    op.execute("DROP INDEX IF EXISTS ix_conversation_insights_matter_id")
    op.execute("DROP INDEX IF EXISTS ix_conversation_insights_workspace_id")
    op.execute("DROP TABLE IF EXISTS conversation_insights CASCADE")
