"""Endpoint POST /api/v1/webhooks/asaas (F1 — Tarefa C).

Cobre o contrato do recebimento do webhook, isolado do processamento
assíncrono (a task Celery em si é coberta por tests/workers/test_finance_tasks.py):
  - token `asaas-access-token` inválido/ausente -> 401, nenhum recibo gravado;
  - evento novo/desconhecido -> 200 + recibo gravado;
  - evento duplicado (mesmo event_id) -> no-op, sem reagendar processamento.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.asaas_webhook_receipt import AsaasWebhookReceipt, AsaasWebhookReceiptStatus
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.services.integration_secrets import protect_integration_settings

ASAAS_WEBHOOK_URL = "/api/v1/webhooks/asaas"
TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_INTEGRATION_SECRET_KEY)


@pytest.fixture(autouse=True)
def _isolate_webhook_dispatch_from_external_broker(monkeypatch):
    """Valida o enqueue sem exigir um broker Celery real nos testes HTTP."""
    queued: list[dict] = []
    monkeypatch.setattr(
        "app.workers.finance_tasks.process_asaas_webhook_receipt.apply_async",
        lambda *args, **kwargs: queued.append(kwargs),
    )
    return queued


async def _make_asaas_connection(db_session, workspace_id, *, webhook_token: str):
    connection = IntegrationConnection(
        workspace_id=workspace_id,
        provider=IntegrationProvider.asaas,
        status=IntegrationStatus.connected,
        label=f"Asaas {uuid.uuid4().hex[:6]}",
        settings=protect_integration_settings({"webhookSecret": webhook_token}),
    )
    db_session.add(connection)
    await db_session.flush()
    await db_session.commit()
    return connection


def _event_body(event_id: str, event_type: str, payment_id: str = "pay_123") -> dict:
    return {
        "id": event_id,
        "event": event_type,
        "dateCreated": "2026-07-24 10:00:00",
        "payment": {
            "id": payment_id,
            "status": "PENDING",
            "value": 100.0,
        },
    }


@pytest.mark.asyncio
async def test_invalid_token_returns_401_and_writes_no_receipt(client, db_session, auth_context):
    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token="correct-token")

    response = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt-1", "PAYMENT_CREATED"),
        headers={"asaas-access-token": "wrong-token"},
    )

    assert response.status_code == 401
    receipts = (await db_session.execute(select(AsaasWebhookReceipt))).scalars().all()
    assert receipts == []


@pytest.mark.asyncio
async def test_missing_token_returns_401_and_writes_no_receipt(client, db_session, auth_context):
    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token="correct-token")

    response = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt-2", "PAYMENT_CREATED"),
    )

    assert response.status_code == 401
    receipts = (await db_session.execute(select(AsaasWebhookReceipt))).scalars().all()
    assert receipts == []


@pytest.mark.asyncio
async def test_unknown_event_type_is_accepted_and_stored(
    client, db_session, auth_context, _isolate_webhook_dispatch_from_external_broker
):
    token = "workspace-webhook-token"
    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token=token)

    response = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt-unknown-1", "PAYMENT_SOME_FUTURE_EVENT_TYPE"),
        headers={"asaas-access-token": token},
    )

    assert response.status_code == 200
    receipts = (await db_session.execute(select(AsaasWebhookReceipt))).scalars().all()
    assert len(receipts) == 1
    assert receipts[0].event_type == "PAYMENT_SOME_FUTURE_EVENT_TYPE"
    assert receipts[0].workspace_id == auth_context.workspace_id
    assert len(_isolate_webhook_dispatch_from_external_broker) == 1


@pytest.mark.asyncio
async def test_duplicate_event_id_is_a_no_op(
    client, db_session, auth_context, _isolate_webhook_dispatch_from_external_broker
):
    token = "workspace-webhook-token"
    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token=token)
    body = _event_body("evt-dup-1", "PAYMENT_CREATED")

    first = await client.post(
        ASAAS_WEBHOOK_URL, json=body, headers={"asaas-access-token": token}
    )
    second = await client.post(
        ASAAS_WEBHOOK_URL, json=body, headers={"asaas-access-token": token}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    receipts = (await db_session.execute(select(AsaasWebhookReceipt))).scalars().all()
    assert len(receipts) == 1
    # Recibo ainda NÃO processado: o replay re-agenda (é o veículo de retry
    # quando o enfileiramento original se perdeu) — baixa nunca morre em silêncio.
    assert len(_isolate_webhook_dispatch_from_external_broker) == 2


@pytest.mark.asyncio
async def test_duplicate_of_processed_receipt_is_noop(
    client, db_session, auth_context, _isolate_webhook_dispatch_from_external_broker
):
    """Replay de recibo JÁ processado é no-op de verdade (não re-agenda)."""
    token = "workspace-webhook-token-proc"
    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token=token)
    body = _event_body("evt-dup-proc-1", "PAYMENT_CREATED")

    first = await client.post(
        ASAAS_WEBHOOK_URL, json=body, headers={"asaas-access-token": token}
    )
    assert first.status_code == 200

    receipt = (await db_session.execute(select(AsaasWebhookReceipt))).scalars().one()
    receipt.status = AsaasWebhookReceiptStatus.processado
    await db_session.commit()

    second = await client.post(
        ASAAS_WEBHOOK_URL, json=body, headers={"asaas-access-token": token}
    )
    assert second.status_code == 200
    assert len(_isolate_webhook_dispatch_from_external_broker) == 1


@pytest.mark.asyncio
async def test_enqueue_failure_returns_503_and_replay_recovers(
    client, db_session, auth_context, monkeypatch
):
    """Broker fora do ar: 503 (Asaas reentrega); na reentrega, re-agenda."""
    token = "workspace-webhook-token-broker"
    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token=token)
    body = _event_body("evt-broker-down-1", "PAYMENT_CREATED")

    def _broker_down(*args, **kwargs):
        raise RuntimeError("broker indisponível")

    monkeypatch.setattr(
        "app.workers.finance_tasks.process_asaas_webhook_receipt.apply_async",
        _broker_down,
    )
    first = await client.post(
        ASAAS_WEBHOOK_URL, json=body, headers={"asaas-access-token": token}
    )
    assert first.status_code == 503
    # O recibo é durável mesmo com a falha de agendamento.
    receipts = (await db_session.execute(select(AsaasWebhookReceipt))).scalars().all()
    assert len(receipts) == 1

    queued: list[dict] = []
    monkeypatch.setattr(
        "app.workers.finance_tasks.process_asaas_webhook_receipt.apply_async",
        lambda *args, **kwargs: queued.append(kwargs),
    )
    retry = await client.post(
        ASAAS_WEBHOOK_URL, json=body, headers={"asaas-access-token": token}
    )
    assert retry.status_code == 200
    assert len(queued) == 1


@pytest.mark.asyncio
async def test_resolves_correct_workspace_among_multiple_asaas_connections(
    client, db_session, auth_context
):
    """Um token só autentica o webhook do workspace dono daquele token."""
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    other_workspace = Workspace(
        name="Outro escritório",
        slug=f"outro-{uuid.uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token="token-a")
    await _make_asaas_connection(db_session, other_workspace.id, webhook_token="token-b")

    response = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt-multi-1", "PAYMENT_CREATED"),
        headers={"asaas-access-token": "token-b"},
    )

    assert response.status_code == 200
    receipts = (await db_session.execute(select(AsaasWebhookReceipt))).scalars().all()
    assert len(receipts) == 1
    assert receipts[0].workspace_id == other_workspace.id


# --- S7: resolução do workspace pelo digest, sob RLS ----------------------


@pytest.mark.asyncio
async def test_protect_grava_digest_deterministico_e_redact_o_esconde():
    from app.services.integration_secrets import (
        WEBHOOK_SECRET_DIGEST_KEY,
        redact_integration_settings,
        webhook_secret_digest,
    )

    protegido = protect_integration_settings({"webhookSecret": "tok-1"})
    assert protegido[WEBHOOK_SECRET_DIGEST_KEY] == webhook_secret_digest("tok-1")
    assert "webhookSecret" not in protegido and "webhookSecretEncrypted" in protegido
    # digest avulso vindo do cliente é descartado; com o cifrado junto, é mantido
    assert WEBHOOK_SECRET_DIGEST_KEY not in protect_integration_settings(
        {WEBHOOK_SECRET_DIGEST_KEY: "forjado"}
    )
    assert protect_integration_settings({**protegido})[WEBHOOK_SECRET_DIGEST_KEY] == (
        protegido[WEBHOOK_SECRET_DIGEST_KEY]
    )
    exposto = redact_integration_settings(protegido)
    assert exposto == {"webhookSecretConfigured": True}


@pytest.mark.asyncio
async def test_mesmo_token_em_dois_workspaces_responde_401_fail_closed(
    client, db_session, auth_context
):
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    outro = Workspace(
        name="Outro escritório",
        slug=f"outro-{uuid.uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(outro)
    await db_session.flush()
    token = "token-repetido"
    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token=token)
    await _make_asaas_connection(db_session, outro.id, webhook_token=token)

    response = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt_amb", "PAYMENT_RECEIVED"),
        headers={"asaas-access-token": token},
    )
    assert response.status_code == 401
    receipts = (await db_session.execute(select(AsaasWebhookReceipt))).scalars().all()
    assert receipts == []


@pytest.mark.asyncio
async def test_digest_sem_segredo_correspondente_responde_401(client, db_session, auth_context):
    from app.services.integration_secrets import WEBHOOK_SECRET_DIGEST_KEY, webhook_secret_digest

    connection = IntegrationConnection(
        workspace_id=auth_context.workspace_id,
        provider=IntegrationProvider.asaas,
        status=IntegrationStatus.connected,
        label="Asaas",
        settings={WEBHOOK_SECRET_DIGEST_KEY: webhook_secret_digest("tok-orfao")},
    )
    db_session.add(connection)
    await db_session.commit()

    response = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt_orfao", "PAYMENT_RECEIVED"),
        headers={"asaas-access-token": "tok-orfao"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_enfileira_com_workspace_e_a_task_processa_sob_contexto(
    client, db_session, auth_context, _isolate_webhook_dispatch_from_external_broker
):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.workers.finance_tasks import _async_process_asaas_webhook_receipt

    token = f"tok-{uuid.uuid4().hex}"
    await _make_asaas_connection(db_session, auth_context.workspace_id, webhook_token=token)
    response = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt_ctx", "PAYMENT_SOME_FUTURE_EVENT_TYPE"),
        headers={"asaas-access-token": token},
    )
    assert response.status_code == 200
    (enfileirado,) = _isolate_webhook_dispatch_from_external_broker
    assert enfileirado["kwargs"]["workspace_id"] == str(auth_context.workspace_id)

    fabrica = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
    await _async_process_asaas_webhook_receipt(
        enfileirado["kwargs"]["receipt_id"],
        workspace_id=enfileirado["kwargs"]["workspace_id"],
        session_factory=fabrica,
    )
    receipt = await db_session.get(
        AsaasWebhookReceipt, uuid.UUID(enfileirado["kwargs"]["receipt_id"])
    )
    await db_session.refresh(receipt)
    assert receipt.status == AsaasWebhookReceiptStatus.processado


@pytest.mark.asyncio
async def test_abrir_a_tela_de_integracoes_cura_conexao_antiga_sem_digest(
    client, db_session, auth_context
):
    """Conexão criada antes do digest não é localizável pelo resolver (401 até
    recadastrar). Listar as integrações do workspace grava o digest a partir
    do cifrado, sob o contexto do tenant."""
    from app.services.finance_service import FinanceService
    from app.services.integration_secrets import (
        WEBHOOK_SECRET_DIGEST_KEY,
        IntegrationSecretCipher,
        webhook_secret_digest,
    )

    antiga = IntegrationConnection(
        workspace_id=auth_context.workspace_id,
        provider=IntegrationProvider.asaas,
        status=IntegrationStatus.connected,
        label="Asaas antiga",
        settings={"webhookSecretEncrypted": IntegrationSecretCipher().encrypt("tok-antigo")},
    )
    db_session.add(antiga)
    await db_session.commit()

    antes = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt_antigo_1", "PAYMENT_RECEIVED"),
        headers={"asaas-access-token": "tok-antigo"},
    )
    assert antes.status_code == 401

    listadas = await FinanceService(db_session).list_integrations(auth_context.workspace_id)
    await db_session.commit()
    curada = next(c for c in listadas if c.label == "Asaas antiga")
    assert curada.settings[WEBHOOK_SECRET_DIGEST_KEY] == webhook_secret_digest("tok-antigo")

    depois = await client.post(
        ASAAS_WEBHOOK_URL,
        json=_event_body("evt_antigo_2", "PAYMENT_RECEIVED"),
        headers={"asaas-access-token": "tok-antigo"},
    )
    assert depois.status_code == 200
