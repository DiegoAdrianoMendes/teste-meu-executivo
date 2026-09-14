"""Add durable provider lead hydration protocol.

Revision ID: 1b3d5f7a9c21
Revises: 0a8b0d2e4f97
Create Date: 2026-07-21 05:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleJSON, FlexibleUUID

revision: str = "1b3d5f7a9c21"
down_revision: str | Sequence[str] | None = "0a8b0d2e4f97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TABLE = "klm_lead_hydration_jobs"
EVENT_TABLE = "klm_lead_hydration_events"
CAPTURE_TABLE = "klm_lead_capture_receipts"
WORKSPACE_SETTING = "app.current_workspace_id"
RLS_POLICY = "workspace_current_id"


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    predicate = f"workspace_id::text = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')"
    for table_name in (JOB_TABLE, EVENT_TABLE):
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
    for table_name in (EVENT_TABLE, JOB_TABLE):
        op.execute(f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{table_name}"')
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
            f'''CREATE TRIGGER "trg_{EVENT_TABLE}_immutable"
            BEFORE UPDATE OR DELETE ON "{EVENT_TABLE}"
            FOR EACH ROW EXECUTE FUNCTION klm_reject_append_only_mutation()'''
        )
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(
                f'''
                CREATE TRIGGER IF NOT EXISTS "trg_{EVENT_TABLE}_immutable_{operation}"
                BEFORE {operation.upper()} ON "{EVENT_TABLE}"
                BEGIN
                    SELECT RAISE(ABORT, '{EVENT_TABLE} is immutable');
                END
                '''
            )


def _disable_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(f'DROP TRIGGER IF EXISTS "trg_{EVENT_TABLE}_immutable" ON "{EVENT_TABLE}"')
    elif dialect == "sqlite":
        for operation in ("update", "delete"):
            op.execute(f'DROP TRIGGER IF EXISTS "trg_{EVENT_TABLE}_immutable_{operation}"')


def upgrade() -> None:
    with op.batch_alter_table(CAPTURE_TABLE) as batch:
        batch.create_unique_constraint(
            "uq_klm_lead_capture_workspace_id", ["workspace_id", "id"]
        )

    op.create_table(
        JOB_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("public_ingress_receipt_id", FlexibleUUID(), nullable=False),
        sa.Column("lead_capture_receipt_id", FlexibleUUID(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("connection_key", sa.String(length=120), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("external_lead_id", sa.String(length=255), nullable=False),
        sa.Column("external_form_id", sa.String(length=255), nullable=True),
        sa.Column("locator_payload", FlexibleJSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="7", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=180), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fetch_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hydrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "provider_receipt",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_klm_lead_hydration_attempts"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_klm_lead_hydration_max_attempts"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_lead_hydration_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "public_ingress_receipt_id"],
            [
                "klm_omnichannel_public_ingress_receipts.workspace_id",
                "klm_omnichannel_public_ingress_receipts.id",
            ],
            name="fk_klm_lead_hydration_workspace_ingress",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "lead_capture_receipt_id"],
            ["klm_lead_capture_receipts.workspace_id", "klm_lead_capture_receipts.id"],
            name="fk_klm_lead_hydration_workspace_capture",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "connection_key",
            "external_lead_id",
            name="uq_klm_lead_hydration_external",
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_klm_lead_hydration_workspace_id"
        ),
    )
    for column in (
        "workspace_id",
        "connection_id",
        "public_ingress_receipt_id",
        "lead_capture_receipt_id",
        "provider",
        "external_event_id",
        "external_lead_id",
        "external_form_id",
        "state",
    ):
        op.create_index(f"ix_{JOB_TABLE}_{column}", JOB_TABLE, [column])
    op.create_index(
        "ix_klm_lead_hydration_workspace_due",
        JOB_TABLE,
        ["workspace_id", "state", "next_attempt_at"],
    )

    op.create_table(
        EVENT_TABLE,
        sa.Column("workspace_id", FlexibleUUID(), nullable=False),
        sa.Column("hydration_job_id", FlexibleUUID(), nullable=False),
        sa.Column("connection_id", FlexibleUUID(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_lead_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=False),
        sa.Column("next_state", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("lease_owner", sa.String(length=180), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "event_metadata",
            FlexibleJSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "hydration_job_id"],
            ["klm_lead_hydration_jobs.workspace_id", "klm_lead_hydration_jobs.id"],
            name="fk_klm_lead_hydration_event_workspace_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "klm_omnichannel_channel_connections.workspace_id",
                "klm_omnichannel_channel_connections.id",
            ],
            name="fk_klm_lead_hydration_event_workspace_connection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_klm_lead_hydration_event_idempotency",
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_klm_lead_hydration_event_workspace_id"
        ),
    )
    for column in (
        "workspace_id",
        "hydration_job_id",
        "connection_id",
        "provider",
        "external_lead_id",
        "event_type",
        "occurred_at",
    ):
        op.create_index(f"ix_{EVENT_TABLE}_{column}", EVENT_TABLE, [column])
    op.create_index(
        "ix_klm_lead_hydration_event_job_time",
        EVENT_TABLE,
        ["workspace_id", "hydration_job_id", "occurred_at"],
    )
    _enable_rls()
    _enable_append_only_guard()


def downgrade() -> None:
    _disable_append_only_guard()
    _disable_rls()
    op.drop_index("ix_klm_lead_hydration_event_job_time", table_name=EVENT_TABLE)
    for column in reversed(
        (
            "workspace_id",
            "hydration_job_id",
            "connection_id",
            "provider",
            "external_lead_id",
            "event_type",
            "occurred_at",
        )
    ):
        op.drop_index(f"ix_{EVENT_TABLE}_{column}", table_name=EVENT_TABLE)
    op.drop_table(EVENT_TABLE)
    op.drop_index("ix_klm_lead_hydration_workspace_due", table_name=JOB_TABLE)
    for column in reversed(
        (
            "workspace_id",
            "connection_id",
            "public_ingress_receipt_id",
            "lead_capture_receipt_id",
            "provider",
            "external_event_id",
            "external_lead_id",
            "external_form_id",
            "state",
        )
    ):
        op.drop_index(f"ix_{JOB_TABLE}_{column}", table_name=JOB_TABLE)
    op.drop_table(JOB_TABLE)
    with op.batch_alter_table(CAPTURE_TABLE) as batch:
        batch.drop_constraint("uq_klm_lead_capture_workspace_id", type_="unique")
