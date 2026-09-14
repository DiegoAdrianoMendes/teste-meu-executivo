"""S22: suspender workspace invalida o cache de membership depois do commit."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.workers.platform_bulk_tasks as bulk_tasks
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.platform_bulk_service import PlatformBulkService


@pytest.mark.asyncio
async def test_suspend_marks_workspace_for_cache_invalidation(db_session):
    workspace = Workspace(
        name="Suspenso",
        slug=f"suspenso-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    service = PlatformBulkService(db_session)

    await service._apply_workspace_action(
        workspace,
        "suspend",
        {},
        actor=None,
        idempotency_key=f"bulk-{uuid4().hex[:8]}",
        reason="teste",
    )

    assert workspace.status == WorkspaceStatus.suspended
    assert workspace.workspaces_to_invalidate if False else service.workspaces_to_invalidate == {
        workspace.id
    }


@pytest.mark.asyncio
async def test_worker_invalidates_after_commit(monkeypatch):
    ordem: list[str] = []
    ws = uuid4()

    class FakeService:
        def __init__(self, db):
            self.workspaces_to_invalidate = {ws}

        async def run_job(self, job_id, *, max_items=None):
            return SimpleNamespace(status="completed")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            ordem.append("commit")

    async def invalidar(workspace_id):
        ordem.append(f"invalidate:{workspace_id}")

    monkeypatch.setattr(bulk_tasks, "PlatformBulkService", FakeService)
    monkeypatch.setattr(bulk_tasks, "invalidate_workspace_cache", invalidar)

    status = await bulk_tasks._async_run_job(uuid4(), session_factory=lambda: FakeSession())

    assert status == "completed"
    assert ordem == ["commit", f"invalidate:{ws}"], "pós-commit, nunca antes"
