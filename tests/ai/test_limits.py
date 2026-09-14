from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.ai.errors import BudgetExceededError
from app.ai.limits import InMemoryDailyBudgetLedger


@pytest.mark.asyncio
async def test_concurrent_reservations_never_cross_daily_limit() -> None:
    ledger = InMemoryDailyBudgetLedger()

    async def reserve_once() -> bool:
        try:
            await ledger.reserve("workspace-a", Decimal("0.02"), Decimal("1.00"))
        except BudgetExceededError:
            return False
        return True

    accepted = await asyncio.gather(*(reserve_once() for _ in range(100)))

    assert sum(accepted) == 50
    assert await ledger.spent("workspace-a") == Decimal("1.00")


@pytest.mark.asyncio
async def test_settlement_overrun_fails_closed_and_retains_reservation() -> None:
    ledger = InMemoryDailyBudgetLedger()
    reservation = await ledger.reserve(
        "workspace-a",
        Decimal("0.10"),
        Decimal("0.10"),
    )

    with pytest.raises(BudgetExceededError, match="conciliação"):
        await ledger.settle(reservation, Decimal("0.11"))

    assert await ledger.spent("workspace-a") == Decimal("0.10")
    assert await ledger.has_reservation(reservation) is True
    assert await ledger.resolution(reservation) is None
