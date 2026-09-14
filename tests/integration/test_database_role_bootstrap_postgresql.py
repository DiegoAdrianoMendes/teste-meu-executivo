from __future__ import annotations

import os
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy.engine import make_url

from app.db.aws_database_roles import (
    DatabaseRoleBootstrapConfig,
    DatabaseRoleBootstrapError,
    DatabaseRoleCredential,
    assert_migration_role,
    bootstrap_database_roles,
    reconcile_runtime_privileges_as_migrator,
    resolver_owner_role_name,
)


def _master_url():
    raw = os.getenv("KLM_TEST_POSTGRES_MASTER_URL", "")
    if not raw:
        pytest.fail("KLM_TEST_POSTGRES_MASTER_URL is required by the PostgreSQL QA wrapper.")
    return make_url(raw)


def _migrator_url():
    raw = os.getenv("KLM_TEST_POSTGRES_ADMIN_URL", "")
    if not raw:
        pytest.fail("KLM_TEST_POSTGRES_ADMIN_URL is required by the PostgreSQL QA wrapper.")
    return make_url(raw)


def _connect_kwargs(url, *, database: str | None = None) -> dict[str, object]:
    return {
        "host": url.host,
        "port": url.port or 5432,
        "dbname": database or url.database,
        "user": url.username,
        "password": url.password,
    }


