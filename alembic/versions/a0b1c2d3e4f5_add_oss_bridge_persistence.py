"""add generic OSS bridge persistence

Revision ID: a0b1c2d3e4f5
Revises: 9c0d1e2f3a4b
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "a0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "9c0d1e2f3a4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _provider_enum() -> sa.Enum:
    return sa.Enum(
        "chatwoot",
        "rocketchat",
        "chattermate",
        "bottlecrm",
        name="oss_bridge_provider_enum",
        native_enum=False,
        create_constraint=True,
        length=32,
    )


def upgrade() -> None:
    op.create_table(
        "oss_external_entity_links",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", _provider_enum(), nullable=False),
        sa.Column("klm_entity_type", sa.String(length=100), nullable=False),
        sa.Column("klm_entity_id", FlexibleUUID(), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=False),
        sa.Column("external_entity_type", sa.String(length=100), nullable=False),
        sa.Column("external_entity_id", sa.String(length=255), nullable=False),
        sa.Column(
            "metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "sync_state",
            sa.Enum(
                "pending",
                "synced",
                "stale",
                "failed",
                "disabled",
                name="oss_bridge_sync_state_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            "klm_entity_type",
            "klm_entity_id",
            "external_entity_type",
            name="uq_oss_link_workspace_provider_klm_entity",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            "external_entity_type",
            "external_entity_id",
            name="uq_oss_link_workspace_provider_external_entity",
        ),
    )
    op.create_index(
        "ix_oss_links_workspace_sync_state",
        "oss_external_entity_links",
        ["workspace_id", "provider", "sync_state"],
        unique=False,
    )

    op.create_table(
        "oss_integration_event_receipts",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", _provider_enum(), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "received",
                "processing",
                "processed",
                "failed",
                "ignored",
                name="oss_bridge_receipt_status_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="received",
            nullable=False,
        ),
        sa.Column(
            "payload",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "external_event_id",
            name="uq_oss_receipt_workspace_provider_event",
        ),
    )
    op.create_index(
        "ix_oss_receipts_workspace_status_received",
        "oss_integration_event_receipts",
        ["workspace_id", "status", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_oss_receipts_workspace_status_received",
        table_name="oss_integration_event_receipts",
    )
    op.drop_table("oss_integration_event_receipts")
    op.drop_index(
        "ix_oss_links_workspace_sync_state",
        table_name="oss_external_entity_links",
    )
    op.drop_table("oss_external_entity_links")
