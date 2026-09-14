"""add separate SaaS staff and workspace access packages

Revision ID: b8d1e3f5a702
Revises: a7c9e1f3b501
Create Date: 2026-07-20 08:25:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON

revision: str = "b8d1e3f5a702"
down_revision: str | Sequence[str] | None = "a7c9e1f3b501"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("platform_role_package", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "platform_access_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "platform_permission_grants",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "platform_permission_denials",
            FlexibleJSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.create_index(
        "ix_users_platform_access_package",
        "users",
        ["platform_access_active", "platform_role_package"],
        unique=False,
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("access_package", sa.String(length=80), nullable=True),
    )
    op.execute(
        "UPDATE workspace_memberships SET access_package = 'office_owner' "
        "WHERE role = 'workspace_owner' AND access_package IS NULL"
    )
    op.execute(
        "UPDATE workspace_memberships SET access_package = 'office_admin' "
        "WHERE role = 'workspace_admin' AND access_package IS NULL"
    )
    op.create_index(
        "ix_workspace_memberships_access_package",
        "workspace_memberships",
        ["workspace_id", "access_package"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_memberships_access_package",
        table_name="workspace_memberships",
    )
    op.drop_column("workspace_memberships", "access_package")
    op.drop_index("ix_users_platform_access_package", table_name="users")
    op.drop_column("users", "platform_permission_denials")
    op.drop_column("users", "platform_permission_grants")
    op.drop_column("users", "platform_access_active")
    op.drop_column("users", "platform_role_package")
