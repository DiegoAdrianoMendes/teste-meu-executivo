from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.approval import ApprovalQueueItem, ApprovalStatus
from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.oss_function_unit import OSSFunctionUnitEvent
from app.models.oss_native import (
    OSSNativeOutboxEvent,
    OSSNativeOutboxStatus,
    OSSNativeRecord,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.oss_function_unit_runtime_service import (
    OSSFunctionUnitAuthorizationError,
    OSSFunctionUnitExternalGateError,
    OSSFunctionUnitHumanApprovalError,
    OSSFunctionUnitIdempotencyConflictError,
    OSSFunctionUnitMaturityError,
    OSSFunctionUnitRuntimeService,
    function_unit_approval_type,
    function_unit_runtime_contracts,
    function_unit_runtime_summary,
)
from app.services.oss_function_unit_service import function_unit_catalog
from app.services.oss_native_document_service import (
    required_capability_manage_permissions,
    required_capability_read_permissions,
)


def _contract_with(
    *,
    mode: str | None = None,
    profile: str | None = None,
    maturity: str = "native_primary",
):
    return next(
        contract
        for contract in function_unit_runtime_contracts().values()
        if (mode is None or contract.runtime_mode == mode)
        and (profile is None or contract.semantic_profile == profile)
        and contract.maturity == maturity
    )


def _permissions_for(contract, *, manage: bool) -> frozenset[tuple[str, str]]:
    capability_key = contract.capability_id.split(".", 1)[1]
    read_permissions = required_capability_read_permissions(
        contract.provider,
        capability_key,
    )
    permissions = read_permissions
    if manage:
        permissions = frozenset(
            {
                *read_permissions,
                *required_capability_manage_permissions(
                    contract.provider,
                    capability_key,
                ),
            }
        )
    if contract.authorization_policy.get("authority") == "workspace_admin":
        permissions = frozenset({*permissions, ("users", "manage")})
    return permissions


def test_all_licensed_units_have_one_exact_executable_runtime_contract() -> None:
    catalog = function_unit_catalog()
    licensed = {
        unit["id"]: unit
        for unit in catalog["units"]
        if unit["license"]["status"] == "verified-file"
    }
    contracts = function_unit_runtime_contracts()

    assert len(licensed) == len(contracts) == 3768
    assert set(contracts) == set(licensed)
    assert len({contract.contract_id for contract in contracts.values()}) == 3768
    assert set(function_unit_runtime_summary()["runtimeModes"]) == {
        "read_only",
        "event_sourced",
        "configuration_gate",
    }
    for unit_id, contract in contracts.items():
        mapping = licensed[unit_id]["klmMapping"]
        assert mapping["runtimeContractId"] == contract.contract_id
        assert mapping["runtimeHandler"] == contract.handler
        assert mapping["semanticProfile"] == contract.semantic_profile
        assert mapping["proofProfile"] == contract.proof_profile
        assert (
            mapping["apiRoute"]
            == f"/api/v1/capabilities/functions/{unit_id}/runtime"
        )
        assert mapping["applicationService"].endswith("OSSFunctionUnitRuntimeService")
        assert contract.handler.startswith("native_function_unit.")
        assert contract.as_dict()["assertions"]


def test_contract_matrix_covers_every_public_boundary_category() -> None:
    contracts = function_unit_runtime_contracts()
    profiles = function_unit_runtime_summary()["profiles"]

    assert sum(profiles.values()) == 3768
    assert {
        "authorization.evaluate",
        "configuration.write",
        "data.export",
            "data.import",
            "http.archive",
            "http.ambiguous",
        "http.create",
        "http.read",
        "http.update",
        "job.enqueue",
        "mcp.invoke",
        "mcp.query",
        "realtime.archive",
        "realtime.command",
        "realtime.configure",
        "realtime.read",
        "realtime.subscribe",
        "ui.navigate",
        "webhook.ingest",
        "webhook.inspect",
    } == set(profiles)
    assert all(
        contract.provider in {"chatwoot", "chattermate", "bottlecrm", "rocketchat"}
        for contract in contracts.values()
    )
    assert all(
        not contract.as_dict()["destination"]["apiRoute"].startswith("http")
        for contract in contracts.values()
    )
    read_groups = {
        "authorization.evaluate",
        "boundary.inspect",
        "data.export.generate",
        "entity.query",
        "mcp.query",
        "realtime.query",
        "realtime.subscribe",
        "ui.navigate",
        "webhook.inspect",
    }
    event_groups = {
        "boundary.invoke",
        "data.import.stage",
        "entity.archive",
        "entity.upsert",
        "job.enqueue",
        "mcp.invoke",
        "realtime.publish",
        "webhook.ingest",
    }
    for contract in contracts.values():
        assert contract.state_transition["effect"] == contract.local_effect
        assert contract.effects["localState"] is contract.mutating
        if contract.runtime_mode == "read_only":
            assert contract.equivalence_group in read_groups
            assert contract.mutating is False
        elif contract.runtime_mode == "event_sourced":
            assert contract.equivalence_group in event_groups
            assert contract.mutating is True
        else:
            assert contract.equivalence_group == "configuration.apply"
            assert contract.mutating is True


@pytest.mark.asyncio
async def test_event_sourced_unit_is_idempotent_replayable_and_tenant_isolated(
    db_session, auth_context
) -> None:
    contract = _contract_with(mode="event_sourced", profile="http.create")
    service = OSSFunctionUnitRuntimeService(db_session)
    key = f"runtime-test-{uuid4()}"
    payload = {"name": "Lead local", "stage": "triagem"}

    first = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="execute",
        idempotency_key=key,
        payload=payload,
        permission_keys=_permissions_for(contract, manage=True),
    )
    replay = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="execute",
        idempotency_key=key,
        payload=payload,
        permission_keys=_permissions_for(contract, manage=True),
    )
    snapshot = await service.snapshot(auth_context.workspace_id, contract.unit_id)

    assert first["status"] == "succeeded"
    assert first["externalNetworkExecuted"] is False
    assert replay["idempotentReplay"] is True
    assert replay["eventId"] == first["eventId"]
    assert snapshot["state"]["auditEventCount"] == 1
    assert snapshot["state"]["records"][0]["data"] == payload
    assert snapshot["nativeSource"]["systemOfRecord"] == "klm"

    with pytest.raises(OSSFunctionUnitIdempotencyConflictError):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            unit_id=contract.unit_id,
            mode="execute",
            idempotency_key=key,
            payload={"name": "Outro lead"},
            permission_keys=_permissions_for(contract, manage=True),
        )

    other_workspace = Workspace(
        name="Outro workspace",
        slug=f"outro-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    other_workspace_id = other_workspace.id
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with session_factory() as other_session:
        other_snapshot = await OSSFunctionUnitRuntimeService(other_session).snapshot(
            other_workspace_id,
            contract.unit_id,
        )
    assert other_snapshot["state"]["auditEventCount"] == 0
    assert other_snapshot["state"]["records"] == []


@pytest.mark.asyncio
async def test_external_contract_only_configures_locally_and_never_dispatches(
    db_session, auth_context
) -> None:
    contract = _contract_with(mode="configuration_gate")
    service = OSSFunctionUnitRuntimeService(db_session)

    with pytest.raises(OSSFunctionUnitExternalGateError):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            unit_id=contract.unit_id,
            mode="execute",
            idempotency_key=f"execute-{uuid4()}",
            payload={"enabled": True},
            permission_keys=_permissions_for(contract, manage=True),
        )

    configured = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="configure",
        idempotency_key=f"configure-{uuid4()}",
        payload={"enabled": True, "endpoint": "/local-only"},
        permission_keys=_permissions_for(contract, manage=True),
    )
    event = await db_session.get(OSSFunctionUnitEvent, configured["eventId"])

    assert configured["status"] == "configured"
    assert configured["externalNetworkExecuted"] is False
    assert event is not None
    assert event.external_network_executed is False
    assert event.result["assertions"][-1] == "external_effect_blocked"


