"""S16/S17: o cliente Meta retenta só a fase de conexão e preserva o status HTTP."""

from __future__ import annotations

import httpx
import pytest

import app.services.whatsapp.meta_cloud_client as meta_module
from app.services.whatsapp.errors import (
    MetaErrorDisposition,
    WhatsAppProviderError,
    classify_meta_error,
)
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.webhooks.subscription_service import _is_transient_provider_error


def _transport(monkeypatch, handler):
    calls: list[httpx.Request] = []

    def contado(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request, len(calls))

    real_client = httpx.AsyncClient  # capturado antes do patch (o módulo é compartilhado)
    monkeypatch.setattr(meta_module, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        meta_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(contado), **kwargs),
    )
    return calls


@pytest.mark.asyncio
async def test_connect_errors_are_retried_and_then_succeed(monkeypatch):
    def handler(request, n):
        if n < 3:
            raise httpx.ConnectError("Meta fora", request=request)
        return httpx.Response(200, json={"messages": [{"id": "wamid-1"}]})

    calls = _transport(monkeypatch, handler)
    payload = await MetaCloudClient()._request("POST", "/phone/messages", json={})
    assert payload["messages"][0]["id"] == "wamid-1"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_connect_errors_give_up_as_wa_connect_after_the_budget(monkeypatch):
    def handler(request, n):
        raise httpx.ConnectError("Meta fora", request=request)

    calls = _transport(monkeypatch, handler)
    with pytest.raises(WhatsAppProviderError) as captured:
        await MetaCloudClient()._request("POST", "/phone/messages", json={})
    assert captured.value.code == "WA-CONNECT"
    assert captured.value.request_started is False
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_5xx_is_not_retried_because_delivery_is_uncertain(monkeypatch):
    def handler(request, n):
        return httpx.Response(500, json={"error": {"message": "boom", "code": 1}})

    calls = _transport(monkeypatch, handler)
    with pytest.raises(WhatsAppProviderError) as captured:
        await MetaCloudClient()._request("POST", "/phone/messages", json={})
    assert len(calls) == 1
    assert captured.value.delivery_uncertain is True
    assert captured.value.http_status == 500


@pytest.mark.asyncio
async def test_429_is_not_retried_here_but_status_and_retry_after_survive(monkeypatch):
    def handler(request, n):
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"error": {"message": "throttled", "code": 80007}},
        )

    calls = _transport(monkeypatch, handler)
    with pytest.raises(WhatsAppProviderError) as captured:
        await MetaCloudClient()._request("POST", "/phone/messages", json={})
    assert len(calls) == 1
    assert captured.value.http_status == 429
    assert captured.value.retry_after == 7.0
    assert captured.value.provider_status_code == 80007
    assert _is_transient_provider_error(captured.value) is True


def test_meta_throttling_codes_defer_the_batch_instead_of_burning_attempts():
    for code in (130429, 80007, 4):
        assert classify_meta_error(code).disposition == MetaErrorDisposition.retry_batch


def test_retry_after_parsing_caps_and_tolerates_garbage():
    assert meta_module._parse_retry_after("3") == 3.0
    assert meta_module._parse_retry_after("9999") == 30.0
    assert meta_module._parse_retry_after("quando der") is None
    assert meta_module._parse_retry_after(None) is None
