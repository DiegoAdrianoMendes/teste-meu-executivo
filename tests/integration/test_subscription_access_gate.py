from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.deps import (
    SUBSCRIPTION_ACCESS_DENIED_CODE,
    SUBSCRIPTION_ACCESS_DENIED_MESSAGE,
    require_subscription_access,
    subscription_access_operation_for_method,
)
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.subscription_access import (
    SubscriptionAccessState,
    SubscriptionAccessStatus,
)
from app.schemas.subscription_access import SubscriptionAccessOperation


async def _seed_access_state(
    db: AsyncSession,
    workspace_id,
    access_status: SubscriptionAccessStatus,
) -> None:
    now = datetime.now(UTC)
    plan = Plan(
        name=f"Gate {access_status.value}",
        tier=PlanTier.free,
        price_monthly_cents=0,
        price_annually_cents=0,
        features={},
    )
    db.add(plan)
    await db.flush()

    subscription = Subscription(
        workspace_id=workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.active,
        interval=BillingInterval.monthly,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        amount_cents=0,
    )
    db.add(subscription)
    await db.flush()

    db.add(
        SubscriptionAccessState(
            workspace_id=workspace_id,
            subscription_id=subscription.id,
            status=access_status,
            state_version=1,
            status_changed_at=now,
            grace_started_at=(
                now if access_status == SubscriptionAccessStatus.grace else None
            ),
            grace_ends_at=(
                now + timedelta(days=7)
                if access_status == SubscriptionAccessStatus.grace
                else None
            ),
        )
    )
    await db.flush()


def _denied_payload(
    access_status: SubscriptionAccessStatus,
    operation: SubscriptionAccessOperation,
) -> dict:
    reason_code = {
        SubscriptionAccessStatus.past_due: "subscription_past_due_read_only",
        SubscriptionAccessStatus.canceled: "subscription_canceled_read_only",
        SubscriptionAccessStatus.suspended: "subscription_suspended",
    }[access_status]
    return {
        "detail": {
            "code": SUBSCRIPTION_ACCESS_DENIED_CODE,
            "message": SUBSCRIPTION_ACCESS_DENIED_MESSAGE,
            "operation": operation.value,
            "state": access_status.value,
            "reasonCode": reason_code,
        }
    }


@pytest.mark.parametrize(
    ("method", "operation"),
    [
        ("GET", SubscriptionAccessOperation.read),
        ("HEAD", SubscriptionAccessOperation.read),
        ("OPTIONS", SubscriptionAccessOperation.read),
        ("POST", SubscriptionAccessOperation.write),
        ("PUT", SubscriptionAccessOperation.write),
        ("PATCH", SubscriptionAccessOperation.write),
        ("DELETE", SubscriptionAccessOperation.write),
    ],
)
def test_subscription_gate_classifies_http_methods(
    method: str,
    operation: SubscriptionAccessOperation,
) -> None:
    assert subscription_access_operation_for_method(method) == operation


@pytest.mark.parametrize(
    ("access_status", "read_status", "write_status"),
    [
        (SubscriptionAccessStatus.active, 200, 200),
        (SubscriptionAccessStatus.grace, 200, 200),
        (SubscriptionAccessStatus.past_due, 200, 403),
        (SubscriptionAccessStatus.canceled, 200, 403),
        (SubscriptionAccessStatus.suspended, 403, 403),
    ],
)
async def test_subscription_gate_enforces_read_write_matrix_on_tenant_routes(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_context,
    access_status: SubscriptionAccessStatus,
    read_status: int,
    write_status: int,
) -> None:
    await _seed_access_state(db_session, auth_context.workspace_id, access_status)

    read_response = await client.get("/api/v1/notifications")
    write_response = await client.post("/api/v1/notifications/mark-all-read")

    assert read_response.status_code == read_status
    assert write_response.status_code == write_status
    if read_status == 403:
        assert read_response.json() == _denied_payload(
            access_status,
            SubscriptionAccessOperation.read,
        )
    if write_status == 403:
        assert write_response.json() == _denied_payload(
            access_status,
            SubscriptionAccessOperation.write,
        )


