"""Add the global durable delivery protocol for official WhatsApp numbers.

Revision ID: d9f1a3c5b617
Revises: c8e0f2a4b615
Create Date: 2026-07-30

The delivery is a platform control-plane operation and intentionally has no
workspace sentinel or tenant RLS.  Only encrypted payload material and keyed
digests are persisted.  A database trigger freezes the request contract while
allowing the worker to advance delivery state and attach the provider receipt.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d9f1a3c5b617"
down_revision: str | Sequence[str] | None = "c8e0f2a4b615"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "platform_official_whatsapp_deliveries"
ROLE_ENUM = "platform_official_whatsapp_role_enum"
STATE_ENUM = "platform_official_whatsapp_delivery_state_enum"
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


def _uuid_type() -> sa.TypeEngine:
    return sa.Uuid().with_variant(sa.CHAR(length=36), "sqlite")


def _role_type() -> sa.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM(
            "notifications",
            "outbound",
            "support",
            name=ROLE_ENUM,
            create_type=False,
        )
    return sa.Enum(
        "notifications",
        "outbound",
        "support",
        name=ROLE_ENUM,
        native_enum=False,
        create_constraint=False,
    )


def _state_type() -> sa.Enum:
    return sa.Enum(
        "prepared",
        "provider_call_started",
        "sent",
        "delivery_uncertain",
        "failed",
        name=STATE_ENUM,
        native_enum=False,
        create_constraint=True,
        length=32,
    )


def _install_contract_guards() -> None:
    if op.get_bind().dialect.name == "sqlite":
        immutable_change = " OR ".join(
            f"NEW.{column_name} IS NOT OLD.{column_name}"
            for column_name in CONTRACT_COLUMNS
        )
        op.execute(
            f'''CREATE TRIGGER "trg_{TABLE}_contract_update"
                BEFORE UPDATE ON "{TABLE}"
                WHEN {immutable_change}
                BEGIN
                    SELECT RAISE(ABORT, '{TABLE} request contract is immutable');
                END'''
        )
        op.execute(
            f'''CREATE TRIGGER "trg_{TABLE}_delete"
                BEFORE DELETE ON "{TABLE}"
                BEGIN
                    SELECT RAISE(ABORT, '{TABLE} cannot be deleted');
                END'''
        )
        return

    function_name = "klm_guard_platform_official_whatsapp_delivery"
    immutable_old = ", ".join(f"OLD.{column_name}" for column_name in CONTRACT_COLUMNS)
    immutable_new = ", ".join(f"NEW.{column_name}" for column_name in CONTRACT_COLUMNS)
    op.execute(
        f'''CREATE OR REPLACE FUNCTION "{function_name}"()
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
                RETURN NEW;
            END;
            $$'''
    )
    op.execute(
        f'''CREATE TRIGGER "trg_{TABLE}_contract"
            BEFORE UPDATE OR DELETE ON "{TABLE}"
            FOR EACH ROW EXECUTE FUNCTION "{function_name}"()'''
    )
    op.execute(
        f'''CREATE TRIGGER "trg_{TABLE}_truncate"
            BEFORE TRUNCATE ON "{TABLE}"
            FOR EACH STATEMENT EXECUTE FUNCTION "{function_name}"()'''
    )


def _revoke_destructive_runtime_privileges() -> None:
    """Strip DELETE/TRUNCATE from every explicit non-owner table grantee."""

    if op.get_bind().dialect.name != "postgresql":
        return
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT pg_get_userbyid(relation.relowner) AS owner_name,
                   COALESCE(grantee.rolname, 'PUBLIC') AS grantee_name,
                   upper(privilege.privilege_type) AS privilege_type
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL aclexplode(
                COALESCE(relation.relacl, acldefault('r', relation.relowner))
            ) privilege
            LEFT JOIN pg_roles grantee ON grantee.oid = privilege.grantee
            WHERE namespace.nspname = current_schema()
              AND relation.relname = :table_name
              AND upper(privilege.privilege_type) IN ('DELETE', 'TRUNCATE')
            ORDER BY grantee_name, privilege_type
            """
        ),
        {"table_name": TABLE},
    ).mappings()
    preparer = bind.dialect.identifier_preparer
    for row in rows:
        if row["grantee_name"] == row["owner_name"]:
            continue
        grantee = (
            "PUBLIC"
            if row["grantee_name"] == "PUBLIC"
            else preparer.quote(str(row["grantee_name"]))
        )
        op.execute(
            f"REVOKE {row['privilege_type']} ON TABLE "
            f"{preparer.quote(TABLE)} FROM {grantee}"
        )


