"""Observabilidade da operação de IA: agregação, filtros, escopo e RBAC/RLS."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
)
from app.main import app as fastapi_app
from app.models.ai_gateway_event import AiGatewayEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)


async def _seed_events(db_session, workspace_id) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            AiGatewayEvent(
                workspace_id=workspace_id,
                request_id="req-1",
                agent_name="klm_sdr",
                task_class="simple",
                provider="anthropic",
                model="claude-haiku-4-5",
                outcome="success",
                route_key="anthropic:claude-haiku-4-5",
                latency_ms=120,
                input_tokens=100,
                output_tokens=40,
                cost_usd=Decimal("0.00050000"),
                fallback_used=False,
                created_at=now,
            ),
            AiGatewayEvent(
                workspace_id=workspace_id,
                request_id="req-2",
                agent_name="klm_drafter",
                task_class="premium",
                provider="openai",
                model="gpt-5.6-sol",
                outcome="success",
                route_key="openai:gpt-5.6-sol",
                latency_ms=900,
                input_tokens=2000,
                output_tokens=800,
                cost_usd=Decimal("0.03000000"),
                fallback_used=True,  # anthropic caiu → openai assumiu
                created_at=now,
            ),
            AiGatewayEvent(
                workspace_id=workspace_id,
                request_id="req-3",
                agent_name="klm_drafter",
                task_class="premium",
                provider="anthropic",
                model="claude-opus-4-8",
                outcome="provider_error",
                route_key="anthropic:claude-opus-4-8",
                latency_ms=50,
                fallback_used=False,
                billing_unknown=True,
                created_at=now,
            ),
        ]
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_workspace_ai_operations_aggregates_own_events(
    client, db_session, auth_context: AuthContext, active_subscription
):
    await _seed_events(db_session, auth_context.workspace_id)
    resp = await client.get("/api/v1/workspaces/ai-operations?days=7")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["executions"] == 3
    assert body["fallback_count"] == 1
    assert body["billing_unknown"] == 1
    assert 0.33 <= body["fallback_rate"] <= 0.34
    agents = {row["agent_name"]: row["executions"] for row in body["by_agent"]}
    assert agents["klm_drafter"] == 2
    # A rota que falhou aparece como alerta durável.
    alert_routes = {a["route_key"] for a in body["route_alerts"]}
    assert "anthropic:claude-opus-4-8" in alert_routes


@pytest.mark.asyncio
async def test_platform_ai_operations_summary_lists_workspace(
    platform_client, db_session, auth_context: AuthContext
):
    await _seed_events(db_session, auth_context.workspace_id)
    resp = await platform_client.get("/api/v1/platform/ai-operations/summary?days=7")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executions"] >= 3
    ws_ids = {row["workspace_id"] for row in body["by_workspace"]}
    assert str(auth_context.workspace_id) in ws_ids


@pytest.mark.asyncio
async def test_platform_ai_operations_events_filters_only_fallbacks(
    platform_client, db_session, auth_context: AuthContext
):
    await _seed_events(db_session, auth_context.workspace_id)
    resp = await platform_client.get(
        "/api/v1/platform/ai-operations/events?only_fallbacks=true"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["hasMore"] is False
    assert all(item["fallback_used"] for item in body["items"])
    assert body["items"][0]["provider"] == "openai"

    first_page = await platform_client.get(
        "/api/v1/platform/ai-operations/events?limit=2&offset=0"
    )
    assert first_page.status_code == 200, first_page.text
    assert first_page.json()["total"] == 3
    assert len(first_page.json()["items"]) == 2
    assert first_page.json()["hasMore"] is True


@pytest.mark.asyncio
async def test_platform_ai_operations_requires_platform_staff(client, db_session, auth_context):
    resp = await client.get("/api/v1/platform/ai-operations/summary")
    assert resp.status_code in (401, 403)


@pytest_asyncio.fixture
async def viewer_client(db_session, auth_context: AuthContext):
    viewer = User(
        name="Viewer Operacao",
        email="viewer-ai-ops@example.com",
        agent_type=AgentType.human,
        role=UserRole.intern,
        initials="VO",
        color="#334155",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(viewer)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=auth_context.workspace_id,
        user_id=viewer.id,
        role=WorkspaceRole.viewer,
        status=MembershipStatus.active,
        is_default=True,
    )
    db_session.add(membership)
    await db_session.flush()
    ctx = AuthContext(
        user_id=viewer.id,
        workspace_id=auth_context.workspace_id,
        membership_id=membership.id,
        role=WorkspaceRole.viewer,
        is_internal_staff=False,
    )

    async def _db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _db
    fastapi_app.dependency_overrides[get_current_auth_context] = lambda: ctx
    fastapi_app.dependency_overrides[get_current_user_id] = lambda: ctx.user_id
    fastapi_app.dependency_overrides[get_current_workspace_id] = lambda: ctx.workspace_id
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_workspace_ai_operations_denies_non_admin(viewer_client):
    resp = await viewer_client.get("/api/v1/workspaces/ai-operations")
    assert resp.status_code == 403
