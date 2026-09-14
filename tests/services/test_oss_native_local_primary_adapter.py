"""Sidecar-off proofs for the KLM-native primary OSS adapter."""

from __future__ import annotations

from typing import NoReturn

import pytest

from app.schemas.oss_native import OSSNativeDocumentActionWrite
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_native_document_service import OSSNativeDocumentService
from app.services.oss_native_local_adapter import (
    OSSNativeLocalPrimaryAdapter,
    OSSNativeLocalRecordConflictError,
    OSSNativeLocalValidationError,
    local_contract_summary,
)
from scripts.verify_oss_native_contracts import _action_payload


def test_local_primary_contract_preserves_all_135_capabilities_and_511_actions() -> None:
    assert local_contract_summary() == {
        "capabilities": 135,
        "actions": 511,
        "byProvider": {
            "bottlecrm": 36,
            "chattermate": 27,
            "chatwoot": 39,
            "rocketchat": 33,
        },
        "actionsByProvider": {
            "bottlecrm": 118,
            "chattermate": 105,
            "chatwoot": 211,
            "rocketchat": 77,
        },
        "sidecarRequired": False,
    }


@pytest.mark.asyncio
async def test_all_capability_documents_render_without_constructing_a_source_adapter(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    def forbidden_source_adapter(_provider: str) -> NoReturn:
        raise AssertionError("A jornada primária tentou construir um adapter de sidecar.")

    monkeypatch.setattr(
        OSSNativeDocumentService,
        "_adapter_class",
        staticmethod(forbidden_source_adapter),
    )
    service = OSSNativeDocumentService(db_session)
    registry = oss_capability_registry()["capabilities"]
    rendered: set[str] = set()
    action_ids: set[str] = set()

    for capability in registry:
        document = await service.document(
            auth_context.workspace_id,
            capability["provider"],
            capability["key"],
        )
        capability_id = capability["id"]
        rendered.add(capability_id)
        assert document["health"]["state"] in {"healthy", "gated"}, capability_id
        assert document["source"]["systemOfRecord"] == "klm", capability_id
        assert document["native"]["roundTrip"] is False, capability_id
        assert document["actions"], capability_id
        action_ids.update(
            f"{capability_id}:{action['id']}" for action in document["actions"]
        )

    assert len(rendered) == 135
    assert len(action_ids) == 511


@pytest.mark.asyncio
async def test_all_511_actions_validate_locally_without_sidecar_or_network(
    db_session,
    auth_context,
) -> None:
    registry = oss_capability_registry()["capabilities"]
    validated: set[str] = set()

    for capability in registry:
        adapter = OSSNativeLocalPrimaryAdapter(db_session, capability["provider"])
        snapshot = await adapter.snapshot(
            auth_context.workspace_id,
            capability["key"],
            {},
        )
        for action in snapshot["actions"]:
            command = str(action["command"])
            payload = {}
            for required in action.get("required") or []:
                alternative = str(required).split("|", maxsplit=1)[0]
                payload[alternative] = f"qa-{alternative}"
            result = await adapter.validate(
                auth_context.workspace_id,
                auth_context.user_id,
                capability["key"],
                command,
                payload,
                mode="dry-run",
            )
            assert result["externalNetworkExecuted"] is False
            assert result["sidecarRequired"] is False
            validated.add(f"{capability['id']}:{command}")

    assert len(validated) == 511


@pytest.mark.asyncio
async def test_executed_local_action_survives_fresh_adapter_replay(
    db_session,
    auth_context,
) -> None:
    service = OSSNativeDocumentService(db_session)
    document = await service.document(
        auth_context.workspace_id,
        "chatwoot",
        "companies-segments",
    )
    action = next(row for row in document["actions"] if row["id"] == "create_segment")
    values, transient = _action_payload(action)
    request = OSSNativeDocumentActionWrite(
        action_id=action["id"],
        values=values,
        transient_secrets=transient,
        mode="execute",
        version=document["version"],
        idempotency_key="sidecar-off-create-segment-0001",
    )

    executed = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chatwoot",
        capability_key="companies-segments",
        request=request,
    )
    replayed = await service.execute(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        provider="chatwoot",
        capability_key="companies-segments",
        request=request,
    )
    restarted_document = await OSSNativeDocumentService(db_session).document(
        auth_context.workspace_id,
        "chatwoot",
        "companies-segments",
    )

    assert executed["status"] == "succeeded"
    assert executed["result"]["engine"] == "oss_native_local_primary"
    assert executed["result"]["externalNetworkExecuted"] is False
    assert replayed["idempotentReplay"] is True
    records = restarted_document["tabs"][0]["sections"][0]["rows"]
    assert len(records) == 1
    assert records[0]["lastCommand"] == "create_segment"
    assert records[0]["state"] == "active"


