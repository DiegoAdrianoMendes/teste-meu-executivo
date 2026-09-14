"""add agent runtime models

Revision ID: 9f2e6d7c1a88
Revises: 6b4e1f5a9c21
Create Date: 2026-04-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9f2e6d7c1a88"
down_revision: Union[str, None] = "6b4e1f5a9c21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE agent_run_status_enum AS ENUM ('queued', 'running', 'completed', 'failed', 'waiting_human', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE human_override_decision_enum AS ENUM ('accepted', 'corrected', 'rejected');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            triggered_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            agent_name VARCHAR(120) NOT NULL,
            trigger_source VARCHAR(120) NOT NULL,
            thread_id VARCHAR(200),
            dedupe_key VARCHAR(255),
            celery_task_id VARCHAR(255),
            status agent_run_status_enum NOT NULL DEFAULT 'queued',
            input_payload JSONB,
            output_payload JSONB,
            error_message TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_agent_runs_dedupe_key UNIQUE (dedupe_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_runs_workspace_id ON agent_runs (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_runs_matter_id ON agent_runs (matter_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_runs_status ON agent_runs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_runs_agent_name ON agent_runs (agent_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_runs_trigger_source ON agent_runs (trigger_source)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_runs_celery_task_id ON agent_runs (celery_task_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS human_overrides (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            decided_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            decision human_override_decision_enum NOT NULL,
            notes TEXT,
            before_payload JSONB,
            after_payload JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_human_overrides_workspace_id ON human_overrides (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_human_overrides_matter_id ON human_overrides (matter_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_human_overrides_agent_run_id ON human_overrides (agent_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_human_overrides_decision ON human_overrides (decision)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_human_overrides_decision")
    op.execute("DROP INDEX IF EXISTS ix_human_overrides_agent_run_id")
    op.execute("DROP INDEX IF EXISTS ix_human_overrides_matter_id")
    op.execute("DROP INDEX IF EXISTS ix_human_overrides_workspace_id")
    op.execute("DROP TABLE IF EXISTS human_overrides CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_agent_runs_celery_task_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_trigger_source")
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_agent_name")
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_status")
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_matter_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_workspace_id")
    op.execute("DROP TABLE IF EXISTS agent_runs CASCADE")

    op.execute("DROP TYPE IF EXISTS human_override_decision_enum")
    op.execute("DROP TYPE IF EXISTS agent_run_status_enum")
