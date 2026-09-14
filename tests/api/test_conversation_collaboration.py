"""Focused API contracts for native conversation collaboration."""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

import app.services.conversation_collaboration_service as collaboration_service_module
from app.api.v1.endpoints.conversation_collaboration import router
from app.core.deps import (
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
)
from app.models.conversation_collaboration import (
    ConversationCollaborationGroup,
    ConversationGroupParticipant,
    ConversationMention,
    ConversationMentionOperation,
    ConversationMessageReference,
)
from app.models.klm_operations import (
    KlmAuditEvent,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.omnichannel_v6 import WorkspaceTeam
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


@pytest_asyncio.fixture
async def collaboration_client(db_session, auth_context, active_subscription):
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


async def _thread_with_messages(db_session, workspace_id, *, count: int = 3):
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        primary_channel=OmnichannelChannel.internal,
        subject=f"Collaboration thread {uuid4()}",
    )
    db_session.add(thread)
    await db_session.flush()
    messages = [
        OmnichannelMessage(
            workspace_id=workspace_id,
            thread_id=thread.id,
            channel=OmnichannelChannel.internal,
            direction=MessageDirection.internal,
            author_type=MessageAuthorType.human,
            author_label="KLM operator",
            body=f"Internal message {index}",
            idempotency_key=f"collaboration-message-{uuid4()}",
            delivery_status=MessageDeliveryStatus.received,
        )
        for index in range(count)
    ]
    db_session.add_all(messages)
    await db_session.flush()
    return thread, messages


async def _team(db_session, workspace_id, *, name: str = "Legal team") -> WorkspaceTeam:
    team = WorkspaceTeam(
        workspace_id=workspace_id,
        name=name,
        slug=f"team-{uuid4().hex}",
    )
    db_session.add(team)
    await db_session.flush()
    return team


