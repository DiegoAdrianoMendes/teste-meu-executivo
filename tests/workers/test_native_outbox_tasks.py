from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.oss_native import OSSNativeOutboxEvent, OSSNativeOutboxStatus
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.workers import native_outbox_tasks


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.mark.asyncio
async def test_worker_claims_broadcasts_and_completes_local_handoff(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
) -> None:
    event = OSSNativeOutboxEvent(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="bottlecrm",
        capability="crm.matters",
        unit_id=f"native-test:{uuid4().hex}",
        binding_id=uuid4().hex,
        topic="job.enqueue",
        dedupe_key=uuid4().hex,
        payload={
            "operationId": "crm.case.summary",
            "resourceId": str(uuid4()),
            "externalNetworkAllowed": False,
        },
        status=OSSNativeOutboxStatus.pending,
    )
    db_session.add(event)
    await db_session.commit()
    broadcasts: list[dict] = []
    scheduled: list[int] = []

    async def capture(event_type, data, *, workspace_id):
        broadcasts.append(
            {
                "eventType": event_type,
                "data": data,
                "workspaceId": workspace_id,
            }
        )

    monkeypatch.setattr(
        native_outbox_tasks.ws_manager,
        "broadcast_board_update",
        capture,
    )
    monkeypatch.setattr(
        native_outbox_tasks.process_native_outbox_workspace,
        "apply_async",
        lambda *, args, countdown: scheduled.append(countdown),
    )

    result = await native_outbox_tasks._process_workspace(
        auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    async with _session_factory(db_session)() as verification_db:
        persisted = await verification_db.get(OSSNativeOutboxEvent, event.id)
    assert result == {
        "workspaceId": str(auth_context.workspace_id),
        "claimed": 1,
        "completed": 1,
        "failed": 0,
    }
    assert persisted is not None
    assert persisted.status == OSSNativeOutboxStatus.completed
    assert persisted.attempts == 1
    assert broadcasts[0]["eventType"] == "NATIVE_FUNCTION_UNIT_HANDOFF"
    assert broadcasts[0]["workspaceId"] == auth_context.workspace_id
    assert broadcasts[0]["data"]["externalNetworkExecuted"] is False
    assert scheduled == [int(native_outbox_tasks.DEFAULT_LEASE_DURATION.total_seconds()) + 1]


@pytest.mark.asyncio
async def test_recovery_enqueues_only_active_workspaces(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    active = Workspace(
        name="Outbox ativo",
        slug=f"outbox-active-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    inactive = Workspace(
        name="Outbox inativo",
        slug=f"outbox-inactive-{uuid4().hex}",
        status=WorkspaceStatus.suspended,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=False,
        settings={},
    )
    db_session.add_all((active, inactive))
    await db_session.commit()
    queued: list[str] = []
    monkeypatch.setattr(
        native_outbox_tasks.process_native_outbox_workspace,
        "delay",
        lambda workspace_id: queued.append(workspace_id),
    )

    count = await native_outbox_tasks._recover_active_workspaces(
        session_factory=_session_factory(db_session),
    )

    assert count == 1
    assert queued == [str(active.id)]


def test_native_outbox_recovery_is_registered_in_celery_beat() -> None:
    schedule = native_outbox_tasks.celery_app.conf.beat_schedule[
        "recover-native-outbox-every-minute"
    ]

    assert schedule["task"] == "native_outbox_tasks.recover_active_workspaces"
    assert (
        "native_outbox_tasks.process_workspace"
        in native_outbox_tasks.celery_app.tasks
    )


@pytest.mark.asyncio
async def test_retry_countdown_cabe_no_visibility_timeout_do_redis(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
) -> None:
    """S46: backoff máximo de 1h mais 1s dava countdown de 3601s, um segundo acima do
    visibility_timeout default do Redis (3600s), e o broker reentregava a task."""
    from app.services.oss_native_outbox_service import OSSNativeOutboxService

    event = OSSNativeOutboxEvent(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="bottlecrm",
        capability="crm.matters",
        unit_id=f"native-test:{uuid4().hex}",
        binding_id=uuid4().hex,
        topic="job.enqueue",
        dedupe_key=uuid4().hex,
        payload={
            "operationId": "crm.case.summary",
            "resourceId": str(uuid4()),
            "externalNetworkAllowed": False,
        },
        status=OSSNativeOutboxStatus.pending,
    )
    db_session.add(event)
    await db_session.commit()
    scheduled: list[int] = []

    async def explode(claim):
        raise RuntimeError("entrega local falhou")

    monkeypatch.setattr(native_outbox_tasks, "_deliver_local_handoff", explode)
    monkeypatch.setattr(
        OSSNativeOutboxService, "retry_delay", lambda self, attempt: timedelta(hours=1)
    )
    monkeypatch.setattr(
        native_outbox_tasks.process_native_outbox_workspace,
        "apply_async",
        lambda *, args, countdown: scheduled.append(countdown),
    )

    result = await native_outbox_tasks._process_workspace(
        auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    assert result["failed"] == 1
    lease = int(native_outbox_tasks.DEFAULT_LEASE_DURATION.total_seconds()) + 1
    retentativa = [c for c in scheduled if c != lease]
    assert retentativa == [native_outbox_tasks.MAX_RETRY_COUNTDOWN_SECONDS]
    # folga para contenção de fila, a mesma conta do monitoramento processual
    assert all(c + 600 <= 3600 for c in scheduled)
