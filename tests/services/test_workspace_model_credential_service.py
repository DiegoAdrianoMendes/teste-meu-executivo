"""Cifra/decifra, redação, auditoria e ciclo de vida das credenciais BYOK."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.klm_operations import KlmAuditEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_model_credential import WorkspaceModelCredential
from app.services.workspace_model_credential_service import (
    InvalidModelCredentialError,
    ModelCredentialCipher,
    ModelCredentialDecryptionError,
    ModelCredentialNotFoundError,
    UnknownModelCredentialProviderError,
    WorkspaceModelCredentialService,
    credential_to_redacted_dict,
    redact_api_key,
)

TEST_CIPHER_KEY = "test-only-model-credential-key-9Xw2mQ7pL4hV8sB1nD5t"
PLAINTEXT_KEY = "sk-ant-teste-0123456789-final-abcd"


@pytest.fixture(autouse=True)
def _configure_cipher_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_CIPHER_KEY)


async def _seed_workspace_and_user(db: AsyncSession) -> tuple[Workspace, User]:
    workspace = Workspace(
        name="Escritório BYOK",
        slug="escritorio-byok",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    user = User(
        name="Admin BYOK",
        email="byok@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="AB",
        color="#111827",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return workspace, user


@pytest.mark.asyncio
async def test_set_credential_encrypts_at_rest_and_decrypts_roundtrip(db_session):
    workspace, user = await _seed_workspace_and_user(db_session)
    service = WorkspaceModelCredentialService(db_session)

    credential = await service.set_credential(
        workspace_id=workspace.id,
        provider="anthropic",
        api_key=PLAINTEXT_KEY,
        actor_user_id=user.id,
    )

    # Em repouso nunca fica em claro.
    assert credential.api_key_encrypted != PLAINTEXT_KEY
    assert PLAINTEXT_KEY not in credential.api_key_encrypted
    assert credential.api_key_hint == PLAINTEXT_KEY[-4:]
    assert credential.is_active is True

    # Roundtrip interno decifra o valor original.
    decrypted = await service.get_decrypted(workspace.id, "anthropic")
    assert decrypted == PLAINTEXT_KEY


@pytest.mark.asyncio
async def test_redacted_representation_never_contains_the_key(db_session):
    workspace, user = await _seed_workspace_and_user(db_session)
    service = WorkspaceModelCredentialService(db_session)
    credential = await service.set_credential(
        workspace_id=workspace.id,
        provider="openai",
        api_key=PLAINTEXT_KEY,
        actor_user_id=user.id,
    )

    redacted = credential_to_redacted_dict(credential)
    serialized = str(redacted)
    assert PLAINTEXT_KEY not in serialized
    assert redacted["apiKeyMasked"] == f"****{PLAINTEXT_KEY[-4:]}"
    assert redacted["configured"] is True
    assert redact_api_key("") == "****"


@pytest.mark.asyncio
async def test_rotate_replaces_key_and_requires_existing(db_session):
    workspace, user = await _seed_workspace_and_user(db_session)
    service = WorkspaceModelCredentialService(db_session)

    with pytest.raises(ModelCredentialNotFoundError):
        await service.rotate_credential(
            workspace_id=workspace.id,
            provider="gemini",
            api_key=PLAINTEXT_KEY,
            actor_user_id=user.id,
        )

    await service.set_credential(
        workspace_id=workspace.id,
        provider="gemini",
        api_key=PLAINTEXT_KEY,
        actor_user_id=user.id,
    )
    rotated = await service.rotate_credential(
        workspace_id=workspace.id,
        provider="gemini",
        api_key="nova-chave-rotacionada-wxyz",
        actor_user_id=user.id,
    )
    assert rotated.api_key_hint == "wxyz"
    assert await service.get_decrypted(workspace.id, "gemini") == (
        "nova-chave-rotacionada-wxyz"
    )

    # Continua única por (workspace, provider).
    rows = (
        await db_session.execute(
            select(WorkspaceModelCredential).where(
                WorkspaceModelCredential.workspace_id == workspace.id,
                WorkspaceModelCredential.provider == "gemini",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_deactivate_stops_internal_decryption(db_session):
    workspace, user = await _seed_workspace_and_user(db_session)
    service = WorkspaceModelCredentialService(db_session)
    await service.set_credential(
        workspace_id=workspace.id,
        provider="maritaca",
        api_key=PLAINTEXT_KEY,
        actor_user_id=user.id,
    )

    credential = await service.deactivate_credential(
        workspace_id=workspace.id,
        provider="maritaca",
        actor_user_id=user.id,
    )
    assert credential.is_active is False
    assert await service.get_decrypted(workspace.id, "maritaca") is None


@pytest.mark.asyncio
async def test_every_key_change_records_redacted_audit_event(db_session):
    workspace, user = await _seed_workspace_and_user(db_session)
    service = WorkspaceModelCredentialService(db_session)

    await service.set_credential(
        workspace_id=workspace.id,
        provider="vertex",
        api_key=PLAINTEXT_KEY,
        actor_user_id=user.id,
    )
    await service.rotate_credential(
        workspace_id=workspace.id,
        provider="vertex",
        api_key="outra-chave-secreta-9k2m",
        actor_user_id=user.id,
    )
    await service.deactivate_credential(
        workspace_id=workspace.id,
        provider="vertex",
        actor_user_id=user.id,
    )

    events = (
        await db_session.execute(
            select(KlmAuditEvent)
            .where(KlmAuditEvent.workspace_id == workspace.id)
            .order_by(KlmAuditEvent.occurred_at)
        )
    ).scalars().all()
    event_types = [event.event_type for event in events]
    assert event_types == [
        "workspace_model_credential_created",
        "workspace_model_credential_rotated",
        "workspace_model_credential_deactivated",
    ]
    for event in events:
        payload = f"{event.summary} {event.after_data}"
        assert PLAINTEXT_KEY not in payload
        assert "outra-chave-secreta-9k2m" not in payload


@pytest.mark.asyncio
async def test_validation_fails_closed(db_session):
    workspace, user = await _seed_workspace_and_user(db_session)
    service = WorkspaceModelCredentialService(db_session)

    with pytest.raises(UnknownModelCredentialProviderError):
        await service.set_credential(
            workspace_id=workspace.id,
            provider="provider-inexistente",
            api_key=PLAINTEXT_KEY,
            actor_user_id=user.id,
        )
    with pytest.raises(InvalidModelCredentialError):
        await service.set_credential(
            workspace_id=workspace.id,
            provider="openai",
            api_key="curta",
            actor_user_id=user.id,
        )


@pytest.mark.asyncio
async def test_decrypt_with_wrong_cipher_key_raises_instead_of_inventing(
    db_session, monkeypatch
):
    workspace, user = await _seed_workspace_and_user(db_session)
    service = WorkspaceModelCredentialService(db_session)
    await service.set_credential(
        workspace_id=workspace.id,
        provider="anthropic",
        api_key=PLAINTEXT_KEY,
        actor_user_id=user.id,
    )

    monkeypatch.setattr(
        settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        "different-model-credential-key-3Fp6zR9tK2cW7vM4qJ8x",
    )
    fresh_service = WorkspaceModelCredentialService(db_session)
    with pytest.raises(ModelCredentialDecryptionError):
        await fresh_service.get_decrypted(workspace.id, "anthropic")


def test_cipher_requires_dedicated_strong_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="INTEGRATION_SECRET_ENCRYPTION_KEY"):
        ModelCredentialCipher()

    shared = "test-only-shared-secret-with-more-than-32-bytes"
    monkeypatch.setattr(settings, "SECRET_KEY", shared)
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", shared)
    with pytest.raises(RuntimeError, match="fraca ou não dedicada"):
        ModelCredentialCipher()


def test_cipher_supports_key_rotation_with_multifernet(monkeypatch):
    old_key = "old-model-credential-cipher-key-5Hq8wN1zT4bY7kC2mV6r"
    new_key = "new-model-credential-cipher-key-8Rv3xL6dP9gS2jF5nB1u"

    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", old_key)
    encrypted_with_old = ModelCredentialCipher().encrypt(PLAINTEXT_KEY)

    # Rotação: chave nova ativa na frente, antiga ainda decifra o legado.
    monkeypatch.setattr(
        settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", f"{new_key},{old_key}"
    )
    rotated_cipher = ModelCredentialCipher()
    assert rotated_cipher.decrypt_strict(encrypted_with_old) == PLAINTEXT_KEY
    assert rotated_cipher.decrypt_strict(rotated_cipher.encrypt(PLAINTEXT_KEY)) == (
        PLAINTEXT_KEY
    )
