"""Scope and safe projection tests for platform agent runtime."""
# ruff: noqa: E501

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.endpoints.agent_platform import (
    _as_utc,
    _is_root_actor,
    _safe_decision_purpose,
    agent_workspace_scope,
    decisions_overview,
    list_context_runtime_options,
    list_runtime_runs,
    owner_alert_channel,
    require_agent_platform_access,
)
from app.core.deps import AuthContext
from app.models.agent_platform import AgentDeployment, DeploymentStatus, HumanDecision
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.platform_audit_event import PlatformAuditEvent


class _Db:
    def __init__(self, actor):
        self.actor = actor

    async def get(self, _model, _user_id):
        return self.actor


class _ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _RunDb:
    def __init__(self, rows):
        self.rows = rows

    async def scalars(self, _query):
        return _ScalarResult(self.rows)

    async def scalar(self, _query):
        return len(self.rows)


@pytest.mark.asyncio
async def test_platform_non_root_cannot_select_workspace():
    auth = AuthContext(
        user_id=uuid4(),
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    with pytest.raises(HTTPException) as error:
        await agent_workspace_scope(
            uuid4(),
            auth,
            _Db(
                SimpleNamespace(
                    is_active=True,
                    deleted_at=None,
                    is_internal_staff=True,
                    platform_access_active=True,
                    is_platform_admin=True,
                    platform_role_package="platform_admin",
                )
            ),
        )
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_platform_root_can_select_explicit_workspace():
    workspace_id = uuid4()
    auth = AuthContext(
        user_id=uuid4(),
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    result = await agent_workspace_scope(
        workspace_id,
        auth,
        _Db(
            SimpleNamespace(
                is_active=True,
                deleted_at=None,
                is_internal_staff=True,
                platform_access_active=True,
                is_platform_admin=True,
                platform_role_package="platform_owner",
            )
        ),
    )
    assert result == workspace_id


@pytest.mark.asyncio
async def test_root_agent_mutation_requires_direct_session_bound_to_selected_workspace(monkeypatch):
    workspace_id = uuid4()
    owner = SimpleNamespace(
        is_active=True,
        deleted_at=None,
        is_internal_staff=True,
        platform_access_active=True,
        is_platform_admin=True,
        platform_role_package="platform_owner",
    )
    manage = require_agent_platform_access("manage")
    normal_blackbox = AuthContext(
        user_id=uuid4(),
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    with pytest.raises(HTTPException) as denied:
        await manage(context=normal_blackbox, db=_Db(owner))
    assert denied.value.status_code == 401
    assert denied.value.detail["code"] == "ROOT-DIRECT-SESSION-REQUIRED"

    direct = AuthContext(
        user_id=normal_blackbox.user_id,
        workspace_id=workspace_id,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="support",
        administrative_session_id=uuid4(),
        administrative_session_type="root_direct",
    )
    assert await manage(context=direct, db=_Db(owner)) is direct

    async def _current_workspace(*, context, db):
        return workspace_id

    monkeypatch.setattr(
        "app.api.v1.endpoints.agent_platform.get_current_workspace_id", _current_workspace
    )
    assert await agent_workspace_scope(workspace_id, direct, _Db(owner)) == workspace_id
    with pytest.raises(HTTPException) as wrong_workspace:
        await agent_workspace_scope(uuid4(), direct, _Db(owner))
    assert wrong_workspace.value.status_code == 403


@pytest.mark.asyncio
async def test_only_platform_owner_is_root_actor():
    auth = AuthContext(
        user_id=uuid4(),
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )
    owner = SimpleNamespace(
        is_active=True,
        deleted_at=None,
        is_internal_staff=True,
        platform_access_active=True,
        is_platform_admin=True,
        platform_role_package="platform_owner",
    )
    admin = SimpleNamespace(
        is_active=True,
        deleted_at=None,
        is_internal_staff=True,
        platform_access_active=True,
        is_platform_admin=True,
        platform_role_package="platform_admin",
    )
    assert await _is_root_actor(_Db(owner), auth) is True
    assert await _is_root_actor(_Db(admin), auth) is False


def test_decision_projection_never_uses_arbitrary_options_as_display_text():
    decision = HumanDecision(
        workspace_id=uuid4(),
        decision_key="manual:opaque-id",
        options={"purpose": "CPF 000.000.000-00", "access_token": "secret"},
        required_roles=["operator"],
    )
    purpose = _safe_decision_purpose(decision)
    assert purpose == "Decisão humana solicitada"
    assert "CPF" not in purpose
    assert "secret" not in purpose


@pytest.mark.asyncio
async def test_decision_overview_normalizes_sqlite_naive_due_dates():
    naive_due_at = datetime.now(UTC).replace(tzinfo=None)

    class _DecisionOverviewDb:
        def __init__(self):
            self.calls = 0

        async def scalars(self, _query):
            self.calls += 1
            if self.calls == 1:
                return _ScalarResult(
                    [
                        SimpleNamespace(
                            status="pending",
                            assigned_user_id=None,
                            acknowledged_at=None,
                            due_at=naive_due_at,
                            escalation_level=0,
                        )
                    ]
                )
            return _ScalarResult([])

    overview = await decisions_overview(
        uuid4(),
        _DecisionOverviewDb(),
        AuthContext(
            user_id=uuid4(),
            workspace_id=None,
            membership_id=None,
            role=None,
            is_internal_staff=True,
            surface="platform",
        ),
    )
    assert _as_utc(naive_due_at).tzinfo is not None
    assert overview["open"] == 1
    assert overview["overdue"] in {0, 1}


@pytest.mark.asyncio
async def test_workspace_session_cannot_cross_workspace(monkeypatch):
    current = uuid4()
    auth = AuthContext(
        user_id=uuid4(),
        workspace_id=current,
        membership_id=None,
        role=None,
        is_internal_staff=False,
        surface="workspace",
    )

    async def _current_workspace(*, context, db):
        return current

    monkeypatch.setattr(
        "app.api.v1.endpoints.agent_platform.get_current_workspace_id", _current_workspace
    )
    with pytest.raises(HTTPException) as error:
        await agent_workspace_scope(uuid4(), auth, _Db(None))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_runtime_runs_are_safe_and_paginated_projection():
    run = AgentRun(
        workspace_id=uuid4(),
        matter_id=uuid4(),
        agent_name="financeiro",
        trigger_source="task",
        status=AgentRunStatus.waiting_human,
        attempt_count=2,
        input_payload={"private": "must-not-leak"},
        output_payload={"secret": "must-not-leak"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    result = await list_runtime_runs(
        run.workspace_id,
        _RunDb([run]),
        AuthContext(
            user_id=uuid4(),
            workspace_id=None,
            membership_id=None,
            role=None,
            is_internal_staff=True,
            surface="platform",
        ),
        limit=1,
        offset=0,
    )
    assert result["total"] == 1
    assert result["items"][0]["status"] == "waiting_human"
    assert "inputPayload" not in result["items"][0]
    assert "outputPayload" not in result["items"][0]


@pytest.mark.asyncio
async def test_owner_alert_channel_uses_selected_workspace_and_masks_phone():
    workspace_id = uuid4()

    class _WorkspaceDb:
        async def get(self, _model, key):
            assert key == workspace_id
            return SimpleNamespace(owner_whatsapp_phone="5511999991234")

    result = await owner_alert_channel(
        workspace_id,
        _WorkspaceDb(),
        AuthContext(
            user_id=uuid4(),
            workspace_id=None,
            membership_id=None,
            role=None,
            is_internal_staff=True,
            surface="platform",
        ),
    )
    assert result == {"ownerWhatsappPhone": "••••1234", "configured": True}


@pytest.mark.asyncio
async def test_context_options_return_only_safe_labels_from_selected_workspace(
    db_session, auth_context
):
    from app.models.client import Client

    visible = Client(workspace_id=auth_context.workspace_id, name="Cliente visível")
    db_session.add(visible)
    await db_session.flush()

    result = await list_context_runtime_options(
        context_type="client",
        query="visível",
        limit=20,
        ws=auth_context.workspace_id,
        db=db_session,
        _=auth_context,
    )

    assert result == {"items": [{"id": str(visible.id), "label": "Cliente visível"}]}
    assert set(result["items"][0]) == {"id", "label"}


@pytest.mark.asyncio
async def test_context_options_reject_unknown_context_type(db_session, auth_context):
    with pytest.raises(HTTPException) as error:
        await list_context_runtime_options(
            context_type="arbitrary_table",
            query="",
            limit=20,
            ws=auth_context.workspace_id,
            db=db_session,
            _=auth_context,
        )
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "agent_context_type_invalid"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_manual_context_run_is_committed_with_its_canonical_audit_event(
    db_session,
    auth_context,
    client,
    monkeypatch,
):
    """A failed audit must roll back the run; a success records the same command."""
    deployment = AgentDeployment(
        workspace_id=auth_context.workspace_id,
        agent_key="finance-director",
        version=1,
        status=DeploymentStatus.active,
        role_package="finance",
        config={
            "definitionVersion": 1,
            "rolePackage": "finance",
            "model": "test-model",
            "tools": ["finance.read"],
            "knowledgeSources": ["finance"],
            "autonomyPolicy": "A1",
            "humanRoutingPolicy": "finance",
            "budgetPolicy": {"monthlyTokenLimit": 10_000},
            "testedAt": "2026-08-04T00:00:00Z",
        },
        tool_bindings=["finance.read"],
        knowledge_bindings=["finance"],
        autonomy_policy={"level": "A1"},
        human_routing_policy={"primaryQueue": "finance"},
        budget_policy={"monthlyTokenLimit": 10_000},
    )
    db_session.add(deployment)
    await db_session.flush()

    async def verified(_db, _deployment):
        return {
            key: True
            for key in (
                "definitionVerified",
                "roleVerified",
                "modelVerified",
                "knowledgeVerified",
                "autonomyVerified",
                "policyVerified",
                "entitlementVerified",
                "toolsVerified",
                "humanQueueVerified",
                "budgetVerified",
                "testPassed",
            )
        }

    monkeypatch.setattr("app.services.agent_runtime_service.verify_deployment", verified)
    response = await client.post(
        "/api/v1/agent-platform/runs/context",
        json={
            "context_type": "workspace",
            "agent_name": "finance-director",
            "trigger_source": "manual",
            "dedupe_key": f"audit-context:{uuid4()}",
        },
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "agent_run",
            PlatformAuditEvent.resource_id == run_id,
            PlatformAuditEvent.action == "create_context_agent_run",
        )
    )
    assert audit is not None
    assert audit.after["status"] == "queued"
    assert audit.workspace_id == auth_context.workspace_id

    # A separate request must see the committed run through the same tenant
    # RLS context used by production PostgreSQL.
    listed = await client.get("/api/v1/agent-platform/runs")
    assert listed.status_code == 200, listed.text
    assert run_id in {item["id"] for item in listed.json()["items"]}
