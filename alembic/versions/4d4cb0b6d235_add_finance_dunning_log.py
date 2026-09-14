"""add finance_dunning_log (F4 — régua de cobrança / dunning)

Revision ID: 4d4cb0b6d235
Revises: ea59e9f09bf6
Create Date: 2026-07-25 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "4d4cb0b6d235"
down_revision: str | Sequence[str] | None = ("7d525e80f1c2", "ea59e9f09bf6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = ("finance_dunning_log",)


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "finance_dunning_log",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("charge_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "step",
            sa.Enum(
                "d1_lembrete", "d7_segunda_via", "d15_formal", name="finance_dunning_step_enum"
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "sent",
                "local_draft",
                "pending_approval",
                "blocked_gate_closed",
                name="finance_dunning_log_status_enum",
            ),
            nullable=False,
            server_default="local_draft",
        ),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("message_preview", sa.Text(), nullable=True),
        sa.Column("viewed_before_dispatch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["charge_id"], ["finance_charges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "charge_id", "step", name="uq_finance_dunning_log_charge_step"
        ),
    )
    op.create_index("ix_finance_dunning_log_workspace_id", "finance_dunning_log", ["workspace_id"])
    op.create_index("ix_finance_dunning_log_charge_id", "finance_dunning_log", ["charge_id"])
    op.create_index("ix_finance_dunning_log_status", "finance_dunning_log", ["status"])

    _enable_rls()


def downgrade() -> None:
    _disable_rls()

    op.drop_index("ix_finance_dunning_log_status", table_name="finance_dunning_log")
    op.drop_index("ix_finance_dunning_log_charge_id", table_name="finance_dunning_log")
    op.drop_index("ix_finance_dunning_log_workspace_id", table_name="finance_dunning_log")
    op.drop_table("finance_dunning_log")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS finance_dunning_log_status_enum")
        op.execute("DROP TYPE IF EXISTS finance_dunning_step_enum")
