import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from httpx import ASGITransport, AsyncClient, Response

from app.middleware.security_headers import (
    API_CONTENT_SECURITY_POLICY,
    DEVELOPMENT_DOCS_CONTENT_SECURITY_POLICY,
    PERMISSIONS_POLICY,
    STRICT_TRANSPORT_SECURITY,
    SecurityHeadersMiddleware,
)


def _assert_common_security_headers(response: Response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == PERMISSIONS_POLICY
    assert "display-capture=(self)" in response.headers["permissions-policy"]
    assert "camera=()" in response.headers["permissions-policy"]
    assert "microphone=()" in response.headers["permissions-policy"]


def _build_test_app(*, is_production: bool) -> FastAPI:
    test_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @test_app.get("/api/private")
    async def private_response():
        return JSONResponse({"secret": "redacted"})

    @test_app.get("/spa", response_class=HTMLResponse)
    async def spa_response():
        return "<!doctype html><html><body><div id='root'></div></body></html>"

    @test_app.get("/public")
    async def public_response():
        return JSONResponse(
            {"status": "public"},
            headers={"Cache-Control": "public, max-age=60"},
        )

    test_app.add_middleware(SecurityHeadersMiddleware, is_production=is_production)
    return test_app


async def _request(
    app: FastAPI,
    path: str,
    *,
    base_url: str = "http://test",
    headers: dict[str, str] | None = None,
) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as client:
        return await client.get(path, headers=headers)


@pytest.mark.asyncio
async def test_main_app_applies_api_security_headers(unauthenticated_client):
    response = await unauthenticated_client.get("/health")

    assert response.status_code == 200
    _assert_common_security_headers(response)
    assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY
    assert response.headers["cache-control"] == "private, no-store"
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_vite_origin_preflight_keeps_cors_and_security_headers(unauthenticated_client):
    response = await unauthenticated_client.options(
        "/api/v1/auth/login",
        headers={
            "Access-Control-Request-Method": "POST",
            "Origin": "http://127.0.0.1:8080",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
    assert response.headers["access-control-allow-credentials"] == "true"
    _assert_common_security_headers(response)
    assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_local_docs_csp_allows_swagger_assets(unauthenticated_client):
    response = await unauthenticated_client.get("/docs")

    assert response.status_code == 200
    _assert_common_security_headers(response)
    assert (
        response.headers["content-security-policy"]
        == DEVELOPMENT_DOCS_CONTENT_SECURITY_POLICY
    )
    assert "https://cdn.jsdelivr.net" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_development_spa_csp_allows_only_loopback_http_and_hmr():
    response = await _request(_build_test_app(is_production=False), "/spa")

    assert response.status_code == 200
    _assert_common_security_headers(response)
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "https://connect.facebook.net" in csp
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "http://localhost:*" in csp
    assert "http://127.0.0.1:*" in csp
    assert "ws://localhost:*" in csp
    assert "ws://127.0.0.1:*" in csp
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_sensitive_cache_rules_preserve_explicit_public_cache_only_when_safe():
    app = _build_test_app(is_production=False)

    private_response = await _request(app, "/api/private")
    public_response = await _request(app, "/public")
    credentialed_response = await _request(
        app,
        "/public",
        headers={"Authorization": "Bearer test-token"},
    )

    assert private_response.headers["cache-control"] == "private, no-store"
    assert public_response.headers["cache-control"] == "public, max-age=60"
    assert credentialed_response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_production", "base_url", "expected_hsts"),
    [
        (True, "https://test", STRICT_TRANSPORT_SECURITY),
        (True, "http://test", None),
        (False, "https://test", None),
    ],
)
async def test_hsts_requires_both_production_and_https(
    is_production: bool,
    base_url: str,
    expected_hsts: str | None,
):
    response = await _request(
        _build_test_app(is_production=is_production),
        "/api/private",
        base_url=base_url,
    )

    assert response.headers.get("strict-transport-security") == expected_hsts
    assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY


@pytest.mark.asyncio
async def test_production_spa_csp_removes_loopback_exceptions():
    response = await _request(
        _build_test_app(is_production=True),
        "/spa",
        base_url="https://test",
    )

    csp = response.headers["content-security-policy"]
    assert "connect-src 'self' https: wss:" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "localhost" not in csp
    assert "127.0.0.1" not in csp
    assert response.headers["strict-transport-security"] == STRICT_TRANSPORT_SECURITY
