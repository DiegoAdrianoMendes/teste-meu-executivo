"""add fail-closed WABA template webhook tenant resolver

Revision ID: a3d5e7f9b142
Revises: f2c4d6e8a031
Create Date: 2026-07-31 00:30:00.000000

Meta template-status webhooks identify the WhatsApp Business Account rather
than one phone number.  Resolve that bootstrap identifier without bypassing
tenant RLS, and return NULL whenever the same WABA is bound to more than one
workspace instead of selecting an arbitrary tenant.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a3d5e7f9b142"
down_revision: str | Sequence[str] | None = "f2c4d6e8a031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION_NAME = "klm_resolve_whatsapp_template_webhook_workspace"
FUNCTION_SIGNATURE = f"{FUNCTION_NAME}(text)"
PREVIOUS_RESOLVER_SIGNATURES = (
    "klm_resolve_zapsign_webhook_workspace(uuid,text)",
    "klm_resolve_whatsapp_webhook_workspace(text)",
    "klm_resolve_signup_verification_workspace(text,uuid)",
    "klm_resolve_workspace_invitation_workspace(uuid,text,uuid)",
)
EXPECTED_BODY = """
    SELECT CASE
        WHEN COUNT(DISTINCT connection.workspace_id) = 1
        THEN MIN(connection.workspace_id::text)::uuid
        ELSE NULL::uuid
    END
    FROM public.whatsapp_connections AS connection
    WHERE connection.waba_id = p_waba_id
