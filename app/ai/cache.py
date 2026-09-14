"""Opt-in response cache that refuses sensitive model requests."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.ai.models import CacheEntry, ModelRequest, ProviderResponse, RouteCandidate
from app.core.redis_client import get_shared_redis_client


class ResponseCache(Protocol):
    async def get(self, key: str) -> CacheEntry | None: ...

    async def set(self, key: str, value: CacheEntry, ttl_seconds: int) -> None: ...


class NullResponseCache:
    async def get(self, key: str) -> CacheEntry | None:
        return None

    async def set(self, key: str, value: CacheEntry, ttl_seconds: int) -> None:
        return None


@dataclass(slots=True)
class _StoredEntry:
    value: CacheEntry
    expires_at: float


class InMemoryResponseCache:
    def __init__(
        self,
        *,
        max_entries: int = 1_024,
        max_value_bytes: int = 1_048_576,
    ) -> None:
        if max_entries <= 0 or max_value_bytes <= 0:
            raise ValueError("Response cache bounds must be positive.")
        self._items: OrderedDict[str, _StoredEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._max_entries = max_entries
        self._max_value_bytes = max_value_bytes

    @staticmethod
    def _value_size(value: CacheEntry) -> int:
        structured = json.dumps(
            value.structured_data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return len(value.response.text.encode("utf-8")) + len(structured.encode("utf-8"))

    async def get(self, key: str) -> CacheEntry | None:
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if item.expires_at <= time.monotonic():
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return item.value

    async def set(self, key: str, value: CacheEntry, ttl_seconds: int) -> None:
        if ttl_seconds <= 0 or self._value_size(value) > self._max_value_bytes:
            return
        async with self._lock:
            self._items[key] = _StoredEntry(value, time.monotonic() + ttl_seconds)
            self._items.move_to_end(key)
            now = time.monotonic()
            expired = [
                stored_key
                for stored_key, item in self._items.items()
                if item.expires_at <= now
            ]
            for stored_key in expired:
                self._items.pop(stored_key, None)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)


class RedisResponseCache:
    """Cross-process cache for explicitly reusable, non-sensitive responses."""

    _VERSION = 1

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "klm:ai:response-cache:",
        max_value_bytes: int = 1_048_576,
        client: object | None = None,
    ) -> None:
        if max_value_bytes <= 0:
            raise ValueError("Response cache max_value_bytes must be positive.")
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._max_value_bytes = max_value_bytes
        self._client = client

    async def _redis(self):
        if self._client is not None:
            return self._client
        return await get_shared_redis_client(self._redis_url, decode_responses=True)

    def _key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    @classmethod
    def _encode(cls, value: CacheEntry) -> str:
        response = value.response
        return json.dumps(
            {
                "version": cls._VERSION,
                "response": {
                    "text": response.text,
                    "model": response.model,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cached_input_tokens": response.cached_input_tokens,
                    "provider_request_id": response.provider_request_id,
                    "finish_reason": response.finish_reason,
                },
                "cost_usd": str(value.cost_usd),
                "structured_data": value.structured_data,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def _decode(cls, raw: str) -> CacheEntry:
        payload = json.loads(raw)
        if payload.get("version") != cls._VERSION:
            raise ValueError("Unsupported response cache version.")
        response = payload["response"]
        return CacheEntry(
            response=ProviderResponse(
                text=str(response["text"]),
                model=str(response["model"]),
                input_tokens=int(response["input_tokens"]),
                output_tokens=int(response["output_tokens"]),
                cached_input_tokens=int(response.get("cached_input_tokens") or 0),
                provider_request_id=str(response.get("provider_request_id") or ""),
                finish_reason=str(response.get("finish_reason") or ""),
            ),
            cost_usd=Decimal(str(payload["cost_usd"])),
            structured_data=payload.get("structured_data"),
        )

    async def get(self, key: str) -> CacheEntry | None:
        redis = await self._redis()
        raw = await redis.get(self._key(key))
        if raw is None:
            return None
        try:
            return self._decode(str(raw))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await redis.delete(self._key(key))
            return None

    async def set(self, key: str, value: CacheEntry, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        encoded = self._encode(value)
        if len(encoded.encode("utf-8")) > self._max_value_bytes:
            return
        redis = await self._redis()
        await redis.set(self._key(key), encoded, ex=ttl_seconds)


def cache_key(request: ModelRequest, route: RouteCandidate) -> str | None:
    if request.media_parts or request.contains_sensitive_data or not request.allow_response_cache:
        return None
    material = {
        "workspace_id": request.workspace_id,
        "agent": request.agent_name,
        "route": route.key,
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
        "messages": [message.to_dict() for message in request.messages],
        "schema": request.response_schema,
        "prompt_cache_policy": request.prompt_cache_policy.value,
        "prompt_cache_key": request.prompt_cache_key,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
