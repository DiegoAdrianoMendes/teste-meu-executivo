"""Bounded periodic reconciliation for tenant-scoped local object storage."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.config import settings
from app.core.klm_runtime import aws_private_storage_allowed
from app.core.logging import logger
from app.db.session import WorkerSessionLocal as AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.workspace import Workspace
from app.services.local_object_storage_service import (
    LocalObjectStorageError,
    LocalObjectStorageService,
)
from app.services.malware_scanner import (
    ClamAVScanner,
    MalwareScannerError,
    MalwareScannerNotConfiguredError,
)
from app.services.private_document_storage_service import PrivateDocumentStorageService
from app.services.s3_service import S3Service
from app.workers.celery_app import celery_app

MAX_OBJECTS_PER_WORKSPACE = 100
STORAGE_RECONCILIATION_LEASE_TTL = timedelta(seconds=55)
_ACTIVE_WORKSPACE_LEASES: set[UUID] = set()
_ACTIVE_WORKSPACE_LEASES_GUARD = threading.Lock()


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _workspace_id(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("workspace_id must be a valid UUID") from exc


@contextmanager
def _workspace_reconciliation_lease(
    workspace_id: UUID,
    *,
    now: datetime | None = None,
) -> Iterator[bool]:
    """Hold a crash-released process/file lease for one workspace task."""
    with _ACTIVE_WORKSPACE_LEASES_GUARD:
        already_active = workspace_id in _ACTIVE_WORKSPACE_LEASES
        if not already_active:
            _ACTIVE_WORKSPACE_LEASES.add(workspace_id)
    if already_active:
        yield False
        return

    descriptor = -1
    acquired = False
    lease_key = f"leases/storage-reconciliation/{workspace_id.hex}.lock"
    try:
        try:
            created = S3Service.create_local_object(lease_key)
        except FileExistsError:
            pass
        else:
            created.close()

        flags = os.O_RDWR | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(S3Service.local_object_path(lease_key), flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("storage reconciliation lease is not a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        acquired = True

        issued_at = now or datetime.now(UTC)
        metadata = json.dumps(
            {
                "token": uuid4().hex,
                "workspaceId": str(workspace_id),
                "issuedAt": issued_at.isoformat(),
                "expiresAt": (
                    issued_at + STORAGE_RECONCILIATION_LEASE_TTL
                ).isoformat(),
            },
            sort_keys=True,
        ).encode("ascii")
        os.ftruncate(descriptor, 0)
        os.write(descriptor, metadata)
        os.fsync(descriptor)
        yield True
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        if descriptor >= 0:
            os.close(descriptor)
        with _ACTIVE_WORKSPACE_LEASES_GUARD:
            _ACTIVE_WORKSPACE_LEASES.discard(workspace_id)


async def _active_workspace_ids(*, session_factory=None) -> list[UUID]:
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as discovery_db:
        return list(
            (
                await discovery_db.scalars(
                    select(Workspace.id)
                    .where(
                        Workspace.deleted_at.is_(None),
                        Workspace.is_active.is_(True),
                    )
                    .order_by(Workspace.id)
                )
            ).all()
        )


@celery_app.task(
    name="storage_tasks.reconcile_local_objects",
    soft_time_limit=50,
    time_limit=55,
)
def reconcile_local_objects(workspace_id: str | None = None) -> int:
    if workspace_id is None:
        return _run_async(_async_dispatch_storage_reconciliation())
    return _run_async(_async_reconcile_workspace_storage(workspace_id))


@celery_app.task(
    bind=True,
    name="storage_tasks.scan_private_document",
    max_retries=settings.DOCUMENT_AV_RETRY_LIMIT,
    soft_time_limit=50,
    time_limit=55,
)
def scan_private_document(self, workspace_id: str, version_id: str) -> str:
    try:
        return _run_async(_async_scan_private_document(workspace_id, version_id))
    except Exception as exc:  # noqa: BLE001 - bounded retry + persisted fail-closed state
        countdown = min(300, 15 * (2 ** int(self.request.retries)))
        raise self.retry(exc=exc, countdown=countdown)


@celery_app.task(
    name="storage_tasks.dispatch_private_document_scans",
    soft_time_limit=50,
    time_limit=55,
)
def dispatch_private_document_scans() -> int:
    return _run_async(_async_dispatch_private_document_scans())


@celery_app.task(
    name="storage_tasks.cleanup_abandoned_private_uploads",
    soft_time_limit=50,
    time_limit=55,
)
def cleanup_abandoned_private_uploads() -> int:
    return _run_async(_async_cleanup_abandoned_private_uploads())


@celery_app.task(
    name="storage_tasks.dispatch_generated_document_deliveries",
    soft_time_limit=50,
    time_limit=55,
)
def dispatch_generated_document_deliveries(
    workspace_id: str | None = None,
    version_id: str | None = None,
) -> int:
    if workspace_id is None:
        return _run_async(_async_dispatch_generated_document_delivery_workspaces())
    return _run_async(
        _async_dispatch_generated_document_deliveries(
            workspace_id,
            version_id=version_id,
        )
    )


async def _async_scan_private_document(
    workspace_id: UUID | str,
    version_id: UUID | str,
    *,
    session_factory=None,
    service_factory=None,
    scanner_factory=None,
) -> str:
    resolved_workspace_id = _workspace_id(workspace_id)
    try:
        resolved_version_id = UUID(str(version_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("version_id must be a valid UUID") from exc

    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        service = (service_factory or PrivateDocumentStorageService)(db)
        try:
            scanner = (scanner_factory or ClamAVScanner.from_settings)()
        except MalwareScannerNotConfiguredError:
            await service.mark_scan_error(
                workspace_id=resolved_workspace_id,
                version_id=resolved_version_id,
                reason="av_scanner_not_configured",
            )
            await db.commit()
            return "not_configured"
        try:
            result = await service.scan_and_promote(
                workspace_id=resolved_workspace_id,
                version_id=resolved_version_id,
                scanner=scanner,
            )
            # The AV verdict and clean-key promotion receipt must be durable
            # before any outbound delivery is allowed to observe them.
            await db.commit()
            if result in {"clean", "already_clean", "infected", "deleted"}:
                dispatch_generated_document_deliveries.apply_async(
                    kwargs={
                        "workspace_id": str(resolved_workspace_id),
                        "version_id": str(resolved_version_id),
                    }
                )
            return result
        except (LocalObjectStorageError, MalwareScannerError) as exc:
            await db.rollback()
            await set_workspace_context(db, resolved_workspace_id)
            service = (service_factory or PrivateDocumentStorageService)(db)
            await service.mark_scan_error(
                workspace_id=resolved_workspace_id,
                version_id=resolved_version_id,
                reason=f"av_scan_transient_error:{type(exc).__name__}",
            )
            await db.commit()
            raise
        except Exception as exc:  # noqa: BLE001 - scanner bugs must fail closed too
            await db.rollback()
            await set_workspace_context(db, resolved_workspace_id)
            service = (service_factory or PrivateDocumentStorageService)(db)
            await service.mark_scan_error(
                workspace_id=resolved_workspace_id,
                version_id=resolved_version_id,
                reason=f"av_scan_unexpected_error:{type(exc).__name__}",
            )
            await db.commit()
            raise


async def _async_dispatch_generated_document_delivery_workspaces(
    *,
    session_factory=None,
) -> int:
    if not aws_private_storage_allowed():
        return 0
    queued = 0
    for workspace_id in await _active_workspace_ids(session_factory=session_factory):
        dispatch_generated_document_deliveries.apply_async(
            kwargs={"workspace_id": str(workspace_id)}
        )
        queued += 1
    return queued


async def _async_dispatch_generated_document_deliveries(
    workspace_id: UUID | str,
    *,
    version_id: UUID | str | None = None,
    session_factory=None,
    service_factory=None,
) -> int:
    resolved_workspace_id = _workspace_id(workspace_id)
    resolved_version_id = UUID(str(version_id)) if version_id is not None else None
    session_maker = session_factory or AsyncSessionLocal
    async with session_maker() as db:
        await set_workspace_context(db, resolved_workspace_id)
        if service_factory is None:
            from app.services.generated_document_delivery_service import (
                GeneratedDocumentDeliveryService,
            )

            service_factory = GeneratedDocumentDeliveryService
        service = service_factory(db)
        if resolved_version_id is not None:
            return await service.dispatch_for_storage_version(resolved_version_id)
        return await service.dispatch_due(workspace_id=resolved_workspace_id)


async def _async_dispatch_private_document_scans(
    *,
    session_factory=None,
    limit_per_workspace: int = 50,
) -> int:
    if not aws_private_storage_allowed():
        return 0
    try:
        # Configuration preflight only; the worker performs the real socket
        # handshake. Avoid requeueing every quarantined row forever while the
        # scanner is explicitly not configured.
        ClamAVScanner.from_settings()
    except MalwareScannerNotConfiguredError:
        logger.warning("storage.private_scan_dispatch_skipped_not_configured")
        return 0
    queued = 0
    session_maker = session_factory or AsyncSessionLocal
    for workspace_id in await _active_workspace_ids(session_factory=session_factory):
        async with session_maker() as db:
            await set_workspace_context(db, workspace_id)
            rows = list(
                (
                    await db.scalars(
                        select(StorageObject)
                        .where(
                            StorageObject.workspace_id == workspace_id,
                            StorageObject.deleted_at.is_(None),
                            StorageObject.uploaded_at.is_not(None),
                            StorageObject.state == StorageObjectState.quarantined,
                            StorageObject.av_status.in_(
                                [
                                    StorageAVStatus.pending,
                                    StorageAVStatus.error,
                                    StorageAVStatus.clean,
                                ]
                            ),
                        )
                        .order_by(StorageObject.updated_at, StorageObject.id)
                        .limit(limit_per_workspace)
                    )
                ).all()
            )
        for row in rows:
            scan_private_document.apply_async(
                kwargs={
                    "workspace_id": str(workspace_id),
                    "version_id": str(row.id),
                }
            )
            queued += 1
    return queued


async def _async_cleanup_abandoned_private_uploads(
    *,
    session_factory=None,
    limit_per_workspace: int = 100,
) -> int:
    if not aws_private_storage_allowed():
        return 0
    cleaned = 0
    observed_at = datetime.now(UTC)
    session_maker = session_factory or AsyncSessionLocal
    for workspace_id in await _active_workspace_ids(session_factory=session_factory):
        async with session_maker() as db:
            await set_workspace_context(db, workspace_id)
            version_ids = list(
                (
                    await db.scalars(
                        select(StorageObject.id)
                        .where(
                            StorageObject.workspace_id == workspace_id,
                            StorageObject.deleted_at.is_(None),
                            StorageObject.uploaded_at.is_(None),
                            StorageObject.state == StorageObjectState.pending,
                            StorageObject.upload_ticket_expires_at.is_not(None),
                            StorageObject.upload_ticket_expires_at <= observed_at,
                        )
                        .order_by(StorageObject.upload_ticket_expires_at, StorageObject.id)
                        .limit(limit_per_workspace)
                    )
                ).all()
            )
            service = PrivateDocumentStorageService(db)
            for version_id in version_ids:
                try:
                    result = await service.expire_abandoned_upload(
                        workspace_id=workspace_id,
                        version_id=version_id,
                        now=observed_at,
                    )
                    await db.commit()
                    if result == "expired":
                        cleaned += 1
                except LocalObjectStorageError as exc:
                    await db.rollback()
                    logger.warning(
                        "storage.private_upload_cleanup_failed",
                        workspace_id=str(workspace_id),
                        version_id=str(version_id),
                        error_type=type(exc).__name__,
                    )
    return cleaned


async def _async_dispatch_storage_reconciliation(*, session_factory=None) -> int:
    if settings.is_production:
        logger.info("storage.reconciliation_dispatch_skipped_in_production")
        return 0
    workspace_ids = await _active_workspace_ids(session_factory=session_factory)
    for workspace_id in workspace_ids:
        reconcile_local_objects.apply_async(kwargs={"workspace_id": str(workspace_id)})
    return len(workspace_ids)


async def _async_reconcile_workspace_storage(
    workspace_id: UUID | str,
    *,
    session_factory=None,
    service_factory=None,
    limit: int = MAX_OBJECTS_PER_WORKSPACE,
) -> int:
    resolved_workspace_id = _workspace_id(workspace_id)
    if settings.is_production:
        logger.info(
            "storage.reconciliation_skipped_in_production",
            workspace_id=str(resolved_workspace_id),
        )
        return 0

    with _workspace_reconciliation_lease(resolved_workspace_id) as acquired:
        if not acquired:
            logger.info(
                "storage.reconciliation_lease_busy",
                workspace_id=str(resolved_workspace_id),
            )
            return 0

        session_maker = session_factory or AsyncSessionLocal
        async with session_maker() as db:
            await set_workspace_context(db, resolved_workspace_id)
            factory = service_factory or LocalObjectStorageService
            service = factory(db)
            result = await service.reconcile_workspace_batch(
                workspace_id=resolved_workspace_id,
                limit=limit,
            )
            await db.commit()
            logger.info(
                "storage.reconciliation_batch_completed",
                workspace_id=str(resolved_workspace_id),
                examined=result.examined,
                pending_recovered=result.report.pending_recovered,
                pending_quarantined=result.report.pending_quarantined,
                quarantine_repaired=result.report.quarantine_repaired,
            )
            return result.examined