async def _other_workspace(db_session) -> Workspace:
    workspace = Workspace(
        name="Other workspace",
        slug=f"other-workspace-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    return workspace


def _reference_path(thread_id) -> str:
    return f"/api/v1/klm/omnichannel/collaboration/threads/{thread_id}/references"


def _participant_path(thread_id) -> str:
    return f"/api/v1/klm/omnichannel/collaboration/threads/{thread_id}/participants"


def _mention_path(thread_id) -> str:
    return f"/api/v1/klm/omnichannel/collaboration/threads/{thread_id}/mentions"


def _message_mentions_path(thread_id, message_id) -> str:
    return (
        f"/api/v1/klm/omnichannel/collaboration/threads/{thread_id}/messages/{message_id}/mentions"
    )


@pytest.mark.asyncio
async def test_reference_mentions_are_resolved_audited_and_idempotent(
    collaboration_client,
    db_session,
    auth_context,
):
    thread, messages = await _thread_with_messages(
        db_session,
        auth_context.workspace_id,
    )
    team = await _team(db_session, auth_context.workspace_id)
    payload = {
        "sourceMessageId": str(messages[2].id),
        "targetMessageId": str(messages[0].id),
        "referenceType": "reply",
        "mentions": [
            {"mentionType": "user", "targetId": str(auth_context.user_id)},
            {"mentionType": "team", "targetId": str(team.id)},
        ],
        "idempotencyKey": "reference-replay-001",
    }

    created = await collaboration_client.post(_reference_path(thread.id), json=payload)
    replay = await collaboration_client.post(_reference_path(thread.id), json=payload)

    assert created.status_code == 201, created.text
    assert created.headers["X-KLM-Deduplicated"] == "false"
    assert replay.status_code == 201, replay.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert replay.json()["id"] == created.json()["id"]
    mentions = created.json()["mentions"]
    assert {(item["mentionType"], item["resolutionSource"]) for item in mentions} == {
        ("user", "workspace_membership"),
        ("team", "workspace_team"),
    }
    user_mention = next(item for item in mentions if item["mentionType"] == "user")
    team_mention = next(item for item in mentions if item["mentionType"] == "team")
    assert user_mention["resolvedLabel"].strip()
    assert team_mention["resolvedLabel"] == "Legal team"

    assert (
        await db_session.scalar(select(func.count()).select_from(ConversationMessageReference)) == 1
    )
    assert await db_session.scalar(select(func.count()).select_from(ConversationMention)) == 2
    created_audits = int(
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "conversation_reference_created",
            )
        )
        or 0
    )
    mention_audits = int(
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "conversation_mention_resolved",
            )
        )
        or 0
    )
    assert created_audits == 1
    assert mention_audits == 2

    listed = await collaboration_client.get(_reference_path(thread.id))
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [created.json()["id"]]
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "conversation_references_listed",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_standalone_mentions_replay_and_reconstruct_all_mentions_by_message(
    collaboration_client,
    db_session,
    auth_context,
):
    thread, messages = await _thread_with_messages(db_session, auth_context.workspace_id)
    team = await _team(db_session, auth_context.workspace_id)
    payload = {
        "sourceMessageId": str(messages[2].id),
        "mentions": [
            {"mentionType": "user", "targetId": str(auth_context.user_id)},
            {"mentionType": "team", "targetId": str(team.id)},
        ],
        "idempotencyKey": "standalone-mentions-001",
    }

    created = await collaboration_client.post(_mention_path(thread.id), json=payload)
    replay = await collaboration_client.post(_mention_path(thread.id), json=payload)
    changed = await collaboration_client.post(
        _mention_path(thread.id),
        json={**payload, "mentions": payload["mentions"][:1]},
    )
    cross_operation_collision = await collaboration_client.post(
        _reference_path(thread.id),
        json={
            "sourceMessageId": str(messages[2].id),
            "targetMessageId": str(messages[0].id),
            "referenceType": "reply",
            "mentions": payload["mentions"],
            "idempotencyKey": payload["idempotencyKey"],
        },
    )

    assert created.status_code == 201, created.text
    assert created.headers["X-KLM-Deduplicated"] == "false"
    assert replay.status_code == 201, replay.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert [item["id"] for item in replay.json()] == [item["id"] for item in created.json()]
    assert changed.status_code == 409
    assert cross_operation_collision.status_code == 409
    assert {item["referenceId"] for item in created.json()} == {None}
    assert len({item["operationId"] for item in created.json()}) == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(ConversationMentionOperation)) == 1
    )
    assert await db_session.scalar(select(func.count()).select_from(ConversationMention)) == 2
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "conversation_mentions_attached",
            )
        )
        == 1
    )

    reference = await collaboration_client.post(
        _reference_path(thread.id),
        json={
            "sourceMessageId": str(messages[2].id),
            "targetMessageId": str(messages[0].id),
            "referenceType": "context",
            "mentions": [
                {"mentionType": "user", "targetId": str(auth_context.user_id)},
            ],
            "idempotencyKey": "reference-with-message-mentions-001",
        },
    )
    listed = await collaboration_client.get(_message_mentions_path(thread.id, messages[2].id))

    assert reference.status_code == 201, reference.text
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 3
    assert sum(item["referenceId"] is None for item in listed.json()) == 2
    assert sum(item["referenceId"] == reference.json()["id"] for item in listed.json()) == 1
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "conversation_message_mentions_listed",
            )
        )
        == 1
    )

    other_thread, _ = await _thread_with_messages(
        db_session,
        auth_context.workspace_id,
        count=1,
    )
    cross_thread = await collaboration_client.post(
        _mention_path(other_thread.id),
        json={**payload, "idempotencyKey": "standalone-mentions-cross-thread-001"},
    )
    assert cross_thread.status_code == 404