def _drop_contract_guards() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f'DROP TRIGGER IF EXISTS "trg_{TABLE}_delete"')
        op.execute(f'DROP TRIGGER IF EXISTS "trg_{TABLE}_contract_update"')
        return
    function_name = "klm_guard_platform_official_whatsapp_delivery"
    op.execute(f'DROP TRIGGER IF EXISTS "trg_{TABLE}_truncate" ON "{TABLE}"')
    op.execute(f'DROP TRIGGER IF EXISTS "trg_{TABLE}_contract" ON "{TABLE}"')
    op.execute(f'DROP FUNCTION IF EXISTS "{function_name}"()')


def upgrade() -> None:
    uuid_type = _uuid_type()
    op.create_table(
        TABLE,
        sa.Column("connection_id", uuid_type, nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("requested_by_user_id", uuid_type, nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_ciphertext", sa.Text(), nullable=True),
        sa.Column("destination_last4", sa.String(length=4), nullable=True),
        sa.Column("body_length", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            _state_type(),
            server_default=sa.text("'prepared'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column(
            "provider_call_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delivery_uncertain_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_type", sa.String(length=120), nullable=True),
        sa.Column("id", uuid_type, nullable=False),
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
            "attempt_count >= 0",
            name="ck_platform_official_wa_delivery_attempt_count",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(payload_digest) = 64",
            name="ck_platform_official_wa_delivery_hashes",
        ),
        sa.CheckConstraint(
            "state != 'prepared' OR payload_ciphertext IS NOT NULL",
            name="ck_platform_official_wa_delivery_prepared_payload",
        ),
        sa.CheckConstraint(
            "state != 'sent' OR external_message_id IS NOT NULL",
            name="ck_platform_official_wa_delivery_sent_receipt",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["platform_official_whatsapp_connections.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_platform_official_wa_delivery_idempotency",
        ),
        sa.UniqueConstraint(
            "external_message_id",
            name="uq_platform_official_wa_delivery_external_message",
        ),
    )
    op.create_index(
        "ix_platform_official_whatsapp_deliveries_connection_id",
        TABLE,
        ["connection_id"],
    )
    op.create_index(
        "ix_platform_official_whatsapp_deliveries_requested_by_user_id",
        TABLE,
        ["requested_by_user_id"],
    )
    op.create_index(
        "ix_platform_official_wa_delivery_state_created",
        TABLE,
        ["state", "created_at"],
    )
    op.create_index(
        "ix_platform_official_wa_delivery_role_state_created",
        TABLE,
        ["role", "state", "created_at"],
    )
    op.create_index(
        "ix_platform_official_wa_delivery_source",
        TABLE,
        ["source_type", "source_id"],
    )
    _install_contract_guards()
    _revoke_destructive_runtime_privileges()


def downgrade() -> None:
    _drop_contract_guards()
    op.drop_index("ix_platform_official_wa_delivery_source", table_name=TABLE)
    op.drop_index("ix_platform_official_wa_delivery_role_state_created", table_name=TABLE)
    op.drop_index("ix_platform_official_wa_delivery_state_created", table_name=TABLE)
    op.drop_index(
        "ix_platform_official_whatsapp_deliveries_requested_by_user_id",
        table_name=TABLE,
    )
    op.drop_index(
        "ix_platform_official_whatsapp_deliveries_connection_id",
        table_name=TABLE,
    )
    op.drop_table(TABLE)
