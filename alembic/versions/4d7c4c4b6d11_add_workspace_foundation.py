"""add workspace foundation

Revision ID: 4d7c4c4b6d11
Revises: 3cd88727g27d
Create Date: 2026-04-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "4d7c4c4b6d11"
down_revision: Union[str, None] = "3cd88727g27d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE workspace_status_enum AS ENUM ('provisioning', 'active', 'suspended');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE workspace_setup_mode_enum AS ENUM ('self_service', 'assisted');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE workspace_role_enum AS ENUM ('workspace_owner', 'workspace_admin', 'legal_manager', 'operator', 'viewer');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE membership_status_enum AS ENUM ('active', 'invited', 'suspended');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            slug VARCHAR(120) NOT NULL,
            legal_name VARCHAR(255),
            description TEXT,
            status workspace_status_enum NOT NULL DEFAULT 'active',
            setup_mode workspace_setup_mode_enum NOT NULL DEFAULT 'self_service',
            is_active BOOLEAN NOT NULL DEFAULT true,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_workspaces_slug ON workspaces (slug)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_memberships (
            id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role workspace_role_enum NOT NULL DEFAULT 'operator',
            status membership_status_enum NOT NULL DEFAULT 'active',
            is_default BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_workspace_membership_workspace_user UNIQUE (workspace_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_memberships_workspace_id ON workspace_memberships (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_memberships_user_id ON workspace_memberships (user_id)")

    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_internal_staff BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS workspace_id UUID")
    op.execute("ALTER TABLE matters ADD COLUMN IF NOT EXISTS workspace_id UUID")
    op.execute("ALTER TABLE whatsapp_contacts ADD COLUMN IF NOT EXISTS workspace_id UUID")
    op.execute("ALTER TABLE whatsapp_connections ADD COLUMN IF NOT EXISTS workspace_id UUID")

    op.execute(
        """
        DO $$
        DECLARE
            default_workspace_id UUID;
        BEGIN
            SELECT id INTO default_workspace_id
            FROM workspaces
            WHERE deleted_at IS NULL
            ORDER BY created_at ASC
            LIMIT 1;

            IF default_workspace_id IS NULL THEN
                default_workspace_id := gen_random_uuid();
                INSERT INTO workspaces (
                    id,
                    name,
                    slug,
                    legal_name,
                    description,
                    status,
                    setup_mode,
                    is_active,
                    settings,
                    created_at,
                    updated_at
                )
                VALUES (
                    default_workspace_id,
                    'Workspace Padrão',
                    'workspace-padrao',
                    'Workspace Padrão',
                    'Workspace legado criado automaticamente pela migração SaaS.',
                    'active',
                    'assisted',
                    true,
                    '{"migratedFromSingleTenant": true}'::jsonb,
                    now(),
                    now()
                );
            END IF;

            UPDATE users
            SET is_internal_staff = false
            WHERE is_internal_staff IS NULL;

            UPDATE clients
            SET workspace_id = default_workspace_id
            WHERE workspace_id IS NULL;

            UPDATE matters
            SET workspace_id = default_workspace_id
            WHERE workspace_id IS NULL;

            UPDATE whatsapp_contacts
            SET workspace_id = default_workspace_id
            WHERE workspace_id IS NULL;

            UPDATE whatsapp_connections
            SET workspace_id = default_workspace_id
            WHERE workspace_id IS NULL;

            INSERT INTO workspace_memberships (
                id,
                workspace_id,
                user_id,
                role,
                status,
                is_default,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                default_workspace_id,
                users.id,
                CASE
                    WHEN users.role = 'admin' THEN 'workspace_owner'::workspace_role_enum
                    WHEN users.role = 'senior_lawyer' THEN 'legal_manager'::workspace_role_enum
                    WHEN users.role = 'intern' THEN 'viewer'::workspace_role_enum
                    ELSE 'operator'::workspace_role_enum
                END,
                'active'::membership_status_enum,
                true,
                now(),
                now()
            FROM users
            WHERE users.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM workspace_memberships memberships
                  WHERE memberships.workspace_id = default_workspace_id
                    AND memberships.user_id = users.id
              );
        END $$;
        """
    )

    op.execute("ALTER TABLE clients ALTER COLUMN workspace_id SET NOT NULL")
    op.execute("ALTER TABLE matters ALTER COLUMN workspace_id SET NOT NULL")
    op.execute("ALTER TABLE whatsapp_contacts ALTER COLUMN workspace_id SET NOT NULL")
    op.execute("ALTER TABLE whatsapp_connections ALTER COLUMN workspace_id SET NOT NULL")

    op.execute("CREATE INDEX IF NOT EXISTS ix_clients_workspace_id ON clients (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_matters_workspace_id ON matters (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_whatsapp_contacts_workspace_id ON whatsapp_contacts (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_whatsapp_connections_workspace_id ON whatsapp_connections (workspace_id)")

    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE clients
            ADD CONSTRAINT fk_clients_workspace_id_workspaces
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE matters
            ADD CONSTRAINT fk_matters_workspace_id_workspaces
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE whatsapp_contacts
            ADD CONSTRAINT fk_whatsapp_contacts_workspace_id_workspaces
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE whatsapp_connections
            ADD CONSTRAINT fk_whatsapp_connections_workspace_id_workspaces
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.execute("DROP INDEX IF EXISTS ix_clients_cpf")
    op.execute("CREATE INDEX IF NOT EXISTS ix_clients_cpf ON clients (cpf)")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_clients_workspace_cpf
        ON clients (workspace_id, cpf)
        WHERE cpf IS NOT NULL
        """
    )

    op.execute("DROP INDEX IF EXISTS ix_matters_case_number")
    op.execute("CREATE INDEX IF NOT EXISTS ix_matters_case_number ON matters (case_number)")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_matters_workspace_case_number
        ON matters (workspace_id, case_number)
        WHERE case_number IS NOT NULL
        """
    )

    op.execute("DROP INDEX IF EXISTS ix_whatsapp_contacts_phone")
    op.execute("CREATE INDEX IF NOT EXISTS ix_whatsapp_contacts_phone ON whatsapp_contacts (phone)")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_contacts_workspace_phone
        ON whatsapp_contacts (workspace_id, phone)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_connections_workspace
        ON whatsapp_connections (workspace_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_connections_phone_number
        ON whatsapp_connections (phone_number_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_whatsapp_connections_phone_number")
    op.execute("DROP INDEX IF EXISTS uq_whatsapp_connections_workspace")
    op.execute("DROP INDEX IF EXISTS uq_whatsapp_contacts_workspace_phone")
    op.execute("DROP INDEX IF EXISTS uq_matters_workspace_case_number")
    op.execute("DROP INDEX IF EXISTS uq_clients_workspace_cpf")

    op.execute("DROP INDEX IF EXISTS ix_whatsapp_connections_workspace_id")
    op.execute("DROP INDEX IF EXISTS ix_whatsapp_contacts_workspace_id")
    op.execute("DROP INDEX IF EXISTS ix_matters_workspace_id")
    op.execute("DROP INDEX IF EXISTS ix_clients_workspace_id")

    op.execute("DROP INDEX IF EXISTS ix_whatsapp_contacts_phone")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_whatsapp_contacts_phone ON whatsapp_contacts (phone)")

    op.execute("DROP INDEX IF EXISTS ix_matters_case_number")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_matters_case_number ON matters (case_number)")

    op.execute("DROP INDEX IF EXISTS ix_clients_cpf")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_clients_cpf ON clients (cpf)")

    op.execute("ALTER TABLE whatsapp_connections DROP CONSTRAINT IF EXISTS fk_whatsapp_connections_workspace_id_workspaces")
    op.execute("ALTER TABLE whatsapp_contacts DROP CONSTRAINT IF EXISTS fk_whatsapp_contacts_workspace_id_workspaces")
    op.execute("ALTER TABLE matters DROP CONSTRAINT IF EXISTS fk_matters_workspace_id_workspaces")
    op.execute("ALTER TABLE clients DROP CONSTRAINT IF EXISTS fk_clients_workspace_id_workspaces")

    op.execute("ALTER TABLE whatsapp_connections DROP COLUMN IF EXISTS workspace_id")
    op.execute("ALTER TABLE whatsapp_contacts DROP COLUMN IF EXISTS workspace_id")
    op.execute("ALTER TABLE matters DROP COLUMN IF EXISTS workspace_id")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS workspace_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_internal_staff")

    op.execute("DROP TABLE IF EXISTS workspace_memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS workspaces CASCADE")

    op.execute("DROP TYPE IF EXISTS membership_status_enum")
    op.execute("DROP TYPE IF EXISTS workspace_role_enum")
    op.execute("DROP TYPE IF EXISTS workspace_setup_mode_enum")
    op.execute("DROP TYPE IF EXISTS workspace_status_enum")
