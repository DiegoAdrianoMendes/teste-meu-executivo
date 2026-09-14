"""Add native legal artifact versions and process monitoring.

Revision ID: 98c9d0e1f2a3
Revises: 97b8c9d0e1f2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "98c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "97b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"
TENANT_TABLES = (
    "klm_legal_artifact_versions",
    "klm_process_monitoring_records",
)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "klm_legal_artifact_versions",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("artifact_key", FlexibleUUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("content", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "source_snapshot", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("approval_item_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["approval_item_id"], ["approval_queue_items.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "artifact_key",
            "version",
            name="uq_klm_legal_artifact_version",
        ),
    )
    op.create_index(
        "ix_klm_legal_artifact_workspace_matter_kind",
        "klm_legal_artifact_versions",
        ["workspace_id", "matter_id", "kind"],
    )
    op.create_index(
        "ix_klm_legal_artifact_workspace_status",
        "klm_legal_artifact_versions",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_klm_legal_artifact_approval_item_id",
        "klm_legal_artifact_versions",
        ["approval_item_id"],
    )
    op.create_index(
        "ix_klm_legal_artifact_created_by_user_id",
        "klm_legal_artifact_versions",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_klm_legal_artifact_artifact_key",
        "klm_legal_artifact_versions",
        ["artifact_key"],
    )

    op.create_table(
        "klm_process_monitoring_records",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=False),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("case_number", sa.String(length=80), nullable=False),
        sa.Column("case_number_normalized", sa.String(length=80), nullable=False),
        sa.Column("court", sa.String(length=240), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=False, server_default="cadastrado"),
        sa.Column("next_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_summary", sa.Text(), nullable=True),
        sa.Column(
            "source_state", sa.String(length=40), nullable=False, server_default="manual_local"
        ),
        sa.Column("events", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "case_number_normalized",
            name="uq_klm_process_workspace_number",
        ),
    )
    op.create_index(
        "ix_klm_process_workspace_matter",
        "klm_process_monitoring_records",
        ["workspace_id", "matter_id"],
    )
    op.create_index(
        "ix_klm_process_workspace_deadline",
        "klm_process_monitoring_records",
        ["workspace_id", "next_deadline_at"],
    )
    op.create_index(
        "ix_klm_process_workspace_status",
        "klm_process_monitoring_records",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_klm_process_created_by_user_id",
        "klm_process_monitoring_records",
        ["created_by_user_id"],
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
        )
        for table_name in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'''
                CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
                AS PERMISSIVE FOR ALL TO PUBLIC
                USING ({predicate}) WITH CHECK ({predicate})
                '''
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in reversed(TENANT_TABLES):
            op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_klm_process_created_by_user_id", table_name="klm_process_monitoring_records")
    op.drop_index("ix_klm_process_workspace_status", table_name="klm_process_monitoring_records")
    op.drop_index("ix_klm_process_workspace_deadline", table_name="klm_process_monitoring_records")
    op.drop_index("ix_klm_process_workspace_matter", table_name="klm_process_monitoring_records")
    op.drop_table("klm_process_monitoring_records")

    op.drop_index("ix_klm_legal_artifact_artifact_key", table_name="klm_legal_artifact_versions")
    op.drop_index("ix_klm_legal_artifact_created_by_user_id", table_name="klm_legal_artifact_versions")
    op.drop_index("ix_klm_legal_artifact_approval_item_id", table_name="klm_legal_artifact_versions")
    op.drop_index("ix_klm_legal_artifact_workspace_status", table_name="klm_legal_artifact_versions")
    op.drop_index("ix_klm_legal_artifact_workspace_matter_kind", table_name="klm_legal_artifact_versions")
    op.drop_table("klm_legal_artifact_versions")
