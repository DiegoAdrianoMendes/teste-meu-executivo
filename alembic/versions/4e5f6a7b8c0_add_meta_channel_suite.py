"""add meta channel suite

Revision ID: 4e5f6a7b8c0
Revises: 3c4d5e6f7a9
Create Date: 2026-04-07 11:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db.base import FlexibleJSON, FlexibleUUID


# revision identifiers, used by Alembic.
revision = "4e5f6a7b8c0"
down_revision = "3c4d5e6f7a9"
branch_labels = None
depends_on = None


meta_channel_type_enum = postgresql.ENUM("INSTAGRAM", "MESSENGER", name="meta_channel_type_enum", create_type=False)
meta_connection_status_enum = postgresql.ENUM("disconnected", "pending", "connected", "error", name="meta_connection_status_enum", create_type=False)
meta_social_channel_type_enum = postgresql.ENUM("INSTAGRAM", "MESSENGER", name="meta_social_channel_type_enum", create_type=False)
meta_social_event_type_enum = postgresql.ENUM(
    "direct_message",
    "story_reply",
    "story_mention",
    "post_comment",
    "keyword_comment",
    "page_message",
    "page_comment",
    "entry_point",
    name="meta_social_event_type_enum",
    create_type=False,
)
meta_follow_status_enum = postgresql.ENUM("following", "not_following", "unknown", name="meta_follow_status_enum", create_type=False)
meta_approved_message_status_enum = postgresql.ENUM("draft", "in_review", "approved", "archived", name="meta_approved_message_status_enum", create_type=False)
meta_approved_message_channel_enum = postgresql.ENUM("INSTAGRAM", "MESSENGER", name="meta_approved_message_channel_enum", create_type=False)
meta_automation_channel_enum = postgresql.ENUM("INSTAGRAM", "MESSENGER", name="meta_automation_channel_enum", create_type=False)
meta_note_channel_enum = postgresql.ENUM("INSTAGRAM", "MESSENGER", name="meta_note_channel_enum", create_type=False)
meta_campaign_channel_enum = postgresql.ENUM("INSTAGRAM", "MESSENGER", name="meta_campaign_channel_enum", create_type=False)
meta_campaign_status_enum = postgresql.ENUM("draft", "scheduled", "running", "paused", "completed", "stopped", "failed", name="meta_campaign_status_enum", create_type=False)
meta_queue_status_enum = postgresql.ENUM("ai", "human", "follow_up", "closed", name="meta_queue_status_enum", create_type=False)
meta_human_control_state_enum = postgresql.ENUM("copilot", "manual_pause", "manual_off", name="meta_human_control_state_enum", create_type=False)
meta_message_direction_enum = postgresql.ENUM("inbound", "outbound", name="meta_message_direction_enum", create_type=False)
meta_message_status_enum = postgresql.ENUM("pending", "sent", "delivered", "read", "failed", name="meta_message_status_enum", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_obj in [
        meta_channel_type_enum,
        meta_connection_status_enum,
        meta_social_channel_type_enum,
        meta_social_event_type_enum,
        meta_follow_status_enum,
        meta_approved_message_status_enum,
        meta_approved_message_channel_enum,
        meta_automation_channel_enum,
        meta_note_channel_enum,
        meta_campaign_channel_enum,
        meta_campaign_status_enum,
        meta_queue_status_enum,
        meta_human_control_state_enum,
        meta_message_direction_enum,
        meta_message_status_enum,
    ]:
        enum_obj.create(bind, checkfirst=True)

    op.create_table(
        "meta_channel_connections",
        sa.Column("workspace_id", FlexibleUUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_type", meta_channel_type_enum, nullable=False),
        sa.Column("page_id", sa.String(length=80), nullable=True),
        sa.Column("instagram_account_id", sa.String(length=80), nullable=True),
        sa.Column("external_account_id", sa.String(length=80), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("scopes", FlexibleJSON(), nullable=False),
        sa.Column("capability_matrix", FlexibleJSON(), nullable=False),
        sa.Column("status", meta_connection_status_enum, nullable=False),
        sa.Column("webhook_registered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
        sa.UniqueConstraint("workspace_id", "channel_type", "external_account_id", name="uq_meta_channel_connections_workspace_channel_external"),
    )
    op.create_index("ix_meta_channel_connections_workspace_channel", "meta_channel_connections", ["workspace_id", "channel_type"])
    op.create_index(op.f("ix_meta_channel_connections_page_id"), "meta_channel_connections", ["page_id"])
    op.create_index(op.f("ix_meta_channel_connections_instagram_account_id"), "meta_channel_connections", ["instagram_account_id"])
    op.create_index(op.f("ix_meta_channel_connections_external_account_id"), "meta_channel_connections", ["external_account_id"])

    op.create_table(
        "meta_social_conversations",
        sa.Column("workspace_id", FlexibleUUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_type", meta_social_channel_type_enum, nullable=False),
        sa.Column("connection_id", FlexibleUUID(), sa.ForeignKey("meta_channel_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), sa.ForeignKey("matters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_thread_id", sa.String(length=160), nullable=False),
        sa.Column("external_participant_id", sa.String(length=160), nullable=True),
        sa.Column("participant_name", sa.String(length=255), nullable=False),
        sa.Column("participant_handle", sa.String(length=255), nullable=True),
        sa.Column("participant_avatar_url", sa.String(length=500), nullable=True),
        sa.Column("event_type", meta_social_event_type_enum, nullable=False),
        sa.Column("queue_status", meta_queue_status_enum, nullable=False),
        sa.Column("human_control_state", meta_human_control_state_enum, nullable=False),
        sa.Column("follow_status", meta_follow_status_enum, nullable=False),
        sa.Column("source_title", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("source_metadata", FlexibleJSON(), nullable=False),
        sa.Column("active_flow_label", sa.String(length=255), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_preview", sa.Text(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("policy_state", sa.String(length=80), nullable=True),
        sa.Column("is_public_thread", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
        sa.UniqueConstraint("workspace_id", "channel_type", "external_thread_id", name="uq_meta_social_conversations_workspace_channel_thread"),
    )
    op.create_index("ix_meta_social_conversations_workspace_channel", "meta_social_conversations", ["workspace_id", "channel_type"])
    op.create_index("ix_meta_social_conversations_workspace_last_message", "meta_social_conversations", ["workspace_id", "last_message_at"])
    op.create_index(op.f("ix_meta_social_conversations_connection_id"), "meta_social_conversations", ["connection_id"])
    op.create_index(op.f("ix_meta_social_conversations_matter_id"), "meta_social_conversations", ["matter_id"])
    op.create_index(op.f("ix_meta_social_conversations_assigned_user_id"), "meta_social_conversations", ["assigned_user_id"])
    op.create_index(op.f("ix_meta_social_conversations_external_thread_id"), "meta_social_conversations", ["external_thread_id"])
    op.create_index(op.f("ix_meta_social_conversations_external_participant_id"), "meta_social_conversations", ["external_participant_id"])

    op.create_table(
        "meta_social_messages",
        sa.Column("conversation_id", FlexibleUUID(), sa.ForeignKey("meta_social_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_message_id", sa.String(length=160), nullable=True),
        sa.Column("direction", meta_message_direction_enum, nullable=False),
        sa.Column("status", meta_message_status_enum, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_name", sa.String(length=255), nullable=True),
        sa.Column("sent_by_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_public_reply", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("message_metadata", FlexibleJSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
        sa.UniqueConstraint("external_message_id", name="uq_meta_social_messages_external_message"),
    )
    op.create_index("ix_meta_social_messages_conversation_timestamp", "meta_social_messages", ["conversation_id", "timestamp"])
    op.create_index(op.f("ix_meta_social_messages_external_message_id"), "meta_social_messages", ["external_message_id"])
    op.create_index(op.f("ix_meta_social_messages_sent_by_id"), "meta_social_messages", ["sent_by_id"])

    op.create_table(
        "meta_approved_messages",
        sa.Column("workspace_id", FlexibleUUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_type", meta_approved_message_channel_enum, nullable=False),
        sa.Column("message_kind", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cta_label", sa.String(length=120), nullable=True),
        sa.Column("cta_url", sa.String(length=500), nullable=True),
        sa.Column("status", meta_approved_message_status_enum, nullable=False),
        sa.Column("requires_following", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("restrictions", FlexibleJSON(), nullable=False),
        sa.Column("created_by_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
    )
    op.create_index("ix_meta_approved_messages_workspace_channel", "meta_approved_messages", ["workspace_id", "channel_type"])
    op.create_index(op.f("ix_meta_approved_messages_created_by_id"), "meta_approved_messages", ["created_by_id"])
    op.create_index(op.f("ix_meta_approved_messages_approved_by_id"), "meta_approved_messages", ["approved_by_id"])

    op.create_table(
        "meta_automation_rules",
        sa.Column("workspace_id", FlexibleUUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_type", meta_automation_channel_enum, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("event_type", meta_social_event_type_enum, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("keyword", sa.String(length=255), nullable=True),
        sa.Column("approved_message_id", FlexibleUUID(), sa.ForeignKey("meta_approved_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action_config", FlexibleJSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
    )
    op.create_index("ix_meta_automation_rules_workspace_channel", "meta_automation_rules", ["workspace_id", "channel_type"])
    op.create_index(op.f("ix_meta_automation_rules_approved_message_id"), "meta_automation_rules", ["approved_message_id"])

    op.create_table(
        "meta_channel_campaigns",
        sa.Column("workspace_id", FlexibleUUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_type", meta_campaign_channel_enum, nullable=False),
        sa.Column("connection_id", FlexibleUUID(), sa.ForeignKey("meta_channel_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_message_id", FlexibleUUID(), sa.ForeignKey("meta_approved_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", meta_campaign_status_enum, nullable=False),
        sa.Column("audience_filter", FlexibleJSON(), nullable=False),
        sa.Column("policy_summary", FlexibleJSON(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("read_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
    )
    op.create_index("ix_meta_channel_campaigns_workspace_channel", "meta_channel_campaigns", ["workspace_id", "channel_type"])
    op.create_index(op.f("ix_meta_channel_campaigns_connection_id"), "meta_channel_campaigns", ["connection_id"])
    op.create_index(op.f("ix_meta_channel_campaigns_created_by_id"), "meta_channel_campaigns", ["created_by_id"])
    op.create_index(op.f("ix_meta_channel_campaigns_approved_message_id"), "meta_channel_campaigns", ["approved_message_id"])

    op.create_table(
        "meta_conversation_notes",
        sa.Column("workspace_id", FlexibleUUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_type", meta_note_channel_enum, nullable=False),
        sa.Column("conversation_id", FlexibleUUID(), sa.ForeignKey("meta_social_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), sa.ForeignKey("matters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mentions", FlexibleJSON(), nullable=False),
        sa.Column("note_metadata", FlexibleJSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", FlexibleUUID(), primary_key=True, nullable=False),
    )
    op.create_index("ix_meta_conversation_notes_conversation_created", "meta_conversation_notes", ["conversation_id", "created_at"])
    op.create_index(op.f("ix_meta_conversation_notes_workspace_id"), "meta_conversation_notes", ["workspace_id"])
    op.create_index(op.f("ix_meta_conversation_notes_channel_type"), "meta_conversation_notes", ["channel_type"])
    op.create_index(op.f("ix_meta_conversation_notes_matter_id"), "meta_conversation_notes", ["matter_id"])
    op.create_index(op.f("ix_meta_conversation_notes_author_user_id"), "meta_conversation_notes", ["author_user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_meta_conversation_notes_author_user_id"), table_name="meta_conversation_notes")
    op.drop_index(op.f("ix_meta_conversation_notes_matter_id"), table_name="meta_conversation_notes")
    op.drop_index(op.f("ix_meta_conversation_notes_channel_type"), table_name="meta_conversation_notes")
    op.drop_index(op.f("ix_meta_conversation_notes_workspace_id"), table_name="meta_conversation_notes")
    op.drop_index("ix_meta_conversation_notes_conversation_created", table_name="meta_conversation_notes")
    op.drop_table("meta_conversation_notes")

    op.drop_index(op.f("ix_meta_channel_campaigns_approved_message_id"), table_name="meta_channel_campaigns")
    op.drop_index(op.f("ix_meta_channel_campaigns_created_by_id"), table_name="meta_channel_campaigns")
    op.drop_index(op.f("ix_meta_channel_campaigns_connection_id"), table_name="meta_channel_campaigns")
    op.drop_index("ix_meta_channel_campaigns_workspace_channel", table_name="meta_channel_campaigns")
    op.drop_table("meta_channel_campaigns")

    op.drop_index(op.f("ix_meta_automation_rules_approved_message_id"), table_name="meta_automation_rules")
    op.drop_index("ix_meta_automation_rules_workspace_channel", table_name="meta_automation_rules")
    op.drop_table("meta_automation_rules")

    op.drop_index(op.f("ix_meta_approved_messages_approved_by_id"), table_name="meta_approved_messages")
    op.drop_index(op.f("ix_meta_approved_messages_created_by_id"), table_name="meta_approved_messages")
    op.drop_index("ix_meta_approved_messages_workspace_channel", table_name="meta_approved_messages")
    op.drop_table("meta_approved_messages")

    op.drop_index(op.f("ix_meta_social_messages_sent_by_id"), table_name="meta_social_messages")
    op.drop_index(op.f("ix_meta_social_messages_external_message_id"), table_name="meta_social_messages")
    op.drop_index("ix_meta_social_messages_conversation_timestamp", table_name="meta_social_messages")
    op.drop_table("meta_social_messages")

    op.drop_index(op.f("ix_meta_social_conversations_external_participant_id"), table_name="meta_social_conversations")
    op.drop_index(op.f("ix_meta_social_conversations_external_thread_id"), table_name="meta_social_conversations")
    op.drop_index(op.f("ix_meta_social_conversations_assigned_user_id"), table_name="meta_social_conversations")
    op.drop_index(op.f("ix_meta_social_conversations_matter_id"), table_name="meta_social_conversations")
    op.drop_index(op.f("ix_meta_social_conversations_connection_id"), table_name="meta_social_conversations")
    op.drop_index("ix_meta_social_conversations_workspace_last_message", table_name="meta_social_conversations")
    op.drop_index("ix_meta_social_conversations_workspace_channel", table_name="meta_social_conversations")
    op.drop_table("meta_social_conversations")

    op.drop_index(op.f("ix_meta_channel_connections_external_account_id"), table_name="meta_channel_connections")
    op.drop_index(op.f("ix_meta_channel_connections_instagram_account_id"), table_name="meta_channel_connections")
    op.drop_index(op.f("ix_meta_channel_connections_page_id"), table_name="meta_channel_connections")
    op.drop_index("ix_meta_channel_connections_workspace_channel", table_name="meta_channel_connections")
    op.drop_table("meta_channel_connections")

    bind = op.get_bind()
    for enum_obj in [
        meta_message_status_enum,
        meta_message_direction_enum,
        meta_human_control_state_enum,
        meta_queue_status_enum,
        meta_campaign_status_enum,
        meta_campaign_channel_enum,
        meta_note_channel_enum,
        meta_automation_channel_enum,
        meta_approved_message_channel_enum,
        meta_approved_message_status_enum,
        meta_follow_status_enum,
        meta_social_event_type_enum,
        meta_social_channel_type_enum,
        meta_connection_status_enum,
        meta_channel_type_enum,
    ]:
        enum_obj.drop(bind, checkfirst=True)
