"""Atomic daily workspace budget reservations for model calls."""
from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import uuid4

from app.ai.errors import BudgetExceededError, BudgetResolutionConflictError
from app.ai.models import BudgetReservation, BudgetResolution
from app.core.redis_client import get_shared_redis_client


def utc_day() -> str:
    return datetime.now(UTC).date().isoformat()


class DailyBudgetLedger(Protocol):
    async def reserve(
        self,
        workspace_id: str,
        amount_usd: Decimal,
        daily_limit_usd: Decimal,
    ) -> BudgetReservation: ...

    async def settle(self, reservation: BudgetReservation, actual_usd: Decimal) -> Decimal: ...

    async def release(self, reservation: BudgetReservation) -> Decimal: ...

    async def has_reservation(self, reservation: BudgetReservation) -> bool: ...

    async def resolution(
        self, reservation: BudgetReservation
    ) -> BudgetResolution | None: ...

    async def spent(self, workspace_id: str, day: str | None = None) -> Decimal: ...


class InMemoryDailyBudgetLedger:
    """Process-local ledger for tests and single-process local development."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        self._reservations: dict[str, BudgetReservation] = {}
        self._reservation_limits: dict[str, Decimal] = {}
        self._resolutions: dict[str, BudgetResolution] = {}

    async def reserve(
        self,
        workspace_id: str,
        amount_usd: Decimal,
        daily_limit_usd: Decimal,
    ) -> BudgetReservation:
        day = utc_day()
        key = (workspace_id, day)
        async with self._lock:
            next_total = self._totals[key] + amount_usd
            if next_total > daily_limit_usd:
                raise BudgetExceededError(
                    f"Orçamento diário do workspace excederia USD {daily_limit_usd}."
                )
            reservation = BudgetReservation(
                reservation_id=str(uuid4()),
                workspace_id=workspace_id,
                amount_usd=amount_usd,
                day=day,
            )
            self._totals[key] = next_total
            self._reservations[reservation.reservation_id] = reservation
            self._reservation_limits[reservation.reservation_id] = daily_limit_usd
            return reservation

    async def settle(self, reservation: BudgetReservation, actual_usd: Decimal) -> Decimal:
        async with self._lock:
            normalized_actual = max(actual_usd, Decimal("0"))
            existing = self._reservations.get(reservation.reservation_id)
            key = (reservation.workspace_id, reservation.day)
            if existing is None:
                resolution = self._resolutions.get(reservation.reservation_id)
                if resolution is not None:
                    if (
                        resolution.action != "settled"
                        or resolution.actual_usd != normalized_actual
                    ):
                        raise BudgetResolutionConflictError(
                            "A reserva de orçamento já foi resolvida com outro resultado."
                        )
                    return resolution.total_usd
                return self._totals[key]
            daily_limit = self._reservation_limits[reservation.reservation_id]
            next_total = max(
                Decimal("0"),
                self._totals[key] - existing.amount_usd + normalized_actual,
            )
            if next_total > daily_limit:
                # The provider has already been called, so retain the reservation
                # and fail closed. A durable reconciliation can then settle the
                # real charge without admitting any additional spend.
                raise BudgetExceededError(
                    "O custo real excede o limite diário reservado; "
                    "a cobrança exige conciliação."
                )
            self._reservations.pop(reservation.reservation_id, None)
            self._reservation_limits.pop(reservation.reservation_id, None)
            self._totals[key] = next_total
            self._resolutions[reservation.reservation_id] = BudgetResolution(
                action="settled",
                actual_usd=normalized_actual,
                total_usd=self._totals[key],
            )
            return self._totals[key]

    async def release(self, reservation: BudgetReservation) -> Decimal:
        async with self._lock:
            existing = self._reservations.pop(reservation.reservation_id, None)
            self._reservation_limits.pop(reservation.reservation_id, None)
            key = (reservation.workspace_id, reservation.day)
            if existing is None:
                resolution = self._resolutions.get(reservation.reservation_id)
                if resolution is not None:
                    if resolution.action != "released":
                        raise BudgetResolutionConflictError(
                            "A reserva de orçamento já foi resolvida com outro resultado."
                        )
                    return resolution.total_usd
                return self._totals[key]
            if existing is not None:
                self._totals[key] = max(
                    Decimal("0"),
                    self._totals[key] - existing.amount_usd,
                )
            self._resolutions[reservation.reservation_id] = BudgetResolution(
                action="released",
                actual_usd=Decimal("0"),
                total_usd=self._totals[key],
            )
            return self._totals[key]

    async def has_reservation(self, reservation: BudgetReservation) -> bool:
        async with self._lock:
            existing = self._reservations.get(reservation.reservation_id)
            return existing == reservation

    async def resolution(
        self, reservation: BudgetReservation
    ) -> BudgetResolution | None:
        async with self._lock:
            return self._resolutions.get(reservation.reservation_id)

    async def spent(self, workspace_id: str, day: str | None = None) -> Decimal:
        async with self._lock:
            return self._totals[(workspace_id, day or utc_day())]


class RedisDailyBudgetLedger:
    """Cross-process ledger using atomic Redis Lua scripts."""

    _RESERVE = """
