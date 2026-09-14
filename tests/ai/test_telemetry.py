import pytest

from app.ai.telemetry import (
    ModelTelemetryEvent,
    RedisStreamTelemetrySink,
    safe_provider_request_id,
    scoped_ref,
    source_refs_digest,
)


def test_provider_request_id_hashes_unexpected_metadata_without_leaking_it():
    raw = "resp-1\nAuthorization: bearer-sensitive-value"

    safe = safe_provider_request_id(raw)

    assert safe.startswith("sha256:")
    assert safe == safe_provider_request_id(raw)
    assert "Authorization" not in safe
    assert "sensitive" not in safe


def test_provider_request_id_preserves_normal_reconciliation_identifier():
    assert safe_provider_request_id("resp_1234-request") == "resp_1234-request"


def test_business_references_are_scoped_and_content_free():
    matter = scoped_ref("workspace-a", "matter-sensitive", namespace="matter")
    sources = source_refs_digest("workspace-a", ("doc-b", "doc-a"))

    assert matter == scoped_ref("workspace-a", "matter-sensitive", namespace="matter")
    assert matter != scoped_ref("workspace-b", "matter-sensitive", namespace="matter")
    assert sources == source_refs_digest("workspace-a", ("doc-a", "doc-b"))
    assert "sensitive" not in matter
    assert "doc-a" not in sources


class FakeRedis:
    def __init__(self) -> None:
        self.pings = 0
        self.events = []

    async def ping(self):
        self.pings += 1
        return True

    async def xadd(self, key, fields, *, maxlen, approximate):
        self.events.append((key, fields, maxlen, approximate))


@pytest.mark.asyncio
async def test_redis_telemetry_preflights_and_persists_only_typed_metadata():
    redis = FakeRedis()
    sink = RedisStreamTelemetrySink("redis://unused", client=redis, max_length=20)
    event = ModelTelemetryEvent(
        request_id="request-a",
        workspace_ref="workspace-hash",
        agent_name="sdr",
        task_class="simple",
        provider="fake",
        model="fake-model",
        outcome="success",
        latency_ms=3,
        operation="lead.classify",
        attribution_complete=True,
    )

    await sink.require_ready()
    await sink.require_ready()
    await sink.emit(event)

    assert redis.pings == 1
    assert len(redis.events) == 1
    key, fields, maxlen, approximate = redis.events[0]
    assert key == "klm:ai:telemetry:v1"
    assert fields["workspace_ref"] == "workspace-hash"
    assert fields["attribution_complete"] == "1"
    assert maxlen == 20
    assert approximate is True
    assert "prompt" not in fields
    assert "response" not in fields
