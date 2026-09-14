from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.main import app
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_operations import PlatformSelectionSet
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.workers.platform_bulk_tasks import run_platform_bulk_job


def test_bulk_control_mutations_publish_canonical_snapshot_contracts() -> None:
    operations = app.openapi()["paths"]
    mutation_paths = {
        "/api/v1/platform/operations/selections": "bulk_selection",
        "/api/v1/platform/operations/bulk/previews": "bulk_preview",
        "/api/v1/platform/operations/bulk/jobs": "bulk_job",
        "/api/v1/platform/operations/bulk/jobs/{job_id}/cancel": "bulk_job",
        "/api/v1/platform/operations/bulk/jobs/{job_id}/retry-failures": "bulk_preview",
        "/api/v1/platform/operations/bulk/jobs/{job_id}/restoration-preview": "bulk_preview",
        "/api/v1/platform/operations/bulk/jobs/{job_id}/restore": "bulk_job",
    }
    for path, expected_adapter in mutation_paths.items():
        descriptor = operations[path]["post"]["x-platform-mutation"]
        assert descriptor["snapshotAdapter"] == expected_adapter, path


@pytest.mark.asyncio
async def test_selection_and_preview_are_audited_atomically(
    platform_client,
    db_session,
    monkeypatch,
) -> None:
    workspace = Workspace(
        name="Conta para lote auditável",
        slug=f"bulk-audit-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.commit()

    correlation = f"bulk-selection-{uuid4()}"
    created = await platform_client.post(
        "/api/v1/platform/operations/selections",
        headers={"X-Correlation-ID": correlation},
        json={
            "resourceType": "workspace",
            "mode": "explicit",
            "ids": [str(workspace.id)],
            "filters": {},
            "sort": [],
            "excludedIds": [],
        },
    )
    assert created.status_code == 201, created.text
    selection_id = created.json()["id"]

    preview = await platform_client.post(
        "/api/v1/platform/operations/bulk/previews",
        json={
            "selectionSetId": selection_id,
            "action": "suspend",
            "parameters": {},
        },
    )
    assert preview.status_code == 201, preview.text

    selection_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "bulk_selection",
            PlatformAuditEvent.resource_id == selection_id,
        )
    )
    assert selection_event is not None
    assert selection_event.correlation_id == correlation
    assert selection_event.before is None
    assert selection_event.after["targetCount"] == 1
    assert selection_event.after["filterKeys"] == []
    assert selection_event.route == "/api/v1/platform/operations/selections"

    preview_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "bulk_preview",
            PlatformAuditEvent.resource_id == preview.json()["id"],
        )
    )
    assert preview_event is not None
    assert preview_event.after["eligibleCount"] == 1
    assert preview_event.after["action"] == "suspend"

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    before_count = int(
        (await db_session.scalar(select(func.count(PlatformSelectionSet.id)))) or 0
    )
    monkeypatch.setattr(PlatformAuditEventService, "record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await platform_client.post(
            "/api/v1/platform/operations/selections",
            json={
                "resourceType": "workspace",
                "mode": "explicit",
                "ids": [str(workspace.id)],
                "filters": {},
                "sort": [],
                "excludedIds": [],
            },
        )
    after_count = int(
        (await db_session.scalar(select(func.count(PlatformSelectionSet.id)))) or 0
    )
    assert after_count == before_count


@pytest.mark.asyncio
async def test_bulk_dispatch_failure_is_terminal_visible_and_audited(
    platform_client,
    db_session,
    monkeypatch,
) -> None:
    workspace = Workspace(
        name="Conta para falha de despacho",
        slug=f"bulk-dispatch-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.commit()
    selection = await platform_client.post(
        "/api/v1/platform/operations/selections",
        json={
            "resourceType": "workspace",
            "mode": "explicit",
            "ids": [str(workspace.id)],
            "filters": {},
            "sort": [],
            "excludedIds": [],
        },
    )
    preview = await platform_client.post(
        "/api/v1/platform/operations/bulk/previews",
        json={
            "selectionSetId": selection.json()["id"],
            "action": "suspend",
            "parameters": {},
        },
    )
    monkeypatch.setattr(
        run_platform_bulk_job,
        "apply_async",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("broker offline")),
    )
    created = await platform_client.post(
        "/api/v1/platform/operations/bulk/jobs",
        headers={"Idempotency-Key": f"bulk-dispatch-{uuid4()}"},
        json={
            "previewId": preview.json()["id"],
            "reason": "Provar indisponibilidade do worker sem fila fantasma.",
            "confirmationPhrase": preview.json()["confirmationPhrase"],
        },
    )

    assert created.status_code == 202, created.text
    payload = created.json()
    assert payload["status"] == "failed"
    assert payload["processedCount"] == 1
    assert payload["failedCount"] == 1
    assert payload["items"][0]["errorCode"] == "ASYNC_WORKER_UNAVAILABLE"
    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "bulk_job",
            PlatformAuditEvent.resource_id == payload["id"],
            PlatformAuditEvent.action == "job.dispatch_failed",
        )
    )
    assert event is not None
    assert event.before["status"] == "queued"
    assert event.after["status"] == "failed"


@pytest.mark.asyncio
async def test_local_root_can_delay_bulk_dispatch_for_deterministic_cancellation(
    platform_client,
    db_session,
    monkeypatch,
) -> None:
    workspace = Workspace(
        name="Conta para cancelamento determinístico",
        slug=f"bulk-delay-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.commit()
    selection = await platform_client.post(
        "/api/v1/platform/operations/selections",
        json={
            "resourceType": "workspace",
            "mode": "explicit",
            "ids": [str(workspace.id)],
            "filters": {},
            "sort": [],
            "excludedIds": [],
        },
    )
    preview = await platform_client.post(
        "/api/v1/platform/operations/bulk/previews",
        json={
            "selectionSetId": selection.json()["id"],
            "action": "suspend",
            "parameters": {},
        },
    )
    dispatches: list[dict] = []

    def capture_dispatch(*_args, **kwargs):
        dispatches.append(kwargs)

    monkeypatch.setattr(run_platform_bulk_job, "apply_async", capture_dispatch)
    created = await platform_client.post(
        "/api/v1/platform/operations/bulk/jobs",
        headers={
            "Idempotency-Key": f"bulk-delay-{uuid4()}",
            "X-KLM-Local-Dispatch-Delay-Ms": "3000",
        },
        json={
            "previewId": preview.json()["id"],
            "reason": "Provar a janela local de cancelamento sem desligar o worker.",
            "confirmationPhrase": preview.json()["confirmationPhrase"],
        },
    )

    assert created.status_code == 202, created.text
    assert created.json()["status"] == "queued"
    assert dispatches == [{"args": [created.json()["id"]], "countdown": 3.0}]
