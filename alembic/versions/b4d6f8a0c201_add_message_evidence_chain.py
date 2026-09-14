"""Add immutable provider envelopes and message evidence chains.

Revision ID: b4d6f8a0c201
Revises: a2c4e6f8b10c
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "b4d6f8a0c201"
down_revision: str | Sequence[str] | None = "a2c4e6f8b10c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENVELOPE_TABLE = "klm_omnichannel_provider_webhook_envelopes"
EVIDENCE_TABLE = "klm_omnichannel_message_evidence_events"


def _append_only(table_name: str) -> None:
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
            f'''CREATE TRIGGER "trg_{table_name}_immutable"
            BEFORE UPDATE OR DELETE ON "{table_name}"
            FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
        )
        op.execute(
            f'''CREATE TRIGGER "trg_{table_name}_immutable_truncate"
            BEFORE TRUNCATE ON "{table_name}"
            FOR EACH STATEMENT EXECUTE FUNCTION klm_reject_append_only_mutation()'''
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'''
                CREATE TRIGGER IF NOT EXISTS "trg_{table_name}_immutable_{operation}"
                BEFORE {operation.upper()} ON "{table_name}"
                BEGIN
                    SELECT RAISE(ABORT, '{table_name} is immutable');
                END
                '''
            )


def _drop_append_only(table_name: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_truncate" ON "{table_name}"'
        )
        op.execute(
            f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"'
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_{operation}"'
            )


def _enable_evidence_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )
    op.execute(f'ALTER TABLE "{EVIDENCE_TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{EVIDENCE_TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "workspace_current_id" ON "{EVIDENCE_TABLE}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_evidence_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "workspace_current_id" ON "{EVIDENCE_TABLE}"')
    op.execute(f'ALTER TABLE "{EVIDENCE_TABLE}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{EVIDENCE_TABLE}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.add_column(
        "klm_omnichannel_messages",
        sa.Column(
            "content_lifecycle_state",
            sa.String(length=32),
            server_default="original",
            nullable=False,
        ),
    )
    op.add_column(
        "klm_omnichannel_messages",
        sa.Column("evidence_event_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "klm_omnichannel_messages",
        sa.Column("evidence_revision_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "klm_omnichannel_messages",
        sa.Column("evidence_chain_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "klm_omnichannel_messages",
        sa.Column("content_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "klm_omnichannel_messages",
        sa.Column("content_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("klm_omnichannel_messages") as batch:
        batch.create_check_constraint(
            "ck_klm_message_content_lifecycle_state",
            "content_lifecycle_state IN ('original', 'edited', 'deleted', 'provider_unsupported')",
        )
        batch.create_check_constraint(
            "ck_klm_message_evidence_counts",
            "evidence_event_count >= 0 AND evidence_revision_count >= 0",
        )
        batch.create_check_constraint(
            "ck_klm_message_evidence_chain_sha256",
            "evidence_chain_sha256 IS NULL OR length(evidence_chain_sha256) = 64",
        )
    op.create_index(
        "ix_klm_omnichannel_messages_content_lifecycle_state",
        "klm_omnichannel_messages",
        ["content_lifecycle_state"],
    )

    op.create_table(
        ENVELOPE_TABLE,
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("signature_header", sa.Text(), nullable=False),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_scheme", sa.String(length=40), nullable=False),
        sa.Column(
            "signature_verified",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "request_metadata",
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
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_klm_provider_envelope_byte_size"),
        sa.CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_klm_provider_envelope_payload_sha256",
        ),
        sa.CheckConstraint(
            "length(signature_sha256) = 64",
            name="ck_klm_provider_envelope_signature_sha256",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_klm_provider_envelope_provider", ENVELOPE_TABLE, ["provider"])
    op.create_index("ix_klm_provider_envelope_received_at", ENVELOPE_TABLE, ["received_at"])
    op.create_index(
        "ix_klm_provider_envelope_provider_received",
        ENVELOPE_TABLE,
        ["provider", "received_at"],
    )
    op.create_index(
        "ix_klm_provider_envelope_payload_sha256",
        ENVELOPE_TABLE,
        ["payload_sha256"],
    )

    op.create_table(
        EVIDENCE_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("chain_id", FlexibleUUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=True),
        sa.Column("message_id", FlexibleUUID(), nullable=True),
        sa.Column("thread_id", FlexibleUUID(), nullable=True),
        sa.Column("channel_event_id", FlexibleUUID(), nullable=True),
        sa.Column("connection_id", FlexibleUUID(), nullable=True),
        sa.Column("legacy_whatsapp_message_id", FlexibleUUID(), nullable=True),
        sa.Column("envelope_id", FlexibleUUID(), nullable=True),
        sa.Column("previous_event_id", FlexibleUUID(), nullable=True),
        sa.Column("storage_object_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column(
            "observation_state",
            sa.String(length=32),
            server_default="observed",
            nullable=False,
        ),
        sa.Column("content_type", sa.String(length=80), nullable=True),
        sa.Column("body_snapshot", sa.Text(), nullable=True),
        sa.Column(
            "structured_content",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("event_payload", FlexibleJSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("event_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_envelope_sha256", sa.String(length=64), nullable=True),
        sa.Column("signature_sha256", sa.String(length=64), nullable=True),
        sa.Column("media_sha256", sa.String(length=64), nullable=True),
        sa.Column("media_byte_size", sa.BigInteger(), nullable=True),
        sa.Column("media_mime_type", sa.String(length=127), nullable=True),
        sa.Column("media_filename", sa.String(length=500), nullable=True),
        sa.Column("provider_media_id", sa.String(length=255), nullable=True),
        sa.Column("storage_object_version", sa.Integer(), nullable=True),
        sa.Column("previous_evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("provider_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_klm_message_evidence_revision"),
        sa.CheckConstraint(
            "content_revision IS NULL OR content_revision > 0",
            name="ck_klm_message_evidence_content_revision",
        ),
        sa.CheckConstraint(
            "event_type IN ('captured', 'edited', 'deleted', 'provider_receipt', "
            "'media_captured', 'media_unavailable', 'provider_unsupported')",
            name="ck_klm_message_evidence_event_type",
        ),
        sa.CheckConstraint(
            "observation_state IN ('observed', 'provider_signal', 'inferred', 'unavailable')",
            name="ck_klm_message_evidence_observation_state",
        ),
        sa.CheckConstraint(
            "length(event_payload_sha256) = 64",
            name="ck_klm_message_evidence_payload_sha256",
        ),
        sa.CheckConstraint("length(evidence_sha256) = 64", name="ck_klm_message_evidence_sha256"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["klm_omnichannel_messages.workspace_id", "klm_omnichannel_messages.id"],
            name="fk_klm_message_evidence_workspace_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["klm_omnichannel_threads.workspace_id", "klm_omnichannel_threads.id"],
            name="fk_klm_message_evidence_workspace_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "channel_event_id"],
            ["klm_omnichannel_channel_events.workspace_id", "klm_omnichannel_channel_events.id"],
            name="fk_klm_message_evidence_workspace_channel_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_message_evidence_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "previous_event_id"],
            [EVIDENCE_TABLE + ".workspace_id", EVIDENCE_TABLE + ".id"],
            name="fk_klm_message_evidence_workspace_previous",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "storage_object_id"],
            ["storage_objects.workspace_id", "storage_objects.id"],
            name="fk_klm_message_evidence_workspace_storage_object",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_whatsapp_message_id"],
            ["whatsapp_messages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["envelope_id"], [ENVELOPE_TABLE + ".id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "chain_id",
            "revision",
            name="uq_klm_message_evidence_chain_revision",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_message_evidence_idempotency",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_message_evidence_workspace_id"),
    )
    for column in (
        "workspace_id",
        "chain_id",
        "message_id",
        "thread_id",
        "channel_event_id",
        "connection_id",
        "legacy_whatsapp_message_id",
        "envelope_id",
        "previous_event_id",
        "storage_object_id",
        "provider",
        "event_type",
        "received_at",
    ):
        op.create_index(f"ix_klm_message_evidence_{column}", EVIDENCE_TABLE, [column])
    op.create_index(
        "ix_klm_message_evidence_message_time",
        EVIDENCE_TABLE,
        ["workspace_id", "message_id", "received_at"],
    )
    op.create_index(
        "ix_klm_message_evidence_external",
        EVIDENCE_TABLE,
        ["workspace_id", "provider", "connection_key", "external_message_id"],
    )
    _enable_evidence_rls()
    _append_only(ENVELOPE_TABLE)
    _append_only(EVIDENCE_TABLE)


def downgrade() -> None:
    _drop_append_only(EVIDENCE_TABLE)
    _drop_append_only(ENVELOPE_TABLE)
    _disable_evidence_rls()
    op.drop_table(EVIDENCE_TABLE)
    op.drop_table(ENVELOPE_TABLE)
    op.drop_index(
        "ix_klm_omnichannel_messages_content_lifecycle_state",
        table_name="klm_omnichannel_messages",
    )
    with op.batch_alter_table("klm_omnichannel_messages") as batch:
        batch.drop_constraint("ck_klm_message_evidence_chain_sha256", type_="check")
        batch.drop_constraint("ck_klm_message_evidence_counts", type_="check")
        batch.drop_constraint("ck_klm_message_content_lifecycle_state", type_="check")
    for column in (
        "content_deleted_at",
        "content_edited_at",
        "evidence_chain_sha256",
        "evidence_revision_count",
        "evidence_event_count",
        "content_lifecycle_state",
    ):
        op.drop_column("klm_omnichannel_messages", column)
