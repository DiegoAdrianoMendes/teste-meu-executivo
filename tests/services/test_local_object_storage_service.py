from __future__ import annotations

import asyncio
import hashlib
import io
import os
import zipfile
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.security import verify_token
from app.models.storage_object import StorageAVStatus, StorageObjectState
from app.services.local_object_storage_service import (
    LocalObjectIntegrityError,
    LocalObjectNotFoundError,
    LocalObjectRetentionError,
    LocalObjectStorageService,
    LocalObjectTicketError,
    LocalObjectValidationError,
    fake_antivirus_scan,
    inspect_payload_handle,
)
from app.services.message_evidence_service import MessageEvidenceService
from app.services.s3_service import S3Service, StorageUnavailableError

PDF_V1 = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
PDF_V2 = b"%PDF-1.7\n2 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


class _BoundedReadHandle(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        assert 0 < size <= 1024 * 1024
        return super().read(size)


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "object-storage")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", "http://test")


def _ticket_from_url(url: str) -> str:
    return parse_qs(urlsplit(url).query)["ticket"][0]


def test_incremental_handle_inspection_never_materializes_the_full_payload() -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024)
    handle = _BoundedReadHandle(payload)

    inspection = inspect_payload_handle(handle, "image/png")

    assert inspection.byte_size == len(payload)
    assert inspection.detected_content_type == "image/png"
    assert inspection.av_status is StorageAVStatus.simulated_unverified
    assert len(handle.read_sizes) >= 3
    assert max(handle.read_sizes) == 1024 * 1024


async def _authorize_and_upload(
    service: LocalObjectStorageService,
    *,
    workspace_id,
    user_id,
    data: bytes = PDF_V1,
    object_id=None,
    content_type: str = "application/pdf",
    checksum: str | None = None,
    retention_until: datetime | None = None,
):
    authorization = await service.create_upload_authorization(
        workspace_id=workspace_id,
        actor_user_id=user_id,
        filename="contrato.pdf",
        content_type=content_type,
        byte_size=len(data),
        checksum_sha256=checksum or hashlib.sha256(data).hexdigest(),
        retention_until=retention_until,
        expires_in=300,
        object_id=object_id,
    )
    uploaded = await service.finalize_upload(
        ticket=_ticket_from_url(authorization.ticket.url),
        data=data,
        request_content_type=content_type,
    )
    return authorization, uploaded


@pytest.mark.asyncio
async def test_versions_checksum_download_delete_restore_and_export(
    db_session,
    auth_context,
):
    service = LocalObjectStorageService(db_session)
    first_auth, first = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )
    second_auth, second = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        data=PDF_V2,
        object_id=first.object_id,
    )

    assert first.state is StorageObjectState.available
    assert second.version == 2
    assert second.checksum_sha256 == hashlib.sha256(PDF_V2).hexdigest()
    assert first_auth.storage_object.storage_key != second_auth.storage_object.storage_key

    versions = await service.list_versions(
        workspace_id=auth_context.workspace_id,
        object_id=first.object_id,
    )
    assert [item.version for item in versions] == [2, 1]

    download = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=first.object_id,
        version=1,
        expires_in=60,
    )
    resolved = await service.resolve_download(ticket=_ticket_from_url(download.ticket.url))
    assert resolved.path.read_bytes() == PDF_V1

    exported = await service.export_object(
        workspace_id=auth_context.workspace_id,
        object_id=first.object_id,
    )
    with zipfile.ZipFile(exported.path) as archive:
        assert "manifest.json" in archive.namelist()
        assert "versions/00000001/contrato.pdf" in archive.namelist()
        assert "versions/00000002/contrato.pdf" in archive.namelist()

    physical_path = S3Service.local_object_path(first.storage_key)
    await service.soft_delete(
        workspace_id=auth_context.workspace_id,
        object_id=first.object_id,
        actor_user_id=auth_context.user_id,
    )
    assert physical_path.is_file()
    with pytest.raises(LocalObjectNotFoundError, match="não encontrado"):
        await service.create_download_authorization(
            workspace_id=auth_context.workspace_id,
            object_id=first.object_id,
            version=1,
            expires_in=60,
        )

    restored = await service.restore(
        workspace_id=auth_context.workspace_id,
        object_id=first.object_id,
        actor_user_id=auth_context.user_id,
    )
    assert restored.deleted_at is None
    assert restored.restored_by_user_id == auth_context.user_id


