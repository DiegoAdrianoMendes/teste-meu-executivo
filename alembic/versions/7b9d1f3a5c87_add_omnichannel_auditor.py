"""Add native omnichannel auditor findings.

Revision ID: 7b9d1f3a5c87
Revises: 6a8c0e2f4b76
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7b9d1f3a5c87"
down_revision: str | Sequence[str] | None = "6a8c0e2f4b76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "klm_omnichannel_audit_findings"
POLICY_NAME = "workspace_current_id"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("matter_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("finding_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), server_default="warning", nullable=False),
        sa.Column("state", sa.String(length=24), server_default="open", nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=180), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("remediation", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default="1", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("internal_note_message_id", sa.Uuid(), nullable=True),
        sa.Column("retry_schedule_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_klm_audit_finding_severity",
        ),
        sa.CheckConstraint(
            "state IN ('open', 'retry_scheduled', 'waiting_human', 'resolved', 'dismissed')",
            name="ck_klm_audit_finding_state",
        ),
        sa.CheckConstraint(
            "retry_count >= 0 AND max_retries >= 0",
            name="ck_klm_audit_finding_retry_counts",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_audit_finding_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_audit_finding_workspace_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_klm_audit_finding_workspace_matter",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "dedupe_key",
            name="uq_klm_omnichannel_audit_finding_dedupe",
        ),
    )
    op.create_index(
        "ix_klm_audit_finding_workspace_state_seen",
        TABLE_NAME,
        ["workspace_id", "state", "last_seen_at"],
    )
    op.create_index(
        "ix_klm_audit_finding_thread_state",
        TABLE_NAME,
        ["workspace_id", "thread_id", "state"],
    )
    for column in (
        "workspace_id",
        "thread_id",
        "matter_id",
        "message_id",
        "finding_type",
        "severity",
        "state",
        "detected_at",
        "last_seen_at",
        "internal_note_message_id",
        "retry_schedule_id",
    ):
        op.create_index(f"ix_{TABLE_NAME}_{column}", TABLE_NAME, [column])

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            "workspace_id::text = "
            "NULLIF(current_setting('app.current_workspace_id', true), '')"
        )
        op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "{POLICY_NAME}" ON "{TABLE_NAME}" '
            f"AS PERMISSIVE FOR ALL TO PUBLIC USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{TABLE_NAME}"')
        op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY')
    op.drop_table(TABLE_NAME)
