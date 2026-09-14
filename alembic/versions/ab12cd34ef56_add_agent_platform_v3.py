"""Agent platform V3 durable deployment, node registry and human decisions."""

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision = "ab12cd34ef56"
down_revision = "ff7a9c1d3e86"
branch_labels = None
depends_on = None
TENANT_TABLES = (
    "agent_deployments",
    "flow_node_definitions",
    "human_decisions",
    "human_notification_deliveries",
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


def upgrade() -> None:
    op.create_table(
        "agent_deployments",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_key", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("config", FlexibleJSON(), nullable=False),
        sa.Column("gates", FlexibleJSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workspace_id", "agent_key", "version", name="uq_agent_deployments_key_version"
        ),
    )
    op.create_table(
        "flow_node_definitions",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_type", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema", FlexibleJSON(), nullable=False),
        sa.Column("output_schema", FlexibleJSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workspace_id", "node_type", "version", name="uq_flow_node_definitions_type_version"
        ),
    )
    op.create_table(
        "human_decisions",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_run_id", FlexibleUUID(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")
        ),
        sa.Column(
            "flow_run_id", FlexibleUUID(), sa.ForeignKey("flow_runs.id", ondelete="SET NULL")
        ),
        sa.Column("decision_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("options", FlexibleJSON(), nullable=False),
        sa.Column("decision", FlexibleJSON()),
        sa.Column(
            "decided_by_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("required_roles", FlexibleJSON(), nullable=False),
        sa.Column("required_skills", FlexibleJSON(), nullable=False),
        sa.Column(
            "assigned_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("assigned_team_id", FlexibleUUID()),
        sa.Column("owner_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("routing_path", FlexibleJSON(), nullable=False),
        sa.Column("max_hops", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "decision_key", name="uq_human_decisions_key"),
    )
    op.create_table(
        "human_notification_deliveries",
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column(
            "workspace_id",
            FlexibleUUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            FlexibleUUID(),
            sa.ForeignKey("human_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_user_id",
            FlexibleUUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient_kind", sa.String(24), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("event_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_receipt_id", sa.String(255)),
        sa.Column("last_error_code", sa.String(120)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "decision_id",
            "recipient_user_id",
            "channel",
            "event_key",
            name="uq_human_delivery_recipient_event",
        ),
    )
    for table, columns in {
        "agent_deployments": ("workspace_id", "agent_key", "status"),
        "flow_node_definitions": ("workspace_id", "node_type"),
        "human_decisions": ("workspace_id", "agent_run_id", "flow_run_id", "status"),
        "human_notification_deliveries": (
            "workspace_id",
            "decision_id",
            "recipient_user_id",
            "status",
        ),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    for table in TENANT_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        _disable_rls(table)
    for table, columns in {
        "human_notification_deliveries": (
            "status",
            "recipient_user_id",
            "decision_id",
            "workspace_id",
        ),
        "human_decisions": ("status", "flow_run_id", "agent_run_id", "workspace_id"),
        "flow_node_definitions": ("node_type", "workspace_id"),
        "agent_deployments": ("status", "agent_key", "workspace_id"),
    }.items():
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
    op.drop_table("human_notification_deliveries")
    op.drop_table("human_decisions")
    op.drop_table("flow_node_definitions")
    op.drop_table("agent_deployments")