@pytest.mark.asyncio
async def test_retention_cannot_be_shortened_and_blocks_soft_delete(
    db_session,
    auth_context,
):
    service = LocalObjectStorageService(db_session)
    retained_until = datetime.now(UTC) + timedelta(days=30)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        retention_until=retained_until,
    )

    with pytest.raises(LocalObjectRetentionError, match="não pode ser reduzido"):
        await service.extend_retention(
            workspace_id=auth_context.workspace_id,
            object_id=uploaded.object_id,
            retention_until=retained_until - timedelta(days=1),
        )
    with pytest.raises(LocalObjectRetentionError, match="retido até"):
        await service.soft_delete(
            workspace_id=auth_context.workspace_id,
            object_id=uploaded.object_id,
            actor_user_id=auth_context.user_id,
        )


@pytest.mark.asyncio
async def test_evidence_referenced_media_cannot_enter_common_delete_flow(
    db_session,
    auth_context,
):
    service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )
    await MessageEvidenceService(db_session).record_event(
        workspace_id=auth_context.workspace_id,
        provider="meta_cloud",
        connection_key="whatsapp:test-connection",
        external_message_id="wamid.media.retained.1",
        event_type="media_captured",
        body_snapshot="[document]",
        content_type="document",
        event_payload={"mediaId": "provider-media-1"},
        storage_object_id=uploaded.id,
        storage_object_version=uploaded.version,
        media_sha256=uploaded.checksum_sha256,
        media_byte_size=uploaded.byte_size,
        media_mime_type=uploaded.detected_content_type,
        media_filename=uploaded.original_filename,
        provider_media_id="provider-media-1",
    )
    await db_session.commit()

    with pytest.raises(LocalObjectRetentionError, match="cadeia de evidência"):
        await service.soft_delete(
            workspace_id=auth_context.workspace_id,
            object_id=uploaded.object_id,
            actor_user_id=auth_context.user_id,
        )

    versions = await service.list_versions(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
    )
    assert versions[0].deleted_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "declared_type", "expected_reason", "expected_av"),
    [
        (
            b"%PDF-1.7\nEICAR-STANDARD-ANTIVIRUS-TEST-FILE\n%%EOF",
            "application/pdf",
            "simulated_eicar_signature",
            StorageAVStatus.simulated_blocked,
        ),
        (
            b"plain text disguised as a PDF",
            "application/pdf",
            "mime_magic_mismatch",
            StorageAVStatus.simulated_unverified,
        ),
    ],
)
async def test_infected_and_magic_mismatch_payloads_stay_in_quarantine(
    db_session,
    auth_context,
    payload,
    declared_type,
    expected_reason,
    expected_av,
):
    service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        data=payload,
        content_type=declared_type,
    )

    assert uploaded.state is StorageObjectState.quarantined
    assert uploaded.av_status is expected_av
    assert uploaded.quarantine_reason == expected_reason
    assert uploaded.quarantine_key is not None
    assert S3Service.local_object_path(uploaded.quarantine_key).read_bytes() == payload
    assert not S3Service.local_object_path(uploaded.storage_key).exists()


@pytest.mark.asyncio
async def test_zip_path_traversal_is_quarantined_without_extraction(db_session, auth_context):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../outside.txt", "not extracted")
    payload = stream.getvalue()
    service = LocalObjectStorageService(db_session)

    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        data=payload,
        content_type="application/zip",
    )

    assert uploaded.state is StorageObjectState.quarantined
    assert uploaded.quarantine_reason == "mime_magic_invalid"
    assert not (S3Service.LOCAL_ROOT.parent / "outside.txt").exists()


@pytest.mark.asyncio
async def test_physical_tampering_is_detected_before_download(db_session, auth_context):
    service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )
    authorization = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        version=1,
        expires_in=60,
    )
    S3Service.local_object_path(uploaded.storage_key).write_bytes(b"tampered")

    with pytest.raises(LocalObjectIntegrityError, match="diverge"):
        await service.resolve_download(ticket=_ticket_from_url(authorization.ticket.url))


