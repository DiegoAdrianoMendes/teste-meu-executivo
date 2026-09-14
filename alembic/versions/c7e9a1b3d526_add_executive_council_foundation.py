"""Add tenant-isolated persistence for Meus Executivos.

Revision ID: c7e9a1b3d526
Revises: b6d8f0a2c415
Create Date: 2026-07-26 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "c7e9a1b3d526"
down_revision: str | Sequence[str] | None = "b6d8f0a2c415"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = (
    "executive_profiles",
    "executive_threads",
    "executive_thread_shares",
    "executive_rounds",
    "executive_source_receipts",
    "executive_messages",
    "executive_reports",
)


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def _tenant_rls(enable: bool) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = "workspace_id::text = NULLIF(current_setting('app.current_workspace_id', true), '')"
    for table in RLS_TABLES:
        if enable:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'''CREATE POLICY "workspace_current_id" ON "{table}"
                AS PERMISSIVE FOR ALL TO PUBLIC
                USING ({predicate}) WITH CHECK ({predicate})''')
        else:
            op.execute(f'DROP POLICY IF EXISTS "workspace_current_id" ON "{table}"')
            op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    def timestamps() -> tuple[sa.Column, sa.Column, sa.Column]:
        return (
            sa.Column("id", FlexibleUUID(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    def workspace_fk() -> sa.ForeignKeyConstraint:
        return sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE")

    op.create_table(
        "executive_profiles",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "key",
            _enum("executive_key_enum", "ceo", "coo", "cfo", "cro", "cpo", "cto", "cio", "chro"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("mention", sa.String(16), nullable=False),
        sa.Column("role", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "domain_manifest", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "allowed_read_capabilities",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "forbidden_capabilities", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("prompt_version", sa.String(40), nullable=False, server_default="v1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        *timestamps(),
        workspace_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "key", name="uq_executive_profile_workspace_key"),
    )
    op.create_index("ix_executive_profiles_workspace_id", "executive_profiles", ["workspace_id"])

    op.create_table(
        "executive_threads",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("title", sa.String(240), nullable=True),
        sa.Column("title_manually_edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "visibility",
            _enum("executive_thread_visibility_enum", "private", "shared"),
            nullable=False,
            server_default="private",
        ),
        sa.Column(
            "status",
            _enum("executive_thread_status_enum", "active", "archived", "deleted"),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "mode", _enum("executive_thread_mode_enum", "direct", "roundtable"), nullable=False
        ),
        sa.Column(
            "selected_executives", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("ceo_always_present", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        workspace_fk(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_executive_thread_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_executive_thread_workspace_id"),
    )
    op.create_index("ix_executive_threads_workspace_id", "executive_threads", ["workspace_id"])
    op.create_index(
        "ix_executive_threads_created_by_user_id", "executive_threads", ["created_by_user_id"]
    )
    op.create_index(
        "ix_executive_threads_last_message_at", "executive_threads", ["last_message_at"]
    )
    op.create_index(
        "ix_executive_threads_workspace_status_activity",
        "executive_threads",
        ["workspace_id", "status", "last_message_at"],
    )

    op.create_table(
        "executive_thread_shares",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("user_id", FlexibleUUID(), nullable=False),
        sa.Column("granted_by_user_id", FlexibleUUID(), nullable=False),
        sa.Column("permission", sa.String(32), nullable=False, server_default="view_and_reply"),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        workspace_fk(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["executive_threads.workspace_id", "executive_threads.id"],
            name="fk_executive_share_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_executive_share_user_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "granted_by_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_executive_share_granter_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "user_id", name="uq_executive_thread_share_user"),
    )
    op.create_index(
        "ix_executive_thread_shares_workspace_id", "executive_thread_shares", ["workspace_id"]
    )
    op.create_index(
        "ix_executive_thread_shares_thread_id", "executive_thread_shares", ["thread_id"]
    )
    op.create_index("ix_executive_thread_shares_user_id", "executive_thread_shares", ["user_id"])

    op.create_table(
        "executive_rounds",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("user_message_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "status",
            _enum(
                "executive_round_status_enum",
                "received",
                "context_planning",
                "retrieving_context",
                "executive_selection",
                "executives_responding",
                "ceo_synthesizing",
                "completed",
                "partial_failure",
                "failed",
                "cancelled",
            ),
            nullable=False,
            server_default="received",
        ),
        sa.Column(
            "requested_executives", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "selected_by_ceo", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "selection_reasons", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("context_scope", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *timestamps(),
        workspace_fk(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["executive_threads.workspace_id", "executive_threads.id"],
            name="fk_executive_round_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_executive_round_workspace_id"),
    )
    op.create_index("ix_executive_rounds_workspace_id", "executive_rounds", ["workspace_id"])
    op.create_index("ix_executive_rounds_thread_id", "executive_rounds", ["thread_id"])
    op.create_index("ix_executive_rounds_status", "executive_rounds", ["status"])

    op.create_table(
        "executive_source_receipts",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=True),
        sa.Column("round_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "executive_key",
            _enum(
                "executive_receipt_key_enum",
                "ceo",
                "coo",
                "cfo",
                "cro",
                "cpo",
                "cto",
                "cio",
                "chro",
            ),
            nullable=True,
        ),
        sa.Column("pipeline_version", sa.String(40), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("period", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "structured_queries", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "selected_sources", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "rejected_sources", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "insufficiency_reasons", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        workspace_fk(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["executive_threads.workspace_id", "executive_threads.id"],
            name="fk_executive_receipt_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "round_id"],
            ["executive_rounds.workspace_id", "executive_rounds.id"],
            name="fk_executive_receipt_workspace_round",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("workspace_id", ["workspace_id"]),
        ("thread_id", ["thread_id"]),
        ("round_id", ["round_id"]),
        ("query_hash", ["query_hash"]),
        ("generated_at", ["generated_at"]),
    ):
        op.create_index(
            f"ix_executive_source_receipts_{name}", "executive_source_receipts", columns
        )

    op.create_table(
        "executive_messages",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("thread_id", FlexibleUUID(), nullable=False),
        sa.Column("round_id", FlexibleUUID(), nullable=False),
        sa.Column(
            "author_type",
            _enum("executive_author_type_enum", "human", "executive", "system"),
            nullable=False,
        ),
        sa.Column("author_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "executive_key",
            _enum(
                "executive_message_key_enum",
                "ceo",
                "coo",
                "cfo",
                "cro",
                "cpo",
                "cto",
                "cio",
                "chro",
            ),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "message_kind",
            _enum(
                "executive_message_kind_enum",
                "question",
                "response",
                "synthesis",
                "notice",
                "error",
            ),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="completed"),
        sa.Column("source_receipt_id", FlexibleUUID(), nullable=True),
        sa.Column("model_run_id", FlexibleUUID(), nullable=True),
        *timestamps(),
        workspace_fk(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["executive_threads.workspace_id", "executive_threads.id"],
            name="fk_executive_message_workspace_thread",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "round_id"],
            ["executive_rounds.workspace_id", "executive_rounds.id"],
            name="fk_executive_message_workspace_round",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "author_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_executive_message_author_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_receipt_id"], ["executive_source_receipts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["model_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "sequence", name="uq_executive_message_round_sequence"),
    )
    op.create_index("ix_executive_messages_workspace_id", "executive_messages", ["workspace_id"])
    op.create_index("ix_executive_messages_thread_id", "executive_messages", ["thread_id"])
    op.create_index("ix_executive_messages_round_id", "executive_messages", ["round_id"])
    op.create_index(
        "ix_executive_messages_thread_sequence", "executive_messages", ["thread_id", "sequence"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_executive_round_user_message",
            "executive_rounds",
            "executive_messages",
            ["user_message_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "executive_reports",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "executive_report_status_enum",
                "scheduled",
                "collecting_data",
                "generating",
                "published",
                "published_with_gaps",
                "partial_failure",
                "failed",
                "superseded",
            ),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "executive_participants", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("source_receipt_id", FlexibleUUID(), nullable=True),
        sa.Column("generation_key", sa.String(100), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", FlexibleUUID(), nullable=True),
        *timestamps(),
        workspace_fk(),
        sa.ForeignKeyConstraint(
            ["source_receipt_id"], ["executive_source_receipts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["executive_reports.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "generation_key", name="uq_executive_report_generation_key"
        ),
        sa.CheckConstraint("period_end >= period_start", name="ck_executive_report_period"),
    )
    op.create_index("ix_executive_reports_workspace_id", "executive_reports", ["workspace_id"])
    op.create_index("ix_executive_reports_status", "executive_reports", ["status"])
    op.create_index(
        "ix_executive_reports_workspace_period", "executive_reports", ["workspace_id", "period_end"]
    )
    _tenant_rls(True)


def downgrade() -> None:
    _tenant_rls(False)
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "fk_executive_round_user_message", "executive_rounds", type_="foreignkey"
        )
    for table in reversed(RLS_TABLES):
        op.drop_table(table)
