"""Roteamento de modelos por classe de tarefa: leitura, escrita fail-closed e RBAC."""
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
from app.models.workspace_ai_model_route import WorkspaceAiModelRoute
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)

ROUTE_CATALOG_JSON = (
    '{"version":"catalog-test-1","classes":{'
    '"simple":[{"provider":"openai","model":"modelo-simples"}],'
    '"medium":[{"provider":"openai","model":"modelo-medio"}],'
    '"complex":[{"provider":"anthropic","model":"modelo-complexo"}],'
    '"premium":[{"provider":"anthropic","model":"modelo-premium"}]'
    '},"agents":{}}'
)
PRICE_CATALOG_JSON = (
    '{"version":"precos-test-1","currency":"USD","effective_at":null,"models":{'
    '"openai:modelo-simples":{"input_per_million_usd":"0.10","output_per_million_usd":"0.40"},'
    '"openai:modelo-medio":{"input_per_million_usd":"0.50","output_per_million_usd":"1.50"},'
    '"anthropic:modelo-complexo":{"input_per_million_usd":"3.00","output_per_million_usd":"15.00"},'
    '"anthropic:modelo-premium":{"input_per_million_usd":"5.00","output_per_million_usd":"25.00"}'
    '}}'
)
ALLOWLIST_JSON = (
    '["openai:modelo-simples","openai:modelo-medio",'
    '"anthropic:modelo-complexo","anthropic:modelo-premium"]'
)


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.fixture
def _catalog(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "api_gateway")
    monkeypatch.setattr(settings, "KLM_AI_ROUTE_CATALOG_JSON", ROUTE_CATALOG_JSON)
    monkeypatch.setattr(settings, "KLM_AI_PRICE_CATALOG_JSON", PRICE_CATALOG_JSON)
    monkeypatch.setattr(settings, "KLM_AI_MODEL_ALLOWLIST_JSON", ALLOWLIST_JSON)


