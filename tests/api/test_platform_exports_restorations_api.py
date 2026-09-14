from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.main import app
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.platform_audit_event_service import PlatformAuditEventService
from app.services.platform_export_service import PlatformExportService
from app.services.s3_service import S3Service
from app.workers.platform_export_tasks import run_platform_export
from app.workers.platform_restoration_tasks import run_platform_restoration


def test_export_and_restoration_mutations_publish_snapshot_contracts() -> None:
    operations = app.openapi()["paths"]
    expected = {
        "/api/v1/platform/exports": "platform_export",
        "/api/v1/platform/exports/{job_id}/cancel": "platform_export",
        "/api/v1/platform/restorations/previews": "restoration_preview",
        "/api/v1/platform/restorations": "restoration_job",
        "/api/v1/platform/restorations/{job_id}/cancel": "restoration_job",
    }
    for path, adapter in expected.items():
        assert operations[path]["post"]["x-platform-mutation"]["snapshotAdapter"] == adapter


@pytest.mark.asyncio
async def test_export_request_and_cancel_have_before_after(
    platform_client,
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(run_platform_export, "apply_async", lambda *args, **kwargs: None)
    created = await platform_client.post(
        "/api/v1/platform/exports",
        headers={
            "Idempotency-Key": f"export-{uuid4()}",
            "X-Correlation-ID": "export-request-audit",
        },
        json={
            "dataset": "accounts",
            "format": "csv",
            "filters": {},
            "columns": ["conta", "status"],
        },
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["id"]
    cancelled = await platform_client.post(
        f"/api/v1/platform/exports/{job_id}/cancel",
        headers={"X-Correlation-ID": "export-cancel-audit"},
    )
    assert cancelled.status_code == 202, cancelled.text

    events = list(
        await db_session.scalars(
            select(PlatformAuditEvent)
            .where(
                PlatformAuditEvent.resource_type == "platform_export",
                PlatformAuditEvent.resource_id == job_id,
            )
            .order_by(PlatformAuditEvent.occurred_at, PlatformAuditEvent.id)
        )
    )
    assert len(events) == 2
    assert events[0].before is None
    assert events[0].after["status"] == "queued"
    assert events[0].correlation_id == "export-request-audit"
    assert events[1].before["status"] == "queued"
    assert events[1].after["status"] == "cancelled"
    assert events[1].correlation_id == "export-cancel-audit"


@pytest.mark.asyncio
async def test_export_dispatch_failure_is_visible_and_audited(
    platform_client,
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        run_platform_export,
        "apply_async",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("broker offline")),
    )
    created = await platform_client.post(
        "/api/v1/platform/exports",
        headers={"Idempotency-Key": f"export-dispatch-{uuid4()}"},
        json={"dataset": "accounts", "format": "csv", "filters": {}, "columns": []},
    )

    assert created.status_code == 202, created.text
    assert created.json()["status"] == "failed"
    assert created.json()["errorCode"] == "ASYNC_WORKER_UNAVAILABLE"
    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "platform_export",
            PlatformAuditEvent.resource_id == created.json()["id"],
            PlatformAuditEvent.action == "dispatch_failed",
        )
    )
    assert event is not None
    assert event.before["status"] == "queued"
    assert event.after["status"] == "failed"


@pytest.mark.asyncio
async def test_completed_export_expires_and_removes_private_file(
    platform_client,
    platform_auth_context,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "exports"))
    monkeypatch.setattr(run_platform_export, "apply_async", lambda *args, **kwargs: None)
    created = await platform_client.post(
        "/api/v1/platform/exports",
        headers={"Idempotency-Key": f"export-expiry-{uuid4()}"},
        json={"dataset": "accounts", "format": "csv", "filters": {}, "columns": []},
    )
    assert created.status_code == 202, created.text

    service = PlatformExportService(db_session)
    job = await service.run_job(created.json()["id"])
    assert job.status == "completed"
    private_file = service.local_artifact_path(
        job.storage_path or "",
        must_exist=True,
    )
    assert private_file.is_file()
    job.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    expired = await platform_client.get(f"/api/v1/platform/exports/{job.id}/download")
    assert expired.status_code == 410, expired.text
    assert expired.json()["detail"]["code"] == "PLATFORM_EXPORT_EXPIRED"
    assert not private_file.exists()

    audit = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "platform_export",
            PlatformAuditEvent.resource_id == str(job.id),
            PlatformAuditEvent.action == "expired",
        )
    )
    assert audit is not None


