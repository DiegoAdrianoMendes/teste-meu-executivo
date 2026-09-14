"""Durable, idempotent D-180/D-90/D-30/D-7 tax-reform review alerts.

Revision ID: jk2e5f6a8b31
Revises: ij1e4f5a7b20
"""

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision = "jk2e5f6a8b31"
down_revision = "ij1e4f5a7b20"
branch_labels = None
depends_on = None


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = NULLIF(current_setting('app.current_workspace_id', true), '')"
    )
    op.execute('ALTER TABLE "finance_tax_reform_alerts" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "finance_tax_reform_alerts" FORCE ROW LEVEL SECURITY')
    op.execute(
        '''CREATE POLICY "workspace_current_id" ON "finance_tax_reform_alerts"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING (''' + predicate + ") WITH CHECK (" + predicate + ")"
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute('DROP POLICY IF EXISTS "workspace_current_id" ON "finance_tax_reform_alerts"')
    op.execute('ALTER TABLE "finance_tax_reform_alerts" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "finance_tax_reform_alerts" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "finance_tax_reform_alerts",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tax_profile_id",
            FlexibleUUID(),
            sa.ForeignKey("finance_tax_profiles.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "human_decision_id",
            FlexibleUUID(),
            sa.ForeignKey("human_decisions.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("schedule_key", sa.String(255), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("milestone_key", sa.String(80), nullable=False),
        sa.Column("milestone_date", sa.Date(), nullable=False),
        sa.Column("offset_days", sa.Integer(), nullable=False),
        sa.Column("trigger_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="called"),
        sa.Column("failure_code", sa.String(120)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workspace_id", "schedule_key", name="uq_finance_tax_reform_alert_workspace_key"
        ),
    )
    op.create_index(
        "ix_finance_tax_reform_alerts_workspace_id",
        "finance_tax_reform_alerts",
        ["workspace_id"],
    )
    op.create_index(
        "ix_finance_tax_reform_alerts_tax_profile_id",
        "finance_tax_reform_alerts",
        ["tax_profile_id"],
    )
    op.create_index(
        "ix_finance_tax_reform_alert_workspace_status",
        "finance_tax_reform_alerts",
        ["workspace_id", "status"],
    )
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index(
        "ix_finance_tax_reform_alert_workspace_status",
        table_name="finance_tax_reform_alerts",
    )
    op.drop_index(
        "ix_finance_tax_reform_alerts_tax_profile_id",
        table_name="finance_tax_reform_alerts",
    )
    op.drop_index(
        "ix_finance_tax_reform_alerts_workspace_id",
        table_name="finance_tax_reform_alerts",
    )
    op.drop_table("finance_tax_reform_alerts")
