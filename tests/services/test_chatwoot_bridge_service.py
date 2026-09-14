"""Receipt lease behavior for the Chatwoot bridge service."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.oss_bridge import IntegrationEventReceiptStatus, OSSBridgeProvider
from app.schemas.oss_bridge import IntegrationEventReceiptCreate
from app.services.chatwoot_bridge_service import (
    CHATWOOT_RECEIPT_LEASE_TIMEOUT,
    ChatwootBridgeService,
    _safe_attachment_metadata,
)
from app.services.oss_bridge_service import OSSBridgeService


def test_attachment_metadata_drops_credentials_query_fragment_and_unsafe_urls() -> None:
    metadata = _safe_attachment_metadata(
        {
            "attachments": [
                {
                    "id": 31,
                    "data_url": (
                        "http://user:pass@localhost:3100/path/to/file.pdf"
                        "?token=secret#download"
                    ),
                },
                {"id": 32, "data_url": "http://localhost:22/private"},
                {"id": 33, "data_url": "https://files.example.com/document.pdf"},
                {"id": 34, "data_url": "file:///etc/passwd"},
            ]
        }
    )

    assert metadata[0]["localUrl"] == "http://localhost:3100/path/to/file.pdf"
    assert metadata[0]["externalProvider"] == "chatwoot"
    assert metadata[0]["externalAttachmentId"] == "31"
    assert all(item["localUrl"] is None for item in metadata[1:])
    assert "user:pass" not in str(metadata)
    assert "token=secret" not in str(metadata)


@pytest.mark.asyncio
async def test_receipt_claim_is_atomic_and_only_current_lease_can_finish(
    db_session,
    auth_context,
):
    receipt, _ = await OSSBridgeService(db_session).register_receipt(
        auth_context.workspace_id,
        IntegrationEventReceiptCreate(
            provider=OSSBridgeProvider.chatwoot,
            external_event_id="service-lease-fencing",
            payload={"event": "message_created", "id": 9100},
        ),
    )
    await db_session.commit()
    receipt_id = receipt.id
    service = ChatwootBridgeService(db_session)

    first_lease = await service._claim_receipt(auth_context.workspace_id, receipt_id)
    assert first_lease is not None
    assert await service._claim_receipt(auth_context.workspace_id, receipt_id) is None

    receipt.updated_at = (
        datetime.now(UTC)
        - CHATWOOT_RECEIPT_LEASE_TIMEOUT
        - timedelta(seconds=1)
    )
    await db_session.commit()
    current_lease = await service._claim_receipt(auth_context.workspace_id, receipt_id)
    assert current_lease is not None
    assert current_lease != first_lease

    assert not await service._finish_receipt(
        auth_context.workspace_id,
        receipt_id,
        first_lease,
        IntegrationEventReceiptStatus.processed,
    )
    await db_session.rollback()

    assert await service._finish_receipt(
        auth_context.workspace_id,
        receipt_id,
        current_lease,
        IntegrationEventReceiptStatus.processed,
    )
    await db_session.commit()
    await db_session.refresh(receipt)
    assert receipt.status == IntegrationEventReceiptStatus.processed
    assert receipt.processed_at is not None