@pytest.mark.asyncio
async def test_download_revalidates_revoked_export_permission(
    platform_client,
    platform_auth_context,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "exports"))
    monkeypatch.setattr(run_platform_export, "apply_async", lambda *args, **kwargs: None)
    operator = User(
        name="Operador com exportação revogável",
        email=f"export-operator-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="OE",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_role_package="platform_admin",
        platform_access_active=True,
        platform_permission_grants=[],
        platform_permission_denials=[],
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(operator)
    await db_session.commit()
    platform_auth_context.user_id = operator.id

    created = await platform_client.post(
        "/api/v1/platform/exports",
        headers={"Idempotency-Key": f"export-revocation-{uuid4()}"},
        json={"dataset": "accounts", "format": "csv", "filters": {}, "columns": []},
    )
    assert created.status_code == 202, created.text
    job = await PlatformExportService(db_session).run_job(created.json()["id"])
    assert job.status == "completed"
    assert PlatformExportService.local_artifact_path(
        job.storage_path or "",
        must_exist=True,
    ).is_file()

    operator.platform_permission_denials = [{"module": "customers", "action": "export"}]
    await db_session.commit()

    denied = await platform_client.get(f"/api/v1/platform/exports/{job.id}/download")
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["code"] == "PLATFORM_EXPORT_FORBIDDEN"


@pytest.mark.asyncio
async def test_remote_download_reauthorizes_then_redirects_to_short_private_url(
    platform_client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    stored: dict[str, bytes] = {}

    def upload_private(_self, source, key, **_kwargs):
        stored[key] = source.read_bytes()
        return key

    def sign_private(_self, key, **kwargs):
        assert key in stored
        assert kwargs["authorization_granted"] is True
        assert kwargs["expires_in"] == 300
        return "https://private-s3.example/signed-download"

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", True)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "kms-key-id")
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "worker"))
    monkeypatch.setattr(S3Service, "upload_authorized_private_file", upload_private)
    monkeypatch.setattr(
        S3Service,
        "generate_authorized_private_download_url",
        sign_private,
    )
    monkeypatch.setattr(run_platform_export, "apply_async", lambda *args, **kwargs: None)
    created = await platform_client.post(
        "/api/v1/platform/exports",
        headers={"Idempotency-Key": f"export-remote-{uuid4()}"},
        json={"dataset": "accounts", "format": "csv", "filters": {}, "columns": []},
    )
    assert created.status_code == 202, created.text
    job = await PlatformExportService(db_session).run_job(created.json()["id"])
    assert job.status == "completed"
    assert job.storage_path and not job.storage_path.startswith("/")

    response = await platform_client.get(
        f"/api/v1/platform/exports/{job.id}/download",
        follow_redirects=False,
    )

    assert response.status_code == 307, response.text
    assert response.headers["location"] == "https://private-s3.example/signed-download"
    assert {
        directive.strip()
        for directive in response.headers["cache-control"].split(",")
    } == {"no-store", "private"}


@pytest.mark.asyncio
async def test_restoration_preview_is_canonically_audited(
    platform_client,
    platform_auth_context,
    db_session,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = Workspace(
        name="Conta para prévia de restauração",
        slug=f"restore-api-{uuid4().hex[:8]}",
        status=WorkspaceStatus.suspended,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=False,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot=actor.platform_role_package or "platform_owner",
            is_platform_root=True,
            session_type="normal",
            workspace_id=workspace.id,
            module="customers",
            action="workspace.suspend",
            resource_type="workspace",
            resource_id=str(workspace.id),
            resource_label_snapshot=workspace.name,
            result="success",
            before={"name": workspace.name, "status": "active", "isActive": True},
            after={"name": workspace.name, "status": "suspended", "isActive": False},
            reason="Suspensão que será restaurada no teste.",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="POST",
        )
    )
    await db_session.commit()

    response = await platform_client.post(
        "/api/v1/platform/restorations/previews",
        headers={"X-Correlation-ID": "restore-preview-audit"},
        json={"sourceAuditEventId": str(source.id)},
    )
    assert response.status_code == 201, response.text
    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "restoration_preview",
            PlatformAuditEvent.resource_id == response.json()["id"],
        )
    )
    assert event is not None
    assert event.before is None
    assert event.after["eligible"] is True
    assert event.correlation_id == "restore-preview-audit"


@pytest.mark.asyncio
async def test_restoration_dispatch_failure_is_visible_and_audited(
    platform_client,
    platform_auth_context,
    db_session,
    monkeypatch,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = Workspace(
        name="Conta para falha de despacho da restauração",
        slug=f"restore-dispatch-{uuid4().hex[:8]}",
        status=WorkspaceStatus.suspended,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=False,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    source = await PlatformAuditEventService(db_session).record(
        PlatformAuditEventCreate(
            actor_id=actor.id,
            actor_role_snapshot=actor.platform_role_package or "platform_owner",
            is_platform_root=True,
            session_type="normal",
            workspace_id=workspace.id,
            module="customers",
            action="workspace.suspend",
            resource_type="workspace",
            resource_id=str(workspace.id),
            resource_label_snapshot=workspace.name,
            result="success",
            before={"name": workspace.name, "status": "active", "isActive": True},
            after={"name": workspace.name, "status": "suspended", "isActive": False},
            reason="Suspensão usada para provar falha de despacho.",
            reason_source="user",
            correlation_id=str(uuid4()),
            operation_id=str(uuid4()),
            route="test",
            method="POST",
        )
    )
    await db_session.commit()
    preview_response = await platform_client.post(
        "/api/v1/platform/restorations/previews",
        json={"sourceAuditEventId": str(source.id)},
    )
    assert preview_response.status_code == 201, preview_response.text
    preview = preview_response.json()
    monkeypatch.setattr(
        run_platform_restoration,
        "apply_async",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("broker offline")),
    )
    created = await platform_client.post(
        "/api/v1/platform/restorations",
        headers={"Idempotency-Key": f"restore-dispatch-{uuid4()}"},
        json={
            "previewId": preview["id"],
            "expectedResourceVersion": preview["currentVersion"],
            "reason": "Provar indisponibilidade do worker de restauração.",
            "confirmation": f"RESTAURAR {workspace.id}",
        },
    )

    assert created.status_code == 202, created.text
    assert created.json()["status"] == "failed"
    assert created.json()["errorCode"] == "ASYNC_WORKER_UNAVAILABLE"
    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "restoration_job",
            PlatformAuditEvent.resource_id == created.json()["id"],
            PlatformAuditEvent.action == "dispatch_failed",
        )
    )
    assert event is not None
    assert event.before["status"] == "queued"
    assert event.after["status"] == "failed"
