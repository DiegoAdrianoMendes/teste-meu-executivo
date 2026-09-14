"""add whatsapp tables

Revision ID: 2bc77616f16c
Revises: 1ab66505e05b
Create Date: 2026-03-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2bc77616f16c'
down_revision: Union[str, None] = '1ab66505e05b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums (idempotente via DO/EXCEPTION) ──
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE wa_connection_status_enum AS ENUM ('disconnected', 'pending', 'connected', 'error');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE wa_message_direction_enum AS ENUM ('inbound', 'outbound');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE wa_message_status_enum AS ENUM ('pending', 'sent', 'delivered', 'read', 'failed');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)

    # ── whatsapp_connections ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_connections (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            phone_number_id VARCHAR(50) NOT NULL,
            phone_number VARCHAR(30),
            display_name VARCHAR(200),
            business_account_id VARCHAR(50),
            status wa_connection_status_enum NOT NULL,
            webhook_registered BOOLEAN NOT NULL DEFAULT false,
            connected_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── whatsapp_contacts ──
    # matter_id é UUID (FK para matters.id que é UUID)
    # sent_by_id é UUID (FK para users.id que é UUID)
    op.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_contacts (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            phone VARCHAR(30) NOT NULL,
            name VARCHAR(200),
            avatar_url VARCHAR(500),
            matter_id UUID REFERENCES matters(id) ON DELETE SET NULL,
            last_seen TIMESTAMPTZ,
            is_blocked BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_whatsapp_contacts_phone ON whatsapp_contacts (phone)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_whatsapp_contacts_matter_id ON whatsapp_contacts (matter_id)")

    # ── whatsapp_messages ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_messages (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            contact_id UUID NOT NULL REFERENCES whatsapp_contacts(id) ON DELETE CASCADE,
            wamid VARCHAR(200),
            direction wa_message_direction_enum NOT NULL,
            status wa_message_status_enum NOT NULL,
            body TEXT NOT NULL,
            media_url VARCHAR(500),
            media_type VARCHAR(50),
            sent_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
            delivered_at TIMESTAMPTZ,
            read_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_contact_id ON whatsapp_messages (contact_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_whatsapp_messages_wamid ON whatsapp_messages (wamid) WHERE wamid IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_timestamp ON whatsapp_messages (timestamp)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wa_messages_contact_timestamp ON whatsapp_messages (contact_id, timestamp)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS whatsapp_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS whatsapp_contacts CASCADE")
    op.execute("DROP TABLE IF EXISTS whatsapp_connections CASCADE")
    op.execute("DROP TYPE IF EXISTS wa_message_status_enum")
    op.execute("DROP TYPE IF EXISTS wa_message_direction_enum")
    op.execute("DROP TYPE IF EXISTS wa_connection_status_enum")
