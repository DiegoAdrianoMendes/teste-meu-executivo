"""Provas de convergência entre o OpenAPI administrativo e o bypass root."""

from __future__ import annotations

import pytest

from app.main import app

ADMIN_PREFIXES = (
    "/api/v1/platform",
    "/api/v1/error-logs",
    "/api/v1/status",
    "/api/v1/super-admin",
)


def collection_get_routes() -> list[str]:
    return sorted(
        path
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(ADMIN_PREFIXES)
        and "get" in operations
        and "{" not in path
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", collection_get_routes())
async def test_platform_root_is_never_blocked_by_rbac_on_admin_collection_reads(
    platform_client,
    path: str,
) -> None:
    response = await platform_client.get(path)

    assert response.status_code not in {401, 403}, (
        f"platform_owner foi bloqueado em {path}: {response.status_code} {response.text}"
    )
    if response.status_code == 422:
        detail = response.json().get("detail", [])
        assert detail and all(item.get("loc", [None])[0] == "query" for item in detail), (
            f"{path} falhou por motivo diferente de query obrigatória: {response.text}"
        )


def test_root_surface_probe_is_derived_and_nontrivial() -> None:
    routes = collection_get_routes()
    assert len(routes) >= 60
    assert "/api/v1/platform/session-manifest" in routes
    assert "/api/v1/platform/governance/security/incidents" in routes
