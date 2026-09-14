"""Add signed public omnichannel ingress credentials and immutable receipts.

Revision ID: fc4a6d8e0b53
Revises: fb3f5d7b9c42
Create Date: 2026-07-21 00:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "fc4a6d8e0b53"
down_revision: str | Sequence[str] | None = "fb3f5d7b9c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"
TABLES = (
    "klm_omnichannel_webhook_credentials",
    "klm_omnichannel_public_ingress_receipts",
)
IMMUTABLE_TABLES = ("klm_omnichannel_public_ingress_receipts",)


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY "{RLS_POLICY}" ON "{table_name}"
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING ({predicate}) WITH CHECK ({predicate})'''
        )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in TABLES:
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def _enable_append_only_guards() -> None:
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
        for table_name in IMMUTABLE_TABLES:
            op.execute(
                f'''CREATE TRIGGER "trg_{table_name}_immutable"
                BEFORE UPDATE OR DELETE ON "{table_name}"
                FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
            )
    elif dialect == "sqlite":
        for table_name in IMMUTABLE_TABLES:
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


def _disable_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table_name in IMMUTABLE_TABLES:
            op.execute(f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable" ON "{table_name}"')
    elif dialect == "sqlite":
        for table_name in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f'DROP TRIGGER IF EXISTS "trg_{table_name}_immutable_{operation}"'
                )


def upgrade() -> None:
    op.create_table(
        "klm_omnichannel_webhook_credentials",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("algorithm", sa.String(length=40), server_default="hmac_sha256", nullable=False),
        sa.Column("signing_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("verify_token_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "timestamp_tolerance_seconds", sa.Integer(), server_default="300", nullable=False
        ),
        sa.Column("state", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "timestamp_tolerance_seconds >= 30 AND timestamp_tolerance_seconds <= 3600",
            name="ck_klm_webhook_credential_timestamp_tolerance",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_webhook_credential_workspace_connection",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "connection_id", name="uq_klm_webhook_credential_connection"
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_webhook_credential_workspace_id"),
    )
    op.create_index(
        "ix_klm_omnichannel_webhook_credentials_workspace_id",
        "klm_omnichannel_webhook_credentials",
        ["workspace_id"],
    )
    op.create_index(
        "ix_klm_omnichannel_webhook_credentials_connection_id",
        "klm_omnichannel_webhook_credentials",
        ["connection_id"],
    )
    op.create_index(
        "ix_klm_omnichannel_webhook_credentials_state",
        "klm_omnichannel_webhook_credentials",
        ["state"],
    )

    op.create_table(
        "klm_omnichannel_public_ingress_receipts",
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("external_lead_id", sa.String(length=255), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signature_scheme", sa.String(length=40), nullable=False),
        sa.Column("request_metadata", FlexibleJSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_public_ingress_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_event_id",
            name="uq_klm_public_ingress_provider_event",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_klm_public_ingress_workspace_id"),
    )
    op.create_index(
        "ix_klm_omnichannel_public_ingress_receipts_workspace_id",
        "klm_omnichannel_public_ingress_receipts",
        ["workspace_id"],
    )
    op.create_index(
        "ix_klm_omnichannel_public_ingress_receipts_connection_id",
        "klm_omnichannel_public_ingress_receipts",
        ["connection_id"],
    )
    op.create_index(
        "ix_klm_omnichannel_public_ingress_receipts_provider",
        "klm_omnichannel_public_ingress_receipts",
        ["provider"],
    )
    op.create_index(
        "ix_klm_omnichannel_public_ingress_receipts_external_lead_id",
        "klm_omnichannel_public_ingress_receipts",
        ["external_lead_id"],
    )
    op.create_index(
        "ix_klm_omnichannel_public_ingress_receipts_received_at",
        "klm_omnichannel_public_ingress_receipts",
        ["received_at"],
    )
    op.create_index(
        "ix_klm_public_ingress_connection_received",
        "klm_omnichannel_public_ingress_receipts",
        ["workspace_id", "connection_id", "received_at"],
    )
    _enable_rls()
    _enable_append_only_guards()


def downgrade() -> None:
    _disable_append_only_guards()
    _disable_rls()
    op.drop_index(
        "ix_klm_public_ingress_connection_received",
        table_name="klm_omnichannel_public_ingress_receipts",
    )
    op.drop_index(
        "ix_klm_omnichannel_public_ingress_receipts_received_at",
        table_name="klm_omnichannel_public_ingress_receipts",
    )
    op.drop_index(
        "ix_klm_omnichannel_public_ingress_receipts_external_lead_id",
        table_name="klm_omnichannel_public_ingress_receipts",
    )
    op.drop_index(
        "ix_klm_omnichannel_public_ingress_receipts_provider",
        table_name="klm_omnichannel_public_ingress_receipts",
    )
    op.drop_index(
        "ix_klm_omnichannel_public_ingress_receipts_connection_id",
        table_name="klm_omnichannel_public_ingress_receipts",
    )
    op.drop_index(
        "ix_klm_omnichannel_public_ingress_receipts_workspace_id",
        table_name="klm_omnichannel_public_ingress_receipts",
    )
    op.drop_table("klm_omnichannel_public_ingress_receipts")
    op.drop_index(
        "ix_klm_omnichannel_webhook_credentials_state",
        table_name="klm_omnichannel_webhook_credentials",
    )
    op.drop_index(
        "ix_klm_omnichannel_webhook_credentials_connection_id",
        table_name="klm_omnichannel_webhook_credentials",
    )
    op.drop_index(
        "ix_klm_omnichannel_webhook_credentials_workspace_id",
        table_name="klm_omnichannel_webhook_credentials",
    )
    op.drop_table("klm_omnichannel_webhook_credentials")
