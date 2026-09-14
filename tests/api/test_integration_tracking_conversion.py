"""Endpoint de conexão de tracking de conversão paga via Google Data Manager
— fecha o dormente #2 da auditoria final de UI/UX.

O despacho real já existe e é provado por teste (Onda 10,
tests/workers/test_ads_feedback_tasks.py). Este arquivo cobre a peça que
faltava: os endpoints PUT/GET que criam/leem a IntegrationConnection que
`load_google_data_manager_config` já sabe resolver — mesmo provider+label,
sem tabela nova — e a garantia de que o segredo nunca volta em claro pela
API.

O dormente #1 (Meta CAPI) NÃO precisou de endpoint novo: já existe conexão
completa via IntegrationsPage.tsx (aba "Mensuração Meta (CAPI)") +
GET/PUT /api/v1/klm/tracking/ads-measurement-config
(app/api/v1/endpoints/klm_operations.py), cobertos por
tests/api/test_klm_operations_ads_measurement_config.py (se existir) e pela
suíte geral de klm_operations.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.klm_operations import ExternalActionIntent, ExternalActionIntentStatus
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.google_conversions_service import (
    GOOGLE_CONVERSIONS_LABEL,
    load_google_data_manager_config,
)
from app.services.integration_secrets import reveal_integration_secret
from app.workers import ads_feedback_tasks

TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(
        settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_INTEGRATION_SECRET_KEY
    )


async def _google_connection(db_session, workspace_id):
    return await db_session.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.google_ads,
            IntegrationConnection.label == GOOGLE_CONVERSIONS_LABEL,
        )
    )


@pytest.mark.asyncio
async def test_google_data_manager_status_is_unconfigured_before_any_put(client, auth_context):
    response = await client.get(
        "/api/v1/integrations/tracking/google-data-manager/status"
    )
    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "customerId": None,
        "hasDeveloperToken": False,
        "hasAccessToken": False,
    }


@pytest.mark.asyncio
async def test_google_data_manager_put_creates_connection_with_encrypted_secrets(
    client, db_session, auth_context
):
    response = await client.put(
        "/api/v1/integrations/tracking/google-data-manager",
        json={
            "customerId": "123-456-7890",
            "developerToken": "dev-token-secret",
            "accessToken": "access-token-secret",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "configured": True,
        "customerId": "123-456-7890",
        "hasDeveloperToken": True,
        "hasAccessToken": True,
    }
    assert "dev-token-secret" not in response.text
    assert "access-token-secret" not in response.text

    conn = await _google_connection(db_session, auth_context.workspace_id)
    assert conn is not None
    assert conn.settings["customerId"] == "123-456-7890"
    assert "developerTokenEncrypted" in conn.settings
    assert "accessTokenEncrypted" in conn.settings
    assert reveal_integration_secret(conn.settings, "developerToken") == "dev-token-secret"
    assert reveal_integration_secret(conn.settings, "accessToken") == "access-token-secret"


@pytest.mark.asyncio
async def test_google_data_manager_put_requires_secrets_on_first_setup(client, auth_context):
    response = await client.put(
        "/api/v1/integrations/tracking/google-data-manager",
        json={"customerId": "123-456-7890"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TRACKING-GOOGLE-DM-422"


@pytest.mark.asyncio
async def test_google_data_manager_put_omitting_secrets_preserves_saved_values(
    client, db_session, auth_context
):
    first = await client.put(
        "/api/v1/integrations/tracking/google-data-manager",
        json={
            "customerId": "123-456-7890",
            "developerToken": "dev-original",
            "accessToken": "access-original",
        },
    )
    assert first.status_code == 200

    second = await client.put(
        "/api/v1/integrations/tracking/google-data-manager",
        json={"customerId": "999-888-7777"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["customerId"] == "999-888-7777"
    assert second.json()["hasDeveloperToken"] is True
    assert second.json()["hasAccessToken"] is True

    conn = await _google_connection(db_session, auth_context.workspace_id)
    assert reveal_integration_secret(conn.settings, "developerToken") == "dev-original"
    assert reveal_integration_secret(conn.settings, "accessToken") == "access-original"

    # Still exactly one connection row (idempotent upsert, no duplicate).
    all_conns = (
        await db_session.scalars(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == auth_context.workspace_id,
                IntegrationConnection.provider == IntegrationProvider.google_ads,
                IntegrationConnection.label == GOOGLE_CONVERSIONS_LABEL,
            )
        )
    ).all()
    assert len(all_conns) == 1


@pytest.mark.asyncio
async def test_google_data_manager_load_config_resolves_what_ui_saved(
    client, db_session, auth_context
):
    response = await client.put(
        "/api/v1/integrations/tracking/google-data-manager",
        json={
            "customerId": "111-222-3333",
            "developerToken": "dev-wired",
            "accessToken": "access-wired",
        },
    )
    assert response.status_code == 200

    config = await load_google_data_manager_config(db_session, auth_context.workspace_id)
    assert config.configured is True
    assert config.customer_id == "111-222-3333"
    assert config.developer_token == "dev-wired"
    assert config.access_token == "access-wired"


@pytest.mark.asyncio
async def test_google_data_manager_dispatch_stays_blocked_when_gate_closed_after_ui_config(
    client, db_session, auth_context, monkeypatch
):
    """Onda 10 não regride: configurar via UI não abre o despacho real."""
    response = await client.put(
        "/api/v1/integrations/tracking/google-data-manager",
        json={
            "customerId": "123-456-7890",
            "developerToken": "dev-token-secret",
            "accessToken": "access-token-secret",
        },
    )
    assert response.status_code == 200

    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: False
    )

    intent = ExternalActionIntent(
        workspace_id=auth_context.workspace_id,
        requested_by_user_id=auth_context.user_id,
        action_type="ads_conversion_feedback",
        provider="google_ads",
        idempotency_key=f"ads-conversion-feedback:google_ads:{uuid4()}",
        status=ExternalActionIntentStatus.approved_local,
        payload={
            "schemaVersion": "klm.ads.conversion-feedback.v2",
            "sourceEventId": str(uuid4()),
            "sourceTouchEventId": str(uuid4()),
            "providerEventId": "klm-abc",
            "conversionEvent": "contract_created",
            "conversionHappenedAt": datetime(2026, 7, 21, 12, 30, tzinfo=UTC).isoformat(),
            "value": 4800.0,
            "currency": "BRL",
            "readyForDispatch": True,
            "missingConfiguration": [],
            "payloadSha256": "b" * 64,
            "providerEnvelope": {
                "schemaVersion": "klm.ads.provider-envelope.v2",
                "payloadSha256": "b" * 64,
                "providerPayload": {"destination": {"customerId": "123-456-7890"}},
            },
            "externalActionsExecuted": 0,
        },
        reason="Conversao preparada para teste.",
    )
    db_session.add(intent)
    await db_session.flush()

    async def _fail_real_dispatch(**_kwargs):
        pytest.fail("Gate fechado nunca deve despachar de verdade.")

    monkeypatch.setattr(ads_feedback_tasks, "_dispatch_google_real", _fail_real_dispatch)

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["dispatchedReal"] == 0
    assert counts["blockedSimulated"] == 1


@pytest.mark.asyncio
async def test_google_data_manager_isolates_between_workspaces(client, db_session, auth_context):
    other_workspace = Workspace(
        name="Workspace Externo Google",
        slug=f"workspace-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    db_session.add(
        IntegrationConnection(
            workspace_id=other_workspace.id,
            provider=IntegrationProvider.google_ads,
            status=IntegrationStatus.connected,
            label=GOOGLE_CONVERSIONS_LABEL,
            settings={
                "customerId": "foreign-customer",
                "developerTokenEncrypted": "x",
                "accessTokenEncrypted": "y",
            },
        )
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/integrations/tracking/google-data-manager/status"
    )
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["customerId"] is None

    foreign_config = await load_google_data_manager_config(db_session, other_workspace.id)
    assert foreign_config.customer_id == "foreign-customer"