@pytest_asyncio.fixture
async def viewer_client(db_session, auth_context: AuthContext):
    """Cliente autenticado como viewer — não é owner/admin do workspace."""
    viewer = User(
        name="Viewer Roteamento",
        email="viewer-ai-models@example.com",
        agent_type=AgentType.human,
        role=UserRole.intern,
        initials="VR",
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
async def test_get_returns_platform_defaults_and_catalog(client, _catalog):
    response = await client.get("/api/v1/workspaces/ai-models")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["catalogVersion"] == "catalog-test-1"
    assert [item["taskClass"] for item in body["classes"]] == [
        "simple",
        "medium",
        "complex",
        "premium",
    ]
    simple = body["classes"][0]
    assert simple["source"] == "platform"
    assert simple["provider"] == "openai"
    assert simple["model"] == "modelo-simples"
    assert simple["price"]["inputPerMillionUsd"] == 0.10
    assert simple["price"]["outputPerMillionUsd"] == 0.40
    assert len(body["options"]) == 4


@pytest.mark.asyncio
async def test_patch_persists_override_validates_and_audits(client, db_session, _catalog):
    response = await client.patch(
        "/api/v1/workspaces/ai-models",
        json={"routes": {"premium": {"provider": "anthropic", "model": "modelo-complexo"}}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    premium = next(item for item in body["classes"] if item["taskClass"] == "premium")
    assert premium["source"] == "workspace"
    assert premium["model"] == "modelo-complexo"
    assert premium["platformModel"] == "modelo-premium"

    stored = await db_session.scalar(select(WorkspaceAiModelRoute))
    assert stored is not None
    assert stored.task_class == "premium"
    assert stored.provider == "anthropic"
    assert stored.model == "modelo-complexo"

    events = (await db_session.execute(select(KlmAuditEvent))).scalars().all()
    assert any(event.event_type == "ai_model_routing_updated" for event in events)

    # Reset com null volta ao padrão da plataforma e remove o override.
    reset = await client.patch(
        "/api/v1/workspaces/ai-models",
        json={"routes": {"premium": None}},
    )
    assert reset.status_code == 200
    premium_after = next(
        item for item in reset.json()["classes"] if item["taskClass"] == "premium"
    )
    assert premium_after["source"] == "platform"
    assert premium_after["model"] == "modelo-premium"
    assert (await db_session.scalar(select(WorkspaceAiModelRoute))) is None


@pytest.mark.asyncio
async def test_patch_fails_closed_outside_catalog_and_unknown_class(client, db_session, _catalog):
    outside = await client.patch(
        "/api/v1/workspaces/ai-models",
        json={"routes": {"premium": {"provider": "openai", "model": "modelo-fantasma"}}},
    )
    assert outside.status_code == 422
    assert outside.json()["detail"]["code"] == "AI-MODEL-003"

    unknown_class = await client.patch(
        "/api/v1/workspaces/ai-models",
        json={"routes": {"turbo": {"provider": "openai", "model": "modelo-simples"}}},
    )
    assert unknown_class.status_code == 422
    assert unknown_class.json()["detail"]["code"] == "AI-MODEL-002"

    empty = await client.patch("/api/v1/workspaces/ai-models", json={"routes": {}})
    assert empty.status_code == 422

    assert (await db_session.scalar(select(WorkspaceAiModelRoute))) is None


@pytest.mark.asyncio
async def test_fake_runtime_exposes_zero_cost_catalog(client, monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "fake")
    response = await client.get("/api/v1/workspaces/ai-models")
    assert response.status_code == 200
    body = response.json()
    assert body["catalogVersion"] == "local-fake"
    assert all(item["source"] == "platform" for item in body["classes"])
    assert all(item["provider"] == "fake" for item in body["classes"])
    assert body["options"][0]["price"]["inputPerMillionUsd"] == 0.0


@pytest.mark.asyncio
async def test_viewer_reads_but_cannot_write(viewer_client, db_session, _catalog):
    read = await viewer_client.get("/api/v1/workspaces/ai-models")
    assert read.status_code == 200

    write = await viewer_client.patch(
        "/api/v1/workspaces/ai-models",
        json={"routes": {"simple": {"provider": "openai", "model": "modelo-simples"}}},
    )
    assert write.status_code == 403
    assert (await db_session.scalar(select(WorkspaceAiModelRoute))) is None


TEST_CIPHER_KEY = "test-only-ai-models-cipher-key-9Qw2Er5Ty8Ui1Op4As"


@pytest.fixture
def _cipher_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_CIPHER_KEY)


@pytest.mark.asyncio
async def test_credentials_list_is_redacted_per_provider(client, _cipher_key):
    listing = await client.get("/api/v1/workspaces/ai-credentials")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    providers = {item["provider"] for item in body}
    assert {"anthropic", "openai", "gemini", "vertex", "maritaca"} <= providers
    assert all(item["configured"] is False for item in body)

    plaintext = "sk-proj-lista-0123456789-omega"
    stored = await client.put(
        "/api/v1/workspaces/ai-credentials/openai",
        json={"apiKey": plaintext},
    )
    assert stored.status_code == 200

    listing_after = await client.get("/api/v1/workspaces/ai-credentials")
    assert listing_after.status_code == 200
    assert plaintext not in listing_after.text
    openai_entry = next(
        item for item in listing_after.json() if item["provider"] == "openai"
    )
    assert openai_entry["configured"] is True
    assert openai_entry["isActive"] is True
    assert openai_entry["apiKeyMasked"] == f"****{plaintext[-4:]}"


@pytest.mark.asyncio
async def test_platform_overview_lists_byok_without_keys(
    platform_client, db_session, _catalog
):
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
    from app.models.workspace_model_credential import WorkspaceModelCredential

    workspace = Workspace(
        name="Escritório BYOK",
        slug="escritorio-byok",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    ciphertext = "gAAAAA-cifrado-nunca-exposto"
    db_session.add(
        WorkspaceModelCredential(
            workspace_id=workspace.id,
            provider="anthropic",
            api_key_encrypted=ciphertext,
            api_key_hint="igma",
            is_active=True,
        )
    )
    db_session.add(
        WorkspaceAiModelRoute(
            workspace_id=workspace.id,
            task_class="complex",
            provider="openai",
            model="modelo-medio",
        )
    )
    second_workspace = Workspace(
        name="Segundo escritório BYOK",
        slug="segundo-escritorio-byok",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(second_workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceModelCredential(
            workspace_id=second_workspace.id,
            provider="openai",
            api_key_encrypted="gAAAAA-segundo-cifrado-nunca-exposto",
            api_key_hint="undo",
            is_active=True,
        )
    )
    await db_session.flush()

    response = await platform_client.get("/api/v1/platform/ai-models")
    assert response.status_code == 200, response.text
    assert ciphertext not in response.text
    body = response.json()
    assert body["catalogVersion"] == "catalog-test-1"
    assert [item["taskClass"] for item in body["defaults"]] == [
        "simple",
        "medium",
        "complex",
        "premium",
    ]
    assert body["byokWorkspaces"], body
    assert len(body["byokWorkspaces"]) == 2
    entry = next(
        item for item in body["byokWorkspaces"]
        if item["workspaceName"] == "Escritório BYOK"
    )
    assert entry["workspaceName"] == "Escritório BYOK"
    assert entry["providers"][0]["provider"] == "anthropic"
    assert entry["providers"][0]["apiKeyMasked"] == "****igma"
    assert entry["customRouteClasses"] == ["complex"]
    second_entry = next(
        item for item in body["byokWorkspaces"]
        if item["workspaceName"] == "Segundo escritório BYOK"
    )
    assert second_entry["providers"][0]["apiKeyMasked"] == "****undo"
