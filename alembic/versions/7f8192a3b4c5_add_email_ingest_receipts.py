"""add transactional e-mail ingest receipts

Revision ID: 7f8192a3b4c5
Revises: 6f708192a3b4
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "7f8192a3b4c5"
down_revision: str | None = "6f708192a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "klm_email_ingest_receipts",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=180), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
            server_default="processing",
        ),
        sa.Column(
            "result",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider_message_id",
            name="uq_klm_email_receipt_workspace_message",
        ),
    )
    op.create_index(
        "ix_klm_email_ingest_receipts_workspace_id",
        "klm_email_ingest_receipts",
        ["workspace_id"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = "
            "NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute(
            'ALTER TABLE "klm_email_ingest_receipts" ENABLE ROW LEVEL SECURITY'
        )
        op.execute(
            'ALTER TABLE "klm_email_ingest_receipts" FORCE ROW LEVEL SECURITY'
        )
        op.execute(
            f"""
            CREATE POLICY "workspace_current_id" ON "klm_email_ingest_receipts"
            AS PERMISSIVE
            FOR ALL
            TO PUBLIC
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "workspace_current_id" '
            'ON "klm_email_ingest_receipts"'
        )
        op.execute(
            'ALTER TABLE "klm_email_ingest_receipts" NO FORCE ROW LEVEL SECURITY'
        )
        op.execute(
            'ALTER TABLE "klm_email_ingest_receipts" DISABLE ROW LEVEL SECURITY'
        )
    op.drop_index(
        "ix_klm_email_ingest_receipts_workspace_id",
        table_name="klm_email_ingest_receipts",
    )
    op.drop_table("klm_email_ingest_receipts")
