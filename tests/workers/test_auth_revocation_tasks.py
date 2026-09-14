from __future__ import annotations

import pytest

from app.workers.auth_revocation_tasks import (
    MAX_OUTBOX_MESSAGES_PER_RUN,
    MAX_REVOCATIONS_PER_RUN,
    _async_purge_expired_identity_outbox,
    _async_retry_pending_auth_revocations,
)
from app.workers.celery_app import celery_app


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_args) -> None:
        return None


@pytest.mark.asyncio
async def test_auth_revocation_worker_drains_a_bounded_batch() -> None:
    session = object()
    observed: dict[str, object] = {}

    class Service:
        def __init__(self, db: object) -> None:
            observed["db"] = db

        async def retry_pending(self, *, limit: int) -> int:
            observed["limit"] = limit
            return 7

    result = await _async_retry_pending_auth_revocations(
        session_factory=lambda: _SessionContext(session),
        service_factory=Service,
    )

    assert result == 7
    assert observed == {"db": session, "limit": MAX_REVOCATIONS_PER_RUN}


def test_auth_revocation_retry_is_registered_in_celery_beat() -> None:
    schedule = celery_app.conf.beat_schedule["retry-auth-revocations-every-minute"]

    assert schedule["task"] == "auth_revocation_tasks.retry_pending"
    assert "auth_revocation_tasks.retry_pending" in celery_app.tasks


@pytest.mark.asyncio
async def test_identity_outbox_worker_purges_a_bounded_batch_and_commits() -> None:
    observed: dict[str, object] = {}

    class Session:
        async def commit(self) -> None:
            observed["committed"] = True

    session = Session()

    class Service:
        def __init__(self, db: object) -> None:
            observed["db"] = db

        async def purge_expired(self, *, limit: int) -> int:
            observed["limit"] = limit
            return 11

    result = await _async_purge_expired_identity_outbox(
        session_factory=lambda: _SessionContext(session),
        service_factory=Service,
    )

    assert result == 11
    assert observed == {
        "db": session,
        "limit": MAX_OUTBOX_MESSAGES_PER_RUN,
        "committed": True,
    }


def test_identity_outbox_cleanup_is_registered_in_celery_beat() -> None:
    schedule = celery_app.conf.beat_schedule[
        "purge-expired-identity-outbox-every-five-minutes"
    ]

    assert schedule["task"] == "auth_revocation_tasks.purge_expired_outbox"
    assert "auth_revocation_tasks.purge_expired_outbox" in celery_app.tasks
