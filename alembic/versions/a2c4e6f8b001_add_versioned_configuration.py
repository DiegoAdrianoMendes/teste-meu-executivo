"""Add hierarchical versioned configuration documents.

Revision ID: a2c4e6f8b001
Revises: 99d0e1f2a3b4
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "a2c4e6f8b001"
down_revision: str | Sequence[str] | None = "99d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"

config_scope_enum = sa.Enum(
    "platform",
    "workspace",
    "role",
    "user",
    name="config_scope_enum",
    native_enum=False,
    create_constraint=True,
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


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    direct = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    parent = (
        "EXISTS (SELECT 1 FROM config_documents WHERE "
        "config_documents.id = config_document_versions.document_id AND "
        "config_documents.workspace_id::text = "
        f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), ''))"
    )
    for table_name, predicate in (
        ("config_documents", direct),
        ("config_document_versions", parent),
    ):
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})
            """
        )


def upgrade() -> None:
    op.create_table(
        "platform_config_documents",
        sa.Column("namespace", sa.String(length=160), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("etag", sa.String(length=64), nullable=False),
        sa.Column("payload", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", name="uq_platform_config_namespace"),
    )
    op.create_table(
        "config_documents",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("namespace", sa.String(length=160), nullable=False),
        sa.Column("scope", config_scope_enum, nullable=False),
        sa.Column("scope_key", sa.String(length=320), nullable=False),
        sa.Column("role_key", sa.String(length=80), nullable=True),
        sa.Column("user_id", FlexibleUUID(), nullable=True),
        sa.Column("schema_version", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("etag", sa.String(length=64), nullable=False),
        sa.Column("payload", FlexibleJSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("updated_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "scope_key", name="uq_config_document_scope_key"),
    )
    op.create_index(
        "ix_config_document_workspace_namespace",
        "config_documents",
        ["workspace_id", "namespace"],
    )
    op.create_table(
        "platform_config_document_versions",
        sa.Column("document_id", FlexibleUUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("payload", FlexibleJSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["platform_config_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version", name="uq_platform_config_document_version"),
    )
    op.create_table(
        "config_document_versions",
        sa.Column("document_id", FlexibleUUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False, server_default="1.0"),
        sa.Column("payload", FlexibleJSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["config_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version", name="uq_config_document_version"),
    )
    op.create_index(
        "ix_config_version_document_created",
        "config_document_versions",
        ["document_id", "created_at"],
    )
    _enable_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in ("config_document_versions", "config_documents"):
            op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')
    op.drop_index("ix_config_version_document_created", table_name="config_document_versions")
    op.drop_table("config_document_versions")
    op.drop_table("platform_config_document_versions")
    op.drop_index("ix_config_document_workspace_namespace", table_name="config_documents")
    op.drop_table("config_documents")
    op.drop_table("platform_config_documents")
