"""Add tenant commercial change requests.

Revision ID: zd1w4t7u8q20
Revises: zc0v3s6t7p19
"""

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision = "zd1w4t7u8q20"
down_revision = "zc0v3s6t7p19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = FlexibleUUID()
    op.create_table(
        "commercial_account_requests",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("requested_by_user_id", uuid_type),
        sa.Column("request_type", sa.String(40), nullable=False),
        sa.Column("target_sku", sa.String(80)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column(
            "preview_snapshot", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolved_by_user_id", uuid_type),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_commercial_account_request_idempotency"
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_commercial_account_request_workspace"),
        sa.CheckConstraint("version >= 1", name="ck_commercial_account_request_version"),
        sa.CheckConstraint(
            "request_type IN ('change_base_plan','activate_legal',"
            "'cancel_legal','cancel_contract')",
            name="ck_commercial_account_request_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','resolved','rejected','cancelled')",
            name="ck_commercial_account_request_status",
        ),
    )
    op.create_index(
        "ix_commercial_account_requests_workspace_status",
        "commercial_account_requests",
        ["workspace_id", "status", "created_at"],
    )
    op.create_index(
        "ix_commercial_account_requests_status_created",
        "commercial_account_requests",
        ["status", "created_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute('ALTER TABLE "commercial_account_requests" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "commercial_account_requests" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""CREATE POLICY "workspace_current_id" ON "commercial_account_requests"
            AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}) WITH CHECK ({predicate})"""
        )


def downgrade() -> None:
    op.drop_table("commercial_account_requests")
