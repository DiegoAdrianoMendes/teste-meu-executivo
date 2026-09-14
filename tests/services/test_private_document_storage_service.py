from __future__ import annotations

import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.tenant import TenantSession
from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.document_asset import (
    DocumentAsset,
    DocumentAssetType,
    DocumentClassificationMethod,
    DocumentOCRStatus,
)
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.local_object_storage_service import (
    LocalObjectConflictError,
    LocalObjectRetentionError,
    LocalObjectStorageService,
    LocalObjectUnavailableError,
)
from app.services.malware_scanner import (
    ClamAVScanner,
    MalwareScannerError,
    MalwareScannerNotConfiguredError,
    MalwareScanReceipt,
)
from app.services.private_document_storage_service import PrivateDocumentStorageService

PDF = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


class _Body(io.BytesIO):
    pass


class FakePrivateClient:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def put(self, key: str, payload: bytes, content_type: str, checksum: str) -> None:
        self.objects[key] = {
            "payload": payload,
            "ContentType": content_type,
            "Metadata": {"storage-state": "quarantine", "sha256": checksum},
            "ServerSideEncryption": "aws:kms",
        }

    def head_object(self, **kwargs):
        key = kwargs["Key"]
        if key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        item = self.objects[key]
        return {
            "ContentLength": len(item["payload"]),
            "ContentType": item["ContentType"],
            "Metadata": dict(item["Metadata"]),
            "ServerSideEncryption": item["ServerSideEncryption"],
        }

    def get_object(self, **kwargs):
        key = kwargs["Key"]
        result = self.head_object(Key=key)
        result["Body"] = _Body(self.objects[key]["payload"])
        return result


class FakePrivateS3:
    def __init__(self) -> None:
        self.client = FakePrivateClient()
        self.deleted: list[str] = []

    def verify_private_bucket(self):
        return None

    def generate_presigned_private_upload_url(self, key, **_kwargs):
        return f"https://private.invalid/{key}"

    def generate_authorized_private_download_url(self, key, **_kwargs):
        return f"https://private.invalid/{key}?signed=1"

    def promote_authorized_quarantine_object(
        self,
        source,
        destination,
        *,
        checksum_sha256,
        content_type,
        **_kwargs,
    ):
        item = self.client.objects.pop(source)
        item["ContentType"] = content_type
        item["Metadata"] = {
            "storage-state": "clean",
            "sha256": checksum_sha256,
        }
        self.client.objects[destination] = item
        return destination

    def delete_authorized_private_object(self, key, **_kwargs):
        self.client.objects.pop(key, None)
        self.deleted.append(key)


class CleanScanner:
    def scan(self, handle):
        assert handle.read() == PDF
        handle.seek(0)
        return MalwareScanReceipt(
            verdict="clean",
            scanner="clamav_instream",
            raw_status="OK",
        )


class InfectedScanner:
    def scan(self, handle):
        assert handle.read() == PDF
        return MalwareScanReceipt(
            verdict="infected",
            scanner="clamav_instream",
            raw_status="FOUND",
            signature="Eicar-Signature",
        )


@pytest.fixture(autouse=True)
def remote_runtime(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", True)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "alias/meu-executivo-documents")
    monkeypatch.setattr(settings, "DOCUMENT_AV_MODE", "clamav")
    monkeypatch.setattr(settings, "CLAMAV_HOST", "clamav.internal")


async def _bound_matter(db_session, workspace_id):
    client = Client(workspace_id=workspace_id, name="Cliente AV")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type="Documento privado",
    )
    db_session.add(matter)
    # The production authorization boundary reloads ACL owners in its own
    # tenant transaction.  Tests must not depend on the old unsafe behavior
    # where storage committed a merely-flushed Matter on the caller's behalf.
    await db_session.commit()
    return matter


