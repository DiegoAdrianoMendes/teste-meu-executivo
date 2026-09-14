"""Add immutable human reconciliation for uncertain provider media uploads.

Revision ID: a2c4e6f8b10c
Revises: 9d1f3a5c7b09
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "a2c4e6f8b10c"
down_revision: str | Sequence[str] | None = "9d1f3a5c7b09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "klm_omnichannel_provider_media_upload_reconciliations"
POLICY_NAME = "workspace_current_id"
IMMUTABLE_TRIGGER = "trg_klm_provider_media_recon_immutable"
TRUNCATE_TRIGGER = "trg_klm_provider_media_recon_immutable_truncate"
INDEX_NAMES = {
    "workspace_id": "ix_klm_provider_media_recon_workspace",
    "connection_id": "ix_klm_provider_media_recon_connection",
    "upload_intent_id": "ix_klm_provider_media_recon_intent",
    "actor_user_id": "ix_klm_provider_media_recon_actor",
    "decision": "ix_klm_provider_media_recon_decision",
    "resolved_at": "ix_klm_provider_media_recon_resolved",
}


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = (
        "workspace_id::text = "
        "NULLIF(current_setting('app.current_workspace_id', true), '')"
    )
    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'''CREATE POLICY "{POLICY_NAME}" ON "{TABLE_NAME}"
        AS PERMISSIVE FOR ALL TO PUBLIC
        USING ({predicate}) WITH CHECK ({predicate})'''
    )


def _disable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{TABLE_NAME}"')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY')


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
            f'''CREATE TRIGGER "{IMMUTABLE_TRIGGER}"
            BEFORE UPDATE OR DELETE ON "{TABLE_NAME}"
            FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
        )
        op.execute(
            f'''CREATE TRIGGER "{TRUNCATE_TRIGGER}"
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
            f'DROP TRIGGER IF EXISTS "{TRUNCATE_TRIGGER}" '
            f'ON "{TABLE_NAME}"'
        )
        op.execute(
            f'DROP TRIGGER IF EXISTS "{IMMUTABLE_TRIGGER}" ON "{TABLE_NAME}"'
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
        sa.Column("upload_intent_id", FlexibleUUID(), nullable=False),
        sa.Column("actor_user_id", FlexibleUUID(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("provider_media_id", sa.String(length=255), nullable=True),
        sa.Column(
            "provider_receipt",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('provider_accepted', 'retry_authorized')",
            name="ck_klm_provider_media_recon_decision",
        ),
        sa.CheckConstraint(
            "length(reason) BETWEEN 3 AND 1000",
            name="ck_klm_provider_media_recon_reason_length",
        ),
        sa.CheckConstraint(
            "(decision = 'provider_accepted' AND provider_media_id IS NOT NULL) OR "
            "(decision = 'retry_authorized' AND provider_media_id IS NULL)",
            name="ck_klm_provider_media_recon_evidence",
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
            name="fk_klm_provider_media_recon_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "upload_intent_id"],
            [
                "klm_omnichannel_provider_media_upload_intents.workspace_id",
                "klm_omnichannel_provider_media_upload_intents.id",
            ],
            name="fk_klm_provider_media_recon_workspace_intent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_provider_media_recon_workspace_id",
        ),
    )
    for column, index_name in INDEX_NAMES.items():
        op.create_index(index_name, TABLE_NAME, [column])
    op.create_index(
        "ix_klm_provider_media_recon_intent_time",
        TABLE_NAME,
        ["workspace_id", "upload_intent_id", "resolved_at"],
    )
    _enable_rls()
    _enable_append_only_guard()


def downgrade() -> None:
    _disable_append_only_guard()
    _disable_rls()
    op.drop_table(TABLE_NAME)
