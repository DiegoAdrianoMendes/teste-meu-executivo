"""Fail-closed contracts for manually-created contextual AgentRuns."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.db.post_commit import _AGENT_RUN_WAKEUPS_KEY
from app.models.agent_platform import AgentDeployment, DeploymentStatus
from app.models.agent_run import AgentRunStatus
from app.models.client import Client
from app.models.finance_dimensional import FinanceCostCenter
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.agent_runtime_service import AgentRuntimeService


@pytest.mark.asyncio
async def test_workspace_run_does_not_invent_a_case_timeline_event(
    db_session,
):
    service = AgentRuntimeService(db_session)

    await service._append_timeline(
        matter_id=None,
        title="Execução de plataforma",
        description="Sem Caso 360 associado.",
    )

    assert not db_session.new


@pytest.fixture(autouse=True)
def verified_deployment(monkeypatch):
    """Keep these runtime-context tests independent of registry seed data.

    Deployment verification has its own integration suite.  These tests prove
    the fail-closed context, idempotency, dispatch and budget contracts after
    the deployment is already authoritatively verified.
    """
    monkeypatch.setattr(
        "app.services.agent_runtime_service.verify_deployment",
        AsyncMock(
            return_value={
                "definitionVerified": True,
                "roleVerified": True,
                "modelVerified": True,
                "knowledgeVerified": True,
                "autonomyVerified": True,
                "policyVerified": True,
                "entitlementVerified": True,
                "toolsVerified": True,
                "humanQueueVerified": True,
                "budgetVerified": True,
                "testPassed": True,
            }
        ),
    )


def _deployment(workspace_id, *, budget_policy=None):
    policy = budget_policy or {"monthlyTokenLimit": 10_000}
    return AgentDeployment(
        workspace_id=workspace_id,
        agent_key="finance-director",
        version=1,
        status=DeploymentStatus.active,
        config={
            "definitionVersion": 1,
            "rolePackage": "finance",
            "model": "test-model",
            "tools": ["finance.read"],
            "knowledgeSources": ["finance"],
            "autonomyPolicy": "A1",
            "humanRoutingPolicy": "finance",
            "budgetPolicy": policy,
            "testedAt": "2026-08-03T00:00:00Z",
        },
        gates={"published": True},
        tool_bindings=["finance.read"],
        role_package="finance",
        knowledge_bindings=["finance"],
        autonomy_policy={"level": "A1"},
        human_routing_policy={"primaryQueue": "finance"},
        budget_policy=policy,
    )


@pytest.mark.asyncio
async def test_context_run_requires_an_active_deployment(db_session, auth_context):
    center = FinanceCostCenter(
        workspace_id=auth_context.workspace_id,
        code="NO-DEPLOYMENT",
        name="Sem deployment",
    )
    db_session.add(center)
    await db_session.flush()

    with pytest.raises(ValueError, match="deployment ativo não encontrado"):
        await AgentRuntimeService(db_session).create_context_run(
            workspace_id=auth_context.workspace_id,
            context_type="finance",
            context_id=center.id,
            agent_name="finance-director",
            trigger_source="manual",
        )


@pytest.mark.asyncio
async def test_context_run_rejects_cross_workspace_record(db_session, auth_context):
    foreign_workspace = Workspace(
        name="Workspace estrangeiro",
        slug=f"foreign-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_client = Client(workspace_id=foreign_workspace.id, name="Cliente estrangeiro")
    db_session.add_all([foreign_client, _deployment(auth_context.workspace_id)])
    await db_session.flush()

    with pytest.raises(ValueError, match="Contexto não encontrado no workspace"):
        await AgentRuntimeService(db_session).create_context_run(
            workspace_id=auth_context.workspace_id,
            context_type="client",
            context_id=foreign_client.id,
            agent_name="finance-director",
            trigger_source="manual",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_type",
    ["matter", "client", "contract", "conversation", "task", "finance"],
)
async def test_context_run_rejects_every_foreign_context_type(
    db_session, auth_context, context_type
):
    """Each contextual lookup is independently fail-closed, not client-only."""
    with pytest.raises(ValueError, match="Contexto não encontrado no workspace"):
        await AgentRuntimeService(db_session).create_context_run(
            workspace_id=auth_context.workspace_id,
            context_type=context_type,
            context_id=uuid4(),
            agent_name="finance-director",
            trigger_source="manual",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("context_type", ["workspace", "platform"])
async def test_global_contexts_reject_ids_and_allow_only_explicit_none(
    db_session, auth_context, context_type
):
    with pytest.raises(ValueError, match="context_id deve ser omitido"):
        await AgentRuntimeService(db_session).create_context_run(
            workspace_id=auth_context.workspace_id,
            context_type=context_type,
            context_id=uuid4(),
            agent_name="finance-director",
            trigger_source="manual",
        )


@pytest.mark.asyncio
async def test_context_run_registers_post_commit_dispatch(db_session, auth_context):
    center = FinanceCostCenter(
        workspace_id=auth_context.workspace_id,
        code="DISPATCH",
        name="Dispatch",
    )
    deployment = _deployment(auth_context.workspace_id)
    db_session.add_all([center, deployment])
    await db_session.flush()

    run = await AgentRuntimeService(db_session).create_context_run(
        workspace_id=auth_context.workspace_id,
        context_type="finance",
        context_id=center.id,
        agent_name="finance-director",
        trigger_source="manual",
        dedupe_key=f"context-run:{uuid4()}",
    )

    assert run.status is AgentRunStatus.queued
    assert run.deployment_id == deployment.id
    assert run.celery_task_id
    assert (
        auth_context.workspace_id,
        run.id,
        run.celery_task_id,
    ) in db_session.info[_AGENT_RUN_WAKEUPS_KEY]


@pytest.mark.asyncio
async def test_budget_pause_creates_human_decision_without_dispatch(db_session, auth_context):
    center = FinanceCostCenter(
        workspace_id=auth_context.workspace_id,
        code="BUDGET",
        name="Budget",
    )
    deployment = _deployment(
        auth_context.workspace_id,
        budget_policy={"monthlyTokenLimit": 1},
    )
    db_session.add_all([center, deployment])
    await db_session.flush()

    # One completed run consumes the deployment's finite budget.
    from app.models.agent_run import AgentRun

    db_session.add(
        AgentRun(
            workspace_id=auth_context.workspace_id,
            context_type="finance",
            context_id=center.id,
            deployment_id=deployment.id,
            agent_name="finance-director",
            trigger_source="seed",
            status=AgentRunStatus.completed,
            input_tokens=1,
            output_tokens=0,
        )
    )
    await db_session.flush()

    run = await AgentRuntimeService(db_session).create_context_run(
        workspace_id=auth_context.workspace_id,
        context_type="finance",
        context_id=center.id,
        agent_name="finance-director",
        trigger_source="manual",
    )

    assert run.status is AgentRunStatus.waiting_human
    assert run.celery_task_id is None
    assert not db_session.info.get(_AGENT_RUN_WAKEUPS_KEY)
