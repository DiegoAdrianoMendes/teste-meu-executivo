from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.omnichannel_v6 import OmnichannelProviderWebhookProcessingState
from app.services.message_evidence_service import MessageEvidenceService


@pytest.mark.asyncio
async def test_blackbox_can_query_terminal_webhook_failures_without_signed_payload(
    platform_client,
    db_session,
):
    context = await MessageEvidenceService(db_session).capture_provider_envelope(
        provider="meta_whatsapp",
        raw_payload=b'{"sensitive_customer_body":"must-not-leak"}',
        signature_header="sha256=must-not-leak",
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
        request_metadata={"authorization": "must-not-leak"},
    )
    state = await db_session.get(
        OmnichannelProviderWebhookProcessingState,
        context.id,
    )
    assert state is not None
    failed_at = datetime.now(UTC)
    state.status = "terminal_failed"
    state.attempts = 9
    state.last_failed_at = failed_at
    state.terminal_failed_at = failed_at
    state.last_error_code = "ProviderTimeout"
    state.last_error_message = "access_token=must-not-leak"
    await db_session.commit()

    response = await platform_client.get(
        "/api/v1/status/webhooks/whatsapp",
        params={"status": "terminal_failed"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["filter"] == "terminal_failed"
    assert payload["counts"]["terminal_failed"] == 1
    assert payload["items"] == [
        {
            "envelopeId": str(context.id),
            "provider": "meta_whatsapp",
            "status": "terminal_failed",
            "attempts": 9,
            "queuedAt": payload["items"][0]["queuedAt"],
            "processingStartedAt": None,
            "lastAttemptedAt": None,
            "nextRetryAt": None,
            "processedAt": None,
            "lastFailedAt": payload["items"][0]["lastFailedAt"],
            "terminalFailedAt": payload["items"][0]["terminalFailedAt"],
            "leaseExpiresAt": None,
            "lastErrorCode": "ProviderTimeout",
        }
    ]
    assert "must-not-leak" not in response.text
    assert "sensitive_customer_body" not in response.text


@pytest.mark.asyncio
async def test_webhook_processing_status_is_closed_to_anonymous_visitors(
    unauthenticated_client,
):
    response = await unauthenticated_client.get(
        "/api/v1/status/webhooks/whatsapp"
    )

    assert response.status_code in (401, 403)
