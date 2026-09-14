"""Small in-process circuit breaker per provider/model route."""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
from dataclasses import dataclass
from typing import Protocol

from app.ai.errors import CircuitOpenError
from app.core.redis_client import get_shared_redis_client


class CircuitBreakerProtocol(Protocol):
    async def acquire(self, key: str) -> bool: ...

    async def release(self, key: str) -> None: ...

    async def success(self, key: str) -> None: ...

    async def failure(self, key: str) -> None: ...


@dataclass(slots=True)
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None
    half_open_probe: bool = False


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int, cooldown_seconds: float) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._states: dict[str, _CircuitState] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> bool:
        """Admit a call and report whether it owns the half-open probe."""
        async with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            if state.opened_at is None:
                return False
            if time.monotonic() - state.opened_at < self.cooldown_seconds:
                raise CircuitOpenError(f"Circuito temporariamente aberto para {key}.")
            if state.half_open_probe:
                raise CircuitOpenError(f"Circuito em meia abertura para {key}.")
            state.half_open_probe = True
            return True

    async def release(self, key: str) -> None:
        """Release an admitted half-open probe without changing failure history."""
        async with self._lock:
            state = self._states.get(key)
            if state is not None:
                state.half_open_probe = False

    async def success(self, key: str) -> None:
        async with self._lock:
            self._states[key] = _CircuitState()

    async def failure(self, key: str) -> None:
        async with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            state.half_open_probe = False
            state.failures += 1
            if state.failures >= self.failure_threshold:
                state.opened_at = time.monotonic()


class RedisCircuitBreaker:
    """Atomic cross-process circuit state using Redis server time."""

    _ACQUIRE = """
local opened = tonumber(redis.call('HGET', KEYS[1], 'opened_at_ms') or '-1')
if opened < 0 then return 0 end
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) * 1000 + math.floor(tonumber(now_parts[2]) / 1000)
if now - opened < tonumber(ARGV[1]) then return -1 end
if redis.call('HSETNX', KEYS[1], 'half_open_probe', '1') == 0 then return -2 end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""
    _FAILURE = """
local failures = redis.call('HINCRBY', KEYS[1], 'failures', 1)
redis.call('HDEL', KEYS[1], 'half_open_probe')
if failures >= tonumber(ARGV[1]) then
  local now_parts = redis.call('TIME')
  local now = tonumber(now_parts[1]) * 1000 + math.floor(tonumber(now_parts[2]) / 1000)
  redis.call('HSET', KEYS[1], 'opened_at_ms', tostring(now))
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return failures
"""

    def __init__(
        self,
        redis_url: str,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        client: object | None = None,
    ) -> None:
        if failure_threshold <= 0 or cooldown_seconds < 0:
            raise ValueError("Circuit breaker limits are invalid.")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._redis_url = redis_url
        self._client = client
        self._ttl_seconds = max(3_600, math.ceil(cooldown_seconds * 4))

    async def _redis(self):
        if self._client is not None:
            return self._client
        return await get_shared_redis_client(self._redis_url, decode_responses=True)

    @staticmethod
    def _key(route_key: str) -> str:
        digest = hashlib.sha256(route_key.encode("utf-8")).hexdigest()[:32]
        return f"klm:ai:circuit:{digest}"

    async def acquire(self, key: str) -> bool:
        redis = await self._redis()
        state = int(
            await redis.eval(
                self._ACQUIRE,
                1,
                self._key(key),
                math.ceil(self.cooldown_seconds * 1_000),
                self._ttl_seconds,
            )
        )
        if state == 0:
            return False
        if state == 1:
            return True
        if state == -2:
            raise CircuitOpenError(f"Circuito em meia abertura para {key}.")
        raise CircuitOpenError(f"Circuito temporariamente aberto para {key}.")

    async def release(self, key: str) -> None:
        redis = await self._redis()
        await redis.hdel(self._key(key), "half_open_probe")

    async def success(self, key: str) -> None:
        redis = await self._redis()
        await redis.delete(self._key(key))

    async def failure(self, key: str) -> None:
        redis = await self._redis()
        await redis.eval(
            self._FAILURE,
            1,
            self._key(key),
            self.failure_threshold,
            self._ttl_seconds,
        )
