from __future__ import annotations

import re

import pytest

from app.services.native_public_boundary import resolve_internal_function_id
from app.services.oss_function_unit_service import function_unit_catalog

PUBLIC_FUNCTION_KEYS = {
    "id",
    "domain",
    "name",
    "category",
    "capability",
    "maturity",
    "disposition",
    "summary",
}
FORBIDDEN_RESPONSE = re.compile(
    r"chatwoot|rocket(?:[. _-]?chat)|chatter(?:[. _-]?mate)|"
    r"bottle(?:[. _-]?crm)|sidecar|upstream|/api/v1/oss-",
    re.IGNORECASE,
)


pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_function_api_exposes_minimal_domain_catalog(client) -> None:
    response = await client.get(
        "/api/v1/capabilities/functions",
        params={"domain": "automation", "category": "realtime", "pageSize": 5},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scope"] == "klm-capability-functions"
    assert payload["summary"]["total"] == len(function_unit_catalog()["units"])
    assert (
        payload["summary"]["available"] + payload["summary"]["blocked"]
        == payload["summary"]["total"]
    )
    assert set(payload["summary"]["byDomain"]) == {
        "communications",
        "collaboration",
        "automation",
        "crm",
    }
    assert payload["total"] == 14
    assert len(payload["items"]) == 5
    assert all(set(item) == PUBLIC_FUNCTION_KEYS for item in payload["items"])
    assert all(re.fullmatch(r"fu_[0-9a-f]{24}", item["id"]) for item in payload["items"])
    assert all(item["domain"] == "automation" for item in payload["items"])
    assert all(item["category"] == "realtime" for item in payload["items"])
    assert FORBIDDEN_RESPONSE.search(response.text) is None

    capability = payload["items"][0]["capability"]
    capability_filtered = await client.get(
        "/api/v1/capabilities/functions",
        params={"capability": capability, "pageSize": 200},
    )
    assert capability_filtered.status_code == 200, capability_filtered.text
    assert capability_filtered.json()["items"]
    assert all(item["capability"] == capability for item in capability_filtered.json()["items"])

    detail = await client.get(f"/api/v1/capabilities/functions/{payload['items'][0]['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json() == payload["items"][0]


@pytest.mark.asyncio
async def test_raw_function_id_cannot_be_queried_or_searched(client) -> None:
    raw_id = str(function_unit_catalog()["units"][0]["id"])

    detail = await client.get(f"/api/v1/capabilities/functions/{raw_id}")
    search = await client.get(
        "/api/v1/capabilities/functions",
        params={"search": raw_id},
    )

    assert detail.status_code == 404
    assert raw_id not in detail.text
    assert search.status_code == 200
    assert search.json()["total"] == 0
    assert raw_id not in search.text


@pytest.mark.asyncio
async def test_function_api_rejects_private_and_invalid_filters(client) -> None:
    invalid_state = await client.get(
        "/api/v1/capabilities/functions",
        params={"disposition": "verified_by_assumption"},
    )
    invalid_domain = await client.get(
        "/api/v1/capabilities/functions",
        params={"domain": "private-runtime"},
    )
    private_filter = await client.get(
        "/api/v1/capabilities/functions",
        params={"upstream": "private-runtime"},
    )
    missing = await client.get("/api/v1/capabilities/functions/fu_000000000000000000000000")

    assert invalid_state.status_code == 422
    assert invalid_domain.status_code == 422
    assert private_filter.status_code == 422
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_function_api_requires_authentication(unauthenticated_client) -> None:
    response = await unauthenticated_client.get("/api/v1/capabilities/functions")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_function_runtime_and_action_keep_reverse_resolution_private(client) -> None:
    listing = await client.get(
        "/api/v1/capabilities/functions",
        params={"category": "api", "disposition": "native_primary", "pageSize": 200},
    )
    assert listing.status_code == 200, listing.text
    raw_by_id = {str(row["id"]): row for row in function_unit_catalog()["units"]}
    unit = next(
        row
        for row in listing.json()["items"]
        if raw_by_id[resolve_internal_function_id(row["id"])]["klmMapping"]["semanticProfile"]
        == "http.create"
    )

    runtime = await client.get(f"/api/v1/capabilities/functions/{unit['id']}/runtime")
    assert runtime.status_code == 200, runtime.text
    assert set(runtime.json()) == {"function", "state"}
    assert runtime.json()["function"] == unit
    assert runtime.json()["state"]["externalEffectsEnabled"] is False
    assert "dry-run" in runtime.json()["state"]["availableModes"]
    assert FORBIDDEN_RESPONSE.search(runtime.text) is None

    request = {
        "mode": "execute",
        "idempotencyKey": "public-function-roundtrip",
        "payload": {"name": "Registro API local"},
    }
    action = await client.post(
        f"/api/v1/capabilities/functions/{unit['id']}/actions",
        json=request,
    )
    replay = await client.post(
        f"/api/v1/capabilities/functions/{unit['id']}/actions",
        json=request,
    )

    assert action.status_code == 200, action.text
    assert set(action.json()) == {
        "status",
        "replayed",
        "externalEffectExecuted",
        "summary",
    }
    assert action.json()["status"] == "succeeded"
    assert action.json()["externalEffectExecuted"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert FORBIDDEN_RESPONSE.search(action.text + replay.text) is None
