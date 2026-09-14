"""Legacy alias for the guarded, local-only KLM Hub bootstrap."""

# ruff: noqa: E402, I001

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.scripts.seed_test_users import seed


if __name__ == "__main__":
    asyncio.run(seed())
