"""Testes das pendências técnicas finais (Sprint 2026-07-23):

- rotação de chave de tokens com MultiFernet versionado (A4/W1.9);
- decrypt que falha com todas as chaves marca a conexão em erro + audita
  (nunca retorna None silencioso para o chamador de negócio);
- re_encrypt_all migra segredos antigos para a chave ativa;
- PIN de re-registro reutilizado em vez de gerado de novo (M4/W1.10);
- 133005 traduzido em mensagem acionável.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select

from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.services.whatsapp.connection_service import (
    META_PIN_MISMATCH_SUBCODE,
    WhatsAppConnectionService,
)
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.secrets import (
    WhatsAppSecretCipher,
    WhatsAppSecretDecryptionError,
    re_encrypt_all,
)


async def _make_connection(db_session, workspace_id, **overrides):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        transport_mode="official_api",
        is_default=True,
        phone_number_id=overrides.pop("phone_number_id", f"pn_{uuid4().hex[:8]}"),
        status=WaConnectionStatus.connected,
        **overrides,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


def test_active_key_encrypts_and_old_key_still_decrypts(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp.secrets.settings.WHATSAPP_SECRET_ENCRYPTION_KEYS",
        "",
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.secrets.settings.WHATSAPP_SECRET_ENCRYPTION_KEY",
        "old-key-material",
        raising=False,
    )
    old_cipher = WhatsAppSecretCipher()
    encrypted_with_old = old_cipher.encrypt("meu-token-secreto")

    # Rotaciona: nova chave ativa primeiro, a antiga continua na lista.
    monkeypatch.setattr(
        "app.services.whatsapp.secrets.settings.WHATSAPP_SECRET_ENCRYPTION_KEYS",
        "new-key-material,old-key-material",
        raising=False,
    )
    rotated_cipher = WhatsAppSecretCipher()

    # A chave antiga ainda decifra o segredo cifrado antes da rotação.
    assert rotated_cipher.decrypt(encrypted_with_old) == "meu-token-secreto"
    # Mas a cifra nova usa a chave ativa (primeira da lista).
    assert not rotated_cipher.is_encrypted_with_active_key(encrypted_with_old)
    new_value = rotated_cipher.encrypt("outro-token")
    assert rotated_cipher.is_encrypted_with_active_key(new_value)


def test_decrypt_fails_with_all_keys_raises_structured_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp.secrets.settings.WHATSAPP_SECRET_ENCRYPTION_KEYS",
        "key-a,key-b",
        raising=False,
    )
    cipher = WhatsAppSecretCipher()
    garbage = Fernet(Fernet.generate_key()).encrypt(b"nope").decode("utf-8")

    assert cipher.decrypt(garbage) is None  # best-effort ainda funciona
    with pytest.raises(WhatsAppSecretDecryptionError):
        cipher.decrypt_strict(garbage)


@pytest.mark.asyncio
async def test_secret_decrypt_failure_marks_connection_error_and_audits(db_session, monkeypatch):
    workspace_id = uuid4()
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    db_session.add(
        Workspace(
            id=workspace_id,
            name="WS",
            slug=f"ws-{uuid4().hex[:8]}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
    )
    await db_session.flush()

    connection = await _make_connection(db_session, workspace_id)
    # Segredo cifrado com uma chave completamente alheia às configuradas.
    rogue_cipher = Fernet(Fernet.generate_key())
    connection.access_token_encrypted = rogue_cipher.encrypt(b"token").decode("utf-8")
    await db_session.flush()

    service = WhatsAppConnectionService(db_session)
    with pytest.raises(WhatsAppProviderError) as excinfo:
        await service.resolve_meta_access_token(connection)
    assert excinfo.value.code == "WA-SECRET-DECRYPT"

    await db_session.refresh(connection)
    assert connection.status == WaConnectionStatus.error
    assert connection.last_error_code == "WA-SECRET-DECRYPT"

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.entity_id == connection.id,
            KlmAuditEvent.event_type == "whatsapp_connection_secret_decrypt_failed",
        )
    )
    assert audit is not None


@pytest.mark.asyncio
async def test_re_encrypt_all_migrates_secrets_to_active_key(db_session, monkeypatch):
    workspace_id = uuid4()
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    db_session.add(
        Workspace(
            id=workspace_id,
            name="WS2",
            slug=f"ws2-{uuid4().hex[:8]}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
    )
    await db_session.flush()

    monkeypatch.setattr(
        "app.services.whatsapp.secrets.settings.WHATSAPP_SECRET_ENCRYPTION_KEYS",
        "",
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.secrets.settings.WHATSAPP_SECRET_ENCRYPTION_KEY",
        "old-key-material",
        raising=False,
    )
    old_cipher = WhatsAppSecretCipher()
    connection = await _make_connection(db_session, workspace_id)
    connection.access_token_encrypted = old_cipher.encrypt("segredo-antigo")
    await db_session.flush()

    monkeypatch.setattr(
        "app.services.whatsapp.secrets.settings.WHATSAPP_SECRET_ENCRYPTION_KEYS",
        "new-key-material,old-key-material",
        raising=False,
    )
    new_cipher = WhatsAppSecretCipher()

    dry_report = await re_encrypt_all(db_session, dry_run=True, cipher=new_cipher)
    assert dry_report["rotated"] == 1
    assert dry_report["failed"] == []
    await db_session.refresh(connection)
    # Dry-run não persiste.
    assert not new_cipher.is_encrypted_with_active_key(connection.access_token_encrypted)

    real_report = await re_encrypt_all(db_session, dry_run=False, cipher=new_cipher)
    assert real_report["rotated"] == 1
    await db_session.refresh(connection)
    assert new_cipher.is_encrypted_with_active_key(connection.access_token_encrypted)
    assert new_cipher.decrypt(connection.access_token_encrypted) == "segredo-antigo"


@pytest.mark.asyncio
async def test_registration_pin_reused_on_existing_connection(db_session):
    workspace_id = uuid4()
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    db_session.add(
        Workspace(
            id=workspace_id,
            name="WS3",
            slug=f"ws3-{uuid4().hex[:8]}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
    )
    await db_session.flush()

    connection = await _make_connection(db_session, workspace_id, phone_number_id="pn_pin_reuse")
    service = WhatsAppConnectionService(db_session)
    connection.registration_pin_encrypted = service.cipher.encrypt("123456")
    await db_session.flush()

    reused = service._reuse_or_generate_pin(connection)
    assert reused == "123456"

    stored = await service.get_stored_registration_pin(workspace_id, "pn_pin_reuse")
    assert stored == "123456"


def test_registration_pin_generated_for_new_connection(db_session):
    service = WhatsAppConnectionService(db_session)
    brand_new = WhatsAppConnection(workspace_id=uuid4())
    generated = service._reuse_or_generate_pin(brand_new)
    assert len(generated) == 6
    assert generated.isdigit()


def test_translate_registration_error_maps_133005_to_actionable_message():
    exc = WhatsAppProviderError(
        code="WA-002",
        message="Two step verification pin mismatch",
        status_code=400,
        provider_error_subcode=META_PIN_MISMATCH_SUBCODE,
    )
    translated = WhatsAppConnectionService.translate_registration_error(exc)
    assert isinstance(translated, HTTPException)
    assert translated.status_code == 422
    assert translated.detail["code"] == "WA-PIN-MISMATCH"
    assert "PIN" in translated.detail["message"]


def test_translate_registration_error_passes_through_other_codes():
    exc = WhatsAppProviderError(code="WA-007", message="sem token", status_code=503)
    translated = WhatsAppConnectionService.translate_registration_error(exc)
    assert translated.status_code == 503
    assert translated.detail["code"] == "WA-007"
