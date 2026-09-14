import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.finance import IntegrationConnection
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.integration_secrets import (
    IntegrationSecretCipher,
    protect_integration_settings,
    reveal_integration_secret,
)

TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        TEST_INTEGRATION_SECRET_KEY,
    )


def test_integration_cipher_fails_closed_instead_of_falling_back_to_jwt_secret(
    monkeypatch,
):
    monkeypatch.setattr(settings, "SECRET_KEY", "public-jwt-secret-with-more-than-32-bytes")
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", "")

    with pytest.raises(RuntimeError, match="INTEGRATION_SECRET_ENCRYPTION_KEY"):
        IntegrationSecretCipher()


def test_integration_cipher_rejects_reusing_jwt_secret(monkeypatch):
    shared_secret = "test-only-shared-secret-with-more-than-32-bytes"
    monkeypatch.setattr(settings, "SECRET_KEY", shared_secret)
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", shared_secret)

    with pytest.raises(RuntimeError, match="não dedicada"):
        IntegrationSecretCipher()


def test_integration_cipher_uses_dedicated_key(monkeypatch):
    encrypted = protect_integration_settings({"apiKey": "secret-value"})

    assert reveal_integration_secret(encrypted, "apiKey") == "secret-value"
    monkeypatch.setattr(
        settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        "different-test-integration-key-4Tb8mP1xR7vC9zN2qL6w",
    )
    assert reveal_integration_secret(encrypted, "apiKey") == ""


@pytest.mark.asyncio
async def test_finance_integration_encrypts_and_redacts_inline_credentials(
    client,
    db_session,
    active_subscription,
):
    response = await client.post(
        "/api/v1/finance/integrations",
        json={
            "provider": "custom",
            "label": "Integração local",
            "status": "pending",
            "settings": {"apiKey": "secret-value", "environment": "sandbox"},
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["settings"] == {
        "apiKeyConfigured": True,
        "environment": "sandbox",
    }
    assert "secret-value" not in response.text
    connection = await db_session.scalar(select(IntegrationConnection))
    assert connection is not None
    assert "apiKey" not in connection.settings
    assert "secret-value" not in connection.settings["apiKeyEncrypted"]


@pytest.mark.asyncio
async def test_native_integration_onboarding_is_blocked_before_network(
    client,
    monkeypatch,
    active_subscription,
):
    call = pytest.fail
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(
        "app.services.zapsign_service.ZapSignService.get_status",
        lambda *_args, **_kwargs: call("ZapSign não deve ser chamado"),
    )

    response = await client.post(
        "/api/v1/integrations/connect/zapsign",
        json={"api_token": "secret-value"},
    )

    assert response.status_code == 409
    assert "bloqueado" in response.text


@pytest.mark.asyncio
async def test_zapsign_onboarding_generates_tenant_webhook_secret(
    client,
    db_session,
    monkeypatch,
    active_subscription,
):
    async def connected(*_args, **_kwargs):
        return {
            "connected": True,
            "api_token_configured": True,
            "webhook_registered": False,
            "external_actions_enabled": True,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.integrations.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.zapsign_service.ZapSignService.get_status",
        connected,
    )

    response = await client.post(
        "/api/v1/integrations/connect/zapsign",
        json={"api_token": "tenant-api-token"},
    )

    assert response.status_code == 200, response.text
    connection = await db_session.scalar(select(IntegrationConnection))
    assert connection is not None
    assert "apiToken" not in connection.settings
    assert "webhookSecret" not in connection.settings
    assert reveal_integration_secret(connection.settings, "apiToken") == "tenant-api-token"
    generated_secret = reveal_integration_secret(
        connection.settings,
        "webhookSecret",
    )
    assert len(generated_secret) >= 32


@pytest.mark.asyncio
async def test_operator_cannot_manage_native_integrations(
    client,
    db_session,
    auth_context,
    active_subscription,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    response = await client.delete("/api/v1/integrations/connect/zapsign")

    assert response.status_code == 403
