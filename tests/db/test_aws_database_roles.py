from __future__ import annotations

import pytest

from app.db.aws_database_roles import (
    DatabaseRoleBootstrapError,
    DatabaseRoleCredential,
    _ensure_required_migration_extensions,
    _ensure_resolver_owner_role,
    _ensure_role,
    assert_migration_role,
    load_bootstrap_config,
    resolver_owner_role_name,
)
from app.db.rls import TENANT_RESOLVER_FUNCTION_BODIES, _normalize_function_body


def _bootstrap_environment() -> dict[str, str]:
    return {
        "KLM_DB_BOOTSTRAP_HOST": "database.internal",
        "KLM_DB_BOOTSTRAP_PORT": "5432",
        "KLM_DB_BOOTSTRAP_DATABASE": "meu_executivo",
        "KLM_DB_BOOTSTRAP_MASTER_USERNAME": "platform_admin",
        "KLM_DB_BOOTSTRAP_MASTER_PASSWORD": "master-password-long",
        "KLM_DB_BOOTSTRAP_MIGRATOR_USERNAME": "klm_migrator",
        "KLM_DB_BOOTSTRAP_MIGRATOR_PASSWORD": "migrator-password-long",
        "KLM_DB_BOOTSTRAP_RUNTIME_USERNAME": "klm_app",
        "KLM_DB_BOOTSTRAP_RUNTIME_PASSWORD": "runtime-password-long",
    }


def test_bootstrap_config_requires_three_distinct_roles() -> None:
    environment = _bootstrap_environment()
    environment["KLM_DB_BOOTSTRAP_RUNTIME_USERNAME"] = "klm_migrator"

    with pytest.raises(DatabaseRoleBootstrapError, match="must be distinct"):
        load_bootstrap_config(environment)


def test_resolver_owner_name_is_stable_distinct_and_within_postgresql_limit() -> None:
    migrator = "m" * 63
    owner = resolver_owner_role_name(migrator)

    assert owner != migrator
    assert len(owner) == 63
    assert owner.endswith("_resolver_owner_9e5bd29f")


def test_role_password_is_bound_instead_of_embedded_in_statement() -> None:
    secret = "runtime-secret-never-in-statement"

    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        def execute(self, statement, parameters=None) -> None:
            self.calls.append((statement, parameters))

        def fetchone(self):
            return None

    cursor = Cursor()
    _ensure_role(
        cursor,
        DatabaseRoleCredential(username="klm_app", password=secret),
    )

    statement, parameters = cursor.calls[-1]
    assert secret not in repr(statement)
    assert parameters == (secret,)


def test_existing_role_rotation_does_not_repeat_superuser_attributes() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        def execute(self, statement, parameters=None) -> None:
            self.calls.append((statement, parameters))

        def fetchone(self):
            return (1,)

    cursor = Cursor()
    _ensure_role(
        cursor,
        DatabaseRoleCredential(
            username="klm_migrator",
            password="rotated-migration-password",
        ),
    )

    statement, parameters = cursor.calls[-1]
    rendered = repr(statement)
    assert "ALTER ROLE" in rendered
    assert "NOSUPERUSER" not in rendered
    assert "NOCREATEDB" not in rendered
    assert parameters == ("rotated-migration-password",)


def test_existing_resolver_owner_is_verified_instead_of_realtered() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []
            self.fetchone_calls = 0

        def execute(self, statement, parameters=None) -> None:
            self.calls.append((statement, parameters))

        def fetchone(self):
            self.fetchone_calls += 1
            return (1,)

        def fetchall(self):
            return []

    cursor = Cursor()
    owner = _ensure_resolver_owner_role(cursor, migrator_role="klm_migrator")

    assert owner == resolver_owner_role_name("klm_migrator")
    rendered = "\n".join(repr(statement) for statement, _ in cursor.calls)
    assert "ALTER ROLE" not in rendered
    assert "GRANT" in rendered


def test_master_bootstrap_preinstalls_only_required_migration_extensions() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.statements: list[object] = []

        def execute(self, statement, _parameters=None) -> None:
            self.statements.append(statement)

    cursor = Cursor()
    _ensure_required_migration_extensions(cursor)

    rendered = "\n".join(repr(statement) for statement in cursor.statements)
    assert rendered.count("CREATE EXTENSION IF NOT EXISTS") == 2
    assert "Identifier('btree_gist')" in rendered
    assert "Identifier('pg_trgm')" in rendered
    assert "vector" not in rendered


def test_migration_preflight_accepts_sqlalchemy_postgresql_driver_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, _statement) -> None:
            return None

        def fetchone(self):
            return ("klm_migrator", False, False, False, False, False, False, True, True)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def cursor(self):
            return Cursor()

    def connect(**options):
        observed.update(options)
        return Connection()

    monkeypatch.setattr("app.db.aws_database_roles.psycopg2.connect", connect)

    assert_migration_role(
        "postgresql+psycopg2://klm_migrator:secret%2Fvalue@db.internal:5433/"
        "meu_executivo?sslmode=require",
        "klm_migrator",
    )

    assert observed == {
        "host": "db.internal",
        "port": 5433,
        "dbname": "meu_executivo",
        "user": "klm_migrator",
        "password": "secret/value",
        "connect_timeout": 10,
        "application_name": "meu-executivo-migration-preflight",
        "sslmode": "require",
    }


def test_migration_preflight_rejects_non_postgresql_url() -> None:
    with pytest.raises(DatabaseRoleBootstrapError, match="requires a PostgreSQL URL"):
        assert_migration_role("sqlite:///unsafe.db", "klm_migrator")


@pytest.mark.parametrize(
    ("function_name", "cast_target"),
    (
        ("klm_resolve_zapsign_webhook_workspace", "p_document_token"),
        ("klm_resolve_whatsapp_webhook_workspace", "p_phone_number_id"),
        ("klm_resolve_asaas_webhook_workspace", "p_token_digest"),
        ("klm_resolve_signup_verification_workspace", "p_token_digest"),
        ("klm_resolve_workspace_invitation_workspace", "p_token_digest"),
    ),
)
def test_resolver_body_normalization_accepts_catalog_casts_but_not_divergent_sql(
    function_name: str,
    cast_target: str,
) -> None:
    expected = TENANT_RESOLVER_FUNCTION_BODIES[function_name]
    catalog_form = f"(({expected.replace(cast_target, cast_target + '::text')}))"

    assert _normalize_function_body(catalog_form) == _normalize_function_body(expected)
    assert _normalize_function_body(expected.replace("workspace_id", "id", 1)) != (
        _normalize_function_body(expected)
    )
