"""Adversarial tests for workspace-scoped OSS bridge persistence."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oss_bridge import (
    REDACTED_SECRET,
    ExternalEntityLink,
    ExternalEntitySyncState,
    IntegrationEventReceipt,
    IntegrationEventReceiptStatus,
    OSSBridgeProvider,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.oss_bridge import (
    ExternalEntityLinkRead,
    ExternalEntityLinkUpsert,
    IntegrationEventReceiptCreate,
)
from app.services.oss_bridge_service import (
    ExternalEntityLinkConflictError,
    IntegrationEventPayloadConflictError,
    OSSBridgeService,
    compute_payload_hash,
)


async def _workspace(db: AsyncSession, label: str) -> Workspace:
    workspace = Workspace(
        name=f"OSS Bridge {label}",
        slug=f"oss-bridge-{label}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()
    return workspace


def _link_data(
    *,
    klm_entity_id: UUID,
    external_entity_id: str = "conversation-42",
    provider: OSSBridgeProvider = OSSBridgeProvider.chatwoot,
    metadata: dict[str, Any] | None = None,
    sync_state: ExternalEntitySyncState = ExternalEntitySyncState.pending,
) -> ExternalEntityLinkUpsert:
    return ExternalEntityLinkUpsert(
        provider=provider,
        klm_entity_type="matter",
        klm_entity_id=klm_entity_id,
        external_account_id="account-7",
        external_entity_type="conversation",
        external_entity_id=external_entity_id,
        metadata=metadata or {},
        sync_state=sync_state,
    )


@pytest.mark.asyncio
async def test_link_upsert_and_lookups_are_workspace_scoped(db_session: AsyncSession):
    workspace_a = await _workspace(db_session, "a")
    workspace_b = await _workspace(db_session, "b")
    klm_entity_id = uuid4()
    service = OSSBridgeService(db_session)

    link_a = await service.upsert_link(
        workspace_a.id,
        _link_data(klm_entity_id=klm_entity_id),
    )

    assert await service.get_link(workspace_b.id, link_a.id) is None
    assert (
        await service.find_link_by_external_entity(
            workspace_b.id,
            OSSBridgeProvider.chatwoot,
            "account-7",
            "conversation",
            "conversation-42",
        )
        is None
    )
    assert (
        await service.find_link_by_klm_entity(
            workspace_b.id,
            OSSBridgeProvider.chatwoot,
            "account-7",
            "matter",
            klm_entity_id,
            "conversation",
        )
        is None
    )

    link_b = await service.upsert_link(
        workspace_b.id,
        _link_data(klm_entity_id=klm_entity_id),
    )
    assert link_b.id != link_a.id

    updated_a = await service.upsert_link(
        workspace_a.id,
        _link_data(
            klm_entity_id=klm_entity_id,
            external_entity_id="conversation-99",
            sync_state=ExternalEntitySyncState.synced,
        ),
    )
    assert updated_a.id == link_a.id
    assert updated_a.external_entity_id == "conversation-99"
    assert updated_a.sync_state == ExternalEntitySyncState.synced
    assert link_b.external_entity_id == "conversation-42"
    assert (
        await service.find_link_by_external_entity(
            workspace_a.id,
            OSSBridgeProvider.chatwoot,
            "account-7",
            "conversation",
            "conversation-42",
        )
        is None
    )


@pytest.mark.asyncio
async def test_link_rejects_external_identity_rebinding_only_within_workspace(
    db_session: AsyncSession,
):
    workspace_a = await _workspace(db_session, "conflict-a")
    workspace_b = await _workspace(db_session, "conflict-b")
    service = OSSBridgeService(db_session)

    original = await service.upsert_link(
        workspace_a.id,
        _link_data(klm_entity_id=uuid4()),
    )
    with pytest.raises(ExternalEntityLinkConflictError):
        await service.upsert_link(
            workspace_a.id,
            _link_data(klm_entity_id=uuid4()),
        )

    cross_workspace = await service.upsert_link(
        workspace_b.id,
        _link_data(klm_entity_id=uuid4()),
    )
    assert cross_workspace.id != original.id
    count_a = await db_session.scalar(
        select(func.count())
        .select_from(ExternalEntityLink)
        .where(ExternalEntityLink.workspace_id == workspace_a.id)
    )
    assert count_a == 1


@pytest.mark.asyncio
async def test_bottlecrm_organization_cannot_bind_two_workspaces(
    db_session: AsyncSession,
):
    workspace_a = await _workspace(db_session, "bottle-binding-a")
    workspace_b = await _workspace(db_session, "bottle-binding-b")
    service = OSSBridgeService(db_session)

    def binding(workspace_id: UUID) -> ExternalEntityLinkUpsert:
        return ExternalEntityLinkUpsert(
            provider=OSSBridgeProvider.bottlecrm,
            klm_entity_type="workspace",
            klm_entity_id=workspace_id,
            external_account_id="bottle-organization-1",
            external_entity_type="organization",
            external_entity_id="bottle-organization-1",
            sync_state=ExternalEntitySyncState.synced,
        )

    await service.upsert_link(workspace_a.id, binding(workspace_a.id))
    with pytest.raises(ExternalEntityLinkConflictError):
        await service.upsert_link(workspace_b.id, binding(workspace_b.id))


@pytest.mark.asyncio
async def test_link_metadata_is_recursively_sanitized_without_mutating_input(
    db_session: AsyncSession,
):
    workspace = await _workspace(db_session, "metadata")
    service = OSSBridgeService(db_session)
    raw_metadata = {
        "safe": "visible",
        "apiKey": "api-key-value",
        "nested": {
            "authorization": "Bearer nested-secret",
            "items": [{"client_secret": "client-secret-value"}],
        },
        "header": "Bearer generic-secret",
    }

    link = await service.upsert_link(
        workspace.id,
        _link_data(klm_entity_id=uuid4(), metadata=raw_metadata),
    )
    assert raw_metadata["apiKey"] == "api-key-value"
    assert link.link_metadata == {
        "safe": "visible",
        "apiKey": REDACTED_SECRET,
        "nested": {
            "authorization": REDACTED_SECRET,
            "items": [{"client_secret": REDACTED_SECRET}],
        },
        "header": REDACTED_SECRET,
    }
    assert "nested-secret" not in str(link.link_metadata)

    read_model = ExternalEntityLinkRead.model_validate(link)
    assert read_model.metadata == link.link_metadata


@pytest.mark.asyncio
async def test_receipt_deduplicates_canonical_payload(db_session: AsyncSession):
    workspace = await _workspace(db_session, "receipt-idempotent")
    service = OSSBridgeService(db_session)

    first, first_deduplicated = await service.register_receipt(
        workspace.id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.rocketchat,
            external_event_id="event-1",
            payload={"b": [2, 3], "a": {"value": 1}},
        ),
    )
    replay, replay_deduplicated = await service.register_receipt(
        workspace.id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.rocketchat,
            external_event_id="event-1",
            payload={"a": {"value": 1}, "b": [2, 3]},
            status=IntegrationEventReceiptStatus.processed,
        ),
    )

    assert first_deduplicated is False
    assert replay_deduplicated is True
    assert replay.id == first.id
    assert replay.status == IntegrationEventReceiptStatus.received
    assert first.received_at is not None
    assert first.created_at is not None
    assert first.updated_at is not None
    count = await db_session.scalar(
        select(func.count())
        .select_from(IntegrationEventReceipt)
        .where(IntegrationEventReceipt.workspace_id == workspace.id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_receipt_processing_lease_is_atomic_and_fenced(db_session: AsyncSession):
    workspace = await _workspace(db_session, "receipt-lease")
    service = OSSBridgeService(db_session)
    receipt, _ = await service.register_receipt(
        workspace.id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.rocketchat,
            external_event_id="rocket-event-lease",
            payload={"message_id": "m-lease"},
        ),
    )
    await db_session.commit()

    lease = await service.claim_receipt(
        workspace.id,
        OSSBridgeProvider.rocketchat,
        receipt.id,
    )

    assert lease is not None
    assert await service.claim_receipt(
        workspace.id,
        OSSBridgeProvider.rocketchat,
        receipt.id,
    ) is None
    assert await service.finish_receipt(
        workspace.id,
        OSSBridgeProvider.rocketchat,
        receipt.id,
        lease + timedelta(microseconds=1),
        IntegrationEventReceiptStatus.processed,
    ) is False
    assert await service.finish_receipt(
        workspace.id,
        OSSBridgeProvider.rocketchat,
        receipt.id,
        lease,
        IntegrationEventReceiptStatus.processed,
    ) is True
    await db_session.commit()
    await db_session.refresh(receipt)
    assert receipt.status == IntegrationEventReceiptStatus.processed


@pytest.mark.asyncio
async def test_receipt_rejects_same_event_id_with_different_payload(db_session: AsyncSession):
    workspace = await _workspace(db_session, "receipt-conflict")
    service = OSSBridgeService(db_session)

    original, _ = await service.register_receipt(
        workspace.id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.chattermate,
            external_event_id="event-conflict",
            payload={"event": "message.created", "messageId": "m-1"},
        ),
    )
    with pytest.raises(IntegrationEventPayloadConflictError):
        await service.register_receipt(
            workspace.id,
            IntegrationEventReceiptCreate(
                provider=OSSBridgeProvider.chattermate,
                external_event_id="event-conflict",
                payload={"event": "message.created", "messageId": "m-2"},
            ),
        )

    persisted = await service.find_receipt(
        workspace.id,
        OSSBridgeProvider.chattermate,
        "event-conflict",
    )
    assert persisted is not None
    assert persisted.id == original.id
    assert persisted.payload["messageId"] == "m-1"


@pytest.mark.asyncio
async def test_receipt_idempotency_is_scoped_by_workspace_and_provider(
    db_session: AsyncSession,
):
    workspace_a = await _workspace(db_session, "receipt-scope-a")
    workspace_b = await _workspace(db_session, "receipt-scope-b")
    service = OSSBridgeService(db_session)

    receipt_a, _ = await service.register_receipt(
        workspace_a.id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.bottlecrm,
            external_event_id="shared-event",
            payload={"workspace": "a"},
        ),
    )
    receipt_b, _ = await service.register_receipt(
        workspace_b.id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.bottlecrm,
            external_event_id="shared-event",
            payload={"workspace": "b"},
        ),
    )
    other_provider, deduplicated = await service.register_receipt(
        workspace_a.id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.chatwoot,
            external_event_id="shared-event",
            payload={"provider": "chatwoot"},
        ),
    )

    assert receipt_a.id != receipt_b.id
    assert other_provider.id != receipt_a.id
    assert deduplicated is False
    assert await service.get_receipt(workspace_b.id, receipt_a.id) is None
    assert (
        await service.find_receipt(
            workspace_a.id,
            OSSBridgeProvider.bottlecrm,
            "shared-event",
        )
    ).id == receipt_a.id


@pytest.mark.asyncio
async def test_receipt_payload_and_hash_are_both_independent_of_secret_values(
    db_session: AsyncSession,
):
    workspace = await _workspace(db_session, "receipt-secrets")
    service = OSSBridgeService(db_session)
    raw_payload = {
        "event": "contact.updated",
        "access_token": "first-token-value",
        "otp_code": "123456",
        "backup_code": "backup-value",
        "nested": [{"password": "password-value"}],
        "header": "Basic dXNlcjpwYXNz",
    }
    data = IntegrationEventReceiptCreate(
        provider=OSSBridgeProvider.chatwoot,
        external_event_id="secret-event",
        payload=raw_payload,
    )

    receipt, _ = await service.register_receipt(workspace.id, data)
    assert raw_payload["access_token"] == "first-token-value"
    assert receipt.payload == {
        "event": "contact.updated",
        "access_token": REDACTED_SECRET,
        "otp_code": REDACTED_SECRET,
        "backup_code": REDACTED_SECRET,
        "nested": [{"password": REDACTED_SECRET}],
        "header": REDACTED_SECRET,
    }
    assert receipt.payload_hash == compute_payload_hash(receipt.payload)
    assert "first-token-value" not in str(receipt.payload)
    assert "password-value" not in str(receipt.payload)
    assert "123456" not in str(receipt.payload)
    assert "backup-value" not in str(receipt.payload)

    replay, deduplicated = await service.register_receipt(
        workspace.id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.chatwoot,
            external_event_id="secret-event",
            payload={**raw_payload, "access_token": "second-token-value"},
        ),
    )
    assert replay.id == receipt.id
    assert replay.payload_hash == receipt.payload_hash
    assert deduplicated is True

    with pytest.raises(IntegrationEventPayloadConflictError):
        await service.register_receipt(
            workspace.id,
            IntegrationEventReceiptCreate(
                provider=OSSBridgeProvider.chatwoot,
                external_event_id="secret-event",
                payload={**raw_payload, "event": "contact.deleted"},
            ),
        )


@pytest.mark.asyncio
async def test_models_sanitize_direct_json_assignment(db_session: AsyncSession):
    workspace = await _workspace(db_session, "direct-model")
    link = ExternalEntityLink(
        workspace_id=workspace.id,
        provider=OSSBridgeProvider.chatwoot,
        klm_entity_type="client",
        klm_entity_id=uuid4(),
        external_account_id="account-direct",
        external_entity_type="contact",
        external_entity_id="contact-direct",
        link_metadata={"session_cookie": "cookie-value", "safe": "kept"},
    )
    raw_payload = {"refreshToken": "refresh-value", "event": "direct"}
    receipt = IntegrationEventReceipt(
        workspace_id=workspace.id,
        provider=OSSBridgeProvider.rocketchat,
        external_event_id="direct-event",
        payload_hash=compute_payload_hash(raw_payload),
        payload=raw_payload,
    )
    db_session.add_all([link, receipt])
    await db_session.flush()

    assert link.link_metadata == {"session_cookie": REDACTED_SECRET, "safe": "kept"}
    assert receipt.payload == {"refreshToken": REDACTED_SECRET, "event": "direct"}
