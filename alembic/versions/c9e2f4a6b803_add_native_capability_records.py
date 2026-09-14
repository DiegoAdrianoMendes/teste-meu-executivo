"""add canonical native capability records

Revision ID: c9e2f4a6b803
Revises: b8d1e3f5a702
Create Date: 2026-07-20 10:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "c9e2f4a6b803"
down_revision: str | Sequence[str] | None = "b8d1e3f5a702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    record_state = sa.Enum(
        "active",
        "archived",
        name="oss_native_record_state_enum",
        native_enum=False,
        create_constraint=True,
    )
    op.create_table(
        "oss_native_records",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column(
            "scope_key",
            sa.String(length=80),
            nullable=False,
            server_default="capability",
        ),
        sa.Column("resource_id", sa.String(length=180), nullable=False),
        sa.Column("record_type", sa.String(length=120), nullable=False),
        sa.Column("last_operation", sa.String(length=180), nullable=False),
        sa.Column("state", record_state, nullable=False, server_default="active"),
        sa.Column("data", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_command_id", FlexibleUUID(), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["last_command_id"],
            ["oss_native_commands.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "capability",
            "scope_key",
            "resource_id",
            name="uq_oss_native_record_workspace_scope_resource",
        ),
    )
    op.create_index(
        "ix_oss_native_record_workspace_capability_state",
        "oss_native_records",
        ["workspace_id", "provider", "capability", "scope_key", "state"],
    )
    op.create_index(
        "ix_oss_native_record_workspace_type_updated",
        "oss_native_records",
        ["workspace_id", "record_type", "updated_at"],
    )
    outbox_status = sa.Enum(
        "pending",
        "processing",
        "completed",
        "failed",
        name="oss_native_outbox_status_enum",
        native_enum=False,
        create_constraint=True,
    )
    op.create_table(
        "oss_native_outbox_events",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("unit_id", sa.String(length=240), nullable=False),
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("topic", sa.String(length=180), nullable=False),
        sa.Column("dedupe_key", sa.String(length=180), nullable=False),
        sa.Column("payload", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", outbox_status, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=240), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "unit_id",
            "dedupe_key",
            name="uq_oss_native_outbox_workspace_unit_dedupe",
        ),
    )
    op.create_index(
        "ix_oss_native_outbox_status_available",
        "oss_native_outbox_events",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_oss_native_outbox_workspace_created",
        "oss_native_outbox_events",
        ["workspace_id", "created_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = "
            "NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute('ALTER TABLE "oss_native_records" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "oss_native_records" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY "workspace_current_id" ON "oss_native_records" '
            f"AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}) WITH CHECK ({predicate})"
        )
        op.execute('ALTER TABLE "oss_native_outbox_events" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "oss_native_outbox_events" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY "workspace_current_id" ON "oss_native_outbox_events" '
            f"AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "workspace_current_id" ON "oss_native_outbox_events"'
        )
        op.execute('ALTER TABLE "oss_native_outbox_events" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "oss_native_outbox_events" DISABLE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS "workspace_current_id" ON "oss_native_records"')
        op.execute('ALTER TABLE "oss_native_records" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "oss_native_records" DISABLE ROW LEVEL SECURITY')
    op.drop_index(
        "ix_oss_native_outbox_workspace_created",
        table_name="oss_native_outbox_events",
    )
    op.drop_index(
        "ix_oss_native_outbox_status_available",
        table_name="oss_native_outbox_events",
    )
    op.drop_table("oss_native_outbox_events")
    op.drop_index("ix_oss_native_record_workspace_type_updated", table_name="oss_native_records")
    op.drop_index(
        "ix_oss_native_record_workspace_capability_state",
        table_name="oss_native_records",
    )
    op.drop_table("oss_native_records")
