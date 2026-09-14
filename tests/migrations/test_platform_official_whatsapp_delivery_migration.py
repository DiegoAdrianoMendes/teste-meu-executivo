from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).parents[2]
    / "alembic/versions/d9f1a3c5b617_add_platform_official_whatsapp_deliveries.py"
)
PROJECT_ROOT = MIGRATION.parents[2]


def _run_alembic(sqlite_url: str, *arguments: str) -> None:
    executable = Path(sys.executable).with_name("alembic")
    environment = os.environ.copy()
    environment["DATABASE_URL"] = sqlite_url
    environment["DATABASE_URL_SYNC"] = sqlite_url
    result = subprocess.run(
        [str(executable), *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _create_c8_boundary(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE users (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE platform_official_whatsapp_connections (
                id VARCHAR(36) PRIMARY KEY,
                role VARCHAR(20) NOT NULL
            );
            """
        )
    finally:
        connection.close()


def test_forward_revision_is_global_and_preserves_runtime_contract() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "d9f1a3c5b617"' in source
    assert 'down_revision: str | Sequence[str] | None = "c8e0f2a4b615"' in source
    assert 'TABLE = "platform_official_whatsapp_deliveries"' in source
    assert "workspace_id" not in source
    assert "ROW({immutable_new}) IS DISTINCT FROM ROW({immutable_old})" in source
    assert "BEFORE UPDATE OR DELETE" in source
    assert "BEFORE TRUNCATE" in source
    assert "REVOKE {row['privilege_type']}" in source
    assert "('DELETE', 'TRUNCATE')" in source


def test_sqlite_forward_round_trip_enforces_contract_and_delete_guards(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "official-whatsapp-delivery.db"
    sqlite_url = f"sqlite:///{database_path}"
    _create_c8_boundary(database_path)

    _run_alembic(sqlite_url, "stamp", "c8e0f2a4b615")
    _run_alembic(sqlite_url, "upgrade", "d9f1a3c5b617")

    connection_id = "00000000-0000-0000-0000-000000000001"
    delivery_id = "00000000-0000-0000-0000-000000000002"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO platform_official_whatsapp_connections (id, role) VALUES (?, ?)",
            (connection_id, "outbound"),
        )
        connection.execute(
            """
            INSERT INTO platform_official_whatsapp_deliveries (
                connection_id, role, source_type, idempotency_key_hash,
                payload_digest, payload_ciphertext, body_length, id
            ) VALUES (?, 'outbound', 'blackbox_test', ?, ?, ?, 12, ?)
            """,
            (connection_id, "a" * 64, "b" * 64, "ciphertext", delivery_id),
        )
        connection.commit()

        connection.execute(
            "UPDATE platform_official_whatsapp_deliveries "
            "SET state = 'provider_call_started', attempt_count = 1 "
            "WHERE id = ?",
            (delivery_id,),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="request contract is immutable"):
            connection.execute(
                "UPDATE platform_official_whatsapp_deliveries "
                "SET source_type = 'forged' WHERE id = ?",
                (delivery_id,),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute(
                "DELETE FROM platform_official_whatsapp_deliveries WHERE id = ?",
                (delivery_id,),
            )
        connection.rollback()
    finally:
        connection.close()

    _run_alembic(sqlite_url, "downgrade", "c8e0f2a4b615")
    _run_alembic(sqlite_url, "upgrade", "d9f1a3c5b617")

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "d9f1a3c5b617",
        )
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'platform_official_whatsapp_deliveries'"
            )
        }
        assert trigger_names == {
            "trg_platform_official_whatsapp_deliveries_contract_update",
            "trg_platform_official_whatsapp_deliveries_delete",
        }
    finally:
        connection.close()
