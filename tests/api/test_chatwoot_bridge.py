"""Signed Chatwoot webhook ingestion and tenant-safe persistence."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.tenant import get_workspace_context
from app.models.client import Client
from app.models.klm_operations import (
    ExternalActionIntent,
    KlmAuditEvent,
    MessageDeliveryStatus,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import Matter
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    IntegrationEventReceipt,
    IntegrationEventReceiptStatus,
    OSSBridgeProvider,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.oss_bridge import IntegrationEventReceiptCreate
from app.services.chatwoot_bridge_service import (
    CHATWOOT_RECEIPT_LEASE_TIMEOUT,
    ChatwootBridgeService,
)
from app.services.oss_bridge_service import OSSBridgeService


@pytest_asyncio.fixture
async def unauthenticated_client(oss_unauthenticated_adapter_client):
    yield oss_unauthenticated_adapter_client


def _payload(
    *, message_id: int = 9001, message_type: str = "incoming", private: bool = False
) -> dict:
    return {
        "event": "message_created",
        "id": message_id,
        "content": "Meu plano negou o medicamento e tenho a negativa em PDF.",
        "message_type": message_type,
        "private": private,
        "status": "sent",
        "created_at": int(time.time()),
        "account": {"id": 1, "name": "KLM local"},
        "conversation": {
            "id": 77,
            "account_id": 1,
            "inbox": {"id": 2, "name": "WhatsApp Saúde", "channel_type": "Channel::Whatsapp"},
            "meta": {
                "sender": {
                    "id": 55,
                    "name": "Ana Chatwoot",
                    "email": "ana.chatwoot@example.com",
                    "phone_number": "+5511999990000",
                    "type": "contact",
                }
            },
        },
        "sender": {
            "id": 55,
            "name": "Ana Chatwoot",
            "email": "ana.chatwoot@example.com",
            "phone_number": "+5511999990000",
            "type": "contact" if message_type == "incoming" else "user",
        },
        "attachments": [
            {
                "id": 31,
                "file_type": "file",
                "file_name": "negativa.pdf",
                "data_url": "http://127.0.0.1:3100/rails/active_storage/blob/31?token=never-store",
            }
        ],
    }


def _request_body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _signed_headers(
    body: bytes, secret: str, delivery_id: str, timestamp: int | None = None
) -> dict:
    timestamp_text = str(timestamp or int(time.time()))
    signature = (
        "sha256="
        + hmac.new(
            secret.encode(), timestamp_text.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
    )
    return {
        "Content-Type": "application/json",
        "X-Chatwoot-Delivery": delivery_id,
        "X-Chatwoot-Timestamp": timestamp_text,
        "X-Chatwoot-Signature": signature,
    }


async def _bind_chatwoot_account(db_session, workspace_id, account_id: str = "1") -> None:
    db_session.add(
        ExternalEntityLink(
            workspace_id=workspace_id,
            provider=OSSBridgeProvider.chatwoot,
            klm_entity_type="workspace",
            klm_entity_id=workspace_id,
            external_account_id=account_id,
            external_entity_type="account",
            external_entity_id=account_id,
            link_metadata={"binding": "chatwoot_account"},
            sync_state=ExternalEntitySyncState.synced,
        )
    )
    await db_session.flush()


async def _register_processing_receipt(
    db_session,
    workspace_id,
    delivery_id: str,
    payload: dict,
    *,
    lease_started_at: datetime,
) -> IntegrationEventReceipt:
    receipt, _ = await OSSBridgeService(db_session).register_receipt(
        workspace_id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.chatwoot,
            external_event_id=delivery_id,
            payload=payload,
            status=IntegrationEventReceiptStatus.processing,
        ),
    )
    receipt.updated_at = lease_started_at
    await db_session.commit()
    return receipt


@pytest.mark.asyncio
async def test_chatwoot_active_account_binding_is_globally_unique(
    db_session,
    auth_context,
):
    other_workspace = Workspace(
        name="Workspace duplicado",
        slug=f"chatwoot-duplicate-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    await _bind_chatwoot_account(db_session, auth_context.workspace_id, "account-unique")

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await _bind_chatwoot_account(db_session, other_workspace.id, "account-unique")


@pytest.mark.asyncio
async def test_chatwoot_webhook_fails_closed_when_secret_is_missing(
    unauthenticated_client,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", "")
    response = await unauthenticated_client.post(
        f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}",
        content=_request_body(_payload()),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_signed_chatwoot_message_creates_case_thread_rag_and_links(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(settings, "KLM_CHATWOOT_ACCOUNT_ID", "1")
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    body = _request_body(_payload())
    response = await unauthenticated_client.post(
        f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}",
        content=body,
        headers=_signed_headers(body, secret, "delivery-valid-1"),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "processed"
    assert payload["deduplicated"] is False
    assert payload["threadId"]
    assert payload["messageId"]
    assert payload["knowledgeChunkId"]

    client_row = await db_session.scalar(select(Client))
    matter = await db_session.scalar(select(Matter))
    thread = await db_session.scalar(select(OmnichannelThread))
    message = await db_session.scalar(select(OmnichannelMessage))
    chunk = await db_session.scalar(select(KnowledgeChunk))
    assert client_row.email == "ana.chatwoot@example.com"
    assert matter.client_id == client_row.id
    assert thread.matter_id == matter.id
    assert thread.primary_channel.value == "whatsapp"
    assert message.thread_id == thread.id
    assert message.delivery_status.value == "received"
    assert message.message_metadata["attachments"][0]["externalProvider"] == "chatwoot"
    assert message.message_metadata["attachments"][0]["externalAttachmentId"] == "31"
    assert "id" not in message.message_metadata["attachments"][0]
    assert message.message_metadata["attachments"][0]["localUrl"].endswith(
        "/rails/active_storage/blob/31"
    )
    assert "token=" not in message.message_metadata["attachments"][0]["localUrl"]
    assert chunk.matter_id == matter.id
    assert chunk.source_type == "conversation_message"
    assert chunk.chunk_metadata["attachmentRefs"][0]["name"] == "negativa.pdf"
    assert await db_session.scalar(select(func.count(ExternalEntityLink.id))) == 3
    receipt = await db_session.scalar(select(IntegrationEventReceipt))
    assert receipt.status.value == "processed"
    assert get_workspace_context(db_session) == auth_context.workspace_id


@pytest.mark.asyncio
async def test_chatwoot_delivery_is_idempotent_and_conflicting_replay_is_rejected(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    body = _request_body(_payload())
    headers = _signed_headers(body, secret, "delivery-idempotent")
    url = f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}"

    first = await unauthenticated_client.post(url, content=body, headers=headers)
    replay = await unauthenticated_client.post(
        url, content=body, headers=_signed_headers(body, secret, "delivery-idempotent")
    )
    assert first.status_code == replay.status_code == 200
    assert replay.json()["status"] == "processed"
    assert replay.json()["deduplicated"] is True
    assert await db_session.scalar(select(func.count(OmnichannelMessage.id))) == 1
    assert await db_session.scalar(select(func.count(KnowledgeChunk.id))) == 1

    changed = _payload()
    changed["content"] = "Payload alterado"
    changed_body = _request_body(changed)
    conflict = await unauthenticated_client.post(
        url,
        content=changed_body,
        headers=_signed_headers(changed_body, secret, "delivery-idempotent"),
    )
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_chatwoot_processing_receipt_deduplicates_while_lease_is_valid(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    payload = _payload(message_id=9020)
    delivery_id = "delivery-processing-active"
    receipt = await _register_processing_receipt(
        db_session,
        auth_context.workspace_id,
        delivery_id,
        payload,
        lease_started_at=datetime.now(UTC),
    )
    body = _request_body(payload)

    response = await unauthenticated_client.post(
        f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}",
        content=body,
        headers=_signed_headers(body, secret, delivery_id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "processing"
    assert response.json()["deduplicated"] is True
    assert await db_session.scalar(select(func.count(OmnichannelMessage.id))) == 0
    await db_session.refresh(receipt)
    assert receipt.status == IntegrationEventReceiptStatus.processing


@pytest.mark.asyncio
async def test_chatwoot_expired_processing_receipt_is_reclaimed_and_processed(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    payload = _payload(message_id=9021)
    delivery_id = "delivery-processing-expired"
    receipt = await _register_processing_receipt(
        db_session,
        auth_context.workspace_id,
        delivery_id,
        payload,
        lease_started_at=(
            datetime.now(UTC) - CHATWOOT_RECEIPT_LEASE_TIMEOUT - timedelta(seconds=1)
        ),
    )
    body = _request_body(payload)

    response = await unauthenticated_client.post(
        f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}",
        content=body,
        headers=_signed_headers(body, secret, delivery_id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "processed"
    assert response.json()["deduplicated"] is True
    assert await db_session.scalar(select(func.count(OmnichannelMessage.id))) == 1
    await db_session.refresh(receipt)
    assert receipt.status == IntegrationEventReceiptStatus.processed


@pytest.mark.asyncio
async def test_chatwoot_failed_receipt_can_be_retried_with_the_same_delivery(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    payload = _payload(message_id=9022)
    delivery_id = "delivery-failed-retry"
    body = _request_body(payload)
    url = f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}"
    original_ingest_message = ChatwootBridgeService._ingest_message

    async def fail_ingest(*_args, **_kwargs):
        raise ValueError("Falha transitória no bridge Chatwoot.")

    monkeypatch.setattr(ChatwootBridgeService, "_ingest_message", fail_ingest)
    failed = await unauthenticated_client.post(
        url,
        content=body,
        headers=_signed_headers(body, secret, delivery_id),
    )

    assert failed.status_code == 422
    receipt = await db_session.scalar(select(IntegrationEventReceipt))
    assert receipt.status == IntegrationEventReceiptStatus.failed

    monkeypatch.setattr(ChatwootBridgeService, "_ingest_message", original_ingest_message)
    retried = await unauthenticated_client.post(
        url,
        content=body,
        headers=_signed_headers(body, secret, delivery_id),
    )

    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "processed"
    assert retried.json()["deduplicated"] is True
    assert await db_session.scalar(select(func.count(OmnichannelMessage.id))) == 1
    await db_session.refresh(receipt)
    assert receipt.status == IntegrationEventReceiptStatus.processed


@pytest.mark.asyncio
async def test_chatwoot_rejects_bad_or_expired_signature(
    unauthenticated_client,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    body = _request_body(_payload())
    url = f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}"
    bad_headers = _signed_headers(body, "wrong-secret", "delivery-bad")
    expired_headers = _signed_headers(body, secret, "delivery-expired", int(time.time()) - 3600)

    bad = await unauthenticated_client.post(url, content=body, headers=bad_headers)
    expired = await unauthenticated_client.post(url, content=body, headers=expired_headers)
    assert bad.status_code == 403
    assert expired.status_code == 403


@pytest.mark.asyncio
async def test_outgoing_chatwoot_receipt_does_not_create_a_second_delivery_intent(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    body = _request_body(_payload(message_id=9010, message_type="outgoing"))
    response = await unauthenticated_client.post(
        f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}",
        content=body,
        headers=_signed_headers(body, secret, f"delivery-{uuid4()}"),
    )
    assert response.status_code == 200, response.text
    message = await db_session.scalar(select(OmnichannelMessage))
    assert message.direction.value == "outbound"
    assert message.author_type.value == "human"
    assert message.author_user_id is None
    assert message.delivery_status.value == "sent"
    assert await db_session.scalar(select(func.count(ExternalActionIntent.id))) == 0
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.entity_id == message.id,
            KlmAuditEvent.event_type == "omnichannel_message_recorded",
        )
    )
    assert audit.actor_type == "system"
    assert audit.actor_label == "Chatwoot bridge"
    assert audit.actor_user_id is None


@pytest.mark.asyncio
async def test_chatwoot_message_updated_reconciles_status_content_and_rag_without_duplicate(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    url = f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}"
    created_payload = _payload(message_id=9110, message_type="outgoing")
    created_body = _request_body(created_payload)
    created = await unauthenticated_client.post(
        url,
        content=created_body,
        headers=_signed_headers(created_body, secret, "delivery-message-created-9110"),
    )
    assert created.status_code == 200, created.text

    updated_payload = {
        **created_payload,
        "event": "message_updated",
        "content": "Conteúdo corrigido no Chatwoot local.",
        "status": "read",
    }
    updated_body = _request_body(updated_payload)
    updated = await unauthenticated_client.post(
        url,
        content=updated_body,
        headers=_signed_headers(updated_body, secret, "delivery-message-updated-9110"),
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["event"] == "message_updated"
    assert await db_session.scalar(select(func.count(OmnichannelMessage.id))) == 1
    message = await db_session.scalar(select(OmnichannelMessage))
    assert message.body == "Conteúdo corrigido no Chatwoot local."
    assert message.delivery_status == MessageDeliveryStatus.read
    assert message.message_metadata["chatwootLastEvent"] == "message_updated"
    chunk = await db_session.scalar(select(KnowledgeChunk))
    assert "Conteúdo corrigido" in chunk.content_text
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(KlmAuditEvent.event_type == "chatwoot.message_updated")
    )
    assert audit.after_data["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_chatwoot_conversation_and_contact_events_are_audited_without_overwriting_klm(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    url = f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}"
    created_payload = _payload(message_id=9120)
    created_body = _request_body(created_payload)
    created = await unauthenticated_client.post(
        url,
        content=created_body,
        headers=_signed_headers(created_body, secret, "delivery-bootstrap-9120"),
    )
    assert created.status_code == 200, created.text
    client_before = await db_session.scalar(select(Client))
    canonical_name = client_before.name

    for index, event in enumerate(
        ("conversation_created", "conversation_updated", "conversation_status_changed")
    ):
        payload = {
            "event": event,
            "id": 77,
            "account_id": 1,
            "account": {"id": 1},
            "status": "resolved" if event == "conversation_status_changed" else "open",
            "priority": "urgent",
            "labels": ["urgente"],
            "assignee": {"id": 7, "name": "Advogada"},
            "team": {"id": 8, "name": "Saúde"},
            "changed_attributes": {"status": ["open", "resolved"]},
        }
        body = _request_body(payload)
        response = await unauthenticated_client.post(
            url,
            content=body,
            headers=_signed_headers(body, secret, f"delivery-conversation-{index}"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["linkState"] == "linked"

    for index, event in enumerate(("contact_created", "contact_updated")):
        payload = {
            "event": event,
            "id": 55,
            "account_id": 1,
            "account": {"id": 1},
            "name": "Nome divergente no sidecar",
            "changed_attributes": {"name": [canonical_name, "Nome divergente no sidecar"]},
        }
        body = _request_body(payload)
        response = await unauthenticated_client.post(
            url,
            content=body,
            headers=_signed_headers(body, secret, f"delivery-contact-{index}"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["linkState"] == "linked"

    thread = await db_session.scalar(select(OmnichannelThread))
    await db_session.refresh(thread)
    assert thread.status.value == "open"
    assert thread.thread_metadata["chatwootObserved"]["status"] == "resolved"
    assert thread.thread_metadata["chatwootObserved"]["assigneeName"] == "Advogada"
    await db_session.refresh(client_before)
    assert client_before.name == canonical_name
    contact_link = await db_session.scalar(
        select(ExternalEntityLink).where(ExternalEntityLink.external_entity_type == "contact")
    )
    assert contact_link.link_metadata["lastObserved"]["canonicalOwner"] == "klm"
    assert (
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.event_type.in_(
                    {
                        "chatwoot.conversation_created",
                        "chatwoot.conversation_updated",
                        "chatwoot.conversation_status_changed",
                        "chatwoot.contact_created",
                        "chatwoot.contact_updated",
                    }
                )
            )
        )
        == 5
    )


@pytest.mark.asyncio
async def test_chatwoot_unknown_event_is_explicitly_ignored(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    payload = {"event": "inbox_updated", "id": 2, "account": {"id": 1}}
    body = _request_body(payload)
    response = await unauthenticated_client.post(
        f"/api/v1/oss-adapters/chatwoot/webhooks/{auth_context.workspace_id}",
        content=body,
        headers=_signed_headers(body, secret, "delivery-ignored-inbox"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ignored"
    receipt = await db_session.scalar(select(IntegrationEventReceipt))
    assert receipt.status == IntegrationEventReceiptStatus.ignored


@pytest.mark.asyncio
async def test_chatwoot_account_bound_to_another_workspace_is_rejected(
    unauthenticated_client,
    db_session,
    auth_context,
    monkeypatch,
):
    other_workspace = Workspace(
        name="Workspace alvo arbitrário",
        slug=f"chatwoot-cross-tenant-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    await _bind_chatwoot_account(db_session, auth_context.workspace_id)
    db_session.add(
        ExternalEntityLink(
            workspace_id=other_workspace.id,
            provider=OSSBridgeProvider.chatwoot,
            klm_entity_type="omnichannel_thread",
            klm_entity_id=uuid4(),
            external_account_id="1",
            external_entity_type="conversation",
            external_entity_id="existing-conversation",
            link_metadata={},
            sync_state=ExternalEntitySyncState.synced,
        )
    )
    await db_session.flush()

    secret = "chatwoot-local-test-secret"
    monkeypatch.setattr(settings, "KLM_CHATWOOT_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(settings, "KLM_CHATWOOT_ACCOUNT_ID", "1")
    body = _request_body(_payload())
    response = await unauthenticated_client.post(
        f"/api/v1/oss-adapters/chatwoot/webhooks/{other_workspace.id}",
        content=body,
        headers=_signed_headers(body, secret, "delivery-cross-tenant"),
    )

    assert response.status_code == 403
    assert "não vinculada" in response.json()["detail"]
    assert await db_session.scalar(select(func.count(IntegrationEventReceipt.id))) == 0
    assert await db_session.scalar(select(func.count(Client.id))) == 0
    assert get_workspace_context(db_session) == other_workspace.id
