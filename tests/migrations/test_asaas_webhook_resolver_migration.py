from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVISION = "b8d2f4a6c9e1"
PARENT = "e3f1a7c9d2b5"
MIGRATION = ROOT / "alembic/versions/b8d2f4a6c9e1_add_asaas_webhook_resolver.py"


def _run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["DATABASE_URL_SYNC"] = database_url
    subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), *arguments],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_asaas_resolver_is_fail_closed_owner_hardened_and_grants_select() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert f'revision: str = "{REVISION}"' in source
    assert f'down_revision: str | Sequence[str] | None = "{PARENT}"' in source
    assert "COUNT(DISTINCT connection.workspace_id) = 1" in source
    assert "MIN(connection.workspace_id::text)::uuid" in source
    assert "ELSE NULL::uuid" in source
    assert "connection.settings->>'webhookSecretDigest' = p_token_digest" in source
    assert "connection.provider = 'asaas'" in source
    assert "connection.status <> 'disconnected'" in source
    # com search_path = pg_catalog, pg_temp o nome do enum (em public) não resolve
    assert "::integration_provider_enum" not in source
    assert "::integration_status_enum" not in source
    assert "LIMIT 1" not in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path = pg_catalog, pg_temp" in source
    # policy sem privilégio de tabela não lê nada: o owner ganha SELECT
    assert "GRANT SELECT ON TABLE public.%I TO %I" in source
    assert "Resolver owner Asaas RLS policy is missing or unsafe" in source
    assert "HAVING COUNT(DISTINCT procedure.oid) = 5" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "Asaas resolver definition or ACL drift blocks downgrade" in source
    assert "DROP POLICY %I ON public.%I" in source


def test_sqlite_revision_graph_round_trips_without_postgresql_side_effects(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "asaas-resolver.db"
    database_url = f"sqlite:///{database_path}"
    sqlite3.connect(database_path).close()

    _run_alembic(database_url, "stamp", PARENT)
    _run_alembic(database_url, "upgrade", REVISION)
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            REVISION,
        )
    finally:
        connection.close()

    _run_alembic(database_url, "downgrade", PARENT)
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (PARENT,)
    finally:
        connection.close()
