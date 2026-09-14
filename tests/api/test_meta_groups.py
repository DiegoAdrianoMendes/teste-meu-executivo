from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

import app.services.meta_groups_service as meta_groups_service_module
from app.api.v1.endpoints.meta_groups import router
from app.core.deps import get_current_auth_context, get_current_workspace_id, get_db
from app.models.omnichannel_v6 import MetaGroup, MetaGroupOperation, OmnichannelChannelConnection
from app.services.meta_groups_service import META_GROUP_REQUIRED_WEBHOOK_FIELDS
from app.services.whatsapp.meta_cloud_client import MetaCloudClient


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest_asyncio.fixture
async def meta_groups_client(db_session, auth_context):
    reference_app = FastAPI()
    reference_app.include_router(router, prefix="/api/v1")

    async def override_get_db():
        yield db_session

    async def override_get_current_auth_context():
        return auth_context

    async def override_get_current_workspace_id():
        return auth_context.workspace_id

    reference_app.dependency_overrides[get_db] = override_get_db
    reference_app.dependency_overrides[get_current_auth_context] = override_get_current_auth_context
    reference_app.dependency_overrides[get_current_workspace_id] = override_get_current_workspace_id
    async with AsyncClient(
        transport=ASGITransport(app=reference_app),
        base_url="http://test",
    ) as client:
        yield client


async def _connection(db_session, workspace_id) -> OmnichannelChannelConnection:
    connection = OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel="whatsapp",
        provider="meta_cloud",
        connection_key="meta-groups-api",
        display_name="WhatsApp Groups API",
        external_account_id="PHONE_NUMBER_API",
        transport_mode="cloud_api",
        status="configured_local",
        configuration={"phoneNumberId": "PHONE_NUMBER_API", "wabaId": "WABA_API"},
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


