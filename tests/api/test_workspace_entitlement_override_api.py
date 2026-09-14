"""API tests for the per-account entitlement override endpoints and the
AGREGADO 360 workspace overview (leitura técnica RBAC, operação root-only).

Covers:
- 403 for a workspace-surface user on both routes (platform-root only);
- 404 for an unknown workspace;
- overview aggregates identification/subscription/entitlements/support/audit;
- an override changes the entitlement's resolved origin from plan to override;
- the overview never leaks another workspace's overrides or entitlements;
- upsert then delete each write a KlmAuditEvent;
- validation: action=upsert requires mode.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.main import app as fastapi_app
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.client import Client
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.finance_harmonization import FinanceHarmonizationQuota
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.task import Task, TaskReviewState, TaskStatus
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.workspace_entitlement_override_service import (
    WorkspaceEntitlementOverrideService,
)

pytestmark = pytest.mark.asyncio


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"ent-ovr-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


async def _seed_workspace_with_plan(
    db_session: AsyncSession, name: str, *, tier: PlanTier = PlanTier.pro
) -> Workspace:
    workspace = _workspace(name)
    db_session.add(workspace)
    await db_session.flush()
    plan = Plan(
        name=f"Plano {name}",
        tier=tier,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=10,
        max_matters=None,
        max_tokens_per_month=100,
        max_ai_agents=10,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        Subscription(
            workspace_id=workspace.id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=19700,
            currency="BRL",
        )
    )
    db_session.add(
        PlanEntitlement(
            plan_id=plan.id,
            entitlement=EntitlementKey.tokens,
            enabled=True,
            limit_quantity=100,
            hard_limit=True,
        )
    )
    await db_session.commit()
    return workspace


async def _seed_platform_actor(db_session: AsyncSession) -> User:
    user = User(
        name="Titular Blackbox",
        email=f"root-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="TB",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def test_overview_requires_platform_root(client, db_session):
    workspace = await _seed_workspace_with_plan(db_session, "Overview Perm WS")
    response = await client.get(f"/api/v1/platform/workspaces/{workspace.id}/overview")
    assert response.status_code == 403


async def test_overview_returns_404_for_unknown_workspace(platform_client):
    response = await platform_client.get(f"/api/v1/platform/workspaces/{uuid4()}/overview")
    assert response.status_code == 404


async def test_overview_aggregates_identification_subscription_and_entitlements(
    db_session, platform_client
):
    workspace = await _seed_workspace_with_plan(db_session, "Overview 360 WS")

    response = await platform_client.get(f"/api/v1/platform/workspaces/{workspace.id}/overview")
    assert response.status_code == 200
    payload = response.json()

    assert payload["workspace"]["id"] == str(workspace.id)
    assert payload["workspace"]["slug"] == workspace.slug
    assert payload["subscription"]["plan"]["tier"] == "pro"

    entitlement_map = {row["entitlement"]: row for row in payload["entitlements"]}
    assert entitlement_map["tokens"]["enabled"] is True
    assert entitlement_map["tokens"]["limit_quantity"] == 100
    assert entitlement_map["tokens"]["origin"] == "plan"
    assert entitlement_map["tokens"]["unit"] == "tokens"
    assert entitlement_map["tokens"]["quantitative"] is True
    assert entitlement_map["tokens"]["metered"] is True
    assert entitlement_map["digital_signature"]["unit"] == "feature"
    assert entitlement_map["digital_signature"]["quantitative"] is False
    assert entitlement_map["digital_signature"]["metered"] is False
    assert entitlement_map["automations"]["unit"] == "automations"
    assert entitlement_map["automations"]["quantitative"] is True
    assert entitlement_map["automations"]["metered"] is False
    assert "usage_this_month" in entitlement_map["tokens"]

    assert payload["support"]["open_tickets"] == 0
    assert payload["integrations"] == []
    assert payload["whatsapp_numbers"] == []
    assert payload["agents"]["active_ai_agents"] == 0
    assert payload["members"] == []
    assert payload["tasks"] == {
        "total": 0,
        "open": 0,
        "overdue": 0,
        "blocked": 0,
        "pending_review": 0,
        "completed": 0,
        "cancelled": 0,
        "last_updated_at": None,
        "content_included": False,
    }
    assert payload["entitlement_overrides"] == []
    assert payload["finance_kpis"] is None
    assert payload["recent_audit_events"] == []
    assert payload["private_data_included"] is False


async def test_overview_exposes_only_safe_task_counts_not_tenant_content(
    db_session, platform_client
):
    workspace = await _seed_workspace_with_plan(db_session, "Overview Task Summary WS")
    customer = Client(workspace_id=workspace.id, name="Cliente que não pode vazar")
    db_session.add(customer)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace.id,
        client_id=customer.id,
        demand_type="Segredo do caso que não pode vazar",
        practice_area=PracticeArea.civel,
    )
    db_session.add(matter)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all(
        [
            Task(
                workspace_id=workspace.id,
                matter_id=matter.id,
                title="Título privado vencido",
                description="Comentário privado que não pode vazar",
                status=TaskStatus.pending,
                due_at=now - timedelta(hours=1),
            ),
            Task(
                workspace_id=workspace.id,
                matter_id=matter.id,
                title="Título privado em revisão",
                status=TaskStatus.in_progress,
                review_required=True,
                review_state=TaskReviewState.pending,
                due_at=now + timedelta(days=2),
                review_due_at=now + timedelta(days=1),
            ),
            Task(
                workspace_id=workspace.id,
                matter_id=matter.id,
                title="Título privado bloqueado",
                status=TaskStatus.blocked,
                due_at=now + timedelta(days=1),
            ),
            Task(
                workspace_id=workspace.id,
                matter_id=matter.id,
                title="Título privado concluído",
                status=TaskStatus.done,
                completed_at=now,
            ),
        ]
    )
    await db_session.commit()

    response = await platform_client.get(
        f"/api/v1/platform/workspaces/{workspace.id}/overview"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tasks"]["total"] == 4
    assert payload["tasks"]["open"] == 3
    assert payload["tasks"]["overdue"] == 1
    assert payload["tasks"]["blocked"] == 1
    assert payload["tasks"]["pending_review"] == 1
    assert payload["tasks"]["completed"] == 1
    assert payload["tasks"]["content_included"] is False
    serialized = str(payload["tasks"])
    assert "Título privado" not in serialized
    assert "Cliente que não pode vazar" not in serialized
    assert "Segredo do caso" not in serialized
    assert "Comentário privado" not in serialized


async def test_overview_allows_customer_staff_but_redacts_private_blocks(db_session):
    workspace = await _seed_workspace_with_plan(db_session, "Overview Staff WS")
    employee = User(
        name="Customer Success",
        email=f"cs-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="CS",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_role_package="customer_success",
        platform_access_active=True,
    )
    db_session.add(employee)
    await db_session.commit()
    context = AuthContext(
        user_id=employee.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    workspace_id = workspace.id

    async def override_get_db():
        await db_session.commit()
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            db_session.expunge_all()
            await db_session.rollback()
            raise

    async def override_context():
        return context

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_auth_context] = override_context
    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/platform/workspaces/{workspace_id}/overview"
            )
            mutation = await client.patch(
                f"/api/v1/platform/people/{workspace_id}/{uuid4()}/block",
                json={"blocked": True, "reason": "Teste de isolamento da operação administrativa."},
            )
    finally:
        fastapi_app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"]["id"] == str(workspace_id)
    assert payload["private_data_included"] is False
    assert payload["finance_kpis"] is None
    assert payload["recent_audit_events"] == []
    assert mutation.status_code == 403


async def test_overview_reflects_entitlement_override_origin(db_session, platform_client):
    workspace = await _seed_workspace_with_plan(db_session, "Overview Override WS")

    put_response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace.id}/entitlement-overrides",
        json={
            "overrides": [
                {
                    "entitlementKey": "tokens",
                    "action": "upsert",
                    "mode": "limit",
                    "quantity": 999,
                    "reason": "Piloto negociado com o cliente.",
                }
            ]
        },
    )
    assert put_response.status_code == 200
    assert put_response.json()["overrides"][0]["quantity"] == 999
    assert put_response.json()["overrides"][0]["mode"] == "limit"

    response = await platform_client.get(f"/api/v1/platform/workspaces/{workspace.id}/overview")
    entitlement_map = {row["entitlement"]: row for row in response.json()["entitlements"]}
    assert entitlement_map["tokens"]["origin"] == "override"
    assert entitlement_map["tokens"]["limit_quantity"] == 999


async def test_overview_does_not_leak_another_workspaces_data(db_session, platform_client):
    # A single test-harness DB session is bound to one workspace's RLS context
    # for its lifetime (app.db.tenant enforces this — see WorkspaceContextError),
    # matching how one real request never switches workspaces mid-transaction.
    # So workspace A's override is seeded directly through the service (no HTTP,
    # no context switch) and only the GET against workspace B goes through the
    # endpoint — exercising the exact same workspace-scoped queries that would
    # run in production, still proving they never return workspace A's rows.
    workspace_a = await _seed_workspace_with_plan(
        db_session, "Overview WS A", tier=PlanTier.starter
    )
    workspace_b = await _seed_workspace_with_plan(
        db_session, "Overview WS B", tier=PlanTier.business
    )
    actor = await _seed_platform_actor(db_session)

    await WorkspaceEntitlementOverrideService(db_session).upsert_override(
        workspace_id=workspace_a.id,
        entitlement_key="digital_signature",
        mode="grant",
        quantity=None,
        reason="Somente para o workspace A.",
        actor_user_id=actor.id,
    )
    await db_session.commit()

    response_b = await platform_client.get(
        f"/api/v1/platform/workspaces/{workspace_b.id}/overview"
    )
    assert response_b.status_code == 200
    payload_b = response_b.json()
    assert payload_b["workspace"]["id"] == str(workspace_b.id)
    assert payload_b["entitlement_overrides"] == []
    entitlement_map_b = {row["entitlement"]: row for row in payload_b["entitlements"]}
    assert entitlement_map_b["digital_signature"]["origin"] != "override"


async def test_put_entitlement_overrides_requires_platform_root(client, db_session):
    workspace = await _seed_workspace_with_plan(db_session, "Override Perm WS 2")
    response = await client.put(
        f"/api/v1/platform/workspaces/{workspace.id}/entitlement-overrides",
        json={"overrides": []},
    )
    assert response.status_code == 403


async def test_put_entitlement_overrides_upsert_then_delete_is_audited(
    db_session, platform_client
):
    workspace = await _seed_workspace_with_plan(db_session, "Override Audit WS")

    upsert_response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace.id}/entitlement-overrides",
        json={
            "overrides": [
                {
                    "entitlementKey": "automations",
                    "action": "upsert",
                    "mode": "grant",
                    "quantity": 5,
                    "reason": "Piloto comercial de automações.",
                }
            ]
        },
    )
    assert upsert_response.status_code == 200
    assert len(upsert_response.json()["overrides"]) == 1

    delete_response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace.id}/entitlement-overrides",
        json={
            "overrides": [
                {
                    "entitlementKey": "automations",
                    "action": "delete",
                    "reason": "Piloto encerrado.",
                }
            ]
        },
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["overrides"] == []

    events = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == workspace.id,
                KlmAuditEvent.entity_type == "workspace_entitlement_override",
            )
        )
    ).all()
    assert {event.event_type for event in events} == {
        "workspace_entitlement_override_upserted",
        "workspace_entitlement_override_deleted",
    }
    canonical = list(await db_session.scalars(
        select(PlatformAuditEvent)
        .where(
            PlatformAuditEvent.resource_type == "workspace_entitlement_overrides",
            PlatformAuditEvent.resource_id == str(workspace.id),
        )
        .order_by(PlatformAuditEvent.occurred_at.asc())
    ))
    assert [event.action for event in canonical] == [
        "update_workspace_entitlement_overrides",
        "update_workspace_entitlement_overrides",
    ]
    assert canonical[0].before["overrides"] == []
    assert canonical[0].after["overrides"][0]["entitlement"] == "automations"
    assert canonical[1].before["overrides"][0]["entitlement"] == "automations"
    assert canonical[1].after["overrides"] == []
    assert canonical[0].changed_paths
    assert canonical[1].changed_paths


async def test_entitlement_override_rolls_back_when_canonical_audit_fails(
    db_session, platform_client, monkeypatch
):
    workspace = await _seed_workspace_with_plan(db_session, "Override Atomic WS")

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.put(
            f"/api/v1/platform/workspaces/{workspace.id}/entitlement-overrides",
            json={
                "overrides": [
                    {
                        "entitlementKey": "automations",
                        "action": "upsert",
                        "mode": "grant",
                        "quantity": 5,
                        "reason": "Teste de atomicidade do bundle comercial.",
                    }
                ]
            },
        )

    overrides = await WorkspaceEntitlementOverrideService(db_session).list_overrides(
        workspace.id
    )
    assert overrides == []
    legacy_events = list(await db_session.scalars(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == workspace.id,
            KlmAuditEvent.entity_type == "workspace_entitlement_override",
        )
    ))
    assert legacy_events == []


async def test_finance_harmonization_bonus_has_canonical_diff(
    db_session, platform_client
):
    workspace = await _seed_workspace_with_plan(db_session, "Finance Bonus Audit WS")
    response = await platform_client.post(
        f"/api/v1/platform/workspaces/{workspace.id}/finance-harmonization-quota/bonus",
        json={
            "competence": "2026-07",
            "extra": 3,
            "reason": "Compensação comercial aprovada para a conta.",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 6

    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "finance_harmonization_quota",
            PlatformAuditEvent.resource_id == f"{workspace.id}:2026-07",
        )
    )
    assert event is not None
    assert event.before["exists"] is False
    assert event.before["bonusExtra"] == 0
    assert event.after["exists"] is True
    assert event.after["bonusExtra"] == 3
    assert {"exists", "bonusExtra", "grantedByUserId", "grantedAt"}.issubset(
        event.changed_paths
    )


async def test_finance_harmonization_bonus_rolls_back_when_audit_fails(
    db_session, platform_client, monkeypatch
):
    workspace = await _seed_workspace_with_plan(db_session, "Finance Bonus Atomic WS")

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.post(
            f"/api/v1/platform/workspaces/{workspace.id}/finance-harmonization-quota/bonus",
            json={
                "competence": "2026-07",
                "extra": 2,
                "reason": "Teste de rollback do bônus administrativo.",
            },
        )

    quota = await db_session.scalar(
        select(FinanceHarmonizationQuota).where(
            FinanceHarmonizationQuota.workspace_id == workspace.id,
            FinanceHarmonizationQuota.competence == "2026-07",
        )
    )
    assert quota is None


async def test_put_entitlement_overrides_rejects_missing_mode_for_upsert(
    db_session, platform_client
):
    workspace = await _seed_workspace_with_plan(db_session, "Override Validation WS")
    response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace.id}/entitlement-overrides",
        json={
            "overrides": [
                {"entitlementKey": "tokens", "action": "upsert", "reason": "Sem modo."}
            ]
        },
    )
    assert response.status_code == 422


async def test_quantitative_override_requires_quantity_without_audit_side_effects(
    db_session, platform_client
):
    workspace = await _seed_workspace_with_plan(
        db_session, "Override Finite Quantity WS"
    )
    workspace_id = workspace.id

    response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace_id}/entitlement-overrides",
        json={
            "overrides": [
                {
                    "entitlementKey": "automations",
                    "action": "upsert",
                    "mode": "grant",
                    "reason": "Tentativa sem franquia quantitativa explícita.",
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "WS-ENT-OVR-003"
    assert "quantity finita" in response.json()["detail"]["message"]
    assert (
        await WorkspaceEntitlementOverrideService(db_session).list_overrides(
            workspace_id
        )
        == []
    )
    legacy_events = list(
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.entity_type == "workspace_entitlement_override",
            )
        )
    )
    canonical_events = list(
        await db_session.scalars(
            select(PlatformAuditEvent).where(
                PlatformAuditEvent.resource_type
                == "workspace_entitlement_overrides",
                PlatformAuditEvent.resource_id == str(workspace_id),
            )
        )
    )
    assert legacy_events == []
    assert canonical_events == []


@pytest.mark.parametrize(
    "quantity",
    [
        pytest.param(True, id="bool-true"),
        pytest.param(False, id="bool-false"),
        pytest.param(2.0, id="float"),
        pytest.param("2", id="string"),
        pytest.param(9_007_199_254_740_992, id="javascript-safe-integer-overflow"),
        pytest.param(9_223_372_036_854_775_808, id="postgres-bigint-overflow"),
    ],
)
async def test_quantitative_override_rejects_non_strict_or_unsafe_quantity(
    db_session, platform_client, quantity
):
    workspace = await _seed_workspace_with_plan(
        db_session, f"Override Strict Quantity {quantity!r}"
    )
    workspace_id = workspace.id

    response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace_id}/entitlement-overrides",
        json={
            "overrides": [
                {
                    "entitlementKey": "automations",
                    "action": "upsert",
                    "mode": "limit",
                    "quantity": quantity,
                    "reason": "Validação estrita da franquia quantitativa.",
                }
            ]
        },
    )

    assert response.status_code == 422
    assert (
        await WorkspaceEntitlementOverrideService(db_session).list_overrides(
            workspace_id
        )
        == []
    )
    assert (
        list(
            await db_session.scalars(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == workspace_id,
                    KlmAuditEvent.entity_type
                    == "workspace_entitlement_override",
                )
            )
        )
        == []
    )
    assert (
        list(
            await db_session.scalars(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == workspace_id,
                    KlmAuditEvent.entity_type
                    == "workspace_entitlement_override",
                )
            )
        )
        == []
    )
    assert (
        list(
            await db_session.scalars(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.resource_type
                    == "workspace_entitlement_overrides",
                    PlatformAuditEvent.resource_id == str(workspace_id),
                )
            )
        )
        == []
    )


async def test_quantitative_revoke_rejects_quantity_without_audit_side_effects(
    db_session, platform_client
):
    workspace = await _seed_workspace_with_plan(
        db_session, "Override Revoke Quantity WS"
    )
    workspace_id = workspace.id

    response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace_id}/entitlement-overrides",
        json={
            "overrides": [
                {
                    "entitlementKey": "tokens",
                    "action": "upsert",
                    "mode": "revoke",
                    "quantity": 5,
                    "reason": "Tentativa de revogação com quantidade contraditória.",
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "WS-ENT-OVR-003"
    assert "mode=revoke não aceita quantity" in response.json()["detail"]["message"]
    assert (
        await WorkspaceEntitlementOverrideService(db_session).list_overrides(
            workspace_id
        )
        == []
    )
    legacy_events = list(
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.entity_type == "workspace_entitlement_override",
            )
        )
    )
    assert legacy_events == []
    assert (
        list(
            await db_session.scalars(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.resource_type
                    == "workspace_entitlement_overrides",
                    PlatformAuditEvent.resource_id == str(workspace_id),
                )
            )
        )
        == []
    )


async def test_boolean_override_rejects_quantity_without_audit_side_effects(
    db_session, platform_client
):
    workspace = await _seed_workspace_with_plan(
        db_session, "Override Boolean Quantity WS"
    )
    workspace_id = workspace.id

    response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace_id}/entitlement-overrides",
        json={
            "overrides": [
                {
                    "entitlementKey": "digital_signature",
                    "action": "upsert",
                    "mode": "grant",
                    "quantity": 1,
                    "reason": "Tentativa de quantidade numa função booleana.",
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "WS-ENT-OVR-003"
    assert "não aceita quantity" in response.json()["detail"]["message"]
    assert (
        await WorkspaceEntitlementOverrideService(db_session).list_overrides(
            workspace_id
        )
        == []
    )
    assert (
        list(
            await db_session.scalars(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == workspace_id,
                    KlmAuditEvent.entity_type
                    == "workspace_entitlement_override",
                )
            )
        )
        == []
    )
    assert (
        list(
            await db_session.scalars(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.resource_type
                    == "workspace_entitlement_overrides",
                    PlatformAuditEvent.resource_id == str(workspace_id),
                )
            )
        )
        == []
    )


async def test_put_entitlement_overrides_rejects_unknown_action(db_session, platform_client):
    workspace = await _seed_workspace_with_plan(db_session, "Override Bad Action WS")
    response = await platform_client.put(
        f"/api/v1/platform/workspaces/{workspace.id}/entitlement-overrides",
        json={
            "overrides": [
                {
                    "entitlementKey": "tokens",
                    "action": "wipe",
                    "reason": "Ação inválida.",
                }
            ]
        },
    )
    assert response.status_code == 422
