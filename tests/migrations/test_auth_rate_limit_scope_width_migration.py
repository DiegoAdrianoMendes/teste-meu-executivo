from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
REVISION = "e8c0a2d4f617"
PARENT = "e7b9d1f3a526"
MIGRATION = ROOT / "alembic/versions/e8c0a2d4f617_widen_auth_rate_limit_event_scope.py"
SCOPES = (
    "login",
    "register",
    "verify_signup",
    "invitation",
    "accept_invitation",
    "forgot_password",
    "reset_password",
)


def _constants() -> SimpleNamespace:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"), filename=str(MIGRATION))
    assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    namespace: dict[str, object] = {}
    exec(
        compile(
            ast.Module(body=assignments, type_ignores=[]),
            str(MIGRATION),
            "exec",
        ),
        namespace,
    )
    return SimpleNamespace(**namespace)


def _alembic(database_url: str, *arguments: str, check: bool = True):
    return subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), *arguments],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "DATABASE_URL_SYNC": database_url,
        },
        check=check,
        capture_output=True,
        text=True,
    )


def _scope_schema(connection: sqlite3.Connection) -> tuple[str, set[str], set[str]]:
    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("auth_rate_limit_events",),
    ).fetchone()[0]
    indexes = {
        row[1]
        for row in connection.execute("PRAGMA index_list('auth_rate_limit_events')")
        if row[1]
    }
    checks = {scope for scope in SCOPES if f"'{scope}'" in table_sql}
    return table_sql, indexes, checks


def test_scope_width_expands_without_losing_contract_and_downgrade_is_fail_closed(
    tmp_path: Path,
) -> None:
    migration = _constants()
    assert migration.revision == REVISION
    assert migration.down_revision == PARENT
    assert migration.PREVIOUS_SCOPE_LENGTH == 15
    assert migration.SCOPE_LENGTH == max(map(len, SCOPES)) == 17

    database_path = tmp_path / "auth-rate-limit-scope.db"
    database_url = f"sqlite:///{database_path}"
    allowed = ", ".join(f"'{scope}'" for scope in SCOPES)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE auth_rate_limit_events ("
            "id TEXT PRIMARY KEY, "
            "scope VARCHAR(15) NOT NULL, "
            "identifier_digest VARCHAR(64) NOT NULL, "
            "network_digest VARCHAR(64) NOT NULL, "
            "was_limited BOOLEAN NOT NULL DEFAULT 0, "
            "occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "CONSTRAINT auth_rate_limit_scope_enum "
            f"CHECK (scope IN ({allowed})) "
            ")"
        )
        connection.execute(
            "CREATE INDEX ix_auth_rate_limit_scope_identifier_occurred "
            "ON auth_rate_limit_events (scope, identifier_digest, occurred_at)"
        )
        connection.execute(
            "CREATE INDEX ix_auth_rate_limit_scope_network_occurred "
            "ON auth_rate_limit_events (scope, network_digest, occurred_at)"
        )
        connection.commit()
        before_sql, before_indexes, before_checks = _scope_schema(connection)
        assert "VARCHAR(15)" in before_sql
    finally:
        connection.close()

    _alembic(database_url, "stamp", PARENT)
    _alembic(database_url, "upgrade", REVISION)

    connection = sqlite3.connect(database_path)
    try:
        expanded_sql, expanded_indexes, expanded_checks = _scope_schema(connection)
        assert "VARCHAR(17)" in expanded_sql
        assert expanded_indexes == before_indexes
        assert expanded_checks == before_checks == set(SCOPES)
        connection.execute(
            "INSERT INTO auth_rate_limit_events "
            "(id, scope, identifier_digest, network_digest) VALUES (?, ?, ?, ?)",
            ("event-1", "accept_invitation", "i" * 64, "n" * 64),
        )
        connection.commit()
    finally:
        connection.close()

    failed = _alembic(database_url, "downgrade", PARENT, check=False)
    assert failed.returncode != 0
    assert "Cannot downgrade auth rate-limit event scope width" in (failed.stdout + failed.stderr)

    connection = sqlite3.connect(database_path)
    try:
        (scope,) = connection.execute(
            "SELECT scope FROM auth_rate_limit_events WHERE id = ?", ("event-1",)
        ).fetchone()
        assert scope == "accept_invitation"
        assert "VARCHAR(17)" in _scope_schema(connection)[0]
        connection.execute("DELETE FROM auth_rate_limit_events WHERE id = ?", ("event-1",))
        connection.commit()
    finally:
        connection.close()

    _alembic(database_url, "downgrade", PARENT)
    connection = sqlite3.connect(database_path)
    try:
        contracted_sql, contracted_indexes, contracted_checks = _scope_schema(connection)
        assert "VARCHAR(15)" in contracted_sql
        assert contracted_indexes == before_indexes
        assert contracted_checks == before_checks
    finally:
        connection.close()
