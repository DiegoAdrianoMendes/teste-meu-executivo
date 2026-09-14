"""enforce one immutable terminal override per agent run

Revision ID: 0f4e5d6c7b8a
Revises: f3a4b5c6d7e8
Create Date: 2026-07-16 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0f4e5d6c7b8a"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("LOCK TABLE human_overrides IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        WITH ranked_overrides AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY agent_run_id
                    ORDER BY created_at ASC, id ASC
                ) AS decision_order,
                COUNT(*) OVER (PARTITION BY agent_run_id) AS override_count
            FROM human_overrides
        ),
        first_overrides AS (
            SELECT human_overrides.*
            FROM human_overrides
            JOIN ranked_overrides
              ON ranked_overrides.id = human_overrides.id
             AND ranked_overrides.decision_order = 1
             AND ranked_overrides.override_count > 1
        )
        UPDATE agent_runs
        SET
            status = CASE
                WHEN first_overrides.decision::text IN ('accepted', 'corrected')
                    THEN 'completed'::agent_run_status_enum
                ELSE 'failed'::agent_run_status_enum
            END,
            output_payload = CASE
                WHEN first_overrides.decision::text = 'corrected'
                     AND first_overrides.after_payload IS NOT NULL
                    THEN first_overrides.after_payload
                ELSE first_overrides.before_payload
            END,
            error_message = CASE
                WHEN first_overrides.decision::text = 'rejected'
                    THEN COALESCE(
                        NULLIF(first_overrides.notes, ''),
                        'Saída do agente rejeitada por override humano'
                    )
                ELSE NULL
            END,
            completed_at = first_overrides.created_at,
            updated_at = now()
        FROM first_overrides
        WHERE agent_runs.id = first_overrides.agent_run_id
        """
    )
    op.execute(
        """
        WITH ranked_overrides AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY agent_run_id
                    ORDER BY created_at ASC, id ASC
                ) AS decision_order
            FROM human_overrides
        )
        DELETE FROM human_overrides
        USING ranked_overrides
        WHERE human_overrides.id = ranked_overrides.id
          AND ranked_overrides.decision_order > 1
        """
    )
    op.create_unique_constraint(
        "uq_human_overrides_agent_run_id",
        "human_overrides",
        ["agent_run_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_human_overrides_agent_run_id",
        "human_overrides",
        type_="unique",
    )