@pytest.mark.asyncio
async def test_read_only_contract_returns_native_snapshot_without_writing_event(
    db_session, auth_context
) -> None:
    contract = _contract_with(mode="read_only", profile="http.read")
    service = OSSFunctionUnitRuntimeService(db_session)

    result = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="execute",
        idempotency_key=f"read-{uuid4()}",
        payload={},
        permission_keys=_permissions_for(contract, manage=False),
    )
    snapshot = await service.snapshot(auth_context.workspace_id, contract.unit_id)

    assert result["status"] == "succeeded"
    assert result["eventId"] is None
    assert result["result"]["nativeOperation"]["records"] == []
    assert result["result"]["nativeOperation"]["externalNetworkExecuted"] is False
    assert snapshot["state"]["auditEventCount"] == 0
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "native_capability_function_read",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_async_function_persists_state_event_and_outbox_atomically(
    db_session, auth_context
) -> None:
    contract = _contract_with(mode="event_sourced", profile="job.enqueue")
    service = OSSFunctionUnitRuntimeService(db_session)

    result = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="execute",
        idempotency_key=f"job-{uuid4()}",
        payload={"name": "Consolidar contexto do caso"},
        permission_keys=_permissions_for(contract, manage=True),
    )
    outbox = await db_session.get(
        OSSNativeOutboxEvent,
        result["result"]["outbox"]["id"],
    )

    assert result["status"] == "succeeded"
    assert result["result"]["nativeOperation"]["job"]["status"] == "queued"
    assert outbox is not None
    assert outbox.workspace_id == auth_context.workspace_id
    assert outbox.unit_id == contract.unit_id
    assert outbox.binding_id == contract.binding_id
    assert outbox.status == OSSNativeOutboxStatus.pending
    assert outbox.payload["externalNetworkAllowed"] is False


