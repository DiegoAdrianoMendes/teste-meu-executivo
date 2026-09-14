from __future__ import annotations

import pytest

from app.db.advisory import (
    acquire_transaction_advisory_lock,
    advisory_lock_key,
)


def test_advisory_lock_key_is_stable_namespaced_and_signed_bigint_safe() -> None:
    first = advisory_lock_key("function-unit", "workspace:unit:key")

    assert first == advisory_lock_key("function-unit", "workspace:unit:key")
    assert first != advisory_lock_key("native-record", "workspace:unit:key")
    assert -(2**63) <= first < 2**63


@pytest.mark.asyncio
async def test_sqlite_local_runtime_skips_postgresql_advisory_sql(db_session) -> None:
    acquired = await acquire_transaction_advisory_lock(
        db_session,
        namespace="native-record",
        value="workspace:record",
    )

    assert acquired is False
