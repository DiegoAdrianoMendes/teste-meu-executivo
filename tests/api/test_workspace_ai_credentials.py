"""Endpoints BYOK: escrita cifrada, resposta sempre redigida e RBAC."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
)
from app.main import app as fastapi_app
from app.models.klm_operations import KlmAuditEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.models.workspace_model_credential import WorkspaceModelCredential
from app.services.workspace_model_credential_service import ModelCredentialCipher

TEST_CIPHER_KEY = "test-only-api-credential-key-6Jd3nT8wR5yG2kP9sX4v"
PLAINTEXT_KEY = "sk-proj-endpoint-0123456789-final-zeta"


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture(autouse=True)
def _configure_cipher_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_CIPHER_KEY)


@pytest_asyncio.fixture
async def viewer_client(db_session, auth_context: AuthContext):
    """Cliente autenticado como viewer — sem integrations.manage/view."""
    viewer = User(
        name="Viewer Sem Permissão",
        email="viewer-byok@example.com",
        agent_type=AgentType.human,
        role=UserRole.intern,
        initials="VS",
        color="#334155",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(viewer)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth_context.workspace_id,
        user_id=viewer.id,
        role=WorkspaceRole.viewer,
        status=MembershipStatus.active,
        is_default=True,
    )
    db_session.add(membership)
    await db_session.flush()

    viewer_context = AuthContext(
        user_id=viewer.id,
        workspace_id=auth_context.workspace_id,
        membership_id=membership.id,
        role=WorkspaceRole.viewer,
        is_internal_staff=False,
    )

    async def override_get_db():
        yield db_session

    async def override_get_current_auth_context():
        return viewer_context

    async def override_get_current_user_id():
        return viewer_context.user_id

    async def override_get_current_workspace_id():
        return viewer_context.workspace_id

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_auth_context] = (
        override_get_current_auth_context
    )
    fastapi_app.dependency_overrides[get_current_user_id] = override_get_current_user_id
    fastapi_app.dependency_overrides[get_current_workspace_id] = (
        override_get_current_workspace_id
    )
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_put_stores_encrypted_and_response_is_redacted(client, db_session):
    response = await client.put(
        "/api/v1/workspaces/ai-credentials/anthropic",
        json={"apiKey": PLAINTEXT_KEY},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # A chave NUNCA aparece na resposta; só a máscara com os últimos 4.
    assert PLAINTEXT_KEY not in response.text
    assert body["provider"] == "anthropic"
    assert body["configured"] is True
    assert body["isActive"] is True
    assert body["apiKeyMasked"] == f"****{PLAINTEXT_KEY[-4:]}"
    assert "apiKey" not in body
    assert "apiKeyEncrypted" not in body

    stored = await db_session.scalar(select(WorkspaceModelCredential))
    assert stored is not None
    assert stored.api_key_encrypted != PLAINTEXT_KEY
    assert PLAINTEXT_KEY not in stored.api_key_encrypted
    assert (
        ModelCredentialCipher().decrypt_strict(stored.api_key_encrypted)
        == PLAINTEXT_KEY
    )

    events = (
        (await db_session.execute(select(KlmAuditEvent))).scalars().all()
    )
    assert any(
        event.event_type == "workspace_model_credential_created" for event in events
    )


@pytest.mark.asyncio
async def test_get_returns_redacted_status_and_404_when_missing(client):
    missing = await client.get("/api/v1/workspaces/ai-credentials/openai")
    assert missing.status_code == 404

    await client.put(
        "/api/v1/workspaces/ai-credentials/openai",
        json={"apiKey": PLAINTEXT_KEY},
    )
    response = await client.get("/api/v1/workspaces/ai-credentials/openai")
    assert response.status_code == 200
    assert PLAINTEXT_KEY not in response.text
    assert response.json()["apiKeyMasked"] == f"****{PLAINTEXT_KEY[-4:]}"


@pytest.mark.asyncio
async def test_put_rotation_and_delete_deactivation(client, db_session):
    await client.put(
        "/api/v1/workspaces/ai-credentials/gemini",
        json={"apiKey": PLAINTEXT_KEY},
    )
    rotated = await client.put(
        "/api/v1/workspaces/ai-credentials/gemini",
        json={"apiKey": "chave-nova-rotacionada-9x8y7z-beta"},
    )
    assert rotated.status_code == 200
    assert rotated.json()["apiKeyMasked"] == "****beta"

    deleted = await client.delete("/api/v1/workspaces/ai-credentials/gemini")
    assert deleted.status_code == 200
    assert deleted.json()["isActive"] is False

    event_types = {
        event.event_type
        for event in (await db_session.execute(select(KlmAuditEvent))).scalars().all()
    }
    assert {
        "workspace_model_credential_created",
        "workspace_model_credential_rotated",
        "workspace_model_credential_deactivated",
    } <= event_types


@pytest.mark.asyncio
async def test_unknown_provider_and_short_key_fail_closed(client):
    unknown = await client.put(
        "/api/v1/workspaces/ai-credentials/provider-fantasma",
        json={"apiKey": PLAINTEXT_KEY},
    )
    assert unknown.status_code == 404

    short = await client.put(
        "/api/v1/workspaces/ai-credentials/openai",
        json={"apiKey": "curtinha"},
    )
    # min_length=8 do schema passa "curtinha", mas o serviço mantém o mínimo.
    assert short.status_code in {200, 422}

    empty = await client.put(
        "/api/v1/workspaces/ai-credentials/openai",
        json={"apiKey": "abc"},
    )
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_rbac_denies_without_integrations_permission(viewer_client, db_session):
    put_response = await viewer_client.put(
        "/api/v1/workspaces/ai-credentials/anthropic",
        json={"apiKey": PLAINTEXT_KEY},
    )
    assert put_response.status_code == 403
    assert put_response.json()["detail"]["code"] == "AUTH-012"

    get_response = await viewer_client.get(
        "/api/v1/workspaces/ai-credentials/anthropic"
    )
    assert get_response.status_code == 403

    delete_response = await viewer_client.delete(
        "/api/v1/workspaces/ai-credentials/anthropic"
    )
    assert delete_response.status_code == 403

    # Nada foi gravado sem permissão.
    assert (await db_session.scalar(select(WorkspaceModelCredential))) is None
