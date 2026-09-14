"""Fail-closed AWS PostgreSQL role bootstrap and migration preflight.

The RDS managed master credential is intentionally consumed only by the
one-shot bootstrap task.  Normal migrations and application processes must
use their own unprivileged credentials.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import md5
from pathlib import Path

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url

_ROLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
_DATABASE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_BOOTSTRAP_LOCK_KEY = 4_809_260_731
_MINIMUM_PASSWORD_LENGTH = 16
_GREENFIELD_HEAD_REVISION = "c9d0e1f2a3b4"
_REQUIRED_MIGRATION_EXTENSIONS = ("btree_gist", "pg_trgm")
_RESOLVER_FUNCTION_NAMES = (
    "klm_resolve_zapsign_webhook_workspace",
    "klm_resolve_whatsapp_webhook_workspace",
    "klm_resolve_whatsapp_template_webhook_workspace",
    "klm_resolve_asaas_webhook_workspace",
    "klm_resolve_signup_verification_workspace",
    "klm_resolve_workspace_invitation_workspace",
)
_RESOLVER_FUNCTION_SIGNATURES = (
    "public.klm_resolve_zapsign_webhook_workspace(uuid,text)",
    "public.klm_resolve_whatsapp_webhook_workspace(text)",
    "public.klm_resolve_whatsapp_template_webhook_workspace(text)",
    "public.klm_resolve_asaas_webhook_workspace(text)",
    "public.klm_resolve_signup_verification_workspace(text,uuid)",
    "public.klm_resolve_workspace_invitation_workspace(uuid,text,uuid)",
)
_RESOLVER_REGPROCEDURES_SQL = ",\n                    ".join(
    f"to_regprocedure('{signature}')" for signature in _RESOLVER_FUNCTION_SIGNATURES
)
_GLOBAL_RUNTIME_PRIVILEGE_EXCEPTIONS = {
    "platform_official_whatsapp_deliveries": frozenset({"SELECT", "INSERT", "UPDATE"}),
}


class DatabaseRoleBootstrapError(RuntimeError):
    """Sanitized bootstrap failure safe to write to operational logs."""


@dataclass(frozen=True)
class DatabaseRoleCredential:
    username: str
    password: str


@dataclass(frozen=True)
class DatabaseRoleBootstrapConfig:
    host: str
    port: int
    database: str
    master: DatabaseRoleCredential
    migrator: DatabaseRoleCredential
    runtime: DatabaseRoleCredential
    sslmode: str = "require"


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key, "")
    if not value:
        raise DatabaseRoleBootstrapError(f"Required bootstrap setting is missing: {key}.")
    return value


def _validated_role(username: str, password: str, *, label: str) -> DatabaseRoleCredential:
    if not _ROLE_NAME_RE.fullmatch(username):
        raise DatabaseRoleBootstrapError(f"Invalid {label} database role name.")
    if len(password) < _MINIMUM_PASSWORD_LENGTH:
        raise DatabaseRoleBootstrapError(
            f"The {label} database password must contain at least "
            f"{_MINIMUM_PASSWORD_LENGTH} characters."
        )
    return DatabaseRoleCredential(username=username, password=password)


def load_bootstrap_config(
    environment: Mapping[str, str] | None = None,
) -> DatabaseRoleBootstrapConfig:
    """Load credentials from dedicated secret fields without parsing URLs."""

    values = os.environ if environment is None else environment
    try:
        port = int(_required(values, "KLM_DB_BOOTSTRAP_PORT"))
    except ValueError as exc:
        raise DatabaseRoleBootstrapError("Invalid PostgreSQL bootstrap port.") from exc
    if not 1 <= port <= 65535:
        raise DatabaseRoleBootstrapError("Invalid PostgreSQL bootstrap port.")

    database = _required(values, "KLM_DB_BOOTSTRAP_DATABASE")
    if not _DATABASE_NAME_RE.fullmatch(database):
        raise DatabaseRoleBootstrapError("Invalid PostgreSQL bootstrap database name.")

    host = _required(values, "KLM_DB_BOOTSTRAP_HOST")
    if any(character.isspace() for character in host):
        raise DatabaseRoleBootstrapError("Invalid PostgreSQL bootstrap host.")

    master = _validated_role(
        _required(values, "KLM_DB_BOOTSTRAP_MASTER_USERNAME"),
        _required(values, "KLM_DB_BOOTSTRAP_MASTER_PASSWORD"),
        label="master",
    )
    migrator = _validated_role(
        _required(values, "KLM_DB_BOOTSTRAP_MIGRATOR_USERNAME"),
        _required(values, "KLM_DB_BOOTSTRAP_MIGRATOR_PASSWORD"),
        label="migrator",
    )
    runtime = _validated_role(
        _required(values, "KLM_DB_BOOTSTRAP_RUNTIME_USERNAME"),
        _required(values, "KLM_DB_BOOTSTRAP_RUNTIME_PASSWORD"),
        label="runtime",
    )
    role_names = {master.username, migrator.username, runtime.username}
    if len(role_names) != 3:
        raise DatabaseRoleBootstrapError(
            "Master, migrator and runtime database roles must be distinct."
        )

    return DatabaseRoleBootstrapConfig(
        host=host,
        port=port,
        database=database,
        master=master,
        migrator=migrator,
        runtime=runtime,
    )


def _connect(config: DatabaseRoleBootstrapConfig, credential: DatabaseRoleCredential):
    return psycopg2.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=credential.username,
        password=credential.password,
        sslmode=config.sslmode,
        connect_timeout=10,
        application_name="meu-executivo-db-role-bootstrap",
    )


def _create_role_statement(credential: DatabaseRoleCredential) -> sql.Composed:
    return sql.SQL(
        "CREATE ROLE {} WITH LOGIN PASSWORD %s "
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
    ).format(
        sql.Identifier(credential.username),
    )


def _ensure_role(cursor, credential: DatabaseRoleCredential) -> None:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (credential.username,))
    role_exists = cursor.fetchone() is not None
    if role_exists:
        # RDS master users have CREATEROLE but are not PostgreSQL superusers.
        # Repeating NOSUPERUSER on ALTER ROLE therefore fails with 42501 even
        # when the role is already safely non-superuser. Rotate only the LOGIN
        # password here; the complete attribute contract is verified below.
        statement = sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(
            sql.Identifier(credential.username)
        )
    else:
        statement = _create_role_statement(credential)
    cursor.execute(statement, (credential.password,))


def resolver_owner_role_name(migrator_role: str) -> str:
    """Derive a collision-resistant cluster role within PostgreSQL's 63-byte limit."""

    digest = md5(migrator_role.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{migrator_role[:39]}_resolver_owner_{digest}"


def _ensure_resolver_owner_role(cursor, *, migrator_role: str) -> str:
    owner_role = resolver_owner_role_name(migrator_role)
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (owner_role,))
    if cursor.fetchone() is None:
        cursor.execute(
            sql.SQL(
                "CREATE ROLE {} WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(owner_role))
        )
    # Existing roles are not ALTERed by the RDS master: repeating the
    # SUPERUSER attributes is forbidden to non-superusers. _verify_role_state
    # remains the fail-closed authority for every required attribute.
    cursor.execute(
        """
        SELECT granted_role.rolname, member_role.rolname
        FROM pg_auth_members AS membership
        JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
        JOIN pg_roles AS member_role ON member_role.oid = membership.member
        WHERE granted_role.rolname = %s OR member_role.rolname = %s
        """,
        (owner_role, owner_role),
    )
    for granted_role, member_role in cursor.fetchall():
        cursor.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(granted_role),
                sql.Identifier(member_role),
            )
        )
    cursor.execute(
        sql.SQL("GRANT {} TO {}").format(
            sql.Identifier(owner_role),
            sql.Identifier(migrator_role),
        )
    )
    return owner_role