async def _authorized_document(db_session, auth_context, monkeypatch):
    monkeypatch.setattr(
        LocalObjectStorageService,
        "_enforce_stock_capacity",
        AsyncMock(),
    )
    monkeypatch.setattr(EntitlementEnforcementService, "lock_workspace", AsyncMock())
    matter = await _bound_matter(db_session, auth_context.workspace_id)
    s3 = FakePrivateS3()
    service = PrivateDocumentStorageService(db_session, s3=s3)
    checksum = hashlib.sha256(PDF).hexdigest()
    authorization = await service.create_upload_authorization(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="contrato.pdf",
        content_type="application/pdf",
        byte_size=len(PDF),
        checksum_sha256=checksum,
        retention_until=None,
        expires_in=300,
        object_id=None,
        matter_id=matter.id,
        client_id=matter.client_id,
    )
    record = authorization.storage_object
    db_session.add(
        Document(
            id=record.object_id,
            matter_id=matter.id,
            created_by=auth_context.user_id,
            name="contrato.pdf",
            doc_type=DocumentType.contrato,
            status=DocumentStatus.aguardando,
            s3_key=record.storage_key,
            file_size=str(len(PDF)),
            mime_type="application/pdf",
        )
    )
    assert record.quarantine_key is not None
    s3.client.put(record.quarantine_key, PDF, "application/pdf", checksum)
    return service, s3, record, authorization


@pytest.mark.asyncio
async def test_private_authorization_commits_only_its_dedicated_session(tmp_path):
    """A presigned URL must not commit dirty state owned by the caller."""

    database_path = tmp_path / "private-authorization-boundary.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    original_name = "Workspace antes da autorização"
    try:
        async with factory() as caller:
            workspace = Workspace(
                name=original_name,
                slug=f"storage-boundary-{uuid.uuid4().hex[:12]}",
                status=WorkspaceStatus.active,
                setup_mode=WorkspaceSetupMode.self_service,
                is_active=True,
                settings={},
            )
            caller.add(workspace)
            await caller.commit()
            workspace_id = workspace.id

            # This mutation belongs to the request transaction and must remain
            # uncommitted when storage persists its external-effect intent.
            workspace.name = "ALTERAÇÃO PENDENTE DO CHAMADOR"
            authorization = await PrivateDocumentStorageService(
                caller,
                s3=FakePrivateS3(),
            ).create_upload_authorization(
                workspace_id=workspace_id,
                actor_user_id=None,
                filename="contrato.pdf",
                content_type="application/pdf",
                byte_size=len(PDF),
                checksum_sha256=hashlib.sha256(PDF).hexdigest(),
                retention_until=None,
                expires_in=300,
                idempotency_key="private-storage-boundary-test",
            )

            async with factory() as observer:
                persisted_workspace = await observer.get(Workspace, workspace_id)
                persisted_storage = await observer.get(
                    StorageObject,
                    authorization.storage_object.id,
                )
                assert persisted_workspace is not None
                assert persisted_workspace.name == original_name
                assert persisted_storage is not None
                assert persisted_storage.state is StorageObjectState.pending

            await caller.rollback()

        async with factory() as observer:
            persisted_workspace = await observer.get(Workspace, workspace_id)
            assert persisted_workspace is not None
            assert persisted_workspace.name == original_name
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_private_first_upload_authorization_is_persistently_idempotent(
    db_session,
    auth_context,
    monkeypatch,
):
    monkeypatch.setattr(LocalObjectStorageService, "_enforce_stock_capacity", AsyncMock())
    monkeypatch.setattr(EntitlementEnforcementService, "lock_workspace", AsyncMock())
    matter = await _bound_matter(db_session, auth_context.workspace_id)
    s3 = FakePrivateS3()
    service = PrivateDocumentStorageService(db_session, s3=s3)
    checksum = hashlib.sha256(PDF).hexdigest()
    raw_key = "document-private-first-authorization"
    common = {
        "workspace_id": auth_context.workspace_id,
        "actor_user_id": auth_context.user_id,
        "filename": "contrato.pdf",
        "content_type": "application/pdf",
        "byte_size": len(PDF),
        "checksum_sha256": checksum,
        "retention_until": None,
        "expires_in": 300,
        "matter_id": matter.id,
        "client_id": matter.client_id,
        "idempotency_key": raw_key,
    }

    first = await service.create_upload_authorization(**common)
    first_id = first.storage_object.id
    # The caller can receive a PUT URL only after the ownership/intention row
    # committed. A later rollback of unrelated request work must not orphan S3
    # bytes without a cleanup target.
    await db_session.rollback()
    durable = await db_session.get(StorageObject, first_id)
    assert durable is not None
    assert durable.state is StorageObjectState.pending
    assert durable.upload_ticket_expires_at is not None
    replay = await service.create_upload_authorization(**common)

    assert replay.storage_object.id == first.storage_object.id
    assert replay.storage_object.object_id == first.storage_object.object_id
    assert replay.upload_required is True
    assert replay.upload_url is not None
    assert first.storage_object.idempotency_key_hash == hashlib.sha256(
        raw_key.encode("utf-8")
    ).hexdigest()
    assert raw_key not in str(first.storage_object.idempotency_key_hash)

    with pytest.raises(LocalObjectConflictError, match="outra autorização"):
        await service.create_upload_authorization(
            **{**common, "filename": "outro-contrato.pdf"}
        )

    assert first.storage_object.quarantine_key is not None
    s3.client.put(first.storage_object.quarantine_key, PDF, "application/pdf", checksum)
    await service.confirm_upload(
        workspace_id=auth_context.workspace_id,
        object_id=first.storage_object.object_id,
        actor_user_id=auth_context.user_id,
    )
    consumed = await service.create_upload_authorization(**common)
    assert consumed.storage_object.id == first.storage_object.id
    assert consumed.upload_required is False
    assert consumed.upload_url is None


