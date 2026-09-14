"""Add exact tenant-scoped OSS FunctionUnit runtime events.

Revision ID: e0b1c2d3f408
Revises: d9a7e5c3b102
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "e0b1c2d3f408"
down_revision: str | Sequence[str] | None = "d9a7e5c3b102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = sa.Enum(
        "succeeded",
        "configured",
        "simulated",
        "blocked_external",
        name="oss_function_unit_event_status_enum",
        native_enum=False,
        create_constraint=True,
    )
    op.create_table(
        "oss_function_unit_events",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("unit_id", sa.String(length=240), nullable=False),
        sa.Column("contract_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("semantic_profile", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("resource_id", sa.String(length=180), nullable=False),
        sa.Column("payload", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("result", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "external_network_executed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "unit_id",
            "idempotency_key",
            name="uq_oss_function_unit_event_idempotency",
        ),
    )
    op.create_index(
        "ix_oss_function_unit_event_workspace_unit_occurred",
        "oss_function_unit_events",
        ["workspace_id", "unit_id", "occurred_at"],
    )
    op.create_index(
        "ix_oss_function_unit_event_workspace_resource",
        "oss_function_unit_events",
        ["workspace_id", "resource_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = "
            "NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute('ALTER TABLE "oss_function_unit_events" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "oss_function_unit_events" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY "workspace_current_id" ON "oss_function_unit_events" '
            f"AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}) WITH CHECK ({predicate})"
        )
        op.execute(
            """
            CREATE FUNCTION reject_oss_function_unit_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'oss_function_unit_events are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_oss_function_unit_events_immutable
            BEFORE UPDATE OR DELETE ON oss_function_unit_events
            FOR EACH ROW EXECUTE FUNCTION reject_oss_function_unit_event_mutation()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_oss_function_unit_events_immutable "
            "ON oss_function_unit_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_oss_function_unit_event_mutation()")
        op.execute(
            'DROP POLICY IF EXISTS "workspace_current_id" ON "oss_function_unit_events"'
        )
        op.execute('ALTER TABLE "oss_function_unit_events" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "oss_function_unit_events" DISABLE ROW LEVEL SECURITY')
    op.drop_index(
        "ix_oss_function_unit_event_workspace_resource",
        table_name="oss_function_unit_events",
    )
    op.drop_index(
        "ix_oss_function_unit_event_workspace_unit_occurred",
        table_name="oss_function_unit_events",
    )
    op.drop_table("oss_function_unit_events")
