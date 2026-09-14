"""Local cross-process lock for MCP mutations."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID


class WorkspaceMutationLock:
    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()

    @contextmanager
    def acquire(self, workspace_id: UUID) -> Iterator[None]:
        lock_dir = self.state_dir / "locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(lock_dir, 0o700)
        lock_path = lock_dir / f"{workspace_id}.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
