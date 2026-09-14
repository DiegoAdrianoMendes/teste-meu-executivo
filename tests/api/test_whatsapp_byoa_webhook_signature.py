"""BYOA multi-tenant webhook signature: cada conexão pode ter o próprio App
Secret (app_secret_encrypted), usado para validar a assinatura HMAC do
webhook em vez de depender só do WHATSAPP_APP_SECRET global. Cobre:

- dois "escritórios" com App Secrets diferentes: cada um valida contra o
  próprio segredo e um NÃO consegue forjar a assinatura com o segredo do
  outro (fail-closed cross-tenant);
- conexão sem segredo próprio cai no fallback global (compatibilidade);
- assinatura inválida em qualquer cenário -> 403;
- o App Secret nunca é devolvido em claro nas respostas da API;
- persistência cifrada em app_secret_encrypted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.platform_official_whatsapp import (
    PlatformOfficialWhatsAppConnection,
    PlatformOfficialWhatsAppRole,
)
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.services.whatsapp.secrets import WhatsAppSecretCipher
from tests.conftest import TestSessionLocal


@pytest.fixture(autouse=True)
def _isolate_webhook_dispatch_from_external_broker(monkeypatch):
    """Valida o enqueue sem exigir Redis nos testes de contrato HTTP."""
    queued: list[dict] = []
    monkeypatch.setattr(
        "app.workers.whatsapp_webhook_tasks.process_inbound_webhook.apply_async",
        lambda *args, **kwargs: queued.append(kwargs),
    )
    return queued


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _body(phone_number_id: str) -> bytes:
    return _payload_body([{"changes": [_message_change(phone_number_id)]}])


def _payload_body(entries: list[dict]) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": entries,
        },
        separators=(",", ":"),
    ).encode()


def _message_change(phone_number_id: str) -> dict:
    return {
        "field": "messages",
        "value": {
            "metadata": {"phone_number_id": phone_number_id},
            "messages": [],
            "statuses": [],
        },
    }


def _template_change() -> dict:
    return {
        "field": "message_template_status_update",
        "value": {
            "event": "APPROVED",
            "message_template_id": "template-security-test",
        },
    }


def _coexistence_history_body(phone_number_id: str) -> bytes:
    return json.dumps(
        {
            "event": "history",
            "data": {
                "metadata": {"phone_number_id": phone_number_id},
                "history": [],
            },
        },
        separators=(",", ":"),
    ).encode()


async def _make_connection(
    db_session,
    workspace_id,
    *,
    phone_number_id,
    app_secret=None,
    waba_id=None,
):
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        transport_mode="official_api",
        is_default=True,
        phone_number_id=phone_number_id,
        business_account_id=waba_id,
        waba_id=waba_id,
        status=WaConnectionStatus.connected,
        app_secret_encrypted=cipher.encrypt(app_secret) if app_secret else None,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


@pytest.mark.asyncio
async def test_byoa_connection_validates_against_own_app_secret_and_rejects_other_tenants_secret(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")  # sem fallback global neste teste

    phone_a = f"pn-a-{uuid4().hex[:8]}"
    phone_b = f"pn-b-{uuid4().hex[:8]}"
    secret_a = "escritorio-a-app-secret"
    secret_b = "escritorio-b-app-secret"

    await _make_connection(db_session, auth_context.workspace_id, phone_number_id=phone_a, app_secret=secret_a)
    await _make_connection(db_session, uuid4(), phone_number_id=phone_b, app_secret=secret_b)
    await db_session.commit()

    body_a = _body(phone_a)

    # Assinado com o próprio secret do escritório A: aceito.
    response_ok = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body_a,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(secret_a, body_a),
        },
    )
    assert response_ok.status_code == 200

    # O MESMO corpo, mas assinado com o secret do escritório B: rejeitado
    # (cross-tenant fail-closed, mesmo que B seja um app Meta legítimo).
    response_cross_tenant = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body_a,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(secret_b, body_a),
        },
    )
    assert response_cross_tenant.status_code == 403
    assert response_cross_tenant.json()["detail"]["code"] == "WA-004"


@pytest.mark.asyncio
async def test_mixed_phone_ids_from_different_tenants_rejected_even_with_same_app_secret(
    client,
    db_session,
    auth_context,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")

    phone_a = f"pn-mixed-a-{uuid4().hex[:8]}"
    phone_b = f"pn-mixed-b-{uuid4().hex[:8]}"
    shared_secret = "shared-secret-does-not-merge-owners"
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=phone_a,
        app_secret=shared_secret,
    )
    await _make_connection(
        db_session,
        uuid4(),
        phone_number_id=phone_b,
        app_secret=shared_secret,
    )
    await db_session.commit()

    body = _payload_body(
        [
            {
                "changes": [
                    _message_change(phone_a),
                    _message_change(phone_b),
                ]
            }
        ]
    )
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(shared_secret, body),
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WA-004"
    assert _isolate_webhook_dispatch_from_external_broker == []


@pytest.mark.asyncio
async def test_platform_and_tenant_references_never_share_one_webhook_authority(
    client,
    db_session,
    auth_context,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    shared_secret = "same-key-still-distinct-authorities"
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", shared_secret)
    platform_phone = f"pn-platform-{uuid4().hex[:8]}"
    tenant_phone = f"pn-tenant-{uuid4().hex[:8]}"
    db_session.add(
        PlatformOfficialWhatsAppConnection(
            role=PlatformOfficialWhatsAppRole.support,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
            phone_number_id=platform_phone,
            is_active=True,
            status=WaConnectionStatus.connected,
        )
    )
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=tenant_phone,
        app_secret=shared_secret,
    )
    await db_session.commit()

    body = _payload_body(
        [
            {
                "changes": [
                    _message_change(platform_phone),
                    _message_change(tenant_phone),
                ]
            }
        ]
    )
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(shared_secret, body),
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WA-004"
    assert _isolate_webhook_dispatch_from_external_broker == []


@pytest.mark.asyncio
async def test_same_owner_with_different_meta_app_secrets_is_rejected(
    client,
    db_session,
    auth_context,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")

    phone_a = f"pn-app-a-{uuid4().hex[:8]}"
    phone_b = f"pn-app-b-{uuid4().hex[:8]}"
    secret_a = "same-owner-meta-app-a"
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=phone_a,
        app_secret=secret_a,
    )
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=phone_b,
        app_secret="same-owner-meta-app-b",
    )
    await db_session.commit()

    body = _payload_body(
        [
            {
                "changes": [
                    _message_change(phone_a),
                    _message_change(phone_b),
                ]
            }
        ]
    )
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(secret_a, body),
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WA-004"
    assert _isolate_webhook_dispatch_from_external_broker == []


@pytest.mark.asyncio
async def test_own_phone_plus_victim_waba_template_rejected_as_mixed_identity(
    client,
    db_session,
    auth_context,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")

    own_phone = f"pn-own-{uuid4().hex[:8]}"
    own_waba = f"waba-own-{uuid4().hex[:8]}"
    victim_waba = f"waba-victim-{uuid4().hex[:8]}"
    own_secret = "own-meta-app-secret"
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=own_phone,
        app_secret=own_secret,
        waba_id=own_waba,
    )
    await _make_connection(
        db_session,
        uuid4(),
        phone_number_id=f"pn-victim-{uuid4().hex[:8]}",
        app_secret="victim-meta-app-secret",
        waba_id=victim_waba,
    )
    await db_session.commit()

    body = _payload_body(
        [
            {
                "id": own_waba,
                "changes": [_message_change(own_phone)],
            },
            {
                "id": victim_waba,
                "changes": [_template_change()],
            },
        ]
    )
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(own_secret, body),
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WA-004"
    assert _isolate_webhook_dispatch_from_external_broker == []


@pytest.mark.asyncio
async def test_byoa_connection_cannot_be_authenticated_by_global_fallback(
    client,
    db_session,
    auth_context,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    global_secret = "global-secret-must-not-supplant-byoa"
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", global_secret)

    phone_id = f"pn-byoa-global-{uuid4().hex[:8]}"
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=phone_id,
        app_secret="connection-specific-secret",
    )
    await db_session.commit()

    body = _body(phone_id)
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(global_secret, body),
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WA-004"
    assert _isolate_webhook_dispatch_from_external_broker == []


@pytest.mark.asyncio
async def test_coexistence_partner_envelope_is_bound_to_its_byoa_phone_secret(
    client,
    db_session,
    auth_context,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    global_secret = "global-secret-not-valid-for-coexistence-byoa"
    own_secret = "coexistence-byoa-secret"
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", global_secret)

    phone_id = f"pn-coexistence-{uuid4().hex[:8]}"
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=phone_id,
        app_secret=own_secret,
    )
    await db_session.commit()

    body = _coexistence_history_body(phone_id)
    rejected = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(global_secret, body),
        },
    )
    accepted = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(own_secret, body),
        },
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert len(_isolate_webhook_dispatch_from_external_broker) == 1


@pytest.mark.asyncio
async def test_same_owner_and_app_accepts_multiple_phone_values_and_template_waba(
    client,
    db_session,
    auth_context,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")

    shared_waba = f"waba-one-owner-{uuid4().hex[:8]}"
    shared_secret = "one-owner-one-meta-app-secret"
    phone_a = f"pn-one-owner-a-{uuid4().hex[:8]}"
    phone_b = f"pn-one-owner-b-{uuid4().hex[:8]}"
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=phone_a,
        app_secret=shared_secret,
        waba_id=shared_waba,
    )
    await _make_connection(
        db_session,
        auth_context.workspace_id,
        phone_number_id=phone_b,
        app_secret=shared_secret,
        waba_id=shared_waba,
    )
    await db_session.commit()

    body = _payload_body(
        [
            {
                "id": shared_waba,
                "changes": [
                    _message_change(phone_a),
                    _message_change(phone_b),
                    _template_change(),
                ],
            }
        ]
    )
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(shared_secret, body),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(_isolate_webhook_dispatch_from_external_broker) == 1


@pytest.mark.asyncio
async def test_second_byoa_tenant_also_validates_only_against_its_own_secret(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")

    phone_b = f"pn-b-{uuid4().hex[:8]}"
    secret_b = "escritorio-b-app-secret"
    await _make_connection(db_session, auth_context.workspace_id, phone_number_id=phone_b, app_secret=secret_b)
    await db_session.commit()

    body_b = _body(phone_b)
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body_b,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(secret_b, body_b),
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_connection_without_own_secret_falls_back_to_global_app_secret(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    global_secret = "global-fallback-secret"
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", global_secret)

    phone_id = f"pn-{uuid4().hex[:8]}"
    await _make_connection(db_session, auth_context.workspace_id, phone_number_id=phone_id, app_secret=None)
    await db_session.commit()

    body = _body(phone_id)
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(global_secret, body),
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_byoa_connection_signature_wrong_secret_rejected_with_403(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")

    phone_id = f"pn-{uuid4().hex[:8]}"
    await _make_connection(db_session, auth_context.workspace_id, phone_number_id=phone_id, app_secret="real-secret")
    await db_session.commit()

    body = _body(phone_id)
    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign("wrong-secret", body),
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WA-004"


@pytest.mark.asyncio
async def test_app_secret_persisted_encrypted_on_manual_connection_and_never_returned_in_clear(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    plaintext_secret = "meu-app-secret-do-escritorio"

    # Fluxo direto no service, cobrindo a cifragem real na coluna existente
    # e o contrato de que o valor em claro nunca volta na resposta.
    from app.schemas.whatsapp import WhatsAppConnectionCreateManual
    from app.services.whatsapp.connection_service import WhatsAppConnectionService
    from app.services.whatsapp.qr_gateway_client import QrGatewayClient

    class _StubMetaClient:
        async def get_phone_number_details(self, *_args, **_kwargs):
            return {"display_phone_number": "+55 11 99999-9999", "verified_name": "Escritório"}

        async def subscribe_app(self, *_args, **_kwargs):
            return None

        async def register_phone_number(self, *_args, **_kwargs):
            return None

    service = WhatsAppConnectionService(
        db_session,
        meta_client=_StubMetaClient(),
        qr_client=QrGatewayClient(),
    )
    read_model = await service.create_or_update_manual_connection(
        workspace_id=auth_context.workspace_id,
        payload=WhatsAppConnectionCreateManual(
            access_token="token-abc",
            phone_number_id=f"pn-{uuid4().hex[:8]}",
            business_account_id="waba-abc",
            app_secret=plaintext_secret,
        ),
    )
    assert read_model.appSecretConfigured is True
    assert plaintext_secret not in read_model.model_dump_json()

    from sqlalchemy import select

    stored = await db_session.scalar(
        select(WhatsAppConnection).where(WhatsAppConnection.id == read_model.id)
    )
    assert stored.app_secret_encrypted is not None
    assert stored.app_secret_encrypted != plaintext_secret
    cipher = WhatsAppSecretCipher()
    assert cipher.decrypt(stored.app_secret_encrypted) == plaintext_secret