def _ensure_required_migration_extensions(cursor) -> None:
    """Install trusted extensions that an unprivileged migrator cannot create.

    These extensions are prerequisites of unconditional historical migrations.
    Keeping their installation in the one-shot master bootstrap lets Alembic
    remain authenticated as a role with no elevated PostgreSQL attributes.
    """

    for extension_name in _REQUIRED_MIGRATION_EXTENSIONS:
        cursor.execute(
            sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(
                sql.Identifier(extension_name)
            )
        )


def _assert_no_foreign_application_objects(
    cursor,
    migrator_role: str,
    *,
    resolver_owner_role: str,
) -> None:
    """Reject silent adoption of a schema previously owned by another role."""

    cursor.execute(
        f"""
        WITH application_objects AS (
            SELECT 'relation' AS object_kind,
                   relation.relname AS object_name,
                   pg_get_userbyid(relation.relowner) AS owner_name,
                   relation.oid AS object_oid
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_class'::regclass
                    AND dependency.objid = relation.oid
                    AND dependency.deptype = 'e'
              )
            UNION ALL
            SELECT 'function',
                   routine.proname,
                   pg_get_userbyid(routine.proowner),
                   routine.oid
            FROM pg_proc routine
            JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
            WHERE namespace.nspname = 'public'
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_proc'::regclass
                    AND dependency.objid = routine.oid
                    AND dependency.deptype = 'e'
              )
            UNION ALL
            SELECT 'type',
                   type_entry.typname,
                   pg_get_userbyid(type_entry.typowner),
                   type_entry.oid
            FROM pg_type type_entry
            JOIN pg_namespace namespace ON namespace.oid = type_entry.typnamespace
            WHERE namespace.nspname = 'public'
              AND type_entry.typtype IN ('d', 'e')
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_depend dependency
                  WHERE dependency.classid = 'pg_type'::regclass
                    AND dependency.objid = type_entry.oid
                    AND dependency.deptype = 'e'
              )
        )
        SELECT object_kind, object_name, owner_name
        FROM application_objects
        WHERE owner_name <> %s
          AND NOT (
              object_kind = 'function'
              AND object_oid = ANY(
                  array_remove(ARRAY[
                      {_RESOLVER_REGPROCEDURES_SQL}
                  ]::oid[], NULL::oid)
              )
              AND owner_name = %s
          )
        ORDER BY object_kind, object_name
        LIMIT 10
        """,
        (migrator_role, resolver_owner_role),
    )
    foreign_objects = cursor.fetchall()
    if foreign_objects:
        labels = ", ".join(
            f"{kind}:{name} (owner {owner})" for kind, name, owner in foreign_objects
        )
        raise DatabaseRoleBootstrapError(
            "The public schema contains objects not owned by the configured migrator: " + labels
        )