@pytest.mark.asyncio
async def test_runtime_fails_closed_without_domain_permissions(
    db_session, auth_context
) -> None:
    contract = _contract_with(mode="read_only", profile="authorization.evaluate")
    service = OSSFunctionUnitRuntimeService(db_session)

    with pytest.raises(OSSFunctionUnitAuthorizationError):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            unit_id=contract.unit_id,
            mode="execute",
            idempotency_key=f"authz-{uuid4()}",
            payload={},
            permission_keys=frozenset(),
        )


@pytest.mark.asyncio
async def test_shadow_contract_allows_dry_run_but_never_mutates(
    db_session, auth_context
) -> None:
    contract = _contract_with(mode="event_sourced", maturity="native_shadow")
    service = OSSFunctionUnitRuntimeService(db_session)
    permissions = _permissions_for(contract, manage=True)

    simulated = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="dry-run",
        idempotency_key=f"shadow-dry-run-{uuid4()}",
        payload={},
        permission_keys=permissions,
    )
    assert simulated["status"] == "simulated"
    snapshot = await service.snapshot(auth_context.workspace_id, contract.unit_id)
    assert snapshot["runtime"]["availableModes"] == ["dry-run"]

    with pytest.raises(OSSFunctionUnitMaturityError):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            unit_id=contract.unit_id,
            mode="execute",
            idempotency_key=f"shadow-execute-{uuid4()}",
            payload={},
            permission_keys=permissions,
        )


@pytest.mark.asyncio
async def test_workspace_admin_contract_requires_explicit_admin_authority(
    db_session, auth_context
) -> None:
    contract = next(
        row
        for row in function_unit_runtime_contracts().values()
        if row.authorization_policy.get("authority") == "workspace_admin"
    )
    service = OSSFunctionUnitRuntimeService(db_session)
    capability_key = contract.capability_id.split(".", 1)[1]
    domain_only = required_capability_read_permissions(
        contract.provider,
        capability_key,
    )

    with pytest.raises(OSSFunctionUnitAuthorizationError):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            unit_id=contract.unit_id,
            mode="dry-run",
            idempotency_key=f"admin-denied-{uuid4()}",
            payload={
                "humanApprovalId": str(uuid4())
            }
            if contract.authorization_policy.get("humanVeto")
            else {},
            permission_keys=domain_only,
        )


