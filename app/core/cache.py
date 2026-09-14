"""
Cache Redis — camada de cache para reduzir latência e carga no banco.

Targets de maior impacto:
  - workspace settings    (lido em todo request autenticado) → TTL 5min
  - workspace memberships (lido em todo request autenticado) → TTL 10min
  - flow templates        (quase nunca mudam)                → TTL 1h
  - kanban columns state  (muda com frequência média)        → TTL 30s

Usa Redis DB 3 (separado de Celery broker=1 e results=2).
"""
import json
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.core.redis_client import get_shared_redis_client

# Redis DB 3 para cache de aplicação
_CACHE_DB = 3


def _get_redis_cache_url() -> str:
    """Troca o DB index da REDIS_URL para o DB de cache."""
    url = settings.REDIS_URL
    # redis://host:port/0 → redis://host:port/3
    parts = url.rsplit("/", 1)
    return f"{parts[0]}/{_CACHE_DB}"


async def get_redis() -> aioredis.Redis:
    """Retorna o cliente Redis de cache (singleton lazy)."""
    return await get_shared_redis_client(
        _get_redis_cache_url(),
        encoding="utf-8",
        decode_responses=True,
        factory=aioredis.from_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


async def cache_get(key: str) -> Any | None:
    """Busca um valor do cache. Retorna None se não existir ou erro."""
    if settings.uses_in_memory_redis:
        return None
    try:
        r = await get_redis()
        raw = await r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug(
            "cache.get_failed",
            cache_key_ref=opaque_ref(key, kind="cache_key"),
            error_type=safe_error_type(e),
        )
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int = 300) -> None:
    """Salva um valor no cache com TTL."""
    if settings.uses_in_memory_redis:
        return
    try:
        r = await get_redis()
        await r.setex(key, ttl_seconds, json.dumps(value, default=str))
    except Exception as e:
        logger.debug(
            "cache.set_failed",
            cache_key_ref=opaque_ref(key, kind="cache_key"),
            error_type=safe_error_type(e),
        )


async def cache_delete(key: str) -> None:
    """Remove uma chave do cache."""
    if settings.uses_in_memory_redis:
        return
    try:
        r = await get_redis()
        await r.delete(key)
    except Exception as e:
        logger.debug(
            "cache.delete_failed",
            cache_key_ref=opaque_ref(key, kind="cache_key"),
            error_type=safe_error_type(e),
        )


async def cache_delete_pattern(pattern: str) -> None:
    """Remove todas as chaves que batem com o padrão (ex: 'workspace:abc:*')."""
    if settings.uses_in_memory_redis:
        return
    try:
        r = await get_redis()
        batch: list[str] = []
        async for key in r.scan_iter(match=pattern, count=500):
            batch.append(key)
            if len(batch) >= 500:
                await r.delete(*batch)
                batch.clear()
        if batch:
            await r.delete(*batch)
    except Exception as e:
        logger.debug(
            "cache.delete_pattern_failed",
            cache_pattern_ref=opaque_ref(pattern, kind="cache_pattern"),
            error_type=safe_error_type(e),
        )


# ── Helpers com chaves padronizadas ──────────────────────────────────────────

def key_workspace_settings(workspace_id: UUID) -> str:
    return f"workspace:{workspace_id}:settings"

def key_workspace_membership(workspace_id: UUID, user_id: UUID) -> str:
    return f"workspace:{workspace_id}:member:{user_id}"

def key_flow_templates() -> str:
    return "flows:templates"

def key_kanban_counts(workspace_id: UUID) -> str:
    return f"workspace:{workspace_id}:kanban:counts"


async def invalidate_workspace_cache(workspace_id: UUID) -> None:
    """Invalida todo o cache de um workspace (chamar após updates)."""
    await cache_delete_pattern(f"workspace:{workspace_id}:*")
    logger.debug("cache.workspace_invalidated", workspace_id=str(workspace_id))