async def test_suspended_workspace_keeps_auth_and_billing_accessible(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _seed_access_state(
        db_session,
        auth_context.workspace_id,
        SubscriptionAccessStatus.suspended,
    )

    tenant_response = await client.get("/api/v1/notifications")
    auth_response = await client.get("/api/v1/auth/me")
    plan_summary_response = await client.get("/api/v1/workspaces/plan-summary")
    billing_response = await client.get("/api/v1/local-billing/portal")
    export_response = await client.get(
        f"/api/v1/local-storage/objects/{uuid4()}/export"
    )

    assert tenant_response.status_code == 403
    assert auth_response.status_code == 200
    assert plan_summary_response.status_code == 200
    assert billing_response.status_code == 200
    assert billing_response.json()["access"]["state"] == "suspended"
    assert export_response.status_code == 404


async def test_missing_access_state_is_compatible_for_development_legacy_workspace(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "online")

    read_response = await client.get("/api/v1/notifications")
    write_response = await client.post("/api/v1/notifications/mark-all-read")

    assert read_response.status_code == 200
    assert write_response.status_code == 200


async def test_missing_access_state_fails_closed_in_production(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")

    response = await client.get("/api/v1/notifications")

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": SUBSCRIPTION_ACCESS_DENIED_CODE,
            "message": SUBSCRIPTION_ACCESS_DENIED_MESSAGE,
            "operation": "read",
            "state": None,
            "reasonCode": "subscription_missing",
        }
    }


def _route_has_subscription_gate(method: str, path: str) -> bool:
    route = next(
        route
        for route in api_router.routes
        if isinstance(route, APIRoute) and route.path == path and method in route.methods
    )
    return any(
        dependency.call is require_subscription_access
        for dependency in route.dependant.dependencies
    )


@pytest.mark.parametrize(
    "path_prefix",
    [
        "/api/v1/auth",
        "/api/v1/local-billing",
        "/api/v1/platform",
        "/api/v1/public",
        "/api/v1/public-api",
        "/api/v1/webhooks",
    ],
)
def test_non_tenant_router_families_never_receive_subscription_gate(
    path_prefix: str,
) -> None:
    routes = [
        route
        for route in api_router.routes
        if isinstance(route, APIRoute)
        and (route.path == path_prefix or route.path.startswith(f"{path_prefix}/"))
    ]

    assert routes
    assert all(
        dependency.call is not require_subscription_access
        for route in routes
        for dependency in route.dependant.dependencies
    )


def test_router_applies_gate_only_to_tenant_surfaces() -> None:
    assert _route_has_subscription_gate("GET", "/api/v1/notifications")
    assert _route_has_subscription_gate("POST", "/api/v1/notifications/mark-all-read")
    assert _route_has_subscription_gate("GET", "/api/v1/workspaces/plan-summary")
    assert _route_has_subscription_gate("POST", "/api/v1/local-storage/objects")
    assert _route_has_subscription_gate(
        "GET",
        "/api/v1/local-storage/objects/{object_id}/export",
    )

    assert not _route_has_subscription_gate("GET", "/api/v1/auth/me")
    assert not _route_has_subscription_gate("POST", "/api/v1/klm/local/session")
    assert not _route_has_subscription_gate("GET", "/api/v1/klm/runtime")
    assert not _route_has_subscription_gate("GET", "/api/v1/klm/tools")
    assert not _route_has_subscription_gate("GET", "/api/v1/local-billing/portal")
    assert not _route_has_subscription_gate(
        "POST",
        "/api/v1/local-billing/intents/export",
    )
    assert not _route_has_subscription_gate(
        "POST",
        "/api/v1/local-billing/intents/closure",
    )
    assert not _route_has_subscription_gate("GET", "/api/v1/local-storage/download")
    assert not _route_has_subscription_gate("GET", "/api/v1/platform/overview")
    assert not _route_has_subscription_gate("GET", "/api/v1/public/site")
    assert not _route_has_subscription_gate("POST", "/api/v1/webhooks/stripe")
    assert not _route_has_subscription_gate("POST", "/api/v1/whatsapp/webhook")
    assert not _route_has_subscription_gate(
        "POST",
        "/api/v1/integrations/webhooks/{workspace_slug}/{provider}",
    )
    assert not _route_has_subscription_gate("POST", "/api/v1/agenda/public/{token}")
