from __future__ import annotations

import httpx
import pytest

import app.services.whatsapp.meta_cloud_client as meta_module
import app.services.whatsapp.qr_gateway_client as qr_module
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.qr_gateway_client import QrGatewayClient


class _AsyncClientStub:
    def __init__(self, *, response: httpx.Response | None = None, error=None) -> None:
        self.response = response
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_uncertain", "connection_unhealthy"),
    [(400, False, False), (401, False, True), (500, True, False)],
)
async def test_meta_http_outcome_classification(
    monkeypatch,
    status_code: int,
    expected_uncertain: bool,
    connection_unhealthy: bool,
):
    monkeypatch.setattr(meta_module, "external_delivery_allowed", lambda: True)
    response = httpx.Response(
        status_code,
        json={"error": {"message": "provider response", "code": 10}},
    )
    monkeypatch.setattr(
        meta_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _AsyncClientStub(response=response),
    )

    with pytest.raises(WhatsAppProviderError) as captured:
        await MetaCloudClient()._request("POST", "/phone/messages", json={})

    assert captured.value.request_started is True
    assert captured.value.delivery_uncertain is expected_uncertain
    assert captured.value.connection_unhealthy is connection_unhealthy


@pytest.mark.asyncio
async def test_meta_oauth_error_on_http_400_is_connection_scoped(monkeypatch):
    monkeypatch.setattr(meta_module, "external_delivery_allowed", lambda: True)
    response = httpx.Response(
        400,
        json={
            "error": {
                "message": "access token expired",
                "code": "190",
                "type": "OAuthException",
            }
        },
    )
    monkeypatch.setattr(
        meta_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _AsyncClientStub(response=response),
    )

    with pytest.raises(WhatsAppProviderError) as captured:
        await MetaCloudClient()._request("POST", "/phone/messages", json={})

    assert captured.value.delivery_uncertain is False
    assert captured.value.connection_unhealthy is True
    assert captured.value.provider_status_code == 190


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "request_started", "uncertain"),
    [
        (httpx.ConnectError, False, False),
        (httpx.ReadTimeout, True, True),
    ],
)
async def test_meta_transport_outcome_classification(
    monkeypatch,
    error_type,
    request_started: bool,
    uncertain: bool,
):
    monkeypatch.setattr(meta_module, "external_delivery_allowed", lambda: True)
    request = httpx.Request("POST", "https://graph.example.test/messages")
    transport_error = error_type("transport failure", request=request)
    monkeypatch.setattr(
        meta_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _AsyncClientStub(error=transport_error),
    )

    with pytest.raises(WhatsAppProviderError) as captured:
        await MetaCloudClient()._request("POST", "/phone/messages", json={})

    assert captured.value.request_started is request_started
    assert captured.value.delivery_uncertain is uncertain


@pytest.mark.asyncio
async def test_meta_request_scope_reuses_one_client_and_closes_it(monkeypatch):
    monkeypatch.setattr(meta_module, "external_delivery_allowed", lambda: True)

    class PooledClient:
        def __init__(self, **_kwargs):
            self.calls: list[str] = []
            self.closed = False

        async def request(self, _method, url, **_kwargs):
            self.calls.append(url)
            return httpx.Response(200, json={})

        async def aclose(self):
            self.closed = True

    instances: list[PooledClient] = []

    def client_factory(**kwargs):
        client = PooledClient(**kwargs)
        instances.append(client)
        return client

    monkeypatch.setattr(meta_module.httpx, "AsyncClient", client_factory)
    client = MetaCloudClient()

    async with client.request_scope():
        await client.get_phone_number_details("phone-1", "token")
        await client.subscribe_app("waba-1", "token")
        await client.register_phone_number("phone-1", "token", "123456")

    assert len(instances) == 1
    assert len(instances[0].calls) == 3
    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_meta_template_dynamic_buttons_use_one_component_per_button(monkeypatch):
    monkeypatch.setattr(meta_module, "external_delivery_allowed", lambda: True)
    client = MetaCloudClient()
    captured = {}

    async def capture(**kwargs):
        captured.update(kwargs)
        return {"messages": [{"id": "wamid-test"}]}

    monkeypatch.setattr(client, "send_message_payload", capture)
    await client.send_template_message(
        phone_number_id="phone",
        access_token="token",
        to="5511999999999",
        template_name="two_links",
        language_code="pt_BR",
        button_parameters=["suffix-a", "suffix-b"],
    )

    components = captured["message"]["template"]["components"]
    assert [item["index"] for item in components] == ["0", "1"]
    assert [item["parameters"][0]["text"] for item in components] == [
        "suffix-a",
        "suffix-b",
    ]


@pytest.mark.asyncio
async def test_meta_template_dynamic_button_preserves_real_button_array_index(monkeypatch):
    monkeypatch.setattr(meta_module, "external_delivery_allowed", lambda: True)
    client = MetaCloudClient()
    captured = {}

    async def capture(**kwargs):
        captured.update(kwargs)
        return {"messages": [{"id": "wamid-test"}]}

    monkeypatch.setattr(client, "send_message_payload", capture)
    await client.send_template_message(
        phone_number_id="phone",
        access_token="token",
        to="5511999999999",
        template_name="mixed_buttons",
        language_code="pt_BR",
        button_parameters=[{"index": 1, "value": "suffix-url"}],
    )

    components = captured["message"]["template"]["components"]
    assert [(item["type"], item["index"]) for item in components] == [("button", "1")]


@pytest.mark.asyncio
async def test_qr_server_error_is_delivery_uncertain(monkeypatch):
    monkeypatch.setattr(qr_module, "external_delivery_allowed", lambda: True)
    response = httpx.Response(503, text="gateway unavailable")
    monkeypatch.setattr(
        qr_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _AsyncClientStub(response=response),
    )
    client = QrGatewayClient()
    client._base_url = "https://qr.example.test"

    with pytest.raises(WhatsAppProviderError) as captured:
        await client._request("POST", "/instances/1/messages", json={})

    assert captured.value.request_started is True
    assert captured.value.delivery_uncertain is True
