"""Add tenant-bound immutable provider media upload receipts.

Revision ID: 9d1f3a5c7b09
Revises: 8c0e2f4a6b98
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "9d1f3a5c7b09"
down_revision: str | Sequence[str] | None = "8c0e2f4a6b98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "klm_omnichannel_provider_media_receipts"
INTENT_TABLE_NAME = "klm_omnichannel_provider_media_upload_intents"
POLICY_NAME = "workspace_current_id"
INTENT_INDEX_NAMES = {
    "workspace_id": "ix_klm_provider_media_intent_workspace",
    "connection_id": "ix_klm_provider_media_intent_connection",
    "provider": "ix_klm_provider_media_intent_provider",
    "idempotency_key_hash": "ix_klm_provider_media_intent_idem_key",
    "state": "ix_klm_provider_media_intent_state",
    "provider_media_id": "ix_klm_provider_media_intent_media_id",
    "receipt_id": "ix_klm_provider_media_intent_receipt",
}


def _enable_rls(table_name: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{POLICY_NAME}" ON "{table_name}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls(table_name: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table_name}"')
    op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _enable_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION klm_reject_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            f'''CREATE TRIGGER "trg_{TABLE_NAME}_immutable"
            BEFORE UPDATE OR DELETE ON "{TABLE_NAME}"
            FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
        )
        op.execute(
            f'''CREATE TRIGGER "trg_{TABLE_NAME}_immutable_truncate"
            BEFORE TRUNCATE ON "{TABLE_NAME}"
            FOR EACH STATEMENT EXECUTE FUNCTION klm_reject_append_only_mutation()'''
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'''
                CREATE TRIGGER IF NOT EXISTS "trg_{TABLE_NAME}_immutable_{operation}"
                BEFORE {operation.upper()} ON "{TABLE_NAME}"
                BEGIN
                    SELECT RAISE(ABORT, '{TABLE_NAME} is immutable');
                END
                '''
            )


def _disable_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f'DROP TRIGGER IF EXISTS "trg_{TABLE_NAME}_immutable_truncate" '
            f'ON "{TABLE_NAME}"'
        )
        op.execute(
            f'DROP TRIGGER IF EXISTS "trg_{TABLE_NAME}_immutable" ON "{TABLE_NAME}"'
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'DROP TRIGGER IF EXISTS "trg_{TABLE_NAME}_immutable_{operation}"'
            )


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_media_id", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=24), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_content_type", sa.String(length=160), nullable=False),
        sa.Column("provider_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "provider_receipt",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "media_type IN ('image', 'audio', 'video', 'document')",
            name="ck_klm_provider_media_receipt_media_type",
        ),
        sa.CheckConstraint(
            "length(source_sha256) = 64 AND length(provider_payload_sha256) = 64",
            name="ck_klm_provider_media_receipt_sha256_length",
        ),
        sa.CheckConstraint(
            "source_size_bytes > 0",
            name="ck_klm_provider_media_receipt_source_size_positive",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_provider_media_receipt_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_provider_media_receipt_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider",
            "provider_media_id",
            name="uq_klm_provider_media_receipt_provider_id",
        ),
    )
    for column in (
        "workspace_id",
        "connection_id",
        "provider",
        "provider_media_id",
        "uploaded_at",
        "expires_at",
        "recorded_at",
    ):
        op.create_index(f"ix_{TABLE_NAME}_{column}", TABLE_NAME, [column])
    op.create_index(
        "ix_klm_provider_media_receipt_connection_recorded",
        TABLE_NAME,
        ["workspace_id", "connection_id", "recorded_at"],
    )
    _enable_rls(TABLE_NAME)
    _enable_append_only_guard()

    op.create_table(
        INTENT_TABLE_NAME,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=24), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_content_type", sa.String(length=160), nullable=False),
        sa.Column("source_extension", sa.String(length=16), nullable=False),
        sa.Column("source_filename_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default="prepared",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider_media_id", sa.String(length=255), nullable=True),
        sa.Column(
            "provider_receipt",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("receipt_id", FlexibleUUID(), nullable=True),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_uncertain_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
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
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "state IN ('prepared', 'dispatching', 'provider_accepted', 'completed', "
            "'failed', 'delivery_uncertain')",
            name="ck_klm_provider_media_intent_state",
        ),
        sa.CheckConstraint(
            "media_type IN ('image', 'audio', 'video', 'document')",
            name="ck_klm_provider_media_intent_media_type",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(payload_sha256) = 64 "
            "AND length(source_sha256) = 64 AND length(source_filename_sha256) = 64",
            name="ck_klm_provider_media_intent_hash_lengths",
        ),
        sa.CheckConstraint(
            "source_size_bytes > 0 AND attempt_count >= 0",
            name="ck_klm_provider_media_intent_positive_values",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_provider_media_intent_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "receipt_id"],
            [
                "klm_omnichannel_provider_media_receipts.workspace_id",
                "klm_omnichannel_provider_media_receipts.id",
            ],
            name="fk_klm_provider_media_intent_workspace_receipt",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_provider_media_intent_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key_hash",
            name="uq_klm_provider_media_intent_idempotency",
        ),
    )
    for column, index_name in INTENT_INDEX_NAMES.items():
        op.create_index(index_name, INTENT_TABLE_NAME, [column])
    op.create_index(
        "ix_klm_provider_media_intent_connection_state",
        INTENT_TABLE_NAME,
        ["workspace_id", "connection_id", "state"],
    )
    _enable_rls(INTENT_TABLE_NAME)


def downgrade() -> None:
    _disable_rls(INTENT_TABLE_NAME)
    op.drop_table(INTENT_TABLE_NAME)
    _disable_append_only_guard()
    _disable_rls(TABLE_NAME)
    op.drop_table(TABLE_NAME)