local total = tonumber(redis.call('HGET', KEYS[1], 'total') or '0')
local amount = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
if total + amount > limit then return {0, tostring(total)} end
redis.call('HSET', KEYS[1], 'total', tostring(total + amount))
redis.call('HSET', KEYS[1], 'r:' .. ARGV[3], ARGV[1] .. '|' .. ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
return {1, tostring(total + amount)}
"""
    _SETTLE = """
local field = 'r:' .. ARGV[1]
local resolution_field = 's:' .. ARGV[1]
local resolved = redis.call('HGET', KEYS[1], resolution_field)
if resolved then return {'resolved', resolved} end
local encoded = redis.call('HGET', KEYS[1], field)
local total = tonumber(redis.call('HGET', KEYS[1], 'total') or '0')
if not encoded then return {'missing', tostring(total)} end
local separator = string.find(encoded, '|', 1, true)
local reserved
local limit
if separator then
  reserved = tonumber(string.sub(encoded, 1, separator - 1))
  limit = tonumber(string.sub(encoded, separator + 1))
else
  reserved = tonumber(encoded)
  limit = reserved
end
if not reserved or not limit or reserved < 0 or limit < 0 then
  return {'invalid', tostring(total)}
end
local actual = tonumber(ARGV[2])
local next = math.max(0, total - reserved + actual)
if next > limit then return {'over_limit', tostring(total)} end
redis.call('HSET', KEYS[1], 'total', tostring(next))
redis.call('HSET', KEYS[1], resolution_field, 'settled|' .. ARGV[2] .. '|' .. tostring(next))
redis.call('HDEL', KEYS[1], field)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return {'applied', tostring(next)}
"""
    _RELEASE = """
local field = 'r:' .. ARGV[1]
local resolution_field = 's:' .. ARGV[1]
local resolved = redis.call('HGET', KEYS[1], resolution_field)
if resolved then return {'resolved', resolved} end
local encoded = redis.call('HGET', KEYS[1], field)
local total = tonumber(redis.call('HGET', KEYS[1], 'total') or '0')
if not encoded then return {'missing', tostring(total)} end
local separator = string.find(encoded, '|', 1, true)
local reserved = tonumber(separator and string.sub(encoded, 1, separator - 1) or encoded)
if not reserved or reserved < 0 then return {'invalid', tostring(total)} end
local next = math.max(0, total - reserved)
redis.call('HSET', KEYS[1], 'total', tostring(next))
redis.call('HSET', KEYS[1], resolution_field, 'released|0|' .. tostring(next))
redis.call('HDEL', KEYS[1], field)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return {'applied', tostring(next)}
"""

    def __init__(
        self,
        redis_url: str,
        *,
        ttl_seconds: int = 3 * 24 * 60 * 60,
        client: object | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._client = client
        self._ttl_seconds = ttl_seconds

    async def _redis(self):
        if self._client is not None:
            return self._client
        return await get_shared_redis_client(self._redis_url, decode_responses=True)

    @staticmethod
    def _key(workspace_id: str, day: str) -> str:
        digest = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:24]
        return f"klm:ai:budget:{day}:{digest}"

    async def reserve(
        self,
        workspace_id: str,
        amount_usd: Decimal,
        daily_limit_usd: Decimal,
    ) -> BudgetReservation:
        day = utc_day()
        reservation = BudgetReservation(str(uuid4()), workspace_id, amount_usd, day)
        redis = await self._redis()
        result = await redis.eval(
            self._RESERVE,
            1,
            self._key(workspace_id, day),
            str(amount_usd),
            str(daily_limit_usd),
            reservation.reservation_id,
            self._ttl_seconds,
        )
        if int(result[0]) != 1:
            raise BudgetExceededError(
                f"Orçamento diário do workspace excederia USD {daily_limit_usd}."
            )
        return reservation

    async def settle(self, reservation: BudgetReservation, actual_usd: Decimal) -> Decimal:
        normalized_actual = max(actual_usd, Decimal("0"))
        redis = await self._redis()
        result = await redis.eval(
            self._SETTLE,
            1,
            self._key(reservation.workspace_id, reservation.day),
            reservation.reservation_id,
            str(normalized_actual),
            self._ttl_seconds,
        )
        state, value = str(result[0]), str(result[1])
        if state == "resolved":
            resolution = self._decode_resolution(value)
            if resolution.action != "settled" or resolution.actual_usd != normalized_actual:
                raise BudgetResolutionConflictError(
                    "A reserva de orçamento já foi resolvida com outro resultado."
                )
            return resolution.total_usd
        if state == "over_limit":
            raise BudgetExceededError(
                "O custo real excede o limite diário reservado; "
                "a cobrança exige conciliação."
            )
        if state == "invalid":
            raise BudgetResolutionConflictError(
                "A reserva de orçamento persistida é inválida."
            )
        return Decimal(value)

    async def release(self, reservation: BudgetReservation) -> Decimal:
        redis = await self._redis()
        result = await redis.eval(
            self._RELEASE,
            1,
            self._key(reservation.workspace_id, reservation.day),
            reservation.reservation_id,
            self._ttl_seconds,
        )
        state, value = str(result[0]), str(result[1])
        if state == "resolved":
            resolution = self._decode_resolution(value)
            if resolution.action != "released":
                raise BudgetResolutionConflictError(
                    "A reserva de orçamento já foi resolvida com outro resultado."
                )
            return resolution.total_usd
        return Decimal(value)

    async def has_reservation(self, reservation: BudgetReservation) -> bool:
        redis = await self._redis()
        value = await redis.hget(
            self._key(reservation.workspace_id, reservation.day),
            f"r:{reservation.reservation_id}",
        )
        if value is None:
            return False
        try:
            encoded = str(value).split("|", 1)[0]
            return Decimal(encoded) == reservation.amount_usd
        except ArithmeticError:
            return False

    @staticmethod
    def _decode_resolution(value: str) -> BudgetResolution:
        action, actual, total = value.split("|", 2)
        if action not in {"settled", "released"}:
            raise BudgetResolutionConflictError(
                "O ledger contém uma resolução de orçamento inválida."
            )
        normalized_action: Literal["settled", "released"] = (
            "settled" if action == "settled" else "released"
        )
        return BudgetResolution(
            action=normalized_action,
            actual_usd=Decimal(actual),
            total_usd=Decimal(total),
        )

    async def resolution(
        self, reservation: BudgetReservation
    ) -> BudgetResolution | None:
        redis = await self._redis()
        value = await redis.hget(
            self._key(reservation.workspace_id, reservation.day),
            f"s:{reservation.reservation_id}",
        )
        return self._decode_resolution(str(value)) if value is not None else None

    async def spent(self, workspace_id: str, day: str | None = None) -> Decimal:
        redis = await self._redis()
        result = await redis.hget(self._key(workspace_id, day or utc_day()), "total")
        return Decimal(str(result or "0"))
