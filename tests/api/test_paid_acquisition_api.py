from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.klm_operations import ExternalActionIntentStatus
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole


pytestmark = pytest.mark.usefixtures("active_subscription")


def _connection(workspace_id, *, channel="whatsapp", status="connected"):
    return OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel=channel,
        provider="meta_ads" if channel != "whatsapp" else "whatsapp_cloud_api",
        connection_key=f"{channel}-{uuid4().hex[:8]}",
        display_name=f"Conexão {channel}",
        external_account_id=f"acct-{channel}",
        status=status,
        is_default=True,
    )


def _draft_body(**overrides):
    body = {
        "idempotencyKey": f"draft-{uuid4().hex[:8]}",
        "channel": "ctwa",
        "campaignName": "Campanha via API",
        "dailyBudget": "120.50",
        "currency": "BRL",
        "creative": {
            "headline": "Fale agora com a MV",
            "primaryText": "Atendimento imediato via WhatsApp.",
        },
        "welcomeMessage": "Olá! Como posso ajudar?",
        "iceBreakers": ["Quero um orçamento"],
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_sync_status_endpoint_is_honest_when_not_synced(client, auth_context):
    response = await client.get("/api/v1/paid-acquisition/sync-status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["state"] == "not_synced"
    assert payload["gateOpen"] is False


@pytest.mark.asyncio
async def test_prepare_campaign_draft_rejects_missing_channel_prereq(client, auth_context):
    response = await client.post(
        "/api/v1/paid-acquisition/campaigns", json=_draft_body(channel="ctwa")
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PAID-ACQ-422"


@pytest.mark.asyncio
async def test_prepare_campaign_draft_creates_and_lists(client, db_session, auth_context):
    db_session.add(_connection(auth_context.workspace_id, channel="whatsapp"))
    await db_session.commit()

    create_response = await client.post(
        "/api/v1/paid-acquisition/campaigns", json=_draft_body(channel="ctwa")
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["status"] == ExternalActionIntentStatus.awaiting_approval.value
    assert created["marketingApiPayload"]["executionState"] == "intent_only"

    list_response = await client.get("/api/v1/paid-acquisition/campaigns")
    assert list_response.status_code == 200
    drafts = list_response.json()
    assert any(item["id"] == created["id"] for item in drafts)

    get_response = await client.get(f"/api/v1/paid-acquisition/campaigns/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]

    missing_response = await client.get(f"/api/v1/paid-acquisition/campaigns/{uuid4()}")
    assert missing_response.status_code == 404


@pytest.mark.asyncio
async def test_funnel_summary_endpoint_returns_empty_state_gracefully(client, auth_context):
    response = await client.get("/api/v1/paid-acquisition/funnel-summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"] == []
    assert payload["totals"]["spend"] == 0.0


@pytest.mark.asyncio
async def test_rbac_denies_member_without_integrations_permission(
    client, db_session, auth_context
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.viewer
    await db_session.commit()

    response = await client.get("/api/v1/paid-acquisition/sync-status")
    assert response.status_code == 403
