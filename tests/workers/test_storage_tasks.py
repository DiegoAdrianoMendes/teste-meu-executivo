from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.malware_scanner import MalwareScannerNotConfiguredError
from app.services.s3_service import S3Service
from app.workers import storage_tasks


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_args) -> None:
        return None


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def _workspace(name: str, *, is_active: bool = True, deleted: bool = False) -> Workspace:
    return Workspace(
        name=name,
        slug=f"storage-worker-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=is_active,
        deleted_at=storage_tasks.datetime.now(storage_tasks.UTC) if deleted else None,
        settings={},
    )


def test_workspace_file_lease_blocks_overlap_and_releases_process_state(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path)

    with storage_tasks._workspace_reconciliation_lease(workspace_id) as acquired:
        assert acquired is True
        with storage_tasks._workspace_reconciliation_lease(workspace_id) as overlapping:
            assert overlapping is False
        assert workspace_id in storage_tasks._ACTIVE_WORKSPACE_LEASES

    assert workspace_id not in storage_tasks._ACTIVE_WORKSPACE_LEASES
    with storage_tasks._workspace_reconciliation_lease(workspace_id) as reacquired:
        assert reacquired is True


@pytest.mark.asyncio
async def test_storage_worker_binds_one_workspace_runs_a_bounded_batch_and_commits(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    observed: dict[str, object] = {}

    class Session:
        async def commit(self) -> None:
            observed["committed"] = True

    session = Session()

    async def bind_workspace(db, value) -> None:
        observed["bound"] = (db, value)

    @contextmanager
    def lease(value):
        observed["lease"] = value
        yield True

    class Service:
        def __init__(self, db: object) -> None:
            observed["db"] = db

        async def reconcile_workspace_batch(self, *, workspace_id, limit):
            observed["batch"] = (workspace_id, limit)
            return SimpleNamespace(
                examined=7,
                report=SimpleNamespace(
                    pending_recovered=2,
                    pending_quarantined=1,
                    quarantine_repaired=3,
                ),
            )

    monkeypatch.setattr(storage_tasks, "set_workspace_context", bind_workspace)
    monkeypatch.setattr(storage_tasks, "_workspace_reconciliation_lease", lease)

    result = await storage_tasks._async_reconcile_workspace_storage(
        workspace_id,
        session_factory=lambda: _SessionContext(session),
        service_factory=Service,
        limit=17,
    )

    assert result == 7
    assert observed == {
        "lease": workspace_id,
        "bound": (session, workspace_id),
        "db": session,
        "batch": (workspace_id, 17),
        "committed": True,
    }


@pytest.mark.asyncio
async def test_storage_dispatcher_enqueues_only_active_workspaces(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    active_a = _workspace("Storage A")
    active_b = _workspace("Storage B")
    inactive = _workspace("Storage inactive", is_active=False)
    deleted = _workspace("Storage deleted", deleted=True)
    db_session.add_all((active_a, active_b, inactive, deleted))
    await db_session.commit()
    queued: list[str] = []
    monkeypatch.setattr(
        storage_tasks.reconcile_local_objects,
        "apply_async",
        lambda *, kwargs: queued.append(kwargs["workspace_id"]),
    )

    count = await storage_tasks._async_dispatch_storage_reconciliation(
        session_factory=_session_factory(db_session)
    )

    assert count == 2
    assert set(queued) == {str(active_a.id), str(active_b.id)}


@pytest.mark.asyncio
async def test_storage_worker_rejects_invalid_workspace_before_database_access() -> None:
    def forbidden_session_factory():
        raise AssertionError("invalid workspace reached storage database")

    with pytest.raises(ValueError, match="workspace_id"):
        await storage_tasks._async_reconcile_workspace_storage(
            "not-a-uuid",
            session_factory=forbidden_session_factory,
        )


def test_storage_reconciliation_is_registered_in_celery_beat() -> None:
    schedule = storage_tasks.celery_app.conf.beat_schedule[
        "reconcile-local-storage-every-minute"
    ]

    assert schedule["task"] == "storage_tasks.reconcile_local_objects"
    assert "storage_tasks.reconcile_local_objects" in storage_tasks.celery_app.tasks

    scan_schedule = storage_tasks.celery_app.conf.beat_schedule[
        "dispatch-private-document-scans-every-minute"
    ]
    cleanup_schedule = storage_tasks.celery_app.conf.beat_schedule[
        "cleanup-abandoned-private-uploads-every-fifteen-minutes"
    ]
    assert scan_schedule["task"] == "storage_tasks.dispatch_private_document_scans"
    assert cleanup_schedule["task"] == (
        "storage_tasks.cleanup_abandoned_private_uploads"
    )
    delivery_schedule = storage_tasks.celery_app.conf.beat_schedule[
        "dispatch-generated-document-deliveries-every-minute"
    ]
    assert delivery_schedule["task"] == (
        "storage_tasks.dispatch_generated_document_deliveries"
    )


@pytest.mark.asyncio
async def test_private_scan_provider_absent_is_persisted_as_not_configured(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    version_id = uuid4()
    observed: dict[str, object] = {}

    class Session:
        async def commit(self) -> None:
            observed["committed"] = True

    session = Session()

    async def bind_workspace(db, value) -> None:
        observed["bound"] = (db, value)

    class Service:
        def __init__(self, db: object) -> None:
            observed["db"] = db

        async def mark_scan_error(self, **kwargs) -> None:
            observed["error"] = kwargs

        async def scan_and_promote(self, **_kwargs) -> str:
            raise AssertionError("scan must not run without a configured provider")

    def missing_scanner():
        raise MalwareScannerNotConfiguredError("not configured")

    monkeypatch.setattr(storage_tasks, "set_workspace_context", bind_workspace)

    result = await storage_tasks._async_scan_private_document(
        workspace_id,
        version_id,
        session_factory=lambda: _SessionContext(session),
        service_factory=Service,
        scanner_factory=missing_scanner,
    )

    assert result == "not_configured"
    assert observed == {
        "bound": (session, workspace_id),
        "db": session,
        "error": {
            "workspace_id": workspace_id,
            "version_id": version_id,
            "reason": "av_scanner_not_configured",
        },
        "committed": True,
    }


@pytest.mark.asyncio
async def test_clean_private_scan_commits_before_enqueuing_generated_delivery(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    version_id = uuid4()
    sequence: list[str] = []

    class Session:
        async def commit(self) -> None:
            sequence.append("commit")

        async def rollback(self) -> None:
            sequence.append("rollback")

    class Service:
        def __init__(self, _db: object) -> None:
            pass

        async def scan_and_promote(self, **_kwargs) -> str:
            sequence.append("scan-clean")
            return "clean"

        async def mark_scan_error(self, **_kwargs) -> None:
            raise AssertionError("clean scan must not be marked as error")

    async def bind_workspace(_db, _workspace_id) -> None:
        sequence.append("bind")

    monkeypatch.setattr(storage_tasks, "set_workspace_context", bind_workspace)
    monkeypatch.setattr(
        storage_tasks.dispatch_generated_document_deliveries,
        "apply_async",
        lambda *, kwargs: sequence.append(
            f"delivery:{kwargs['workspace_id']}:{kwargs['version_id']}"
        ),
    )

    result = await storage_tasks._async_scan_private_document(
        workspace_id,
        version_id,
        session_factory=lambda: _SessionContext(Session()),
        service_factory=Service,
        scanner_factory=lambda: object(),
    )

    assert result == "clean"
    assert sequence == [
        "bind",
        "scan-clean",
        "commit",
        f"delivery:{workspace_id}:{version_id}",
    ]


@pytest.mark.asyncio
async def test_unexpected_private_scan_error_is_persisted_fail_closed(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    version_id = uuid4()
    sequence: list[str] = []

    class Session:
        async def commit(self) -> None:
            sequence.append("commit")

        async def rollback(self) -> None:
            sequence.append("rollback")

    class Service:
        def __init__(self, _db: object) -> None:
            pass

        async def scan_and_promote(self, **_kwargs) -> str:
            raise RuntimeError("unexpected scanner adapter bug")

        async def mark_scan_error(self, **kwargs) -> None:
            sequence.append(f"marked:{kwargs['reason']}")

    async def bind_workspace(_db, _workspace_id) -> None:
        sequence.append("bind")

    monkeypatch.setattr(storage_tasks, "set_workspace_context", bind_workspace)

    with pytest.raises(RuntimeError, match="unexpected scanner adapter bug"):
        await storage_tasks._async_scan_private_document(
            workspace_id,
            version_id,
            session_factory=lambda: _SessionContext(Session()),
            service_factory=Service,
            scanner_factory=lambda: object(),
        )

    assert sequence == [
        "bind",
        "rollback",
        "bind",
        "marked:av_scan_unexpected_error:RuntimeError",
        "commit",
    ]


@pytest.mark.asyncio
async def test_generated_delivery_worker_passes_explicit_workspace_scope(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    observed: dict[str, object] = {}

    class Service:
        def __init__(self, db: object) -> None:
            observed["db"] = db

        async def dispatch_due(self, *, workspace_id) -> int:
            observed["workspace"] = workspace_id
            return 3

    async def bind_workspace(db, value) -> None:
        observed["bound"] = (db, value)

    session = object()
    monkeypatch.setattr(storage_tasks, "set_workspace_context", bind_workspace)

    result = await storage_tasks._async_dispatch_generated_document_deliveries(
        workspace_id,
        session_factory=lambda: _SessionContext(session),
        service_factory=Service,
    )

    assert result == 3
    assert observed == {
        "bound": (session, workspace_id),
        "db": session,
        "workspace": workspace_id,
    }


@pytest.mark.asyncio
async def test_private_scan_dispatch_does_not_churn_when_provider_is_absent(
    monkeypatch,
) -> None:
    monkeypatch.setattr(storage_tasks, "aws_private_storage_allowed", lambda: True)

    def missing_scanner():
        raise MalwareScannerNotConfiguredError("not configured")

    async def forbidden_workspace_discovery(**_kwargs):
        raise AssertionError("database scan must not run without an AV provider")

    monkeypatch.setattr(storage_tasks.ClamAVScanner, "from_settings", missing_scanner)
    monkeypatch.setattr(
        storage_tasks,
        "_active_workspace_ids",
        forbidden_workspace_discovery,
    )

    assert await storage_tasks._async_dispatch_private_document_scans() == 0
