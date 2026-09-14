from __future__ import annotations

import asyncio

import pytest

from app.core import cache
from app.core.redis_client import close_shared_redis_clients, get_shared_redis_client


class FakeRedis:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_redis_pool_and_shutdown_once():
    await close_shared_redis_clients()
    calls = 0
    created = FakeRedis()

    async def factory(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return created

    clients = await asyncio.gather(
        *(
            get_shared_redis_client(
                "redis://local.test/0",
                factory=factory,
            )
            for _ in range(40)
        )
    )

    assert calls == 1
    assert all(client is created for client in clients)
    await close_shared_redis_clients()
    assert created.close_calls == 1


@pytest.mark.asyncio
async def test_shared_redis_pool_has_bounded_socket_defaults() -> None:
    await close_shared_redis_clients()
    captured: dict[str, object] = {}

    def factory(_url: str, **kwargs):
        captured.update(kwargs)
        return FakeRedis()

    await get_shared_redis_client("redis://bounded.test/0", factory=factory)

    assert captured["socket_connect_timeout"] == 2.0
    assert captured["socket_timeout"] == 3.0
    assert captured["health_check_interval"] == 30
    assert captured["retry"] is not None
    await close_shared_redis_clients()


def test_default_retry_state_is_not_shared_across_event_loops() -> None:
    captured_retries: list[object] = []

    def factory(_url: str, **kwargs):
        captured_retries.append(kwargs["retry"])
        return FakeRedis()

    async def acquire():
        return await get_shared_redis_client(
            "redis://isolated-retry.test/0",
            factory=factory,
        )

    asyncio.run(acquire())
    asyncio.run(acquire())

    assert len(captured_retries) == 2
    assert captured_retries[0] is not captured_retries[1]
    asyncio.run(close_shared_redis_clients())


def test_clients_are_not_reused_across_event_loops_and_close_best_effort():
    created: list[FakeRedis] = []

    def factory(*_args, **_kwargs):
        client = FakeRedis()
        created.append(client)
        return client

    async def acquire():
        return await get_shared_redis_client(
            "redis://loop-local.test/0",
            factory=factory,
        )

    first = asyncio.run(acquire())
    second = asyncio.run(acquire())

    assert first is not second
    assert len(created) == 2
    asyncio.run(close_shared_redis_clients())
    assert [client.close_calls for client in created] == [1, 1]


@pytest.mark.asyncio
async def test_pattern_invalidation_uses_bounded_scan_batches(monkeypatch):
    class ScanRedis:
        def __init__(self) -> None:
            self.deleted: list[list[str]] = []

        async def scan_iter(self, *, match: str, count: int):
            assert match == "workspace:abc:*"
            assert count == 500
            for index in range(1_201):
                yield f"workspace:abc:{index}"

        async def delete(self, *keys: str) -> None:
            self.deleted.append(list(keys))

    redis = ScanRedis()
    monkeypatch.setattr(cache.settings, "KLM_LOCAL_REDIS_ENABLED", True)
    monkeypatch.setattr(cache, "get_redis", lambda: asyncio.sleep(0, result=redis))

    await cache.cache_delete_pattern("workspace:abc:*")

    assert [len(batch) for batch in redis.deleted] == [500, 500, 201]
