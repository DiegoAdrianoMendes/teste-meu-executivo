#!/bin/sh
set -eu

: "${PGHOST:=postgres}"
: "${PGPORT:=5432}"
: "${POSTGRES_DB:=klm_legal_hub}"
: "${POSTGRES_USER:=klm_user}"
: "${KLM_APP_DB_USER:=klm_app}"
: "${KLM_APP_DB_PASSWORD:=klm_app_password}"

if [ "$KLM_APP_DB_USER" = "$POSTGRES_USER" ]; then
  echo "Runtime and migration database roles must be different." >&2
  exit 1
fi

export PGHOST PGPORT

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 \
  --set app_user="$KLM_APP_DB_USER" \
  --set app_password="$KLM_APP_DB_PASSWORD" \
  --set migrator_user="$POSTGRES_USER" <<'SQL'
SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
    :'app_user',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
    :'app_user',
    :'app_password'
)
\gexec
SELECT format('ALTER ROLE %I SET row_security TO on', :'app_user')
\gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('REVOKE CREATE ON SCHEMA public FROM %I', :'app_user')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user')
\gexec
SELECT format(
    'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
    :'app_user'
)
\gexec
SELECT format(
    'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I',
    :'app_user'
)
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'migrator_user',
    :'app_user'
)
\gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT USAGE, SELECT ON SEQUENCES TO %I',
    :'migrator_user',
    :'app_user'
)
\gexec

-- Re-running the bootstrap after migrations must not re-expand the runtime
-- role beyond the operations represented by RLS.  Append-only tables are
-- stricter than legacy FOR ALL policies: their immutable trigger means the
-- application needs INSERT/SELECT, never UPDATE/DELETE.  The head migration
-- performs the same reconciliation transactionally when tables are first
-- created from the broad default privilege template above.
WITH protected_tables AS (
    SELECT namespace.nspname AS schema_name,
           relation.relname AS table_name,
           EXISTS (
               SELECT 1
               FROM pg_trigger immutable_trigger
               WHERE immutable_trigger.tgrelid = relation.oid
                 AND NOT immutable_trigger.tgisinternal
                 AND immutable_trigger.tgname LIKE '%immutable%'
           ) AS immutable
    FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relkind IN ('r', 'p')
      AND relation.relrowsecurity
), forbidden_privileges AS (
    SELECT protected.schema_name,
           protected.table_name,
           candidate.privilege
    FROM protected_tables protected
    CROSS JOIN (
        VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')
    ) AS candidate(privilege)
    WHERE (
        protected.immutable
        AND candidate.privilege IN ('UPDATE', 'DELETE')
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_policies policy
        WHERE policy.schemaname = protected.schema_name
          AND policy.tablename = protected.table_name
          AND policy.cmd IN ('ALL', candidate.privilege)
    )
)
SELECT format(
    'REVOKE %s ON TABLE %I.%I FROM %I',
    string_agg(privilege, ', ' ORDER BY privilege),
    schema_name,
    table_name,
    :'app_user'
)
FROM forbidden_privileges
GROUP BY schema_name, table_name
\gexec

-- Global control-plane deliveries deliberately have no tenant RLS, but their
-- request contract is immutable and deletion is forbidden. A repeat role
-- bootstrap must not undo the migration's DELETE revocation.
SELECT format(
    'REVOKE DELETE ON TABLE %I FROM %I',
    'platform_official_whatsapp_deliveries',
    :'app_user'
)
WHERE to_regclass('public.platform_official_whatsapp_deliveries') IS NOT NULL
\gexec
SQL
