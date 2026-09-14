"""Model and endpoint allowlist enforcement."""
from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from app.ai.errors import BaseURLNotAllowedError, ModelNotAllowedError
from app.ai.models import RouteCandidate


def normalize_base_url(raw_url: str) -> str:
    parsed = urlparse((raw_url or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.params
    ):
        raise BaseURLNotAllowedError("Base URL de IA inválida; use HTTPS sem credenciais ou query.")
    path = parsed.path.rstrip("/")
    normalized = parsed._replace(path=path, query="", fragment="", params="")
    return urlunparse(normalized)


def require_allowed_base_url(raw_url: str, allowed_urls: frozenset[str]) -> str:
    normalized = normalize_base_url(raw_url)
    normalized_allowlist = frozenset(normalize_base_url(item) for item in allowed_urls)
    if normalized not in normalized_allowlist:
        raise BaseURLNotAllowedError("Base URL de IA fora da allowlist exata.")
    return normalized


def require_allowed_model(route: RouteCandidate, allowlist: frozenset[str]) -> None:
    if route.key not in allowlist:
        raise ModelNotAllowedError(f"Modelo fora da allowlist: {route.key}.")
