"""Explicit deployment fixtures for tests that exercise the agent runtime."""

from unittest.mock import AsyncMock

from app.models.agent_platform import AgentDeployment, DeploymentStatus


async def seed_verified_agent_deployments(db, workspace_id, agent_keys, monkeypatch) -> None:
    for agent_key in set(agent_keys):
        db.add(
            AgentDeployment(
                workspace_id=workspace_id,
                agent_key=agent_key,
                version=1,
                status=DeploymentStatus.active,
                config={
                    "definitionVersion": 1,
                    "rolePackage": "legal_operations",
                    "model": "test",
                    "testedAt": "2026-08-03T00:00:00Z",
                },
                role_package="legal_operations",
                gates={"published": True},
                budget_policy={"monthlyTokenLimit": 100_000},
            )
        )
    monkeypatch.setattr(
        "app.services.agent_runtime_service.verify_deployment",
        AsyncMock(return_value={"testPassed": True}),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime_service.validate_deployment_ready",
        lambda config, gates: None,
    )
    await db.flush()
