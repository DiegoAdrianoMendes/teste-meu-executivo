"""Transaction-scoped PostgreSQL locks for aggregate and idempotency keys."""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def advisory_lock_key(namespace: str, value: str) -> int:
    """Return a stable signed 64-bit key accepted by PostgreSQL advisory locks."""

    digest = hashlib.sha256(f"{namespace}\0{value}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def acquire_transaction_advisory_lock(
    session: AsyncSession,
    *,
    namespace: str,
    value: str,
) -> bool:
    """Serialize one logical key until transaction end; SQLite needs no SQL lock."""

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": advisory_lock_key(namespace, value)},
    )
    return True


__all__ = ["acquire_transaction_advisory_lock", "advisory_lock_key"]
