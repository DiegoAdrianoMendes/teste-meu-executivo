from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVISION = "e1b3c5d7e920"
PARENT = "e0a2b4d6c819"
C8 = "c8e0f2a4b615"
D9 = "d9f1a3c5b617"
MIGRATION = ROOT / "alembic/versions/e1b3c5d7e920_harden_official_whatsapp_payload_guard.py"


def _run_alembic(database_url: str, *arguments: str) -> None:
    executable = Path(sys.executable).with_name("alembic")
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["DATABASE_URL_SYNC"] = database_url
    result = subprocess.run(
        [str(executable), *arguments],
        cwd=ROOT,
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


def _insert_delivery(database_path: Path) -> tuple[sqlite3.Connection, str]:
    connection_id = "00000000-0000-0000-0000-000000000011"
    delivery_id = "00000000-0000-0000-0000-000000000012"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "INSERT INTO platform_official_whatsapp_connections (id, role) VALUES (?, ?)",
        (connection_id, "outbound"),
    )
    connection.execute(
        """
        INSERT INTO platform_official_whatsapp_deliveries (
            connection_id, role, source_type, idempotency_key_hash,
            payload_digest, payload_ciphertext, body_length, id
        ) VALUES (?, 'outbound', 'migration_test', ?, ?, ?, 12, ?)
        """,
        (connection_id, "a" * 64, "b" * 64, "ciphertext-original", delivery_id),
    )
    connection.commit()
    return connection, delivery_id


def test_revision_is_forward_only_after_task_owner_correction() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert f'revision: str = "{REVISION}"' in source
    assert f'down_revision: str | Sequence[str] | None = "{PARENT}"' in source
    assert "payload_ciphertext IS DISTINCT FROM OLD.payload_ciphertext" in source
    assert "OLD.state IN" in source
    assert "NEW.state IN" in source


def test_sqlite_round_trip_rejects_tamper_and_allows_terminal_purge(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "official-whatsapp-payload-guard.db"
    database_url = f"sqlite:///{database_path}"
    _create_c8_boundary(database_path)
    _run_alembic(database_url, "stamp", C8)
    _run_alembic(database_url, "upgrade", D9)
    _run_alembic(database_url, "stamp", PARENT)
    _run_alembic(database_url, "upgrade", REVISION)

    connection, delivery_id = _insert_delivery(database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE platform_official_whatsapp_deliveries "
                "SET payload_ciphertext = 'ciphertext-forged' WHERE id = ?",
                (delivery_id,),
            )
        connection.rollback()

        connection.execute(
            "UPDATE platform_official_whatsapp_deliveries "
            "SET state = 'provider_call_started', attempt_count = 1 WHERE id = ?",
            (delivery_id,),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE platform_official_whatsapp_deliveries "
                "SET payload_ciphertext = 'ciphertext-forged' WHERE id = ?",
                (delivery_id,),
            )
        connection.rollback()

        connection.execute(
            "UPDATE platform_official_whatsapp_deliveries "
            "SET state = 'sent', external_message_id = 'wamid.migration', "
            "payload_ciphertext = NULL WHERE id = ?",
            (delivery_id,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT state, payload_ciphertext FROM "
            "platform_official_whatsapp_deliveries WHERE id = ?",
            (delivery_id,),
        ).fetchone() == ("sent", None)
    finally:
        connection.close()

    _run_alembic(database_url, "downgrade", PARENT)
    _run_alembic(database_url, "upgrade", REVISION)
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            REVISION,
        )
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            ("trg_platform_official_whatsapp_deliveries_contract_update",),
        ).fetchone()[0]
        assert "payload_ciphertext" in trigger_sql
    finally:
        connection.close()