def _reconcile_roles_as_master(config: DatabaseRoleBootstrapConfig) -> None:
    with _connect(config, config.master) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_BOOTSTRAP_LOCK_KEY,))
            cursor.execute("SELECT current_user")
            current_user = cursor.fetchone()[0]
            if current_user != config.master.username:
                raise DatabaseRoleBootstrapError(
                    "The bootstrap connection did not authenticate as the configured master."
                )

            _ensure_role(cursor, config.migrator)
            _ensure_role(cursor, config.runtime)
            resolver_owner_role = _ensure_resolver_owner_role(
                cursor,
                migrator_role=config.migrator.username,
            )
            _ensure_required_migration_extensions(cursor)
            _assert_no_foreign_application_objects(
                cursor,
                config.migrator.username,
                resolver_owner_role=resolver_owner_role,
            )

            cursor.execute(
                sql.SQL("ALTER ROLE {} SET row_security TO on").format(
                    sql.Identifier(config.migrator.username)
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET row_security TO on").format(
                    sql.Identifier(config.runtime.username)
                )
            )
            cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(config.database),
                    sql.Identifier(config.migrator.username),
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(config.database),
                    sql.Identifier(config.runtime.username),
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(config.migrator.username)
                )
            )
            cursor.execute(
                sql.SQL(
                    "GRANT CREATE ON SCHEMA public TO {} WITH GRANT OPTION"
                ).format(sql.Identifier(config.migrator.username))
            )
            cursor.execute(
                f"""
                SELECT COUNT(*) FILTER (
                           WHERE pg_get_userbyid(procedure.proowner) = %s
                       ),
                       COUNT(procedure.oid)
                FROM unnest(ARRAY[
                    {_RESOLVER_REGPROCEDURES_SQL}
                ]::oid[]) AS resolver(function_oid)
                LEFT JOIN pg_proc AS procedure ON procedure.oid = resolver.function_oid
                """,
                (resolver_owner_role,),
            )
            resolver_owned_count, resolver_count = cursor.fetchone()
            if resolver_count not in (0, len(_RESOLVER_FUNCTION_NAMES)):
                raise DatabaseRoleBootstrapError(
                    "Tenant resolver ownership is only partially installed."
                )
            # Clear any stale grant regardless of its historical grantor. The
            # migrator session below will add a revocable temporary grant only
            # when the resolver functions do not exist yet.
            cursor.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(
                    sql.Identifier(resolver_owner_role)
                )
            )
            if resolver_count == len(_RESOLVER_FUNCTION_NAMES):
                if resolver_owned_count != len(_RESOLVER_FUNCTION_NAMES):
                    raise DatabaseRoleBootstrapError(
                        "Tenant resolver ownership does not match the dedicated role."
                    )
            else:
                # Temporary and narrowly bounded: PostgreSQL requires the new
                # owner to have CREATE on the containing schema during ALTER
                # FUNCTION OWNER. The migrator grants it in its own session so
                # PostgreSQL records the migrator as grantor and the forward
                # migration can revoke that exact grant atomically.
                cursor.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                        sql.Identifier(resolver_owner_role)
                    )
                )
            cursor.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(
                    sql.Identifier(config.runtime.username)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(config.runtime.username)
                )
            )


