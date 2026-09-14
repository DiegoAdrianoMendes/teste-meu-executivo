"""Add tenant-scoped metadata for versioned local object storage.

Revision ID: d5f7a9c1e004
Revises: c4e6f8a0d003
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "d5f7a9c1e004"
down_revision: str | Sequence[str] | None = "c4e6f8a0d003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "workspace_current_id"
WORKSPACE_SETTING = "app.current_workspace_id"


def _state_enum() -> sa.Enum:
    return sa.Enum(
        "pending",
        "quarantined",
        "available",
        name="storage_object_state_enum",
        native_enum=False,
        create_constraint=True,
    )


def _av_status_enum() -> sa.Enum:
    return sa.Enum(
        "pending",
        "simulated_unverified",
        "simulated_blocked",
        "clean",
        "infected",
        "error",
        name="storage_av_status_enum",
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    op.create_table(
        "storage_objects",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("object_id", FlexibleUUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("matter_id", FlexibleUUID(), nullable=True),
        sa.Column("client_id", FlexibleUUID(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_content_type", sa.String(length=127), nullable=False),
        sa.Column("detected_content_type", sa.String(length=127), nullable=True),
        sa.Column("expected_byte_size", sa.BigInteger(), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("expected_sha256", sa.String(length=64), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("quarantine_key", sa.Text(), nullable=True),
        sa.Column(
            "state",
            _state_enum(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "av_status",
            _av_status_enum(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("quarantine_reason", sa.String(length=255), nullable=True),
        sa.Column("scan_method", sa.String(length=64), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("deleted_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("restored_by_user_id", FlexibleUUID(), nullable=True),
        sa.Column("upload_ticket_hash", sa.String(length=64), nullable=True),
        sa.Column("upload_ticket_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upload_ticket_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticket_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "reconciliation_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
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
        sa.CheckConstraint("version > 0", name="ck_storage_object_version_positive"),
        sa.CheckConstraint(
            "expected_byte_size IS NULL OR expected_byte_size >= 0",
            name="ck_storage_object_expected_size_nonnegative",
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_storage_object_size_nonnegative",
        ),
        sa.CheckConstraint(
            "expected_sha256 IS NULL OR length(expected_sha256) = 64",
            name="ck_storage_object_expected_sha256_length",
        ),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR length(checksum_sha256) = 64",
            name="ck_storage_object_sha256_length",
        ),
        sa.CheckConstraint(
            "ticket_epoch >= 0",
            name="ck_storage_object_ticket_epoch_nonnegative",
        ),
        sa.CheckConstraint(
            "reconciliation_attempts >= 0",
            name="ck_storage_object_reconciliation_attempts_nonnegative",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["restored_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "object_id",
            "version",
            name="uq_storage_object_workspace_object_version",
        ),
        sa.UniqueConstraint("storage_key", name="uq_storage_object_storage_key"),
    )
    op.create_index(
        "ix_storage_object_workspace_object_version",
        "storage_objects",
        ["workspace_id", "object_id", "version"],
    )
    op.create_index(
        "ix_storage_object_workspace_state",
        "storage_objects",
        ["workspace_id", "state", "deleted_at"],
    )
    op.create_index(
        "ix_storage_objects_matter_id",
        "storage_objects",
        ["matter_id"],
    )
    op.create_index(
        "ix_storage_objects_client_id",
        "storage_objects",
        ["client_id"],
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = (
            f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
        )
        op.execute('ALTER TABLE "storage_objects" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "storage_objects" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{POLICY_NAME}" ON "storage_objects"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "storage_objects"'
        )
        op.execute('ALTER TABLE "storage_objects" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "storage_objects" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_storage_objects_client_id", table_name="storage_objects")
    op.drop_index("ix_storage_objects_matter_id", table_name="storage_objects")
    op.drop_index("ix_storage_object_workspace_state", table_name="storage_objects")
    op.drop_index(
        "ix_storage_object_workspace_object_version",
        table_name="storage_objects",
    )
    op.drop_table("storage_objects")