@pytest.mark.asyncio
async def test_remote_upload_fails_closed_without_verified_private_bucket(
    db_session,
    auth_context,
    monkeypatch,
):
    s3 = FakePrivateS3()

    def fail_verification() -> None:
        raise RuntimeError("public access block unavailable")

    monkeypatch.setattr(s3, "verify_private_bucket", fail_verification)
    service = PrivateDocumentStorageService(db_session, s3=s3)

    with pytest.raises(
        LocalObjectUnavailableError,
        match="bloqueio público integral",
    ):
        await service.create_upload_authorization(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            filename="contrato.pdf",
            content_type="application/pdf",
            byte_size=len(PDF),
            checksum_sha256=hashlib.sha256(PDF).hexdigest(),
            retention_until=None,
            expires_in=300,
        )


@pytest.mark.asyncio
async def test_remote_upload_stays_quarantined_until_real_clean_receipt(
    db_session,
    auth_context,
    monkeypatch,
):
    from app.workers.storage_tasks import scan_private_document

    queued: list[dict] = []
    monkeypatch.setattr(
        scan_private_document,
        "apply_async",
        lambda *, kwargs: queued.append(kwargs),
    )
    service, s3, record, authorization = await _authorized_document(
        db_session,
        auth_context,
        monkeypatch,
    )
    assert authorization.upload_headers["x-amz-meta-storage-state"] == "quarantine"
    assert authorization.upload_headers["x-amz-server-side-encryption"] == "aws:kms"
    assert authorization.upload_url.startswith("https://private.invalid/")
    assert "://" not in record.storage_key
    assert record.storage_key not in {authorization.upload_url, record.quarantine_key}

    confirmed = await service.confirm_upload(
        workspace_id=auth_context.workspace_id,
        object_id=record.object_id,
        actor_user_id=auth_context.user_id,
    )
    assert confirmed.state is StorageObjectState.quarantined
    assert confirmed.av_status is StorageAVStatus.pending
    asset = DocumentAsset(
        workspace_id=auth_context.workspace_id,
        storage_object_id=record.id,
        content_sha256=hashlib.sha256(PDF).hexdigest(),
        original_filename="contrato.pdf",
        matter_id=record.matter_id,
        client_id=record.client_id,
        created_by_user_id=auth_context.user_id,
        document_type=DocumentAssetType.nao_classificado,
        classification_method=DocumentClassificationMethod.indisponivel,
        ocr_status=DocumentOCRStatus.nao_aplicavel,
    )
    db_session.add(asset)
    await db_session.commit()
    assert queued == [
        {"workspace_id": str(auth_context.workspace_id), "version_id": str(record.id)}
    ]

    result = await service.scan_and_promote(
        workspace_id=auth_context.workspace_id,
        version_id=record.id,
        scanner=CleanScanner(),
    )
    assert result == "clean"
    stored = await db_session.get(StorageObject, record.id)
    assert stored is not None
    assert stored.state is StorageObjectState.available
    assert stored.av_status is StorageAVStatus.clean
    assert stored.quarantine_key is None
    assert stored.storage_key in s3.client.objects
    assert s3.client.objects[stored.storage_key]["Metadata"]["storage-state"] == "clean"
    document = await db_session.get(Document, record.object_id)
    assert document is not None and document.status is DocumentStatus.completo
    await db_session.refresh(asset)
    assert asset.ocr_status is DocumentOCRStatus.ocr_pendente

    download = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=record.object_id,
        version=None,
        expires_in=300,
    )
    assert download.download_url.endswith("?signed=1")


