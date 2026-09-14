"""Preserve effective-dated finance tax profiles."""

import sqlalchemy as sa

from alembic import op

revision = "mn5h8c1e0f64"
down_revision = "lm4g7b0c9d53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_finance_tax_profiles_workspace", "finance_tax_profiles", type_="unique")
    op.add_column("finance_tax_profiles", sa.Column("effective_until", sa.Date(), nullable=True))
    op.create_index(
        "ix_finance_tax_profiles_workspace_effective",
        "finance_tax_profiles",
        ["workspace_id", "effective_from"],
    )


def downgrade() -> None:
    # Legacy schema allowed one row/workspace only. Preserve the latest
    # effective row deterministically before restoring that constraint; the
    # historical rows cannot be represented by the legacy shape.
    op.execute(
        sa.text(
            """DELETE FROM finance_tax_profiles old
            USING finance_tax_profiles newer
            WHERE old.workspace_id = newer.workspace_id
              AND (old.effective_from, old.version, old.id)
                  < (newer.effective_from, newer.version, newer.id)"""
        )
    )
    op.drop_index("ix_finance_tax_profiles_workspace_effective", table_name="finance_tax_profiles")
    op.drop_column("finance_tax_profiles", "effective_until")
    op.create_unique_constraint(
        "uq_finance_tax_profiles_workspace", "finance_tax_profiles", ["workspace_id"]
    )
