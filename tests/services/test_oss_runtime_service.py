from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
import pytest

from app.services import oss_runtime_service
from app.services.oss_runtime_service import (
    _integration_state,
    _probe_bridge_auth,
    _probe_chatwoot,
    _probe_service,
    _probe_versioned,
    _require_authenticated_probe,
    _source_integrity,
    _version_matches,
    oss_runtime_status,
    reset_oss_runtime_status_cache,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_chatwoot_probe_requires_health_api_version_queue_and_data():
    async def healthy(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            200,
            json={
                "version": "4.15.1",
                "queue_services": "ok",
                "data_services": "ok",
            },
        )

    async with _client(healthy) as client:
        result = await _probe_chatwoot(client, "http://127.0.0.1:3100")
    assert result["state"] in {"ready", "degraded"}
    assert result["label"] != "fila local indisponível"
    assert result["label"] != "dados locais indisponíveis"

    for failed_key, expected_label in (
        ("queue_services", "fila local indisponível"),
        ("data_services", "dados locais indisponíveis"),
    ):

        async def unhealthy(request: httpx.Request, key: str = failed_key) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(
                200,
                json={
                    "version": "4.15.1",
                    "queue_services": "failing" if key == "queue_services" else "ok",
                    "data_services": "failing" if key == "data_services" else "ok",
                },
            )

        async with _client(unhealthy) as client:
            result = await _probe_chatwoot(client, "http://127.0.0.1:3100")
        assert result == {"state": "degraded", "label": expected_label, "auth": "unknown"}


@pytest.mark.asyncio
async def test_chatwoot_probe_rejects_missing_route_and_unexpected_version():
    async def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    async with _client(missing) as client:
        result = await _probe_chatwoot(client, "http://127.0.0.1:3100")
    assert result["state"] == "degraded"

    async def wrong_version(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            200,
            json={"version": "5.0.0", "queue_services": "ok", "data_services": "ok"},
        )

    async with _client(wrong_version) as client:
        result = await _probe_chatwoot(client, "http://127.0.0.1:3100")
    assert result["state"] == "degraded"
    assert result["label"] == "versão inesperada"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "health",
    [
        {"status": "ok", "errors": [{"code": "redis_unavailable"}]},
        {"status": "ok", "children": {"worker": {"status": "unavailable"}}},
    ],
)
async def test_chatwoot_probe_degrades_on_health_errors_or_unavailable_children(health):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=health)
        return httpx.Response(
            200,
            json={
                "version": "4.15.1",
                "queue_services": "ok",
                "data_services": "ok",
            },
        )

    async with _client(handler) as client:
        result = await _probe_chatwoot(client, "http://127.0.0.1:3100")

    assert result["state"] == "degraded"
    assert result["auth"] == "unknown"


@pytest.mark.asyncio
async def test_chatwoot_authenticated_data_read_overrides_lazy_connection_false_negative(
    monkeypatch,
):
    monkeypatch.setattr("app.services.oss_runtime_service.settings.KLM_CHATWOOT_ACCOUNT_ID", "1")
    monkeypatch.setattr(
        "app.services.oss_runtime_service.settings.KLM_CHATWOOT_API_ACCESS_TOKEN", "local-token"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "woot"})
        if request.url.path == "/api":
            return httpx.Response(
                200,
                json={
                    "version": "4.15.1",
                    "queue_services": "ok",
                    "data_services": "failing",
                },
            )
        assert request.url.path == "/api/v1/accounts/1"
        assert request.headers["api_access_token"] == "local-token"
        return httpx.Response(200, json={"id": 1, "name": "KLM Hub"})

    async with _client(handler) as client:
        result = await _probe_chatwoot(client, "http://127.0.0.1:3100")

    assert result == {
        "state": "ready",
        "label": "saudável, dados confirmados pela API autenticada",
        "auth": "ready",
    }


@pytest.mark.asyncio
async def test_versioned_probe_accepts_exact_rocket_minor_only():
    for reported, expected_state in (
        ("6.13", "ready"),
        ("6.13.1", "ready"),
        ("6.14", "degraded"),
        ("6.13.2", "degraded"),
        ("8.6", "degraded"),
    ):

        async def handler(_: httpx.Request, version: str = reported) -> httpx.Response:
            return httpx.Response(200, json={"version": version})

        async with _client(handler) as client:
            result = await _probe_versioned(
                client,
                "rocketchat",
                "http://127.0.0.1:3200",
                "/api/info",
            )
        assert result["state"] == expected_state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"status": "healthy", "errors": [{"code": "database_unavailable"}]},
        {
            "status": "healthy",
            "children": {"worker": {"status": "unavailable"}},
        },
        {"children": {"worker": {"status": "ready"}}},
    ],
)
async def test_versioned_probe_never_assumes_health_with_errors_or_missing_confirmation(
    payload,
):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        result = await _probe_versioned(
            client,
            "chattermate",
            "http://127.0.0.1:3300",
            "/health",
        )

    assert result["state"] == "degraded"