@pytest.mark.asyncio
async def test_typed_api_distinguishes_local_capability_execution_and_official_projection(
    meta_groups_client,
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        meta_groups_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    create_group = AsyncMock(side_effect=AssertionError("live Meta call is forbidden"))
    send_group = AsyncMock(side_effect=AssertionError("live Meta call is forbidden"))
    monkeypatch.setattr(MetaCloudClient, "create_group", create_group)
    monkeypatch.setattr(MetaCloudClient, "send_group_message_payload", send_group)
    connection = await _connection(db_session, auth_context.workspace_id)
    base = "/api/v1/klm/omnichannel/meta-groups"

    initial = await meta_groups_client.get(f"{base}/connections/{connection.id}/gate")
    assert initial.status_code == 200, initial.text
    assert initial.json()["developerAppReady"] is False
    assert initial.json()["obaGateOpen"] is False

    configured = await meta_groups_client.put(
        f"{base}/connections/{connection.id}/capability",
        headers={"Idempotency-Key": "api-capability-001"},
        json={
            "expectedVersion": 0,
            "phoneNumberId": "PHONE_NUMBER_API",
            "wabaId": "WABA_API",
            "developerAppConfigured": True,
            "webhookCallbackVerified": True,
            "subscribedWebhookFields": list(META_GROUP_REQUIRED_WEBHOOK_FIELDS),
            "obaEligibilityState": "eligible",
            "obaEvidenceSource": "meta_api",
            "obaEvidenceReference": "meta-api-evidence-api-test",
            "obaEvidenceRecordedAt": datetime.now(UTC).isoformat(),
        },
    )
    assert configured.status_code == 200, configured.text
    gate = configured.json()["gate"]
    assert gate["developerAppState"] == "ready"
    assert gate["developerAppReady"] is True
    assert gate["obaGateOpen"] is True
    assert gate["productModeSupported"] is True
    assert gate["externalCapabilityGateOpen"] is True
    assert gate["runtimeExternalActionsEnabled"] is False
    assert gate["externalExecutionGateOpen"] is False
    assert gate["externalDispatchImplemented"] is False
    assert gate["capabilityDenialReasons"] == []
    assert "runtime_external_actions_disabled" in gate["executionDenialReasons"]
    assert "external_dispatch_not_implemented" in gate["executionDenialReasons"]

    local = await meta_groups_client.post(
        f"{base}/connections/{connection.id}/groups",
        headers={"Idempotency-Key": "api-local-group-001"},
        json={"subject": "Grupo interno da equipe"},
    )
    assert local.status_code == 201, local.text
    local_group = local.json()["group"]
    assert local_group["groupKind"] == "local_internal"
    assert local_group["isLocalInternal"] is True
    assert local_group["isOfficialMetaGroup"] is False
    assert local_group["providerGroupId"] is None
    assert local.headers["X-KLM-External-Dispatch"] == "false"

    prepared = await meta_groups_client.post(
        f"{base}/connections/{connection.id}/official-groups/prepare",
        headers={"Idempotency-Key": "api-official-prepare-001"},
        json={
            "subject": "Grupo oficial de clientes",
            "description": "Acesso por convite.",
            "joinApprovalMode": "approval_required",
        },
    )
    assert prepared.status_code == 202, prepared.text
    prepared_payload = prepared.json()
    assert prepared_payload["groupProjectionCreated"] is False
    assert prepared_payload["projectionSource"] == "verified_group_lifecycle_update"
    assert prepared_payload["providerRequestPayloadReady"] is True
    assert prepared_payload["providerRequestPath"] == "/PHONE_NUMBER_API/groups"
    assert prepared_payload["operation"]["groupId"] is None
    assert prepared_payload["externalDispatchExecuted"] is False
    create_group.assert_not_awaited()

    lifecycle = await meta_groups_client.post(
        f"{base}/connections/{connection.id}/lifecycle-events",
        json={
            "webhookField": "group_lifecycle_update",
            "externalEventId": "api-provider-group-created-001",
            "providerGroupId": "GROUP_ID_API_001",
            "preparedOperationId": prepared_payload["operation"]["id"],
            "eventType": "group_created",
            "providerRequestId": "api-meta-request-001",
            "status": "active",
            "subject": "Grupo oficial de clientes",
            "description": "Acesso por convite.",
            "joinApprovalMode": "approval_required",
            "occurredAt": datetime.now(UTC).isoformat(),
            "ingressEvidenceSource": "verified_provider_adapter",
            "ingressEvidenceReference": "signed-provider-envelope-api-001",
        },
    )
    assert lifecycle.status_code == 202, lifecycle.text
    official = lifecycle.json()["group"]
    assert official["groupKind"] == "meta_cloud_group"
    assert official["isLocalInternal"] is False
    assert official["isOfficialMetaGroup"] is True
    assert official["providerGroupId"] == "GROUP_ID_API_001"

    listed = await meta_groups_client.get(f"{base}/groups")
    assert listed.status_code == 200, listed.text
    assert {item["groupKind"] for item in listed.json()["items"]} == {
        "local_internal",
        "meta_cloud_group",
    }
    assert await db_session.scalar(select(func.count()).select_from(MetaGroup)) == 2
    assert (
        await db_session.scalar(
            select(func.count(MetaGroupOperation.id)).where(
                MetaGroupOperation.operation_type == "prepare_official_group_creation"
            )
        )
        == 1
    )

    unsupported = await meta_groups_client.post(
        f"{base}/groups/{official['id']}/messages/prepare",
        headers={"Idempotency-Key": "api-group-interactive-001"},
        json={
            "message": {
                "type": "interactive",
                "interactive": {"type": "button", "body": {"text": "Escolha"}},
            }
        },
    )
    assert unsupported.status_code == 422, unsupported.text
    assert unsupported.json()["detail"]["code"] == "META-GROUPS-INVALID"
    send_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_thread_invite_link_participants_and_pin_endpoints(
    meta_groups_client,
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        meta_groups_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    connection = await _connection(db_session, auth_context.workspace_id)
    base = "/api/v1/klm/omnichannel/meta-groups"

    local = await meta_groups_client.post(
        f"{base}/connections/{connection.id}/groups",
        headers={"Idempotency-Key": "api-local-group-thread-001"},
        json={"subject": "Grupo do caso Silva"},
    )
    assert local.status_code == 201, local.text
    group_id = local.json()["group"]["id"]
    assert local.json()["group"]["participantCount"] == 1

    message_prepared = await meta_groups_client.post(
        f"{base}/groups/{group_id}/messages/prepare",
        headers={"Idempotency-Key": "api-group-thread-message-001"},
        json={"message": {"type": "text", "text": {"body": "Audiência marcada para sexta"}}},
    )
    assert message_prepared.status_code == 202, message_prepared.text

    thread = await meta_groups_client.get(f"{base}/groups/{group_id}/messages")
    assert thread.status_code == 200, thread.text
    assert thread.json()["total"] == 1
    assert thread.json()["items"][0]["text"] == "Audiência marcada para sexta"

    listed = await meta_groups_client.get(f"{base}/groups")
    assert listed.status_code == 200, listed.text
    listed_group = next(item for item in listed.json()["items"] if item["id"] == group_id)
    assert listed_group["lastMessagePreview"] == "Audiência marcada para sexta"

    rotated = await meta_groups_client.post(
        f"{base}/groups/{group_id}/invite-link/rotate",
        headers={"Idempotency-Key": "api-invite-rotate-001"},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["rotated"] is True
    assert rotated.json()["simulated"] is True
    assert rotated.json()["group"]["inviteLink"]

    participants = await meta_groups_client.get(f"{base}/groups/{group_id}/participants")
    assert participants.status_code == 200, participants.text
    assert participants.json()["total"] == 1
    participant_id = participants.json()["items"][0]["id"]

    blocked_removal = await meta_groups_client.request(
        "DELETE",
        f"{base}/groups/{group_id}/participants/{participant_id}",
        headers={"Idempotency-Key": "api-remove-last-participant-001"},
    )
    assert blocked_removal.status_code == 422, blocked_removal.text

    pin = await meta_groups_client.put(
        f"{base}/groups/{group_id}/pinned-message",
        headers={"Idempotency-Key": "api-pin-001"},
        json={"text": "Levar procuração original na audiência"},
    )
    assert pin.status_code == 200, pin.text
    assert pin.json()["pinnedMessage"]["text"] == "Levar procuração original na audiência"

    unpin = await meta_groups_client.request(
        "DELETE",
        f"{base}/groups/{group_id}/pinned-message",
        headers={"Idempotency-Key": "api-unpin-001"},
    )
    assert unpin.status_code == 200, unpin.text
    assert unpin.json()["pinnedMessage"] is None
