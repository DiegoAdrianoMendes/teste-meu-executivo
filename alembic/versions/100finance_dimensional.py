"""Finance dimensional planning tables."""

import sqlalchemy as sa

from alembic import op

revision = "100finance_dimensional"
down_revision = "ff7a9c1d3e86"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "finance_cost_centers",
    "finance_allocations",
    "finance_forecasts",
    "finance_valuations",
    "finance_tax_profiles",
    "finance_export_jobs",
)


def _enable_rls(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = NULLIF(" "current_setting('app.current_workspace_id', true), '')"
    )
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""CREATE POLICY "workspace_current_id" ON "{table}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})"""
    )


def _disable_rls(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "workspace_current_id" ON "{table}"')
    op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
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
    ]


def upgrade() -> None:
    op.create_table(
        "finance_cost_centers",
        *_base_columns(),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("type", sa.String(40), server_default="operational", nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["finance_cost_centers.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workspace_id", "code", name="uq_finance_cost_centers_workspace_code"),
    )
    op.create_table(
        "finance_allocations",
        *_base_columns(),
        sa.Column(
            "finance_entry_id",
            sa.Uuid(),
            sa.ForeignKey("finance_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cost_center_id",
            sa.Uuid(),
            sa.ForeignKey("finance_cost_centers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="SET NULL")),
        sa.Column(
            "contract_id",
            sa.Uuid(),
            sa.ForeignKey("contract_billing_proposals.id", ondelete="SET NULL"),
        ),
        sa.Column("matter_id", sa.Uuid(), sa.ForeignKey("matters.id", ondelete="SET NULL")),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("klm_workspace_teams.id", ondelete="SET NULL")
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("percentage", sa.Numeric(7, 4), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2)),
        sa.Column("source", sa.String(40), server_default="manual", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("audit_metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.CheckConstraint(
            "percentage > 0 AND percentage <= 100", name="ck_finance_allocation_percentage"
        ),
    )
    op.create_index(
        "ix_finance_allocations_finance_entry_id",
        "finance_allocations",
        ["finance_entry_id"],
    )
    op.create_index(
        "ix_finance_allocations_cost_center_id",
        "finance_allocations",
        ["cost_center_id"],
    )
    op.create_table(
        "finance_forecasts",
        *_base_columns(),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("revenue", sa.Numeric(15, 2), server_default="0", nullable=False),
        sa.Column("expense", sa.Numeric(15, 2), server_default="0", nullable=False),
        sa.Column("scenario", sa.String(30), server_default="base", nullable=False),
        sa.Column("horizon", sa.String(20), server_default="monthly", nullable=False),
        sa.Column("assumptions", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("period_end >= period_start", name="ck_finance_forecast_period"),
    )
    op.create_table(
        "finance_valuations",
        *_base_columns(),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("enterprise_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("premise_quality", sa.String(20), server_default="unverified", nullable=False),
        sa.Column("assumptions", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_table(
        "finance_tax_profiles",
        *_base_columns(),
        sa.Column("regime", sa.String(40), nullable=False),
        sa.Column("tax_rate", sa.Numeric(7, 4), server_default="0", nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("municipality", sa.String(120)),
        sa.Column("sources", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("reform_calendar", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("settings", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("workspace_id", name="uq_finance_tax_profiles_workspace"),
    )
    op.create_table(
        "finance_export_jobs",
        *_base_columns(),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False),
        sa.Column("drill_context", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("storage_path", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("error_detail", sa.Text()),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("row_count", sa.Integer()),
        sa.Column("snapshot_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="ck_finance_export_progress"),
    )
    for table in TENANT_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        _disable_rls(table)
    op.drop_table("finance_export_jobs")
    op.drop_table("finance_tax_profiles")
    op.drop_table("finance_valuations")
    op.drop_table("finance_forecasts")
    op.drop_index(
        "ix_finance_allocations_cost_center_id",
        table_name="finance_allocations",
    )
    op.drop_index(
        "ix_finance_allocations_finance_entry_id",
        table_name="finance_allocations",
    )
    op.drop_table("finance_allocations")
    op.drop_table("finance_cost_centers")
