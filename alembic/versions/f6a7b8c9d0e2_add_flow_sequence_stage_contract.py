"""Persist the additive Flow sequence-stage contract.

The flow revision JSON remains authoritative. These columns snapshot the
delivery contract on each queued ScheduledMessage so retries and later flow
edits cannot change an already-created stage.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "f6a7b8c9d0e2"
# Parent task linearizes the concurrent WhatsApp picker migration under this
# stable id before the final Alembic gate.
down_revision: str | Sequence[str] | None = "wa_picker_20260819"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scheduled_messages") as batch_op:
        batch_op.add_column(sa.Column("stage_id", sa.String(100), nullable=True))
        batch_op.add_column(
            sa.Column(
                "template_id",
                FlexibleUUID(),
                sa.ForeignKey("whatsapp_meta_templates.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "whatsapp_connection_id",
                FlexibleUUID(),
                sa.ForeignKey("whatsapp_connections.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("template_name", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("template_language", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("template_status", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("template_bindings", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("delay_value", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("delay_unit", sa.String(12), nullable=True))
        batch_op.add_column(
            sa.Column("send_now", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column(
                "interrupt_on_reply",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column("pending_grace_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pending_retry_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "tariff_status",
                sa.String(20),
                nullable=False,
                server_default="unverified",
            )
        )
        batch_op.add_column(sa.Column("tariff_amount", sa.Float(), nullable=True))
    op.create_index(
        "ix_scheduled_messages_flow_stage",
        "scheduled_messages",
        ["flow_run_id", "stage_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_messages_flow_stage", table_name="scheduled_messages")
    with op.batch_alter_table("scheduled_messages") as batch_op:
        for column in (
            "tariff_amount",
            "tariff_status",
            "pending_grace_until",
            "pending_retry_at",
            "interrupt_on_reply",
            "delay_unit",
            "delay_value",
            "send_now",
            "template_bindings",
            "template_status",
            "template_language",
            "template_name",
            "stage_id",
            "template_id",
            "whatsapp_connection_id",
        ):
            batch_op.drop_column(column)
