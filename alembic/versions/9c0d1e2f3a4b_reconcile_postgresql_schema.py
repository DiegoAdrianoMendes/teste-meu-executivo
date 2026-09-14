"""reconcile PostgreSQL schema with ORM metadata

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
Create Date: 2026-07-12
"""

import sqlalchemy as sa

from alembic import op

revision = "9c0d1e2f3a4b"
down_revision = "8b9c0d1e2f3a"
branch_labels = None
depends_on = None


INDEXES = (
    ("ix_agent_runs_thread_id", "agent_runs", ("thread_id",)),
    (
        "ix_agent_runs_triggered_by_user_id",
        "agent_runs",
        ("triggered_by_user_id",),
    ),
    (
        "ix_human_overrides_decided_by_user_id",
        "human_overrides",
        ("decided_by_user_id",),
    ),
    (
        "ix_meta_approved_messages_channel_type",
        "meta_approved_messages",
        ("channel_type",),
    ),
    (
        "ix_meta_approved_messages_workspace_id",
        "meta_approved_messages",
        ("workspace_id",),
    ),
    (
        "ix_meta_automation_rules_channel_type",
        "meta_automation_rules",
        ("channel_type",),
    ),
    (
        "ix_meta_automation_rules_workspace_id",
        "meta_automation_rules",
        ("workspace_id",),
    ),
    (
        "ix_meta_channel_campaigns_channel_type",
        "meta_channel_campaigns",
        ("channel_type",),
    ),
    (
        "ix_meta_channel_campaigns_workspace_id",
        "meta_channel_campaigns",
        ("workspace_id",),
    ),
    (
        "ix_meta_channel_connections_channel_type",
        "meta_channel_connections",
        ("channel_type",),
    ),
    (
        "ix_meta_channel_connections_workspace_id",
        "meta_channel_connections",
        ("workspace_id",),
    ),
    (
        "ix_meta_conversation_notes_conversation_id",
        "meta_conversation_notes",
        ("conversation_id",),
    ),
    (
        "ix_meta_social_conversations_channel_type",
        "meta_social_conversations",
        ("channel_type",),
    ),
    (
        "ix_meta_social_conversations_last_message_at",
        "meta_social_conversations",
        ("last_message_at",),
    ),
    (
        "ix_meta_social_conversations_workspace_id",
        "meta_social_conversations",
        ("workspace_id",),
    ),
    (
        "ix_meta_social_messages_conversation_id",
        "meta_social_messages",
        ("conversation_id",),
    ),
    (
        "ix_public_api_request_logs_token_id",
        "public_api_request_logs",
        ("token_id",),
    ),
    (
        "ix_public_api_request_logs_workspace_id",
        "public_api_request_logs",
        ("workspace_id",),
    ),
    (
        "ix_whatsapp_campaign_delivery_events_recipient_id",
        "whatsapp_campaign_delivery_events",
        ("recipient_id",),
    ),
    (
        "ix_wa_campaign_recipients_wamid",
        "whatsapp_campaign_recipients",
        ("wamid",),
    ),
    (
        "ix_workspace_public_api_tokens_created_by_id",
        "workspace_public_api_tokens",
        ("created_by_id",),
    ),
    (
        "ix_workspace_public_api_tokens_workspace_id",
        "workspace_public_api_tokens",
        ("workspace_id",),
    ),
)


def upgrade() -> None:
    op.drop_index(
        "ix_password_reset_tokens_token_hash",
        table_name="password_reset_tokens",
    )
    op.alter_column(
        "whatsapp_connections",
        "phone_number_id",
        existing_type=sa.String(length=50),
        nullable=True,
    )
    for index_name, table_name, columns in INDEXES:
        op.create_index(index_name, table_name, list(columns), unique=False)


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(INDEXES):
        op.drop_index(index_name, table_name=table_name)
    op.alter_column(
        "whatsapp_connections",
        "phone_number_id",
        existing_type=sa.String(length=50),
        nullable=False,
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )
