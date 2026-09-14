from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.platform_export_service import PlatformExportError, PlatformExportService


def test_billing_export_accepts_the_documented_finance_permission() -> None:
    finance_actor = User(
        name="Financeiro com exportação",
        email="finance-export@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="FE",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_role_package="sales_sdr",
        platform_access_active=True,
        platform_permission_grants=[{"module": "finance", "action": "export"}],
        platform_permission_denials=[],
    )

    PlatformExportService.authorize(finance_actor, "billing")
    with pytest.raises(PlatformExportError) as captured:
        PlatformExportService.authorize(finance_actor, "accounts")
    assert captured.value.status_code == 403


@pytest.mark.asyncio
async def test_export_is_private_async_safe_and_idempotent(
    db_session, platform_auth_context, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "exports"))
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = Workspace(
        name="=Fórmula proibida",
        slug=f"export-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    service = PlatformExportService(db_session)

    job = await service.create_job(
        actor=actor,
        dataset="accounts",
        format="csv",
        filters={"status": "active"},
        columns=["conta", "status"],
        selection_set_id=None,
        idempotency_key="export-idempotency-test",
    )
    duplicate = await service.create_job(
        actor=actor,
        dataset="accounts",
        format="csv",
        filters={"status": "active"},
        columns=["conta", "status"],
        selection_set_id=None,
        idempotency_key="export-idempotency-test",
    )
    assert duplicate.id == job.id

    completed = await service.run_job(job.id)
    assert completed.status == "completed"
    assert completed.row_count is not None and completed.row_count >= 1
    assert completed.checksum_sha256 and len(completed.checksum_sha256) == 64
    assert completed.storage_path == (
        f"platform-exports/{completed.id}/{completed.id}.csv"
    )
    assert not completed.storage_path.startswith("/")
    path = service.local_artifact_path(completed.storage_path, must_exist=True)
    assert path.is_file()
    assert (tmp_path / "exports").resolve() in path.parents
    assert "'=Fórmula proibida" in path.read_text(encoding="utf-8-sig")


@pytest.mark.asyncio
async def test_export_rejects_unknown_filter_and_column(db_session, platform_auth_context):
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    service = PlatformExportService(db_session)

    with pytest.raises(PlatformExportError, match="Filtros não suportados"):
        await service.create_job(
            actor=actor,
            dataset="accounts",
            format="csv",
            filters={"unsafe": "value"},
            columns=[],
            selection_set_id=None,
            idempotency_key="invalid-filter",
        )
    with pytest.raises(PlatformExportError, match="Colunas não suportadas"):
        await service.create_job(
            actor=actor,
            dataset="accounts",
            format="csv",
            filters={},
            columns=["secret"],
            selection_set_id=None,
            idempotency_key="invalid-column",
        )


@pytest.mark.asyncio
async def test_ticket_export_preserves_support_center_filters(
    db_session, platform_auth_context, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "exports"))
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    workspace = Workspace(
        name="Conta filtrada",
        slug=f"ticket-export-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(SupportTicket(
        public_number=f"SAC-EXPORT-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        reporter_user_id=actor.id,
        assigned_to_user_id=actor.id,
        ticket_type=SupportTicketType.problem,
        priority=SupportTicketPriority.urgent,
        severity=SupportTicketSeverity.critical,
        queue=SupportTicketQueue.technical,
        status=SupportTicketStatus.triage,
        subject="Falha crítica exportável",
        description="Conteúdo privado não deve integrar a planilha.",
        technical_context={},
        idempotency_key=f"ticket-export-{uuid4().hex}",
        first_response_due_at=now - timedelta(hours=1),
        resolution_due_at=now + timedelta(hours=3),
    ))
    await db_session.flush()

    service = PlatformExportService(db_session)
    job = await service.create_job(
        actor=actor,
        dataset="tickets",
        format="csv",
        filters={
            "workspaceId": str(workspace.id),
            "statuses": ["triage"],
            "queues": ["technical"],
            "severities": ["critical"],
            "ticketTypes": ["problem"],
            "assignedToUserId": str(actor.id),
            "overdue": True,
            "search": "exportável",
        },
        columns=[],
        selection_set_id=None,
        idempotency_key="ticket-filter-export",
    )
    completed = await service.run_job(job.id)

    assert completed.status == "completed"
    assert completed.row_count == 1
    exported = service.local_artifact_path(
        completed.storage_path or "",
        must_exist=True,
    ).read_text(encoding="utf-8-sig")
    assert "Falha crítica exportável" in exported
    assert "Conteúdo privado" not in exported


@pytest.mark.asyncio
async def test_remote_export_persists_only_logical_key_and_streams_to_shared_storage(
    db_session,
    platform_auth_context,
    tmp_path,
    monkeypatch,
):
    class FakePrivateStorage:
        def __init__(self):
            self.objects: dict[str, bytes] = {}
            self.upload_call: dict | None = None
            self.download_call: dict | None = None
            self.delete_call: dict | None = None

        def upload_authorized_private_file(
            self,
            source,
            key,
            **kwargs,
        ):
            self.objects[key] = source.read_bytes()
            self.upload_call = {"key": key, **kwargs}
            return key

        def generate_authorized_private_download_url(self, key, **kwargs):
            assert key in self.objects
            self.download_call = {"key": key, **kwargs}
            return "https://private-s3.example/signed-export"

        def delete_authorized_private_object(self, key, **kwargs):
            self.delete_call = {"key": key, **kwargs}
            self.objects.pop(key, None)

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", True)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "kms-key-id")
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "worker"))
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    storage = FakePrivateStorage()
    service = PlatformExportService(db_session, s3=storage)  # type: ignore[arg-type]
    job = await service.create_job(
        actor=actor,
        dataset="accounts",
        format="csv",
        filters={},
        columns=["conta", "status"],
        selection_set_id=None,
        idempotency_key="remote-export-logical-key",
    )

    completed = await service.run_job(job.id)

    assert completed.status == "completed"
    assert completed.storage_path == (
        f"platform-exports/{completed.id}/{completed.id}.csv"
    )
    assert completed.storage_path in storage.objects
    assert storage.upload_call is not None
    assert storage.upload_call["state"] == "clean"
    assert storage.upload_call["authorization_granted"] is True
    assert storage.upload_call["checksum_sha256"] == completed.checksum_sha256
    assert not (tmp_path / "worker" / completed.storage_path).exists()

    download = await service.authorize_download(completed)
    assert download.local_path is None
    assert download.signed_url == "https://private-s3.example/signed-export"
    assert storage.download_call is not None
    assert storage.download_call["authorization_granted"] is True
    assert storage.download_call["expires_in"] == (
        settings.PLATFORM_EXPORT_DOWNLOAD_URL_EXPIRY_SECONDS
    )

    await service.delete_artifact(completed.storage_path)
    assert storage.delete_call == {
        "key": completed.storage_path,
        "authorization_granted": True,
    }
    assert completed.storage_path not in storage.objects


@pytest.mark.asyncio
async def test_production_export_does_not_inherit_external_aws_gate(
    db_session,
    platform_auth_context,
    tmp_path,
    monkeypatch,
):
    class UnexpectedStorage:
        def __init__(self):
            self.upload_called = False
            self.download_called = False
            self.delete_called = False

        def upload_authorized_private_file(self, *_args, **_kwargs):
            self.upload_called = True
            raise AssertionError("External delivery must not authorize private exports.")

        def generate_authorized_private_download_url(self, *_args, **_kwargs):
            self.download_called = True
            raise AssertionError("External delivery must not authorize private downloads.")

        def delete_authorized_private_object(self, *_args, **_kwargs):
            self.delete_called = True
            raise AssertionError("External delivery must not authorize private deletion.")

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", False)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "kms-key-id")
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "worker"))
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    storage = UnexpectedStorage()
    service = PlatformExportService(db_session, s3=storage)  # type: ignore[arg-type]
    job = await service.create_job(
        actor=actor,
        dataset="accounts",
        format="csv",
        filters={},
        columns=["conta", "status"],
        selection_set_id=None,
        idempotency_key="private-storage-gate-required",
    )

    failed = await service.run_job(job.id)

    assert failed.status == "failed"
    assert failed.error_code == "PLATFORM_EXPORT_STORAGE_UNAVAILABLE"
    assert storage.upload_called is False

    failed.storage_path = f"platform-exports/{failed.id}/{failed.id}.csv"
    with pytest.raises(PlatformExportError) as download_blocked:
        await service.authorize_download(failed)
    assert download_blocked.value.code == "PLATFORM_EXPORT_STORAGE_UNAVAILABLE"
    with pytest.raises(PlatformExportError) as delete_blocked:
        await service.delete_artifact(failed.storage_path)
    assert delete_blocked.value.code == "PLATFORM_EXPORT_STORAGE_UNAVAILABLE"
    assert storage.download_called is False
    assert storage.delete_called is False


@pytest.mark.asyncio
async def test_export_failure_persists_only_allowlisted_safe_detail(
    db_session,
    platform_auth_context,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "exports"))
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    service = PlatformExportService(db_session)
    job = await service.create_job(
        actor=actor,
        dataset="accounts",
        format="csv",
        filters={},
        columns=["conta", "status"],
        selection_set_id=None,
        idempotency_key="export-safe-failure-detail",
    )
    secret = "provider-secret-that-must-not-be-persisted"

    async def fail_persistence(**_kwargs):
        raise RuntimeError(f"S3 rejected Authorization: Bearer {secret}")

    monkeypatch.setattr(service, "_persist_artifact", fail_persistence)

    failed = await service.run_job(job.id)

    assert failed.status == "failed"
    assert failed.error_code == "PLATFORM_EXPORT_INTERNAL_ERROR"
    assert failed.error_detail == (
        "A exportação falhou internamente. Consulte o correlation ID."
    )
    assert secret not in (failed.error_detail or "")


@pytest.mark.asyncio
async def test_audit_export_resanitizes_legacy_snapshots_and_reason(
    db_session,
    platform_auth_context,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "PLATFORM_EXPORT_STORAGE_DIR", str(tmp_path / "exports"))
    actor = await db_session.get(User, platform_auth_context.user_id)
    assert actor is not None
    raw_secret = "legacy-secret-value-that-must-not-leave-the-database"
    db_session.add(PlatformAuditEvent(
        actor_id=actor.id,
        actor_role_snapshot="platform_owner",
        is_platform_root=True,
        session_type="normal",
        module="security",
        action="legacy.import",
        resource_type="legacy",
        resource_id=str(uuid4()),
        result="success",
        before={"access_token": raw_secret, "status": "old"},
        after={"access_token": f"{raw_secret}-changed", "status": "new"},
        changed_paths=["access_token", "status"],
        reason=f"Authorization: Bearer {raw_secret}",
        reason_source="user",
        correlation_id=str(uuid4()),
        operation_id=str(uuid4()),
        route="legacy",
        method="POST",
        occurred_at=datetime.now(UTC),
    ))
    await db_session.flush()
    service = PlatformExportService(db_session)
    job = await service.create_job(
        actor=actor,
        dataset="audit",
        format="csv",
        filters={"action": "legacy.import"},
        columns=["motivo", "campos_alterados"],
        selection_set_id=None,
        idempotency_key="audit-legacy-sanitizer-export",
    )

    completed = await service.run_job(job.id)

    exported = service.local_artifact_path(
        completed.storage_path or "",
        must_exist=True,
    ).read_text(encoding="utf-8-sig")
    assert raw_secret not in exported
    assert "[REDACTED]" in exported
    assert "access_token" in exported
