"""Rate limit persistente e temporização uniforme para endpoints de identidade."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic

from fastapi import Request
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import AUTH_DUMMY_PASSWORD_HASH, verify_password_async
from app.models.auth_security import (
    AuthRateLimitEvent,
    AuthRateLimitScope,
    AuthSecurityEvent,
)
from app.models.identity import AuthRateLimitBucket
from app.models.user import AgentType, User, normalize_email


@dataclass(frozen=True)
class RateLimitPolicy:
    window_seconds: int
    identifier_limit: int
    network_limit: int
    minimum_response_seconds: float


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int


RATE_LIMIT_POLICIES = {
    AuthRateLimitScope.login: RateLimitPolicy(15 * 60, 5, 30, 0.35),
    AuthRateLimitScope.register: RateLimitPolicy(60 * 60, 4, 20, 0.35),
    AuthRateLimitScope.verify_signup: RateLimitPolicy(15 * 60, 8, 40, 0.25),
    AuthRateLimitScope.invitation: RateLimitPolicy(15 * 60, 10, 50, 0.1),
    AuthRateLimitScope.accept_invitation: RateLimitPolicy(15 * 60, 3, 40, 0.25),
    AuthRateLimitScope.forgot_password: RateLimitPolicy(15 * 60, 3, 20, 0.35),
    AuthRateLimitScope.reset_password: RateLimitPolicy(15 * 60, 5, 30, 0.35),
}
REGISTER_ENUMERATION_FLOOR_SECONDS = 0.35
_RETENTION = timedelta(days=1)


def opaque_auth_digest(namespace: str, value: str) -> str:
    context = f"klm-auth:{namespace}:{value}".encode()
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        context,
        hashlib.sha256,
    ).hexdigest()


def request_network_key(request: Request) -> str:
    """Usa somente o peer observado; headers de proxy não confiáveis são ignorados."""
    return request.client.host if request.client else "unknown"


def request_audit_digests(request: Request) -> tuple[str, str | None]:
    network_digest = opaque_auth_digest("network", request_network_key(request))
    user_agent = request.headers.get("user-agent")
    user_agent_digest = (
        opaque_auth_digest("user-agent", user_agent[:512]) if user_agent else None
    )
    return network_digest, user_agent_digest


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def credentials_match_active_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
) -> bool:
    """Executa um bcrypt para toda falha, inclusive identidade inexistente."""
    normalized_email = normalize_email(email)
    row = (
        await db.execute(
            select(
                User.hashed_password,
                User.is_active,
                User.email_verified_at,
            ).where(
                func.lower(User.email) == normalized_email,
                User.agent_type == AgentType.human,
                User.deleted_at.is_(None),
            )
        )
    ).one_or_none()
    encoded_password = password.encode()
    password_too_long = len(encoded_password) > 72
    safe_password = (
        encoded_password[:72].decode(errors="ignore")
        if password_too_long
        else password
    )
    candidate_hash = (
        row.hashed_password if row and row.hashed_password else AUTH_DUMMY_PASSWORD_HASH
    )
    try:
        password_matches = await verify_password_async(
            safe_password,
            candidate_hash,
        )
    except (TypeError, ValueError):
        password_matches = await verify_password_async(
            safe_password,
            AUTH_DUMMY_PASSWORD_HASH,
        )
        return False
    return bool(
        row
        and row.hashed_password
        and row.is_active
        and row.email_verified_at is not None
        and password_matches
        and not password_too_long
    )


class PersistentAuthRateLimiter:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def check(
        self,
        scope: AuthRateLimitScope,
        *,
        identifier: str,
        network: str,
    ) -> RateLimitDecision:
        policy = RATE_LIMIT_POLICIES[scope]
        now = datetime.now(UTC)
        identifier_digest = opaque_auth_digest(
            f"rate-limit:{scope.value}:identifier",
            identifier,
        )
        network_digest = opaque_auth_digest("network", network)

        if self.db.get_bind().dialect.name == "postgresql":
            lock_keys = sorted(
                {
                    self._advisory_key(scope, "identifier", identifier_digest),
                    self._advisory_key(scope, "network", network_digest),
                }
            )
            for lock_key in lock_keys:
                await self.db.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": lock_key},
                )

        await self.db.execute(
            delete(AuthRateLimitEvent).where(
                AuthRateLimitEvent.occurred_at < now - _RETENTION
            )
        )
        identifier_bucket = await self._increment_bucket(
            scope,
            key_type="identifier",
            key_digest=identifier_digest,
            now=now,
            policy=policy,
        )
        network_bucket = await self._increment_bucket(
            scope,
            key_type="network",
            key_digest=network_digest,
            now=now,
            policy=policy,
        )
        identifier_limited = identifier_bucket.attempt_count > policy.identifier_limit
        network_limited = network_bucket.attempt_count > policy.network_limit
        limited = identifier_limited or network_limited

        event = AuthRateLimitEvent(
            scope=scope,
            identifier_digest=identifier_digest,
            network_digest=network_digest,
            was_limited=limited,
            occurred_at=now,
        )
        self.db.add(event)
        await self.db.flush()

        if limited:
            self.db.add(
                AuthSecurityEvent(
                    event_type=f"{scope.value}_rate_limited",
                    outcome="blocked",
                    network_digest=network_digest,
                    details={
                        "identifier_limit": policy.identifier_limit,
                        "network_limit": policy.network_limit,
                    },
                )
            )
        await self.db.commit()

        oldest_candidates = [
            bucket.window_started_at
            for bucket, active in (
                (identifier_bucket, identifier_limited),
                (network_bucket, network_limited),
            )
            if active
        ]
        retry_after = 0
        if oldest_candidates:
            oldest = min(_as_utc(value) for value in oldest_candidates)
            retry_after = max(
                1,
                math.ceil(policy.window_seconds - (now - oldest).total_seconds()),
            )
        return RateLimitDecision(allowed=not limited, retry_after=retry_after)

    async def _increment_bucket(
        self,
        scope: AuthRateLimitScope,
        *,
        key_type: str,
        key_digest: str,
        now: datetime,
        policy: RateLimitPolicy,
    ) -> AuthRateLimitBucket:
        bucket = await self.db.get(
            AuthRateLimitBucket,
            {"scope": scope, "key_type": key_type, "key_digest": key_digest},
            with_for_update=True,
        )
        if bucket is None:
            bucket = AuthRateLimitBucket(
                scope=scope,
                key_type=key_type,
                key_digest=key_digest,
                window_started_at=now,
                attempt_count=1,
                updated_at=now,
            )
            self.db.add(bucket)
            await self.db.flush()
            return bucket
        if now - _as_utc(bucket.window_started_at) >= timedelta(seconds=policy.window_seconds):
            bucket.window_started_at = now
            bucket.attempt_count = 1
        else:
            bucket.attempt_count += 1
        bucket.updated_at = now
        await self.db.flush()
        return bucket

    @staticmethod
    def _advisory_key(scope: AuthRateLimitScope, key_type: str, digest: str) -> int:
        unsigned = int(
            opaque_auth_digest("rate-limit-lock", f"{scope.value}:{key_type}:{digest}")[:16],
            16,
        )
        return unsigned - 2**64 if unsigned >= 2**63 else unsigned

    async def clear(
        self,
        scope: AuthRateLimitScope,
        *,
        identifier: str,
    ) -> None:
        identifier_digest = opaque_auth_digest(
            f"rate-limit:{scope.value}:identifier",
            identifier,
        )
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {
                    "lock_key": self._advisory_key(
                        scope,
                        "identifier",
                        identifier_digest,
                    )
                },
            )
        await self.db.execute(
            delete(AuthRateLimitBucket).where(
                AuthRateLimitBucket.scope == scope,
                AuthRateLimitBucket.key_type == "identifier",
                AuthRateLimitBucket.key_digest == identifier_digest,
            )
        )
        await self.db.execute(
            delete(AuthRateLimitEvent).where(
                AuthRateLimitEvent.scope == scope,
                AuthRateLimitEvent.identifier_digest == identifier_digest,
            )
        )
        await self.db.commit()


async def enforce_uniform_timing(
    started_at: float,
    scope: AuthRateLimitScope,
    *,
    security_floor_seconds: float = 0,
) -> None:
    minimum = max(
        RATE_LIMIT_POLICIES[scope].minimum_response_seconds,
        security_floor_seconds,
    )
    remaining = minimum - (monotonic() - started_at)
    if remaining > 0:
        await asyncio.sleep(remaining)


def start_uniform_timing() -> float:
    return monotonic()
