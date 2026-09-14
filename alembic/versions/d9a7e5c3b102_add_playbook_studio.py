"""Add tenant-bound Playbook Studio governance, scope and simulation tables.

Revision ID: d9a7e5c3b102
Revises: c8f5d2a9b714
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "d9a7e5c3b102"
down_revision: str | Sequence[str] | None = "c8f5d2a9b714"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"

playbook_lifecycle_state_enum = sa.Enum(
    "draft", "in_review", "approved", "published", "superseded",
    name="playbook_lifecycle_state_enum", native_enum=False, create_constraint=True,
)
playbook_scope_dimension_enum = sa.Enum(
    "area", "channel", "stage", "agent", "offer",
    name="playbook_scope_dimension_enum", native_enum=False, create_constraint=True,
)
playbook_simulation_status_enum = sa.Enum(
    "passed", "failed", "needs_review",
    name="playbook_simulation_status_enum", native_enum=False, create_constraint=True,
)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    )
    for table_name in (
        "playbook_version_governance",
        "playbook_scope_bindings",
        "playbook_simulation_runs",
    ):
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def upgrade() -> None:
    op.create_table(
        "playbook_version_governance",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("document_id", FlexibleUUID(), nullable=False),
        sa.Column("document_version_id", FlexibleUUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", playbook_lifecycle_state_enum, nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        _created_at(),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "(state = 'draft' AND sequence = 1) OR "
            "(state = 'in_review' AND sequence = 2) OR "
            "(state = 'approved' AND sequence = 3) OR "
            "(state = 'published' AND sequence = 4) OR "
            "(state = 'superseded' AND sequence = 5)",
            name="ck_playbook_governance_state_sequence",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["config_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["config_document_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version", "sequence", name="uq_playbook_governance_sequence"),
        sa.UniqueConstraint("document_id", "version", "state", name="uq_playbook_governance_state"),
    )
    op.create_index(
        "ix_playbook_governance_workspace_document",
        "playbook_version_governance",
        ["workspace_id", "document_id", "version"],
    )

    op.create_table(
        "playbook_scope_bindings",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("document_id", FlexibleUUID(), nullable=False),
        sa.Column("document_version_id", FlexibleUUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("dimension", playbook_scope_dimension_enum, nullable=False),
        sa.Column("value", sa.String(length=160), nullable=False),
        _created_at(),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["config_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["config_document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version", "dimension", "value", name="uq_playbook_scope_binding"),
    )
    op.create_index(
        "ix_playbook_scope_resolver",
        "playbook_scope_bindings",
        ["workspace_id", "dimension", "value"],
    )

    op.create_table(
        "playbook_simulation_runs",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("document_id", FlexibleUUID(), nullable=False),
        sa.Column("document_version_id", FlexibleUUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("status", playbook_simulation_status_enum, nullable=False),
        sa.Column("scenario", sa.String(length=240), nullable=False),
        sa.Column("customer_message", sa.String(length=4096), nullable=False),
        sa.Column("context", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("response", sa.String(length=8192), nullable=False),
        sa.Column("next_action", sa.String(length=1000), nullable=False),
        sa.Column("policy_trace", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("sources", FlexibleJSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("autonomy_mode", sa.String(length=80), nullable=False),
        sa.Column("human_approval_required", sa.Boolean(), nullable=False),
        sa.Column("external_actions_executed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_brl", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("model_key", sa.String(length=120), nullable=False, server_default="local-deterministic-v1"),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        _created_at(),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint("external_actions_executed = 0", name="ck_playbook_simulation_local_only"),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND estimated_cost_brl >= 0",
            name="ck_playbook_simulation_nonnegative_usage",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["config_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["config_document_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_playbook_simulation_document_created",
        "playbook_simulation_runs",
        ["workspace_id", "document_id", "created_at"],
    )
    _enable_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in (
            "playbook_simulation_runs",
            "playbook_scope_bindings",
            "playbook_version_governance",
        ):
            op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
    op.drop_index("ix_playbook_simulation_document_created", table_name="playbook_simulation_runs")
    op.drop_table("playbook_simulation_runs")
    op.drop_index("ix_playbook_scope_resolver", table_name="playbook_scope_bindings")
    op.drop_table("playbook_scope_bindings")
    op.drop_index("ix_playbook_governance_workspace_document", table_name="playbook_version_governance")
    op.drop_table("playbook_version_governance")
