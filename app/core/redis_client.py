"""Process-wide Redis clients backed by reusable async connection pools."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from threading import Lock
from typing import Any

import redis.asyncio as aioredis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

_clients: dict[tuple[Any, ...], tuple[aioredis.Redis, asyncio.AbstractEventLoop]] = {}
_inflight: dict[tuple[Any, ...], asyncio.Task[Any]] = {}
_clients_lock = Lock()
_DEFAULT_SOCKET_CONNECT_TIMEOUT_SECONDS = 2.0
_DEFAULT_SOCKET_TIMEOUT_SECONDS = 3.0
_DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS = 30
_DEFAULT_RETRY_RETRIES = 2
_DEFAULT_RETRY_BASE_SECONDS = 0.05
_DEFAULT_RETRY_CAP_SECONDS = 0.2


def _new_default_retry() -> Retry:
    """Build an isolated retry state for each retained Redis client."""
    return Retry(
        ExponentialBackoff(
            cap=_DEFAULT_RETRY_CAP_SECONDS,
            base=_DEFAULT_RETRY_BASE_SECONDS,
        ),
        retries=_DEFAULT_RETRY_RETRIES,
    )


async def _close_client(client: Any) -> None:
    close = getattr(client, "aclose", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def get_shared_redis_client(
    url: str,
    *,
    decode_responses: bool = True,
    encoding: str | None = None,
    factory: Callable[..., Any] | None = None,
    **client_options: Any,
) -> aioredis.Redis:
    """Return one retained client per event loop and connection configuration."""
    owner_loop = asyncio.get_running_loop()
    resolved_factory = factory or aioredis.from_url
    uses_default_retry = "retry" not in client_options
    client_options = {
        "socket_connect_timeout": _DEFAULT_SOCKET_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": _DEFAULT_SOCKET_TIMEOUT_SECONDS,
        "health_check_interval": _DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS,
        **client_options,
    }
    options_key = tuple(sorted((name, repr(value)) for name, value in client_options.items()))
    retry_key = (
        "default",
        _DEFAULT_RETRY_RETRIES,
        _DEFAULT_RETRY_BASE_SECONDS,
        _DEFAULT_RETRY_CAP_SECONDS,
    ) if uses_default_retry else ("custom", repr(client_options["retry"]))
    key = (
        owner_loop,
        url,
        decode_responses,
        encoding,
        id(resolved_factory),
        options_key,
        retry_key,
    )
    with _clients_lock:
        existing = _clients.get(key)
    if existing is not None:
        return existing[0]

    async def create_candidate() -> Any:
        kwargs: dict[str, Any] = {"decode_responses": decode_responses}
        if encoding is not None:
            kwargs["encoding"] = encoding
        kwargs.update(client_options)
        if uses_default_retry:
            kwargs["retry"] = _new_default_retry()
        created = resolved_factory(url, **kwargs)
        return await created if inspect.isawaitable(created) else created

    with _clients_lock:
        task = _inflight.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(create_candidate())
            _inflight[key] = task
    try:
        candidate = await task
    except BaseException:
        with _clients_lock:
            if _inflight.get(key) is task and task.done():
                _inflight.pop(key, None)
        raise
    with _clients_lock:
        winner, _winner_loop = _clients.setdefault(key, (candidate, owner_loop))
        if _inflight.get(key) is task:
            _inflight.pop(key, None)
        return winner


async def close_shared_redis_clients() -> None:
    """Close retained pools exactly once during application shutdown or tests."""
    current_loop = asyncio.get_running_loop()
    with _clients_lock:
        clients_by_id: dict[int, tuple[Any, asyncio.AbstractEventLoop]] = {}
        for client, owner_loop in _clients.values():
            existing = clients_by_id.get(id(client))
            if existing is None or owner_loop is current_loop:
                clients_by_id[id(client)] = (client, owner_loop)
        clients = list(clients_by_id.values())
        inflight = list({id(task): task for task in _inflight.values()}.values())
        _clients.clear()
        _inflight.clear()

    for task in inflight:
        if task.done():
            continue
        owner_loop = task.get_loop()
        if owner_loop is current_loop:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif owner_loop.is_running():
            owner_loop.call_soon_threadsafe(task.cancel)

    for client, owner_loop in clients:
        if owner_loop is current_loop or not owner_loop.is_running():
            try:
                await _close_client(client)
            except RuntimeError as exc:
                # A closed loop has already torn down its transports. Redis can
                # still raise while disposing the now-unusable writer object.
                if not owner_loop.is_closed() or "Event loop is closed" not in str(exc):
                    raise
            continue

        future = asyncio.run_coroutine_threadsafe(_close_client(client), owner_loop)
        await asyncio.wrap_future(future)
