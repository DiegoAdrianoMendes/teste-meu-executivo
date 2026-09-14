"""Contract tests for tenant-native OSS state and command persistence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import OSS_NATIVE_TENANT_TABLES, RLS_DIRECT_TENANT_TABLES
from app.models.oss_native import (
    OSSNativeAuditEvent,
    OSSNativeCommand,
    OSSNativeCommandStatus,
    OSSNativeExecutionMode,
    OSSNativeSecretError,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.oss_native import (
    OSSNativeCommandCreate,
    OSSNativeCommandOperation,
    OSSNativeConfigPatch,
)
from app.services.oss_native_state_service import (
    DEFAULT_OSS_NATIVE_COMMAND_LEASE,
    OSSNativeCommandConflictError,
    OSSNativeDispatcherRegistry,
    OSSNativeServerExecutionPolicy,
    OSSNativeStateService,
    OSSNativeVersionConflictError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _payload_hash(
    payload: dict[str, object],
    operation: OSSNativeCommandOperation = OSSNativeCommandOperation.execute,
) -> str:
    encoded = json.dumps(
        {"operation": operation.value, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _allow_local_execution(
    _provider: str,
    _capability: str,
) -> OSSNativeServerExecutionPolicy:
    return OSSNativeServerExecutionPolicy(
        catalogued=True,
        native_mode="klm_canonical",
        external_execution_state="local_only",
        execute_allowed=True,
    )


async def _workspace(db_session: AsyncSession, label: str) -> Workspace:
    workspace = Workspace(
        name=f"Workspace {label}",
        slug=f"workspace-{label}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


@pytest.mark.asyncio
async def test_state_is_scoped_by_workspace_provider_and_capability(
    db_session: AsyncSession,
    auth_context,
) -> None:
    other_workspace = await _workspace(db_session, "native-other")
    service_a = OSSNativeStateService(db_session)
    await service_a.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chatwoot",
        capability="inbox.routing",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"layout": "dense"},
            execution_mode=OSSNativeExecutionMode.local_native,
        ),
    )

    async with AsyncSession(bind=db_session.bind, expire_on_commit=False) as other_session:
        service_b = OSSNativeStateService(other_session)
        await service_b.update_config(
            workspace_id=other_workspace.id,
            actor_user_id=None,
            provider="chatwoot",
            capability="inbox.routing",
            data=OSSNativeConfigPatch(
                expected_version=0,
                configuration={"layout": "compact"},
                execution_mode=OSSNativeExecutionMode.disabled,
            ),
        )
        snapshot_b = await service_b.get_snapshot(
            other_workspace.id,
            "chatwoot",
            "inbox.routing",
        )

    snapshot_a = await service_a.get_snapshot(
        auth_context.workspace_id,
        "chatwoot",
        "inbox.routing",
    )
    assert snapshot_a.state.configuration == {"layout": "dense"}
    assert snapshot_b.state.configuration == {"layout": "compact"}
    assert snapshot_a.state.id != snapshot_b.state.id


@pytest.mark.asyncio
async def test_command_is_idempotent_and_audit_omits_payload_content(
    db_session: AsyncSession,
    auth_context,
) -> None:
    registry = OSSNativeDispatcherRegistry()
    calls = 0

    async def dispatch(context):
        nonlocal calls
        calls += 1
        assert context.db is db_session
        assert context.operation == OSSNativeCommandOperation.execute
        assert context.external_network_allowed is False
        return {"accepted": True, "localRevision": 7}

    registry.register("chattermate", dispatch)
    service = OSSNativeStateService(
        db_session,
        dispatchers=registry,
        execution_policy_resolver=_allow_local_execution,
    )
    await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.preview",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"canvas": "private-layout-value"},
            execution_mode=OSSNativeExecutionMode.local_native,
        ),
    )
    request = OSSNativeCommandCreate(
        command_type="preview.refresh",
        idempotency_key="preview-refresh-0001",
        expected_version=1,
        payload={"matterReference": "case-private-123"},
    )

    first = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.preview",
        data=request,
    )
    replay = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.preview",
        data=request,
    )

    assert first.status == OSSNativeCommandStatus.succeeded
    assert replay.id == first.id
    assert replay.deduplicated is True
    assert calls == 1
    assert await db_session.scalar(select(func.count()).select_from(OSSNativeCommand)) == 1
    audits = list((await db_session.scalars(select(OSSNativeAuditEvent))).all())
    assert {event.action.value for event in audits} >= {
        "command_received",
        "command_started",
        "command_succeeded",
    }
    assert "case-private-123" not in str([event.audit_metadata for event in audits])
    assert "private-layout-value" not in str([event.audit_metadata for event in audits])


@pytest.mark.asyncio
async def test_conflicting_command_replay_is_rejected_without_second_dispatch(
    db_session: AsyncSession,
    auth_context,
) -> None:
    registry = OSSNativeDispatcherRegistry()
    calls = 0

    async def dispatch(_context):
        nonlocal calls
        calls += 1
        return {"ok": True}

    registry.register("bottlecrm", dispatch)
    service = OSSNativeStateService(
        db_session,
        dispatchers=registry,
        execution_policy_resolver=_allow_local_execution,
    )
    await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="bottlecrm",
        capability="crm.card",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"projection": "local"},
            execution_mode=OSSNativeExecutionMode.local_native,
        ),
    )
    base = dict(
        command_type="card.qualify",
        idempotency_key="card-qualify-0001",
        expected_version=1,
    )
    await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="bottlecrm",
        capability="crm.card",
        data=OSSNativeCommandCreate(**base, payload={"score": 70}),
    )

    with pytest.raises(OSSNativeCommandConflictError):
        await service.execute_command(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="bottlecrm",
            capability="crm.card",
            data=OSSNativeCommandCreate(**base, payload={"score": 90}),
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_optimistic_config_version_rejects_stale_writer(
    db_session: AsyncSession,
    auth_context,
) -> None:
    service = OSSNativeStateService(db_session)
    await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chatwoot",
        capability="contacts.profile",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"fields": ["name"]},
        ),
    )

    with pytest.raises(OSSNativeVersionConflictError) as conflict:
        await service.update_config(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="chatwoot",
            capability="contacts.profile",
            data=OSSNativeConfigPatch(
                expected_version=0,
                configuration={"fields": ["name", "email"]},
            ),
        )
    assert conflict.value.current_version == 1


@pytest.mark.asyncio
async def test_secret_rejection_is_enforced_by_schema_and_service(
    db_session: AsyncSession,
    auth_context,
) -> None:
    with pytest.raises(ValidationError):
        OSSNativeConfigPatch(
            expected_version=0,
            configuration={"apiKey": "forbidden-value"},
        )
    service = OSSNativeStateService(db_session)
    bypassed_schema = OSSNativeConfigPatch.model_construct(
        expected_version=0,
        configuration={"nested": {"authorization": "Bearer forbidden-value"}},
        execution_mode=OSSNativeExecutionMode.local_native,
    )
    with pytest.raises(OSSNativeSecretError):
        await service.update_config(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="chatwoot",
            capability="inbox.secure",
            data=bypassed_schema,
        )

    safe = await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chatwoot",
        capability="inbox.secure",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"enabled": True},
        ),
    )
    assert safe.state.config_version == 1
    bypassed_command = OSSNativeCommandCreate.model_construct(
        command_type="inbox.refresh",
        idempotency_key="inbox-refresh-0001",
        expected_version=1,
        payload={"password": "forbidden-value"},
    )
    with pytest.raises(OSSNativeSecretError):
        await service.execute_command(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="chatwoot",
            capability="inbox.secure",
            data=bypassed_command,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [
        OSSNativeExecutionMode.external_equivalent,
        OSSNativeExecutionMode.enterprise_equivalent,
    ],
)
async def test_external_and_enterprise_commands_persist_without_dispatch(
    db_session: AsyncSession,
    auth_context,
    execution_mode: OSSNativeExecutionMode,
) -> None:
    registry = OSSNativeDispatcherRegistry()
    registry.register(
        "rocketchat",
        "tests.module_that_must_not_be_imported:dispatcher",
    )
    service = OSSNativeStateService(
        db_session,
        dispatchers=registry,
        execution_policy_resolver=_allow_local_execution,
    )
    snapshot = await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="rocketchat",
        capability="enterprise.retention",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"retentionDays": 30},
            execution_mode=execution_mode,
        ),
    )
    command = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="rocketchat",
        capability="enterprise.retention",
        data=OSSNativeCommandCreate(
            command_type="retention.apply",
            idempotency_key=f"retention-{execution_mode.value}",
            expected_version=snapshot.state.config_version,
            payload={"scope": "workspace"},
        ),
    )

    assert command.status == OSSNativeCommandStatus.blocked
    assert command.result == {
        "persistedLocally": True,
        "dispatcherInvoked": False,
        "externalNetworkExecuted": False,
    }
    assert command.error_code == "external_execution_blocked"
    assert command.dispatcher_invoked is False
    assert command.external_network_allowed is False


@pytest.mark.asyncio
async def test_dispatch_failure_is_effect_unknown_and_replay_never_dispatches_again(
    db_session: AsyncSession,
    auth_context,
) -> None:
    registry = OSSNativeDispatcherRegistry()
    calls = 0

    async def dispatch(_context):
        nonlocal calls
        calls += 1
        raise RuntimeError("private dispatcher detail must not be persisted")

    registry.register("chattermate", dispatch)
    service = OSSNativeStateService(
        db_session,
        dispatchers=registry,
        execution_policy_resolver=_allow_local_execution,
    )
    snapshot = await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.failure",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"enabled": True},
            execution_mode=OSSNativeExecutionMode.local_native,
        ),
    )
    request = OSSNativeCommandCreate(
        command_type="flow.publish",
        idempotency_key="flow-failure-0001",
        expected_version=snapshot.state.config_version,
        payload={"flowId": "flow-1"},
    )

    first = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.failure",
        data=request,
    )
    replay = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.failure",
        data=request,
    )

    assert first.status == OSSNativeCommandStatus.failed
    assert first.error_code == "effect_unknown"
    assert first.effect_state == "unknown"
    assert first.is_success is False
    assert first.is_terminal is True
    assert first.requires_reconciliation is True
    assert first.automatic_retry_allowed is False
    assert replay.id == first.id
    assert replay.status == OSSNativeCommandStatus.failed
    assert replay.deduplicated is True
    assert replay.requires_reconciliation is True
    assert calls == 1
    persisted = await db_session.get(OSSNativeCommand, first.id)
    assert persisted is not None
    assert "private dispatcher detail" not in (persisted.error_message or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reported_effect", "marker_path"),
    [
        ({"externalNetworkExecuted": True}, "externalNetworkExecuted"),
        ({"externalDispatchExecuted": True}, "externalDispatchExecuted"),
        ({"externalEffects": 2}, "externalEffects"),
        ({"remoteWritePerformed": "true"}, "remoteWritePerformed"),
        ({"external": {"effects": 2}}, "external.effects"),
        ({"external": {"dispatchExecuted": True}}, "external.dispatchExecuted"),
        ({"events": ({"external": {"effects": 1}},)}, "events.0.external.effects"),
    ],
)
async def test_dispatcher_cannot_report_an_external_effect_as_local_success(
    db_session: AsyncSession,
    auth_context,
    reported_effect: dict,
    marker_path: str,
) -> None:
    registry = OSSNativeDispatcherRegistry()

    async def dispatch(_context):
        return {
            "status": "completed",
            **reported_effect,
        }

    registry.register("chattermate", dispatch)
    service = OSSNativeStateService(
        db_session,
        dispatchers=registry,
        execution_policy_resolver=_allow_local_execution,
    )
    snapshot = await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.external-marker",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"enabled": True},
            execution_mode=OSSNativeExecutionMode.local_native,
        ),
    )
    command = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.external-marker",
        data=OSSNativeCommandCreate(
            command_type="flow.preview",
            idempotency_key=("external-marker-state-" + marker_path.casefold())[:64],
            expected_version=snapshot.state.config_version,
            payload={},
        ),
    )

    assert command.status == OSSNativeCommandStatus.reconciliation_required
    assert command.result["adapterEffectReport"] == {
        "markerPath": marker_path,
        "state": "positive",
    }
    assert command.error_code == "external_effect_reported"
    assert command.requires_reconciliation is True


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_running_replay_never_redispatches_and_expired_lease_requires_reconciliation(
    db_session: AsyncSession,
    auth_context,
    expired: bool,
) -> None:
    registry = OSSNativeDispatcherRegistry()
    calls = 0

    async def dispatch(_context):
        nonlocal calls
        calls += 1
        return {"unexpected": True}

    registry.register("chattermate", dispatch)
    service = OSSNativeStateService(
        db_session,
        dispatchers=registry,
        running_lease=DEFAULT_OSS_NATIVE_COMMAND_LEASE,
        execution_policy_resolver=_allow_local_execution,
    )
    capability = f"flows.running-{'expired' if expired else 'active'}"
    snapshot = await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability=capability,
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"enabled": True},
            execution_mode=OSSNativeExecutionMode.local_native,
        ),
    )
    payload = {"flowId": "flow-lease"}
    request = OSSNativeCommandCreate(
        command_type="flow.publish",
        idempotency_key=f"flow-running-{'expired' if expired else 'active'}",
        expected_version=snapshot.state.config_version,
        payload=payload,
    )
    started_at = datetime.now(UTC) - (
        DEFAULT_OSS_NATIVE_COMMAND_LEASE + timedelta(seconds=1) if expired else timedelta(seconds=1)
    )
    command = OSSNativeCommand(
        workspace_id=auth_context.workspace_id,
        state_id=snapshot.state.id,
        provider="chattermate",
        capability=capability,
        command_type=request.command_type,
        idempotency_key=request.idempotency_key,
        payload_hash=_payload_hash(payload),
        command_payload=payload,
        status=OSSNativeCommandStatus.running,
        execution_mode=OSSNativeExecutionMode.local_native,
        state_version=snapshot.state.config_version,
        command_result={},
        requested_by_user_id=auth_context.user_id,
        dispatcher_invoked=True,
        external_network_allowed=False,
        row_version=2,
        started_at=started_at,
    )
    db_session.add(command)
    await db_session.commit()

    replay = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability=capability,
        data=request,
    )
    second_replay = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability=capability,
        data=request,
    )

    assert replay.id == command.id
    assert replay.deduplicated is True
    assert replay.is_success is False
    assert replay.automatic_retry_allowed is False
    assert calls == 0
    if expired:
        assert replay.status == OSSNativeCommandStatus.reconciliation_required
        assert replay.effect_state == "unknown"
        assert replay.is_terminal is True
        assert replay.requires_reconciliation is True
        assert replay.error_code == "command_lease_expired"
        assert replay.completed_at is not None
        assert replay.row_version == 3
        reconciliation_audits = list(
            (
                await db_session.scalars(
                    select(OSSNativeAuditEvent).where(
                        OSSNativeAuditEvent.command_id == command.id,
                        OSSNativeAuditEvent.action == "command_reconciliation_required",
                    )
                )
            ).all()
        )
        assert len(reconciliation_audits) == 1
    else:
        assert replay.status == OSSNativeCommandStatus.running
        assert replay.effect_state == "in_progress"
        assert replay.is_terminal is False
        assert replay.requires_reconciliation is False
        assert replay.lease_expires_at is not None
    assert second_replay.status == replay.status


@pytest.mark.asyncio
async def test_received_replay_is_closed_without_redispatch(
    db_session: AsyncSession,
    auth_context,
) -> None:
    registry = OSSNativeDispatcherRegistry()
    calls = 0

    async def dispatch(_context):
        nonlocal calls
        calls += 1
        return {"unexpected": True}

    registry.register("chattermate", dispatch)
    service = OSSNativeStateService(
        db_session,
        dispatchers=registry,
        execution_policy_resolver=_allow_local_execution,
    )
    payload = {"flowId": "flow-abandoned"}
    request = OSSNativeCommandCreate(
        command_type="flow.publish",
        idempotency_key="flow-received-abandoned-0001",
        expected_version=0,
        payload=payload,
    )
    command = OSSNativeCommand(
        workspace_id=auth_context.workspace_id,
        state_id=None,
        provider="chattermate",
        capability="flows.received-abandoned",
        command_type=request.command_type,
        idempotency_key=request.idempotency_key,
        payload_hash=_payload_hash(payload),
        command_payload=payload,
        status=OSSNativeCommandStatus.received,
        execution_mode=OSSNativeExecutionMode.disabled,
        state_version=0,
        command_result={},
        requested_by_user_id=auth_context.user_id,
        dispatcher_invoked=False,
        external_network_allowed=False,
    )
    db_session.add(command)
    await db_session.commit()

    replay = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.received-abandoned",
        data=request,
    )
    second_replay = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chattermate",
        capability="flows.received-abandoned",
        data=request,
    )

    assert calls == 0
    assert replay.status == OSSNativeCommandStatus.failed
    assert replay.error_code == "command_abandoned_before_dispatch"
    assert replay.result == {
        "persistedLocally": True,
        "dispatcherInvoked": False,
        "effectState": "not_executed",
        "safeToRetryWithNewKey": True,
    }
    assert replay.deduplicated is True
    assert second_replay.status == OSSNativeCommandStatus.failed
    failed_audits = list(
        (
            await db_session.scalars(
                select(OSSNativeAuditEvent).where(
                    OSSNativeAuditEvent.command_id == command.id,
                    OSSNativeAuditEvent.action == "command_failed",
                )
            )
        ).all()
    )
    assert len(failed_audits) == 1


@pytest.mark.asyncio
async def test_server_catalog_prevents_local_native_escalation_but_allows_trusted_controls(
    db_session: AsyncSession,
    auth_context,
) -> None:
    registry = OSSNativeDispatcherRegistry()
    operations: list[OSSNativeCommandOperation] = []

    async def dispatch(context):
        operations.append(context.operation)
        return {
            "operation": context.operation.value,
            "externalNetworkExecuted": False,
        }

    registry.register("chatwoot", dispatch)
    service = OSSNativeStateService(db_session, dispatchers=registry)
    snapshot = await service.update_config(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chatwoot",
        capability="external-channels",
        data=OSSNativeConfigPatch(
            expected_version=0,
            configuration={"previewEnabled": True},
            execution_mode=OSSNativeExecutionMode.local_native,
        ),
    )
    assert snapshot.state.execution_mode == OSSNativeExecutionMode.external_equivalent

    spoofed = await service.execute_command(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chatwoot",
        capability="external-channels",
        data=OSSNativeCommandCreate(
            command_type="channel.connect",
            idempotency_key="gated-spoofed-dry-run",
            expected_version=snapshot.state.config_version,
            payload={"actionMode": "dry-run"},
        ),
    )
    assert spoofed.status == OSSNativeCommandStatus.blocked
    assert spoofed.error_code == "server_execution_gate"
    assert spoofed.dispatcher_invoked is False
    assert operations == []

    for operation in (
        OSSNativeCommandOperation.dry_run,
        OSSNativeCommandOperation.configure,
    ):
        controlled = await service.execute_command(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            provider="chatwoot",
            capability="external-channels",
            data=OSSNativeCommandCreate(
                command_type=f"channel.{operation.name}",
                idempotency_key=f"gated-control-{operation.name}",
                expected_version=snapshot.state.config_version,
                payload={"candidate": True},
            ),
            operation=operation,
        )
        assert controlled.status == OSSNativeCommandStatus.succeeded
        assert controlled.execution_mode == OSSNativeExecutionMode.external_equivalent
        assert controlled.result["externalNetworkExecuted"] is False
        assert controlled.external_network_allowed is False

    assert operations == [
        OSSNativeCommandOperation.dry_run,
        OSSNativeCommandOperation.configure,
    ]
    audits = list(
        (
            await db_session.scalars(
                select(OSSNativeAuditEvent).where(
                    OSSNativeAuditEvent.workspace_id == auth_context.workspace_id,
                    OSSNativeAuditEvent.provider == "chatwoot",
                    OSSNativeAuditEvent.capability == "external-channels",
                )
            )
        ).all()
    )
    audit_metadata = [event.audit_metadata for event in audits]
    assert any(item.get("executionModeConstrained") is True for item in audit_metadata)
    assert {item.get("operation") for item in audit_metadata} >= {
        "execute",
        "dry-run",
        "configure",
    }


def test_oss_native_tables_are_part_of_the_runtime_rls_inventory() -> None:
    assert OSS_NATIVE_TENANT_TABLES == (
        "oss_native_capability_states",
        "oss_native_commands",
        "oss_native_audit_events",
        "oss_native_records",
        "oss_native_outbox_events",
        "oss_function_unit_events",
    )
    assert set(OSS_NATIVE_TENANT_TABLES) <= set(RLS_DIRECT_TENANT_TABLES)


def test_oss_native_migration_upgrade_downgrade_and_revision(monkeypatch) -> None:
    original_path = sys.path[:]
    sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != PROJECT_ROOT]
    try:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
    finally:
        sys.path = original_path

    migration_path = (
        PROJECT_ROOT / "alembic" / "versions" / "95f6a7b8c9d0_add_oss_native_foundation.py"
    )
    migration_source = migration_path.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in migration_source
    assert "FORCE ROW LEVEL SECURITY" in migration_source
    assert "current_setting('{WORKSPACE_SETTING}', true)" in migration_source
    spec = importlib.util.spec_from_file_location("oss_native_migration", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "95f6a7b8c9d0"
    assert migration.down_revision == "94e5f6a7b8c9"
    assert "reconciliation_required" in migration.command_status_enum.enums
    assert "command_reconciliation_required" in migration.audit_action_enum.enums
    assert migration.TENANT_TABLES == OSS_NATIVE_TENANT_TABLES[:3]

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert {
            "oss_native_capability_states",
            "oss_native_commands",
            "oss_native_audit_events",
        }.issubset(inspector.get_table_names())
        assert {
            "workspace_id",
            "provider",
            "capability",
            "configuration",
            "config_version",
        }.issubset(
            column["name"] for column in inspector.get_columns("oss_native_capability_states")
        )
        migration.downgrade()
        assert not {
            "oss_native_capability_states",
            "oss_native_commands",
            "oss_native_audit_events",
        }.intersection(sa.inspect(connection).get_table_names())