@pytest.mark.asyncio
async def test_infected_receipt_never_promotes_or_allows_download(
    db_session,
    auth_context,
    monkeypatch,
):
    from app.workers.storage_tasks import scan_private_document

    monkeypatch.setattr(scan_private_document, "apply_async", lambda **_kwargs: None)
    service, s3, record, _ = await _authorized_document(
        db_session,
        auth_context,
        monkeypatch,
    )
    await service.confirm_upload(
        workspace_id=auth_context.workspace_id,
        object_id=record.object_id,
    )
    await db_session.commit()
    result = await service.scan_and_promote(
        workspace_id=auth_context.workspace_id,
        version_id=record.id,
        scanner=InfectedScanner(),
    )
    assert result == "infected"
    stored = await db_session.get(StorageObject, record.id)
    assert stored is not None
    assert stored.state is StorageObjectState.quarantined
    assert stored.av_status is StorageAVStatus.infected
    assert stored.quarantine_key in s3.client.objects
    assert stored.storage_key not in s3.client.objects
    with pytest.raises(Exception, match="limpo"):
        await service.create_download_authorization(
            workspace_id=auth_context.workspace_id,
            object_id=record.object_id,
            version=None,
            expires_in=300,
        )


def test_clamav_protocol_accepts_only_conclusive_verdicts(monkeypatch):
    class Socket:
        def __init__(self, response: bytes):
            self.response = response
            self.sent = bytearray()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _value):
            return None

        def sendall(self, payload):
            self.sent.extend(payload)

        def recv(self, _size):
            response, self.response = self.response, b""
            return response

    clean_socket = Socket(b"stream: OK\x00")
    monkeypatch.setattr(
        "app.services.malware_scanner.socket.create_connection",
        lambda *_args, **_kwargs: clean_socket,
    )
    scanner = ClamAVScanner(host="clamav", port=3310, max_bytes=1024)
    assert scanner.scan(io.BytesIO(PDF)).verdict == "clean"
    assert clean_socket.sent.startswith(b"zINSTREAM\x00")

    unknown_socket = Socket(b"stream: UNKNOWN\x00")
    monkeypatch.setattr(
        "app.services.malware_scanner.socket.create_connection",
        lambda *_args, **_kwargs: unknown_socket,
    )
    with pytest.raises(MalwareScannerError, match="desconhecido"):
        scanner.scan(io.BytesIO(PDF))


