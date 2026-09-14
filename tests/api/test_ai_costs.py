from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_auth_context, get_current_workspace_id
from app.db.tenant import get_workspace_context
from app.main import app as fastapi_app
from app.models.ai_token_package import (
    AiTokenPackage,
    AiTokenPackageRequestStatus,
    WorkspaceAiTokenPackageRequest,
)
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    PlatformTokenUsage,
    PlatformTokenUsageByTask,
    Subscription,
    SubscriptionStatus,
    TokenBudget,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement, WorkspaceMonthlyUsage
from app.models.klm_operations import KlmAuditEvent
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.platform_audit_event_service import PlatformAuditEventService


async def _workspace(db: AsyncSession, label: str) -> Workspace:
    workspace = Workspace(
        name=f"Workspace IA {label}",
        slug=f"workspace-ia-{label}-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()
    return workspace


async def _typed_plan(
    db: AsyncSession,
    workspace_ids: list[UUID],
    *,
    token_limit: int,
    hard_limit: bool = True,
) -> Plan:
    plan = Plan(
        name="Plano de custo IA",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=token_limit,
        max_ai_agents=100,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db.add(plan)
    await db.flush()
    db.add(
        PlanEntitlement(
            plan_id=plan.id,
            entitlement=EntitlementKey.tokens,
            enabled=True,
            limit_quantity=token_limit,
            hard_limit=hard_limit,
        )
    )
    db.add_all(
        [
            Subscription(
                workspace_id=workspace_id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=0,
                currency="BRL",
            )
            for workspace_id in workspace_ids
        ]
    )
    await db.flush()
    return plan


def _usage(
    workspace_id: UUID,
    *,
    model: str,
    provider: str,
    runs: int,
    input_tokens: int,
    output_tokens: int,
    cost: str,
    occurred_at: datetime | None = None,
) -> PlatformTokenUsage:
    return PlatformTokenUsage(
        workspace_id=workspace_id,
        model_used=model,
        provider=provider,
        usage_date=occurred_at or datetime.now(UTC),
        total_runs=runs,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_cost_usd=Decimal(cost),
    )


def _task_usage(
    workspace_id: UUID,
    *,
    task_class: str,
    model: str,
    provider: str,
    runs: int,
    input_tokens: int,
    output_tokens: int,
    cost: str,
) -> PlatformTokenUsageByTask:
    return PlatformTokenUsageByTask(
        workspace_id=workspace_id,
        task_class=task_class,
        model_used=model,
        provider=provider,
        usage_date=datetime.now(UTC),
        total_runs=runs,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_cost_usd=Decimal(cost),
    )


@pytest.mark.asyncio
async def test_tenant_summary_is_camel_case_monthly_and_workspace_isolated(
    client,
    db_session: AsyncSession,
    auth_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = await _workspace(db_session, "isolado")
    await _typed_plan(
        db_session,
        [auth_context.workspace_id, other.id],
        token_limit=200_000,
    )
    month_start = datetime.now(UTC).date().replace(day=1)
    db_session.add_all(
        [
            TokenBudget(
                workspace_id=auth_context.workspace_id,
                monthly_limit=100_000,
                alert_at_pct=80,
                hard_limit=True,
            ),
            WorkspaceMonthlyUsage(
                workspace_id=auth_context.workspace_id,
                entitlement=EntitlementKey.tokens,
                period_start=month_start,
                consumed_quantity=50_000,
                reserved_quantity=0,
                limit_quantity=100_000,
                hard_limit=True,
                limit_source="token_budget",
            ),
            _usage(
                auth_context.workspace_id,
                model="model-a",
                provider="local",
                runs=8,
                input_tokens=20_000,
                output_tokens=10_000,
                cost="0.400000",
            ),
            _usage(
                auth_context.workspace_id,
                model="model-b",
                provider="fake",
                runs=4,
                input_tokens=12_000,
                output_tokens=8_000,
                cost="0.100000",
            ),
            _usage(
                other.id,
                model="private-other-model",
                provider="other",
                runs=99,
                input_tokens=900_000,
                output_tokens=100_000,
                cost="99.000000",
            ),
            _task_usage(
                auth_context.workspace_id,
                task_class="simple",
                model="model-a",
                provider="local",
                runs=8,
                input_tokens=20_000,
                output_tokens=10_000,
                cost="0.400000",
            ),
            _task_usage(
                auth_context.workspace_id,
                task_class="complex",
                model="model-b",
                provider="fake",
                runs=4,
                input_tokens=12_000,
                output_tokens=8_000,
                cost="0.100000",
            ),
            _task_usage(
                other.id,
                task_class="premium",
                model="private-other-model",
                provider="other",
                runs=99,
                input_tokens=900_000,
                output_tokens=100_000,
                cost="99.000000",
            ),
        ]
    )
    await db_session.flush()
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)

    response = await client.get("/api/v1/workspaces/ai-costs")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workspaceId"] == str(auth_context.workspace_id)
    assert payload["totalRuns"] == 12
    assert payload["inputTokens"] == 32_000
    assert payload["outputTokens"] == 18_000
    assert payload["totalTokens"] == 50_000
    assert "costUsd" not in payload
    assert payload["monthlyLimit"] == 100_000
    assert payload["policyMonthlyLimit"] == 100_000
    assert payload["usedPercent"] == 50.0
    assert payload["remainingTokens"] == 50_000
    assert payload["alertLevel"] == "ok"
    assert payload["quotaState"] == "configured"
    assert [(item["provider"], item["model"]) for item in payload["byModel"]] == [
        ("local", "model-a"),
        ("fake", "model-b"),
    ]
    assert all("costUsd" not in item for item in payload["byModel"])
    assert [(item["taskClass"], item["runs"]) for item in payload["byTask"]] == [
        ("simple", 8),
        ("complex", 4),
    ]
    assert all("costUsd" not in item for item in payload["byTask"])
    assert "private-other-model" not in response.text
    assert payload["gates"] == {
        "modelApisEnabled": False,
        "onlineApproved": False,
        "externalActionsEnabled": False,
    }
    assert payload["periodStart"].startswith(f"{month_start.isoformat()}T00:00:00")
    assert settings.KLM_AWS_ENABLED is False


@pytest.mark.asyncio
async def test_tenant_patch_validates_audits_and_reconciles_current_ledger(
    client,
    db_session: AsyncSession,
    auth_context,
) -> None:
    await _typed_plan(db_session, [auth_context.workspace_id], token_limit=1_000)
    month_start = datetime.now(UTC).date().replace(day=1)
    budget = TokenBudget(
        workspace_id=auth_context.workspace_id,
        monthly_limit=500,
        alert_at_pct=80,
        hard_limit=True,
    )
    ledger = WorkspaceMonthlyUsage(
        workspace_id=auth_context.workspace_id,
        entitlement=EntitlementKey.tokens,
        period_start=month_start,
        consumed_quantity=400,
        reserved_quantity=0,
        limit_quantity=500,
        hard_limit=True,
        limit_source="token_budget",
    )
    db_session.add_all(
        [
            budget,
            ledger,
            _usage(
                auth_context.workspace_id,
                model="model-budget",
                provider="local",
                runs=2,
                input_tokens=300,
                output_tokens=100,
                cost="0.020000",
            ),
        ]
    )
    await db_session.flush()

    invalid_payloads = [
        {},
        {"monthlyLimit": -1},
        {"monthlyLimit": 2_147_483_648},
        {"alertAtPct": 0},
        {"alertAtPct": 101},
        {"hardLimit": None},
    ]
    for invalid in invalid_payloads:
        rejected = await client.patch("/api/v1/workspaces/ai-costs", json=invalid)
        assert rejected.status_code == 422, (invalid, rejected.text)

    updated = await client.patch(
        "/api/v1/workspaces/ai-costs",
        json={"monthlyLimit": 300, "alertAtPct": 75, "hardLimit": False},
    )

    assert updated.status_code == 200, updated.text
    payload = updated.json()
    assert payload["monthlyLimit"] == 1_000
    assert payload["policyMonthlyLimit"] == 300
    assert payload["alertAtPct"] == 75
    assert payload["hardLimit"] is True
    assert payload["usedPercent"] == 40.0
    assert payload["remainingTokens"] == 600
    assert payload["alertLevel"] == "ok"
    await db_session.refresh(budget)
    await db_session.refresh(ledger)
    assert (budget.monthly_limit, budget.alert_at_pct, budget.hard_limit) == (300, 75, False)
    assert ledger.limit_quantity == 1_000
    assert ledger.hard_limit is True
    assert ledger.limit_source == "plan_entitlement"

    audit = await db_session.scalar(
        select(KlmAuditEvent)
        .where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "ai_token_budget_updated",
        )
        .order_by(KlmAuditEvent.occurred_at.desc())
    )
    assert audit is not None
    assert audit.actor_user_id == auth_context.user_id
    assert audit.entity_id == budget.id
    assert audit.before_data["monthlyLimit"] == 500
    assert audit.after_data["monthlyLimit"] == 300
    assert audit.after_data["effectiveMonthlyLimit"] == 1_000

    reset = await client.patch(
        "/api/v1/workspaces/ai-costs",
        json={"monthlyLimit": None},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["monthlyLimit"] == 1_000
    assert reset.json()["policyMonthlyLimit"] is None
    assert reset.json()["usedPercent"] == 40.0
    assert reset.json()["remainingTokens"] == 600
    assert reset.json()["alertLevel"] == "ok"
    await db_session.refresh(ledger)
    assert ledger.limit_quantity == 1_000
    assert ledger.hard_limit is True


@pytest.mark.asyncio
async def test_tenant_ai_costs_require_owner_or_workspace_admin(
    client,
    db_session: AsyncSession,
    auth_context,
    active_subscription,
) -> None:
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None
    membership.role = WorkspaceRole.operator
    auth_context.role = WorkspaceRole.operator
    await db_session.flush()

    denied_read = await client.get("/api/v1/workspaces/ai-costs")
    denied_write = await client.patch(
        "/api/v1/workspaces/ai-costs",
        json={"monthlyLimit": 100},
    )
    denied_platform = await client.get("/api/v1/platform/ai-costs?days=30")

    assert denied_read.status_code == 403
    assert denied_write.status_code == 403
    assert denied_platform.status_code == 403
    assert await db_session.scalar(
        select(func.count(TokenBudget.id)).where(
            TokenBudget.workspace_id == auth_context.workspace_id
        )
    ) == 0

    membership.role = WorkspaceRole.workspace_admin
    auth_context.role = WorkspaceRole.workspace_admin
    await db_session.flush()
    allowed = await client.get("/api/v1/workspaces/ai-costs")
    assert allowed.status_code == 200, allowed.text
    created = await client.patch(
        "/api/v1/workspaces/ai-costs",
        json={"monthlyLimit": 100},
    )
    assert created.status_code == 200, created.text
    assert created.json()["monthlyLimit"] == 100
    assert created.json()["quotaState"] == "local_legacy"
    assert created.json()["alertAtPct"] == 80
    assert created.json()["hardLimit"] is False
    assert await db_session.scalar(
        select(func.count(TokenBudget.id)).where(
            TokenBudget.workspace_id == auth_context.workspace_id
        )
    ) == 1


@pytest.mark.asyncio
async def test_tenant_summary_fails_closed_when_token_quota_is_missing(
    client,
    auth_context,
    active_subscription,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)

    response = await client.get("/api/v1/workspaces/ai-costs")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workspaceId"] == str(auth_context.workspace_id)
    assert payload["monthlyLimit"] is None
    assert payload["policyMonthlyLimit"] is None
    assert payload["remainingTokens"] is None
    assert payload["usedPercent"] is None
    assert payload["quotaState"] == "missing"
    assert "unlimited" not in response.text.lower()
    assert "ilimitado" not in response.text.lower()


@pytest.mark.asyncio
async def test_platform_ai_costs_support_global_workspace_view_manage_and_isolation(
    platform_client,
    db_session: AsyncSession,
    platform_auth_context,
) -> None:
    workspace_a = await _workspace(db_session, "platform-a")
    workspace_b = await _workspace(db_session, "platform-b")
    budget_a = TokenBudget(
        workspace_id=workspace_a.id,
        monthly_limit=1_000,
        alert_at_pct=80,
        hard_limit=True,
    )
    budget_b = TokenBudget(
        workspace_id=workspace_b.id,
        monthly_limit=2_000,
        alert_at_pct=90,
        hard_limit=False,
    )
    db_session.add_all(
        [
            budget_a,
            budget_b,
            _usage(
                workspace_a.id,
                model="platform-model-a",
                provider="local",
                runs=3,
                input_tokens=600,
                output_tokens=200,
                cost="0.080000",
            ),
            _usage(
                workspace_b.id,
                model="platform-model-b",
                provider="fake",
                runs=5,
                input_tokens=500,
                output_tokens=500,
                cost="0.120000",
            ),
            _usage(
                workspace_b.id,
                model="outside-window",
                provider="fake",
                runs=50,
                input_tokens=50_000,
                output_tokens=50_000,
                cost="50.000000",
                occurred_at=datetime.now(UTC) - timedelta(days=40),
            ),
        ]
    )
    await db_session.flush()
    workspace_a_id = workspace_a.id
    budget_a_id = budget_a.id
    budget_b_id = budget_b.id

    global_response = await platform_client.get("/api/v1/platform/ai-costs?days=30")
    workspace_response = await platform_client.get(
        f"/api/v1/platform/ai-costs?days=30&workspaceId={workspace_a.id}"
    )

    assert global_response.status_code == 200, global_response.text
    assert global_response.json()["workspaceId"] is None
    assert global_response.json()["totalRuns"] == 8
    assert global_response.json()["totalTokens"] == 1_800
    assert global_response.json()["costUsd"] == 0.2
    assert global_response.json()["monthlyLimit"] is None
    assert global_response.json()["policyMonthlyLimit"] is None
    assert "outside-window" not in global_response.text
    assert workspace_response.status_code == 200, workspace_response.text
    assert workspace_response.json()["workspaceId"] == str(workspace_a.id)
    assert workspace_response.json()["totalRuns"] == 3
    assert "platform-model-b" not in workspace_response.text
    assert get_workspace_context(db_session) == workspace_a.id

    updated = await platform_client.patch(
        f"/api/v1/platform/ai-costs/{workspace_a.id}",
        json={"monthlyLimit": 750, "alertAtPct": 70, "hardLimit": True},
    )
    missing = await platform_client.patch(
        f"/api/v1/platform/ai-costs/{uuid4()}",
        json={"monthlyLimit": 100},
    )
    invalid_days = await platform_client.get("/api/v1/platform/ai-costs?days=0")
    assert updated.status_code == 200, updated.text
    assert updated.json()["workspaceId"] == str(workspace_a.id)
    assert updated.json()["monthlyLimit"] == 750
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "AI-COST-001"
    assert invalid_days.status_code == 422
    budget_a = await db_session.get(TokenBudget, budget_a_id)
    budget_b = await db_session.get(TokenBudget, budget_b_id)
    assert budget_a is not None
    assert budget_b is not None
    assert budget_a.monthly_limit == 750
    assert budget_b.monthly_limit == 2_000
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == workspace_a_id,
            KlmAuditEvent.event_type == "ai_token_budget_updated",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == platform_auth_context.user_id
    assert audit.before_data["monthlyLimit"] == 1_000
    assert audit.after_data["monthlyLimit"] == 750

    platform_user = await db_session.get(User, platform_auth_context.user_id)
    assert platform_user is not None
    platform_user.is_platform_admin = False
    platform_user.is_internal_staff = True
    platform_user.platform_access_active = True
    platform_user.platform_role_package = "product_operations"
    await db_session.flush()

    view_only = await platform_client.get("/api/v1/platform/ai-costs?days=30")
    aws_costs_denied = await platform_client.get("/api/v1/platform/aws-costs")
    overview_without_internal_costs = await platform_client.get("/api/v1/platform/overview")
    manage_denied = await platform_client.patch(
        f"/api/v1/platform/ai-costs/{workspace_b.id}",
        json={"monthlyLimit": 900},
    )
    assert view_only.status_code == 403, view_only.text
    assert view_only.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"
    assert aws_costs_denied.status_code == 403
    assert aws_costs_denied.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"
    assert overview_without_internal_costs.status_code == 200
    assert "ai_costs_30d" not in overview_without_internal_costs.json()
    assert "aws_costs" not in overview_without_internal_costs.json()
    assert manage_denied.status_code == 403
    assert manage_denied.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"
    budget_b = await db_session.get(TokenBudget, budget_b_id)
    assert budget_b is not None
    assert budget_b.monthly_limit == 2_000


@pytest.mark.asyncio
async def test_extra_token_package_requires_support_and_changes_effective_quota(
    client,
    platform_client,
    db_session: AsyncSession,
    auth_context,
    platform_auth_context,
) -> None:
    def use_auth_context(context) -> None:
        async def override_auth_context():
            return context

        async def override_workspace_id():
            return context.workspace_id

        fastapi_app.dependency_overrides[get_current_auth_context] = override_auth_context
        fastapi_app.dependency_overrides[get_current_workspace_id] = override_workspace_id

    await _typed_plan(db_session, [auth_context.workspace_id], token_limit=1_000)

    use_auth_context(platform_auth_context)
    created_package = await platform_client.put(
        "/api/v1/platform/ai-token-packages/tokens-250",
        json={
            "name": "Pacote 250 tokens",
            "description": "Crédito adicional para o mês corrente.",
            "tokenQuantity": 250,
            "commercialPriceCents": 4990,
            "currency": "BRL",
            "isActive": True,
            "sortOrder": 1,
        },
    )
    assert created_package.status_code == 200, created_package.text
    package = created_package.json()
    package_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == package["id"],
            PlatformAuditEvent.action == "ai_token_package.upsert",
        )
    )
    assert package_audit is not None
    assert package_audit.before is None
    assert package_audit.after["tokenQuantity"] == 250
    assert package_audit.after["commercialPriceCents"] == 4990

    use_auth_context(auth_context)
    catalog = await client.get("/api/v1/workspaces/ai-token-packages")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["supportReleaseRequired"] is True
    assert catalog.json()["selfActivationEnabled"] is False
    assert catalog.json()["packages"][0]["commercialPriceCents"] == 4990

    request_payload = {
        "packageId": package["id"],
        "idempotencyKey": f"test-package-{uuid4()}",
    }
    requested = await client.post(
        "/api/v1/workspaces/ai-token-packages/requests",
        json=request_payload,
    )
    duplicated = await client.post(
        "/api/v1/workspaces/ai-token-packages/requests",
        json=request_payload,
    )
    assert requested.status_code == 201, requested.text
    assert duplicated.status_code == 201, duplicated.text
    assert requested.json()["id"] == duplicated.json()["id"]
    assert requested.json()["status"] == "requested"

    before_approval = await client.get("/api/v1/workspaces/ai-costs")
    assert before_approval.status_code == 200, before_approval.text
    assert before_approval.json()["monthlyLimit"] == 1_000

    platform_user = await db_session.get(User, platform_auth_context.user_id)
    assert platform_user is not None
    platform_user.is_platform_admin = False
    platform_user.is_internal_staff = True
    platform_user.platform_access_active = True
    platform_user.platform_role_package = "support"
    await db_session.flush()

    use_auth_context(platform_auth_context)
    support_catalog = await platform_client.get("/api/v1/platform/ai-token-packages")
    requests = await platform_client.get(
        f"/api/v1/platform/workspaces/{auth_context.workspace_id}/ai-token-package-requests/page",
        params={"limit": 10, "offset": 0},
    )
    assert support_catalog.status_code == 200, support_catalog.text
    assert requests.status_code == 200, requests.text
    request_page = requests.json()
    assert request_page["total"] == 1
    assert request_page["limit"] == 10
    assert request_page["offset"] == 0
    assert request_page["hasMore"] is False
    request_id = request_page["items"][0]["id"]

    approval_reason = "Crédito adicional validado pelo suporte."

    missing_reason = await platform_client.post(
        f"/api/v1/platform/workspaces/{auth_context.workspace_id}/ai-token-package-requests/{request_id}/decision",
        json={"decision": "approved"},
    )
    blank_reason = await platform_client.post(
        f"/api/v1/platform/workspaces/{auth_context.workspace_id}/ai-token-package-requests/{request_id}/decision",
        json={"decision": "approved", "decisionNote": "        "},
    )
    assert missing_reason.status_code == 422
    assert blank_reason.status_code == 422

    approved = await platform_client.post(
        f"/api/v1/platform/workspaces/{auth_context.workspace_id}/ai-token-package-requests/{request_id}/decision",
        json={"decision": "approved", "decisionNote": approval_reason},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    approval_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == request_id,
            PlatformAuditEvent.action == "ai_token_package_request.approved",
        )
    )
    assert approval_audit is not None
    assert approval_audit.reason == approval_reason
    assert approval_audit.before["status"] == "requested"
    assert approval_audit.after["status"] == "approved"
    assert "status" in approval_audit.changed_paths

    use_auth_context(auth_context)
    after_approval = await client.get("/api/v1/workspaces/ai-costs")
    assert after_approval.status_code == 200, after_approval.text
    assert after_approval.json()["monthlyLimit"] == 1_250
    assert after_approval.json()["policyMonthlyLimit"] is None

    use_auth_context(platform_auth_context)
    root_only_catalog_write = await platform_client.put(
        "/api/v1/platform/ai-token-packages/forbidden-for-support",
        json={
            "name": "Não permitido",
            "tokenQuantity": 100,
            "currency": "BRL",
            "isActive": True,
            "sortOrder": 0,
        },
    )
    assert root_only_catalog_write.status_code == 403
    assert root_only_catalog_write.json()["detail"]["code"] == "PLATFORM-ROOT-REQUIRED"

    revoke_reason = "Solicitação encerrada após revisão operacional."
    revoked = await platform_client.post(
        f"/api/v1/platform/workspaces/{auth_context.workspace_id}/ai-token-package-requests/{request_id}/revoke",
        json={"decisionNote": revoke_reason},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"
    revoke_audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_id == request_id,
            PlatformAuditEvent.action == "ai_token_package_request.revoked",
        )
    )
    assert revoke_audit is not None
    assert revoke_audit.reason == revoke_reason
    assert revoke_audit.before["status"] == "approved"
    assert revoke_audit.after["status"] == "revoked"
    assert "status" in revoke_audit.changed_paths
    use_auth_context(auth_context)
    after_revoke = await client.get("/api/v1/workspaces/ai-costs")
    assert after_revoke.status_code == 200, after_revoke.text
    assert after_revoke.json()["monthlyLimit"] == 1_000

    request_row = await db_session.get(WorkspaceAiTokenPackageRequest, UUID(request_id))
    assert request_row is not None
    assert request_row.status == AiTokenPackageRequestStatus.revoked


@pytest.mark.asyncio
async def test_ai_token_package_rolls_back_when_canonical_audit_fails(
    platform_client,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    before_count = int(
        (await db_session.scalar(select(func.count(AiTokenPackage.id)))) or 0
    )
    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.put(
            "/api/v1/platform/ai-token-packages/tokens-audit-rollback",
            json={
                "name": "Pacote que exige auditoria",
                "tokenQuantity": 100,
                "commercialPriceCents": 1000,
                "currency": "BRL",
                "isActive": True,
                "sortOrder": 10,
            },
        )
    db_session.expire_all()
    after_count = int(
        (await db_session.scalar(select(func.count(AiTokenPackage.id)))) or 0
    )
    assert after_count == before_count
