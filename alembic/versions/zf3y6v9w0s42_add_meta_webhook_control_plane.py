"""Add durable Meta webhook control-plane state.

Revision ID: zf3y6v9w0s42
Revises: ze2x5u8v9r31
"""

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision = "zf3y6v9w0s42"
down_revision = "ze2x5u8v9r31"
branch_labels = None
depends_on = None

TABLES = (
    "meta_webhook_receipts",
    "meta_webhook_operational_states",
    "meta_webhook_subscription_states",
    "meta_webhook_automation_policies",
    "meta_webhook_subscription_jobs",
)


def _tenant_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    workspace_scope = (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )
    op.execute(
        f'''CREATE POLICY "{table}_workspace_isolation" ON "{table}"
        USING ({workspace_scope})
        WITH CHECK ({workspace_scope})'''
    )


def upgrade() -> None:
    op.create_table(
        "meta_webhook_receipts",
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("provider_envelope_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("field", sa.String(80), nullable=False),
        sa.Column("item_fingerprint", sa.String(64), nullable=False),
        sa.Column("handler_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(40), nullable=False, server_default="received"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("projection_type", sa.String(80), nullable=True),
        sa.Column("projection_id", FlexibleUUID(), nullable=True),
        sa.Column("sanitized_error_code", sa.String(120), nullable=True),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_envelope_id"],
            ["klm_omnichannel_provider_webhook_envelopes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["whatsapp_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_envelope_id",
            "field",
            "item_fingerprint",
            name="uq_meta_webhook_receipt_delivery_item",
        ),
    )
    op.create_index(
        "ix_meta_webhook_receipts_workspace_status",
        "meta_webhook_receipts",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_meta_webhook_receipts_connection_field",
        "meta_webhook_receipts",
        ["connection_id", "field"],
    )
    op.create_index(
        "ix_meta_webhook_receipts_observed", "meta_webhook_receipts", ["observed_at", "id"]
    )

    op.create_table(
        "meta_webhook_operational_states",
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("field", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_ref", sa.String(160), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state_code", sa.String(120), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column(
            "sanitized_snapshot",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "projected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["whatsapp_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "field",
            "resource_type",
            "resource_ref",
            name="uq_meta_webhook_operational_resource",
        ),
    )
    op.create_index(
        "ix_meta_webhook_states_workspace_field",
        "meta_webhook_operational_states",
        ["workspace_id", "field"],
    )
    op.create_index(
        "ix_meta_webhook_states_connection_severity",
        "meta_webhook_operational_states",
        ["connection_id", "severity"],
    )

    op.create_table(
        "meta_webhook_subscription_states",
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("field", sa.String(80), nullable=False),
        sa.Column("desired", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("applicability", sa.String(32), nullable=False, server_default="waba_level"),
        sa.Column("provider_state", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sanitized_error_code", sa.String(120), nullable=True),
        sa.Column("evidence_ref", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["whatsapp_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "field", name="uq_meta_webhook_subscription_connection_field"
        ),
    )
    op.create_index(
        "ix_meta_webhook_subscriptions_workspace_state",
        "meta_webhook_subscription_states",
        ["workspace_id", "provider_state"],
    )

    op.create_table(
        "meta_webhook_automation_policies",
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("field", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mode", sa.String(32), nullable=False, server_default="observe_only"),
        sa.Column("action_key", sa.String(120), nullable=True),
        sa.Column("minimum_severity", sa.String(20), nullable=False, server_default="warning"),
        sa.Column("owner_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_accepted_by", FlexibleUUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_accepted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "field", name="uq_meta_webhook_policy_workspace_field"),
    )
    op.create_index(
        "ix_meta_webhook_policies_workspace_mode",
        "meta_webhook_automation_policies",
        ["workspace_id", "mode"],
    )

    op.create_table(
        "meta_webhook_subscription_jobs",
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("requested_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("attempted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "result_summary", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("sanitized_error_code", sa.String(120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_meta_webhook_subscription_jobs_workspace_status",
        "meta_webhook_subscription_jobs",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_meta_webhook_subscription_jobs_created",
        "meta_webhook_subscription_jobs",
        ["created_at", "id"],
    )

    for table in TABLES:
        _tenant_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
