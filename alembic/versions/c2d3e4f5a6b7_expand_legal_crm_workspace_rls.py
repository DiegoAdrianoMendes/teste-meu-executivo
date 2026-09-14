"""expand workspace RLS to legal and CRM domain tables

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-16 16:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
LEGACY_POLICY_NAME = "klm_workspace_isolation"
POLICY_NAME = "workspace_current_id"

LEGACY_TENANT_TABLES = (
    "klm_area_blueprints",
    "klm_omnichannel_threads",
    "klm_omnichannel_messages",
    "klm_follow_up_tasks",
    "klm_acquisition_events",
    "klm_technical_requests",
    "klm_audit_events",
    "klm_external_action_intents",
    "oss_external_entity_links",
    "oss_integration_event_receipts",
)

DIRECT_WORKSPACE_TABLES = (
    "clients",
    "conversation_insights",
    "finance_entries",
    "finance_reminders",
    "integration_connections",
    "knowledge_chunks",
    "matters",
    "meetings",
    "meta_approved_messages",
    "meta_automation_rules",
    "meta_channel_campaigns",
    "meta_channel_connections",
    "meta_conversation_notes",
    "meta_social_conversations",
    "whatsapp_campaign_recipients",
    "whatsapp_campaign_send_jobs",
    "whatsapp_campaigns",
    "whatsapp_connections",
    "whatsapp_contacts",
    "whatsapp_conversation_notes",
    "whatsapp_meta_templates",
    "whatsapp_opt_in_records",
    "whatsapp_suppression_entries",
)

# child table -> (parent table, child FK, parent key, same-name relationship columns)
PARENT_WORKSPACE_POLICIES = {
    "approval_queue_items": ("matters", "matter_id", "id", ()),
    "campaign_attributions": ("matters", "matter_id", "id", ()),
    "card_field_changes": ("matters", "matter_id", "id", ()),
    "card_scores": ("matters", "matter_id", "id", ()),
    "conversations": ("matters", "matter_id", "id", ()),
    "documents": ("matters", "matter_id", "id", ()),
    "honorarium_estimates": ("matters", "matter_id", "id", ()),
    "meta_social_messages": (
        "meta_social_conversations",
        "conversation_id",
        "id",
        (),
    ),
    "revenue_projections": ("matters", "matter_id", "id", ()),
    "tasks": ("matters", "matter_id", "id", ()),
    "timeline_events": ("matters", "matter_id", "id", ()),
    "whatsapp_campaign_audiences": (
        "whatsapp_campaigns",
        "campaign_id",
        "id",
        (),
    ),
    "whatsapp_campaign_delivery_events": (
        "whatsapp_campaign_recipients",
        "recipient_id",
        "id",
        ("campaign_id",),
    ),
    "whatsapp_campaign_template_snapshots": (
        "whatsapp_campaigns",
        "campaign_id",
        "id",
        (),
    ),
    "whatsapp_messages": (
        "whatsapp_contacts",
        "contact_id",
        "id",
        ("whatsapp_connection_id",),
    ),
}


def _workspace_predicate() -> str:
    return (
        "workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )


def _parent_workspace_predicate(table_name: str) -> str:
    parent, child_reference, parent_reference, shared_references = (
        PARENT_WORKSPACE_POLICIES[table_name]
    )
    conditions = [
        f"{parent}.{parent_reference} = {table_name}.{child_reference}"
    ]
    conditions.extend(
        f"{parent}.{column_name} = {table_name}.{column_name}"
        for column_name in shared_references
    )
    conditions.append(
        f"{parent}.workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )
    return f"EXISTS (SELECT 1 FROM {parent} WHERE {' AND '.join(conditions)})"


def _enable_policy(table_name: str, predicate: str) -> None:
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
        AS PERMISSIVE
        FOR ALL
        TO PUBLIC
        USING ({predicate})
        WITH CHECK ({predicate})
        """
    )


def _disable_policy(table_name: str) -> None:
    op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
    op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name in LEGACY_TENANT_TABLES:
        op.execute(
            f'ALTER POLICY "{LEGACY_POLICY_NAME}" ON "{table_name}" '
            f'RENAME TO "{POLICY_NAME}"'
        )

    direct_predicate = _workspace_predicate()
    for table_name in DIRECT_WORKSPACE_TABLES:
        _enable_policy(table_name, direct_predicate)
    for table_name in PARENT_WORKSPACE_POLICIES:
        _enable_policy(table_name, _parent_workspace_predicate(table_name))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name in reversed(tuple(PARENT_WORKSPACE_POLICIES)):
        _disable_policy(table_name)
    for table_name in reversed(DIRECT_WORKSPACE_TABLES):
        _disable_policy(table_name)

    for table_name in reversed(LEGACY_TENANT_TABLES):
        op.execute(
            f'ALTER POLICY "{POLICY_NAME}" ON "{table_name}" '
            f'RENAME TO "{LEGACY_POLICY_NAME}"'
        )
