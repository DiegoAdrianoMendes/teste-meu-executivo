from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import AuthContext
from app.models.conversion_feedback import AdsConversionFeedbackRecord
from app.models.finance import IntegrationConnection
from app.models.klm_operations import ExternalActionIntent, ExternalActionIntentStatus
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole

TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"
pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(
        settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_INTEGRATION_SECRET_KEY
    )


@pytest.mark.asyncio
async def test_measurement_config_round_trip_never_leaks_the_token(client, db_session):
    get_before = await client.get("/api/v1/klm/tracking/ads-measurement-config")
    assert get_before.status_code == 200, get_before.text
    assert get_before.json()["configured"] is False

    put_response = await client.put(
        "/api/v1/klm/tracking/ads-measurement-config",
        json={
            "datasetId": "dataset-abc",
            "pixelId": "pixel-abc",
            "accessToken": "super-secret-capi-token",
        },
    )
    assert put_response.status_code == 200, put_response.text
    body = put_response.json()
    assert body["configured"] is True
    assert body["datasetId"] == "dataset-abc"
    assert body["accessTokenConfigured"] is True
    assert body["accessTokenLast4"] == "oken"
    assert "super-secret-capi-token" not in put_response.text

    connection = await db_session.scalar(select(IntegrationConnection))
    assert connection is not None
    assert "accessToken" not in connection.settings
    assert "super-secret-capi-token" not in str(connection.settings)

    get_after = await client.get("/api/v1/klm/tracking/ads-measurement-config")
    assert get_after.status_code == 200
    payload = get_after.json()
    assert payload["configured"] is True
    assert payload["datasetId"] == "dataset-abc"
    assert "super-secret-capi-token" not in get_after.text


@pytest.mark.asyncio
async def test_measurement_config_requires_integrations_manage_permission(
    client, db_session, auth_context
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    response = await client.put(
        "/api/v1/klm/tracking/ads-measurement-config",
        json={"datasetId": "dataset-abc"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dispatch_endpoint_reports_blocked_when_gate_closed(
    client, db_session, auth_context: AuthContext, monkeypatch
):
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: False
    )
    intent = ExternalActionIntent(
        workspace_id=auth_context.workspace_id,
        requested_by_user_id=auth_context.user_id,
        action_type="ads_conversion_feedback",
        provider="meta_ads",
        idempotency_key=f"ads-conversion-feedback:meta_ads:{uuid4()}",
        status=ExternalActionIntentStatus.awaiting_approval,
        payload={
            "schemaVersion": "klm.ads.conversion-feedback.v2",
            "sourceEventId": str(uuid4()),
            "sourceTouchEventId": str(uuid4()),
            "providerEventId": "klm-abc",
            "conversionEvent": "contract_created",
            "conversionHappenedAt": datetime(2026, 7, 21, 12, 30, tzinfo=UTC).isoformat(),
            "readyForDispatch": True,
            "missingConfiguration": [],
            "payloadSha256": "b" * 64,
            "providerEnvelope": {
                "payloadSha256": "b" * 64,
                "providerPayload": {"data": [{"event_id": "klm-abc"}]},
            },
            "externalActionsExecuted": 0,
        },
        reason="Conversao preparada para teste.",
    )
    db_session.add(intent)
    await db_session.flush()
    await db_session.commit()

    response = await client.post("/api/v1/klm/tracking/conversion-feedback/dispatch")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preparedProcessed"] == 1
    assert body["blockedSimulated"] == 1
    assert body["dispatchedReal"] == 0
    assert body["externalActionsEnabled"] is False

    receipt = await db_session.scalar(
        select(AdsConversionFeedbackRecord).where(
            AdsConversionFeedbackRecord.intent_id == intent.id
        )
    )
    assert receipt is not None
    assert receipt.provider_receipt["simulated"] is True