def test_qa_migration_url_authenticates_as_exact_least_privilege_role() -> None:
    master = _master_url()
    migrator = _migrator_url()
    assert master.username != migrator.username
    resolver_owner = resolver_owner_role_name(migrator.username or "")

    with psycopg2.connect(**_connect_kwargs(migrator)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user,
                       session_user,
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
            assert cursor.fetchone() == (
                migrator.username,
                migrator.username,
                False,
                False,
                False,
                False,
                False,
                False,
                True,
                True,
            )
            cursor.execute(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('btree_gist', 'pg_trgm') ORDER BY extname"
            )
            assert cursor.fetchall() == [("btree_gist",), ("pg_trgm",)]
            cursor.execute(
                """
                SELECT rolcanlogin,
                       rolsuper,
                       rolcreatedb,
                       rolcreaterole,
                       rolinherit,
                       rolreplication,
                       rolbypassrls,
                       pg_has_role(current_user, oid, 'MEMBER'),
                       has_schema_privilege(rolname, 'public', 'USAGE'),
                       has_schema_privilege(rolname, 'public', 'CREATE')
                FROM pg_roles
                WHERE rolname = %s
                """,
                (resolver_owner,),
            )
            assert cursor.fetchone() == (
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                True,
                True,
                False,
            )
            cursor.execute(
                """
                SELECT grantee.rolname, grantor.rolname
                FROM pg_namespace AS namespace
                CROSS JOIN LATERAL aclexplode(namespace.nspacl) AS privilege
                JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
                JOIN pg_roles AS grantor ON grantor.oid = privilege.grantor
                WHERE namespace.nspname = 'public'
                  AND privilege.privilege_type = 'CREATE'
                  AND grantee.rolname = %s
                """,
                (resolver_owner,),
            )
            assert cursor.fetchall() == []
            cursor.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (
                           WHERE pg_get_userbyid(procedure.proowner) = %s
                       )
                FROM pg_proc AS procedure
                WHERE procedure.oid IN (
                    to_regprocedure(
                        'public.klm_resolve_zapsign_webhook_workspace(uuid,text)'
                    ),
                    to_regprocedure(
                        'public.klm_resolve_whatsapp_webhook_workspace(text)'
                    ),
                    to_regprocedure(
                        'public.klm_resolve_whatsapp_template_webhook_workspace(text)'
                    ),
                    to_regprocedure(
                        'public.klm_resolve_asaas_webhook_workspace(text)'
                    ),
                    to_regprocedure(
                        'public.klm_resolve_signup_verification_workspace(text,uuid)'
                    ),
                    to_regprocedure(
                        'public.klm_resolve_workspace_invitation_workspace(uuid,text,uuid)'
                    )
                )
                """,
                (resolver_owner,),
            )
            assert cursor.fetchone() == (6, 6)


def test_post_head_reconcile_rejects_public_resolver_execute() -> None:
    migrator = _migrator_url()
    resolver_owner = resolver_owner_role_name(migrator.username or "")
    signature = "public.klm_resolve_whatsapp_webhook_workspace(text)"

    with psycopg2.connect(**_connect_kwargs(migrator)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(resolver_owner)))
            cursor.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC")

    migration_url = os.environ["KLM_TEST_POSTGRES_ADMIN_URL"]
    try:
        with pytest.raises(DatabaseRoleBootstrapError, match="definition or ACL drifted"):
            reconcile_runtime_privileges_as_migrator(
                migration_url,
                migrator.username or "",
            )
    finally:
        with psycopg2.connect(**_connect_kwargs(migrator)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(resolver_owner))
                )
                cursor.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")

    assert (
        reconcile_runtime_privileges_as_migrator(
            migration_url,
            migrator.username or "",
        )
        == "klm_app"
    )


def test_fresh_database_role_bootstrap_is_idempotent_and_least_privilege() -> None:
    admin = _master_url()
    token = uuid4().hex[:10]
    database = f"klm_bootstrap_{token}"
    migrator_name = f"klm_migrator_{token}"
    runtime_name = f"klm_app_{token}"
    migrator_password = f"migrator-{uuid4().hex}"
    runtime_password = f"runtime-{uuid4().hex}"
    resolver_owner_name = resolver_owner_role_name(migrator_name)
    config = DatabaseRoleBootstrapConfig(
        host=admin.host or "127.0.0.1",
        port=admin.port or 5432,
        database=database,
        master=DatabaseRoleCredential(
            username=admin.username or "",
            password=admin.password or "",
        ),
        migrator=DatabaseRoleCredential(migrator_name, migrator_password),
        runtime=DatabaseRoleCredential(runtime_name, runtime_password),
        sslmode="disable",
    )

    administrative = psycopg2.connect(**_connect_kwargs(admin))
    administrative.autocommit = True
    try:
        with administrative.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

        bootstrap_database_roles(config)
        bootstrap_database_roles(config)

        with administrative.cursor() as cursor:
            cursor.execute(
                """
                SELECT rolcanlogin,
                       rolsuper,
                       rolcreatedb,
                       rolcreaterole,
                       rolinherit,
                       rolreplication,
                       rolbypassrls,
                       pg_has_role(%s, oid, 'MEMBER'),
                       pg_has_role(%s, oid, 'MEMBER')
                FROM pg_roles
                WHERE rolname = %s
                """,
                (migrator_name, runtime_name, resolver_owner_name),
            )
            assert cursor.fetchone() == (
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                True,
                False,
            )
        with psycopg2.connect(
            **_connect_kwargs(admin, database=database)
        ) as database_connection:
            with database_connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT grantee.rolname, grantor.rolname
                    FROM pg_namespace AS namespace
                    CROSS JOIN LATERAL aclexplode(namespace.nspacl) AS privilege
                    JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
                    JOIN pg_roles AS grantor ON grantor.oid = privilege.grantor
                    WHERE namespace.nspname = 'public'
                      AND privilege.privilege_type = 'CREATE'
                      AND grantee.rolname = %s
                    """,
                    (resolver_owner_name,),
                )
                assert cursor.fetchall() == [(resolver_owner_name, migrator_name)]

        migrator_url = (
            f"postgresql+psycopg2://{migrator_name}:{migrator_password}"
            f"@{config.host}:{config.port}/{database}"
        )
        assert_migration_role(migrator_url, migrator_name)

        with psycopg2.connect(
            host=config.host,
            port=config.port,
            dbname=database,
            user=migrator_name,
            password=migrator_password,
        ) as migrator_connection:
            with migrator_connection.cursor() as cursor:
                cursor.execute("CREATE TABLE bootstrap_contract (id bigint PRIMARY KEY)")
                cursor.execute("CREATE SEQUENCE bootstrap_contract_sequence")
                cursor.execute(
                    "CREATE TABLE bootstrap_protected "
                    "(id bigint PRIMARY KEY, payload text NOT NULL)"
                )
                cursor.execute("ALTER TABLE bootstrap_protected ENABLE ROW LEVEL SECURITY")
                cursor.execute("ALTER TABLE bootstrap_protected FORCE ROW LEVEL SECURITY")
                cursor.execute(
                    "CREATE POLICY bootstrap_protected_select ON bootstrap_protected "
                    "FOR SELECT USING (true)"
                )
                cursor.execute(
                    "CREATE POLICY bootstrap_protected_insert ON bootstrap_protected "
                    "FOR INSERT WITH CHECK (true)"
                )
                cursor.execute(
                    """
                    CREATE FUNCTION guard_bootstrap_protected_immutable()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $$
                    BEGIN
                        IF TG_OP IN ('UPDATE', 'DELETE') THEN
                            RAISE EXCEPTION 'bootstrap_protected is immutable';
                        END IF;
                        RETURN NEW;
                    END;
                    $$
                    """
                )
                cursor.execute(
                    "CREATE TRIGGER trg_bootstrap_protected_immutable "
                    "BEFORE UPDATE OR DELETE ON bootstrap_protected "
                    "FOR EACH ROW EXECUTE FUNCTION guard_bootstrap_protected_immutable()"
                )

        # A repeat run represents controlled credential rotation/repair after
        # migrations have created application objects.  It must preserve the
        # owner, refresh current grants, then re-apply RLS/immutability limits.
        bootstrap_database_roles(config)

        with psycopg2.connect(
            host=config.host,
            port=config.port,
            dbname=database,
            user=runtime_name,
            password=runtime_password,
        ) as runtime_connection:
            with runtime_connection.cursor() as cursor:
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
                           has_schema_privilege(current_user, 'public', 'CREATE'),
                           has_table_privilege(
                               current_user,
                               'bootstrap_contract',
                               'SELECT,INSERT,UPDATE,DELETE'
                           ),
                           has_sequence_privilege(
                               current_user,
                               'bootstrap_contract_sequence',
                               'USAGE,SELECT'
                           ),
                           has_table_privilege(
                               current_user,
                               'bootstrap_protected',
                               'SELECT'
                           ),
                           has_table_privilege(
                               current_user,
                               'bootstrap_protected',
                               'INSERT'
                           ),
                           has_table_privilege(
                               current_user,
                               'bootstrap_protected',
                               'UPDATE'
                           ),
                           has_table_privilege(
                               current_user,
                               'bootstrap_protected',
                               'DELETE'
                           )
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
                row = cursor.fetchone()
                assert row == (
                    runtime_name,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    True,
                    False,
                    True,
                    True,
                    True,
                    True,
                    False,
                    False,
                )
    finally:
        with administrative.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
            cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(runtime_name)))
            cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(migrator_name)))
            cursor.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(resolver_owner_name))
            )
        administrative.close()
