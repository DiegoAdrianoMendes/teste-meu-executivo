"""harden WhatsApp webhook dedupe and durable processing state

Revision ID: c5f7a9b1d304
Revises: b4e6f8a0c253
Create Date: 2026-07-31 04:20:00.000000

The immutable envelope remains the custody record. New captures receive a
canonical fingerprint of the exact signed bytes, while historical envelopes
keep a NULL fingerprint and are matched by their immutable hashes plus an
exact byte comparison in the service. This avoids mutating or deleting
append-only evidence during rollout.

Historical envelopes were created by the former synchronous/async ingress
before this projection existed. They are backfilled as processed so a schema
upgrade cannot replay old customer events. Every post-upgrade capture creates
its queued state atomically with the envelope.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "c5f7a9b1d304"
down_revision: str | Sequence[str] | None = "b4e6f8a0c253"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENVELOPE_TABLE = "klm_omnichannel_provider_webhook_envelopes"
STATE_TABLE = "klm_omnichannel_provider_webhook_processing_states"
FINGERPRINT_COLUMN = "delivery_fingerprint_sha256"
FINGERPRINT_CHECK = "ck_klm_provider_envelope_delivery_fingerprint"
FINGERPRINT_INDEX = "uq_klm_provider_envelope_delivery_fingerprint"
CAMPAIGN_EVENT_TABLE = "whatsapp_campaign_delivery_events"
CAMPAIGN_EVENT_KEY_COLUMN = "provider_event_key"
CAMPAIGN_EVENT_KEY_CHECK = "ck_wa_campaign_delivery_event_provider_key"
CAMPAIGN_EVENT_KEY_INDEX = "uq_wa_campaign_delivery_events_provider_event_key"


def _add_fingerprint_column() -> None:
    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot add a named table constraint without rebuilding the
        # append-only table. Keep the guard inline on the new column instead.
        op.execute(
            f"""
            ALTER TABLE {ENVELOPE_TABLE}
            ADD COLUMN {FINGERPRINT_COLUMN} VARCHAR(64)
            CHECK (
                {FINGERPRINT_COLUMN} IS NULL
                OR length({FINGERPRINT_COLUMN}) = 64
            )
            """
        )
        return
    op.add_column(
        ENVELOPE_TABLE,
        sa.Column(FINGERPRINT_COLUMN, sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        FINGERPRINT_CHECK,
        ENVELOPE_TABLE,
        f"{FINGERPRINT_COLUMN} IS NULL OR length({FINGERPRINT_COLUMN}) = 64",
    )


def _add_campaign_event_key_column() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            f"""
            ALTER TABLE {CAMPAIGN_EVENT_TABLE}
            ADD COLUMN {CAMPAIGN_EVENT_KEY_COLUMN} VARCHAR(64)
            CHECK (
                {CAMPAIGN_EVENT_KEY_COLUMN} IS NULL
                OR length({CAMPAIGN_EVENT_KEY_COLUMN}) = 64
            )
            """
        )
        return
    op.add_column(
        CAMPAIGN_EVENT_TABLE,
        sa.Column(CAMPAIGN_EVENT_KEY_COLUMN, sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        CAMPAIGN_EVENT_KEY_CHECK,
        CAMPAIGN_EVENT_TABLE,
        f"{CAMPAIGN_EVENT_KEY_COLUMN} IS NULL OR length({CAMPAIGN_EVENT_KEY_COLUMN}) = 64",
    )


def upgrade() -> None:
    _add_fingerprint_column()
    fingerprint_present = sa.text(f"{FINGERPRINT_COLUMN} IS NOT NULL")
    op.create_index(
        FINGERPRINT_INDEX,
        ENVELOPE_TABLE,
        [FINGERPRINT_COLUMN],
        unique=True,
        postgresql_where=fingerprint_present,
        sqlite_where=fingerprint_present,
    )
    _add_campaign_event_key_column()
    campaign_event_key_present = sa.text(f"{CAMPAIGN_EVENT_KEY_COLUMN} IS NOT NULL")
    op.create_index(
        CAMPAIGN_EVENT_KEY_INDEX,
        CAMPAIGN_EVENT_TABLE,
        [CAMPAIGN_EVENT_KEY_COLUMN],
        unique=True,
        postgresql_where=campaign_event_key_present,
        sqlite_where=campaign_event_key_present,
    )

    op.create_table(
        STATE_TABLE,
        sa.Column(
            "envelope_id",
            FlexibleUUID(),
            sa.ForeignKey(f"{ENVELOPE_TABLE}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
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
        sa.CheckConstraint(
            "status IN "
            "('queued', 'processing', 'processed', 'retryable_failed', 'terminal_failed')",
            name="ck_klm_provider_webhook_processing_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_klm_provider_webhook_processing_attempts",
        ),
        sa.CheckConstraint(
            "(status = 'processing' "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'processing' "
            "AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_klm_provider_webhook_processing_lease_shape",
        ),
        sa.CheckConstraint(
            "(status = 'processed' AND processed_at IS NOT NULL) "
            "OR (status <> 'processed' AND processed_at IS NULL)",
            name="ck_klm_provider_webhook_processing_processed_at",
        ),
        sa.CheckConstraint(
            "(status = 'retryable_failed' "
            "AND next_retry_at IS NOT NULL AND last_failed_at IS NOT NULL) "
            "OR (status <> 'retryable_failed' AND next_retry_at IS NULL)",
            name="ck_klm_provider_webhook_processing_retry_shape",
        ),
        sa.CheckConstraint(
            "(status = 'terminal_failed' AND terminal_failed_at IS NOT NULL) "
            "OR (status <> 'terminal_failed' AND terminal_failed_at IS NULL)",
            name="ck_klm_provider_webhook_processing_terminal_at",
        ),
        sa.PrimaryKeyConstraint("envelope_id"),
    )
    op.create_index(
        "ix_klm_provider_webhook_processing_claim",
        STATE_TABLE,
        ["status", "next_retry_at", "lease_expires_at"],
    )
    terminal = sa.text("status = 'terminal_failed'")
    op.create_index(
        "ix_klm_provider_webhook_processing_terminal",
        STATE_TABLE,
        ["terminal_failed_at"],
        postgresql_where=terminal,
        sqlite_where=terminal,
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO {STATE_TABLE} (
                envelope_id,
                status,
                attempts,
                queued_at,
                processing_started_at,
                last_attempted_at,
                processed_at,
                created_at,
                updated_at
            )
            SELECT
                id,
                'processed',
                1,
                received_at,
                received_at,
                received_at,
                received_at,
                received_at,
                received_at
            FROM {ENVELOPE_TABLE}
            """
        )
    )
    orphan_count = int(
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT COUNT(*)
                FROM {ENVELOPE_TABLE} AS envelope
                LEFT JOIN {STATE_TABLE} AS state
                  ON state.envelope_id = envelope.id
                WHERE state.envelope_id IS NULL
                """
            )
        )
        .scalar_one()
    )
    if orphan_count:
        raise RuntimeError(
            "Webhook processing-state backfill did not cover every immutable envelope."
        )


def downgrade() -> None:
    # A downgrade is safe only before this revision has accepted new traffic.
    # Otherwise dropping the fingerprint and processing projection would erase
    # durable dedupe/retry evidence and could replay customer effects after a
    # subsequent re-upgrade. Operators must drain/export the post-upgrade
    # records explicitly instead of receiving a falsely "successful" rollback.
    post_upgrade_fingerprints = int(
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT COUNT(*)
                FROM {ENVELOPE_TABLE}
                WHERE {FINGERPRINT_COLUMN} IS NOT NULL
                """
            )
        )
        .scalar_one()
    )
    non_historical_states = int(
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT COUNT(*)
                FROM {STATE_TABLE}
                WHERE status <> 'processed'
                   OR attempts <> 1
                   OR processed_at IS NULL
                   OR lease_token IS NOT NULL
                   OR lease_expires_at IS NOT NULL
                   OR next_retry_at IS NOT NULL
                   OR terminal_failed_at IS NOT NULL
                """
            )
        )
        .scalar_one()
    )
    provider_campaign_receipts = int(
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT COUNT(*)
                FROM {CAMPAIGN_EVENT_TABLE}
                WHERE {CAMPAIGN_EVENT_KEY_COLUMN} IS NOT NULL
                """
            )
        )
        .scalar_one()
    )
    if post_upgrade_fingerprints or non_historical_states or provider_campaign_receipts:
        raise RuntimeError(
            "Refusing to drop durable WhatsApp webhook processing evidence; "
            "post-upgrade deliveries or mutable states exist."
        )

    op.drop_index(
        "ix_klm_provider_webhook_processing_terminal",
        table_name=STATE_TABLE,
    )
    op.drop_index(
        "ix_klm_provider_webhook_processing_claim",
        table_name=STATE_TABLE,
    )
    op.drop_table(STATE_TABLE)
    op.drop_index(CAMPAIGN_EVENT_KEY_INDEX, table_name=CAMPAIGN_EVENT_TABLE)
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            CAMPAIGN_EVENT_KEY_CHECK,
            CAMPAIGN_EVENT_TABLE,
            type_="check",
        )
    op.drop_column(CAMPAIGN_EVENT_TABLE, CAMPAIGN_EVENT_KEY_COLUMN)
    op.drop_index(FINGERPRINT_INDEX, table_name=ENVELOPE_TABLE)
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            FINGERPRINT_CHECK,
            ENVELOPE_TABLE,
            type_="check",
        )
    op.drop_column(ENVELOPE_TABLE, FINGERPRINT_COLUMN)
