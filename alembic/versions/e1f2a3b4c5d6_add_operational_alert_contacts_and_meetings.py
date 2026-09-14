"""add operational alert contacts and meetings

Revision ID: e1f2a3b4c5d6
Revises: c9d8e7f6a5b4
Create Date: 2026-04-06 12:40:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.base import FlexibleJSON, FlexibleUUID


revision = "e1f2a3b4c5d6"
down_revision = "c9d8e7f6a5b4"
branch_labels = None
depends_on = None


meeting_status_enum = sa.Enum("scheduled", "completed", "cancelled", name="meeting_status_enum")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    status_enum = meeting_status_enum

    op.add_column("users", sa.Column("phone", sa.String(length=30), nullable=True))

    if dialect == "postgresql":
        op.execute(
            """
            DO $$ BEGIN
                CREATE TYPE meeting_status_enum AS ENUM ('scheduled', 'completed', 'cancelled');
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
            """
        )
        status_enum = postgresql.ENUM(
            "scheduled",
            "completed",
            "cancelled",
            name="meeting_status_enum",
            create_type=False,
        )

    op.create_table(
        "meetings",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("responsible_user_id", FlexibleUUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", status_enum, nullable=False, server_default="scheduled"),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("meeting_url", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recipient_overrides", FlexibleJSON(), nullable=True),
        sa.Column("google_calendar_event_id", sa.String(length=255), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["responsible_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meetings_workspace_id"), "meetings", ["workspace_id"], unique=False)
    op.create_index(op.f("ix_meetings_matter_id"), "meetings", ["matter_id"], unique=False)
    op.create_index(op.f("ix_meetings_responsible_user_id"), "meetings", ["responsible_user_id"], unique=False)
    op.create_index(op.f("ix_meetings_starts_at"), "meetings", ["starts_at"], unique=False)
    op.create_index(op.f("ix_meetings_status"), "meetings", ["status"], unique=False)
    op.create_index(
        op.f("ix_meetings_google_calendar_event_id"),
        "meetings",
        ["google_calendar_event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_meetings_google_calendar_event_id"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_status"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_starts_at"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_responsible_user_id"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_matter_id"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_workspace_id"), table_name="meetings")
    op.drop_table("meetings")
    op.drop_column("users", "phone")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS meeting_status_enum")