def _install_migrator_default_privileges(config: DatabaseRoleBootstrapConfig) -> None:
    """Set defaults as their owning role; no role membership is granted to master."""

    with _connect(config, config.migrator) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            resolver_owner_role = resolver_owner_role_name(config.migrator.username)
            cursor.execute(
                f"""
                SELECT COUNT(procedure.oid)
                FROM unnest(ARRAY[
                    {_RESOLVER_REGPROCEDURES_SQL}
                ]::oid[]) AS resolver(function_oid)
                LEFT JOIN pg_proc AS procedure ON procedure.oid = resolver.function_oid
                """
            )
            resolver_count = cursor.fetchone()[0]
            if resolver_count == 0:
                cursor.execute(
                    sql.SQL(
                        "GRANT CREATE ON SCHEMA public TO {} GRANTED BY {}"
                    ).format(
                        sql.Identifier(resolver_owner_role),
                        sql.Identifier(config.migrator.username),
                    )
                )
            elif resolver_count != len(_RESOLVER_FUNCTION_NAMES):
                raise DatabaseRoleBootstrapError(
                    "Tenant resolver ownership is only partially installed."
                )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                ).format(sql.Identifier(config.runtime.username))
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT USAGE, SELECT ON SEQUENCES TO {}"
                ).format(sql.Identifier(config.runtime.username))
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
                ).format(sql.Identifier(config.runtime.username))
            )
            cursor.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                    sql.Identifier(config.runtime.username)
                )
            )
            _reconcile_existing_runtime_table_privileges(
                cursor,
                runtime_role=config.runtime.username,
            )


def _reconcile_existing_runtime_table_privileges(cursor, *, runtime_role: str) -> None:
    """Match current RLS/immutability contracts after a repeat bootstrap."""

    cursor.execute(
        """
        WITH protected_tables AS (
            SELECT namespace.nspname AS schema_name,
                   relation.relname AS table_name,
                   EXISTS (
                       SELECT 1
                       FROM pg_trigger immutable_trigger
                       WHERE immutable_trigger.tgrelid = relation.oid
                         AND NOT immutable_trigger.tgisinternal
                         AND immutable_trigger.tgname LIKE '%%immutable%%'
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
        SELECT schema_name, table_name, privilege
        FROM forbidden_privileges
        ORDER BY schema_name, table_name, privilege
        """
    )
    forbidden_by_table: dict[tuple[str, str], set[str]] = {}
    for schema_name, table_name, privilege in cursor.fetchall():
        forbidden_by_table.setdefault((schema_name, table_name), set()).add(privilege)

    for (schema_name, table_name), privileges in forbidden_by_table.items():
        cursor.execute(
            sql.SQL("REVOKE {} ON TABLE {}.{} FROM {}").format(
                sql.SQL(", ").join(sql.SQL(item) for item in sorted(privileges)),
                sql.Identifier(schema_name),
                sql.Identifier(table_name),
                sql.Identifier(runtime_role),
            )
        )

    for table_name, permitted in _GLOBAL_RUNTIME_PRIVILEGE_EXCEPTIONS.items():
        cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
        if cursor.fetchone()[0] is None:
            continue
        forbidden = sorted({"SELECT", "INSERT", "UPDATE", "DELETE"} - permitted)
        if forbidden:
            cursor.execute(
                sql.SQL("REVOKE {} ON TABLE public.{} FROM {}").format(
                    sql.SQL(", ").join(sql.SQL(item) for item in forbidden),
                    sql.Identifier(table_name),
                    sql.Identifier(runtime_role),
                )
            )


def _discover_runtime_role_from_resolver_acl(cursor, *, migrator_role: str) -> str:
    """Return the one safe LOGIN role that can execute every tenant resolver."""

    cursor.execute(
        f"""
        SELECT grantee.rolname
        FROM pg_proc AS procedure
        CROSS JOIN LATERAL aclexplode(
            COALESCE(procedure.proacl, acldefault('f', procedure.proowner))
        ) AS privilege
        JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
        WHERE procedure.oid = ANY(ARRAY[
            {_RESOLVER_REGPROCEDURES_SQL}
        ]::oid[])
          AND privilege.privilege_type = 'EXECUTE'
          AND grantee.rolname <> %s
          AND grantee.rolcanlogin
          AND NOT grantee.rolsuper
          AND NOT grantee.rolcreatedb
          AND NOT grantee.rolcreaterole
          AND NOT grantee.rolinherit
          AND NOT grantee.rolreplication
          AND NOT grantee.rolbypassrls
          AND has_schema_privilege(grantee.rolname, 'public', 'USAGE')
          AND NOT has_schema_privilege(grantee.rolname, 'public', 'CREATE')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_roles AS privileged_role
              WHERE (privileged_role.rolsuper OR privileged_role.rolbypassrls)
                AND pg_has_role(grantee.oid, privileged_role.oid, 'MEMBER')
          )
        GROUP BY grantee.rolname
        HAVING COUNT(DISTINCT procedure.oid) = %s
        ORDER BY grantee.rolname
        """,
        (migrator_role, len(_RESOLVER_FUNCTION_NAMES)),
    )
    candidates = [row[0] for row in cursor.fetchall()]
    if len(candidates) != 1:
        raise DatabaseRoleBootstrapError(
            "Could not identify exactly one safe runtime role from resolver ACLs."
        )
    return str(candidates[0])


def _normalize_catalog_text(value: str | None) -> str:
    return " ".join((value or "").split()).replace("::text", "").strip("() ")


