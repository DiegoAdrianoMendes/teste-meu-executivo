"""Safe model telemetry: metadata only, never prompts, responses, or credentials."""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass
from typing import Protocol

from app.core.logging import logger
from app.core.redis_client import get_shared_redis_client

_SAFE_PROVIDER_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/=-]{0,199}\Z")


@dataclass(frozen=True, slots=True)
class ModelTelemetryEvent:
    request_id: str
    workspace_ref: str
    agent_name: str
    task_class: str
    provider: str
    model: str
    outcome: str
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: str | None = None
    estimated_cost_usd: str | None = None
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    route_key: str = ""
    provider_request_id: str = ""
    request_started: bool = False
    billing_unknown: bool = False
    price_catalog_version: str = ""
    route_catalog_version: str = ""
    fallback_used: bool = False
    cache_hit: bool = False
    operation: str = ""
    scope_type: str = "workspace"
    matter_ref: str = ""
    context_digest: str = ""
    playbook_ref: str = ""
    source_refs_digest: str = ""
    source_count: int = 0
    attribution_complete: bool = False
    prompt_cache_policy: str = "off"


class TelemetrySink(Protocol):
    async def require_ready(self) -> None: ...

    async def emit(self, event: ModelTelemetryEvent) -> None: ...


def workspace_ref(workspace_id: str) -> str:
    return hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:16]


def scoped_ref(workspace_id: str, value: str, *, namespace: str) -> str:
    material = f"{namespace}:{workspace_id}:{value}".encode("utf-8", errors="replace")
    return hashlib.sha256(material).hexdigest()[:20]


def source_refs_digest(workspace_id: str, values: tuple[str, ...]) -> str:
    if not values:
        return ""
    material = "\n".join(sorted(values))
    return scoped_ref(workspace_id, material, namespace="sources")


def safe_provider_request_id(value: object) -> str:
    """Keep ordinary provider IDs intact and hash unexpected metadata shapes."""
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    if _SAFE_PROVIDER_REQUEST_ID.fullmatch(candidate):
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"sha256:{digest}"


class LoggingTelemetrySink:
    async def require_ready(self) -> None:
        return None

    async def emit(self, event: ModelTelemetryEvent) -> None:
        logger.info(
            "ai_gateway.call",
            request_id=event.request_id,
            workspace_ref=event.workspace_ref,
            agent_name=event.agent_name,
            task_class=event.task_class,
            provider=event.provider,
            model=event.model,
            outcome=event.outcome,
            latency_ms=event.latency_ms,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            cached_input_tokens=event.cached_input_tokens,
            cost_usd=event.cost_usd,
            estimated_cost_usd=event.estimated_cost_usd,
            estimated_input_tokens=event.estimated_input_tokens,
            estimated_output_tokens=event.estimated_output_tokens,
            route_key=event.route_key,
            provider_request_id=event.provider_request_id,
            request_started=event.request_started,
            billing_unknown=event.billing_unknown,
            price_catalog_version=event.price_catalog_version,
            route_catalog_version=event.route_catalog_version,
            fallback_used=event.fallback_used,
            cache_hit=event.cache_hit,
            operation=event.operation,
            scope_type=event.scope_type,
            matter_ref=event.matter_ref,
            context_digest=event.context_digest,
            playbook_ref=event.playbook_ref,
            source_refs_digest=event.source_refs_digest,
            source_count=event.source_count,
            attribution_complete=event.attribution_complete,
            prompt_cache_policy=event.prompt_cache_policy,
        )


class InMemoryTelemetrySink:
    def __init__(self) -> None:
        self.events: list[ModelTelemetryEvent] = []

    async def require_ready(self) -> None:
        return None

    async def emit(self, event: ModelTelemetryEvent) -> None:
        self.events.append(event)


class RedisStreamTelemetrySink:
    """Bounded, cross-process telemetry stream containing metadata only."""

    def __init__(
        self,
        redis_url: str,
        *,
        stream_key: str = "klm:ai:telemetry:v1",
        max_length: int = 100_000,
        readiness_ttl_seconds: float = 5.0,
        client: object | None = None,
    ) -> None:
        if max_length <= 0 or readiness_ttl_seconds <= 0:
            raise ValueError("Telemetry stream bounds must be positive.")
        self._redis_url = redis_url
        self._stream_key = stream_key
        self._max_length = max_length
        self._readiness_ttl_seconds = readiness_ttl_seconds
        self._client = client
        self._ready_until = 0.0

    async def _redis(self):
        if self._client is not None:
            return self._client
        return await get_shared_redis_client(self._redis_url, decode_responses=True)

    async def require_ready(self) -> None:
        now = time.monotonic()
        if now < self._ready_until:
            return
        redis = await self._redis()
        if not await redis.ping():
            raise RuntimeError("Redis telemetry stream is unavailable.")
        self._ready_until = now + self._readiness_ttl_seconds

    async def emit(self, event: ModelTelemetryEvent) -> None:
        redis = await self._redis()
        fields = {
            key: "" if value is None else str(int(value)) if isinstance(value, bool) else str(value)
            for key, value in asdict(event).items()
        }
        await redis.xadd(
            self._stream_key,
            fields,
            maxlen=self._max_length,
            approximate=True,
        )


class CompositeTelemetrySink:
    def __init__(self, *sinks: TelemetrySink) -> None:
        if not sinks:
            raise ValueError("Composite telemetry requires at least one sink.")
        self._sinks = sinks

    async def require_ready(self) -> None:
        for sink in self._sinks:
            await sink.require_ready()

    async def emit(self, event: ModelTelemetryEvent) -> None:
        for sink in self._sinks:
            await sink.emit(event)
