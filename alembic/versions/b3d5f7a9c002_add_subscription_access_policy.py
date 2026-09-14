"""Add the local subscription access projection and audit trail.

Revision ID: b3d5f7a9c002
Revises: a2c4e6f8b001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "b3d5f7a9c002"
down_revision: str | Sequence[str] | None = "a2c4e6f8b001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
TENANT_TABLES = (
    "subscription_access_states",
    "subscription_access_transitions",
)
ACCESS_STATES = "'trial', 'active', 'grace', 'past_due', 'canceled', 'suspended'"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )
    for table_name in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''
            CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})
            '''
        )


def upgrade() -> None:
    op.create_table(
        "subscription_access_states",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("subscription_id", FlexibleUUID(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_ends_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            f"status IN ({ACCESS_STATES})",
            name="ck_subscription_access_status",
        ),
        sa.CheckConstraint(
            "state_version >= 1",
            name="ck_subscription_access_version_positive",
        ),
        sa.CheckConstraint(
            "((status = 'trial' AND trial_ends_at IS NOT NULL) OR "
            "(status <> 'trial' AND trial_ends_at IS NULL))",
            name="ck_subscription_access_trial_window",
        ),
        sa.CheckConstraint(
            "((status = 'grace' AND grace_started_at IS NOT NULL "
            "AND grace_ends_at IS NOT NULL AND grace_ends_at > grace_started_at) OR "
            "(status <> 'grace' AND grace_started_at IS NULL AND grace_ends_at IS NULL))",
            name="ck_subscription_access_grace_window",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_subscription_access_workspace"),
    )
    op.create_index(
        "ix_subscription_access_status",
        "subscription_access_states",
        ["status"],
    )
    op.create_index(
        "ix_subscription_access_subscription_id",
        "subscription_access_states",
        ["subscription_id"],
    )

    op.create_table(
        "subscription_access_transitions",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("access_state_id", FlexibleUUID(), nullable=False),
        sa.Column("subscription_id", FlexibleUUID(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({ACCESS_STATES})",
            name="ck_subscription_access_transition_from_status",
        ),
        sa.CheckConstraint(
            f"to_status IN ({ACCESS_STATES})",
            name="ck_subscription_access_transition_to_status",
        ),
        sa.CheckConstraint(
            "((applied = true AND to_version = from_version + 1) OR "
            "(applied = false AND to_version = from_version))",
            name="ck_subscription_access_transition_version_step",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["access_state_id"], ["subscription_access_states.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_subscription_access_transition_idempotency",
        ),
    )
    op.create_index(
        "ix_subscription_access_transition_workspace_occurred",
        "subscription_access_transitions",
        ["workspace_id", "occurred_at"],
    )
    op.create_index(
        "ix_subscription_access_transition_state_version",
        "subscription_access_transitions",
        ["access_state_id", "to_version"],
    )
    _enable_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in reversed(TENANT_TABLES):
            op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index(
        "ix_subscription_access_transition_state_version",
        table_name="subscription_access_transitions",
    )
    op.drop_index(
        "ix_subscription_access_transition_workspace_occurred",
        table_name="subscription_access_transitions",
    )
    op.drop_table("subscription_access_transitions")
    op.drop_index(
        "ix_subscription_access_subscription_id",
        table_name="subscription_access_states",
    )
    op.drop_index(
        "ix_subscription_access_status",
        table_name="subscription_access_states",
    )
    op.drop_table("subscription_access_states")