@pytest.mark.asyncio
async def test_canonical_records_enforce_scope_version_and_same_resource_archive(
    db_session,
    auth_context,
) -> None:
    adapter = OSSNativeLocalPrimaryAdapter(db_session, "bottlecrm")
    common = {
        "workspace_id": auth_context.workspace_id,
        "actor_user_id": auth_context.user_id,
        "capability_key": "accounts-contacts-leads",
        "operation_id": "entity.upsert",
        "effect": "upsert",
        "resource_id": "lead-42",
        "record_type": "lead",
    }
    first = await adapter.execute_operation(
        **common,
        scope_key="function:a",
        payload={"name": "Lead A", "expectedRecordVersion": 0},
    )
    second_scope = await adapter.execute_operation(
        **common,
        scope_key="function:b",
        payload={"name": "Lead B", "expectedRecordVersion": 0},
    )

    assert first["resourceId"] == second_scope["resourceId"] == "lead-42"
    assert first["recordVersion"] == second_scope["recordVersion"] == 1
    with pytest.raises(OSSNativeLocalRecordConflictError):
        await adapter.execute_operation(
            **common,
            scope_key="function:a",
            payload={"name": "Stale", "expectedRecordVersion": 0},
        )

    archived = await adapter.execute_operation(
        **{**common, "operation_id": "entity.archive", "effect": "archive"},
        scope_key="function:a",
        payload={"expectedRecordVersion": 1},
    )
    scope_a = await adapter.snapshot(
        auth_context.workspace_id,
        "accounts-contacts-leads",
        {"scopeKey": "function:a"},
    )
    scope_b = await adapter.snapshot(
        auth_context.workspace_id,
        "accounts-contacts-leads",
        {"scopeKey": "function:b"},
    )

    assert archived["recordVersion"] == 2
    assert scope_a["records"][0]["state"] == "archived"
    assert scope_b["records"][0]["state"] == "active"
    assert scope_b["records"][0]["data"]["name"] == "Lead B"


@pytest.mark.asyncio
async def test_canonical_aggregate_prevents_lost_updates_and_uses_deep_merge(
    db_session,
    auth_context,
) -> None:
    adapter = OSSNativeLocalPrimaryAdapter(db_session, "bottlecrm")
    common = {
        "workspace_id": auth_context.workspace_id,
        "actor_user_id": auth_context.user_id,
        "capability_key": "accounts-contacts-leads",
        "resource_id": "lead-typed-1",
        "record_type": "lead",
        "scope_key": "aggregate:crm",
    }
    created = await adapter.execute_operation(
        **common,
        operation_id="create_record",
        effect="upsert",
        payload={
            "profile": {"name": "Ana", "phone": "1"},
            "expectedRecordVersion": 0,
        },
    )

    with pytest.raises(OSSNativeLocalValidationError, match="obrigatório"):
        await adapter.execute_operation(
            **common,
            operation_id="update_record",
            effect="upsert",
            payload={"profile": {"phone": "2"}},
        )

    updated = await adapter.execute_operation(
        **common,
        operation_id="update_record",
        effect="upsert",
        payload={
            "profile": {"phone": "2"},
            "expectedRecordVersion": created["recordVersion"],
        },
    )

    assert updated["record"]["data"] == {
        "profile": {"name": "Ana", "phone": "2"}
    }
    assert updated["aggregate"]["domain"] == "crm"
    assert updated["aggregate"]["schemaVersion"] == 1
    assert updated["aggregate"]["dataSha256"] == updated["record"]["dataSha256"]


@pytest.mark.asyncio
async def test_canonical_aggregate_never_persists_control_or_ownership_fields(
    db_session,
    auth_context,
) -> None:
    adapter = OSSNativeLocalPrimaryAdapter(db_session, "bottlecrm")
    result = await adapter.execute_operation(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        capability_key="accounts-contacts-leads",
        operation_id="create_record",
        effect="upsert",
        resource_id="lead-controls-1",
        record_type="lead",
        scope_key="aggregate:crm",
        payload={
            "name": "Lead",
            "expectedRecordVersion": 0,
            "humanApprovalId": "approval-private",
            "resourceId": "lead-controls-1",
        },
    )
    assert result["record"]["data"] == {"name": "Lead"}

    with pytest.raises(OSSNativeLocalValidationError, match="ownership"):
        await adapter.execute_operation(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            capability_key="accounts-contacts-leads",
            operation_id="create_record",
            effect="upsert",
            resource_id="lead-cross-tenant",
            record_type="lead",
            scope_key="aggregate:crm",
            payload={"workspaceId": "forged", "expectedRecordVersion": 0},
        )
