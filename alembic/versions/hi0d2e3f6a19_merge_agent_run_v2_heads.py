"""Merge the existing finance and AgentRun V2 heads."""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "hi0d2e3f6a19"
down_revision: str | Sequence[str] | None = ("b7d4e2f9a610", "gh9c1d2e5f08")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
