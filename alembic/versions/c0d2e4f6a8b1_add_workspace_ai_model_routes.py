"""add workspace ai model routes (roteamento de modelos por classe de tarefa)

Revision ID: c0d2e4f6a8b1
Revises: b9c1d3e5f7a2
Create Date: 2026-07-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "c0d2e4f6a8b1"
down_revision: str | Sequence[str] | None = "b9c1d3e5f7a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLE_NAME = "workspace_ai_model_routes"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{RLS_POLICY}" ON "{TABLE_NAME}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{TABLE_NAME}"')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", FlexibleUUID(), primary_key=True),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("task_class", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "task_class",
            name="uq_workspace_ai_model_routes_workspace_task_class",
        ),
        sa.CheckConstraint(
            "task_class IN ('simple', 'medium', 'complex', 'premium')",
            name="ck_workspace_ai_model_routes_task_class",
        ),
    )
    op.create_index(
        "ix_workspace_ai_model_routes_workspace_id",
        TABLE_NAME,
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_ai_model_routes_task_class",
        TABLE_NAME,
        ["task_class"],
    )
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index("ix_workspace_ai_model_routes_task_class", table_name=TABLE_NAME)
    op.drop_index("ix_workspace_ai_model_routes_workspace_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
