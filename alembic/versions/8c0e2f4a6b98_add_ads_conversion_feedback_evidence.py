"""Add immutable provider-specific ads conversion feedback evidence.

Revision ID: 8c0e2f4a6b98
Revises: 7b9d1f3a5c87
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "8c0e2f4a6b98"
down_revision: str | Sequence[str] | None = "7b9d1f3a5c87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "klm_ads_conversion_feedback_records"
INTENT_TABLE = "klm_external_action_intents"
POLICY_NAME = "workspace_current_id"


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
            f'''CREATE TRIGGER "trg_{TABLE_NAME}_immutable"
            BEFORE UPDATE OR DELETE ON "{TABLE_NAME}"
            FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
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
            f'DROP TRIGGER IF EXISTS "trg_{TABLE_NAME}_immutable" ON "{TABLE_NAME}"'
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'DROP TRIGGER IF EXISTS "trg_{TABLE_NAME}_immutable_{operation}"'
            )


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(INTENT_TABLE) as batch_op:
            batch_op.create_unique_constraint(
                "uq_klm_external_intent_workspace_id",
                ["workspace_id", "id"],
            )
    else:
        op.create_unique_constraint(
            "uq_klm_external_intent_workspace_id",
            INTENT_TABLE,
            ["workspace_id", "id"],
        )

    op.create_table(
        TABLE_NAME,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("intent_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempt_number", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider_event_id", sa.String(length=180), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "request_envelope",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "provider_receipt",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column(
            "external_actions_executed",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.CheckConstraint(
            "record_type IN ('prepared', 'dispatch_attempt', 'provider_receipt', 'reconciliation')",
            name="ck_klm_ads_feedback_record_type",
        ),
        sa.CheckConstraint(
            "state IN ('prepared_local', 'blocked', 'queued', 'accepted', "
            "'partial_failure', 'rejected', 'retryable', 'reconciled')",
            name="ck_klm_ads_feedback_record_state",
        ),
        sa.CheckConstraint(
            "attempt_number >= 0 AND external_actions_executed >= 0",
            name="ck_klm_ads_feedback_record_counts",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "intent_id"],
            [f"{INTENT_TABLE}.workspace_id", f"{INTENT_TABLE}.id"],
            name="fk_klm_ads_feedback_record_workspace_intent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_klm_ads_feedback_record_workspace_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "intent_id",
            "record_type",
            "attempt_number",
            name="uq_klm_ads_feedback_record_attempt",
        ),
    )
    for column in (
        "workspace_id",
        "intent_id",
        "provider",
        "record_type",
        "state",
        "provider_event_id",
        "idempotency_key",
        "payload_sha256",
        "recorded_at",
    ):
        op.create_index(f"ix_{TABLE_NAME}_{column}", TABLE_NAME, [column])
    op.create_index(
        "ix_klm_ads_feedback_workspace_provider_recorded",
        TABLE_NAME,
        ["workspace_id", "provider", "recorded_at"],
    )
    op.create_index(
        "ix_klm_ads_feedback_workspace_state_recorded",
        TABLE_NAME,
        ["workspace_id", "state", "recorded_at"],
    )
    _enable_rls()
    _enable_append_only_guard()


def downgrade() -> None:
    _disable_append_only_guard()
    _disable_rls()
    op.drop_table(TABLE_NAME)
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(INTENT_TABLE) as batch_op:
            batch_op.drop_constraint(
                "uq_klm_external_intent_workspace_id", type_="unique"
            )
    else:
        op.drop_constraint(
            "uq_klm_external_intent_workspace_id", INTENT_TABLE, type_="unique"
        )