@pytest.mark.asyncio
async def test_versioned_backend_never_initializes_boto3(
    db_session,
    auth_context,
    monkeypatch,
):
    boto3_client = Mock(side_effect=AssertionError("boto3 não deve ser inicializado"))
    monkeypatch.setattr("app.services.s3_service.boto3.client", boto3_client)
    service = LocalObjectStorageService(db_session)

    authorization, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )
    download = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        version=uploaded.version,
        expires_in=60,
    )
    resolved = await service.resolve_download(ticket=_ticket_from_url(download.ticket.url))
    ticket_payload = verify_token(_ticket_from_url(authorization.ticket.url))

    assert resolved.path.read_bytes() == PDF_V1
    assert ticket_payload is not None
    assert ticket_payload["workspace_id"] == str(auth_context.workspace_id)
    assert ticket_payload["object_id"] == str(uploaded.object_id)
    assert ticket_payload["version_id"] == str(uploaded.id)
    assert ticket_payload["operation"] == "object_upload"
    assert ticket_payload["aud"].endswith(":local_storage")
    boto3_client.assert_not_called()


@pytest.mark.asyncio
async def test_declared_checksum_mismatch_is_quarantined(db_session, auth_context):
    service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        checksum="0" * 64,
    )

    assert uploaded.state is StorageObjectState.quarantined
    assert uploaded.quarantine_reason == "checksum_mismatch"


def test_fake_antivirus_is_deterministic_and_marker_based():
    clean = fake_antivirus_scan(PDF_V1)
    infected = fake_antivirus_scan(b"KLM-FAKE-AV:INFECTED")

    assert clean == fake_antivirus_scan(PDF_V1) == (
        StorageAVStatus.simulated_unverified,
        None,
    )
    assert infected == fake_antivirus_scan(b"KLM-FAKE-AV:INFECTED")
    assert infected[0] is StorageAVStatus.simulated_blocked


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"%PDF-1.7\n/OpenAction <</S /JavaScript>>\n%%EOF", "simulated_pdf_active_content"),
        (b"PK\x03\x04KLM-FAKE-AV:MACRO", "simulated_macro_detected"),
        (PDF_V1 + b"PK\x03\x04polyglot", "simulated_polyglot_detected"),
        (
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-office",
            "simulated_macro_unverifiable",
        ),
    ],
)
def test_fake_antivirus_fails_closed_for_active_and_polyglot_payloads(payload, reason):
    assert fake_antivirus_scan(payload) == (StorageAVStatus.simulated_blocked, reason)


@pytest.mark.asyncio
async def test_download_ticket_epoch_is_revoked_by_delete_and_restore(
    db_session,
    auth_context,
):
    service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )
    authorization = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        version=1,
        expires_in=60,
    )
    old_ticket = authorization.ticket.token

    await service.soft_delete(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        actor_user_id=auth_context.user_id,
    )
    await service.restore(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        actor_user_id=auth_context.user_id,
    )

    with pytest.raises(LocalObjectTicketError, match="revogado"):
        await service.resolve_download(ticket=old_ticket)
    fresh = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        version=1,
        expires_in=60,
    )
    resolved = await service.resolve_download(ticket=fresh.ticket.token)
    try:
        assert resolved.handle.read() == PDF_V1
    finally:
        resolved.handle.close()


@pytest.mark.asyncio
async def test_download_and_media_tickets_are_purpose_bound_and_single_use(
    db_session,
    auth_context,
):
    service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )

    download = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        version=1,
        expires_in=60,
    )
    downloaded = await service.resolve_download(ticket=download.ticket.token)
    downloaded.handle.close()
    with pytest.raises(LocalObjectTicketError, match="revogado"):
        await service.resolve_download(ticket=download.ticket.token)

    media = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        version=1,
        expires_in=60,
        purpose="media_delivery",
    )
    media_claims = verify_token(media.ticket.token)
    assert media_claims is not None
    assert media_claims["operation"] == "media_download"
    assert media_claims["purpose"] == "media_delivery"
    assert "/api/v1/local-storage/media-download?ticket=" in media.ticket.url

    with pytest.raises(LocalObjectTicketError, match="inválido"):
        await service.resolve_download(ticket=media.ticket.token)
    delivered = await service.resolve_download(
        ticket=media.ticket.token,
        purpose="media_delivery",
    )
    delivered.handle.close()
    with pytest.raises(LocalObjectTicketError, match="revogado"):
        await service.resolve_download(
            ticket=media.ticket.token,
            purpose="media_delivery",
        )


