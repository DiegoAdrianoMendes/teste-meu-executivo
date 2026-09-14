"""add platform inbox

Revision ID: b2d5f9c3a871
Revises: a1c4e8b2f760
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2d5f9c3a871"
down_revision: Union[str, Sequence[str], None] = "a1c4e8b2f760"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_inbox_conversations",
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("external_thread_id", sa.String(255), nullable=True),
        sa.Column("public_access_token_hash", sa.String(64), nullable=True),
        sa.Column("contact_name", sa.String(200), nullable=False),
        sa.Column("contact_address", sa.String(320), nullable=True),
        sa.Column("queue", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), server_default="open", nullable=False),
        sa.Column("assigned_to_user_id", sa.Uuid(), nullable=True),
        sa.Column("prospect_id", sa.Uuid(), nullable=True),
        sa.Column("support_ticket_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("subject", sa.String(240), nullable=True),
        sa.Column("last_message_preview", sa.Text(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["prospect_id"], ["growth_prospects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["support_ticket_id"], ["support_tickets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "external_thread_id", name="uq_platform_inbox_channel_thread"),
    )
    op.create_index("ix_platform_inbox_queue_status_last", "platform_inbox_conversations", ["queue", "status", "last_message_at"])
    op.create_index("ix_platform_inbox_assignee_status", "platform_inbox_conversations", ["assigned_to_user_id", "status"])
    op.create_index("ix_platform_inbox_conversations_channel", "platform_inbox_conversations", ["channel"])
    op.create_index("ix_platform_inbox_conversations_first_response_due_at", "platform_inbox_conversations", ["first_response_due_at"])
    op.create_index("ix_platform_inbox_conversations_resolution_due_at", "platform_inbox_conversations", ["resolution_due_at"])
    op.create_table(
        "platform_inbox_messages",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(40), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("delivery_status", sa.String(40), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(200), nullable=False),
        sa.Column("external_message_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["platform_inbox_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "idempotency_key", name="uq_platform_inbox_message_idempotency"),
        sa.UniqueConstraint("external_message_id", name="uq_platform_inbox_external_message"),
    )
    op.create_index("ix_platform_inbox_message_conversation_time", "platform_inbox_messages", ["conversation_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("platform_inbox_messages")
    op.drop_table("platform_inbox_conversations")
