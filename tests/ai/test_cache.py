from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.cache import InMemoryResponseCache, RedisResponseCache
from app.ai.models import CacheEntry, ProviderResponse


def entry(text: str) -> CacheEntry:
    return CacheEntry(
        response=ProviderResponse(text=text, model="model-a", input_tokens=1, output_tokens=1),
        cost_usd=Decimal("0.01"),
        structured_data={"text": text},
    )


@pytest.mark.asyncio
async def test_memory_cache_is_bounded_lru() -> None:
    cache = InMemoryResponseCache(max_entries=2, max_value_bytes=1_000)
    await cache.set("a", entry("a"), 60)
    await cache.set("b", entry("b"), 60)
    assert await cache.get("a") is not None

    await cache.set("c", entry("c"), 60)

    assert await cache.get("a") is not None
    assert await cache.get("b") is None
    assert await cache.get("c") is not None


@pytest.mark.asyncio
async def test_memory_cache_refuses_oversized_values() -> None:
    cache = InMemoryResponseCache(max_entries=2, max_value_bytes=8)
    await cache.set("large", entry("too-large"), 60)
    assert await cache.get("large") is None


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int):
        assert ex > 0
        self.values[key] = value

    async def delete(self, key: str):
        self.values.pop(key, None)


@pytest.mark.asyncio
async def test_redis_cache_round_trip_and_corruption_eviction() -> None:
    redis = FakeRedis()
    cache = RedisResponseCache("redis://unused", client=redis)
    await cache.set("a", entry("ok"), 60)

    restored = await cache.get("a")
    assert restored is not None
    assert restored.response.text == "ok"
    assert restored.cost_usd == Decimal("0.01")

    redis.values["klm:ai:response-cache:a"] = "not-json"
    assert await cache.get("a") is None
    assert "klm:ai:response-cache:a" not in redis.values
