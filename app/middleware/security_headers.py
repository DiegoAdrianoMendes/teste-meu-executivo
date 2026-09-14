"""HTTP response hardening for the API and any HTML served by FastAPI."""

from collections.abc import Iterable

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

DEVELOPMENT_DOCS_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com; "
    "connect-src 'self'"
)

PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(), camera=(), clipboard-read=(), "
    "clipboard-write=(self), display-capture=(self), encrypted-media=(), "
    "fullscreen=(self), geolocation=(), gyroscope=(), magnetometer=(), "
    "microphone=(), payment=(), picture-in-picture=(), usb=()"
)

STRICT_TRANSPORT_SECURITY = "max-age=31536000"

_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})
_NO_STORE_PATHS = _DOCS_PATHS | frozenset({"/health", "/openapi.json"})
_SENSITIVE_PREFIXES = ("/api/", "/webhooks/")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CREDENTIAL_HEADERS = frozenset({b"authorization", b"cookie"})


def build_spa_content_security_policy(*, is_production: bool) -> str:
    """Return the SPA policy, allowing loopback/HMR only outside production."""
    connect_sources = "'self' https: wss:"
    script_sources = "'self' https://connect.facebook.net"
    if not is_production:
        connect_sources = (
            "'self' https: wss: "
            "http://localhost:* http://127.0.0.1:* "
            "ws://localhost:* ws://127.0.0.1:*"
        )
        script_sources = "'self' 'unsafe-inline' https://connect.facebook.net"

    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        f"script-src {script_sources}; "
        "script-src-attr 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' data:; "
        f"connect-src {connect_sources}; "
        "frame-src https://*.facebook.com; "
        "media-src 'self' blob: https:; "
        "worker-src 'self' blob:; "
        "manifest-src 'self'"
    )


def _has_request_credentials(headers: Iterable[tuple[bytes, bytes]]) -> bool:
    return any(name.lower() in _CREDENTIAL_HEADERS for name, _ in headers)


def _requires_no_store(scope: Scope, response_headers: MutableHeaders, status: int) -> bool:
    path = scope.get("path", "")
    method = scope.get("method", "GET").upper()
    return (
        path in _NO_STORE_PATHS
        or path.startswith(_SENSITIVE_PREFIXES)
        or method not in _SAFE_METHODS
        or status >= 400
        or "set-cookie" in response_headers
        or _has_request_credentials(scope.get("headers", ()))
    )


class SecurityHeadersMiddleware:
    """Apply browser hardening without changing response bodies or streaming."""

    def __init__(self, app: ASGIApp, *, is_production: bool = False) -> None:
        self.app = app
        self.is_production = is_production
        self.spa_content_security_policy = build_spa_content_security_policy(
            is_production=is_production
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                content_type = headers.get("content-type", "").lower()
                path = scope.get("path", "")

                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = PERMISSIONS_POLICY

                if content_type.startswith("text/html"):
                    if not self.is_production and path in _DOCS_PATHS:
                        csp = DEVELOPMENT_DOCS_CONTENT_SECURITY_POLICY
                    else:
                        csp = self.spa_content_security_policy
                else:
                    csp = API_CONTENT_SECURITY_POLICY
                headers["Content-Security-Policy"] = csp

                status = int(message["status"])
                if _requires_no_store(scope, headers, status):
                    headers["Cache-Control"] = "private, no-store"

                if self.is_production and scope.get("scheme") == "https":
                    headers["Strict-Transport-Security"] = STRICT_TRANSPORT_SECURITY
                elif "strict-transport-security" in headers:
                    del headers["strict-transport-security"]

            await send(message)

        await self.app(scope, receive, send_with_security_headers)
