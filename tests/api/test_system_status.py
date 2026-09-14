from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import status as status_endpoint
from app.models.whatsapp import (
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)


@pytest.mark.asyncio
async def test_whatsapp_status_uses_embedded_signup_platform_configuration(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.db.session.AsyncSessionLocal",
        lambda: _AsyncSessionContext(db_session),
    )
    monkeypatch.setattr(status_endpoint.settings, "META_APP_ID", "app-id")
    monkeypatch.setattr(status_endpoint.settings, "META_CONFIG_ID", "config-id")
    monkeypatch.setattr(status_endpoint.settings, "WHATSAPP_APP_SECRET", "app-secret")
    monkeypatch.setattr(status_endpoint.settings, "WHATSAPP_VERIFY_TOKEN", "verify-token")
    monkeypatch.setattr(
        status_endpoint.settings,
        "WHATSAPP_SECRET_ENCRYPTION_KEYS",
        "encryption-key",
    )

    result = await status_endpoint._inspect_whatsapp()

    assert result == {
        "status": "prepared",
        "note": "Plataforma preparada; nenhum número operacional conectado",
    }


class _AsyncSessionContext:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args) -> None:
        return None


@pytest.mark.asyncio
async def test_whatsapp_status_requires_real_connected_number_for_operational(
    db_session,
    auth_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        status=WaConnectionStatus.connected,
        phone_number_id="phone-status-operational",
        access_token_encrypted="encrypted-token",
    )
    db_session.add(connection)
    await db_session.flush()
    monkeypatch.setattr(
        "app.db.session.AsyncSessionLocal",
        lambda: _AsyncSessionContext(db_session),
    )
    tenant_context = AsyncMock()
    monkeypatch.setattr("app.db.tenant.set_workspace_context", tenant_context)

    result = await status_endpoint._inspect_whatsapp()

    assert result == {
        "status": "connected_unverified",
        "note": "Número cadastrado localmente; funcionamento externo não confirmado",
    }
    tenant_context.assert_awaited_with(db_session, auth_context.workspace_id)

    active_connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        status=WaConnectionStatus.connected,
        phone_number_id="phone-status-confirmed",
        access_token_encrypted="encrypted-token-confirmed",
    )
    db_session.add(active_connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=active_connection.id,
        phone="5511999999999",
    )
    db_session.add(contact)
    await db_session.flush()
    db_session.add(
        WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=active_connection.id,
            wamid="wamid.status-confirmed",
            direction=WaMessageDirection.outbound,
            status=WaMessageStatus.sent,
            body="Teste operacional",
            timestamp=status_endpoint.datetime.now(status_endpoint.UTC),
        )
    )
    await db_session.flush()

    confirmed = await status_endpoint._inspect_whatsapp()

    assert confirmed == {
        "status": "operational",
        "note": "A Meta confirmou atividade de envio nas últimas 24 horas",
    }


def test_prepared_component_keeps_overall_status_partial() -> None:
    components = {
        "database": {"status": "operational"},
        "whatsapp_api": {"status": "prepared"},
    }

    assert status_endpoint._overall_status(components) == "partial"


@pytest.mark.asyncio
async def test_whatsapp_status_check_is_single_flight_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspect = AsyncMock(
        return_value={"status": "prepared", "note": "Sem conexão operacional"}
    )
    monkeypatch.setattr(status_endpoint, "_inspect_whatsapp", inspect)
    monkeypatch.setattr(status_endpoint, "cache_get", AsyncMock(return_value=None))
    cache_set = AsyncMock()
    monkeypatch.setattr(status_endpoint, "cache_set", cache_set)
    monkeypatch.setattr(status_endpoint, "_whatsapp_status_cache", None)
    monkeypatch.setattr(status_endpoint, "_whatsapp_status_task", None)
    monkeypatch.setattr(status_endpoint, "_whatsapp_status_task_loop", None)

    results = await asyncio.gather(
        status_endpoint.check_whatsapp(),
        status_endpoint.check_whatsapp(),
    )

    assert results == [
        {"status": "prepared", "note": "Sem conexão operacional"},
        {"status": "prepared", "note": "Sem conexão operacional"},
    ]
    inspect.assert_awaited_once()
    cache_set.assert_awaited_once()


@pytest.mark.asyncio
async def test_celery_status_check_is_single_flight_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspect = AsyncMock(
        return_value={"status": "operational", "workers": 2}
    )
    monkeypatch.setattr(status_endpoint, "_inspect_celery", inspect)
    monkeypatch.setattr(status_endpoint, "_celery_status_cache", None)
    monkeypatch.setattr(status_endpoint, "_celery_status_task", None)
    monkeypatch.setattr(status_endpoint, "_celery_status_task_loop", None)
    monkeypatch.setattr(status_endpoint, "cache_get", AsyncMock(return_value=None))
    cache_set = AsyncMock()
    monkeypatch.setattr(status_endpoint, "cache_set", cache_set)

    concurrent = await asyncio.gather(
        *(status_endpoint.check_celery() for _ in range(20))
    )
    cached = await status_endpoint.check_celery()

    assert concurrent == [{"status": "operational", "workers": 2}] * 20
    assert cached == {"status": "operational", "workers": 2}
    assert inspect.await_count == 1
    cache_set.assert_awaited_once_with(
        status_endpoint.CELERY_STATUS_CACHE_KEY,
        {"status": "operational", "workers": 2},
        ttl_seconds=10,
    )


