"""add klm_knowledge_article_feedback (Onda 1 item 16 — feedback de artigos)

Fecha o elo "feedback só em localStorage" do backlog de dormentes
(docs/ONDA1_DORMENTES_BACKLOG.md item 16): `KnowledgePage.tsx` gravava
thumbs up/down só no navegador do usuário, sem visibilidade agregada de
quais artigos funcionam. `article_id` guarda o `slug` do catálogo estático
do front (`frontend/src/content/knowledge`), não um UUID — não existe
tabela de artigos no backend.

Revision ID: 58f5e2e75303
Revises: 45913a19da62
Create Date: 2026-07-26 00:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "58f5e2e75303"
down_revision: str | Sequence[str] | None = "45913a19da62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLE_NAME = "klm_knowledge_article_feedback"


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
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("article_id", sa.String(length=160), nullable=False),
        sa.Column(
            "value",
            sa.Enum("up", "down", name="klm_knowledge_feedback_value_enum"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "article_id",
            name="uq_klm_knowledge_feedback_workspace_user_article",
        ),
    )
    op.create_index(
        f"ix_{TABLE_NAME}_workspace_id", TABLE_NAME, ["workspace_id"]
    )
    op.create_index(f"ix_{TABLE_NAME}_user_id", TABLE_NAME, ["user_id"])
    op.create_index(f"ix_{TABLE_NAME}_article_id", TABLE_NAME, ["article_id"])
    op.create_index(
        "ix_klm_knowledge_feedback_workspace_article",
        TABLE_NAME,
        ["workspace_id", "article_id"],
    )
    _enable_rls()


def downgrade() -> None:
    _disable_rls()
    op.drop_index(
        "ix_klm_knowledge_feedback_workspace_article", table_name=TABLE_NAME
    )
    op.drop_index(f"ix_{TABLE_NAME}_article_id", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_user_id", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_workspace_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS klm_knowledge_feedback_value_enum")
