from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_status import SystemStatusObservation

AVAILABLE_STATUSES = frozenset({"operational", "partial"})
INCIDENT_STATUSES = frozenset({"degraded", "unknown"})
STATUS_PRIORITY = {"operational": 0, "partial": 1, "unknown": 2, "degraded": 3}
HISTORY_RETENTION_DAYS = 30
OBSERVATION_INTERVAL = timedelta(minutes=5)
INCIDENT_JOIN_WINDOW = timedelta(minutes=10)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _worst_status(statuses: Iterable[str]) -> str:
    return max(statuses, key=lambda item: STATUS_PRIORITY.get(item, 2), default="unknown")


def _impacted_components(row: SystemStatusObservation) -> set[str]:
    return {
        str(name)
        for name, value in (row.components or {}).items()
        if isinstance(value, dict) and value.get("status") in INCIDENT_STATUSES
    }


class SystemStatusHistoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        *,
        overall_status: str,
        components: dict[str, Any],
        message: str,
        observed_at: datetime | None = None,
    ) -> bool:
        now = _utc(observed_at or datetime.now(UTC))
        latest = await self.db.scalar(
            select(SystemStatusObservation)
            .order_by(SystemStatusObservation.observed_at.desc())
            .limit(1)
        )
        if latest is not None and now - _utc(latest.observed_at) < OBSERVATION_INTERVAL:
            return False

        self.db.add(
            SystemStatusObservation(
                overall_status=overall_status,
                components=components,
                message=message,
                observed_at=now,
            )
        )
        await self.db.execute(
            delete(SystemStatusObservation).where(
                SystemStatusObservation.observed_at < now - timedelta(days=31)
            ).execution_options(synchronize_session=False)
        )
        await self.db.flush()
        return True

    async def history(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _utc(now or datetime.now(UTC))
        rows = list(
            (
                await self.db.scalars(
                    select(SystemStatusObservation)
                    .where(
                        SystemStatusObservation.observed_at
                        >= current - timedelta(days=HISTORY_RETENTION_DAYS)
                    )
                    .order_by(SystemStatusObservation.observed_at.asc())
                )
            ).all()
        )
        return self.summarize(rows, now=current)

    @staticmethod
    def summarize(
        rows: Iterable[SystemStatusObservation],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _utc(now or datetime.now(UTC))
        ordered = sorted(rows, key=lambda row: _utc(row.observed_at))
        buckets: defaultdict[date, list[SystemStatusObservation]] = defaultdict(list)
        for row in ordered:
            buckets[_utc(row.observed_at).date()].append(row)

        daily: list[dict[str, Any]] = []
        first_day = current.date() - timedelta(days=HISTORY_RETENTION_DAYS - 1)
        for offset in range(HISTORY_RETENTION_DAYS):
            day = first_day + timedelta(days=offset)
            samples = buckets.get(day, [])
            statuses = [row.overall_status for row in samples]
            available = sum(status in AVAILABLE_STATUSES for status in statuses)
            daily.append(
                {
                    "date": day.isoformat(),
                    "status": _worst_status(statuses) if samples else "no_data",
                    "observations": len(samples),
                    "uptimePercent": (
                        round(available / len(samples) * 100, 2) if samples else None
                    ),
                }
            )

        total = len(ordered)
        available_total = sum(row.overall_status in AVAILABLE_STATUSES for row in ordered)
        incidents: list[dict[str, Any]] = []
        active: dict[str, Any] | None = None
        previous_bad_at: datetime | None = None
        for row in ordered:
            observed_at = _utc(row.observed_at)
            is_bad = row.overall_status in INCIDENT_STATUSES
            if is_bad:
                if active is None or (
                    previous_bad_at is not None
                    and observed_at - previous_bad_at > INCIDENT_JOIN_WINDOW
                ):
                    if active is not None:
                        active["endedAt"] = _iso(previous_bad_at)
                        active["ongoing"] = False
                        active["components"] = sorted(active["components"])
                        incidents.append(active)
                    active = {
                        "startedAt": _iso(observed_at),
                        "endedAt": None,
                        "ongoing": True,
                        "status": row.overall_status,
                        "observations": 0,
                        "components": set(),
                    }
                active["status"] = _worst_status(
                    [str(active["status"]), row.overall_status]
                )
                active["observations"] += 1
                active["components"].update(_impacted_components(row))
                previous_bad_at = observed_at
            elif active is not None:
                active["endedAt"] = _iso(observed_at)
                active["ongoing"] = False
                active["components"] = sorted(active["components"])
                incidents.append(active)
                active = None
                previous_bad_at = None
        if active is not None:
            active["components"] = sorted(active["components"])
            incidents.append(active)

        return {
            "state": "ready" if ordered else "collecting",
            "periodDays": HISTORY_RETENTION_DAYS,
            "observations": total,
            "uptimePercent": round(available_total / total * 100, 2) if total else None,
            "firstObservedAt": _iso(ordered[0].observed_at) if ordered else None,
            "lastObservedAt": _iso(ordered[-1].observed_at) if ordered else None,
            "daysObserved": sum(bool(samples) for samples in buckets.values()),
            "latestStatus": ordered[-1].overall_status if ordered else None,
            "daily": daily,
            "incidents": list(reversed(incidents)),
        }