@pytest.mark.asyncio
async def test_reference_rejects_key_conflicts_cross_thread_and_cross_tenant_messages(
    collaboration_client,
    db_session,
    auth_context,
):
    thread, messages = await _thread_with_messages(db_session, auth_context.workspace_id)
    other_thread, other_messages = await _thread_with_messages(
        db_session,
        auth_context.workspace_id,
        count=1,
    )
    other_workspace = await _other_workspace(db_session)
    _, tenant_messages = await _thread_with_messages(db_session, other_workspace.id, count=1)
    base = {
        "sourceMessageId": str(messages[2].id),
        "targetMessageId": str(messages[0].id),
        "referenceType": "quote",
        "idempotencyKey": "reference-boundary-001",
    }

    created = await collaboration_client.post(_reference_path(thread.id), json=base)
    changed = await collaboration_client.post(
        _reference_path(thread.id),
        json={**base, "targetMessageId": str(messages[1].id)},
    )
    cross_thread = await collaboration_client.post(
        _reference_path(thread.id),
        json={
            **base,
            "idempotencyKey": "reference-cross-thread-001",
            "targetMessageId": str(other_messages[0].id),
        },
    )
    cross_tenant = await collaboration_client.post(
        _reference_path(thread.id),
        json={
            **base,
            "idempotencyKey": "reference-cross-tenant-001",
            "targetMessageId": str(tenant_messages[0].id),
        },
    )

    assert created.status_code == 201, created.text
    assert changed.status_code == 409
    assert cross_thread.status_code == 404
    assert cross_tenant.status_code == 404
    assert other_thread.id != thread.id
    assert (
        await db_session.scalar(select(func.count()).select_from(ConversationMessageReference)) == 1
    )


@pytest.mark.asyncio
async def test_participant_group_roles_are_resolved_tenant_scoped_and_idempotent(
    collaboration_client,
    db_session,
    auth_context,
):
    thread, _ = await _thread_with_messages(db_session, auth_context.workspace_id)
    team = await _team(db_session, auth_context.workspace_id)
    user_payload = {
        "participantType": "user",
        "participantId": str(auth_context.user_id),
        "role": "owner",
        "idempotencyKey": "participant-user-001",
    }
    team_payload = {
        "participantType": "team",
        "participantId": str(team.id),
        "role": "observer",
        "idempotencyKey": "participant-team-001",
    }

    user_created = await collaboration_client.post(_participant_path(thread.id), json=user_payload)
    user_replay = await collaboration_client.post(_participant_path(thread.id), json=user_payload)
    team_created = await collaboration_client.post(_participant_path(thread.id), json=team_payload)
    duplicate_user = await collaboration_client.post(
        _participant_path(thread.id),
        json={**user_payload, "idempotencyKey": "participant-user-duplicate-002"},
    )

    assert user_created.status_code == 201, user_created.text
    assert user_created.json()["role"] == "owner"
    assert user_created.json()["resolutionSource"] == "workspace_membership"
    assert user_replay.status_code == 201
    assert user_replay.headers["X-KLM-Deduplicated"] == "true"
    assert team_created.status_code == 201, team_created.text
    assert team_created.json()["role"] == "observer"
    assert duplicate_user.status_code == 409

    listed = await collaboration_client.get(_participant_path(thread.id))
    assert listed.status_code == 200, listed.text
    assert listed.json()["group"]["threadId"] == str(thread.id)
    assert {item["participantType"] for item in listed.json()["participants"]} == {
        "user",
        "team",
    }
    assert (
        await db_session.scalar(select(func.count()).select_from(ConversationCollaborationGroup))
        == 1
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(ConversationGroupParticipant)) == 2
    )
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "conversation_group_created",
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.event_type == "conversation_participant_created",
            )
        )
        == 2
    )

    other_workspace = await _other_workspace(db_session)
    foreign_team = await _team(db_session, other_workspace.id, name="Foreign team")
    rejected = await collaboration_client.post(
        _participant_path(thread.id),
        json={
            "participantType": "team",
            "participantId": str(foreign_team.id),
            "role": "member",
            "idempotencyKey": "participant-cross-tenant-001",
        },
    )
    assert rejected.status_code == 404