def test_clamav_settings_fail_closed_when_provider_is_absent(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_AV_MODE", "disabled")
    monkeypatch.setattr(settings, "CLAMAV_HOST", "")
    with pytest.raises(MalwareScannerNotConfiguredError, match="não configurado"):
        ClamAVScanner.from_settings()


@pytest.mark.asyncio
async def test_expired_unconfirmed_upload_is_deleted_from_private_quarantine(
    db_session,
    auth_context,
    monkeypatch,
):
    service, s3, record, _ = await _authorized_document(
        db_session,
        auth_context,
        monkeypatch,
    )
    quarantine_key = record.quarantine_key
    assert quarantine_key is not None and quarantine_key in s3.client.objects
    record.upload_ticket_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    result = await service.expire_abandoned_upload(
        workspace_id=auth_context.workspace_id,
        version_id=record.id,
    )

    assert result == "expired"
    assert quarantine_key not in s3.client.objects
    assert s3.deleted == [quarantine_key]
    assert record.deleted_at is not None
    assert record.state is StorageObjectState.quarantined
    assert record.av_status is StorageAVStatus.error
    assert record.quarantine_reason == "upload_authorization_expired"


@pytest.mark.asyncio
async def test_remote_soft_delete_respects_retention_and_restore_is_audited(
    db_session,
    auth_context,
    monkeypatch,
):
    from app.workers.storage_tasks import scan_private_document

    queued: list[dict] = []
    monkeypatch.setattr(
        scan_private_document,
        "apply_async",
        lambda *, kwargs: queued.append(kwargs),
    )
    service, _s3, record, _ = await _authorized_document(
        db_session,
        auth_context,
        monkeypatch,
    )
    await service.confirm_upload(
        workspace_id=auth_context.workspace_id,
        object_id=record.object_id,
        actor_user_id=auth_context.user_id,
    )
    record.retention_until = datetime.now(UTC) + timedelta(days=1)
    await db_session.flush()

    with pytest.raises(LocalObjectRetentionError, match="retido até"):
        await service.soft_delete(
            workspace_id=auth_context.workspace_id,
            object_id=record.object_id,
            actor_user_id=auth_context.user_id,
        )

    record.retention_until = None
    deleted = await service.soft_delete(
        workspace_id=auth_context.workspace_id,
        object_id=record.object_id,
        actor_user_id=auth_context.user_id,
    )
    assert deleted.deleted_at is not None

    restored = await service.restore(
        workspace_id=auth_context.workspace_id,
        object_id=record.object_id,
        actor_user_id=auth_context.user_id,
    )
    assert restored.deleted_at is None
    assert restored.restored_by_user_id == auth_context.user_id

    event_types = set(
        (
            await db_session.scalars(
                select(KlmAuditEvent.event_type).where(
                    KlmAuditEvent.entity_id == record.id,
                    KlmAuditEvent.event_type.in_(
                        ["document_soft_deleted", "document_restored"]
                    ),
                )
            )
        ).all()
    )
    assert event_types == {"document_soft_deleted", "document_restored"}


@pytest.mark.asyncio
async def test_promotion_error_preserves_real_clean_receipt_for_retry(
    db_session,
    auth_context,
    monkeypatch,
):
    from app.workers.storage_tasks import scan_private_document

    monkeypatch.setattr(scan_private_document, "apply_async", lambda **_kwargs: None)
    service, _s3, record, _ = await _authorized_document(
        db_session,
        auth_context,
        monkeypatch,
    )
    await service.confirm_upload(
        workspace_id=auth_context.workspace_id,
        object_id=record.object_id,
    )
    record.av_status = StorageAVStatus.clean
    record.quarantine_reason = "scan_clean_pending_promotion"
    await db_session.flush()

    await service.mark_scan_error(
        workspace_id=auth_context.workspace_id,
        version_id=record.id,
        reason="av_scan_transient_error:LocalObjectUnavailableError",
    )

    assert record.state is StorageObjectState.quarantined
    assert record.av_status is StorageAVStatus.clean
    assert record.quarantine_reason is not None
    assert record.quarantine_reason.startswith("clean_receipt_promotion_retry:")
