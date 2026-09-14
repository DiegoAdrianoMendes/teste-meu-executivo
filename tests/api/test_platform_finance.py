"""Administrative finance endpoints keep an explicit tenant boundary."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.deps import get_current_auth_context
from app.main import app
from app.models.auth_security import AdministrativeSession, MfaVerificationMethod
from app.models.finance_dimensional import FinanceExportJob, FinanceTaxProfile
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus


async def _workspace(db_session, label: str) -> Workspace:
    workspace = Workspace(
        name=label,
        slug=f"platform-finance-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.commit()
    return workspace


async def _enter_root_direct_finance_workspace(db_session, auth, workspace: Workspace) -> None:
    session = AdministrativeSession(
        id=uuid4(),
        user_id=auth.user_id,
        token_jti=f"test-finance-{uuid4().hex}",
        mfa_method=MfaVerificationMethod.challenge,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db_session.add(session)
    await db_session.commit()
    context = replace(
        auth,
        workspace_id=workspace.id,
        administrative_session_id=session.id,
        administrative_session_type="root_direct",
    )

    async def override_context():
        return context

    app.dependency_overrides[get_current_auth_context] = override_context


@pytest.mark.asyncio
async def test_platform_finance_reads_selected_workspace_without_impersonation(
    platform_client,
    db_session,
    platform_auth_context,
) -> None:
    workspace = await _workspace(db_session, "Conta Financeira Alfa")
    await _enter_root_direct_finance_workspace(db_session, platform_auth_context, workspace)

    executive = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/executive"
    )
    assert executive.status_code == 200, executive.text
    assert executive.json()["arr"]["source"]
    assert executive.json()["arr"]["quality"] in {
        "verified",
        "derived",
        "partial",
        "insufficient_data",
    }

    drilldown = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/drilldown",
        json={"limit": 50, "offset": 0},
    )
    assert drilldown.status_code == 200, drilldown.text
    assert drilldown.json()["workspaceId"] == str(workspace.id)
    assert drilldown.json()["items"] == []

    unknown = uuid4()
    rejected = await platform_client.get(f"/api/v1/platform/finance/workspaces/{unknown}/executive")
    assert rejected.status_code == 403
    assert rejected.json()["detail"]["code"] == "PLATFORM-FINANCE-SESSION-WORKSPACE-MISMATCH"


@pytest.mark.asyncio
async def test_platform_finance_export_lifecycle_is_scoped_to_selected_workspace(
    platform_client,
    db_session,
    monkeypatch,
    platform_auth_context,
) -> None:
    workspace = await _workspace(db_session, "Conta Exportadora")
    other_workspace = await _workspace(db_session, "Outra Conta")
    await _enter_root_direct_finance_workspace(db_session, platform_auth_context, workspace)
    dispatched: list[tuple[list[str], bool]] = []

    def fake_apply_async(*, args, retry):
        dispatched.append((args, retry))

    monkeypatch.setattr(
        "app.workers.finance_export_tasks.run_finance_export.apply_async",
        fake_apply_async,
    )
    created = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/exports",
        json={"format": "xlsx", "drill_context": {"source": "blackbox-finance"}},
    )
    assert created.status_code == 202, created.text
    job = created.json()
    assert job["workspaceId"] == str(workspace.id)
    assert job["status"] == "queued"
    assert dispatched == [([job["id"], str(workspace.id)], False)]
    created_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "finance_export_job",
            PlatformAuditEvent.resource_id == job["id"],
        )
    )
    assert created_event is not None
    assert created_event.before is None
    assert created_event.after["workspaceId"] == str(workspace.id)

    listed = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/exports"
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [job["id"]]
    assert listed.json()["total"] == 1

    cross_workspace = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{other_workspace.id}/exports/{job['id']}"
    )
    assert cross_workspace.status_code == 403
    assert cross_workspace.json()["detail"]["code"] == "PLATFORM-FINANCE-SESSION-WORKSPACE-MISMATCH"

    unavailable = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/exports/{job['id']}/download"
    )
    assert unavailable.status_code == 409

    cancelled = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/exports/{job['id']}/cancel"
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    events = list(
        (
            await db_session.scalars(
                select(PlatformAuditEvent)
                .where(
                    PlatformAuditEvent.resource_type == "finance_export_job",
                    PlatformAuditEvent.resource_id == job["id"],
                )
                .order_by(PlatformAuditEvent.occurred_at)
            )
        ).all()
    )
    assert len(events) == 2
    assert events[-1].before["status"] == "queued"
    assert events[-1].after["status"] == "cancelled"


@pytest.mark.asyncio
async def test_finance_export_keeps_durable_queued_intent_when_initial_broker_wakeup_fails(
    platform_client,
    db_session,
    monkeypatch,
    platform_auth_context,
) -> None:
    workspace = await _workspace(db_session, "Conta com broker indisponível")
    await _enter_root_direct_finance_workspace(db_session, platform_auth_context, workspace)

    def unavailable(**_kwargs):
        raise ConnectionError("broker offline")

    monkeypatch.setattr(
        "app.workers.finance_export_tasks.run_finance_export.apply_async", unavailable
    )
    response = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/exports",
        json={"format": "pdf", "drill_context": {}},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "queued"
    persisted = await db_session.get(FinanceExportJob, job["id"])
    assert persisted is not None
    assert persisted.status == "queued"
    assert persisted.error_detail is None


@pytest.mark.asyncio
async def test_expired_finance_export_records_canonical_lifecycle_event(
    platform_client,
    db_session,
    auth_context,
    platform_auth_context,
) -> None:
    workspace = await _workspace(db_session, "Conta com export expirado")
    await _enter_root_direct_finance_workspace(db_session, platform_auth_context, workspace)
    job = FinanceExportJob(
        workspace_id=workspace.id,
        actor_user_id=auth_context.user_id,
        format="pdf",
        status="completed",
        drill_context={},
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(job)
    await db_session.commit()

    response = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/exports/{job.id}/download"
    )
    assert response.status_code == 410
    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "finance_export_job",
            PlatformAuditEvent.resource_id == str(job.id),
            PlatformAuditEvent.action == "export.expired",
        )
    )
    assert event is not None
    assert event.before["status"] == "completed"
    assert event.after["status"] == "expired"


@pytest.mark.asyncio
async def test_platform_finance_cross_account_analytics_routes_and_export_pagination(
    platform_client, db_session, monkeypatch, platform_auth_context
) -> None:
    workspace = await _workspace(db_session, "Conta analítica")
    # A plain Blackbox context cannot operate tenant finance.
    forbidden = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/forecasts/generate",
        json={"as_of": "2026-08-04", "horizon": "30d", "scenarios": ["base"]},
    )
    assert forbidden.status_code == 403
    await _enter_root_direct_finance_workspace(db_session, platform_auth_context, workspace)
    dre = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/dre",
        json={"mode": "cash", "period_start": "2026-01-01", "period_end": "2026-08-04"},
    )
    assert dre.status_code == 200, dre.text
    forecast = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/forecasts/generate",
        json={"as_of": "2026-08-04", "horizon": "30d", "scenarios": ["base"]},
    )
    assert forecast.status_code == 200, forecast.text
    valuation = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/valuations/calculate",
        json={
            "method": "revenue_multiple",
            "as_of": "2026-08-04",
            "assumptions": {"multiple": "1"},
        },
    )
    assert valuation.status_code == 200, valuation.text
    tax = await platform_client.put(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/tax-profile",
        json={
            "regime": "lucro_real",
            "effective_from": "2026-01-01",
            "sources": [{"source": "local"}],
        },
    )
    assert tax.status_code == 200, tax.text
    history = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/tax-profile"
    )
    assert history.status_code == 200 and len(history.json()) == 1
    reform = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/tax-reform/simulate",
        json={
            "annual_revenue": "100000",
            "annual_expense": "30000",
            "transition_rates": {"2027": "10"},
        },
    )
    assert reform.status_code == 200, reform.text
    insights = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/insights"
    )
    assert insights.status_code == 200 and isinstance(insights.json()["humanReviewRequired"], bool)
    budgets = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/budget-lines"
    )
    assert budgets.status_code == 200 and isinstance(budgets.json(), list)

    monkeypatch.setattr(
        "app.workers.finance_export_tasks.run_finance_export.apply_async", lambda **_: None
    )
    for _ in range(2):
        response = await platform_client.post(
            f"/api/v1/platform/finance/workspaces/{workspace.id}/exports",
            json={"format": "pdf", "drill_context": {}},
        )
        assert response.status_code == 202
    page = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/exports?limit=1&offset=1"
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2 and page.json()["limit"] == 1 and page.json()["offset"] == 1
    assert len(page.json()["items"]) == 1 and page.json()["hasMore"] is False
    app.dependency_overrides.pop(get_current_auth_context, None)


@pytest.mark.asyncio
async def test_platform_finance_audit_uses_real_tax_before_and_null_for_creations(
    platform_client, db_session, monkeypatch, platform_auth_context
) -> None:
    workspace = await _workspace(db_session, "Conta com auditoria financeira")
    await _enter_root_direct_finance_workspace(db_session, platform_auth_context, workspace)

    created_tax = await platform_client.put(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/tax-profile",
        json={
            "regime": "lucro_real",
            "tax_rate": "10",
            "effective_from": "2026-01-01",
            "sources": [{"source": "official-catalog"}],
        },
    )
    assert created_tax.status_code == 200, created_tax.text
    created_tax_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "FinanceTaxProfile",
            PlatformAuditEvent.resource_id == created_tax.json()["id"],
            PlatformAuditEvent.action == "tax_profile.update",
        )
    )
    assert created_tax_event is not None
    assert created_tax_event.before is None

    updated_tax = await platform_client.put(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/tax-profile",
        json={
            "regime": "lucro_real",
            "tax_rate": "12",
            "effective_from": "2026-08-01",
            "expected_version": created_tax.json()["version"],
            "sources": [{"source": "official-catalog"}],
        },
    )
    assert updated_tax.status_code == 200, updated_tax.text
    updated_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "FinanceTaxProfile",
            PlatformAuditEvent.resource_id == updated_tax.json()["id"],
            PlatformAuditEvent.action == "tax_profile.update",
        )
    )
    assert updated_event is not None
    assert Decimal(updated_event.before["tax_rate"]) == Decimal("10")
    assert Decimal(updated_event.after["tax_rate"]) == Decimal("12")
    assert "tax_rate" in updated_event.changed_paths

    forecast = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/forecasts/generate",
        json={"as_of": "2026-08-04", "horizon": "30d", "scenarios": ["base"]},
    )
    assert forecast.status_code == 200, forecast.text
    valuation = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/valuations/calculate",
        json={
            "method": "revenue_multiple",
            "as_of": "2026-08-04",
            "assumptions": {"multiple": "1"},
        },
    )
    assert valuation.status_code == 200, valuation.text
    budget = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/budget-lines",
        json={
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "entry_type": "expense",
            "amount": "100",
        },
    )
    assert budget.status_code == 200, budget.text
    for resource_type, resource_id in (
        ("FinanceForecast", forecast.json()[0]["id"]),
        ("FinanceValuation", valuation.json()["id"]),
        ("FinanceBudgetLine", budget.json()["id"]),
    ):
        event = await db_session.scalar(
            select(PlatformAuditEvent).where(
                PlatformAuditEvent.resource_type == resource_type,
                PlatformAuditEvent.resource_id == resource_id,
            )
        )
        assert event is not None
        assert event.before is None
        assert event.after is not None


@pytest.mark.asyncio
async def test_tax_profile_mutation_and_canonical_audit_rollback_together_on_audit_failure(
    platform_client, db_session, monkeypatch, platform_auth_context
) -> None:
    workspace = await _workspace(db_session, "Conta de rollback tributário")
    await _enter_root_direct_finance_workspace(db_session, platform_auth_context, workspace)
    created = await platform_client.put(
        f"/api/v1/platform/finance/workspaces/{workspace.id}/tax-profile",
        json={
            "regime": "lucro_real",
            "tax_rate": "10",
            "effective_from": "2026-01-01",
            "sources": [{"source": "official-catalog"}],
        },
    )
    assert created.status_code == 200, created.text

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("injected canonical audit failure")

    monkeypatch.setattr(
        "app.api.v1.endpoints.platform_admin.record_canonical_platform_mutation", fail_audit
    )
    with pytest.raises(RuntimeError, match="injected canonical audit failure"):
        await platform_client.put(
            f"/api/v1/platform/finance/workspaces/{workspace.id}/tax-profile",
            json={
                "regime": "lucro_real",
                "tax_rate": "12",
                "effective_from": "2026-08-01",
                "expected_version": created.json()["version"],
                "sources": [{"source": "official-catalog"}],
            },
        )

    await db_session.rollback()
    profiles = list(
        (
            await db_session.scalars(
                select(FinanceTaxProfile)
                .where(FinanceTaxProfile.workspace_id == workspace.id)
                .order_by(FinanceTaxProfile.effective_from)
            )
        ).all()
    )
    assert len(profiles) == 1
    assert profiles[0].effective_until is None
    assert Decimal(profiles[0].tax_rate) == Decimal("10")
    audit_events = list(
        (
            await db_session.scalars(
                select(PlatformAuditEvent).where(
                    PlatformAuditEvent.workspace_id == workspace.id,
                    PlatformAuditEvent.action == "tax_profile.update",
                )
            )
        ).all()
    )
    assert len(audit_events) == 1


@pytest.mark.asyncio
async def test_platform_finance_requires_matching_session_for_reads_exports_and_writes(
    platform_client, db_session, platform_auth_context
) -> None:
    workspace_a = await _workspace(db_session, "Conta Financeira A")
    workspace_b = await _workspace(db_session, "Conta Financeira B")
    root_a = str(workspace_a.id)
    root_b = str(workspace_b.id)

    no_session = await platform_client.get(
        f"/api/v1/platform/finance/workspaces/{root_a}/executive"
    )
    assert no_session.status_code == 403
    no_session_export = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{root_a}/exports",
        json={"format": "pdf", "drill_context": {}},
    )
    assert no_session_export.status_code == 403

    await _enter_root_direct_finance_workspace(db_session, platform_auth_context, workspace_a)
    allowed = await platform_client.post(
        f"/api/v1/platform/finance/workspaces/{root_a}/forecasts/generate",
        json={"as_of": "2026-08-04", "horizon": "30d", "scenarios": ["base"]},
    )
    assert allowed.status_code == 200, allowed.text

    cross_workspace_requests = (
        (
            "post",
            f"/api/v1/platform/finance/workspaces/{root_b}/forecasts/generate",
            {"as_of": "2026-08-04", "horizon": "30d", "scenarios": ["base"]},
        ),
        (
            "post",
            f"/api/v1/platform/finance/workspaces/{root_b}/valuations/calculate",
            {"method": "revenue_multiple", "as_of": "2026-08-04", "assumptions": {"multiple": "1"}},
        ),
        (
            "put",
            f"/api/v1/platform/finance/workspaces/{root_b}/tax-profile",
            {
                "regime": "lucro_real",
                "effective_from": "2026-01-01",
                "sources": [{"source": "test"}],
            },
        ),
        (
            "post",
            f"/api/v1/platform/finance/workspaces/{root_b}/budget-lines",
            {
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "amount": "100",
                "category": "ops",
                "entryType": "expense",
            },
        ),
    )
    for method, path, payload in cross_workspace_requests:
        response = await getattr(platform_client, method)(path, json=payload)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "PLATFORM-FINANCE-SESSION-WORKSPACE-MISMATCH"

    for path in (
        f"/api/v1/platform/finance/workspaces/{root_b}/executive",
        f"/api/v1/platform/finance/workspaces/{root_b}/tax-profile",
        f"/api/v1/platform/finance/workspaces/{root_b}/budget-lines",
        f"/api/v1/platform/finance/workspaces/{root_b}/exports",
    ):
        response = await platform_client.get(path)
        assert response.status_code == 403, response.text
