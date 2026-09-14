from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).parents[2]
    / "alembic/versions/c5f7a9b1d304_harden_whatsapp_webhook_processing.py"
)
PROJECT_ROOT = MIGRATION.parents[2]
PARENT_REVISION = "b4e6f8a0c253"
REVISION = "c5f7a9b1d304"
ENVELOPE_TABLE = "klm_omnichannel_provider_webhook_envelopes"
STATE_TABLE = "klm_omnichannel_provider_webhook_processing_states"
CAMPAIGN_EVENT_TABLE = "whatsapp_campaign_delivery_events"


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


def _run_alembic_expect_failure(sqlite_url: str, *arguments: str) -> str:
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
    assert result.returncode != 0
    return result.stdout + result.stderr


def _create_parent_boundary(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            f"""
            PRAGMA foreign_keys = ON;
            CREATE TABLE {ENVELOPE_TABLE} (
                id VARCHAR(36) PRIMARY KEY,
                received_at DATETIME NOT NULL
            );
            CREATE TABLE {CAMPAIGN_EVENT_TABLE} (
                id VARCHAR(36) PRIMARY KEY
            );
            CREATE TRIGGER trg_{ENVELOPE_TABLE}_immutable
            BEFORE UPDATE ON {ENVELOPE_TABLE}
            BEGIN
                SELECT RAISE(ABORT, 'provider webhook envelope is immutable');
            END;
            CREATE TRIGGER trg_{ENVELOPE_TABLE}_immutable_delete
            BEFORE DELETE ON {ENVELOPE_TABLE}
            BEGIN
                SELECT RAISE(ABORT, 'provider webhook envelope is immutable');
            END;
            INSERT INTO {ENVELOPE_TABLE} (id, received_at)
            VALUES (
                '00000000-0000-0000-0000-000000000001',
                '2026-07-30 12:00:00'
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def test_revision_documents_exact_signed_delivery_and_historical_no_replay() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert f'revision: str = "{REVISION}"' in source
    assert f'down_revision: str | Sequence[str] | None = "{PARENT_REVISION}"' in source
    assert "delivery_fingerprint_sha256" in source
    assert "provider_event_key" in source
    assert "historical envelopes" in source.lower()
    assert "'processed'" in source
    assert "orphan_count" in source
    assert "Refusing to drop durable WhatsApp webhook processing evidence" in source


def test_sqlite_round_trip_backfills_without_replay_and_enforces_state_shapes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "whatsapp-webhook-processing.db"
    sqlite_url = f"sqlite:///{database_path}"
    _create_parent_boundary(database_path)

    _run_alembic(sqlite_url, "stamp", PARENT_REVISION)
    _run_alembic(sqlite_url, "upgrade", REVISION)

    connection = sqlite3.connect(database_path)
    try:
        assert "delivery_fingerprint_sha256" in _columns(connection, ENVELOPE_TABLE)
        assert "provider_event_key" in _columns(connection, CAMPAIGN_EVENT_TABLE)
        historical = connection.execute(
            f"""
            SELECT status, attempts, processed_at
            FROM {STATE_TABLE}
            WHERE envelope_id = '00000000-0000-0000-0000-000000000001'
            """
        ).fetchone()
        assert historical is not None
        assert historical[0:2] == ("processed", 1)
        assert historical[2] is not None

        fingerprint = "a" * 64
        connection.execute(
            f"""
            INSERT INTO {ENVELOPE_TABLE} (
                id, received_at, delivery_fingerprint_sha256
            ) VALUES (?, ?, ?)
            """,
            (
                "00000000-0000-0000-0000-000000000002",
                "2026-07-31 01:00:00",
                fingerprint,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"""
                INSERT INTO {ENVELOPE_TABLE} (
                    id, received_at, delivery_fingerprint_sha256
                ) VALUES (?, ?, ?)
                """,
                (
                    "00000000-0000-0000-0000-000000000003",
                    "2026-07-31 01:01:00",
                    fingerprint,
                ),
            )
        connection.rollback()

        connection.execute(
            f"""
            INSERT INTO {CAMPAIGN_EVENT_TABLE} (id, provider_event_key)
            VALUES (?, ?)
            """,
            ("10000000-0000-0000-0000-000000000001", "c" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"""
                INSERT INTO {CAMPAIGN_EVENT_TABLE} (id, provider_event_key)
                VALUES (?, ?)
                """,
                ("10000000-0000-0000-0000-000000000002", "c" * 64),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"""
                INSERT INTO {CAMPAIGN_EVENT_TABLE} (id, provider_event_key)
                VALUES (?, ?)
                """,
                ("10000000-0000-0000-0000-000000000003", "too-short"),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"""
                INSERT INTO {ENVELOPE_TABLE} (
                    id, received_at, delivery_fingerprint_sha256
                ) VALUES (?, ?, ?)
                """,
                (
                    "00000000-0000-0000-0000-000000000004",
                    "2026-07-31 01:02:00",
                    "too-short",
                ),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"""
                INSERT INTO {STATE_TABLE} (
                    envelope_id, status, attempts
                ) VALUES (?, 'processing', 1)
                """,
                ("00000000-0000-0000-0000-000000000001",),
            )
        connection.rollback()
    finally:
        connection.close()

    _run_alembic(sqlite_url, "downgrade", PARENT_REVISION)
    connection = sqlite3.connect(database_path)
    try:
        assert STATE_TABLE not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "delivery_fingerprint_sha256" not in _columns(
            connection,
            ENVELOPE_TABLE,
        )
        assert "provider_event_key" not in _columns(connection, CAMPAIGN_EVENT_TABLE)
        assert connection.execute(
            f"SELECT COUNT(*) FROM {ENVELOPE_TABLE}"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'trigger' AND name = ?",
            (f"trg_{ENVELOPE_TABLE}_immutable",),
        ).fetchone() == (1,)
    finally:
        connection.close()

    _run_alembic(sqlite_url, "upgrade", REVISION)
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            REVISION,
        )
        assert connection.execute(
            f"SELECT status, attempts FROM {STATE_TABLE}"
        ).fetchall() == [("processed", 1)]
    finally:
        connection.close()


def test_sqlite_downgrade_fails_closed_after_post_upgrade_delivery(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "whatsapp-webhook-fail-closed.db"
    sqlite_url = f"sqlite:///{database_path}"
    _create_parent_boundary(database_path)
    _run_alembic(sqlite_url, "stamp", PARENT_REVISION)
    _run_alembic(sqlite_url, "upgrade", REVISION)

    connection = sqlite3.connect(database_path)
    try:
        envelope_id = "00000000-0000-0000-0000-000000000010"
        connection.execute(
            f"""
            INSERT INTO {ENVELOPE_TABLE} (
                id, received_at, delivery_fingerprint_sha256
            ) VALUES (?, ?, ?)
            """,
            (envelope_id, "2026-07-31 02:00:00", "b" * 64),
        )
        connection.execute(
            f"""
            INSERT INTO {STATE_TABLE} (
                envelope_id, status, attempts
            ) VALUES (?, 'queued', 0)
            """,
            (envelope_id,),
        )
        connection.commit()
    finally:
        connection.close()

    output = _run_alembic_expect_failure(
        sqlite_url,
        "downgrade",
        PARENT_REVISION,
    )
    assert "Refusing to drop durable WhatsApp webhook processing evidence" in output

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            REVISION,
        )
        assert connection.execute(
            f"SELECT status FROM {STATE_TABLE} WHERE envelope_id = ?",
            ("00000000-0000-0000-0000-000000000010",),
        ).fetchone() == ("queued",)
    finally:
        connection.close()