def _verify_resolver_owner_contract(
    cursor,
    *,
    migrator_role: str,
    runtime_role: str,
) -> None:
    """Verify the complete f2 owner, definition, ACL, policy and FORCE RLS state."""

    from app.db.rls import (
        RESOLVER_OWNER_POLICY_NAME,
        TENANT_RESOLVER_FUNCTION_BODIES,
        TENANT_RESOLVER_FUNCTIONS,
        TENANT_RESOLVER_STRICTNESS,
    )

    resolver_owner = resolver_owner_role_name(migrator_role)
    cursor.execute(
        f"""
        SELECT procedure.proname,
               owner.rolname,
               owner.rolcanlogin,
               owner.rolsuper,
               owner.rolcreatedb,
               owner.rolcreaterole,
               owner.rolinherit,
               owner.rolreplication,
               owner.rolbypassrls,
               pg_has_role(%s, owner.oid, 'MEMBER'),
               pg_has_role(%s, owner.oid, 'MEMBER'),
               has_schema_privilege(owner.rolname, 'public', 'USAGE'),
               has_schema_privilege(owner.rolname, 'public', 'CREATE'),
               procedure.prosecdef,
               procedure.proconfig,
               procedure.prosrc,
               language.lanname,
               procedure.provolatile,
               procedure.proisstrict,
               ARRAY(
                   SELECT acl_role
                   FROM (
                       SELECT DISTINCT COALESCE(grantee.rolname, 'public') AS acl_role
                       FROM aclexplode(
                           COALESCE(
                               procedure.proacl,
                               acldefault('f', procedure.proowner)
                           )
                       ) AS privilege
                       LEFT JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
                       WHERE privilege.privilege_type = 'EXECUTE'
                   ) AS execute_roles
                   ORDER BY acl_role
               )
        FROM pg_proc AS procedure
        JOIN pg_roles AS owner ON owner.oid = procedure.proowner
        JOIN pg_language AS language ON language.oid = procedure.prolang
        WHERE procedure.oid = ANY(ARRAY[
            {_RESOLVER_REGPROCEDURES_SQL}
        ]::oid[])
        ORDER BY procedure.proname
        """,
        (migrator_role, runtime_role),
    )
    rows = {row[0]: tuple(row[1:]) for row in cursor.fetchall()}
    if set(rows) != set(TENANT_RESOLVER_FUNCTIONS):
        raise DatabaseRoleBootstrapError(
            "The strict resolver-owner contract does not contain every function."
        )
    expected_acl = {resolver_owner, runtime_role}
    for function_name, state in rows.items():
        (
            owner_name,
            owner_login,
            owner_superuser,
            owner_createdb,
            owner_createrole,
            owner_inherit,
            owner_replication,
            owner_bypassrls,
            migrator_is_member,
            runtime_is_member,
            owner_usage,
            owner_create,
            security_definer,
            settings,
            body,
            language_name,
            volatility,
            strict,
            execute_roles,
        ) = state
        if (
            owner_name != resolver_owner
            or owner_login
            or owner_superuser
            or owner_createdb
            or owner_createrole
            or owner_inherit
            or owner_replication
            or owner_bypassrls
            or not migrator_is_member
            or runtime_is_member
            or not owner_usage
            or owner_create
            or not security_definer
            or set(settings or ()) != {"search_path=pg_catalog, pg_temp"}
            or _normalize_catalog_text(body)
            != _normalize_catalog_text(TENANT_RESOLVER_FUNCTION_BODIES[function_name])
            or language_name != "sql"
            or volatility != "s"
            or strict != TENANT_RESOLVER_STRICTNESS[function_name]
            or set(execute_roles or ()) != expected_acl
        ):
            raise DatabaseRoleBootstrapError(
                f"Resolver-owner definition or ACL drifted for {function_name}."
            )

    cursor.execute(
        """
        SELECT relation.relname,
               relation.relrowsecurity,
               relation.relforcerowsecurity,
               policy.policyname,
               policy.permissive,
               policy.cmd,
               policy.roles,
               policy.qual,
               policy.with_check
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_policies AS policy
          ON policy.schemaname = namespace.nspname
         AND policy.tablename = relation.relname
         AND policy.policyname = %s
        WHERE namespace.nspname = 'public'
          AND relation.relname IN (
              'matters',
              'whatsapp_connections',
              'signup_verifications',
              'workspace_invitations',
              'integration_connections'
          )
        ORDER BY relation.relname
        """,
        (RESOLVER_OWNER_POLICY_NAME,),
    )
    policies = cursor.fetchall()
    if len(policies) != 5 or any(
        not row[1]
        or not row[2]
        or row[3] != RESOLVER_OWNER_POLICY_NAME
        or row[4] != "PERMISSIVE"
        or row[5] != "SELECT"
        or set(row[6] or ()) != {resolver_owner}
        or _normalize_catalog_text(row[7]) != "true"
        or row[8] is not None
        for row in policies
    ):
        raise DatabaseRoleBootstrapError(
            "Resolver-owner SELECT policies or FORCE RLS state drifted."
        )

    cursor.execute(
        """
        SELECT relation.relname,
               has_table_privilege(%s, relation.oid, 'SELECT'),
               has_table_privilege(%s, relation.oid, 'INSERT'),
               has_table_privilege(%s, relation.oid, 'UPDATE'),
               has_table_privilege(%s, relation.oid, 'DELETE'),
               has_table_privilege(%s, relation.oid, 'TRUNCATE'),
               has_table_privilege(%s, relation.oid, 'REFERENCES'),
               has_table_privilege(%s, relation.oid, 'TRIGGER')
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname IN (
              'matters',
              'whatsapp_connections',
              'signup_verifications',
              'workspace_invitations',
              'identity_outbox_messages',
              'integration_connections'
          )
        ORDER BY relation.relname
        """,
        (resolver_owner,) * 7,
    )
    owner_privileges = cursor.fetchall()
    if len(owner_privileges) != 6 or any(
        tuple(row[1:]) != (True, False, False, False, False, False, False)
        for row in owner_privileges
    ):
        raise DatabaseRoleBootstrapError(
            "Resolver owner has missing or excessive lookup-table privileges."
        )


