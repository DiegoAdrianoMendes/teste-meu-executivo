from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVISION = "e7b9d1f3a526"
PARENT = "d6a8c0e2f415"
MIGRATION = ROOT / "alembic/versions/e7b9d1f3a526_add_contact_stock_entitlement.py"


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


def test_contact_catalog_revision_round_trips_and_refuses_lossy_downgrade(
    tmp_path: Path,
) -> None:
    migration = _constants()
    assert migration.revision == REVISION
    assert migration.down_revision == PARENT
    assert set(migration.ENTITLEMENT_VALUES) == {
        *migration.PREVIOUS_ENTITLEMENT_VALUES,
        "contacts",
    }

    database_path = tmp_path / "contact-stock.db"
    database_url = f"sqlite:///{database_path}"
    previous_allowed = ", ".join(f"'{value}'" for value in migration.PREVIOUS_ENTITLEMENT_VALUES)
    connection = sqlite3.connect(database_path)
    try:
        for table_name, constraint_name in migration.ENTITLEMENT_CATALOG_TABLES.items():
            connection.execute(
                f'CREATE TABLE "{table_name}" ('
                "id INTEGER PRIMARY KEY, "
                f"entitlement VARCHAR({migration.PREVIOUS_ENTITLEMENT_LENGTH}) "
                "NOT NULL, "
                f'CONSTRAINT "{constraint_name}" '
                f"CHECK (entitlement IN ({previous_allowed})))"
            )
        connection.commit()
    finally:
        connection.close()

    _alembic(database_url, "stamp", PARENT)
    _alembic(database_url, "upgrade", REVISION)
    connection = sqlite3.connect(database_path)
    try:
        for table_name in migration.ENTITLEMENT_CATALOG_TABLES:
            connection.execute(
                f'INSERT INTO "{table_name}" (entitlement) VALUES (?)',
                ("contacts",),
            )
        connection.commit()
    finally:
        connection.close()

    failed = _alembic(database_url, "downgrade", PARENT, check=False)
    assert failed.returncode != 0
    assert "Cannot downgrade contacts" in failed.stdout + failed.stderr

    connection = sqlite3.connect(database_path)
    try:
        for table_name in migration.ENTITLEMENT_CATALOG_TABLES:
            connection.execute(
                f'DELETE FROM "{table_name}" WHERE entitlement = ?',
                ("contacts",),
            )
        connection.commit()
    finally:
        connection.close()

    _alembic(database_url, "downgrade", PARENT)
    connection = sqlite3.connect(database_path)
    try:
        for table_name in migration.ENTITLEMENT_CATALOG_TABLES:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f'INSERT INTO "{table_name}" (entitlement) VALUES (?)',
                    ("contacts",),
                )
    finally:
        connection.close()
