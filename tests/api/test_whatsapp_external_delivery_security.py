from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.db.tenant import get_workspace_context
from app.models.omnichannel_v6 import (
    OmnichannelProviderWebhookEnvelope,
    OmnichannelProviderWebhookProcessingState,
)
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from tests.conftest import TestSessionLocal


@pytest.fixture(autouse=True)
def _isolate_webhook_dispatch_from_external_broker(monkeypatch):
    """Mantém o broker real fora do teste sem alterar o comportamento produtivo."""
    queued: list[dict] = []
    monkeypatch.setattr(
        "app.workers.whatsapp_webhook_tasks.process_inbound_webhook.apply_async",
        lambda *args, **kwargs: queued.append(kwargs),
    )
    return queued


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_secret", ["", "   "])
async def test_whatsapp_post_webhook_is_unavailable_without_app_secret(
    client,
    monkeypatch,
    missing_secret,
):
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", missing_secret)

    response = await client.post(
        "/api/v1/whatsapp/webhook",
        json={"object": "whatsapp_business_account", "entry": []},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "WA-005"


@pytest.mark.asyncio
async def test_whatsapp_post_webhook_rejects_invalid_signature(client, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "webhook-secret")

    response = await client.post(
        "/api/v1/whatsapp/webhook",
        json={"object": "whatsapp_business_account", "entry": []},
        headers={"X-Hub-Signature-256": "sha256=invalid"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WA-004"


@pytest.mark.asyncio
async def test_whatsapp_post_webhook_accepts_valid_signature(
    client,
    db_session,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    secret = "webhook-secret"
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(_isolate_webhook_dispatch_from_external_broker) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(OmnichannelProviderWebhookEnvelope)
    ) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(OmnichannelProviderWebhookProcessingState)
    ) == 1


@pytest.mark.asyncio
async def test_exact_signed_webhook_redelivery_keeps_one_durable_envelope_and_state(
    client,
    db_session,
    monkeypatch,
    _isolate_webhook_dispatch_from_external_broker,
):
    secret = "webhook-secret"
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
    }

    first = await client.post("/api/v1/whatsapp/webhook", content=body, headers=headers)
    replay = await client.post("/api/v1/whatsapp/webhook", content=body, headers=headers)

    assert first.status_code == replay.status_code == 200
    assert len(_isolate_webhook_dispatch_from_external_broker) == 2
    queued_envelope_ids = {
        queued["kwargs"]["envelope_id"]
        for queued in _isolate_webhook_dispatch_from_external_broker
    }
    assert len(queued_envelope_ids) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(OmnichannelProviderWebhookEnvelope)
    ) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(OmnichannelProviderWebhookProcessingState)
    ) == 1


@pytest.mark.asyncio
async def test_webhook_broker_failure_keeps_queued_state_and_still_acknowledges_meta(
    client,
    db_session,
    monkeypatch,
):
    secret = "webhook-secret"
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

    def _broker_unavailable(*_args, **_kwargs):
        raise RuntimeError("broker password must not leak")

    monkeypatch.setattr(
        "app.workers.whatsapp_webhook_tasks.process_inbound_webhook.apply_async",
        _broker_unavailable,
    )

    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 200
    state = await db_session.scalar(select(OmnichannelProviderWebhookProcessingState))
    assert state is not None
    assert state.status == "queued"
    assert state.attempts == 0


@pytest.mark.asyncio
async def test_whatsapp_webhook_responds_200_immediately_and_never_5xx_for_meta(
    client,
    monkeypatch,
):
    """W1.8: o POST responde 200 assim que o envelope é persistido; falhas de
    processamento de negócio (agora assíncronas, via Celery) nunca chegam a
    virar 5xx para a Meta — elas só afetam o retry interno da task."""
    secret = "webhook-secret"
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

    queued: list[dict] = []
    monkeypatch.setattr(
        "app.workers.whatsapp_webhook_tasks.process_inbound_webhook.apply_async",
        lambda *args, **kwargs: queued.append(kwargs),
    )

    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(queued) == 1
    assert "envelope_id" in queued[0]["kwargs"]


