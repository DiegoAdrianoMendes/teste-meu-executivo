"""add card dossier models

Revision ID: 6b4e1f5a9c21
Revises: 4d7c4c4b6d11
Create Date: 2026-04-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "6b4e1f5a9c21"
down_revision: Union[str, None] = "4d7c4c4b6d11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE card_field_change_actor_type_enum AS ENUM ('human', 'ai', 'system');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.execute("ALTER TABLE matters ADD COLUMN IF NOT EXISTS lead_source VARCHAR(120)")
    op.execute("ALTER TABLE matters ADD COLUMN IF NOT EXISTS executive_summary TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS ix_matters_lead_source ON matters (lead_source)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_attributions (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            source VARCHAR(100),
            medium VARCHAR(100),
            campaign VARCHAR(150),
            content VARCHAR(150),
            term VARCHAR(150),
            channel VARCHAR(100),
            first_touch_at VARCHAR(40),
            last_touch_at VARCHAR(40),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_campaign_attributions_matter UNIQUE (matter_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_campaign_attributions_matter_id ON campaign_attributions (matter_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS revenue_projections (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            estimated_claim_value DOUBLE PRECISION,
            projected_revenue DOUBLE PRECISION,
            success_probability DOUBLE PRECISION,
            confidence_level DOUBLE PRECISION,
            payback_days INTEGER,
            currency VARCHAR(10) NOT NULL DEFAULT 'BRL',
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_revenue_projections_matter UNIQUE (matter_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_revenue_projections_matter_id ON revenue_projections (matter_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS honorarium_estimates (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            contract_type VARCHAR(60),
            fixed_fee DOUBLE PRECISION,
            quota_litis_percentage DOUBLE PRECISION,
            expected_expenses DOUBLE PRECISION,
            total_estimated_value DOUBLE PRECISION,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_honorarium_estimates_matter UNIQUE (matter_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_honorarium_estimates_matter_id ON honorarium_estimates (matter_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS card_scores (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            viability_score DOUBLE PRECISION,
            urgency_score DOUBLE PRECISION,
            evidence_score DOUBLE PRECISION,
            conversion_score DOUBLE PRECISION,
            relationship_score DOUBLE PRECISION,
            overall_score DOUBLE PRECISION,
            explanation TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_card_scores_matter UNIQUE (matter_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_card_scores_matter_id ON card_scores (matter_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS card_field_changes (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            matter_id UUID NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
            changed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            actor_type card_field_change_actor_type_enum NOT NULL DEFAULT 'system',
            field_name VARCHAR(120) NOT NULL,
            old_value JSONB,
            new_value JSONB,
            summary TEXT,
            change_source VARCHAR(120),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_card_field_changes_matter_id ON card_field_changes (matter_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_card_field_changes_field_name ON card_field_changes (field_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_card_field_changes_changed_by_user_id ON card_field_changes (changed_by_user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_card_field_changes_changed_by_user_id")
    op.execute("DROP INDEX IF EXISTS ix_card_field_changes_field_name")
    op.execute("DROP INDEX IF EXISTS ix_card_field_changes_matter_id")
    op.execute("DROP TABLE IF EXISTS card_field_changes CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_card_scores_matter_id")
    op.execute("DROP TABLE IF EXISTS card_scores CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_honorarium_estimates_matter_id")
    op.execute("DROP TABLE IF EXISTS honorarium_estimates CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_revenue_projections_matter_id")
    op.execute("DROP TABLE IF EXISTS revenue_projections CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_campaign_attributions_matter_id")
    op.execute("DROP TABLE IF EXISTS campaign_attributions CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_matters_lead_source")
    op.execute("ALTER TABLE matters DROP COLUMN IF EXISTS executive_summary")
    op.execute("ALTER TABLE matters DROP COLUMN IF EXISTS lead_source")

    op.execute("DROP TYPE IF EXISTS card_field_change_actor_type_enum")
