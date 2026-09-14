"""Focused contract tests for the agent runtime V2 control plane."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.agent_platform import (
    agent_workspace_scope,
    cancel_runtime_run,
    create_deployment,
    flow_node_catalog,
    list_runtime_runs,
    provide_runtime_human_input,
    retry_runtime_run,
)
from app.core.deps import AuthContext
from app.models.agent_platform import AgentDeployment
from app.models.agent_run import AgentRun, AgentRunStatus
from app.schemas.agent_platform import AgentRunHumanInput, DeploymentCreate


def _auth(*, root: bool = False, workspace_id=None) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        workspace_id=workspace_id,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
        # The endpoint receives the already-authorized context; root is
        # represented by the same platform role used by the scope dependency.
        credential_source="test-root" if root else "test-staff",
    )


class _Db:
    def __init__(self, run=None):
        self.run = run
        self.events = []
        self.commits = 0
        self.added = []

    async def scalar(self, _query):
        return self.run

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        return None

    async def refresh(self, _value):
        return None


class _InputDb(_Db):
    def __init__(self, run, decision):
        super().__init__(run)
        self.decision = decision

    async def scalar(self, query):
        # The endpoint first locks the run, then the pending decision.
        entity = getattr(query, "column_descriptions", [{}])[0].get("entity")
        if entity is not None and entity.__name__ == "HumanDecision":
            return self.decision
        return self.run

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_cancel_runtime_run_is_audited_and_changes_only_nonterminal_run():
    workspace_id, run_id = uuid4(), uuid4()
    run = SimpleNamespace(id=run_id, workspace_id=workspace_id, status=AgentRunStatus.running)
    db = _Db(run)

    result = await cancel_runtime_run(run_id, workspace_id, db, _auth(workspace_id=workspace_id))

    assert result == {"id": str(run_id), "status": "cancelled"}
    assert run.status is AgentRunStatus.cancelled
    assert db.commits == 1
    assert any(getattr(event, "event_type", "") == "agent_run_cancelled" for event in db.added)


@pytest.mark.asyncio
async def test_cancel_and_retry_reject_invalid_states():
    workspace_id, run_id = uuid4(), uuid4()
    completed = SimpleNamespace(
        id=run_id, workspace_id=workspace_id, status=AgentRunStatus.completed
    )
    with pytest.raises(HTTPException) as cancel_error:
        await cancel_runtime_run(
            run_id, workspace_id, _Db(completed), _auth(workspace_id=workspace_id)
        )
    assert cancel_error.value.status_code == 409

    running = SimpleNamespace(
        id=run_id, workspace_id=workspace_id, status=AgentRunStatus.running, attempt_count=2
    )
    with pytest.raises(HTTPException) as retry_error:
        await retry_runtime_run(
            run_id, workspace_id, _Db(running), _auth(workspace_id=workspace_id)
        )
    assert retry_error.value.status_code == 409


@pytest.mark.asyncio
async def test_retry_requeues_failed_run_and_increments_attempt():
    workspace_id, run_id = uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id, workspace_id=workspace_id, status=AgentRunStatus.failed, attempt_count=2
    )
    db = _Db(run)

    result = await retry_runtime_run(run_id, workspace_id, db, _auth(workspace_id=workspace_id))

    assert result["status"] == "queued"
    assert result["attemptCount"] == 3
    assert run.status is AgentRunStatus.queued
    assert any(getattr(event, "event_type", "") == "agent_run_retried" for event in db.added)


@pytest.mark.asyncio
async def test_run_projection_never_returns_payload_or_error_details():
    workspace_id, run_id = uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id,
        workspace_id=workspace_id,
        agent_name="safe-agent",
        context_type="finance",
        context_id=uuid4(),
        matter_id=uuid4(),
        trigger_source="test",
        status=AgentRunStatus.failed,
        attempt_count=1,
        started_at=None,
        completed_at=None,
        created_at=None,
        updated_at=None,
        input_payload={"token": "secret"},
        output_payload={"private": "content"},
        error_message="provider credential leaked",
    )

    class _Rows(_Db):
        async def scalars(self, _query):
            return SimpleNamespace(all=lambda: [run])

        async def scalar(self, _query):
            return 1

    payload = await list_runtime_runs(
        workspace_id, _Rows(run), _auth(workspace_id=workspace_id), 25, 0
    )
    assert payload["items"][0]["contextType"] == "finance"
    assert {"inputPayload", "outputPayload", "errorMessage"}.isdisjoint(payload["items"][0])


@pytest.mark.asyncio
async def test_workspace_scope_rejects_cross_workspace_for_non_root():
    workspace_id = uuid4()
    auth = _auth(workspace_id=uuid4())

    class _ScopeDb:
        async def get(self, _model, _user_id):
            return SimpleNamespace(
                is_active=True,
                deleted_at=None,
                is_internal_staff=True,
                platform_access_active=True,
                is_platform_admin=True,
                platform_role_package="platform_admin",
            )

    with pytest.raises(HTTPException) as error:
        await agent_workspace_scope(workspace_id, auth, _ScopeDb())
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_deployment_persists_typed_bindings_from_config():
    workspace_id = uuid4()
    db = _Db()
    data = DeploymentCreate(
        agent_key="finance_director",
        role_package="finance",
        version=2,
        config={
            "model": "local-test-model",
            "tools": ["finance.read"],
            "knowledgeSources": ["workspace.finance"],
            "autonomyPolicy": "draft_only",
            "humanRoutingPolicy": "finance_review_v1",
            "budgetPolicy": {"monthlyTokenLimit": 1000},
        },
    )
    obj = await create_deployment(data, workspace_id, db, _auth(workspace_id=workspace_id))
    assert isinstance(obj, AgentDeployment)
    assert obj.role_package == "finance"
    assert obj.tool_bindings == ["finance.read"]
    assert obj.knowledge_bindings == ["workspace.finance"]
    assert obj.autonomy_policy == {"key": "draft_only"}
    assert obj.human_routing_policy == {"key": "finance_review_v1"}
    assert obj.budget_policy["monthlyTokenLimit"] == 1000


@pytest.mark.asyncio
async def test_deployment_rejects_human_policy_not_published_by_backend():
    workspace_id = uuid4()
    data = DeploymentCreate(
        agent_key="finance_director",
        role_package="finance",
        version=2,
        config={
            "model": "local-test-model",
            "tools": ["finance.read"],
            "knowledgeSources": ["workspace.finance"],
            "autonomyPolicy": "A1",
            "humanRoutingPolicy": "operator_typed_this_value",
            "budgetPolicy": {"monthlyTokenLimit": 1000},
        },
    )
    with pytest.raises(HTTPException) as error:
        await create_deployment(data, workspace_id, _Db(), _auth(workspace_id=workspace_id))
    assert error.value.status_code == 422
    assert "published policy catalog" in str(error.value.detail)


def test_flow_node_catalog_exposes_v3_execution_contract():
    catalog = flow_node_catalog()
    assert catalog
    required = {
        "capability_key",
        "risk",
        "required_permission",
        "approval_policy",
        "idempotency_policy",
        "retry_policy",
        "compensation_policy",
        "human_fallback",
    }
    assert required <= set(catalog[0])


@pytest.mark.asyncio
async def test_agent_run_v2_accepts_non_matter_finance_context(db_session, auth_context):
    run = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=None,
        context_type="finance",
        context_id=uuid4(),
        agent_name="finance-director",
        trigger_source="finance-alert",
        status=AgentRunStatus.queued,
    )
    db_session.add(run)
    await db_session.flush()

    assert run.id is not None
    assert run.matter_id is None
    assert run.context_type == "finance"


@pytest.mark.asyncio
async def test_human_input_is_allowlisted_single_use_and_resumes_waiting_run():
    workspace_id, run_id, decision_id = uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id,
        workspace_id=workspace_id,
        status=AgentRunStatus.waiting_data,
        input_payload={"existing": True},
    )
    decision = SimpleNamespace(
        id=decision_id,
        workspace_id=workspace_id,
        agent_run_id=run_id,
        status="pending",
        options={"inputFields": ["taxRegime"]},
        assigned_user_id=None,
        owner_user_id=None,
        expires_at=None,
    )
    db = _InputDb(run, decision)
    result = await provide_runtime_human_input(
        run_id,
        AgentRunHumanInput(values={"taxRegime": "simples"}),
        workspace_id,
        _auth(workspace_id=workspace_id),
        db,
    )
    assert result["status"] == "queued"
    assert run.input_payload["humanInput"] == {"taxRegime": "simples"}
    assert decision.status == "decided"


@pytest.mark.asyncio
async def test_human_input_rejects_undeclared_field_before_consuming_checkpoint():
    workspace_id, run_id = uuid4(), uuid4()
    run = SimpleNamespace(id=run_id, workspace_id=workspace_id, status=AgentRunStatus.waiting_human)
    decision = SimpleNamespace(
        id=uuid4(),
        workspace_id=workspace_id,
        agent_run_id=run_id,
        status="pending",
        options={"inputFields": ["answer"]},
        assigned_user_id=None,
        owner_user_id=None,
        expires_at=None,
    )
    with pytest.raises(HTTPException) as error:
        await provide_runtime_human_input(
            run_id,
            AgentRunHumanInput(values={"secret": "nope"}),
            workspace_id,
            _auth(workspace_id=workspace_id),
            _InputDb(run, decision),
        )
    assert error.value.status_code == 422
    assert decision.status == "pending"