@pytest.mark.asyncio
async def test_capabilities_fail_closed_then_support_only_verified_linked_provider(
    collaboration_client,
    db_session,
    auth_context,
    monkeypatch,
):
    thread, _ = await _thread_with_messages(db_session, auth_context.workspace_id)
    contracts = {
        capability_id: {
            "contractEligible": True,
            "currentExposure": "full_app",
            "disposition": "approved_full_app",
            "externalEffectVerified": False,
            "reason": "Official contract exists but provider effect is unverified.",
        }
        for provider in collaboration_service_module._PROVIDER_CAPABILITY_IDS.values()
        for capability_id in provider.values()
        if capability_id is not None
    }
    monkeypatch.setattr(
        collaboration_service_module,
        "_official_provider_contracts",
        lambda: contracts,
    )
    monkeypatch.setattr(
        collaboration_service_module,
        "provider_collaboration_gate_open",
        lambda: False,
    )
    monkeypatch.setattr(
        collaboration_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    path = f"/api/v1/klm/omnichannel/collaboration/threads/{thread.id}/capabilities"

    blocked = await collaboration_client.get(path)

    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["localBridgeGateOpen"] is False
    native = [item for item in blocked.json()["capabilities"] if item["scope"] == "native"]
    providers = [item for item in blocked.json()["capabilities"] if item["scope"] == "provider"]
    assert len(native) == 5
    assert {item["status"] for item in native} == {"supported"}
    assert "supported" not in {item["status"] for item in providers}
    assert {item["status"] for item in providers} == {
        "pending_enablement",
        "unavailable",
    }

    db_session.add(
        ExternalEntityLink(
            workspace_id=auth_context.workspace_id,
            provider=OSSBridgeProvider.rocketchat,
            klm_entity_type="omnichannel_thread",
            klm_entity_id=thread.id,
            external_account_id="rocketchat-test-account",
            external_entity_type="room",
            external_entity_id="room-collaboration-test",
            link_metadata={"scope": "internal_team"},
            sync_state=ExternalEntitySyncState.synced,
        )
    )
    await db_session.flush()
    contracts["rocketchat.threads-discussions"] = {
        **contracts["rocketchat.threads-discussions"],
        "externalEffectVerified": True,
    }
    monkeypatch.setattr(
        collaboration_service_module,
        "provider_collaboration_gate_open",
        lambda: True,
    )

    enabled = await collaboration_client.get(path)

    assert enabled.status_code == 200, enabled.text
    structured = next(
        item
        for item in enabled.json()["capabilities"]
        if item["provider"] == "rocketchat" and item["capability"] == "structured_references"
    )
    assert structured["status"] == "supported"
    assert structured["providerLinked"] is True
    assert structured["gateOpen"] is True
    assert structured["externalEffectVerified"] is True


def test_models_expose_composite_tenant_and_thread_foreign_keys():
    reference_fks = {
        tuple(constraint.column_keys)
        for constraint in ConversationMessageReference.__table__.foreign_key_constraints
    }
    mention_fks = {
        tuple(constraint.column_keys)
        for constraint in ConversationMention.__table__.foreign_key_constraints
    }
    operation_fks = {
        tuple(constraint.column_keys)
        for constraint in ConversationMentionOperation.__table__.foreign_key_constraints
    }
    participant_fks = {
        tuple(constraint.column_keys)
        for constraint in ConversationGroupParticipant.__table__.foreign_key_constraints
    }

    assert ("workspace_id", "thread_id") in reference_fks
    assert ("workspace_id", "source_message_id") in reference_fks
    assert ("workspace_id", "target_message_id") in reference_fks
    assert ("workspace_id", "thread_id") in mention_fks
    assert (
        "workspace_id",
        "thread_id",
        "source_message_id",
        "operation_id",
    ) in mention_fks
    assert (
        "workspace_id",
        "thread_id",
        "source_message_id",
        "reference_id",
    ) in mention_fks
    assert ("workspace_id", "mentioned_user_id") in mention_fks
    assert ("workspace_id", "mentioned_team_id") in mention_fks
    assert ("workspace_id", "thread_id") in operation_fks
    assert ("workspace_id", "source_message_id") in operation_fks
    assert (
        "workspace_id",
        "thread_id",
        "source_message_id",
        "reference_id",
    ) in operation_fks
    assert ("workspace_id", "thread_id", "group_id") in participant_fks
    assert ("workspace_id", "user_id") in participant_fks
    assert ("workspace_id", "team_id") in participant_fks


def test_provider_contract_ids_are_present_in_the_official_registry():
    contracts = collaboration_service_module._official_provider_contracts()
    expected = {
        capability_id
        for capabilities in collaboration_service_module._PROVIDER_CAPABILITY_IDS.values()
        for capability_id in capabilities.values()
        if capability_id is not None
    }

    assert expected <= set(contracts)
