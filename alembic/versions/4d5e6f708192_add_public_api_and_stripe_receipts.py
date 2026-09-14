"""add Public API mutation and Stripe webhook receipts

Revision ID: 4d5e6f708192
Revises: 3c4d5e6f7081
Create Date: 2026-07-16 20:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "4d5e6f708192"
down_revision: str | Sequence[str] | None = "3c4d5e6f7081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("last_stripe_event_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("last_stripe_event_created", sa.BigInteger(), nullable=True),
    )
    op.drop_index("ix_subscriptions_stripe_sub_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_stripe_customer_id", table_name="subscriptions")
    op.create_index(
        "ux_subscriptions_stripe_subscription_id_not_null",
        "subscriptions",
        ["stripe_subscription_id"],
        unique=True,
        postgresql_where=sa.text("stripe_subscription_id IS NOT NULL"),
        sqlite_where=sa.text("stripe_subscription_id IS NOT NULL"),
    )
    op.create_index(
        "ux_subscriptions_stripe_customer_id_not_null",
        "subscriptions",
        ["stripe_customer_id"],
        unique=True,
        postgresql_where=sa.text("stripe_customer_id IS NOT NULL"),
        sqlite_where=sa.text("stripe_customer_id IS NOT NULL"),
    )

    op.create_table(
        "public_api_mutation_receipts",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("token_id", FlexibleUUID(), nullable=False),
        sa.Column("operation", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", FlexibleJSON(), nullable=True),
        sa.Column("outbox_payload", FlexibleJSON(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uncertain_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["token_id"],
            ["workspace_public_api_tokens.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "token_id",
            "operation",
            "idempotency_key",
            name="uq_public_api_receipt_workspace_token_operation_key",
        ),
    )
    op.create_index(
        "ix_public_api_receipts_workspace_token_created",
        "public_api_mutation_receipts",
        ["workspace_id", "token_id", "created_at"],
    )
    op.create_index(
        "ix_public_api_receipts_state_updated",
        "public_api_mutation_receipts",
        ["state", "updated_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = "
            "NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute(
            'ALTER TABLE "public_api_mutation_receipts" ENABLE ROW LEVEL SECURITY'
        )
        op.execute(
            'ALTER TABLE "public_api_mutation_receipts" FORCE ROW LEVEL SECURITY'
        )
        op.execute(
            f"""
            CREATE POLICY "workspace_current_id" ON "public_api_mutation_receipts"
            AS PERMISSIVE
            FOR ALL
            TO PUBLIC
            USING ({predicate})
            WITH CHECK ({predicate})
            """
        )

    op.create_table(
        "stripe_webhook_event_receipts",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("event_created", sa.BigInteger(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=24),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("subscription_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_stripe_webhook_receipts_event_id"),
    )
    op.create_index(
        "ix_stripe_webhook_receipts_state_received",
        "stripe_webhook_event_receipts",
        ["state", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_stripe_webhook_receipts_state_received",
        table_name="stripe_webhook_event_receipts",
    )
    op.drop_table("stripe_webhook_event_receipts")

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "workspace_current_id" '
            'ON "public_api_mutation_receipts"'
        )
        op.execute(
            'ALTER TABLE "public_api_mutation_receipts" NO FORCE ROW LEVEL SECURITY'
        )
        op.execute(
            'ALTER TABLE "public_api_mutation_receipts" DISABLE ROW LEVEL SECURITY'
        )
    op.drop_index(
        "ix_public_api_receipts_state_updated",
        table_name="public_api_mutation_receipts",
    )
    op.drop_index(
        "ix_public_api_receipts_workspace_token_created",
        table_name="public_api_mutation_receipts",
    )
    op.drop_table("public_api_mutation_receipts")

    op.drop_index(
        "ux_subscriptions_stripe_customer_id_not_null",
        table_name="subscriptions",
    )
    op.drop_index(
        "ux_subscriptions_stripe_subscription_id_not_null",
        table_name="subscriptions",
    )
    op.create_index(
        "ix_subscriptions_stripe_customer_id",
        "subscriptions",
        ["stripe_customer_id"],
    )
    op.create_index(
        "ix_subscriptions_stripe_sub_id",
        "subscriptions",
        ["stripe_subscription_id"],
    )
    op.drop_column("subscriptions", "last_stripe_event_created")
    op.drop_column("subscriptions", "last_stripe_event_id")
