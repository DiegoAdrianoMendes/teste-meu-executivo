"""add whatsapp inbox operational fields

Revision ID: 1a2b3c4d5e7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-07 10:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.base import FlexibleUUID


revision = "1a2b3c4d5e7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


wa_queue_status_enum = sa.Enum(
    "ai",
    "human",
    "follow_up",
    "closed",
    name="wa_queue_status_enum",
)
wa_human_control_state_enum = sa.Enum(
    "copilot",
    "manual_pause",
    "manual_off",
    name="wa_human_control_state_enum",
)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    queue_enum = wa_queue_status_enum
    control_enum = wa_human_control_state_enum

    if dialect == "postgresql":
        op.execute(
            """
            DO $$ BEGIN
                CREATE TYPE wa_queue_status_enum AS ENUM ('ai', 'human', 'follow_up', 'closed');
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
            """
        )
        op.execute(
            """
            DO $$ BEGIN
                CREATE TYPE wa_human_control_state_enum AS ENUM ('copilot', 'manual_pause', 'manual_off');
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
            """
        )
        queue_enum = postgresql.ENUM(
            "ai",
            "human",
            "follow_up",
            "closed",
            name="wa_queue_status_enum",
            create_type=False,
        )
        control_enum = postgresql.ENUM(
            "copilot",
            "manual_pause",
            "manual_off",
            name="wa_human_control_state_enum",
            create_type=False,
        )
    else:
        wa_queue_status_enum.create(bind, checkfirst=True)
        wa_human_control_state_enum.create(bind, checkfirst=True)

    op.add_column("whatsapp_contacts", sa.Column("assigned_user_id", FlexibleUUID(), nullable=True))
    op.add_column("whatsapp_contacts", sa.Column("queue_status", queue_enum, nullable=False, server_default="ai"))
    op.add_column("whatsapp_contacts", sa.Column("human_control_state", control_enum, nullable=False, server_default="copilot"))
    op.add_column("whatsapp_contacts", sa.Column("active_flow_template_id", sa.String(length=36), nullable=True))
    op.add_column("whatsapp_contacts", sa.Column("active_flow_label", sa.String(length=255), nullable=True))
    op.add_column("whatsapp_contacts", sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        "fk_whatsapp_contacts_assigned_user_id_users",
        "whatsapp_contacts",
        "users",
        ["assigned_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_whatsapp_contacts_assigned_user_id"), "whatsapp_contacts", ["assigned_user_id"], unique=False)
    op.create_index(op.f("ix_whatsapp_contacts_queue_status"), "whatsapp_contacts", ["queue_status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_whatsapp_contacts_queue_status"), table_name="whatsapp_contacts")
    op.drop_index(op.f("ix_whatsapp_contacts_assigned_user_id"), table_name="whatsapp_contacts")
    op.drop_constraint("fk_whatsapp_contacts_assigned_user_id_users", "whatsapp_contacts", type_="foreignkey")

    op.drop_column("whatsapp_contacts", "next_follow_up_at")
    op.drop_column("whatsapp_contacts", "active_flow_label")
    op.drop_column("whatsapp_contacts", "active_flow_template_id")
    op.drop_column("whatsapp_contacts", "human_control_state")
    op.drop_column("whatsapp_contacts", "queue_status")
    op.drop_column("whatsapp_contacts", "assigned_user_id")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS wa_human_control_state_enum")
        op.execute("DROP TYPE IF EXISTS wa_queue_status_enum")
    else:
        wa_human_control_state_enum.drop(bind, checkfirst=True)
        wa_queue_status_enum.drop(bind, checkfirst=True)
