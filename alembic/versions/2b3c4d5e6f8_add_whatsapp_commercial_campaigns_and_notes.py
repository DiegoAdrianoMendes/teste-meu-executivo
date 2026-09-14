"""add whatsapp commercial campaigns and notes

Revision ID: 2b3c4d5e6f8
Revises: 1a2b3c4d5e7
Create Date: 2026-04-07 18:20:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.base import FlexibleJSON, FlexibleUUID


revision = "2b3c4d5e6f8"
down_revision = "1a2b3c4d5e7"
branch_labels = None
depends_on = None


wa_meta_template_status_enum = sa.Enum(
    "approved",
    "pending",
    "rejected",
    "paused",
    "disabled",
    name="wa_meta_template_status_enum",
)
wa_opt_in_status_enum = sa.Enum(
    "opted_in",
    "opted_out",
    name="wa_opt_in_status_enum",
)
wa_campaign_status_enum = sa.Enum(
    "draft",
    "scheduled",
    "running",
    "paused",
    "completed",
    "stopped",
    "failed",
    name="wa_campaign_status_enum",
)
wa_campaign_template_status_enum = sa.Enum(
    "approved",
    "pending",
    "rejected",
    "paused",
    "disabled",
    name="wa_campaign_template_status_enum",
)
wa_campaign_recipient_status_enum = sa.Enum(
    "queued",
    "scheduled",
    "processing",
    "sent",
    "delivered",
    "read",
    "failed",
    "skipped",
    name="wa_campaign_recipient_status_enum",
)
wa_campaign_send_job_status_enum = sa.Enum(
    "pending",
    "scheduled",
    "processing",
    "sent",
    "failed",
    "cancelled",
    name="wa_campaign_send_job_status_enum",
)
wa_campaign_delivery_status_enum = sa.Enum(
    "queued",
    "scheduled",
    "processing",
    "sent",
    "delivered",
    "read",
    "failed",
    "skipped",
    name="wa_campaign_delivery_status_enum",
)


def _enum_for_pg(enum_obj: sa.Enum, *values: str):
    return postgresql.ENUM(*values, name=enum_obj.name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    meta_template_status = wa_meta_template_status_enum
    opt_in_status = wa_opt_in_status_enum
    campaign_status = wa_campaign_status_enum
    campaign_template_status = wa_campaign_template_status_enum
    recipient_status = wa_campaign_recipient_status_enum
    send_job_status = wa_campaign_send_job_status_enum
    delivery_status = wa_campaign_delivery_status_enum

    enums = [
        wa_meta_template_status_enum,
        wa_opt_in_status_enum,
        wa_campaign_status_enum,
        wa_campaign_template_status_enum,
        wa_campaign_recipient_status_enum,
        wa_campaign_send_job_status_enum,
        wa_campaign_delivery_status_enum,
    ]

    if dialect == "postgresql":
        for enum_obj, values in [
            (wa_meta_template_status_enum, ("approved", "pending", "rejected", "paused", "disabled")),
            (wa_opt_in_status_enum, ("opted_in", "opted_out")),
            (wa_campaign_status_enum, ("draft", "scheduled", "running", "paused", "completed", "stopped", "failed")),
            (wa_campaign_template_status_enum, ("approved", "pending", "rejected", "paused", "disabled")),
            (wa_campaign_recipient_status_enum, ("queued", "scheduled", "processing", "sent", "delivered", "read", "failed", "skipped")),
            (wa_campaign_send_job_status_enum, ("pending", "scheduled", "processing", "sent", "failed", "cancelled")),
            (wa_campaign_delivery_status_enum, ("queued", "scheduled", "processing", "sent", "delivered", "read", "failed", "skipped")),
        ]:
            op.execute(
                f"""
                DO $$ BEGIN
                    CREATE TYPE {enum_obj.name} AS ENUM {values};
                EXCEPTION WHEN duplicate_object THEN null;
                END $$;
                """
            )
        meta_template_status = _enum_for_pg(wa_meta_template_status_enum, "approved", "pending", "rejected", "paused", "disabled")
        opt_in_status = _enum_for_pg(wa_opt_in_status_enum, "opted_in", "opted_out")
        campaign_status = _enum_for_pg(wa_campaign_status_enum, "draft", "scheduled", "running", "paused", "completed", "stopped", "failed")
        campaign_template_status = _enum_for_pg(wa_campaign_template_status_enum, "approved", "pending", "rejected", "paused", "disabled")
        recipient_status = _enum_for_pg(wa_campaign_recipient_status_enum, "queued", "scheduled", "processing", "sent", "delivered", "read", "failed", "skipped")
        send_job_status = _enum_for_pg(wa_campaign_send_job_status_enum, "pending", "scheduled", "processing", "sent", "failed", "cancelled")
        delivery_status = _enum_for_pg(wa_campaign_delivery_status_enum, "queued", "scheduled", "processing", "sent", "delivered", "read", "failed", "skipped")
    else:
        for enum_obj in enums:
            enum_obj.create(bind, checkfirst=True)

    op.create_table(
        "whatsapp_meta_templates",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("whatsapp_connection_id", FlexibleUUID(), nullable=False),
        sa.Column("external_template_id", sa.String(length=120), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=False, server_default="pt_BR"),
        sa.Column("status", meta_template_status, nullable=False, server_default="pending"),
        sa.Column("quality_rating", sa.String(length=40), nullable=True),
        sa.Column("components", FlexibleJSON(), nullable=False),
        sa.Column("raw_payload", FlexibleJSON(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["whatsapp_connection_id"], ["whatsapp_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "whatsapp_connection_id",
            "name",
            "language",
            name="uq_wa_meta_templates_workspace_connection_name_language",
        ),
    )
    op.create_index("ix_whatsapp_meta_templates_workspace_id", "whatsapp_meta_templates", ["workspace_id"], unique=False)
    op.create_index("ix_whatsapp_meta_templates_whatsapp_connection_id", "whatsapp_meta_templates", ["whatsapp_connection_id"], unique=False)
    op.create_index("ix_whatsapp_meta_templates_external_template_id", "whatsapp_meta_templates", ["external_template_id"], unique=False)
    op.create_index("ix_wa_meta_templates_workspace_status", "whatsapp_meta_templates", ["workspace_id", "status"], unique=False)

    op.create_table(
        "whatsapp_opt_in_records",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("contact_id", FlexibleUUID(), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False, server_default="whatsapp"),
        sa.Column("status", opt_in_status, nullable=False, server_default="opted_in"),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("details", FlexibleJSON(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["whatsapp_contacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "phone", "channel", name="uq_wa_optin_workspace_phone_channel"),
    )
    op.create_index("ix_whatsapp_opt_in_records_workspace_id", "whatsapp_opt_in_records", ["workspace_id"], unique=False)
    op.create_index("ix_whatsapp_opt_in_records_contact_id", "whatsapp_opt_in_records", ["contact_id"], unique=False)
    op.create_index("ix_whatsapp_opt_in_records_phone", "whatsapp_opt_in_records", ["phone"], unique=False)

    op.create_table(
        "whatsapp_suppression_entries",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("contact_id", FlexibleUUID(), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=False, server_default="manual"),
        sa.Column("details", FlexibleJSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["whatsapp_contacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "phone", name="uq_wa_suppression_workspace_phone"),
    )
    op.create_index("ix_whatsapp_suppression_entries_workspace_id", "whatsapp_suppression_entries", ["workspace_id"], unique=False)
    op.create_index("ix_whatsapp_suppression_entries_contact_id", "whatsapp_suppression_entries", ["contact_id"], unique=False)
    op.create_index("ix_whatsapp_suppression_entries_phone", "whatsapp_suppression_entries", ["phone"], unique=False)

    op.create_table(
        "whatsapp_conversation_notes",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("contact_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("author_user_id", FlexibleUUID(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mentions", FlexibleJSON(), nullable=False),
        sa.Column("note_metadata", FlexibleJSON(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["whatsapp_contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whatsapp_conversation_notes_workspace_id", "whatsapp_conversation_notes", ["workspace_id"], unique=False)
    op.create_index("ix_whatsapp_conversation_notes_contact_id", "whatsapp_conversation_notes", ["contact_id"], unique=False)
    op.create_index("ix_whatsapp_conversation_notes_matter_id", "whatsapp_conversation_notes", ["matter_id"], unique=False)
    op.create_index("ix_whatsapp_conversation_notes_author_user_id", "whatsapp_conversation_notes", ["author_user_id"], unique=False)
    op.create_index("ix_wa_conversation_notes_contact_created", "whatsapp_conversation_notes", ["contact_id", "created_at"], unique=False)

    op.create_table(
        "whatsapp_campaigns",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("whatsapp_connection_id", FlexibleUUID(), nullable=False),
        sa.Column("created_by_id", FlexibleUUID(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", campaign_status, nullable=False, server_default="draft"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("read_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["whatsapp_connection_id"], ["whatsapp_connections.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whatsapp_campaigns_workspace_id", "whatsapp_campaigns", ["workspace_id"], unique=False)
    op.create_index("ix_whatsapp_campaigns_whatsapp_connection_id", "whatsapp_campaigns", ["whatsapp_connection_id"], unique=False)
    op.create_index("ix_whatsapp_campaigns_created_by_id", "whatsapp_campaigns", ["created_by_id"], unique=False)
    op.create_index("ix_wa_campaigns_workspace_status", "whatsapp_campaigns", ["workspace_id", "status"], unique=False)

    op.create_table(
        "whatsapp_campaign_audiences",
        sa.Column("campaign_id", FlexibleUUID(), nullable=False),
        sa.Column("filters", FlexibleJSON(), nullable=False),
        sa.Column("estimated_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_recipients", FlexibleJSON(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["whatsapp_campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", name="uq_wa_campaign_audiences_campaign"),
    )
    op.create_index("ix_whatsapp_campaign_audiences_campaign_id", "whatsapp_campaign_audiences", ["campaign_id"], unique=False)

    op.create_table(
        "whatsapp_campaign_template_snapshots",
        sa.Column("campaign_id", FlexibleUUID(), nullable=False),
        sa.Column("source_template_id", FlexibleUUID(), nullable=True),
        sa.Column("template_name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=False, server_default="pt_BR"),
        sa.Column("status", campaign_template_status, nullable=False, server_default="pending"),
        sa.Column("header_parameters", FlexibleJSON(), nullable=False),
        sa.Column("body_parameters", FlexibleJSON(), nullable=False),
        sa.Column("components", FlexibleJSON(), nullable=False),
        sa.Column("raw_payload", FlexibleJSON(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["whatsapp_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_template_id"], ["whatsapp_meta_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", name="uq_wa_campaign_template_snapshot_campaign"),
    )
    op.create_index("ix_whatsapp_campaign_template_snapshots_campaign_id", "whatsapp_campaign_template_snapshots", ["campaign_id"], unique=False)
    op.create_index("ix_whatsapp_campaign_template_snapshots_source_template_id", "whatsapp_campaign_template_snapshots", ["source_template_id"], unique=False)

    op.create_table(
        "whatsapp_campaign_recipients",
        sa.Column("campaign_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("contact_id", FlexibleUUID(), nullable=True),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("wamid", sa.String(length=255), nullable=True),
        sa.Column("status", recipient_status, nullable=False, server_default="queued"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("send_context", FlexibleJSON(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["whatsapp_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["whatsapp_contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "phone", name="uq_wa_campaign_recipients_campaign_phone"),
    )
    op.create_index("ix_whatsapp_campaign_recipients_campaign_id", "whatsapp_campaign_recipients", ["campaign_id"], unique=False)
    op.create_index("ix_whatsapp_campaign_recipients_workspace_id", "whatsapp_campaign_recipients", ["workspace_id"], unique=False)
    op.create_index("ix_whatsapp_campaign_recipients_contact_id", "whatsapp_campaign_recipients", ["contact_id"], unique=False)
    op.create_index("ix_whatsapp_campaign_recipients_matter_id", "whatsapp_campaign_recipients", ["matter_id"], unique=False)
    op.create_index("ix_whatsapp_campaign_recipients_wamid", "whatsapp_campaign_recipients", ["wamid"], unique=False)
    op.create_index("ix_wa_campaign_recipients_campaign_status", "whatsapp_campaign_recipients", ["campaign_id", "status"], unique=False)

    op.create_table(
        "whatsapp_campaign_send_jobs",
        sa.Column("campaign_id", FlexibleUUID(), nullable=False),
        sa.Column("recipient_id", FlexibleUUID(), nullable=False),
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("whatsapp_connection_id", FlexibleUUID(), nullable=False),
        sa.Column("status", send_job_status, nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["whatsapp_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_id"], ["whatsapp_campaign_recipients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["whatsapp_connection_id"], ["whatsapp_connections.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipient_id", name="uq_wa_campaign_send_jobs_recipient"),
    )
    op.create_index("ix_whatsapp_campaign_send_jobs_campaign_id", "whatsapp_campaign_send_jobs", ["campaign_id"], unique=False)
    op.create_index("ix_whatsapp_campaign_send_jobs_recipient_id", "whatsapp_campaign_send_jobs", ["recipient_id"], unique=False)
    op.create_index("ix_whatsapp_campaign_send_jobs_workspace_id", "whatsapp_campaign_send_jobs", ["workspace_id"], unique=False)
    op.create_index("ix_whatsapp_campaign_send_jobs_whatsapp_connection_id", "whatsapp_campaign_send_jobs", ["whatsapp_connection_id"], unique=False)
    op.create_index("ix_wa_campaign_send_jobs_workspace_status", "whatsapp_campaign_send_jobs", ["workspace_id", "status"], unique=False)

    op.create_table(
        "whatsapp_campaign_delivery_events",
        sa.Column("campaign_id", FlexibleUUID(), nullable=False),
        sa.Column("recipient_id", FlexibleUUID(), nullable=False),
        sa.Column("wamid", sa.String(length=255), nullable=True),
        sa.Column("status", delivery_status, nullable=False, server_default="sent"),
        sa.Column("payload", FlexibleJSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["whatsapp_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_id"], ["whatsapp_campaign_recipients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whatsapp_campaign_delivery_events_campaign_id", "whatsapp_campaign_delivery_events", ["campaign_id"], unique=False)
    op.create_index("ix_wa_campaign_delivery_events_campaign", "whatsapp_campaign_delivery_events", ["campaign_id"], unique=False)
    op.create_index("ix_wa_campaign_delivery_events_wamid", "whatsapp_campaign_delivery_events", ["wamid"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.drop_index("ix_wa_campaign_delivery_events_wamid", table_name="whatsapp_campaign_delivery_events")
    op.drop_index("ix_wa_campaign_delivery_events_campaign", table_name="whatsapp_campaign_delivery_events")
    op.drop_index("ix_whatsapp_campaign_delivery_events_campaign_id", table_name="whatsapp_campaign_delivery_events")
    op.drop_table("whatsapp_campaign_delivery_events")

    op.drop_index("ix_wa_campaign_send_jobs_workspace_status", table_name="whatsapp_campaign_send_jobs")
    op.drop_index("ix_whatsapp_campaign_send_jobs_whatsapp_connection_id", table_name="whatsapp_campaign_send_jobs")
    op.drop_index("ix_whatsapp_campaign_send_jobs_workspace_id", table_name="whatsapp_campaign_send_jobs")
    op.drop_index("ix_whatsapp_campaign_send_jobs_recipient_id", table_name="whatsapp_campaign_send_jobs")
    op.drop_index("ix_whatsapp_campaign_send_jobs_campaign_id", table_name="whatsapp_campaign_send_jobs")
    op.drop_table("whatsapp_campaign_send_jobs")

    op.drop_index("ix_wa_campaign_recipients_campaign_status", table_name="whatsapp_campaign_recipients")
    op.drop_index("ix_whatsapp_campaign_recipients_wamid", table_name="whatsapp_campaign_recipients")
    op.drop_index("ix_whatsapp_campaign_recipients_matter_id", table_name="whatsapp_campaign_recipients")
    op.drop_index("ix_whatsapp_campaign_recipients_contact_id", table_name="whatsapp_campaign_recipients")
    op.drop_index("ix_whatsapp_campaign_recipients_workspace_id", table_name="whatsapp_campaign_recipients")
    op.drop_index("ix_whatsapp_campaign_recipients_campaign_id", table_name="whatsapp_campaign_recipients")
    op.drop_table("whatsapp_campaign_recipients")

    op.drop_index("ix_whatsapp_campaign_template_snapshots_source_template_id", table_name="whatsapp_campaign_template_snapshots")
    op.drop_index("ix_whatsapp_campaign_template_snapshots_campaign_id", table_name="whatsapp_campaign_template_snapshots")
    op.drop_table("whatsapp_campaign_template_snapshots")

    op.drop_index("ix_whatsapp_campaign_audiences_campaign_id", table_name="whatsapp_campaign_audiences")
    op.drop_table("whatsapp_campaign_audiences")

    op.drop_index("ix_wa_campaigns_workspace_status", table_name="whatsapp_campaigns")
    op.drop_index("ix_whatsapp_campaigns_created_by_id", table_name="whatsapp_campaigns")
    op.drop_index("ix_whatsapp_campaigns_whatsapp_connection_id", table_name="whatsapp_campaigns")
    op.drop_index("ix_whatsapp_campaigns_workspace_id", table_name="whatsapp_campaigns")
    op.drop_table("whatsapp_campaigns")

    op.drop_index("ix_wa_conversation_notes_contact_created", table_name="whatsapp_conversation_notes")
    op.drop_index("ix_whatsapp_conversation_notes_author_user_id", table_name="whatsapp_conversation_notes")
    op.drop_index("ix_whatsapp_conversation_notes_matter_id", table_name="whatsapp_conversation_notes")
    op.drop_index("ix_whatsapp_conversation_notes_contact_id", table_name="whatsapp_conversation_notes")
    op.drop_index("ix_whatsapp_conversation_notes_workspace_id", table_name="whatsapp_conversation_notes")
    op.drop_table("whatsapp_conversation_notes")

    op.drop_index("ix_whatsapp_suppression_entries_phone", table_name="whatsapp_suppression_entries")
    op.drop_index("ix_whatsapp_suppression_entries_contact_id", table_name="whatsapp_suppression_entries")
    op.drop_index("ix_whatsapp_suppression_entries_workspace_id", table_name="whatsapp_suppression_entries")
    op.drop_table("whatsapp_suppression_entries")

    op.drop_index("ix_whatsapp_opt_in_records_phone", table_name="whatsapp_opt_in_records")
    op.drop_index("ix_whatsapp_opt_in_records_contact_id", table_name="whatsapp_opt_in_records")
    op.drop_index("ix_whatsapp_opt_in_records_workspace_id", table_name="whatsapp_opt_in_records")
    op.drop_table("whatsapp_opt_in_records")

    op.drop_index("ix_wa_meta_templates_workspace_status", table_name="whatsapp_meta_templates")
    op.drop_index("ix_whatsapp_meta_templates_external_template_id", table_name="whatsapp_meta_templates")
    op.drop_index("ix_whatsapp_meta_templates_whatsapp_connection_id", table_name="whatsapp_meta_templates")
    op.drop_index("ix_whatsapp_meta_templates_workspace_id", table_name="whatsapp_meta_templates")
    op.drop_table("whatsapp_meta_templates")

    if dialect == "postgresql":
        for enum_name in [
            "wa_campaign_delivery_status_enum",
            "wa_campaign_send_job_status_enum",
            "wa_campaign_recipient_status_enum",
            "wa_campaign_template_status_enum",
            "wa_campaign_status_enum",
            "wa_opt_in_status_enum",
            "wa_meta_template_status_enum",
        ]:
            op.execute(f"DROP TYPE IF EXISTS {enum_name}")
    else:
        for enum_obj in [
            wa_campaign_delivery_status_enum,
            wa_campaign_send_job_status_enum,
            wa_campaign_recipient_status_enum,
            wa_campaign_template_status_enum,
            wa_campaign_status_enum,
            wa_opt_in_status_enum,
            wa_meta_template_status_enum,
        ]:
            enum_obj.drop(bind, checkfirst=True)
