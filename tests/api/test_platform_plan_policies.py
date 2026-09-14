from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints.platform_plan_policies import router
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import EntitlementKey
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.plan_policy import (
    FEATURE_ENTITLEMENTS,
    PlanPolicyEntitlementSetting,
    PlanPolicyParameters,
    PlanPolicySnapshot,
)

NOW = datetime(2026, 7, 22, 18, 0, tzinfo=UTC)


def _snapshot() -> PlanPolicySnapshot:
    return PlanPolicySnapshot(
        name="Starter API",
        description="Plano editado por checkbox e número",
        price_monthly_cents=29_700,
        price_annually_cents=285_120,
        parameters=PlanPolicyParameters(
            max_campaign_recipients_per_month=1_000,
        ),
        entitlements={
            key: PlanPolicyEntitlementSetting(
                enabled=(key not in FEATURE_ENTITLEMENTS),
                limit_quantity=(
                    750_000
                    if key is EntitlementKey.tokens
                    else 20
                    if key not in FEATURE_ENTITLEMENTS
                    else None
                ),
                hard_limit=True,
            )
            for key in EntitlementKey
        },
    )


@pytest_asyncio.fixture
async def plan_policy_client(db_session, platform_auth_context):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_get_db():
        yield db_session

    async def override_context():
        return platform_auth_context

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def delegated_plan_policy_client(db_session):
    user = User(
        name="Onboarding com permissão de planos",
        email=f"onboarding-plans-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="OP",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        platform_access_active=True,
        platform_role_package="onboarding",
        platform_permission_grants=[{"module": "plans", "action": "draft"}],
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    context = AuthContext(
        user_id=user.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_get_db():
        yield db_session

    async def override_context():
        return context

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def tenant_plan_policy_client(db_session, auth_context):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_get_db():
        yield db_session

    async def override_context():
        return auth_context

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth_context] = override_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _seed_plan_and_account(db_session) -> tuple[Plan, Workspace]:
    plan = Plan(
        name="Starter",
        tier=PlanTier.starter,
        description="Starter",
        price_monthly_cents=29_700,
        price_annually_cents=285_120,
        max_users=3,
        max_matters=200,
        max_tokens_per_month=500_000,
        max_ai_agents=2,
        features={},
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    workspace = Workspace(
        name="Conta API",
        slug=f"conta-api-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
        created_at=NOW - timedelta(days=10),
        updated_at=NOW - timedelta(days=10),
    )
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        Subscription(
            workspace_id=workspace.id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=29_700,
            currency="BRL",
            created_at=workspace.created_at,
            updated_at=workspace.created_at,
        )
    )
    await db_session.flush()
    return plan, workspace


@pytest.mark.asyncio
async def test_super_admin_edits_previews_publishes_and_resolves_real_policy(
    plan_policy_client,
    db_session,
) -> None:
    plan, workspace = await _seed_plan_and_account(db_session)
    snapshot_payload = _snapshot().model_dump(mode="json", by_alias=True)

    catalog = await plan_policy_client.get("/api/v1/platform/plan-policies/entitlement-catalog")
    assert catalog.status_code == 200, catalog.text
    assert len(catalog.json()) == len(EntitlementKey)
    assert next(row for row in catalog.json() if row["key"] == "tokens") == {
        "key": "tokens",
        "control": "checkbox_and_number",
        "supportsLimit": True,
        "requiresFiniteLimit": True,
    }

    draft = await plan_policy_client.put(
        f"/api/v1/platform/plan-policies/{plan.id}/draft",
        json={"snapshot": snapshot_payload},
    )
    assert draft.status_code == 200, draft.text
    draft_payload = draft.json()
    assert draft_payload["revision"] == 1

    editor = await plan_policy_client.get(f"/api/v1/platform/plan-policies/{plan.id}/editor")
    assert editor.status_code == 200, editor.text
    assert editor.json()["source"] == "draft"
    assert editor.json()["snapshot"]["entitlements"]["tokens"]["limitQuantity"] == 750_000
    assert editor.json()["snapshot"]["parameters"]["maxCampaignRecipientsPerMonth"] == 1_000

    preview = await plan_policy_client.post(
        f"/api/v1/platform/plan-policies/{plan.id}/preview",
        json={
            "rolloutMode": "existing_and_new",
            "effectiveAt": NOW.isoformat(),
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["affectedCount"] == 1
    assert preview.json()["accounts"][0]["workspaceId"] == str(workspace.id)

    publication_request = {
        "rolloutMode": "existing_and_new",
        "effectiveAt": NOW.isoformat(),
        "draftEtag": draft_payload["etag"],
        "reason": "Publicação API",
    }
    published = await plan_policy_client.post(
        f"/api/v1/platform/plan-policies/{plan.id}/publish",
        json=publication_request,
        headers={"Idempotency-Key": "api-plan-policy-v1"},
    )
    replayed = await plan_policy_client.post(
        f"/api/v1/platform/plan-policies/{plan.id}/publish",
        json=publication_request,
        headers={"Idempotency-Key": "api-plan-policy-v1"},
    )
    assert published.status_code == 201, published.text
    assert replayed.status_code == 201, replayed.text
    assert published.json()["policy"]["version"] == 1
    assert published.json()["assignmentsCreated"] == 1
    assert replayed.json()["policy"]["id"] == published.json()["policy"]["id"]
    assert replayed.json()["idempotentReplay"] is True

    versions = await plan_policy_client.get(f"/api/v1/platform/plan-policies/{plan.id}/versions")
    plans_page = await plan_policy_client.get(
        "/api/v1/platform/plan-policies/page",
        params={"limit": 10, "offset": 0},
    )
    versions_page = await plan_policy_client.get(
        f"/api/v1/platform/plan-policies/{plan.id}/versions/page",
        params={"limit": 10, "offset": 0},
    )
    audit = await plan_policy_client.get(f"/api/v1/platform/plan-policies/{plan.id}/audit")
    resolution = await plan_policy_client.post(
        f"/api/v1/platform/plan-policies/workspaces/{workspace.id}/resolve",
        params={"effectiveAt": NOW.isoformat()},
    )
    assert versions.status_code == 200, versions.text
    assert len(versions.json()) == 1
    assert plans_page.status_code == 200, plans_page.text
    assert plans_page.json()["total"] >= 1
    assert plans_page.json()["items"][0]["planId"] == str(plan.id)
    assert versions_page.status_code == 200, versions_page.text
    assert versions_page.json()["total"] == 1
    assert versions_page.json()["hasMore"] is False
    assert audit.status_code == 200, audit.text
    assert {event["eventType"] for event in audit.json()} >= {
        "draft_saved",
        "policy_published",
    }
    assert resolution.status_code == 200, resolution.text
    assert resolution.json()["parameters"]["maxCampaignRecipientsPerMonth"] == 1_000
    assert resolution.json()["policyVersion"] == 1
    assert resolution.json()["entitlements"]["tokens"]["limitQuantity"] == 750_000


@pytest.mark.asyncio
async def test_plan_policy_routes_require_platform_root(
    tenant_plan_policy_client,
    db_session,
) -> None:
    plan, _workspace = await _seed_plan_and_account(db_session)

    denied = await tenant_plan_policy_client.get(f"/api/v1/platform/plan-policies/{plan.id}/editor")
    denied_write = await tenant_plan_policy_client.put(
        f"/api/v1/platform/plan-policies/{plan.id}/draft",
        json={"snapshot": _snapshot().model_dump(mode="json", by_alias=True)},
    )

    assert denied.status_code == 403
    assert denied_write.status_code == 403
    assert denied_write.json()["detail"]["code"] == "BLACKBOX-IDENTITY-REQUIRED"


@pytest.mark.asyncio
async def test_plan_policy_routes_allow_delegated_internal_staff_with_explicit_permission(
    delegated_plan_policy_client,
    db_session,
) -> None:
    plan, _workspace = await _seed_plan_and_account(db_session)

    editor = await delegated_plan_policy_client.get(
        f"/api/v1/platform/plan-policies/{plan.id}/editor"
    )
    draft = await delegated_plan_policy_client.put(
        f"/api/v1/platform/plan-policies/{plan.id}/draft",
        json={"snapshot": _snapshot().model_dump(mode="json", by_alias=True)},
    )

    assert editor.status_code == 200, editor.text
    assert draft.status_code == 200, draft.text
