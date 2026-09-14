"""add workspace model credentials (BYOK de LLM por workspace)

Revision ID: b7d1f3a9c520
Revises: 8af3d982fb78
Create Date: 2026-07-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "b7d1f3a9c520"
down_revision: str | Sequence[str] | None = "8af3d982fb78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLE_NAME = "workspace_model_credentials"


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
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "api_key_hint",
            sa.String(length=8),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
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
            "provider",
            name="uq_workspace_model_credentials_workspace_provider",
        ),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'openai', 'gemini', 'vertex', 'maritaca')",
            name="ck_workspace_model_credentials_provider",
        ),
    )
    op.create_index(
        "ix_workspace_model_credentials_workspace_id",
        TABLE_NAME,
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_model_credentials_provider",
        TABLE_NAME,
        ["provider"],
    )
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index("ix_workspace_model_credentials_provider", table_name=TABLE_NAME)
    op.drop_index("ix_workspace_model_credentials_workspace_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