@pytest.mark.asyncio
async def test_download_consumption_commits_before_cancelled_stream_and_replay_new_session(
    db_session,
    auth_context,
):
    service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )
    authorization = await service.create_download_authorization(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
        version=1,
        expires_in=60,
    )

    resolved = await service.resolve_download(ticket=authorization.ticket.token)
    assert db_session.in_transaction() is False
    resolved.handle.close()

    fresh_sessions = async_sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
    )
    async with fresh_sessions() as fresh_db:
        with pytest.raises(LocalObjectTicketError, match="revogado"):
            await LocalObjectStorageService(fresh_db).resolve_download(
                ticket=authorization.ticket.token
            )


@pytest.mark.asyncio
async def test_concurrent_version_creation_uses_stable_aggregate_lock(
    db_session,
    auth_context,
):
    service = LocalObjectStorageService(db_session)
    second_service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )

    async def authorize(target_service, filename: str):
        return await target_service.create_upload_authorization(
            workspace_id=auth_context.workspace_id,
            actor_user_id=auth_context.user_id,
            filename=filename,
            content_type="application/pdf",
            byte_size=len(PDF_V1),
            checksum_sha256=hashlib.sha256(PDF_V1).hexdigest(),
            retention_until=None,
            expires_in=60,
            object_id=uploaded.object_id,
        )

    first, second = await asyncio.gather(
        authorize(service, "v2.pdf"),
        authorize(second_service, "v3.pdf"),
    )
    assert {first.storage_object.version, second.storage_object.version} == {2, 3}


@pytest.mark.asyncio
async def test_concurrent_retention_and_delete_return_domain_conflict(
    db_session,
    auth_context,
):
    retention_service = LocalObjectStorageService(db_session)
    delete_service = LocalObjectStorageService(db_session)
    _, uploaded = await _authorize_and_upload(
        retention_service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
    )
    retained_until = datetime.now(UTC) + timedelta(days=2)

    retention, deletion = await asyncio.gather(
        retention_service.extend_retention(
            workspace_id=auth_context.workspace_id,
            object_id=uploaded.object_id,
            retention_until=retained_until,
        ),
        delete_service.soft_delete(
            workspace_id=auth_context.workspace_id,
            object_id=uploaded.object_id,
            actor_user_id=auth_context.user_id,
        ),
        return_exceptions=True,
    )

    assert not isinstance(retention, Exception)
    assert isinstance(deletion, LocalObjectRetentionError)
    current = await retention_service.get_version(
        workspace_id=auth_context.workspace_id,
        object_id=uploaded.object_id,
    )
    assert current.deleted_at is None
    assert current.retention_until is not None