@pytest.mark.asyncio
async def test_archive_contract_requires_and_consumes_workspace_approval(
    db_session, auth_context
) -> None:
    contract = next(
        row
        for row in function_unit_runtime_contracts().values()
        if row.maturity == "native_primary"
        and row.runtime_mode == "event_sourced"
        and row.local_effect == "archive"
    )
    service = OSSFunctionUnitRuntimeService(db_session)
    permissions = frozenset(
        {
            *_permissions_for(contract, manage=True),
            ("aprovacoes", "approve"),
        }
    )

    with pytest.raises(OSSFunctionUnitHumanApprovalError):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            unit_id=contract.unit_id,
            mode="execute",
            idempotency_key=f"archive-without-approval-{uuid4()}",
            payload={},
            permission_keys=permissions,
        )

    client = Client(workspace_id=auth_context.workspace_id, name="Cliente do veto")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        demand_type="Validação de veto humano",
    )
    db_session.add(matter)
    await db_session.flush()
    resource_id = f"archive-target-{uuid4()}"
    approval_payload = {"expectedRecordVersion": 1}
    simulation = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="dry-run",
        idempotency_key=f"archive-intent-{uuid4()}",
        payload=approval_payload,
        resource_id=resource_id,
        permission_keys=permissions,
    )
    expected_approval_type = function_unit_approval_type(
        contract,
        resource_id=resource_id,
        payload=approval_payload,
    )
    assert simulation["result"]["nativeOperation"]["approvalIntent"] == {
        "required": True,
        "singleUse": True,
        "resourceId": resource_id,
        "approvalType": expected_approval_type,
    }
    approval = ApprovalQueueItem(
        matter_id=matter.id,
        approval_type=expected_approval_type,
        intent_key=f"function-unit:{uuid4()}",
        title="Autorizar arquivamento nativo",
        status=ApprovalStatus.approved,
        resolved_by=auth_context.user_id,
        resolved_at=datetime.now(UTC),
    )
    db_session.add(approval)
    await db_session.flush()
    db_session.add(
        OSSNativeRecord(
            workspace_id=auth_context.workspace_id,
            provider=contract.provider,
            capability=contract.capability_id.split(".", 1)[1],
            scope_key=f"function:{contract.binding_id[:32]}",
            resource_id=resource_id,
            record_type=contract.equivalence_group,
            last_operation="seed_for_archive_test",
            data={"name": "Registro sujeito a veto"},
            created_by_user_id=auth_context.user_id,
            updated_by_user_id=auth_context.user_id,
        )
    )
    await db_session.flush()

    idempotency_key = f"archive-approved-{uuid4()}"
    result = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="execute",
        idempotency_key=idempotency_key,
        payload={
            **approval_payload,
            "humanApprovalId": str(approval.id),
        },
        resource_id=resource_id,
        permission_keys=permissions,
    )

    assert result["status"] == "succeeded"

    replay = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        unit_id=contract.unit_id,
        mode="execute",
        idempotency_key=idempotency_key,
        payload={
            **approval_payload,
            "humanApprovalId": str(approval.id),
        },
        resource_id=resource_id,
        permission_keys=permissions,
    )
    assert replay["idempotentReplay"] is True

    with pytest.raises(OSSFunctionUnitHumanApprovalError, match="já foi consumida"):
        await service.execute(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            unit_id=contract.unit_id,
            mode="execute",
            idempotency_key=f"archive-reuse-{uuid4()}",
            payload={
                **approval_payload,
                "humanApprovalId": str(approval.id),
            },
            resource_id=resource_id,
            permission_keys=permissions,
        )
