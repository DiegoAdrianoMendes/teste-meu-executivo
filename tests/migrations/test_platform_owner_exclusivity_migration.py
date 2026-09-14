from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVISION = "b4e6f8a0c253"
PARENT = "a3d5e7f9b142"
SELECTOR = "KLM_PLATFORM_OWNER_USER_ID"
MIGRATION = ROOT / ("alembic/versions/b4e6f8a0c253_enforce_platform_owner_exclusivity.py")
LOCAL_SEED = ROOT / "app/services/klm_local_seed_service.py"


def _run_alembic(
    database_url: str,
    *arguments: str,
    owner_id: UUID | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["DATABASE_URL_SYNC"] = database_url
    environment.pop(SELECTOR, None)
    if owner_id is not None:
        environment[SELECTOR] = str(owner_id)
    return subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), *arguments],
        cwd=ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def _create_boundary_schema(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE users (
            id VARCHAR(36) PRIMARY KEY,
            is_platform_admin BOOLEAN NOT NULL DEFAULT 0,
            is_internal_staff BOOLEAN NOT NULL DEFAULT 0,
            platform_access_active BOOLEAN NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            deleted_at DATETIME,
            platform_role_package VARCHAR(80)
        );
        CREATE TABLE workspace_memberships (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL
        );
        """
    )
    connection.commit()
    return connection


def test_revision_is_a_forward_fail_closed_correction() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert f'revision: str = "{REVISION}"' in source
    assert f'down_revision: str | Sequence[str] | None = "{PARENT}"' in source
    assert SELECTOR in source
    assert "Multiple historical platform owners were found" in source
    assert "platform_role_package = :admin_package" in source
    assert "platform_role_package = :owner_package" in source
    assert "uq_users_single_platform_owner" in source
    assert (
        "LIMIT 1"
        not in source.split("def _owner_rows", 1)[1].split("def _eligible_candidate", 1)[0]
    )
    assert "ORDER BY id" in source


def test_two_legacy_admins_require_explicit_titular_and_preserve_one_root(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "two-legacy-admins.db"
    database_url = f"sqlite:///{database_path}"
    first_id = uuid4()
    titular_id = uuid4()
    connection = _create_boundary_schema(database_path)
    try:
        connection.executemany(
            """
            INSERT INTO users (
                id,
                is_platform_admin,
                is_internal_staff,
                platform_access_active,
                is_active,
                platform_role_package
            ) VALUES (?, 1, 1, 1, 1, 'platform_owner')
            """,
            [(str(first_id),), (str(titular_id),)],
        )
        connection.commit()
    finally:
        connection.close()

    _run_alembic(database_url, "stamp", PARENT)
    refused = _run_alembic(
        database_url,
        "upgrade",
        REVISION,
        check=False,
    )
    assert refused.returncode != 0
    assert "Multiple historical platform owners were found" in (refused.stdout + refused.stderr)

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (PARENT,)
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE platform_role_package = 'platform_owner'
            """
        ).fetchone() == (2,)
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'index' AND name = 'uq_users_single_platform_owner'
            """
        ).fetchone() == (0,)
    finally:
        connection.close()

    _run_alembic(database_url, "upgrade", REVISION, owner_id=titular_id)
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """
            SELECT id, platform_role_package
            FROM users
            ORDER BY id
            """
        ).fetchall()
        packages = {row_id: package for row_id, package in rows}
        assert packages[str(titular_id)] == "platform_owner"
        assert packages[str(first_id)] == "platform_admin"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE users
                SET platform_role_package = 'platform_owner'
                WHERE id = ?
                """,
                (str(first_id),),
            )
    finally:
        connection.close()


def test_greenfield_head_accepts_the_current_single_root_bootstrap(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "greenfield-owner.db"
    database_url = f"sqlite:///{database_path}"
    owner_id = uuid4()
    connection = _create_boundary_schema(database_path)
    connection.close()

    _run_alembic(database_url, "stamp", PARENT)
    _run_alembic(database_url, "upgrade", REVISION)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO users (
                id,
                is_platform_admin,
                is_internal_staff,
                platform_access_active,
                is_active,
                platform_role_package
            ) VALUES (?, 1, 1, 1, 1, 'platform_owner')
            """,
            (str(owner_id),),
        )
        connection.commit()
        assert connection.execute(
            """
            SELECT id
            FROM users
            WHERE platform_role_package = 'platform_owner'
            """
        ).fetchone() == (str(owner_id),)
    finally:
        connection.close()

    seed = LOCAL_SEED.read_text(encoding="utf-8")
    assert 'LOCAL_PLATFORM_USER_EMAIL = "blackbox@meuexecutivo.local"' in seed
    assert 'platform_role_package="platform_owner"' in seed
    assert "platform_user.is_platform_admin = True" in seed
    assert "platform_user.is_internal_staff = True" in seed
    assert "platform_user.platform_access_active = True" in seed
