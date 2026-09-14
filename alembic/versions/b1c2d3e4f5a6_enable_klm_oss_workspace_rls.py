"""enable workspace RLS for KLM and OSS persistence

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-07-16 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
POLICY_NAME = "klm_workspace_isolation"
TENANT_TABLES = (
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


def _policy_predicate() -> str:
    return (
        "workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    predicate = _policy_predicate()
    for table_name in TENANT_TABLES:
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


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name in reversed(TENANT_TABLES):
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