def _verify_exact_runtime_dml(cursor, *, runtime_role: str) -> None:
    """Prove each RLS table has exactly the DML its policies can authorize."""

    cursor.execute(
        """
        SELECT relation.relname,
               EXISTS (
                   SELECT 1
                   FROM pg_trigger AS immutable_trigger
                   WHERE immutable_trigger.tgrelid = relation.oid
                     AND NOT immutable_trigger.tgisinternal
                              AND immutable_trigger.tgname LIKE '%%immutable%%'
               ) AS immutable,
               has_table_privilege(%s, relation.oid, 'SELECT'),
               has_table_privilege(%s, relation.oid, 'INSERT'),
               has_table_privilege(%s, relation.oid, 'UPDATE'),
               has_table_privilege(%s, relation.oid, 'DELETE'),
               ARRAY(
                   SELECT DISTINCT policy.cmd
                   FROM pg_policies AS policy
                   WHERE policy.schemaname = namespace.nspname
                     AND policy.tablename = relation.relname
                   ORDER BY policy.cmd
               )
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
          AND relation.relrowsecurity
        ORDER BY relation.relname
        """,
        (runtime_role,) * 4,
    )
    rows = cursor.fetchall()
    if not rows:
        raise DatabaseRoleBootstrapError(
            "No PostgreSQL RLS tables were available for runtime grant verification."
        )
    drift: list[str] = []
    for table_name, immutable, select, insert, update, delete, commands in rows:
        expected = {
            privilege
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
            if "ALL" in set(commands or ()) or privilege in set(commands or ())
        }
        if immutable:
            expected -= {"UPDATE", "DELETE"}
        if table_name in _GLOBAL_RUNTIME_PRIVILEGE_EXCEPTIONS:
            expected = set(_GLOBAL_RUNTIME_PRIVILEGE_EXCEPTIONS[table_name])
        actual = {
            privilege
            for privilege, granted in (
                ("SELECT", select),
                ("INSERT", insert),
                ("UPDATE", update),
                ("DELETE", delete),
            )
            if granted
        }
        if actual != expected:
            drift.append(f"{table_name}: expected {sorted(expected)}, got {sorted(actual)}")
    if drift:
        raise DatabaseRoleBootstrapError(
            "Runtime RLS DML contract drifted: " + "; ".join(drift[:10])
        )


