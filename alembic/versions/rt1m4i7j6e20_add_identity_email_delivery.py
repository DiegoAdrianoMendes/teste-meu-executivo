"""add durable identity email delivery state

Revision ID: rt1m4i7j6e20
Revises: qs0l3h6j5e19
"""

import sqlalchemy as sa

from alembic import op

revision = "rt1m4i7j6e20"
down_revision = "qs0l3h6j5e19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    delivery_status = sa.Enum(
        "pending",
        "sending",
        "sent",
        "failed",
        "blocked",
        name="identity_delivery_status_enum",
        native_enum=False,
        create_constraint=True,
    )
    op.add_column(
        "identity_outbox_messages",
        sa.Column("delivery_status", delivery_status, nullable=False, server_default="pending"),
    )
    op.add_column(
        "identity_outbox_messages",
        sa.Column("delivery_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "identity_outbox_messages",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "identity_outbox_messages",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "identity_outbox_messages",
        sa.Column("delivery_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "identity_outbox_messages",
        sa.Column("provider_message_ref", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "identity_outbox_messages",
        sa.Column("last_delivery_error_code", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_identity_outbox_delivery",
        "identity_outbox_messages",
        ["delivery_status", "last_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_identity_outbox_delivery", table_name="identity_outbox_messages")
    for column in (
        "last_delivery_error_code",
        "provider_message_ref",
        "delivery_provider",
        "delivered_at",
        "last_attempt_at",
        "delivery_attempt_count",
        "delivery_status",
    ):
        op.drop_column("identity_outbox_messages", column)
