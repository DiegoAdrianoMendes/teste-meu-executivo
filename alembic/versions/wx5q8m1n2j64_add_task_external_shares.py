"""Add least-privilege external shares for legal tasks.

Revision ID: wx5q8m1n2j64
Revises: uv4p7l9m0h53
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision = "wx5q8m1n2j64"
down_revision = "uv4p7l9m0h53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = FlexibleUUID()
    op.create_table(
        "task_external_shares",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("task_id", uuid_type, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("title_snapshot", sa.String(500), nullable=False),
        sa.Column("status_snapshot", sa.String(32), nullable=False),
        sa.Column("due_at_snapshot", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_context_snapshot", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", uuid_type, nullable=True),
        sa.Column("revoked_by_user_id", uuid_type, nullable=True),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_external_shares_workspace_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_task_external_shares_token_hash"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key_hash",
            name="uq_task_external_shares_workspace_idempotency",
        ),
    )
    op.create_index(
        "ix_task_external_shares_workspace_id", "task_external_shares", ["workspace_id"]
    )
    op.create_index("ix_task_external_shares_task_id", "task_external_shares", ["task_id"])
    op.create_index(
        "ix_task_external_shares_workspace_task",
        "task_external_shares",
        ["workspace_id", "task_id"],
    )
    op.create_index("ix_task_external_shares_expires_at", "task_external_shares", ["expires_at"])
    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute('ALTER TABLE "task_external_shares" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "task_external_shares" FORCE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY "workspace_current_id" ON "task_external_shares"
            AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}) WITH CHECK ({predicate})""")


def downgrade() -> None:
    op.drop_table("task_external_shares")
