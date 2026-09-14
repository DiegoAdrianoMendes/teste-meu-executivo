"""Add versioned Meta commercial rate cards.

Revision ID: f1c3e5a7b902
Revises: e8b0c2d4f615
Create Date: 2026-07-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "f1c3e5a7b902"
down_revision: str | Sequence[str] | None = "e8b0c2d4f615"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "meta_commercial_rate_cards"
META_PRICING_SOURCE_URL = "https://developers.facebook.com/docs/whatsapp/pricing"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("rate_card_key", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("commercial_rate", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
            "commercial_rate >= 0",
            name="ck_meta_commercial_rate_nonnegative",
        ),
        sa.CheckConstraint(
            "country_code = '*' OR "
            "(length(country_code) = 2 AND country_code = upper(country_code))",
            name="ck_meta_commercial_rate_country_code",
        ),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_meta_commercial_rate_currency",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_meta_commercial_rate_effective_window",
        ),
        sa.CheckConstraint(
            f"source_url = '{META_PRICING_SOURCE_URL}'",
            name="ck_meta_commercial_rate_source_url",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rate_card_key",
            name="uq_meta_commercial_rate_cards_key",
        ),
    )
    op.create_index(
        "ix_meta_commercial_rate_lookup",
        TABLE_NAME,
        ["category", "country_code", "is_active", "effective_from"],
    )
    op.create_index(
        "ix_meta_commercial_rate_history",
        TABLE_NAME,
        ["category", "country_code", "currency", "effective_from"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        op.execute(
            f"""
            ALTER TABLE {TABLE_NAME}
            ADD CONSTRAINT ex_meta_commercial_rate_effective_window
            EXCLUDE USING gist (
                category WITH =,
                country_code WITH =,
                currency WITH =,
                tstzrange(effective_from, effective_to, '[)') WITH &&
            )
            """
        )


def downgrade() -> None:
    op.drop_index("ix_meta_commercial_rate_history", table_name=TABLE_NAME)
    op.drop_index("ix_meta_commercial_rate_lookup", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
