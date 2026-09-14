"""harden tenant resolver owner access through forced RLS

Revision ID: f2c4d6e8a031
Revises: e1b3c5d7e920
Create Date: 2026-07-30 18:00:00.000000

The four tenant lookup tables already FORCE row-level security.  Move their
SECURITY DEFINER functions from the LOGIN migrator to its dedicated NOLOGIN
owner, then give only that owner a narrow SELECT policy.  The identity outbox
is deliberately not added here: it is not an RLS table today and changing
that boundary requires a separate access inventory.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f2c4d6e8a031"
down_revision: str | Sequence[str] | None = "e1b3c5d7e920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_POLICY_NAME = "klm_resolver_owner_select"
FORCED_RLS_LOOKUP_TABLES = (
    "matters",
    "whatsapp_connections",
    "signup_verifications",
    "workspace_invitations",
)
RESOLVER_SIGNATURES = (
    "klm_resolve_zapsign_webhook_workspace(uuid,text)",
    "klm_resolve_whatsapp_webhook_workspace(text)",
    "klm_resolve_signup_verification_workspace(text,uuid)",
    "klm_resolve_workspace_invitation_workspace(uuid,text,uuid)",
)
RESOLVER_BODIES = {
    RESOLVER_SIGNATURES[0]: """
        SELECT matter.workspace_id
        FROM public.matters AS matter
        WHERE matter.id = p_matter_id
          AND matter.zapsign_doc_token = p_document_token
          AND matter.deleted_at IS NULL
    """,
    RESOLVER_SIGNATURES[1]: """
        SELECT connection.workspace_id
        FROM public.whatsapp_connections AS connection
        WHERE connection.phone_number_id = p_phone_number_id
    """,
    RESOLVER_SIGNATURES[2]: """
        SELECT verification.workspace_id
        FROM public.signup_verifications AS verification
        WHERE ((p_token_digest IS NOT NULL) <> (p_delivery_id IS NOT NULL))
          AND (
                verification.token_digest = p_token_digest
                OR EXISTS (
                    SELECT 1
                    FROM public.identity_outbox_messages AS delivery
                    WHERE delivery.id = p_delivery_id
                      AND delivery.related_id = verification.id
                      AND delivery.related_type = 'signup_verification'
                      AND delivery.purpose = 'signup_verification'
                )
          )
        LIMIT 1
    """,
    RESOLVER_SIGNATURES[3]: """
        SELECT invitation.workspace_id
        FROM public.workspace_invitations AS invitation
        WHERE invitation.id = p_invitation_id
          AND ((p_token_digest IS NOT NULL) <> (p_delivery_id IS NOT NULL))
          AND (
                invitation.token_digest = p_token_digest
                OR EXISTS (
                    SELECT 1
                    FROM public.identity_outbox_messages AS delivery
                    WHERE delivery.id = p_delivery_id
                      AND delivery.related_id = invitation.id
                      AND delivery.related_type = 'workspace_invitation'
                      AND delivery.purpose = 'workspace_invitation'
                )
          )
        LIMIT 1
    """,
}
STRICT_RESOLVERS = frozenset(RESOLVER_SIGNATURES[:2])


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _normalized_body(value: str) -> str:
    return " ".join(value.split())


def _create_owner_policies() -> None:
    for table_name in FORCED_RLS_LOOKUP_TABLES:
        op.execute(
            f"""
            DO $owner_policy$
            BEGIN
                EXECUTE format(
                    'CREATE POLICY %I ON public.%I '
                    'AS PERMISSIVE FOR SELECT TO %I USING (true)',
                    '{OWNER_POLICY_NAME}',
                    '{table_name}',
                    left(current_user, 39)
                        || '_resolver_owner_'
                        || left(md5(current_user), 8)
                );
            END
            $owner_policy$
            """
        )


def _restrict_resolver_execution() -> None:
    signatures = ", ".join(f"'{signature}'" for signature in RESOLVER_SIGNATURES)
    op.execute(
        f"""
        DO $resolver_acl$
        DECLARE
            runtime_roles name[];
            runtime_role name;
            migrator_role name;
            resolver_owner name;
            resolver_signature text;
            lookup_table text;
            granted_role name;
        BEGIN
            migrator_role := current_user;
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
            EXECUTE format(
                'GRANT CREATE ON SCHEMA public TO %I GRANTED BY %I',
                resolver_owner,
                migrator_role
            );
            IF NOT has_schema_privilege(resolver_owner, 'public', 'CREATE') THEN
                RAISE EXCEPTION
                    'Could not delegate temporary schema CREATE to resolver owner';
            END IF;

            SELECT array_agg(role.rolname ORDER BY role.rolname)
            INTO runtime_roles
            FROM pg_catalog.pg_roles AS role
            WHERE role.rolname <> migrator_role
              AND role.rolname <> resolver_owner
              AND role.rolcanlogin
              AND NOT role.rolsuper
              AND NOT role.rolcreatedb
              AND NOT role.rolcreaterole
              AND NOT role.rolinherit
              AND NOT role.rolreplication
              AND NOT role.rolbypassrls
              AND has_schema_privilege(role.rolname, 'public', 'USAGE')
              AND NOT has_schema_privilege(role.rolname, 'public', 'CREATE')
              AND has_table_privilege(role.rolname, 'public.matters', 'SELECT')
              AND has_table_privilege(
                    role.rolname,
                    'public.whatsapp_connections',
                    'SELECT'
              )
              AND has_table_privilege(
                    role.rolname,
                    'public.signup_verifications',
                    'SELECT'
              )
              AND has_table_privilege(
                    role.rolname,
                    'public.workspace_invitations',
                    'SELECT'
              )
              AND has_table_privilege(
                    role.rolname,
                    'public.identity_outbox_messages',
                    'SELECT'
              )
              AND has_function_privilege(
                    role.rolname,
                    'public.klm_resolve_zapsign_webhook_workspace(uuid,text)',
                    'EXECUTE'
              )
              AND has_function_privilege(
                    role.rolname,
                    'public.klm_resolve_whatsapp_webhook_workspace(text)',
                    'EXECUTE'
              )
              AND has_function_privilege(
                    role.rolname,
                    'public.klm_resolve_signup_verification_workspace(text,uuid)',
                    'EXECUTE'
              )
              AND has_function_privilege(
                    role.rolname,
                    'public.klm_resolve_workspace_invitation_workspace(uuid,text,uuid)',
                    'EXECUTE'
              );

            IF COALESCE(array_length(runtime_roles, 1), 0) <> 1 THEN
                RAISE EXCEPTION
                    'Expected exactly one non-privileged runtime resolver grantee; found %',
                    COALESCE(array_length(runtime_roles, 1), 0);
            END IF;
            runtime_role := runtime_roles[1];

            FOREACH lookup_table IN ARRAY ARRAY[
                'matters',
                'whatsapp_connections',
                'signup_verifications',
                'workspace_invitations',
                'identity_outbox_messages'
            ]
            LOOP
                EXECUTE format(
                    'REVOKE ALL ON TABLE public.%I FROM %I',
                    lookup_table,
                    resolver_owner
                );
                EXECUTE format(
                    'GRANT SELECT ON TABLE public.%I TO %I',
                    lookup_table,
                    resolver_owner
                );
            END LOOP;

            FOREACH resolver_signature IN ARRAY ARRAY[{signatures}]
            LOOP
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_proc AS procedure
                    WHERE procedure.oid = (
                        'public.' || resolver_signature
                    )::regprocedure
                      AND pg_catalog.pg_get_userbyid(procedure.proowner) = migrator_role
                ) THEN
                    RAISE EXCEPTION
                        'Resolver is missing or not migrator-owned: %',
                        resolver_signature;
                END IF;
                IF NOT has_function_privilege(
                    migrator_role,
                    'public.' || resolver_signature,
                    'EXECUTE'
                ) OR NOT has_function_privilege(
                    runtime_role,
                    'public.' || resolver_signature,
                    'EXECUTE'
                ) THEN
                    RAISE EXCEPTION
                        'Resolver ACL drift blocks owner hardening: %',
                        resolver_signature;
                END IF;

                -- RDS master users are not PostgreSQL superusers.  Older
                -- resolver migrations therefore mistook the bootstrap
                -- administrator for an application runtime when it inherited
                -- table SELECT through the migrator role.  Remove only those
                -- legacy grants held by explicitly privileged login roles;
                -- PUBLIC and ordinary unexpected grantees still fail closed
                -- in the exact ACL check immediately below.
                FOR granted_role IN
                    SELECT DISTINCT grantee.rolname
                    FROM pg_catalog.pg_proc AS procedure
                    CROSS JOIN LATERAL pg_catalog.aclexplode(
                        COALESCE(
                            procedure.proacl,
                            pg_catalog.acldefault('f', procedure.proowner)
                        )
                    ) AS privilege
                    JOIN pg_catalog.pg_roles AS grantee
                      ON grantee.oid = privilege.grantee
                    WHERE procedure.oid = (
                        'public.' || resolver_signature
                    )::regprocedure
                      AND privilege.privilege_type = 'EXECUTE'
                      AND grantee.rolname NOT IN (migrator_role, runtime_role)
                      AND grantee.rolcanlogin
                      AND (
                          grantee.rolsuper
                          OR grantee.rolcreatedb
                          OR grantee.rolcreaterole
                          OR grantee.rolreplication
                          OR grantee.rolbypassrls
                      )
                LOOP
                    EXECUTE format(
                        'REVOKE ALL ON FUNCTION public.%s FROM %I',
                        resolver_signature,
                        granted_role
                    );
                END LOOP;

                IF EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_proc AS procedure
                    CROSS JOIN LATERAL pg_catalog.aclexplode(
                        COALESCE(
                            procedure.proacl,
                            pg_catalog.acldefault('f', procedure.proowner)
                        )
                    ) AS privilege
                    LEFT JOIN pg_catalog.pg_roles AS grantee
                      ON grantee.oid = privilege.grantee
                    WHERE procedure.oid = (
                        'public.' || resolver_signature
                    )::regprocedure
                      AND privilege.privilege_type = 'EXECUTE'
                      AND (
                          COALESCE(grantee.rolname, 'PUBLIC')
                              NOT IN (migrator_role, runtime_role)
                          OR (
                              grantee.rolname = runtime_role
                              AND privilege.is_grantable
                          )
                      )
                ) THEN
                    RAISE EXCEPTION
                        'Resolver ACL drift blocks owner hardening: %',
                        resolver_signature;
                END IF;

                EXECUTE format(
                    'ALTER FUNCTION public.%s OWNER TO %I',
                    resolver_signature,
                    resolver_owner
                );
            END LOOP;

            EXECUTE format('SET LOCAL ROLE %I', resolver_owner);
            FOREACH resolver_signature IN ARRAY ARRAY[{signatures}]
            LOOP
                EXECUTE format(
                    'REVOKE ALL ON FUNCTION public.%s FROM PUBLIC',
                    resolver_signature
                );

                FOR granted_role IN
                    SELECT DISTINCT grantee.rolname
                    FROM pg_catalog.pg_proc AS procedure
                    CROSS JOIN LATERAL pg_catalog.aclexplode(
                        COALESCE(
                            procedure.proacl,
                            pg_catalog.acldefault('f', procedure.proowner)
                        )
                    ) AS privilege
                    JOIN pg_catalog.pg_roles AS grantee
                      ON grantee.oid = privilege.grantee
                    WHERE procedure.oid = (
                        'public.' || resolver_signature
                    )::regprocedure
                      AND privilege.privilege_type = 'EXECUTE'
                      AND grantee.rolname NOT IN (resolver_owner, runtime_role)
                LOOP
                    EXECUTE format(
                        'REVOKE ALL ON FUNCTION public.%s FROM %I',
                        resolver_signature,
                        granted_role
                    );
                END LOOP;

                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.%s TO %I',
                    resolver_signature,
                    runtime_role
                );
            END LOOP;
            EXECUTE 'RESET ROLE';

            EXECUTE format(
                'REVOKE CREATE ON SCHEMA public FROM %I GRANTED BY %I',
                resolver_owner,
                migrator_role
            );
        END
        $resolver_acl$
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _create_owner_policies()
    _restrict_resolver_execution()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    signatures = ", ".join(f"'{signature}'" for signature in RESOLVER_SIGNATURES)
    expected_body_case = " ".join(
        f"WHEN {_sql_literal(signature)} THEN "
        f"{_sql_literal(_normalized_body(RESOLVER_BODIES[signature]))}"
        for signature in RESOLVER_SIGNATURES
    )
    expected_strict_case = " ".join(
        f"WHEN {_sql_literal(signature)} THEN "
        f"{'true' if signature in STRICT_RESOLVERS else 'false'}"
        for signature in RESOLVER_SIGNATURES
    )
    op.execute(
        f"""
        DO $resolver_owner_downgrade$
        DECLARE
            runtime_roles name[];
            runtime_role name;
            migrator_role name;
            resolver_owner name;
            resolver_signature text;
            resolver_definition text;
            resolver_definitions text[] := ARRAY[]::text[];
            lookup_table text;
        BEGIN
            migrator_role := current_user;
            resolver_owner := left(migrator_role, 39)
                || '_resolver_owner_'
                || left(md5(migrator_role), 8);
            IF NOT pg_has_role(migrator_role, resolver_owner, 'MEMBER') THEN
                RAISE EXCEPTION 'Migrator lost dedicated resolver owner membership';
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
                    to_regprocedure(
                        'public.klm_resolve_zapsign_webhook_workspace(uuid,text)'
                    ),
                    to_regprocedure(
                        'public.klm_resolve_whatsapp_webhook_workspace(text)'
                    ),
                    to_regprocedure(
                        'public.klm_resolve_signup_verification_workspace(text,uuid)'
                    ),
                    to_regprocedure(
                        'public.klm_resolve_workspace_invitation_workspace(uuid,text,uuid)'
                    )
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
                GROUP BY grantee.rolname
                HAVING COUNT(DISTINCT procedure.oid) = 4
            ) AS candidate;
            IF COALESCE(array_length(runtime_roles, 1), 0) <> 1 THEN
                RAISE EXCEPTION
                    'Expected exactly one runtime resolver grantee during downgrade; found %',
                    COALESCE(array_length(runtime_roles, 1), 0);
            END IF;
            runtime_role := runtime_roles[1];

            FOREACH resolver_signature IN ARRAY ARRAY[{signatures}]
            LOOP
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_proc AS procedure
                    JOIN pg_catalog.pg_language AS language
                      ON language.oid = procedure.prolang
                    WHERE procedure.oid = (
                        'public.' || resolver_signature
                    )::regprocedure
                      AND pg_catalog.pg_get_userbyid(procedure.proowner)
                          = resolver_owner
                      AND procedure.prosecdef
                      AND procedure.proconfig
                          = ARRAY['search_path=pg_catalog, pg_temp']::text[]
                      AND language.lanname = 'sql'
                      AND procedure.provolatile = 's'
                      AND procedure.proisstrict = CASE resolver_signature
                          {expected_strict_case}
                          ELSE NULL
                      END
                      AND btrim(
                          regexp_replace(procedure.prosrc, '\\s+', ' ', 'g')
                      ) = CASE resolver_signature
                          {expected_body_case}
                          ELSE NULL
                      END
                ) THEN
                    RAISE EXCEPTION
                        'Resolver definition drift blocks downgrade: %',
                        resolver_signature;
                END IF;
                IF NOT has_function_privilege(
                    resolver_owner,
                    'public.' || resolver_signature,
                    'EXECUTE'
                ) OR NOT has_function_privilege(
                    runtime_role,
                    'public.' || resolver_signature,
                    'EXECUTE'
                ) OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_proc AS procedure
                    CROSS JOIN LATERAL pg_catalog.aclexplode(
                        COALESCE(
                            procedure.proacl,
                            pg_catalog.acldefault('f', procedure.proowner)
                        )
                    ) AS privilege
                    LEFT JOIN pg_catalog.pg_roles AS grantee
                      ON grantee.oid = privilege.grantee
                    WHERE procedure.oid = (
                        'public.' || resolver_signature
                    )::regprocedure
                      AND privilege.privilege_type = 'EXECUTE'
                      AND (
                          COALESCE(grantee.rolname, 'PUBLIC')
                              NOT IN (resolver_owner, runtime_role)
                          OR (
                              grantee.rolname = runtime_role
                              AND privilege.is_grantable
                          )
                      )
                ) THEN
                    RAISE EXCEPTION
                        'Resolver ACL drift blocks downgrade: %',
                        resolver_signature;
                END IF;
                SELECT pg_catalog.pg_get_functiondef(
                           ('public.' || resolver_signature)::regprocedure
                       )
                INTO resolver_definition;
                resolver_definitions := array_append(
                    resolver_definitions,
                    resolver_definition
                );
            END LOOP;

            EXECUTE format(
                'GRANT CREATE ON SCHEMA public TO %I GRANTED BY %I',
                resolver_owner,
                migrator_role
            );
            EXECUTE format('SET LOCAL ROLE %I', resolver_owner);
            FOREACH resolver_signature IN ARRAY ARRAY[{signatures}]
            LOOP
                EXECUTE format(
                    'DROP FUNCTION public.%s',
                    resolver_signature
                );
            END LOOP;
            EXECUTE 'RESET ROLE';

            FOREACH resolver_definition IN ARRAY resolver_definitions
            LOOP
                EXECUTE resolver_definition;
            END LOOP;
            FOREACH resolver_signature IN ARRAY ARRAY[{signatures}]
            LOOP
                EXECUTE format(
                    'REVOKE ALL ON FUNCTION public.%s FROM PUBLIC',
                    resolver_signature
                );
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.%s TO %I',
                    resolver_signature,
                    runtime_role
                );
            END LOOP;
            FOREACH lookup_table IN ARRAY ARRAY[
                'matters',
                'whatsapp_connections',
                'signup_verifications',
                'workspace_invitations',
                'identity_outbox_messages'
            ]
            LOOP
                EXECUTE format(
                    'REVOKE ALL ON TABLE public.%I FROM %I',
                    lookup_table,
                    resolver_owner
                );
            END LOOP;
            EXECUTE format(
                'REVOKE CREATE ON SCHEMA public FROM %I GRANTED BY %I',
                resolver_owner,
                migrator_role
            );
        END
        $resolver_owner_downgrade$
        """
    )
    for table_name in reversed(FORCED_RLS_LOOKUP_TABLES):
        op.execute(
            f'DROP POLICY IF EXISTS "{OWNER_POLICY_NAME}" ON public."{table_name}"'
        )
