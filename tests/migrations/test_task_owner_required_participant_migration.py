from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)

ROOT = Path(__file__).resolve().parents[2]
REVISION = "e0a2b4d6c819"
PARENT = "d9f1a3c5b617"
MIGRATION = ROOT / "alembic/versions/e0a2b4d6c819_allow_task_owner_required_for_completion.py"


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


def _task_participants_ddl(database_url: str) -> str:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            ddl = connection.scalar(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'task_participants'"
                )
            )
            assert isinstance(ddl, str)
            return re.sub(r"\s+", " ", ddl.casefold())
    finally:
        engine.dispose()


def test_revision_is_an_isolated_forward_correction() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert f'revision: str = "{REVISION}"' in source
    assert f'down_revision: str | Sequence[str] | None = "{PARENT}"' in source
    assert "role IN ('owner', 'assignee')" in source
    assert "required_for_completion = false OR role = 'assignee'" in source
    downgrade = source[source.index("def downgrade() -> None:") :]
    assert downgrade.index("_require_no_required_owner_before_downgrade()") < (
        downgrade.index("_replace_check(LEGACY_PREDICATE)")
    )


def test_sqlite_round_trip_replaces_and_restores_the_check(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'task-owner-required.db'}"

    engine = create_engine(database_url)
    try:
        metadata = MetaData()
        Table(
            "task_participants",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("role", String(20), nullable=False),
            Column("required_for_completion", Boolean, nullable=False, default=False),
            CheckConstraint(
                "required_for_completion = false OR role = 'assignee'",
                name="ck_task_participants_required_assignee",
            ),
        )
        metadata.create_all(engine)
    finally:
        engine.dispose()

    _run_alembic(database_url, "stamp", PARENT)
    _run_alembic(database_url, "upgrade", REVISION)
    upgraded = _task_participants_ddl(database_url)
    assert "role in ('owner', 'assignee')" in upgraded

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO task_participants "
                    "(id, role, required_for_completion) VALUES "
                    "(1, 'owner', true)"
                )
            )
    finally:
        engine.dispose()

    environment = {
        **os.environ,
        "DATABASE_URL": database_url,
        "DATABASE_URL_SYNC": database_url,
    }
    refused = subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), "downgrade", PARENT],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    assert "refused before replacing the check constraint" in refused.stdout + refused.stderr
    assert "role in ('owner', 'assignee')" in _task_participants_ddl(database_url)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM task_participants "
                        "WHERE role = 'owner' AND required_for_completion = true"
                    )
                )
                == 1
            )
            connection.execute(text("DELETE FROM task_participants WHERE id = 1"))
    finally:
        engine.dispose()

    _run_alembic(database_url, "downgrade", PARENT)
    downgraded = _task_participants_ddl(database_url)
    assert "role = 'assignee'" in downgraded
    assert "role in ('owner', 'assignee')" not in downgraded

    _run_alembic(database_url, "upgrade", REVISION)
    assert "role in ('owner', 'assignee')" in _task_participants_ddl(database_url)
