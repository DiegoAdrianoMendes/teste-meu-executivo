from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_status import SystemStatusObservation
from app.services.system_status_service import SystemStatusHistoryService


def observation(
    status: str,
    observed_at: datetime,
    *,
    component_status: str = "operational",
) -> SystemStatusObservation:
    return SystemStatusObservation(
        overall_status=status,
        observed_at=observed_at,
        message=status,
        components={"database": {"status": component_status}},
    )


def test_empty_history_is_explicitly_collecting_without_synthetic_uptime() -> None:
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)

    result = SystemStatusHistoryService.summarize([], now=now)

    assert result["state"] == "collecting"
    assert result["observations"] == 0
    assert result["uptimePercent"] is None
    assert result["incidents"] == []
    assert len(result["daily"]) == 30
    assert {item["status"] for item in result["daily"]} == {"no_data"}


def test_history_calculates_uptime_daily_state_and_recovery_from_real_samples() -> None:
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    rows = [
        observation("partial", now - timedelta(days=1)),
        observation("operational", now - timedelta(minutes=15)),
        observation(
            "degraded",
            now - timedelta(minutes=10),
            component_status="degraded",
        ),
        observation("operational", now - timedelta(minutes=3)),
    ]

    result = SystemStatusHistoryService.summarize(rows, now=now)

    assert result["state"] == "ready"
    assert result["observations"] == 4
    assert result["uptimePercent"] == 75.0
    assert result["daysObserved"] == 2
    assert result["latestStatus"] == "operational"
    today = next(item for item in result["daily"] if item["date"] == now.date().isoformat())
    assert today == {
        "date": now.date().isoformat(),
        "status": "degraded",
        "observations": 3,
        "uptimePercent": 66.67,
    }
    assert len(result["incidents"]) == 1
    assert result["incidents"][0]["ongoing"] is False
    assert result["incidents"][0]["components"] == ["database"]
    assert result["incidents"][0]["observations"] == 1


@pytest.mark.asyncio
async def test_observation_interval_prevents_status_polling_from_inflating_history(
    db_session: AsyncSession,
) -> None:
    service = SystemStatusHistoryService(db_session)
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    payload = {
        "overall_status": "operational",
        "components": {"database": {"status": "operational"}},
        "message": "ok",
    }

    assert await service.record(**payload, observed_at=now) is True
    assert await service.record(**payload, observed_at=now + timedelta(minutes=4)) is False
    assert await service.record(**payload, observed_at=now + timedelta(minutes=5)) is True
    await db_session.commit()

    history = await service.history(now=now + timedelta(minutes=5))
    assert history["observations"] == 2
    assert history["uptimePercent"] == 100.0
