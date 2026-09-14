"""add workspace public api tokens

Revision ID: 3c4d5e6f7a9
Revises: 2b3c4d5e6f8
Create Date: 2026-04-07 19:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.base import FlexibleJSON, FlexibleUUID


revision = "3c4d5e6f7a9"
down_revision = "2b3c4d5e6f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_public_api_tokens",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("created_by_id", FlexibleUUID(), nullable=True),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("scopes", FlexibleJSON(), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_prefix"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_workspace_public_api_tokens_workspace_active",
        "workspace_public_api_tokens",
        ["workspace_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_public_api_tokens_prefix",
        "workspace_public_api_tokens",
        ["token_prefix"],
        unique=False,
    )

    op.create_table(
        "public_api_request_logs",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("token_id", FlexibleUUID(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("request_metadata", FlexibleJSON(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["token_id"], ["workspace_public_api_tokens.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_public_api_request_logs_workspace_requested",
        "public_api_request_logs",
        ["workspace_id", "requested_at"],
        unique=False,
    )
    op.create_index(
        "ix_public_api_request_logs_token_requested",
        "public_api_request_logs",
        ["token_id", "requested_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_public_api_request_logs_token_requested", table_name="public_api_request_logs")
    op.drop_index("ix_public_api_request_logs_workspace_requested", table_name="public_api_request_logs")
    op.drop_table("public_api_request_logs")

    op.drop_index("ix_workspace_public_api_tokens_prefix", table_name="workspace_public_api_tokens")
    op.drop_index("ix_workspace_public_api_tokens_workspace_active", table_name="workspace_public_api_tokens")
    op.drop_table("workspace_public_api_tokens")
