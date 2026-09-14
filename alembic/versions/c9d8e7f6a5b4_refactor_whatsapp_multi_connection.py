"""refactor whatsapp into multi-connection tenant-aware architecture

Revision ID: c9d8e7f6a5b4
Revises: a8c9d0e1f2a3
Create Date: 2026-04-05 21:10:00.000000
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa

from app.db.base import FlexibleUUID


revision = "c9d8e7f6a5b4"
down_revision = "a8c9d0e1f2a3"
branch_labels = None
depends_on = None


connection_type_enum = sa.Enum(
    "QR_CODE",
    "OFFICIAL_MANUAL",
    "TECH_PROVIDER",
    name="wa_connection_type_enum",
)


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def _build_cipher():
    from cryptography.fernet import Fernet
    from app.core.config import settings

    material = settings.WHATSAPP_SECRET_ENCRYPTION_KEY or settings.SECRET_KEY
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str | None) -> str | None:
    if not value:
        return None
    cipher = _build_cipher()
    return cipher.encrypt(value.encode("utf-8")).decode("utf-8")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            DO $$ BEGIN
                CREATE TYPE wa_connection_type_enum AS ENUM ('QR_CODE', 'OFFICIAL_MANUAL', 'TECH_PROVIDER');
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
            """
        )

    op.add_column(
        "whatsapp_connections",
        sa.Column("connection_type", connection_type_enum, nullable=True, server_default="OFFICIAL_MANUAL"),
    )
    op.add_column("whatsapp_connections", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("whatsapp_connections", sa.Column("display_phone_number", sa.String(length=40), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("waba_id", sa.String(length=50), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("access_token_encrypted", sa.Text(), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("business_token_encrypted", sa.Text(), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("instance_id", sa.String(length=120), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("instance_token_encrypted", sa.Text(), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("registration_pin_encrypted", sa.Text(), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("qr_provider", sa.String(length=50), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("qr_status", sa.String(length=50), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("qr_last_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("last_error_code", sa.String(length=50), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("last_error_message", sa.Text(), nullable=True))
    op.add_column("whatsapp_connections", sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("whatsapp_contacts", sa.Column("whatsapp_connection_id", FlexibleUUID(), nullable=True))
    op.add_column("whatsapp_messages", sa.Column("whatsapp_connection_id", FlexibleUUID(), nullable=True))

    op.create_index("ix_whatsapp_connections_instance_id", "whatsapp_connections", ["instance_id"], unique=True)
    op.create_index("ix_whatsapp_contacts_whatsapp_connection_id", "whatsapp_contacts", ["whatsapp_connection_id"], unique=False)
    op.create_index("ix_whatsapp_messages_whatsapp_connection_id", "whatsapp_messages", ["whatsapp_connection_id"], unique=False)

    workspaces = sa.table(
        "workspaces",
        sa.column("id", FlexibleUUID()),
        sa.column("settings", sa.JSON()),
    )
    connections = sa.table(
        "whatsapp_connections",
        sa.column("id", FlexibleUUID()),
        sa.column("workspace_id", FlexibleUUID()),
        sa.column("phone_number_id", sa.String()),
        sa.column("phone_number", sa.String()),
        sa.column("display_phone_number", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("business_account_id", sa.String()),
        sa.column("waba_id", sa.String()),
        sa.column("connection_type", connection_type_enum),
        sa.column("is_default", sa.Boolean()),
        sa.column("status", sa.String()),
        sa.column("access_token_encrypted", sa.Text()),
    )
    contacts = sa.table(
        "whatsapp_contacts",
        sa.column("id", FlexibleUUID()),
        sa.column("workspace_id", FlexibleUUID()),
        sa.column("whatsapp_connection_id", FlexibleUUID()),
    )
    messages = sa.table(
        "whatsapp_messages",
        sa.column("id", FlexibleUUID()),
        sa.column("contact_id", FlexibleUUID()),
        sa.column("whatsapp_connection_id", FlexibleUUID()),
    )

    workspace_rows = bind.execute(sa.select(workspaces.c.id, workspaces.c.settings)).mappings().all()
    for workspace_row in workspace_rows:
        workspace_id = workspace_row["id"]
        settings = workspace_row["settings"] or {}
        legacy_whatsapp = dict((settings or {}).get("whatsapp") or {})

        existing_connections = bind.execute(
            sa.select(
                connections.c.id,
                connections.c.phone_number_id,
                connections.c.phone_number,
                connections.c.business_account_id,
            ).where(connections.c.workspace_id == workspace_id)
        ).mappings().all()

        if existing_connections:
            first = existing_connections[0]
            bind.execute(
                sa.update(connections)
                .where(connections.c.id == first["id"])
                .values(
                    connection_type="OFFICIAL_MANUAL",
                    is_default=True,
                    display_phone_number=first["phone_number"],
                    phone_number=_normalize_phone(first["phone_number"]),
                    waba_id=first["business_account_id"],
                    access_token_encrypted=_encrypt(legacy_whatsapp.get("accessToken")),
                )
            )
            for extra in existing_connections[1:]:
                bind.execute(
                    sa.update(connections)
                    .where(connections.c.id == extra["id"])
                    .values(
                        connection_type="OFFICIAL_MANUAL",
                        is_default=False,
                        display_phone_number=extra["phone_number"],
                        phone_number=_normalize_phone(extra["phone_number"]),
                        waba_id=extra["business_account_id"],
                    )
                )
            default_connection_id = first["id"]
        elif legacy_whatsapp.get("phoneNumberId") or legacy_whatsapp.get("accessToken"):
            default_connection_id = uuid.uuid4()
            bind.execute(
                sa.insert(connections).values(
                    id=default_connection_id,
                    workspace_id=workspace_id,
                    connection_type="OFFICIAL_MANUAL",
                    is_default=True,
                    phone_number_id=legacy_whatsapp.get("phoneNumberId"),
                    phone_number=_normalize_phone(legacy_whatsapp.get("phoneNumber")),
                    display_phone_number=legacy_whatsapp.get("phoneNumber"),
                    display_name=legacy_whatsapp.get("displayName"),
                    business_account_id=legacy_whatsapp.get("businessAccountId"),
                    waba_id=legacy_whatsapp.get("businessAccountId"),
                    status="connected" if legacy_whatsapp.get("phoneNumberId") and legacy_whatsapp.get("accessToken") else "pending",
                    webhook_registered=False,
                    access_token_encrypted=_encrypt(legacy_whatsapp.get("accessToken")),
                )
            )
        else:
            default_connection_id = None

        if default_connection_id:
            bind.execute(
                sa.update(contacts)
                .where(
                    contacts.c.workspace_id == workspace_id,
                    contacts.c.whatsapp_connection_id.is_(None),
                )
                .values(whatsapp_connection_id=default_connection_id)
            )

        if legacy_whatsapp:
            updated_settings = dict(settings or {})
            updated_settings.pop("whatsapp", None)
            bind.execute(
                sa.update(workspaces)
                .where(workspaces.c.id == workspace_id)
                .values(settings=updated_settings)
            )

    bind.execute(
        sa.text(
            """
            UPDATE whatsapp_messages AS wm
            SET whatsapp_connection_id = wc.whatsapp_connection_id
            FROM whatsapp_contacts AS wc
            WHERE wm.contact_id = wc.id
              AND wm.whatsapp_connection_id IS NULL
              AND wc.whatsapp_connection_id IS NOT NULL
            """
        )
    )

    op.drop_index("uq_whatsapp_connections_workspace", table_name="whatsapp_connections")
    op.drop_index("uq_whatsapp_contacts_workspace_phone", table_name="whatsapp_contacts")
    op.create_index(
        "uq_whatsapp_contacts_workspace_phone_connection",
        "whatsapp_contacts",
        ["workspace_id", "phone", "whatsapp_connection_id"],
        unique=True,
    )

    op.create_foreign_key(
        "fk_whatsapp_contacts_connection",
        "whatsapp_contacts",
        "whatsapp_connections",
        ["whatsapp_connection_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_whatsapp_messages_connection",
        "whatsapp_messages",
        "whatsapp_connections",
        ["whatsapp_connection_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.alter_column("whatsapp_connections", "connection_type", nullable=False, server_default=None)
    op.alter_column("whatsapp_contacts", "whatsapp_connection_id", nullable=False)
    op.alter_column("whatsapp_messages", "whatsapp_connection_id", nullable=False)


def downgrade() -> None:
    op.drop_constraint("fk_whatsapp_messages_connection", "whatsapp_messages", type_="foreignkey")
    op.drop_constraint("fk_whatsapp_contacts_connection", "whatsapp_contacts", type_="foreignkey")
    op.drop_index("uq_whatsapp_contacts_workspace_phone_connection", table_name="whatsapp_contacts")
    op.create_index("uq_whatsapp_contacts_workspace_phone", "whatsapp_contacts", ["workspace_id", "phone"], unique=True)
    op.create_index("uq_whatsapp_connections_workspace", "whatsapp_connections", ["workspace_id"], unique=True)
    op.drop_index("ix_whatsapp_messages_whatsapp_connection_id", table_name="whatsapp_messages")
    op.drop_index("ix_whatsapp_contacts_whatsapp_connection_id", table_name="whatsapp_contacts")
    op.drop_index("ix_whatsapp_connections_instance_id", table_name="whatsapp_connections")
    op.drop_column("whatsapp_messages", "whatsapp_connection_id")
    op.drop_column("whatsapp_contacts", "whatsapp_connection_id")
    op.drop_column("whatsapp_connections", "disconnected_at")
    op.drop_column("whatsapp_connections", "last_error_message")
    op.drop_column("whatsapp_connections", "last_error_code")
    op.drop_column("whatsapp_connections", "qr_last_sync_at")
    op.drop_column("whatsapp_connections", "qr_status")
    op.drop_column("whatsapp_connections", "qr_provider")
    op.drop_column("whatsapp_connections", "registration_pin_encrypted")
    op.drop_column("whatsapp_connections", "instance_token_encrypted")
    op.drop_column("whatsapp_connections", "instance_id")
    op.drop_column("whatsapp_connections", "business_token_encrypted")
    op.drop_column("whatsapp_connections", "access_token_encrypted")
    op.drop_column("whatsapp_connections", "waba_id")
    op.drop_column("whatsapp_connections", "display_phone_number")
    op.drop_column("whatsapp_connections", "is_default")
    op.drop_column("whatsapp_connections", "connection_type")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS wa_connection_type_enum")
