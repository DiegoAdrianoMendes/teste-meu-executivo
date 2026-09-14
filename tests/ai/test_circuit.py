from __future__ import annotations

import pytest

from app.ai.circuit import RedisCircuitBreaker
from app.ai.errors import CircuitOpenError


class FakeRedis:
    def __init__(self, acquire_states: list[int]) -> None:
        self.acquire_states = acquire_states
        self.deleted: list[str] = []
        self.released: list[tuple[str, str]] = []
        self.failures = 0

    async def eval(self, script, key_count, key, *args):
        assert key_count == 1
        if "HSETNX" in script:
            return self.acquire_states.pop(0)
        self.failures += 1
        return self.failures

    async def hdel(self, key, field):
        self.released.append((key, field))

    async def delete(self, key):
        self.deleted.append(key)


@pytest.mark.asyncio
async def test_redis_circuit_maps_closed_open_and_half_open_states() -> None:
    redis = FakeRedis([0, -1, -2, 1])
    circuit = RedisCircuitBreaker(
        "redis://unused",
        failure_threshold=2,
        cooldown_seconds=30,
        client=redis,
    )

    assert await circuit.acquire("openai:model") is False
    with pytest.raises(CircuitOpenError, match="temporariamente"):
        await circuit.acquire("openai:model")
    with pytest.raises(CircuitOpenError, match="meia abertura"):
        await circuit.acquire("openai:model")
    assert await circuit.acquire("openai:model") is True

    await circuit.release("openai:model")
    await circuit.failure("openai:model")
    await circuit.success("openai:model")

    assert redis.failures == 1
    assert redis.released[0][1] == "half_open_probe"
    assert len(redis.deleted) == 1