"""


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _normalized_body(value: str) -> str:
    return " ".join(value.split())


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        f"""
        CREATE FUNCTION public.{FUNCTION_NAME}(
            p_waba_id text
        ) RETURNS uuid
        LANGUAGE sql
        STABLE
        STRICT
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            {EXPECTED_BODY}
        $function$
        """
    )

    op.execute(
        f"""
        DO $template_resolver$
        DECLARE
            migrator_role name := current_user;
            resolver_owner name;
            runtime_roles name[];
            runtime_role name;
        BEGIN
            resolver_owner := left(migrator_role, 39)
                || '_resolver_owner_'
                || left(md5(migrator_role), 8);
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_roles AS owner
                WHERE owner.rolname = resolver_owner
                  AND NOT owner.rolcanlogin
                  AND NOT owner.rolsuper
                  AND NOT owner.rolcreatedb
                  AND NOT owner.rolcreaterole
                  AND NOT owner.rolinherit
                  AND NOT owner.rolreplication
                  AND NOT owner.rolbypassrls
                  AND pg_has_role(migrator_role, owner.oid, 'MEMBER')
                  AND has_schema_privilege(owner.rolname, 'public', 'USAGE')
            ) THEN
                RAISE EXCEPTION
                    'Dedicated NOLOGIN resolver owner is missing or unsafe';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_policies AS policy
                WHERE policy.schemaname = 'public'
                  AND policy.tablename = 'whatsapp_connections'
                  AND policy.policyname = 'klm_resolver_owner_select'
                  AND policy.cmd = 'SELECT'
                  AND policy.permissive = 'PERMISSIVE'
                  AND resolver_owner::text = ANY(policy.roles::text[])
                  AND policy.qual = 'true'
                  AND policy.with_check IS NULL
            ) THEN
                RAISE EXCEPTION
                    'Resolver owner WhatsApp RLS policy is missing or unsafe';
            END IF;

            SELECT array_agg(candidate.rolname ORDER BY candidate.rolname)
            INTO runtime_roles
            FROM (
                SELECT grantee.rolname
                FROM pg_catalog.pg_proc AS procedure
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        procedure.proacl,
                        pg_catalog.acldefault('f', procedure.proowner)
                    )
                ) AS privilege
                JOIN pg_catalog.pg_roles AS grantee
                  ON grantee.oid = privilege.grantee
                WHERE procedure.oid = ANY(ARRAY[
                    {", ".join(
                        "to_regprocedure('public." + item + "')"
                        for item in PREVIOUS_RESOLVER_SIGNATURES
                    )}
                ]::oid[])
                  AND privilege.privilege_type = 'EXECUTE'
                  AND grantee.rolname <> resolver_owner
                  AND grantee.rolcanlogin
                  AND NOT grantee.rolsuper
                  AND NOT grantee.rolcreatedb
                  AND NOT grantee.rolcreaterole
                  AND NOT grantee.rolinherit
                  AND NOT grantee.rolreplication
                  AND NOT grantee.rolbypassrls
                  AND has_schema_privilege(grantee.rolname, 'public', 'USAGE')
                  AND NOT has_schema_privilege(grantee.rolname, 'public', 'CREATE')
                GROUP BY grantee.rolname
                HAVING COUNT(DISTINCT procedure.oid) = 4
            ) AS candidate;
            IF COALESCE(array_length(runtime_roles, 1), 0) <> 1 THEN
                RAISE EXCEPTION
                    'Expected exactly one runtime resolver grantee; found %',
                    COALESCE(array_length(runtime_roles, 1), 0);
            END IF;
            runtime_role := runtime_roles[1];

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_proc AS procedure
                WHERE procedure.oid = 'public.{FUNCTION_SIGNATURE}'::regprocedure
                  AND pg_catalog.pg_get_userbyid(procedure.proowner) = migrator_role
            ) THEN
                RAISE EXCEPTION
                    'New template resolver is missing or not migrator-owned';
            END IF;

            EXECUTE format(
                'GRANT CREATE ON SCHEMA public TO %I GRANTED BY %I',
                resolver_owner,
                migrator_role
            );
            EXECUTE format(
                'ALTER FUNCTION public.{FUNCTION_SIGNATURE} OWNER TO %I',
                resolver_owner
            );
            EXECUTE format('SET LOCAL ROLE %I', resolver_owner);
            EXECUTE
                'REVOKE ALL ON FUNCTION public.{FUNCTION_SIGNATURE} FROM PUBLIC';
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION public.{FUNCTION_SIGNATURE} TO %I',
                runtime_role
            );
            EXECUTE 'RESET ROLE';
            EXECUTE format(
                'REVOKE CREATE ON SCHEMA public FROM %I GRANTED BY %I',
                resolver_owner,
                migrator_role
            );
        END
        $template_resolver$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    expected_body = _sql_literal(_normalized_body(EXPECTED_BODY))
    op.execute(
        f"""
        DO $template_resolver_downgrade$
        DECLARE
            migrator_role name := current_user;
            resolver_owner name;
            runtime_roles name[];
            runtime_role name;
        BEGIN
            resolver_owner := left(migrator_role, 39)
                || '_resolver_owner_'
                || left(md5(migrator_role), 8);
            IF NOT pg_has_role(migrator_role, resolver_owner, 'MEMBER') THEN
                RAISE EXCEPTION
                    'Migrator lost dedicated resolver owner membership';
            END IF;

            SELECT array_agg(grantee.rolname ORDER BY grantee.rolname)
            INTO runtime_roles
            FROM pg_catalog.pg_proc AS procedure
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    procedure.proacl,
                    pg_catalog.acldefault('f', procedure.proowner)
                )
            ) AS privilege
            JOIN pg_catalog.pg_roles AS grantee
              ON grantee.oid = privilege.grantee
            WHERE procedure.oid = 'public.{FUNCTION_SIGNATURE}'::regprocedure
              AND privilege.privilege_type = 'EXECUTE'
              AND grantee.rolname <> resolver_owner
              AND grantee.rolcanlogin
              AND NOT grantee.rolsuper
              AND NOT grantee.rolcreatedb
              AND NOT grantee.rolcreaterole
              AND NOT grantee.rolinherit
              AND NOT grantee.rolreplication
              AND NOT grantee.rolbypassrls;
            IF COALESCE(array_length(runtime_roles, 1), 0) <> 1 THEN
                RAISE EXCEPTION
                    'Expected exactly one runtime template resolver grantee; found %',
                    COALESCE(array_length(runtime_roles, 1), 0);
            END IF;
            runtime_role := runtime_roles[1];

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_proc AS procedure
                JOIN pg_catalog.pg_language AS language
                  ON language.oid = procedure.prolang
                WHERE procedure.oid = 'public.{FUNCTION_SIGNATURE}'::regprocedure
                  AND pg_catalog.pg_get_userbyid(procedure.proowner) = resolver_owner
                  AND procedure.prosecdef
                  AND procedure.proconfig
                      = ARRAY['search_path=pg_catalog, pg_temp']::text[]
                  AND language.lanname = 'sql'
                  AND procedure.provolatile = 's'
                  AND procedure.proisstrict
                  AND btrim(
                      regexp_replace(procedure.prosrc, '\\s+', ' ', 'g')
                  ) = {expected_body}
                  AND has_function_privilege(
                      resolver_owner,
                      'public.{FUNCTION_SIGNATURE}',
                      'EXECUTE'
                  )
                  AND has_function_privilege(
                      runtime_role,
                      'public.{FUNCTION_SIGNATURE}',
                      'EXECUTE'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pg_catalog.pg_proc AS acl_procedure
                      CROSS JOIN LATERAL pg_catalog.aclexplode(
                          COALESCE(
                              acl_procedure.proacl,
                              pg_catalog.acldefault(
                                  'f',
                                  acl_procedure.proowner
                              )
                          )
                      ) AS privilege
                      LEFT JOIN pg_catalog.pg_roles AS grantee
                        ON grantee.oid = privilege.grantee
                      WHERE acl_procedure.oid =
                          'public.{FUNCTION_SIGNATURE}'::regprocedure
                        AND privilege.privilege_type = 'EXECUTE'
                        AND (
                            COALESCE(grantee.rolname, 'PUBLIC')
                                NOT IN (resolver_owner, runtime_role)
                            OR (
                                grantee.rolname = runtime_role
                                AND privilege.is_grantable
                            )
                        )
                  )
            ) THEN
                RAISE EXCEPTION
                    'Template resolver definition or ACL drift blocks downgrade';
            END IF;

            EXECUTE format('SET LOCAL ROLE %I', resolver_owner);
            EXECUTE 'DROP FUNCTION public.{FUNCTION_SIGNATURE}';
            EXECUTE 'RESET ROLE';
        END
        $template_resolver_downgrade$
        """
    )
