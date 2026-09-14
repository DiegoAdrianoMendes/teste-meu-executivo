from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.auth_security import AuthRateLimitEvent, AuthRateLimitScope
from app.models.identity import AuthRateLimitBucket
from app.services.auth_rate_limit_service import (
    RATE_LIMIT_POLICIES,
    PersistentAuthRateLimiter,
    RateLimitPolicy,
    opaque_auth_digest,
)

ADMIN_URL = os.getenv("KLM_TEST_POSTGRES_ADMIN_URL")

if not ADMIN_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_ADMIN_URL; "
        "run scripts/verify_postgresql_qa.sh."
    )


@pytest.mark.asyncio
async def test_auth_rate_limit_is_atomic_across_concurrent_postgresql_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(ADMIN_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scope = AuthRateLimitScope.register
    identifier = f"concurrent-{uuid4().hex}@example.com"
    monkeypatch.setitem(
        RATE_LIMIT_POLICIES,
        scope,
        RateLimitPolicy(
            window_seconds=60,
            identifier_limit=3,
            network_limit=100,
            minimum_response_seconds=0,
        ),
    )

    async def attempt(index: int) -> bool:
        async with sessions() as db:
            decision = await PersistentAuthRateLimiter(db).check(
                scope,
                identifier=identifier,
                network=f"198.51.100.{index + 1}",
            )
            return decision.allowed

    try:
        decisions = await asyncio.gather(*(attempt(index) for index in range(12)))
        assert decisions.count(True) == 3
        assert decisions.count(False) == 9

        identifier_digest = opaque_auth_digest(
            f"rate-limit:{scope.value}:identifier",
            identifier,
        )
        async with sessions() as db:
            bucket = await db.scalar(
                select(AuthRateLimitBucket).where(
                    AuthRateLimitBucket.scope == scope,
                    AuthRateLimitBucket.key_type == "identifier",
                    AuthRateLimitBucket.key_digest == identifier_digest,
                )
            )
            assert bucket is not None
            assert bucket.attempt_count == 12
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_accept_invitation_scope_persists_in_migrated_postgresql_schema() -> None:
    engine = create_async_engine(ADMIN_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scope = AuthRateLimitScope.accept_invitation
    identifier = f"invitation:{uuid4()}:delivery:token"
    network = "198.51.100.90"
    identifier_digest = opaque_auth_digest(
        f"rate-limit:{scope.value}:identifier",
        identifier,
    )

    try:
        async with sessions() as db:
            event_width = await db.scalar(
                text(
                    "SELECT character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'auth_rate_limit_events' "
                    "AND column_name = 'scope'"
                )
            )
            assert event_width is not None
            assert event_width >= max(len(member.value) for member in AuthRateLimitScope)

            decision = await PersistentAuthRateLimiter(db).check(
                scope,
                identifier=identifier,
                network=network,
            )
            assert decision.allowed is True

        async with sessions() as db:
            event_count = await db.scalar(
                select(func.count(AuthRateLimitEvent.id)).where(
                    AuthRateLimitEvent.scope == scope,
                    AuthRateLimitEvent.identifier_digest == identifier_digest,
                )
            )
            bucket = await db.scalar(
                select(AuthRateLimitBucket).where(
                    AuthRateLimitBucket.scope == scope,
                    AuthRateLimitBucket.key_type == "identifier",
                    AuthRateLimitBucket.key_digest == identifier_digest,
                )
            )
            assert event_count == 1
            assert bucket is not None
            assert bucket.attempt_count == 1
    finally:
        await engine.dispose()