@pytest.mark.asyncio
async def test_bottlecrm_probe_uses_exact_gateway_health_path():
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"status": "healthy"})

    async with _client(handler) as client:
        result = await _probe_service(client, "bottlecrm", "http://127.0.0.1:3400/")

    assert result["state"] == "ready"
    assert paths == ["/healthz"]


@pytest.mark.asyncio
async def test_probe_retries_one_transient_transport_failure():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("sidecar restarting", request=request)
        return httpx.Response(200, json={"status": "healthy"})

    async with _client(handler) as client:
        result = await _probe_service(
            client,
            "chattermate",
            "http://127.0.0.1:3300",
        )

    assert result == {
        "state": "ready",
        "label": "saudável localmente",
        "auth": "ready",
    }
    assert attempts == 2


@pytest.mark.asyncio
async def test_probe_blocks_non_loopback_origin_without_request():
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("non-loopback probe must not issue a request")

    async with _client(handler) as client:
        result = await _probe_service(client, "bottlecrm", "https://crm.example.com")
    assert result == {
        "state": "blocked",
        "label": "URL não local bloqueada",
        "auth": "blocked",
    }


@pytest.mark.asyncio
async def test_bridge_probe_requires_authenticated_local_identity(monkeypatch):
    class FakeRocketClient:
        async def identity(self):
            return {"_id": "rocket-user", "username": "klm-admin"}

    class FakeChatterClient:
        async def identity(self):
            return {"id": "chatter-user", "organization_id": "klm-org"}

    class FakeBottleClient:
        async def authenticate(self):
            return {"accessToken": "local-token", "organizationId": "klm-org"}

    monkeypatch.setattr(
        "app.services.oss_runtime_service.settings.KLM_ROCKETCHAT_ADMIN_USERNAME",
        "klm-admin",
    )
    monkeypatch.setattr(
        "app.services.oss_runtime_service.settings.KLM_ROCKETCHAT_ADMIN_PASSWORD",
        "local-password",
    )
    monkeypatch.setattr(
        "app.services.oss_runtime_service.settings.KLM_CHATTERMATE_ADMIN_EMAIL",
        "admin@klm.local",
    )
    monkeypatch.setattr(
        "app.services.oss_runtime_service.settings.KLM_CHATTERMATE_ADMIN_PASSWORD",
        "local-password",
    )
    monkeypatch.setattr(
        "app.services.oss_runtime_service.settings.KLM_BOTTLECRM_BRIDGE_SECRET",
        "b" * 32,
    )
    monkeypatch.setattr(
        "app.services.oss_runtime_service.RocketChatClient",
        FakeRocketClient,
    )
    monkeypatch.setattr(
        "app.services.oss_runtime_service.ChatterMateClient",
        FakeChatterClient,
    )
    monkeypatch.setattr(
        "app.services.oss_runtime_service.BottleCRMClient",
        FakeBottleClient,
    )

    for provider in ("rocketchat", "chattermate", "bottlecrm"):
        assert await _probe_bridge_auth(provider) == {
            "state": "ready",
            "label": "saudável e autenticado pela ponte local",
            "auth": "ready",
        }


@pytest.mark.asyncio
async def test_rocket_authenticated_probe_uses_runtime_specific_timeout_floor(monkeypatch):
    async def delayed_probe(key: str):
        assert key == "rocketchat"
        await asyncio.sleep(0.02)
        return {
            "state": "ready",
            "label": "saudável e autenticado pela ponte local",
            "auth": "ready",
        }

    monkeypatch.setattr(oss_runtime_service, "_probe_bridge_auth", delayed_probe)
    monkeypatch.setattr(
        oss_runtime_service,
        "_BRIDGE_AUTH_TIMEOUT_FLOOR_SECONDS",
        {"rocketchat": 0.05},
    )

    result = await _require_authenticated_probe(
        "rocketchat",
        {"state": "ready", "label": "saudável localmente", "auth": "ready"},
        timeout=0.001,
    )

    assert result["state"] == "ready"
    assert result["auth"] == "ready"


def test_integration_state_never_reports_connected_from_health_alone(monkeypatch):
    monkeypatch.setattr(
        "app.services.oss_runtime_service.settings.KLM_OSS_LOCAL_BRIDGES_ENABLED",
        True,
    )
    assert (
        _integration_state(
            "rocketchat",
            {"state": "degraded", "label": "auth failed", "auth": "failed"},
        )
        == "auth_failed"
    )
    assert (
        _integration_state(
            "bottlecrm",
            {"state": "degraded", "label": "bootstrap", "auth": "not_configured"},
        )
        == "needs_bootstrap"
    )


def test_version_contract_handles_prefixes_and_invalid_values():
    assert _version_matches("chatwoot", "v4.15.1", "4.15.1")
    assert _version_matches("rocketchat", "6.13.1", "6.13")
    assert not _version_matches("rocketchat", "6.13.1", "6.13.2")
    assert not _version_matches("chatwoot", "v4.15.1", "latest")


