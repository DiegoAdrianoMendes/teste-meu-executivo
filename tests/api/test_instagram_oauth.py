from __future__ import annotations

import pytest

import app.services.meta_oauth_service as meta_oauth_service_module
from app.models.meta_channels import MetaChannelConnection, MetaChannelType, MetaConnectionStatus
from app.services.meta_oauth_service import sign_oauth_state


async def _connection(db_session, workspace_id) -> MetaChannelConnection:
    connection = MetaChannelConnection(
        workspace_id=workspace_id,
        channel_type=MetaChannelType.instagram,
        account_name="Perfil Oficial",
        status=MetaConnectionStatus.pending,
        scopes=[],
        capability_matrix={},
        webhook_registered=False,
    )
    db_session.add(connection)
    await db_session.commit()
    await db_session.refresh(connection)
    return connection


@pytest.mark.asyncio
async def test_authorize_url_requires_integrations_manage_permission(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    connection = await _connection(db_session, auth_context.workspace_id)
    response = await client.get(
        "/api/v1/instagram/oauth/authorize-url",
        params={
            "connection_id": str(connection.id),
            "redirect_uri": "https://app.example/ig/callback",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"]
    assert "instagram_business_basic" in body["authorizationUrl"]
    assert "instagram_business_manage_messages" in body["authorizationUrl"]
    assert "instagram_business_manage_comments" in body["authorizationUrl"]


@pytest.mark.asyncio
async def test_authorize_url_unknown_connection_returns_404(
    client,
    auth_context,
    active_subscription,
):
    import uuid

    response = await client.get(
        "/api/v1/instagram/oauth/authorize-url",
        params={
            "connection_id": str(uuid.uuid4()),
            "redirect_uri": "https://app.example/ig/callback",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_callback_gate_closed_returns_oauth_prepared_and_no_token_leak(
    client,
    db_session,
    auth_context,
    monkeypatch,
    active_subscription,
):
    monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
    connection = await _connection(db_session, auth_context.workspace_id)
    state, _ = sign_oauth_state(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        redirect_uri="https://app.example/ig/callback",
    )
    response = await client.get(
        "/api/v1/instagram/oauth/callback",
        params={"code": "raw-code-should-not-leak", "state": state},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "oauth_prepared"
    assert "raw-code-should-not-leak" not in response.text


@pytest.mark.asyncio
async def test_callback_rejects_forged_state(
    client,
    auth_context,
    active_subscription,
):
    response = await client.get(
        "/api/v1/instagram/oauth/callback",
        params={"code": "any-code", "state": "forged.state"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_token_status_endpoint_returns_state_without_token(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    connection = await _connection(db_session, auth_context.workspace_id)
    response = await client.get(f"/api/v1/instagram/oauth/token-status/{connection.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "not_connected"
