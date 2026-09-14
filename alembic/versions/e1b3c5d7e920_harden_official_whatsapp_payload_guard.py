"""Harden the official WhatsApp encrypted-payload contract.

Revision ID: e1b3c5d7e920
Revises: e0a2b4d6c819
Create Date: 2026-07-30

The durable outbox must never dispatch ciphertext different from the payload
that was persisted for the idempotency contract.  Ciphertext may only be
cleared while moving a prepared/started delivery into a terminal state.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e1b3c5d7e920"
down_revision: str | Sequence[str] | None = "e0a2b4d6c819"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "platform_official_whatsapp_deliveries"
FUNCTION = "klm_guard_platform_official_whatsapp_delivery"
UPDATE_TRIGGER = f"trg_{TABLE}_contract_update"
CONTRACT_COLUMNS = (
    "id",
    "connection_id",
    "role",
    "requested_by_user_id",
    "source_type",
    "source_id",
    "idempotency_key_hash",
    "payload_digest",
    "destination_last4",
    "body_length",
    "created_at",
)
NONTERMINAL_STATES = ("prepared", "provider_call_started")
TERMINAL_STATES = ("sent", "delivery_uncertain", "failed")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _postgresql_guard(*, protect_ciphertext: bool) -> None:
    immutable_old = ", ".join(f"OLD.{column_name}" for column_name in CONTRACT_COLUMNS)
    immutable_new = ", ".join(f"NEW.{column_name}" for column_name in CONTRACT_COLUMNS)
    payload_guard = ""
    if protect_ciphertext:
        payload_guard = f"""
                IF NEW.payload_ciphertext IS DISTINCT FROM OLD.payload_ciphertext
                   AND NOT (
                       OLD.payload_ciphertext IS NOT NULL
                       AND NEW.payload_ciphertext IS NULL
                       AND OLD.state IN ({_quoted(NONTERMINAL_STATES)})
                       AND NEW.state IN ({_quoted(TERMINAL_STATES)})
                   ) THEN
                    RAISE EXCEPTION '{TABLE} payload ciphertext is immutable';
                END IF;
        """
    op.execute(
        f'''CREATE OR REPLACE FUNCTION "{FUNCTION}"()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                    RAISE EXCEPTION '{TABLE} cannot be %', lower(TG_OP);
                END IF;
                IF ROW({immutable_new}) IS DISTINCT FROM ROW({immutable_old}) THEN
                    RAISE EXCEPTION '{TABLE} request contract is immutable';
                END IF;
                {payload_guard}
                RETURN NEW;
            END;
            $$'''
    )


def _sqlite_guard(*, protect_ciphertext: bool) -> None:
    immutable_change = " OR ".join(
        f"NEW.{column_name} IS NOT OLD.{column_name}" for column_name in CONTRACT_COLUMNS
    )
    invalid_payload_change = ""
    if protect_ciphertext:
        invalid_payload_change = f""" OR (
            NEW.payload_ciphertext IS NOT OLD.payload_ciphertext
            AND NOT (
                OLD.payload_ciphertext IS NOT NULL
                AND NEW.payload_ciphertext IS NULL
                AND OLD.state IN ({_quoted(NONTERMINAL_STATES)})
                AND NEW.state IN ({_quoted(TERMINAL_STATES)})
            )
        )"""
    op.execute(f'DROP TRIGGER IF EXISTS "{UPDATE_TRIGGER}"')
    op.execute(
        f'''CREATE TRIGGER "{UPDATE_TRIGGER}"
            BEFORE UPDATE ON "{TABLE}"
            WHEN ({immutable_change}){invalid_payload_change}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    '{TABLE} request payload or contract is immutable'
                );
            END'''
    )


def _replace_guard(*, protect_ciphertext: bool) -> None:
    if op.get_bind().dialect.name == "sqlite":
        _sqlite_guard(protect_ciphertext=protect_ciphertext)
        return
    _postgresql_guard(protect_ciphertext=protect_ciphertext)


def upgrade() -> None:
    _replace_guard(protect_ciphertext=True)


def downgrade() -> None:
    _replace_guard(protect_ciphertext=False)
