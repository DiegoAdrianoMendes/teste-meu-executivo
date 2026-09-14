"""Chaves de provedores de IA da plataforma (Blackbox): cifra, redação, RBAC e auditoria."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.main import app as fastapi_app
from app.models.auth_security import AuthSecurityEvent
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_model_credential import PlatformModelCredential
from app.models.user import AgentType, User, UserRole
from app.models.workspace_model_credential import MODEL_CREDENTIAL_PROVIDERS
from app.services.workspace_model_credential_service import ModelCredentialCipher

TEST_CIPHER_KEY = "test-only-platform-credential-key-7Hk2mQ9vB4nX8rT5wL"
PLAINTEXT_KEY = "chave-gemini-sintetica-0123456789-final-abcd"
ROTATED_KEY = "chave-gemini-rotacionada-9876543210-final-wxyz"
ENV_ONLY_KEY = "chave-anthropic-da-env-0123456789-final-envk"

BASE = "/api/v1/platform/ai-credentials"


@pytest.fixture(autouse=True)
def _configure_cipher_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_CIPHER_KEY)
    # Sem chave de env por padrão: a fonte efetiva parte de "none".
    for attribute in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "MARITACA_API_KEY"):
        monkeypatch.setattr(settings, attribute, "")


@pytest_asyncio.fixture
async def view_only_platform_client(db_session):
    """Staff da plataforma com pacote product_operations: ai_operations só com view."""
    user = User(
        name="Produto e Operações",
        email="produto-ops-ai-cred@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="PO",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_role_package="product_operations",
        platform_access_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    # O finalizador de auditoria faz expunge_all + rollback num 403: o usuário
    # precisa estar commitado para o segundo pedido ainda encontrá-lo.
    await db_session.commit()
    context = AuthContext(
        user_id=user.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )

    async def override_get_db():
        yield db_session

    async def override_get_current_auth_context():
        return context

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_auth_context] = override_get_current_auth_context
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


async def _stored(db_session, provider: str) -> PlatformModelCredential | None:
    return await db_session.scalar(
        select(PlatformModelCredential).where(PlatformModelCredential.provider == provider)
    )


async def _audit_text(db_session) -> str:
    """Todo before/after da trilha canônica e os details dos eventos de segurança."""
    audit_rows = (await db_session.execute(select(PlatformAuditEvent))).scalars().all()
    security_rows = (await db_session.execute(select(AuthSecurityEvent))).scalars().all()
    chunks = [
        f"{row.before}|{row.after}|{row.resource_label_snapshot}|{row.reason}|"
        f"{row.error_code}|{row.route}|{row.resource_type}"
        for row in audit_rows
    ]
    chunks.extend(str(row.details) for row in security_rows)
    return "\n".join(chunks)


@pytest.mark.asyncio
async def test_put_stores_encrypted_key_and_returns_only_redaction(
    platform_client, db_session, platform_auth_context: AuthContext
):
    resp = await platform_client.put(f"{BASE}/gemini", json={"apiKey": PLAINTEXT_KEY})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "gemini"
    assert body["configured"] is True
    assert body["isActive"] is True
    assert body["apiKeyMasked"] == "****abcd"
    assert body["keyLast4"] == "abcd"
    assert body["keyManaged"] is True
    assert body["adapterRegistered"] is True
    assert body["effectiveSource"] == "blackbox"
    assert body["updatedByUserId"] == str(platform_auth_context.user_id)
    assert PLAINTEXT_KEY not in resp.text

    stored = await _stored(db_session, "gemini")
    assert stored is not None
    assert stored.api_key_encrypted != PLAINTEXT_KEY
    assert PLAINTEXT_KEY not in stored.api_key_encrypted
    assert stored.api_key_hint == "abcd"
    assert stored.is_active is True
    assert stored.updated_by_user_id == platform_auth_context.user_id
    assert ModelCredentialCipher().decrypt_strict(stored.api_key_encrypted) == PLAINTEXT_KEY


@pytest.mark.asyncio
async def test_list_reports_gate_and_effective_sources(platform_client, monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", ENV_ONLY_KEY)

    resp = await platform_client.get(BASE)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["gate"]) == {"modelApisEnabled", "onlineApproved", "cipherConfigured"}
    assert body["gate"]["cipherConfigured"] is True
    providers = [item["provider"] for item in body["providers"]]
    assert providers == list(MODEL_CREDENTIAL_PROVIDERS)
    by_provider = {item["provider"]: item for item in body["providers"]}

    assert by_provider["gemini"]["configured"] is False
    assert by_provider["gemini"]["effectiveSource"] == "none"
    assert by_provider["gemini"]["apiKeyMasked"] is None

    anthropic = by_provider["anthropic"]
    assert anthropic["envConfigured"] is True
    assert anthropic["effectiveSource"] == "environment"
    assert ENV_ONLY_KEY not in resp.text

    vertex = by_provider["vertex"]
    assert vertex["keyManaged"] is False
    assert vertex["adapterRegistered"] is False
    assert vertex["effectiveSource"] == "none"
    assert vertex["reason"]

    await platform_client.put(f"{BASE}/gemini", json={"apiKey": PLAINTEXT_KEY})
    after = (await platform_client.get(BASE)).json()
    gemini = {item["provider"]: item for item in after["providers"]}["gemini"]
    assert gemini["effectiveSource"] == "blackbox"
    assert gemini["apiKeyMasked"] == "****abcd"


@pytest.mark.asyncio
async def test_rotation_updates_hint_and_audit_never_carries_the_key(
    platform_client, db_session
):
    first = await platform_client.put(f"{BASE}/gemini", json={"apiKey": PLAINTEXT_KEY})
    assert first.status_code == 200, first.text
    second = await platform_client.put(f"{BASE}/gemini", json={"apiKey": ROTATED_KEY})
    assert second.status_code == 200, second.text
    assert second.json()["keyLast4"] == "wxyz"

    stored = await _stored(db_session, "gemini")
    assert ModelCredentialCipher().decrypt_strict(stored.api_key_encrypted) == ROTATED_KEY

    events = (
        (
            await db_session.execute(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.resource_type == "platform_model_credential",
                    PlatformAuditEvent.resource_id == "gemini",
                )
            )
        )
        .scalars()
        .all()
    )
    actions = sorted(event.action for event in events)
    assert actions == ["ai_credentials.create", "ai_credentials.rotate"]
    assert {event.module for event in events} == {"ai_operations"}
    assert {event.result for event in events} == {"success"}
    rotate = next(event for event in events if event.action == "ai_credentials.rotate")
    assert rotate.before["keyLast4"] == "abcd"
    assert rotate.after["keyLast4"] == "wxyz"

    audit_text = await _audit_text(db_session)
    assert PLAINTEXT_KEY not in audit_text
    assert ROTATED_KEY not in audit_text


@pytest.mark.asyncio
async def test_delete_deactivates_without_erasing_and_audits(platform_client, db_session):
    await platform_client.put(f"{BASE}/openai", json={"apiKey": PLAINTEXT_KEY})

    resp = await platform_client.delete(f"{BASE}/openai")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["isActive"] is False
    assert body["effectiveSource"] == "none"

    stored = await _stored(db_session, "openai")
    assert stored.is_active is False
    assert stored.api_key_encrypted  # desativar não apaga o ciphertext

    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.action == "ai_credentials.deactivate",
            PlatformAuditEvent.resource_id == "openai",
        )
    )
    assert event is not None
    assert event.after["isActive"] is False
    assert PLAINTEXT_KEY not in await _audit_text(db_session)

    missing = await platform_client.delete(f"{BASE}/anthropic")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PLATFORM-AI-CRED-002"

    again = await platform_client.delete(f"{BASE}/openai")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "PLATFORM-AI-CRED-006"


@pytest.mark.asyncio
async def test_validation_and_provider_errors(platform_client):
    unknown = await platform_client.put(f"{BASE}/foo", json={"apiKey": PLAINTEXT_KEY})
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "PLATFORM-AI-CRED-001"

    unknown_delete = await platform_client.delete(f"{BASE}/foo")
    assert unknown_delete.status_code == 404
    assert unknown_delete.json()["detail"]["code"] == "PLATFORM-AI-CRED-001"

    # Passa no tamanho mínimo do schema, mas fica curta depois do strip.
    blank = await platform_client.put(f"{BASE}/gemini", json={"apiKey": "        ab"})
    assert blank.status_code == 422
    assert blank.json()["detail"]["code"] == "PLATFORM-AI-CRED-003"

    short = await platform_client.put(f"{BASE}/gemini", json={"apiKey": "abc"})
    assert short.status_code == 422  # validação do schema (min_length)

    vertex = await platform_client.put(f"{BASE}/vertex", json={"apiKey": PLAINTEXT_KEY})
    assert vertex.status_code == 422
    assert vertex.json()["detail"]["code"] == "PLATFORM-AI-CRED-004"


@pytest.mark.asyncio
async def test_workspace_identity_is_rejected(client):
    listing = await client.get(BASE)
    assert listing.status_code == 403
    assert listing.json()["detail"]["code"] == "BLACKBOX-IDENTITY-REQUIRED"

    write = await client.put(f"{BASE}/gemini", json={"apiKey": PLAINTEXT_KEY})
    assert write.status_code == 403
    assert write.json()["detail"]["code"] == "BLACKBOX-IDENTITY-REQUIRED"
    assert PLAINTEXT_KEY not in write.text


@pytest.mark.asyncio
async def test_view_only_staff_reads_but_cannot_write(view_only_platform_client, db_session):
    listing = await view_only_platform_client.get(BASE)
    assert listing.status_code == 200, listing.text

    write = await view_only_platform_client.put(f"{BASE}/gemini", json={"apiKey": PLAINTEXT_KEY})
    assert write.status_code == 403
    assert write.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"
    assert await _stored(db_session, "gemini") is None

    delete = await view_only_platform_client.delete(f"{BASE}/gemini")
    assert delete.status_code == 403
    assert delete.json()["detail"]["code"] == "PLATFORM-PERMISSION-DENIED"

    # O 403 vem do pacote sem "manage", não de um ator desconhecido.
    denied = (
        (
            await db_session.execute(
                select(PlatformAuditEvent).where(PlatformAuditEvent.result == "denied")
            )
        )
        .scalars()
        .all()
    )
    assert denied
    assert {event.actor_role_snapshot for event in denied} == {"product_operations"}


@pytest.mark.asyncio
async def test_missing_cipher_is_reported_and_blocks_writes(
    platform_client, monkeypatch, db_session
):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", "")

    listing = await platform_client.get(BASE)
    assert listing.status_code == 200, listing.text
    assert listing.json()["gate"]["cipherConfigured"] is False

    write = await platform_client.put(f"{BASE}/gemini", json={"apiKey": PLAINTEXT_KEY})
    assert write.status_code == 503
    assert write.json()["detail"]["code"] == "PLATFORM-AI-CRED-005"
    assert await _stored(db_session, "gemini") is None
