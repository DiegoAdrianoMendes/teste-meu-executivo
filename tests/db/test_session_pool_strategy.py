"""Regression tests for the local HTTP database pool strategy."""

from __future__ import annotations

import os
import subprocess
import sys


def _pool_class_for(*, testing: bool) -> str:
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "DATABASE_URL_SYNC": "sqlite:///:memory:",
            "TESTING": "true" if testing else "false",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.db.session import engine; print(type(engine.sync_engine.pool).__name__)",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    return completed.stdout.strip().splitlines()[-1]


def test_local_sqlite_server_uses_bounded_queue_pool() -> None:
    assert _pool_class_for(testing=False) == "AsyncAdaptedQueuePool"


def test_explicit_test_runtime_keeps_loop_safe_null_pool() -> None:
    assert _pool_class_for(testing=True) == "NullPool"