@pytest.mark.asyncio
async def test_reconciler_recovers_interrupted_promotion_and_sweeps_orphans(
    db_session,
    auth_context,
    monkeypatch,
):
    service = LocalObjectStorageService(db_session)
    authorization = await service.create_upload_authorization(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="boundary.pdf",
        content_type="application/pdf",
        byte_size=len(PDF_V1),
        checksum_sha256=hashlib.sha256(PDF_V1).hexdigest(),
        retention_until=None,
        expires_in=60,
    )
    original_promote = service._promote_quarantine

    async def fail_promotion(*_args, **_kwargs):
        raise LocalObjectIntegrityError("boundary failure")

    monkeypatch.setattr(service, "_promote_quarantine", fail_promotion)
    with pytest.raises(LocalObjectIntegrityError, match="boundary failure"):
        await service.finalize_upload(
            ticket=authorization.ticket.token,
            data=PDF_V1,
            request_content_type="application/pdf",
        )
    monkeypatch.setattr(service, "_promote_quarantine", original_promote)

    _, blocked = await _authorize_and_upload(
        service,
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        data=b"%PDF-1.7\nEICAR-STANDARD-ANTIVIRUS-TEST-FILE\n%%EOF",
    )
    assert blocked.state is StorageObjectState.quarantined
    assert blocked.quarantine_key is not None
    S3Service.delete_local_object(blocked.quarantine_key)

    orphan_key = f"staging/{auth_context.workspace_id.hex}/orphan/payload"
    temporary_key = f"staging/{auth_context.workspace_id.hex}/orphan/partial.tmp"
    export_key = f"exports/{auth_context.workspace_id.hex}/stale.zip"
    S3Service.write_local_object_atomic(orphan_key, b"orphan")
    S3Service.write_local_object_atomic(temporary_key, b"partial")
    S3Service.write_local_object_atomic(export_key, b"stale export")
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    for key in (orphan_key, temporary_key, export_key):
        os.utime(S3Service.local_object_path(key), (old, old))

    report = await service.reconcile_workspace(
        workspace_id=auth_context.workspace_id,
        stale_after=timedelta(seconds=0),
        export_ttl=timedelta(seconds=0),
    )
    recovered = await service.get_version(
        workspace_id=auth_context.workspace_id,
        object_id=authorization.storage_object.object_id,
    )

    assert recovered.state is StorageObjectState.available
    assert report.pending_recovered == 1
    assert report.quarantine_repaired == 1
    assert report.orphaned_files_removed == 1
    assert report.temporary_files_removed == 1
    assert report.exports_removed == 1
    assert not S3Service.local_object_exists(orphan_key)
    assert not S3Service.local_object_exists(temporary_key)
    assert not S3Service.local_object_exists(export_key)

    repeated = await service.reconcile_workspace(
        workspace_id=auth_context.workspace_id,
        stale_after=timedelta(seconds=0),
        export_ttl=timedelta(seconds=0),
    )
    assert repeated == type(repeated)()


@pytest.mark.asyncio
async def test_ticket_signature_and_claim_binding_precede_rls_context(
    db_session,
    auth_context,
    monkeypatch,
):
    service = LocalObjectStorageService(db_session)
    authorization = await service.create_upload_authorization(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        filename="rls-order.pdf",
        content_type="application/pdf",
        byte_size=len(PDF_V1),
        checksum_sha256=hashlib.sha256(PDF_V1).hexdigest(),
        retention_until=None,
        expires_in=60,
    )
    set_context = AsyncMock()
    monkeypatch.setattr(
        "app.services.local_object_storage_service.set_workspace_context",
        set_context,
    )
    ticket = authorization.ticket.token
    midpoint = len(ticket) // 2
    replacement = "A" if ticket[midpoint] != "A" else "B"
    tampered = f"{ticket[:midpoint]}{replacement}{ticket[midpoint + 1:]}"

    with pytest.raises(LocalObjectTicketError, match="inválido"):
        await service.validate_upload_ticket(tampered)
    set_context.assert_not_awaited()

    payload = await service.validate_upload_ticket(ticket)
    assert payload["workspace_id"] == str(auth_context.workspace_id)
    set_context.assert_awaited_once_with(db_session, auth_context.workspace_id)


@pytest.mark.parametrize(
    "unsafe_key",
    ["../escape", "/absolute", "safe/../../escape", "safe\\..\\escape", "http://host/file"],
)
def test_local_object_path_rejects_traversal_and_urls(unsafe_key):
    with pytest.raises(ValueError, match="inválida"):
        S3Service.local_object_path(unsafe_key)


@pytest.mark.parametrize(
    "unsafe_base_url",
    ["http://169.254.169.254", "http://metadata.google.internal", "http://user:pass@test"],
)
def test_local_ticket_url_rejects_ssrf_metadata_and_credentials(monkeypatch, unsafe_base_url):
    monkeypatch.setattr(settings, "PAPERCLIP_PUBLIC_URL", unsafe_base_url)
    with pytest.raises(StorageUnavailableError):
        S3Service._local_ticket_url("/api/v1/local-storage/download", "redacted")


def test_unknown_mime_and_oversized_contract_fail_before_metadata():
    with pytest.raises(LocalObjectValidationError, match="MIME"):
        # Validation is deliberately reachable without filesystem or network effects.
        from app.services.local_object_storage_service import _validate_upload_contract

        _validate_upload_contract(
            filename="payload.exe",
            content_type="application/x-msdownload",
            byte_size=1,
            checksum_sha256=None,
            retention_until=None,
        )