def test_source_integrity_requires_locked_clean_checkout(monkeypatch, tmp_path):
    checkout = tmp_path / "chatwoot"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        "app.services.oss_runtime_service._UPSTREAM_ROOT",
        tmp_path,
    )

    def clean_git_output(_checkout: Path, *args: str) -> str:
        return "locked-commit" if args[:2] == ("rev-parse", "HEAD") else ""

    monkeypatch.setattr(
        "app.services.oss_runtime_service._git_output",
        clean_git_output,
    )
    specs = {
        "chatwoot": {
            "commit": "locked-commit",
            "excludePaths": ["enterprise"],
        }
    }

    assert _source_integrity("chatwoot", specs) == {
        "state": "pinned_clean",
        "head": "locked-commit",
        "components": {"chatwoot": "locked-commit"},
        "dirty": False,
        "details": [],
    }

    (checkout / "enterprise").mkdir()
    result = _source_integrity("chatwoot", specs)
    assert result["state"] == "drifted"
    assert result["details"] == ["chatwoot:exclusão proprietária materializada"]


def test_rocket_bootstrap_uses_version_compatible_integration_methods():
    bootstrap = Path(__file__).parents[2] / "docker" / "oss" / "rocketchat-bootstrap.py"
    tree = ast.parse(bootstrap.read_text(encoding="utf-8"))
    methods_by_path: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "request_json":
            continue
        method, path = node.args[:2]
        if not isinstance(method, ast.Constant) or not isinstance(path, ast.Constant):
            continue
        if isinstance(method.value, str) and isinstance(path.value, str):
            methods_by_path.setdefault(path.value, set()).add(method.value)

    assert methods_by_path["/api/v1/integrations.update"] == {"PUT"}
    assert methods_by_path["/api/v1/integrations.create"] == {"POST"}


@pytest.mark.asyncio
async def test_runtime_labels_commands_endpoint_disabled_without_dispatcher(monkeypatch):
    async def probe(_client, _key, _url):
        return {"state": "ready", "label": "ready", "auth": "ready"}

    async def authenticated(_key, current, *, timeout):
        del timeout
        return current

    monkeypatch.setattr(oss_runtime_service, "_probe_service", probe)
    monkeypatch.setattr(oss_runtime_service, "_require_authenticated_probe", authenticated)
    monkeypatch.setattr(
        oss_runtime_service,
        "_source_integrity",
        lambda _key, _specs: {
            "state": "pinned_clean",
            "head": "locked",
            "components": {},
            "dirty": False,
            "details": [],
        },
    )

    status = await oss_runtime_service._collect_oss_runtime_status()
    services = {row["key"]: row for row in status["services"]}

    assert services["chatwoot"]["commandsEndpoint"]["enabled"] is True
    for provider in ("rocketchat", "chattermate", "bottlecrm"):
        endpoint = services[provider]["commandsEndpoint"]
        assert endpoint["enabled"] is False
        assert endpoint["state"] == "disabled_no_dispatcher"
        assert endpoint["replacementPathTemplate"].endswith("/{capability}/actions")


@pytest.mark.asyncio
async def test_runtime_status_coalesces_concurrent_spa_probes(monkeypatch):
    reset_oss_runtime_status_cache()
    calls = 0

    async def collect():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {
            "mode": "local_only",
            "externalActionsEnabled": False,
            "services": [
                {"key": "chattermate", "state": "ready", "operational": True}
            ],
        }

    monkeypatch.setattr(oss_runtime_service, "_collect_oss_runtime_status", collect)

    first, second = await asyncio.gather(oss_runtime_status(), oss_runtime_status())
    first["mode"] = "mutated-by-caller"
    third = await oss_runtime_status()

    assert calls == 1
    assert second["mode"] == "local_only"
    assert third["mode"] == "local_only"
    reset_oss_runtime_status_cache()


@pytest.mark.asyncio
async def test_degraded_runtime_snapshot_refreshes_before_ready_cache(monkeypatch):
    reset_oss_runtime_status_cache()
    calls = 0

    async def collect():
        nonlocal calls
        calls += 1
        operational = calls > 1
        return {
            "mode": "local_only",
            "externalActionsEnabled": False,
            "services": [
                {
                    "key": "chattermate",
                    "state": "ready" if operational else "degraded",
                    "operational": operational,
                }
            ],
        }

    monkeypatch.setattr(oss_runtime_service, "_collect_oss_runtime_status", collect)
    monkeypatch.setattr(oss_runtime_service, "_DEGRADED_STATUS_CACHE_TTL_SECONDS", 0.01)

    first = await oss_runtime_status()
    await asyncio.sleep(0.02)
    recovered = await oss_runtime_status()
    cached = await oss_runtime_status()

    assert first["services"][0]["operational"] is False
    assert recovered["services"][0]["operational"] is True
    assert cached["services"][0]["operational"] is True
    assert calls == 2
    reset_oss_runtime_status_cache()