@pytest.mark.asyncio
async def test_celery_status_uses_shared_cache_before_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = {"status": "operational", "workers": 1}
    inspect = AsyncMock()
    monkeypatch.setattr(status_endpoint, "_inspect_celery", inspect)
    monkeypatch.setattr(status_endpoint, "_celery_status_cache", None)
    monkeypatch.setattr(status_endpoint, "cache_get", AsyncMock(return_value=shared))

    result = await status_endpoint.check_celery()

    assert result == shared
    inspect.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_status_persists_real_sample_and_history_has_no_synthetic_claims(
    unauthenticated_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status_endpoint, "_status_observation_next_at", 0.0)
    checks = {
        "check_database": {"status": "operational", "latency_ms": 3.2},
        "check_redis": {"status": "operational", "latency_ms": 1.4},
        "check_celery": {"status": "operational", "workers": 2},
        "check_whatsapp": {"status": "not_configured", "note": "sem credencial"},
        "check_zapsign": {"status": "not_configured", "note": "sem credencial"},
    }
    for name, result in checks.items():
        monkeypatch.setattr(status_endpoint, name, AsyncMock(return_value=result))

    current = await unauthenticated_client.get("/api/v1/status")
    assert current.status_code == 200
    payload = current.json()
    assert payload["status"] == "partial"
    assert payload["historyRecorded"] is True
    assert payload["components"]["database"]["latency_ms"] == 3.2

    repeated = await unauthenticated_client.get("/api/v1/status")
    assert repeated.status_code == 200
    assert repeated.json()["historyRecorded"] is False

    history = await unauthenticated_client.get("/api/v1/status/history")
    assert history.status_code == 200
    history_payload = history.json()
    assert history_payload["state"] == "ready"
    assert history_payload["observations"] == 1
    assert history_payload["uptimePercent"] == 100.0
    assert len(history_payload["daily"]) == 30
    assert "uptime_30d" not in history_payload
    assert "last_incident" not in history_payload


def test_status_observation_reservation_is_single_flight_and_time_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status_endpoint, "_status_observation_next_at", 0.0)

    assert status_endpoint._reserve_status_observation(now=100.0) is True
    assert status_endpoint._reserve_status_observation(now=100.0) is False
    assert status_endpoint._reserve_status_observation(now=399.999) is False
    assert status_endpoint._reserve_status_observation(now=400.0) is True

    status_endpoint._release_status_observation_reservation()
    assert status_endpoint._reserve_status_observation(now=101.0) is True


# --- V3: a frase de "partial" nasce dos componentes que a causaram ---------


def test_partial_message_distinguishes_not_configured_from_unverified() -> None:
    so_nao_verificado = status_endpoint._partial_message(
        {"database": {"status": "operational"}, "whatsapp_api": {"status": "prepared"}}
    )
    assert "ainda não verificadas" in so_nao_verificado
    assert "não configuradas" not in so_nao_verificado

    so_nao_configurado = status_endpoint._partial_message(
        {"database": {"status": "operational"}, "zapsign_api": {"status": "not_configured"}}
    )
    assert "não configuradas" in so_nao_configurado
    assert "ZapSign" in so_nao_configurado and "zapsign_api" not in so_nao_configurado
    assert "não verificadas" not in so_nao_configurado

    ambos = status_endpoint._partial_message(
        {
            "whatsapp_api": {"status": "configured_unverified"},
            "zapsign_api": {"status": "not_configured"},
        }
    )
    assert "não configuradas" in ambos and "não verificadas" in ambos

    worker_local = status_endpoint._partial_message(
        {"database": {"status": "operational"}, "workers": {"status": "not_configured"}}
    )
    assert "worker distribuído" in worker_local
    assert "integrações externas" not in worker_local


@pytest.mark.asyncio
async def test_public_status_message_names_the_partial_cause(
    unauthenticated_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status_endpoint, "_status_observation_next_at", 0.0)
    checks = {
        "check_database": {"status": "operational", "latency_ms": 3.2},
        "check_redis": {"status": "operational", "latency_ms": 1.4},
        "check_celery": {"status": "operational", "workers": 2},
        "check_whatsapp": {"status": "prepared", "note": "Número cadastrado localmente"},
        "check_zapsign": {"status": "operational", "note": "Credencial global configurada"},
    }
    for name, result in checks.items():
        monkeypatch.setattr(status_endpoint, name, AsyncMock(return_value=result))

    payload = (await unauthenticated_client.get("/api/v1/status")).json()
    assert payload["status"] == "partial"
    assert "ainda não verificadas" in payload["message"]
    assert "não configuradas" not in payload["message"]


@pytest.mark.asyncio
async def test_public_status_names_zapsign_when_it_is_the_missing_integration(
    unauthenticated_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fable (lote 3): o dicionário real usa a chave `zapsign_api`; com a chave errada
    # a frase perdia "não configuradas" justamente no cenário mais comum.
    monkeypatch.setattr(status_endpoint, "_status_observation_next_at", 0.0)
    checks = {
        "check_database": {"status": "operational", "latency_ms": 3.2},
        "check_redis": {"status": "operational", "latency_ms": 1.4},
        "check_celery": {"status": "operational", "workers": 2},
        "check_whatsapp": {"status": "operational", "note": "Conexão verificada"},
        "check_zapsign": {"status": "not_configured", "note": "Credencial externa não configurada"},
    }
    for name, result in checks.items():
        monkeypatch.setattr(status_endpoint, name, AsyncMock(return_value=result))

    payload = (await unauthenticated_client.get("/api/v1/status")).json()
    assert payload["status"] == "partial"
    assert "não configuradas: ZapSign" in payload["message"]
    assert "zapsign_api" not in payload["message"]
    assert "não verificadas" not in payload["message"]