def reconcile_runtime_privileges_as_migrator(
    database_url: str,
    expected_migrator_role: str,
) -> str:
    """Contract runtime grants after ``upgrade head`` without a master credential.

    The runtime username is derived fail-closed from the four hardened resolver
    ACLs.  This lets the one-shot migration task remove excessive default DML
    after creating tables, even though the ephemeral master/bootstrap plane has
    already been destroyed.
    """

    assert_migration_role(database_url, expected_migrator_role)
    try:
        parsed_url = make_url(database_url)
        connection_options = {
            "host": parsed_url.host,
            "port": parsed_url.port or 5432,
            "dbname": parsed_url.database,
            "user": parsed_url.username,
            "password": parsed_url.password,
            "connect_timeout": 10,
            "application_name": "meu-executivo-runtime-grant-contract",
        }
        sslmode = parsed_url.query.get("sslmode")
        if isinstance(sslmode, str):
            connection_options["sslmode"] = sslmode
        with psycopg2.connect(**connection_options) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SET LOCAL statement_timeout = '30s'")
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_BOOTSTRAP_LOCK_KEY,))
                cursor.execute("SELECT current_user")
                if cursor.fetchone()[0] != expected_migrator_role:
                    raise DatabaseRoleBootstrapError(
                        "Runtime grant reconciliation did not authenticate as the migrator."
                    )
                cursor.execute("SELECT version_num FROM alembic_version")
                revisions = tuple(row[0] for row in cursor.fetchall())
                if revisions != (_GREENFIELD_HEAD_REVISION,):
                    raise DatabaseRoleBootstrapError(
                        "Post-head runtime grant reconciliation requires the exact "
                        f"greenfield revision {_GREENFIELD_HEAD_REVISION}."
                    )
                runtime_role = _discover_runtime_role_from_resolver_acl(
                    cursor,
                    migrator_role=expected_migrator_role,
                )
                _verify_resolver_owner_contract(
                    cursor,
                    migrator_role=expected_migrator_role,
                    runtime_role=runtime_role,
                )
                _reconcile_existing_runtime_table_privileges(
                    cursor,
                    runtime_role=runtime_role,
                )
                _verify_exact_runtime_dml(cursor, runtime_role=runtime_role)
                return runtime_role
    except DatabaseRoleBootstrapError:
        raise
    except (psycopg2.Error, ValueError) as exc:
        code = exc.pgcode if isinstance(exc, psycopg2.Error) else None
        raise DatabaseRoleBootstrapError(
            "Runtime grant reconciliation failed "
            f"(database error code: {code or 'unavailable'})."
        ) from None


