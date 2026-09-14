"""add billing, platform token tracking, agent run token fields, and soft-delete/platform-admin

Revision ID: a1b2c3d4e5f6
Revises: f2a4b6c8d001
Create Date: 2026-04-04 00:00:00.000000

Adds:
  - plans table (Plan model)
  - subscriptions table (Subscription model)
  - token_budgets table (TokenBudget model)
  - platform_token_usage table (PlatformTokenUsage model)
  - agent_runs: model_used, ai_provider, input_tokens, output_tokens, cost_usd
  - finance_entries: deleted_at (SoftDeleteMixin)
  - users: is_platform_admin
  - workspaces: subscription + token_budget FK references handled by tables above
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f2a4b6c8d001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enum types via SQL puro — sa.Enum/postgresql.ENUM não emite CREATE TYPE
    # quando usamos op.execute() + create_type=False no Column ──────────────
    op.execute("CREATE TYPE plan_tier_enum AS ENUM ('free', 'starter', 'pro', 'business', 'enterprise')")
    op.execute("CREATE TYPE billing_interval_enum AS ENUM ('monthly', 'annually')")
    op.execute("CREATE TYPE subscription_status_enum AS ENUM ('trialing', 'active', 'past_due', 'cancelled', 'unpaid', 'paused')")
    op.execute("CREATE TYPE token_alert_level_enum AS ENUM ('none', 'warning', 'critical', 'exceeded')")

    # Usar postgresql.ENUM(create_type=False) — não emite CREATE TYPE adicional
    plan_tier      = postgresql.ENUM('free', 'starter', 'pro', 'business', 'enterprise', name='plan_tier_enum', create_type=False)
    sub_status     = postgresql.ENUM('trialing', 'active', 'past_due', 'cancelled', 'unpaid', 'paused', name='subscription_status_enum', create_type=False)
    bill_interval  = postgresql.ENUM('monthly', 'annually', name='billing_interval_enum', create_type=False)
    token_alert    = postgresql.ENUM('none', 'warning', 'critical', 'exceeded', name='token_alert_level_enum', create_type=False)

    # ── plans ───────────────────────────────────────────────────────────────
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tier", plan_tier, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_monthly_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_annually_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_users", sa.Integer(), nullable=True),
        sa.Column("max_matters", sa.Integer(), nullable=True),
        sa.Column("max_tokens_per_month", sa.Integer(), nullable=True),
        sa.Column("max_ai_agents", sa.Integer(), nullable=True),
        sa.Column("features", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("stripe_product_id", sa.String(120), nullable=True),
        sa.Column("stripe_price_monthly_id", sa.String(120), nullable=True),
        sa.Column("stripe_price_annually_id", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tier", name="uq_plans_tier"),
    )

    # ── subscriptions ───────────────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_customer_id", sa.String(120), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(120), nullable=True),
        sa.Column("stripe_payment_method_id", sa.String(120), nullable=True),
        sa.Column("status", sub_status, nullable=False, server_default="trialing"),
        sa.Column("interval", bill_interval, nullable=False, server_default="monthly"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="BRL"),
        sa.Column("last_stripe_event", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_subscription_workspace"),
    )
    op.create_index("ix_subscriptions_stripe_sub_id", "subscriptions", ["stripe_subscription_id"])
    op.create_index("ix_subscriptions_stripe_customer_id", "subscriptions", ["stripe_customer_id"])
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])

    # ── token_budgets ───────────────────────────────────────────────────────
    op.create_table(
        "token_budgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monthly_limit", sa.Integer(), nullable=True),
        sa.Column("alert_at_pct", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("hard_limit", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("current_month_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_month_cost_usd", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("current_month_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alert_level", token_alert, nullable=False, server_default="none"),
        sa.Column("last_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_token_budget_workspace"),
    )

    # ── platform_token_usage ────────────────────────────────────────────────
    op.create_table(
        "platform_token_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_used", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("usage_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "model_used", "provider", "usage_date", name="uq_token_usage_workspace_model_date"),
    )
    op.create_index("ix_token_usage_date", "platform_token_usage", ["usage_date"])
    op.create_index("ix_token_usage_workspace", "platform_token_usage", ["workspace_id"])

    # ── agent_runs: token tracking fields ───────────────────────────────────
    op.add_column("agent_runs", sa.Column("model_used", sa.String(120), nullable=True))
    op.add_column("agent_runs", sa.Column("ai_provider", sa.String(60), nullable=True))
    op.add_column("agent_runs", sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_runs", sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_runs", sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"))

    # ── finance_entries: soft-delete ────────────────────────────────────────
    op.add_column("finance_entries", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    # ── users: platform admin flag ──────────────────────────────────────────
    op.add_column("users", sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("users", "is_platform_admin")
    op.drop_column("finance_entries", "deleted_at")

    op.drop_column("agent_runs", "cost_usd")
    op.drop_column("agent_runs", "output_tokens")
    op.drop_column("agent_runs", "input_tokens")
    op.drop_column("agent_runs", "ai_provider")
    op.drop_column("agent_runs", "model_used")

    op.drop_index("ix_token_usage_workspace", table_name="platform_token_usage")
    op.drop_index("ix_token_usage_date", table_name="platform_token_usage")
    op.drop_table("platform_token_usage")

    op.drop_table("token_budgets")

    op.drop_index("ix_subscriptions_status", table_name="subscriptions")
    op.drop_index("ix_subscriptions_stripe_customer_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_stripe_sub_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_table("plans")

    op.execute("DROP TYPE IF EXISTS token_alert_level_enum")
    op.execute("DROP TYPE IF EXISTS subscription_status_enum")
    op.execute("DROP TYPE IF EXISTS billing_interval_enum")
    op.execute("DROP TYPE IF EXISTS plan_tier_enum")