@pytest.mark.asyncio
async def test_whatsapp_webhook_rejects_non_object_payload_before_enqueue(
    client,
    monkeypatch,
):
    secret = "webhook-secret"
    body = b"[1, 2, 3]"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

    queued: list[dict] = []
    monkeypatch.setattr(
        "app.workers.whatsapp_webhook_tasks.process_inbound_webhook.apply_async",
        lambda *args, **kwargs: queued.append(kwargs),
    )

    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "WA-009"
    assert queued == []


@pytest.mark.asyncio
async def test_whatsapp_webhook_routes_phone_number_to_rls_workspace(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    """A resolução do tenant continua correta; agora ocorre dentro da task
    Celery (aqui executada inline via monkeypatch) em vez do request."""
    secret = "webhook-secret"
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    phone_number_id = f"pn-{uuid4().hex}"
    db_session.add(
        WhatsAppConnection(
            workspace_id=auth_context.workspace_id,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
            is_default=True,
            phone_number_id=phone_number_id,
            status=WaConnectionStatus.connected,
        )
    )
    await db_session.commit()
    body = (
        '{"object":"whatsapp_business_account","entry":[{"changes":['
        '{"field":"messages","value":{"metadata":{"phone_number_id":"'
        + phone_number_id
        + '"},"messages":[],"statuses":[]}}]}]}'
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

    captured: dict = {}

    class _DbSessionContext:
        """Adapter so the task's `async with session_maker() as db` reuses
        the same request-scoped `db_session` (single sqlite connection)."""

        def __init__(self, session):
            self._session = session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *_exc):
            return None

    def _run_task_inline(*_args, **kwargs):
        # apply_async runs synchronously in tests (no real broker); the
        # coroutine is scheduled on the SAME running loop as the request.
        from app.workers import whatsapp_webhook_tasks

        envelope_id = kwargs["kwargs"]["envelope_id"]
        captured["coro"] = whatsapp_webhook_tasks._async_process_inbound_webhook(
            envelope_id,
            session_factory=lambda: _DbSessionContext(db_session),
        )
        captured["envelope_id"] = envelope_id

    monkeypatch.setattr(
        "app.workers.whatsapp_webhook_tasks.process_inbound_webhook.apply_async",
        _run_task_inline,
    )

    response = await client.post(
        "/api/v1/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 200
    assert "envelope_id" in captured
    # Simula o worker Celery consumindo a task depois que o request já
    # respondeu 200 para a Meta.
    await captured["coro"]
    assert get_workspace_context(db_session) == auth_context.workspace_id


@pytest.mark.asyncio
async def test_whatsapp_get_webhook_keeps_meta_verification_contract(client, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "verify-me")
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")

    response = await client.get(
        "/api/v1/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "321",
        },
    )

    assert response.status_code == 200
    assert response.json() == 321


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_token", ["", "   "])
async def test_whatsapp_get_webhook_fails_closed_without_verify_token(
    client,
    monkeypatch,
    missing_token,
):
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", missing_token)

    response = await client.get(
        "/api/v1/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "",
            "hub.challenge": "321",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WA-004"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_message_send_requires_rbac_before_service_call(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.viewer
    auth_context.role = WorkspaceRole.viewer
    await db_session.flush()

    async def forbidden_send(*args, **kwargs):
        raise AssertionError("service must not run without mensagens:send_message")

    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.send_message",
        forbidden_send,
    )

    response = await client.post(
        "/api/v1/whatsapp/messages",
        json={"contactId": str(uuid4()), "body": "Nao autorizado"},
        headers={"Idempotency-Key": "test-message-rbac-denied"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_start_conversation_requires_external_delivery_gate_before_service_call(
    client,
    monkeypatch,
):
    async def forbidden_start(*args, **kwargs):
        raise AssertionError("service must not run while external delivery is closed")

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.start_conversation",
        forbidden_start,
    )

    response = await client.post(
        "/api/v1/whatsapp/conversations/start",
        json={
            "connectionId": str(uuid4()),
            "metaTemplateId": str(uuid4()),
            "phone": "5561999999999",
        },
        headers={"Idempotency-Key": "start-conversation-gate-closed"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_start_conversation_requires_send_permission_before_service_call(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.viewer
    auth_context.role = WorkspaceRole.viewer
    await db_session.flush()

    async def forbidden_start(*args, **kwargs):
        raise AssertionError("service must not run without mensagens:send_message")

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.start_conversation",
        forbidden_start,
    )

    response = await client.post(
        "/api/v1/whatsapp/conversations/start",
        json={
            "connectionId": str(uuid4()),
            "metaTemplateId": str(uuid4()),
            "phone": "5561999999999",
        },
        headers={"Idempotency-Key": "start-conversation-rbac-denied"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_connection_setup_requires_integration_management_permission(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    async def forbidden_connection(*args, **kwargs):
        raise AssertionError("provider setup must not run without integrations:manage")

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.create_manual_connection",
        forbidden_connection,
    )

    response = await client.post(
        "/api/v1/whatsapp/connections/manual",
        json={
            "accessToken": "test-token",
            "phoneNumberId": "pn-rbac",
            "businessAccountId": "waba-rbac",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-012"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_composite_contract_send_is_blocked_before_zapsign_or_post_send_changes(
    client,
    monkeypatch,
):
    async def forbidden_contract(*args, **kwargs):
        raise AssertionError("composite send must not run while delivery is blocked")

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.send_contract_for_conversation",
        forbidden_contract,
    )

    response = await client.post(
        f"/api/v1/whatsapp/conversations/{uuid4()}/send-contract",
        json={"templateId": "contrato-padrao"},
        headers={"Idempotency-Key": "test-contract-blocked-by-gate"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KLM-EXT-001"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_contract_send_requires_human_approval_permission(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    async def forbidden_contract(*args, **kwargs):
        raise AssertionError("contract service must not run without approval permission")

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService.send_contract_for_conversation",
        forbidden_contract,
    )

    response = await client.post(
        f"/api/v1/whatsapp/conversations/{uuid4()}/send-contract",
        json={"templateId": "contrato-padrao"},
        headers={"Idempotency-Key": "test-contract-rbac-denied"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AUTH-012"


# --- V22: quem envia mensagens lê conexões e a sonda de gate ---------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_operator_can_list_connections_and_probe_the_gate(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "verify-secret-123")
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: True,
    )
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)

    # Fable (lote 3): quem tem integrations:view continua vendo o token de verificação.
    as_owner = await client.get("/api/v1/whatsapp/status")
    assert as_owner.status_code == 200, as_owner.text
    assert as_owner.json()["verifyToken"] == "verify-secret-123"

    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    listed = await client.get("/api/v1/whatsapp/connections")
    assert listed.status_code == 200, listed.text

    # O operador sonda o gate, mas não recebe segredo de plataforma.
    as_operator = await client.get("/api/v1/whatsapp/status")
    assert as_operator.status_code == 200, as_operator.text
    assert as_operator.json()["verifyToken"] == ""
    assert as_operator.json()["webhookUrl"] == ""

    # Gate fechado: a sonda responde 503 KLM-EXT-001 (não 403 de permissão).
    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: False,
    )
    probe = await client.get("/api/v1/whatsapp/status")
    assert probe.status_code == 503, probe.text
    assert probe.json()["detail"]["code"] == "KLM-EXT-001"

    # Negação individual de mensagens:send_message fecha as duas leituras para o operador.
    membership.permission_denials = [
        {"module": "mensagens", "action": "send_message", "scope": "workspace"}
    ]
    await db_session.flush()
    denied = await client.get("/api/v1/whatsapp/connections")
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "AUTH-012"
    denied_probe = await client.get("/api/v1/whatsapp/status")
    assert denied_probe.status_code == 403
    assert denied_probe.json()["detail"]["code"] == "AUTH-012"