def _verify_role_state(config: DatabaseRoleBootstrapConfig) -> None:
    resolver_owner = resolver_owner_role_name(config.migrator.username)
    with _connect(config, config.master) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rolname,
                       rolcanlogin,
                       rolsuper,
                       rolcreatedb,
                       rolcreaterole,
                       rolinherit,
                       rolreplication,
                       rolbypassrls
                FROM pg_roles
                WHERE rolname IN (%s, %s, %s)
                ORDER BY rolname
                """,
                (config.migrator.username, config.runtime.username, resolver_owner),
            )
            rows = cursor.fetchall()
            if len(rows) != 3:
                raise DatabaseRoleBootstrapError("Database role reconciliation is incomplete.")
            for row in rows:
                (
                    role_name,
                    can_login,
                    superuser,
                    createdb,
                    createrole,
                    inherit,
                    replication,
                    bypassrls,
                ) = row
                if any((superuser, createdb, createrole, inherit, replication, bypassrls)):
                    raise DatabaseRoleBootstrapError(
                        "A reconciled database role retained unsafe attributes."
                    )
                if can_login != (role_name != resolver_owner):
                    raise DatabaseRoleBootstrapError(
                        "The dedicated resolver owner must be the only NOLOGIN role."
                    )
            cursor.execute(
                f"""
                SELECT pg_has_role(%s, %s, 'MEMBER'),
                       pg_has_role(%s, %s, 'MEMBER'),
                       has_schema_privilege(%s, 'public', 'CREATE'),
                       COUNT(*) FILTER (
                           WHERE pg_get_userbyid(procedure.proowner) = %s
                       ),
                       COUNT(procedure.oid),
                       (
                           SELECT COUNT(*)
                           FROM pg_auth_members AS membership
                           WHERE membership.roleid = (
                               SELECT oid FROM pg_roles WHERE rolname = %s
                           )
                       ),
                       (
                           SELECT COUNT(*)
                           FROM pg_auth_members AS membership
                           WHERE membership.member = (
                               SELECT oid FROM pg_roles WHERE rolname = %s
                           )
                       )
                FROM unnest(ARRAY[
                    {_RESOLVER_REGPROCEDURES_SQL}
                ]::oid[]) AS resolver(function_oid)
                LEFT JOIN pg_proc AS procedure ON procedure.oid = resolver.function_oid
                """,
                (
                    config.migrator.username,
                    resolver_owner,
                    config.runtime.username,
                    resolver_owner,
                    resolver_owner,
                    resolver_owner,
                    resolver_owner,
                    resolver_owner,
                ),
            )
            (
                migrator_is_member,
                runtime_is_member,
                owner_has_schema_create,
                owned_resolver_count,
                resolver_count,
                owner_member_count,
                owner_parent_count,
            ) = cursor.fetchone()
            if (
                not migrator_is_member
                or runtime_is_member
                or owner_member_count != 1
                or owner_parent_count != 0
            ):
                raise DatabaseRoleBootstrapError(
                    "Resolver owner membership does not match the migration/runtime boundary."
                )
            expected_count = len(_RESOLVER_FUNCTION_NAMES)
            if resolver_count not in (0, expected_count):
                raise DatabaseRoleBootstrapError(
                    "Tenant resolver ownership is only partially installed."
                )
            if resolver_count == 0 and not owner_has_schema_create:
                raise DatabaseRoleBootstrapError(
                    "Resolver owner lacks the temporary pre-migration schema privilege."
                )
            if resolver_count == expected_count and (
                owned_resolver_count != expected_count or owner_has_schema_create
            ):
                raise DatabaseRoleBootstrapError(
                    "Resolver owner retained unsafe privileges after migration."
                )
            cursor.execute(
                """
                SELECT has_schema_privilege(%s, 'public', 'CREATE'),
                       EXISTS (
                           SELECT 1
                           FROM pg_namespace AS namespace
                           CROSS JOIN LATERAL aclexplode(namespace.nspacl) AS privilege
                           JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
                           WHERE namespace.nspname = 'public'
                             AND grantee.rolname = %s
                             AND privilege.privilege_type = 'CREATE'
                             AND privilege.is_grantable
                       ),
                       has_schema_privilege(%s, 'public', 'CREATE')
                """,
                (
                    config.migrator.username,
                    config.migrator.username,
                    config.runtime.username,
                ),
            )
            migrator_create, migrator_grantable, runtime_create = cursor.fetchone()
            if not migrator_create or not migrator_grantable or runtime_create:
                raise DatabaseRoleBootstrapError(
                    "Schema privilege delegation does not match the migration boundary."
                )


def bootstrap_database_roles(config: DatabaseRoleBootstrapConfig) -> None:
    """Create or rotate the migrator/runtime roles and least-privilege grants."""

    try:
        _reconcile_roles_as_master(config)
        _install_migrator_default_privileges(config)
        _verify_role_state(config)
    except DatabaseRoleBootstrapError:
        raise
    except psycopg2.Error as exc:
        code = exc.pgcode or "unavailable"
        raise DatabaseRoleBootstrapError(
            f"PostgreSQL role bootstrap failed (database error code: {code})."
        ) from None


def assert_migration_role(database_url: str, expected_role: str) -> None:
    """Prove that the migration URL resolves to the intended unprivileged role."""

    if not _ROLE_NAME_RE.fullmatch(expected_role):
        raise DatabaseRoleBootstrapError("Invalid expected migration role name.")
    try:
        parsed_url = make_url(database_url)
        if not parsed_url.drivername.startswith("postgresql"):
            raise DatabaseRoleBootstrapError("Migration preflight requires a PostgreSQL URL.")
        if not all(
            (
                parsed_url.username,
                parsed_url.password,
                parsed_url.host,
                parsed_url.database,
            )
        ):
            raise DatabaseRoleBootstrapError("The migration database URL is incomplete.")
        connection_options = {
            "host": parsed_url.host,
            "port": parsed_url.port or 5432,
            "dbname": parsed_url.database,
            "user": parsed_url.username,
            "password": parsed_url.password,
            "connect_timeout": 10,
            "application_name": "meu-executivo-migration-preflight",
        }
        sslmode = parsed_url.query.get("sslmode")
        if isinstance(sslmode, str):
            connection_options["sslmode"] = sslmode
        with psycopg2.connect(**connection_options) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_user,
                           rolsuper,
                           rolcreatedb,
                           rolcreaterole,
                           rolinherit,
                           rolreplication,
                           rolbypassrls,
                           has_schema_privilege(current_user, 'public', 'USAGE'),
                           has_schema_privilege(current_user, 'public', 'CREATE')
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
                row = cursor.fetchone()
    except DatabaseRoleBootstrapError:
        raise
    except (psycopg2.Error, ValueError) as exc:
        code = exc.pgcode if isinstance(exc, psycopg2.Error) else None
        raise DatabaseRoleBootstrapError(
            f"Migration role preflight failed (database error code: {code or 'unavailable'})."
        ) from None

    if row is None or row[0] != expected_role:
        raise DatabaseRoleBootstrapError(
            "The migration URL did not authenticate as the expected role."
        )
    unsafe_flags = row[1:7]
    has_usage, has_create = row[7:9]
    if any(unsafe_flags) or not has_usage or not has_create:
        raise DatabaseRoleBootstrapError(
            "The migration role does not satisfy the least-privilege contract."
        )


def run_migrations() -> None:
    database_url = _required(os.environ, "DATABASE_URL_SYNC")
    expected_role = _required(os.environ, "KLM_EXPECTED_MIGRATION_ROLE")
    assert_migration_role(database_url, expected_role)
    repository_root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=repository_root,
        check=True,
    )
    reconcile_runtime_privileges_as_migrator(database_url, expected_role)


def main(arguments: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    if args == ["bootstrap"]:
        bootstrap_database_roles(load_bootstrap_config())
        print("Database roles reconciled successfully; no credentials were logged.")
        return 0
    if args == ["migrate"]:
        run_migrations()
        print("Database migration and post-head runtime grant contract completed.")
        return 0
    print("Usage: python -m app.db.aws_database_roles bootstrap|migrate", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatabaseRoleBootstrapError as exc:
        print(f"Database role operation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
