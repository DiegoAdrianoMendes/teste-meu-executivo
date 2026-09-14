from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.services.oss_native_outbox_service as outbox_module
from app.db.base import Base
from app.db.tenant import TenantSession, WorkspaceContextError, get_workspace_context
from app.models.oss_native import (
    OSSNativeOutboxEvent,
    OSSNativeOutboxStatus,
    OSSNativeSecretError,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.oss_native_outbox_service import (
    OSSNativeOutboxEventNotFoundError,
    OSSNativeOutboxLeaseLostError,
    OSSNativeOutboxService,
)

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _event(
    workspace_id: UUID,
    *,
    available_at: datetime = NOW,
    payload: dict | None = None,
    status: OSSNativeOutboxStatus = OSSNativeOutboxStatus.pending,
    attempts: int = 0,
    lease_expires_at: datetime | None = None,
) -> OSSNativeOutboxEvent:
    suffix = uuid4().hex
    return OSSNativeOutboxEvent(
        workspace_id=workspace_id,
        actor_user_id=None,
        provider="chatwoot",
        capability="messaging.threads",
        unit_id=f"chatwoot:function:{suffix}",
        binding_id=f"binding-{suffix}",
        topic="job.enqueue",
        dedupe_key=f"dedupe-{suffix}",
        payload=payload or {"operationId": "thread.sync", "data": {"subject": "Local"}},
        status=status,
        attempts=attempts,
        available_at=available_at,
        lease_expires_at=lease_expires_at,
    )


@pytest_asyncio.fixture
async def concurrent_session_factory(tmp_path):
    database_path = tmp_path / "native-outbox.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        poolclass=NullPool,
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


def test_postgresql_claim_uses_skip_locked_and_payload_rejects_secrets() -> None:
    statement = outbox_module._postgresql_claim_candidates(
        uuid4(),
        now=NOW,
        max_attempts=5,
        limit=10,
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "oss_native_outbox_events.workspace_id" in sql
    assert "oss_native_outbox_events.available_at" in sql

    with pytest.raises(OSSNativeSecretError):
        _event(uuid4(), payload={"api_token": "must-not-enter-the-outbox"})


@pytest.mark.asyncio
async def test_two_sqlite_workers_never_claim_the_same_event(
    concurrent_session_factory,
) -> None:
    async with concurrent_session_factory() as seed_db:
        workspace = Workspace(
            name="Concurrent outbox",
            slug=f"concurrent-outbox-{uuid4().hex[:8]}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        seed_db.add(workspace)
        await seed_db.flush()
        event = _event(workspace.id)
        seed_db.add(event)
        await seed_db.commit()
        workspace_id = workspace.id
        event_id = event.id

    start = asyncio.Event()

    async def worker():
        async with concurrent_session_factory() as worker_db:
            await start.wait()
            claim = await OSSNativeOutboxService(worker_db).claim_one(
                workspace_id,
                now=NOW,
            )
            await worker_db.commit()
            return claim

    workers = [asyncio.create_task(worker()), asyncio.create_task(worker())]
    start.set()
    claims = await asyncio.wait_for(asyncio.gather(*workers), timeout=10)
    claimed = [claim for claim in claims if claim is not None]

    assert len(claimed) == 1
    assert claimed[0].event_id == event_id
    assert claimed[0].attempt == 1
    assert claimed[0].lease_expires_at == NOW + timedelta(minutes=5)

    async with concurrent_session_factory() as verification_db:
        event = await verification_db.get(OSSNativeOutboxEvent, event_id)
        assert event is not None
        assert event.status == OSSNativeOutboxStatus.processing
        assert event.attempts == 1


@pytest.mark.asyncio
async def test_complete_is_idempotent_and_expired_attempt_is_fenced(
    db_session,
    auth_context,
) -> None:
    completed_event = _event(auth_context.workspace_id, payload={"data": {"name": "Local"}})
    reclaimed_event = _event(auth_context.workspace_id)
    db_session.add_all([completed_event, reclaimed_event])
    await db_session.flush()
    service = OSSNativeOutboxService(db_session, lease_duration=timedelta(seconds=30))

    first_claims = await service.claim_batch(auth_context.workspace_id, limit=2, now=NOW)
    claims_by_id = {claim.event_id: claim for claim in first_claims}
    completed_claim = claims_by_id[completed_event.id]
    stale_claim = claims_by_id[reclaimed_event.id]

    completed = await service.complete(completed_claim, now=NOW + timedelta(seconds=1))
    replay = await service.complete(completed_claim, now=NOW + timedelta(seconds=2))

    assert completed.status == OSSNativeOutboxStatus.completed
    assert completed.idempotent_replay is False
    assert replay.status == OSSNativeOutboxStatus.completed
    assert replay.idempotent_replay is True
    assert replay.completed_at == NOW + timedelta(seconds=1)
    assert completed_claim.payload == {"data": {"name": "Local"}}

    replacement = await service.claim_one(
        auth_context.workspace_id,
        now=NOW + timedelta(seconds=31),
    )
    assert replacement is not None
    assert replacement.event_id == reclaimed_event.id
    assert replacement.attempt == stale_claim.attempt + 1

    with pytest.raises(OSSNativeOutboxLeaseLostError):
        await service.complete(stale_claim, now=NOW + timedelta(seconds=32))

    final = await service.complete(replacement, now=NOW + timedelta(seconds=32))
    assert final.status == OSSNativeOutboxStatus.completed


@pytest.mark.asyncio
async def test_fail_retries_with_deterministic_backoff_then_dead_letters(
    db_session,
    auth_context,
) -> None:
    event = _event(auth_context.workspace_id)
    db_session.add(event)
    await db_session.flush()
    service = OSSNativeOutboxService(
        db_session,
        lease_duration=timedelta(seconds=30),
        max_attempts=2,
        base_backoff=timedelta(seconds=10),
        max_backoff=timedelta(minutes=1),
    )

    first_claim = await service.claim_one(auth_context.workspace_id, now=NOW)
    assert first_claim is not None
    retry = await service.fail(first_claim, "temporary local failure", now=NOW)
    retry_replay = await service.fail(first_claim, "ignored replay", now=NOW)

    assert retry.status == OSSNativeOutboxStatus.pending
    assert retry.available_at == NOW + timedelta(seconds=10)
    assert retry.idempotent_replay is False
    assert retry_replay.idempotent_replay is True
    assert (
        await service.claim_one(
            auth_context.workspace_id,
            now=NOW + timedelta(seconds=9),
        )
        is None
    )

    waiting_metrics = await service.metrics(auth_context.workspace_id, now=NOW)
    assert waiting_metrics.pending == 1
    assert waiting_metrics.retry_scheduled == 1
    assert waiting_metrics.claimable == 0
    assert waiting_metrics.attempts_total == 1

    second_claim = await service.claim_one(
        auth_context.workspace_id,
        now=NOW + timedelta(seconds=10),
    )
    assert second_claim is not None
    assert second_claim.attempt == 2
    terminal = await service.fail(
        second_claim,
        "Bearer must-not-be-persisted",
        now=NOW + timedelta(seconds=11),
    )

    assert terminal.status == OSSNativeOutboxStatus.failed
    assert terminal.last_error == "processing error redacted"
    assert terminal.lease_expires_at is None
    assert (
        await service.claim_one(
            auth_context.workspace_id,
            now=NOW + timedelta(days=1),
        )
        is None
    )

    final_metrics = await service.metrics(
        auth_context.workspace_id,
        now=NOW + timedelta(days=1),
    )
    assert final_metrics.total == 1
    assert final_metrics.failed == 1
    assert final_metrics.claimable == 0
    assert final_metrics.attempts_total == 2


@pytest.mark.asyncio
async def test_expired_final_attempt_is_dead_lettered_and_stale_worker_is_fenced(
    db_session,
    auth_context,
) -> None:
    event = _event(auth_context.workspace_id)
    db_session.add(event)
    await db_session.flush()
    service = OSSNativeOutboxService(
        db_session,
        lease_duration=timedelta(seconds=10),
        max_attempts=1,
    )

    stale_claim = await service.claim_one(auth_context.workspace_id, now=NOW)
    assert stale_claim is not None
    assert (
        await service.claim_one(
            auth_context.workspace_id,
            now=NOW + timedelta(seconds=11),
        )
        is None
    )

    status = await service.get_status(auth_context.workspace_id, event.id)
    assert status.status == OSSNativeOutboxStatus.failed
    assert status.attempts == 1
    assert status.last_error == "lease expired after maximum attempts"
    with pytest.raises(OSSNativeOutboxLeaseLostError):
        await service.fail(
            stale_claim,
            "late worker failure",
            now=NOW + timedelta(seconds=12),
        )


@pytest.mark.asyncio
async def test_claim_status_and_metrics_are_tenant_isolated_and_bind_rls_context(
    db_session,
    auth_context,
) -> None:
    other_workspace = Workspace(
        name="Other outbox tenant",
        slug=f"other-outbox-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    own_event = _event(auth_context.workspace_id)
    foreign_event = _event(other_workspace.id)
    db_session.add_all([own_event, foreign_event])
    await db_session.flush()

    service = OSSNativeOutboxService(db_session)
    own_claim = await service.claim_one(auth_context.workspace_id, now=NOW)

    assert own_claim is not None
    assert own_claim.event_id == own_event.id
    assert get_workspace_context(db_session) == auth_context.workspace_id
    metrics = await service.metrics(auth_context.workspace_id, now=NOW)
    assert metrics.total == 1
    assert metrics.processing == 1
    status = await service.get_status(auth_context.workspace_id, own_event.id)
    assert status.status == OSSNativeOutboxStatus.processing
    assert status.attempts == 1

    with pytest.raises(OSSNativeOutboxEventNotFoundError):
        await service.get_status(auth_context.workspace_id, foreign_event.id)
    with pytest.raises(WorkspaceContextError):
        await service.claim_one(other_workspace.id, now=NOW)
