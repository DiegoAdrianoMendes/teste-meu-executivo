"""add finance controls

Revision ID: e19f2b7a4c21
Revises: c6e1a9d4f102
Create Date: 2026-04-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e19f2b7a4c21"
down_revision: Union[str, None] = "c6e1a9d4f102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE workspace_role_enum ADD VALUE IF NOT EXISTS 'finance_manager'")

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE finance_entry_type_enum AS ENUM ('receivable', 'expense', 'refund');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE finance_entry_status_enum AS ENUM ('pending', 'scheduled', 'paid', 'overdue', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE finance_reminder_status_enum AS ENUM ('pending', 'sent', 'dismissed');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE integration_provider_enum AS ENUM ('asaas', 'conta_azul', 'custom');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE integration_status_enum AS ENUM ('disconnected', 'pending', 'connected', 'error');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_entries (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            matter_id UUID REFERENCES matters(id) ON DELETE SET NULL,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            entry_type finance_entry_type_enum NOT NULL DEFAULT 'receivable',
            status finance_entry_status_enum NOT NULL DEFAULT 'pending',
            provider VARCHAR(50),
            external_ref VARCHAR(120),
            amount NUMERIC(15, 2) NOT NULL DEFAULT 0,
            currency VARCHAR(10) NOT NULL DEFAULT 'BRL',
            due_date TIMESTAMPTZ,
            paid_at TIMESTAMPTZ,
            reminder_days_before INTEGER NOT NULL DEFAULT 3,
            notify_client BOOLEAN NOT NULL DEFAULT false,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            entry_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_finance_entries_workspace_external_ref UNIQUE (workspace_id, external_ref)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_entries_workspace_id ON finance_entries (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_entries_matter_id ON finance_entries (matter_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_entries_status ON finance_entries (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_entries_due_date ON finance_entries (due_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_entries_external_ref ON finance_entries (external_ref)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_reminders (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            finance_entry_id UUID NOT NULL REFERENCES finance_entries(id) ON DELETE CASCADE,
            status finance_reminder_status_enum NOT NULL DEFAULT 'pending',
            remind_at TIMESTAMPTZ NOT NULL,
            channel VARCHAR(30) NOT NULL DEFAULT 'email',
            message TEXT,
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_reminders_workspace_id ON finance_reminders (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_reminders_finance_entry_id ON finance_reminders (finance_entry_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_reminders_status ON finance_reminders (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_reminders_remind_at ON finance_reminders (remind_at)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS integration_connections (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            provider integration_provider_enum NOT NULL,
            status integration_status_enum NOT NULL DEFAULT 'disconnected',
            label VARCHAR(120) NOT NULL,
            base_url VARCHAR(255),
            external_account_id VARCHAR(120),
            webhook_url VARCHAR(255),
            api_version VARCHAR(40),
            capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_sync_at TIMESTAMPTZ,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_integration_workspace_provider_label UNIQUE (workspace_id, provider, label)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_integration_connections_workspace_id ON integration_connections (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_integration_connections_provider ON integration_connections (provider)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS integration_connections")
    op.execute("DROP TABLE IF EXISTS finance_reminders")
    op.execute("DROP TABLE IF EXISTS finance_entries")
    op.execute("DROP TYPE IF EXISTS integration_status_enum")
    op.execute("DROP TYPE IF EXISTS integration_provider_enum")
    op.execute("DROP TYPE IF EXISTS finance_reminder_status_enum")
    op.execute("DROP TYPE IF EXISTS finance_entry_status_enum")
    op.execute("DROP TYPE IF EXISTS finance_entry_type_enum")
