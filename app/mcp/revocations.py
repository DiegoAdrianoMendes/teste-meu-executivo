"""Immediate, local revocation markers for already-running MCP grants."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID


class GrantRevocationStore:
    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()

    def _marker(self, grant_id: UUID) -> Path:
        return self.state_dir / "revoked-grants" / str(grant_id)

    def is_revoked(self, grant_id: UUID) -> bool:
        # Any filesystem object at the marker path means revoked. This is
        # deliberately fail-closed if the local state has been tampered with.
        return self._marker(grant_id).exists() or self._marker(grant_id).is_symlink()

    def revoke(self, grant_id: UUID) -> Path:
        directory = self.state_dir / "revoked-grants"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        marker = self._marker(grant_id)
        try:
            descriptor = os.open(
                marker,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError:
            return marker
        try:
            payload = datetime.now(UTC).isoformat().encode("ascii")
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return marker
