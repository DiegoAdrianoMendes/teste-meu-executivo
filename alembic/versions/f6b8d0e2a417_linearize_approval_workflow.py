"""Linearize pending approval workflow by explicit intent.

Revision ID: f6b8d0e2a417
Revises: e5a7c9d1f306
Create Date: 2026-07-20 15:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6b8d0e2a417"
down_revision: str | Sequence[str] | None = "e5a7c9d1f306"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_approval_pending_intent"
PENDING_PREDICATE = sa.text("status = 'pending'")
INTENT_COLUMN = "intent_key"


def upgrade() -> None:
    with op.batch_alter_table("approval_queue_items") as batch:
        batch.add_column(sa.Column(INTENT_COLUMN, sa.String(length=255), nullable=True))
    op.get_bind().execute(
        sa.text(
            """
            UPDATE approval_queue_items
            SET intent_key = 'legacy:' || CAST(id AS TEXT)
            WHERE intent_key IS NULL
            """
        )
    )
    with op.batch_alter_table("approval_queue_items") as batch:
        batch.alter_column(
            INTENT_COLUMN,
            existing_type=sa.String(length=255),
            nullable=False,
        )

    op.create_index(
        INDEX_NAME,
        "approval_queue_items",
        ["matter_id", "approval_type", INTENT_COLUMN],
        unique=True,
        postgresql_where=PENDING_PREDICATE,
        sqlite_where=PENDING_PREDICATE,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="approval_queue_items")
    with op.batch_alter_table("approval_queue_items") as batch:
        batch.drop_column(INTENT_COLUMN)
