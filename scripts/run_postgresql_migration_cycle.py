#!/usr/bin/env python3
"""Bootstrap isolated PostgreSQL roles and exercise Alembic as the migrator.

The cluster/bootstrap credential is used only by ``bootstrap_database_roles``.
Every Alembic command receives a URL for the separately authenticated,
least-privilege migration role.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import replace

import psycopg2
from sqlalchemy.engine import URL

from app.db.aws_database_roles import (
    DatabaseRoleBootstrapConfig,
    DatabaseRoleBootstrapError,
    assert_migration_role,
    bootstrap_database_roles,
    load_bootstrap_config,
    reconcile_runtime_privileges_as_migrator,
    resolver_owner_role_name,
)
from app.db.rls import (
    TENANT_RESOLVER_FUNCTION_BODIES,
    TENANT_RESOLVER_FUNCTIONS,
    TENANT_RESOLVER_STRICTNESS,
)

_LOCAL_SSL_MODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)
CURRENT_HEAD_REVISION = "c9d0e1f2a3b4"
# Resolvers que existem na revisão anterior ao template da Meta (e1b3c5d7e920):
# as quatro originais. Os que vieram depois (template a3d5e7f9b142, Asaas
# b8d2f4a6c9e1) são dropados pelo downgrade antes desse ponto.
PRE_TEMPLATE_RESOLVER_FUNCTIONS = tuple(
    function_name
    for function_name in TENANT_RESOLVER_FUNCTIONS
    if function_name
    not in {
        "klm_resolve_whatsapp_template_webhook_workspace",
        "klm_resolve_asaas_webhook_workspace",
    }
)


def _load_config() -> DatabaseRoleBootstrapConfig:
    config = load_bootstrap_config()
    sslmode = os.getenv("KLM_DB_BOOTSTRAP_SSLMODE", config.sslmode)
    if sslmode not in _LOCAL_SSL_MODES:
        raise DatabaseRoleBootstrapError("Invalid PostgreSQL bootstrap sslmode.")
    return replace(config, sslmode=sslmode)


def _migration_url(config: DatabaseRoleBootstrapConfig) -> str:
    return URL.create(
        drivername="postgresql+psycopg2",
        username=config.migrator.username,
        password=config.migrator.password,
        host=config.host,
        port=config.port,
        database=config.database,
        query={"sslmode": config.sslmode},
    ).render_as_string(hide_password=False)


def _assert_exact_migrator_state(
    config: DatabaseRoleBootstrapConfig,
    migration_url: str,
    *,
    resolver_owner_schema_create: bool,
) -> None:
    """Adversarially prove identity, flags and schema privileges on this URL."""

    assert_migration_role(migration_url, config.migrator.username)
    with psycopg2.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.migrator.username,
        password=config.migrator.password,
        sslmode=config.sslmode,
        connect_timeout=10,
        application_name="meu-executivo-migration-cycle-proof",
    ) as connection:
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
            state = cursor.fetchone()
            owner_role = resolver_owner_role_name(config.migrator.username)
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
                (owner_role,),
            )
            owner_state = cursor.fetchone()

    expected = (
        config.migrator.username,
        config.migrator.username,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    )
    if state != expected:
        raise DatabaseRoleBootstrapError(
            "The authenticated migration role differs from the exact least-privilege contract."
        )
    expected_owner = (
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
        resolver_owner_schema_create,
    )
    if owner_state != expected_owner:
        raise DatabaseRoleBootstrapError(
            "The dedicated NOLOGIN resolver owner differs from the exact privilege contract."
        )


def _run_alembic(alembic_bin: str, migration_url: str, *arguments: str) -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": migration_url,
        "DATABASE_URL_SYNC": migration_url,
    }
    subprocess.run(
        [alembic_bin, *arguments],
        env=environment,
        check=True,
    )


def _normalize_body(value: str) -> str:
    return " ".join(value.split())


def _assert_e1_resolver_contract_after_downgrade(
    config: DatabaseRoleBootstrapConfig,
) -> None:
    """Prove f2's own schema downgrade restores the exact e1 contract.

    This is a disposable-database migration round-trip assertion.  It is not a
    claim that the pre-greenfield application binary is safe to reactivate.
    """

    with _connect_role(config, config.migrator) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT procedure.proname,
                       pg_get_userbyid(procedure.proowner),
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
                               LEFT JOIN pg_roles AS grantee
                                 ON grantee.oid = privilege.grantee
                               WHERE privilege.privilege_type = 'EXECUTE'
                           ) AS roles
                           ORDER BY acl_role
                       )
                FROM pg_proc AS procedure
                JOIN pg_language AS language ON language.oid = procedure.prolang
                WHERE procedure.oid IN (
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
                )
                """
            )
            rows = {row[0]: tuple(row[1:]) for row in cursor.fetchall()}

    if set(rows) != set(PRE_TEMPLATE_RESOLVER_FUNCTIONS):
        raise DatabaseRoleBootstrapError(
            "The previous revision is missing a tenant resolver after downgrade."
        )
    expected_acl = {config.migrator.username, config.runtime.username}
    for function_name, state in rows.items():
        owner, security_definer, settings, body, language, volatility, strict, acl = state
        if (
            owner != config.migrator.username
            or not security_definer
            or set(settings or ()) != {"search_path=pg_catalog, pg_temp"}
            or _normalize_body(body)
            != _normalize_body(TENANT_RESOLVER_FUNCTION_BODIES[function_name])
            or language != "sql"
            or volatility != "s"
            or strict != TENANT_RESOLVER_STRICTNESS[function_name]
            or set(acl or ()) != expected_acl
        ):
            raise DatabaseRoleBootstrapError(
                "The previous revision tenant resolver contract drifted during downgrade."
            )

    with _connect_role(config, config.runtime) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT public.klm_resolve_zapsign_webhook_workspace(NULL::uuid, NULL::text),
                       public.klm_resolve_whatsapp_webhook_workspace(NULL::text),
                       public.klm_resolve_signup_verification_workspace(
                           NULL::text,
                           NULL::uuid
                       ),
                       public.klm_resolve_workspace_invitation_workspace(
                           NULL::uuid,
                           NULL::text,
                           NULL::uuid
                       )
                """
            )
            if cursor.fetchone() != (None, None, None, None):
                raise DatabaseRoleBootstrapError(
                    "Runtime execution of the previous revision resolvers is unsafe."
                )


def _connect_role(
    config: DatabaseRoleBootstrapConfig,
    credential,
):
    return psycopg2.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=credential.username,
        password=credential.password,
        sslmode=config.sslmode,
        connect_timeout=10,
        application_name="meu-executivo-migration-cycle-contract",
    )


def _assert_alembic_revision(
    config: DatabaseRoleBootstrapConfig,
    *,
    expected_revision: str,
) -> None:
    with _connect_role(config, config.migrator) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revisions = tuple(row[0] for row in cursor.fetchall())
    if revisions != (expected_revision,):
        raise DatabaseRoleBootstrapError(
            "The PostgreSQL QA database is not at the single expected Alembic head."
        )


def _finalize_secure_head(
    config: DatabaseRoleBootstrapConfig,
    migration_url: str,
) -> None:
    """Reconcile runtime grants and prove the strict greenfield head contract."""

    runtime_role = reconcile_runtime_privileges_as_migrator(
        migration_url,
        config.migrator.username,
    )
    if runtime_role != config.runtime.username:
        raise DatabaseRoleBootstrapError(
            "The post-head grant contract resolved an unexpected runtime role."
        )
    _assert_exact_migrator_state(
        config,
        migration_url,
        resolver_owner_schema_create=False,
    )
    _assert_alembic_revision(config, expected_revision=CURRENT_HEAD_REVISION)


def run_cycle(*, alembic_bin: str) -> None:
    config = _load_config()
    migration_url = _migration_url(config)

    bootstrap_database_roles(config)
    _assert_exact_migrator_state(
        config,
        migration_url,
        resolver_owner_schema_create=True,
    )

    _run_alembic(alembic_bin, migration_url, "upgrade", "head")
    _assert_alembic_revision(config, expected_revision=CURRENT_HEAD_REVISION)
    _run_alembic(alembic_bin, migration_url, "downgrade", "e1b3c5d7e920")
    _assert_e1_resolver_contract_after_downgrade(config)
    _run_alembic(alembic_bin, migration_url, "upgrade", "head")
    _assert_alembic_revision(config, expected_revision=CURRENT_HEAD_REVISION)
    _run_alembic(alembic_bin, migration_url, "downgrade", "base")
    _run_alembic(alembic_bin, migration_url, "upgrade", "head")
    _run_alembic(alembic_bin, migration_url, "check")
    _finalize_secure_head(config, migration_url)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alembic-bin", required=True)
    options = parser.parse_args(arguments)
    try:
        run_cycle(alembic_bin=options.alembic_bin)
    except DatabaseRoleBootstrapError as exc:
        print(f"PostgreSQL migration-cycle gate failed: {exc}", file=sys.stderr)
        return 1
    print(
        "PostgreSQL base-to-head round-trip passed with the strict greenfield "
        "resolver-owner contract and separately authenticated least-privilege migrator."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
