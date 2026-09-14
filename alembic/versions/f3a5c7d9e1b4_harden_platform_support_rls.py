"""harden platform support access under forced RLS

Revision ID: f3a5c7d9e1b4
Revises: e2f4a6b8c0d3
Create Date: 2026-07-24 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f3a5c7d9e1b4"
down_revision: str | Sequence[str] | None = "e2f4a6b8c0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_POLICY = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
PLATFORM_SCOPE_SETTING = "app.current_platform_scope"
PLATFORM_ACTOR_SETTING = "app.current_platform_actor_id"
PLATFORM_ACTION_SETTING = "app.current_platform_action"
PLATFORM_SUPPORT_SCOPE = "support"
PLATFORM_SUPPORT_ACTIONS = (
    "support:assign",
    "support:export",
    "support:manage",
    "support:respond",
    "support:triage",
    "support:view",
)
SUPPORT_TABLES = (
    "support_tickets",
    "support_ticket_collaborators",
    "support_ticket_messages",
    "support_ticket_attachments",
    "support_ticket_events",
    "support_notification_outbox",
)


def _workspace_predicate() -> str:
    return (
        f"workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )


def _platform_support_predicate() -> str:
    allowed_actions = " OR ".join(
        f"current_setting('{PLATFORM_ACTION_SETTING}', true) = '{action}'"
        for action in PLATFORM_SUPPORT_ACTIONS
    )
    return (
        f"({_workspace_predicate()}) OR ("
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = "
        f"'{PLATFORM_SUPPORT_SCOPE}' AND "
        f"NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') "
        f"IS NOT NULL AND "
        f"({allowed_actions}))"
    )


def _replace_policy(predicate: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in SUPPORT_TABLES:
        op.execute(
            f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"'
        )
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def upgrade() -> None:
    _replace_policy(_platform_support_predicate())


def downgrade() -> None:
    _replace_policy(_workspace_predicate())
