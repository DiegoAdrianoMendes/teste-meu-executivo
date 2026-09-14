"""Add versioned dimensional finance budget lines."""

import sqlalchemy as sa

from alembic import op

revision = "b7d4e2f9a610"
down_revision = "8f3c1a9d2b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_budget_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "cost_center_id",
            sa.Uuid(),
            sa.ForeignKey("finance_cost_centers.id", ondelete="SET NULL"),
        ),
        sa.Column("category", sa.String(80)),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("assumptions", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("period_end >= period_start", name="ck_finance_budget_period"),
        sa.CheckConstraint("amount >= 0", name="ck_finance_budget_amount"),
        sa.CheckConstraint(
            "entry_type IN ('receivable', 'expense', 'refund')",
            name="ck_finance_budget_entry_type",
        ),
    )
    op.create_index(
        "ix_finance_budget_workspace_period",
        "finance_budget_lines",
        ["workspace_id", "period_start", "period_end"],
    )
    op.create_index(
        "ix_finance_budget_workspace_center",
        "finance_budget_lines",
        ["workspace_id", "cost_center_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute('ALTER TABLE "finance_budget_lines" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "finance_budget_lines" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""CREATE POLICY "workspace_current_id" ON "finance_budget_lines"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS "workspace_current_id" ON "finance_budget_lines"')
        op.execute('ALTER TABLE "finance_budget_lines" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "finance_budget_lines" DISABLE ROW LEVEL SECURITY')
    op.drop_index("ix_finance_budget_workspace_center", table_name="finance_budget_lines")
    op.drop_index("ix_finance_budget_workspace_period", table_name="finance_budget_lines")
    op.drop_table("finance_budget_lines")
