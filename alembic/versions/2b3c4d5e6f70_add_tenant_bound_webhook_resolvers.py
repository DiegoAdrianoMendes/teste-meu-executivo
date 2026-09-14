"""add tenant-bound SECURITY DEFINER webhook resolvers

Revision ID: 2b3c4d5e6f70
Revises: 2f4a6c8e0b1d
Create Date: 2026-07-16 18:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2b3c4d5e6f70"
down_revision: str | Sequence[str] | None = "2f4a6c8e0b1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ZAPSIGN_FUNCTION = "klm_resolve_zapsign_webhook_workspace(uuid,text)"
WHATSAPP_FUNCTION = "klm_resolve_whatsapp_webhook_workspace(text)"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.klm_resolve_zapsign_webhook_workspace(
            p_matter_id uuid,
            p_document_token text
        ) RETURNS uuid
        LANGUAGE sql
        STABLE
        STRICT
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT matter.workspace_id
            FROM public.matters AS matter
            WHERE matter.id = p_matter_id
              AND matter.zapsign_doc_token = p_document_token
              AND matter.deleted_at IS NULL
        $function$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.klm_resolve_whatsapp_webhook_workspace(
            p_phone_number_id text
        ) RETURNS uuid
        LANGUAGE sql
        STABLE
        STRICT
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT connection.workspace_id
            FROM public.whatsapp_connections AS connection
            WHERE connection.phone_number_id = p_phone_number_id
        $function$
        """
    )

    # PostgreSQL grants function EXECUTE to PUBLIC by default. Remove it and
    # grant only to non-privileged roles that already have explicit SELECT on
    # both protected routing tables (the runtime role in supported installs).
    op.execute(f"REVOKE ALL ON FUNCTION public.{ZAPSIGN_FUNCTION} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION public.{WHATSAPP_FUNCTION} FROM PUBLIC")
    op.execute(
        f"""
        DO $grant_runtime$
        DECLARE
            runtime_role name;
        BEGIN
            FOR runtime_role IN
                SELECT DISTINCT role.rolname
                FROM pg_catalog.pg_roles AS role
                WHERE role.rolcanlogin
                  AND NOT role.rolsuper
                  AND NOT role.rolbypassrls
                  AND has_table_privilege(role.rolname, 'public.matters', 'SELECT')
                  AND has_table_privilege(
                      role.rolname,
                      'public.whatsapp_connections',
                      'SELECT'
                  )
            LOOP
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.{ZAPSIGN_FUNCTION} TO %I',
                    runtime_role
                );
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.{WHATSAPP_FUNCTION} TO %I',
                    runtime_role
                );
            END LOOP;
        END
        $grant_runtime$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS public.{WHATSAPP_FUNCTION}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{ZAPSIGN_FUNCTION}")
