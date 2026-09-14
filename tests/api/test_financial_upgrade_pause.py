import httpx
import pytest
from sqlalchemy import select

from app.api.v1.endpoints import integrations as integrations_endpoint
from app.models.finance import IntegrationConnection, IntegrationProvider


@pytest.mark.asyncio
async def test_asaas_onboarding_stays_paused_before_network_when_external_gate_is_open(
    client,
    db_session,
    monkeypatch,
    active_subscription,
):
    def forbidden_http_client(*_args, **_kwargs):
        pytest.fail("Asaas HTTP client must not be created while the upgrade is paused")

    existing_ids = {
        connection.id
        for connection in (
            await db_session.scalars(
                select(IntegrationConnection).where(
                    IntegrationConnection.provider == IntegrationProvider.asaas,
                )
            )
        ).all()
    }
    monkeypatch.setattr(integrations_endpoint, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden_http_client)

    response = await client.post(
        "/api/v1/integrations/connect/asaas",
        json={"api_key": "not-a-real-credential", "environment": "sandbox"},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail == {
        "code": "FINANCIAL_UPGRADE_PAUSED",
        "status": "PAUSADO",
        "message": (
            "Upgrade financeiro avançado Em breve. O financeiro local permanece "
            "disponível."
        ),
        "unlock_trigger": "destravar upgrade de funções financeiras",
        "external_calls_executed": 0,
    }
    persisted_ids = {
        connection.id
        for connection in (
            await db_session.scalars(
                select(IntegrationConnection).where(
                    IntegrationConnection.provider == IntegrationProvider.asaas,
                )
            )
        ).all()
    }
    assert persisted_ids == existing_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["asaas", "conta_azul", "pluga", "zapier"])
async def test_financial_provider_webhooks_stay_paused(
    provider,
    client,
    monkeypatch,
    active_subscription,
):
    monkeypatch.setattr(integrations_endpoint, "external_delivery_allowed", lambda: True)

    response = await client.post(
        f"/api/v1/integrations/webhooks/local-workspace/{provider}",
        json={"event": "payment.received", "id": "evt-local-test"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "FINANCIAL_UPGRADE_PAUSED"
    assert response.json()["detail"]["external_calls_executed"] == 0


def test_financial_upgrade_uses_the_exact_unlock_trigger():
    assert integrations_endpoint.FINANCIAL_UPGRADE_STATUS == "PAUSADO"
    assert (
        integrations_endpoint.FINANCIAL_UPGRADE_UNLOCK_TRIGGER
        == "destravar upgrade de funções financeiras"
    )
