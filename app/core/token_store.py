"""
Persistência leve de revogação de tokens em Redis.
"""
from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from time import time_ns

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.redis_client import get_shared_redis_client
from app.core.security import parse_session_token_id

_local_revoked_tokens: dict[str, int] = {}
_local_revoked_user_sessions: dict[str, tuple[int, int]] = {}
_local_revoked_websocket_sessions: dict[str, int] = {}
_local_revoked_tokens_lock = Lock()

_SET_MONOTONIC_MARKER_SCRIPT = """
local current = redis.call('GET', KEYS[1]) or '0'
local marker = ARGV[1]
local marker_is_newer = string.len(marker) > string.len(current)
    or (string.len(marker) == string.len(current) and marker > current)
if marker_is_newer then
    if ARGV[2] == '' then
        redis.call('SET', KEYS[1], ARGV[1])
    else
        redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    end
    return 1
end
if ARGV[2] ~= '' then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


def _token_key(token_id: str) -> str:
    return f"auth:revoked:{token_id}"


def _user_sessions_key(user_id: str) -> str:
    return f"auth:user-revoked-before:{user_id}"


def _websocket_sessions_key(user_id: str) -> str:
    return f"auth:websocket-revoked-before:{user_id}"


def _revocation_ttl(expires_at: int | None) -> int:
    if not expires_at:
        return 60
    now = int(datetime.now(UTC).timestamp())
    return max(int(expires_at) - now, 60)


def _user_revocation_ttl() -> int:
    return max(
        settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        settings.WEBSOCKET_TICKET_EXPIRE_SECONDS,
        60,
    )


def _token_id_candidates(token_id: str) -> tuple[str, ...]:
    parsed = parse_session_token_id(token_id)
    if parsed is None or parsed[2] == token_id:
        return (token_id,)
    # O nonce preserva o jti original de JWTs legados e sua revogação anterior.
    return token_id, parsed[2]


async def _get_redis_client():
    return await get_shared_redis_client(
        settings.REDIS_URL,
        decode_responses=True,
        factory=aioredis.from_url,
    )


def _local_user_session_revoked(token_id: str | None, now: int) -> bool:
    parsed = parse_session_token_id(token_id)
    if parsed is None:
        return False
    user_id, issued_at_ns, _nonce = parsed
    revocation = _local_revoked_user_sessions.get(user_id)
    if revocation is None:
        return False
    revoked_before_ns, expires_at = revocation
    if expires_at <= now:
        _local_revoked_user_sessions.pop(user_id, None)
        return False
    return issued_at_ns <= revoked_before_ns


def _local_websocket_session_revoked(token_id: str | None) -> bool:
    parsed = parse_session_token_id(token_id)
    if parsed is None:
        return False
    user_id, issued_at_ns, _nonce = parsed
    revoked_before_ns = _local_revoked_websocket_sessions.get(user_id)
    return revoked_before_ns is not None and issued_at_ns <= revoked_before_ns


async def _set_monotonic_marker(
    client,
    key: str,
    marker: int,
    *,
    ttl: int | None,
) -> None:
    await client.eval(
        _SET_MONOTONIC_MARKER_SCRIPT,
        1,
        key,
        str(marker),
        str(ttl) if ttl is not None else "",
    )


async def _redis_user_session_revoked(client, token_id: str | None) -> bool:
    parsed = parse_session_token_id(token_id)
    if parsed is None:
        return False
    user_id, issued_at_ns, _nonce = parsed
    revoked_before_ns = await client.get(_user_sessions_key(user_id))
    return revoked_before_ns is not None and issued_at_ns <= int(revoked_before_ns)


async def revoke_user_tokens(user_id: str, *, revoked_at_ns: int | None = None) -> None:
    """Revoga todas as sessões emitidas para um usuário até este instante."""
    normalized_user_id = str(user_id).strip()
    if not normalized_user_id:
        return
    marker = time_ns() if revoked_at_ns is None else int(revoked_at_ns)
    if marker <= 0:
        raise ValueError("O marcador de revogação deve ser positivo.")
    ttl = _user_revocation_ttl()

    if settings.uses_in_memory_redis:
        now = int(datetime.now(UTC).timestamp())
        with _local_revoked_tokens_lock:
            current = _local_revoked_user_sessions.get(normalized_user_id)
            current_marker = current[0] if current else 0
            _local_revoked_user_sessions[normalized_user_id] = (
                max(marker, current_marker),
                now + ttl,
            )
        return

    client = await _get_redis_client()
    current = await client.get(_user_sessions_key(normalized_user_id))
    await client.set(
        _user_sessions_key(normalized_user_id),
        str(max(marker, int(current or 0))),
        ex=ttl,
    )


async def revoke_websocket_sessions(
    user_id: str,
    *,
    revoked_at_ns: int | None = None,
) -> None:
    """Revoga duravelmente conexões WebSocket emitidas até este instante."""
    normalized_user_id = str(user_id).strip()
    if not normalized_user_id:
        return
    marker = time_ns() if revoked_at_ns is None else int(revoked_at_ns)
    if marker <= 0:
        raise ValueError("O marcador de revogação deve ser positivo.")
    if settings.uses_in_memory_redis:
        with _local_revoked_tokens_lock:
            current_marker = _local_revoked_websocket_sessions.get(normalized_user_id, 0)
            _local_revoked_websocket_sessions[normalized_user_id] = max(
                marker,
                current_marker,
            )
        return

    client = await _get_redis_client()
    key = _websocket_sessions_key(normalized_user_id)
    await _set_monotonic_marker(client, key, marker, ttl=None)


async def is_user_session_revoked(token_id: str | None) -> bool:
    """Consulta apenas o corte por usuário, sem considerar consumo do jti."""
    if settings.uses_in_memory_redis:
        now = int(datetime.now(UTC).timestamp())
        with _local_revoked_tokens_lock:
            return _local_user_session_revoked(token_id, now)
    client = await _get_redis_client()
    return await _redis_user_session_revoked(client, token_id)


async def is_websocket_session_revoked(token_id: str | None) -> bool:
    """Consulta cortes duráveis globais e exclusivos de WebSocket."""
    if settings.uses_in_memory_redis:
        now = int(datetime.now(UTC).timestamp())
        with _local_revoked_tokens_lock:
            return _local_user_session_revoked(
                token_id,
                now,
            ) or _local_websocket_session_revoked(token_id)

    parsed = parse_session_token_id(token_id)
    if parsed is None:
        return False
    user_id, issued_at_ns, _nonce = parsed
    client = await _get_redis_client()
    if await _redis_user_session_revoked(client, token_id):
        return True
    revoked_before_ns = await client.get(_websocket_sessions_key(user_id))
    return revoked_before_ns is not None and issued_at_ns <= int(revoked_before_ns)


async def revoke_token(token_id: str, expires_at: int | None) -> None:
    if not token_id:
        return
    ttl = _revocation_ttl(expires_at)

    if settings.uses_in_memory_redis:
        with _local_revoked_tokens_lock:
            _local_revoked_tokens[token_id] = int(datetime.now(UTC).timestamp()) + ttl
        return

    client = await _get_redis_client()
    await client.set(_token_key(token_id), "1", ex=ttl)


async def consume_token(token_id: str | None, expires_at: int | None) -> bool:
    """Revoga um token atomicamente e aceita apenas o primeiro consumidor."""
    if not token_id:
        return False
    ttl = _revocation_ttl(expires_at)

    if settings.uses_in_memory_redis:
        now = int(datetime.now(UTC).timestamp())
        with _local_revoked_tokens_lock:
            if _local_user_session_revoked(token_id, now):
                return False
            for candidate in _token_id_candidates(token_id):
                current_expiry = _local_revoked_tokens.get(candidate)
                if current_expiry is not None and current_expiry > now:
                    return False
            _local_revoked_tokens[token_id] = now + ttl
            return True

    client = await _get_redis_client()
    if await _redis_user_session_revoked(client, token_id):
        return False
    for candidate in _token_id_candidates(token_id):
        if await client.get(_token_key(candidate)) is not None:
            return False
    claimed = await client.set(_token_key(token_id), "1", ex=ttl, nx=True)
    if not claimed:
        return False
    return not await _redis_user_session_revoked(client, token_id)


async def is_token_revoked(token_id: str | None) -> bool:
    if not token_id:
        return False
    if settings.uses_in_memory_redis:
        now = int(datetime.now(UTC).timestamp())
        with _local_revoked_tokens_lock:
            if _local_user_session_revoked(token_id, now):
                return True
            for candidate in _token_id_candidates(token_id):
                expires_at = _local_revoked_tokens.get(candidate)
                if expires_at is None:
                    continue
                if expires_at <= now:
                    _local_revoked_tokens.pop(candidate, None)
                    continue
                return True
            return False
    client = await _get_redis_client()
    for candidate in _token_id_candidates(token_id):
        if await client.get(_token_key(candidate)) is not None:
            return True
    return await _redis_user_session_revoked(client, token_id)
