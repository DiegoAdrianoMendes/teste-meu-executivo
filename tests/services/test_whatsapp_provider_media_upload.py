from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.models.omnichannel_v6 import (
    OmnichannelProviderMediaReceipt,
    OmnichannelProviderMediaUploadIntent,
    OmnichannelProviderMediaUploadReconciliation,
)
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.media_upload import (
    WhatsAppMediaUploadError,
    resolve_media_policy,
    validate_media_content,
    validate_media_size,
)
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.provider_media_receipt_service import (
    ProviderMediaReceiptError,
    ProviderMediaReceiptService,
)
from app.services.whatsapp.strategies.meta_cloud import MetaCloudStrategy
from app.services.whatsapp_service import WhatsAppService


pytestmark = pytest.mark.usefixtures("active_subscription")


class _RecordingUploadClient:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {"id": "meta-uploaded-media-1"}
        self.calls: list[dict] = []

    async def upload_media(self, **kwargs):
        content = kwargs["file_content"].read()
        self.calls.append({**kwargs, "content": content})
        return dict(self.response)


class _UncertainUploadClient(_RecordingUploadClient):
    async def upload_media(self, **kwargs):
        self.calls.append({**kwargs, "content": kwargs["file_content"].read()})
        raise WhatsAppProviderError(
            code="WA-DELIVERY-UNCERTAIN",
            message="A resposta do upload foi perdida.",
            status_code=502,
            request_started=True,
            delivery_outcome_known=False,
        )


class _RejectedUploadClient(_RecordingUploadClient):
    async def upload_media(self, **kwargs):
        self.calls.append({**kwargs, "content": kwargs["file_content"].read()})
        raise WhatsAppProviderError(
            code="WA-002",
            message="A Meta rejeitou o arquivo.",
            status_code=400,
            provider_status_code=100,
            request_started=True,
            delivery_outcome_known=True,
        )


def _pdf_bytes(label: bytes = b"KLM") -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        + b"% "
        + label
        + b"\nxref\n0 2\n0000000000 65535 f \n"
        + b"trailer\n<< /Root 1 0 R >>\nstartxref\n42\n%%EOF"
    )


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(12, 34, 56)).save(output, format=image_format)
    return output.getvalue()


def _ooxml_bytes(content_type: str, part_name: str, *, extra_entry: bytes | None = None) -> bytes:
    output = BytesIO()
    manifest = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + f'<Override PartName="/{part_name}" ContentType="{content_type}"/>'.encode()
        + b'</Types>'
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", manifest)
        archive.writestr(part_name, b"<root>conteudo juridico</root>")
        if extra_entry is not None:
            archive.writestr("word/large.xml", extra_entry)
    return output.getvalue()


def _oversized_ooxml_manifest() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            b"<Types>" + b"x" * (2 * 1024 * 1024) + b"</Types>",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("word/document.xml", b"<document/>")
    return output.getvalue()


def _mp3_bytes() -> bytes:
    frame_header = b"\xff\xfb\x90\x64"
    frame_length = 417
    frame = frame_header + b"\x01" * (frame_length - len(frame_header))
    return frame * 2


def _aac_bytes() -> bytes:
    frame_length = 20
    header = bytes(
        (
            0xFF,
            0xF1,
            (1 << 6) | (4 << 2),
            (2 << 6) | (frame_length >> 11),
            (frame_length >> 3) & 0xFF,
            ((frame_length & 0x7) << 5) | 0x1F,
            0xFC,
        )
    )
    frame = header + b"\x01" * (frame_length - len(header))
    return frame * 2


def _amr_bytes() -> bytes:
    frame = b"\x04" + b"\x01" * 13
    return b"#!AMR\n" + frame * 2


def _ogg_page(sequence: int, payload: bytes) -> bytes:
    assert len(payload) < 255
    return (
        b"OggS"
        + b"\x00\x00"
        + (0).to_bytes(8, "little")
        + (77).to_bytes(4, "little")
        + sequence.to_bytes(4, "little")
        + b"\x00" * 4
        + b"\x01"
        + bytes((len(payload),))
        + payload
    )


def _ogg_opus_bytes() -> bytes:
    opus_head = (
        b"OpusHead\x01\x02"
        + (312).to_bytes(2, "little")
        + (48_000).to_bytes(4, "little")
        + (0).to_bytes(2, "little", signed=True)
        + b"\x00"
    )
    return _ogg_page(0, opus_head) + _ogg_page(1, b"OpusTags\x00\x00\x00\x00")


def _iso_box(kind: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def _iso_media_bytes(handler: bytes, *, brand: bytes = b"isom") -> bytes:
    ftyp = _iso_box(b"ftyp", brand + b"\x00\x00\x00\x00" + brand)
    hdlr = _iso_box(b"hdlr", b"\x00" * 8 + handler + b"\x00" * 12)
    moov = _iso_box(b"moov", _iso_box(b"trak", _iso_box(b"mdia", hdlr)))
    mdat = _iso_box(b"mdat", b"\x01" * 32)
    return ftyp + moov + mdat


async def _legacy_connection(db_session, workspace_id, *, qr: bool = False):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=(ConnectionType.QR_CODE if qr else ConnectionType.OFFICIAL_MANUAL),
        transport_mode="qr_gateway" if qr else "official_api",
        is_default=True,
        phone_number_id=None if qr else "phone-media-upload",
        instance_id="qr-media-upload" if qr else None,
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.commit()
    return connection


@pytest.mark.asyncio
async def test_runtime_upload_sends_exact_bytes_and_persists_immutable_evidence(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"exact-provider-upload-bytes")
    source_sha = hashlib.sha256(source).hexdigest()
    meta = _RecordingUploadClient()
    service = WhatsAppService(db_session)
    service.meta_client = meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )

    result = await service.upload_provider_media(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        filename="prescricao.pdf",
        content_type="application/pdf",
        file_content=BytesIO(source),
        source_sha256=source_sha,
        source_size_bytes=len(source),
    )

    stored = await db_session.scalar(
        select(OmnichannelProviderMediaReceipt).where(
            OmnichannelProviderMediaReceipt.id == result.receipt_id
        )
    )
    assert stored is not None
    assert meta.calls[0]["content"] == source
    assert meta.calls[0]["content_type"] == "application/pdf"
    assert stored.provider_media_id == meta.response["id"]
    assert stored.provider_receipt == meta.response
    assert stored.source_sha256 == source_sha
    assert stored.source_size_bytes == len(source)
    assert stored.source_content_type == "application/pdf"
    assert result.expires_at is not None


@pytest.mark.asyncio
async def test_runtime_upload_replays_same_intent_without_second_provider_call(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"idempotent")
    meta = _RecordingUploadClient()
    service = WhatsAppService(db_session)
    service.meta_client = meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    kwargs = {
        "workspace_id": auth_context.workspace_id,
        "connection_id": connection.id,
        "filename": "prescricao.pdf",
        "content_type": "application/pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_size_bytes": len(source),
        "idempotency_key": "media-upload-idempotent-1",
    }

    first = await service.upload_provider_media(
        **kwargs,
        file_content=BytesIO(source),
    )
    replay = await service.upload_provider_media(
        **kwargs,
        file_content=BytesIO(source),
    )

    assert replay.receipt_id == first.receipt_id
    assert replay.upload_intent_id == first.upload_intent_id
    assert first.deduplicated is False
    assert replay.deduplicated is True
    assert len(meta.calls) == 1
    assert await db_session.scalar(select(func.count(OmnichannelProviderMediaUploadIntent.id))) == 1


@pytest.mark.asyncio
async def test_runtime_upload_rejects_same_key_with_changed_payload(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    first_source = _pdf_bytes(b"first")
    second_source = _pdf_bytes(b"second")
    meta = _RecordingUploadClient()
    service = WhatsAppService(db_session)
    service.meta_client = meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    await service.upload_provider_media(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        filename="laudo.pdf",
        content_type="application/pdf",
        file_content=BytesIO(first_source),
        source_sha256=hashlib.sha256(first_source).hexdigest(),
        source_size_bytes=len(first_source),
        idempotency_key="media-upload-conflict-1",
    )

    with pytest.raises(ProviderMediaReceiptError) as caught:
        await service.upload_provider_media(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            filename="laudo.pdf",
            content_type="application/pdf",
            file_content=BytesIO(second_source),
            source_sha256=hashlib.sha256(second_source).hexdigest(),
            source_size_bytes=len(second_source),
            idempotency_key="media-upload-conflict-1",
        )

    assert caught.value.code == "provider_media_idempotency_conflict"
    assert caught.value.status_code == 409
    assert len(meta.calls) == 1


@pytest.mark.asyncio
async def test_uncertain_provider_response_is_checkpointed_and_never_auto_retried(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"uncertain")
    meta = _UncertainUploadClient()
    service = WhatsAppService(db_session)
    service.meta_client = meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    kwargs = {
        "workspace_id": auth_context.workspace_id,
        "connection_id": connection.id,
        "filename": "laudo.pdf",
        "content_type": "application/pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_size_bytes": len(source),
        "idempotency_key": "media-upload-uncertain-1",
    }

    with pytest.raises(ProviderMediaReceiptError) as first_error:
        await service.upload_provider_media(
            **kwargs,
            file_content=BytesIO(source),
        )
    intent = await db_session.get(
        OmnichannelProviderMediaUploadIntent,
        first_error.value.intent_id,
    )
    assert intent is not None
    assert intent.state == "delivery_uncertain"

    with pytest.raises(ProviderMediaReceiptError) as replay_error:
        await service.upload_provider_media(
            **kwargs,
            file_content=BytesIO(source),
        )
    assert replay_error.value.code == "provider_media_reconciliation_required"
    assert replay_error.value.intent_id == intent.id
    assert len(meta.calls) == 1


@pytest.mark.asyncio
async def test_human_retry_reconciliation_is_audited_before_exactly_one_new_attempt(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"uncertain-human-retry")
    uncertain_meta = _UncertainUploadClient()
    service = WhatsAppService(db_session)
    service.meta_client = uncertain_meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    kwargs = {
        "workspace_id": auth_context.workspace_id,
        "connection_id": connection.id,
        "filename": "laudo.pdf",
        "content_type": "application/pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_size_bytes": len(source),
        "idempotency_key": "media-upload-human-retry-1",
    }

    with pytest.raises(ProviderMediaReceiptError) as caught:
        await service.upload_provider_media(**kwargs, file_content=BytesIO(source))

    reconciled = await service.reconcile_provider_media_upload_intent(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        intent_id=caught.value.intent_id,
        actor_user_id=auth_context.user_id,
        decision="retry_authorized",
        reason="A Meta confirmou no painel que não recebeu a mídia.",
    )
    assert reconciled.state == "prepared"
    assert reconciled.reconciliation_decision == "retry_authorized"
    assert reconciled.reconciled_by_user_id == auth_context.user_id
    assert await db_session.scalar(
        select(func.count(OmnichannelProviderMediaUploadReconciliation.id))
    ) == 1

    retry_meta = _RecordingUploadClient({"id": "meta-after-human-retry"})
    service.meta_client = retry_meta
    completed = await service.upload_provider_media(
        **kwargs,
        file_content=BytesIO(source),
    )
    assert completed.provider_media_id == "meta-after-human-retry"
    assert len(uncertain_meta.calls) == 1
    assert len(retry_meta.calls) == 1
    intent = await db_session.get(
        OmnichannelProviderMediaUploadIntent,
        caught.value.intent_id,
    )
    assert intent is not None
    assert intent.state == "completed"
    assert intent.attempt_count == 2


@pytest.mark.asyncio
async def test_known_provider_rejection_is_traceable_and_never_auto_retried(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"known-rejection")
    meta = _RejectedUploadClient()
    service = WhatsAppService(db_session)
    service.meta_client = meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    kwargs = {
        "workspace_id": auth_context.workspace_id,
        "connection_id": connection.id,
        "filename": "laudo.pdf",
        "content_type": "application/pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_size_bytes": len(source),
        "idempotency_key": "media-upload-known-rejection-1",
    }

    with pytest.raises(ProviderMediaReceiptError) as first_error:
        await service.upload_provider_media(
            **kwargs,
            file_content=BytesIO(source),
        )
    assert first_error.value.code == "WA-002"
    assert first_error.value.status_code == 400
    assert first_error.value.intent_id is not None
    intent = await db_session.get(
        OmnichannelProviderMediaUploadIntent,
        first_error.value.intent_id,
    )
    assert intent is not None
    assert intent.state == "failed"

    with pytest.raises(ProviderMediaReceiptError) as replay_error:
        await service.upload_provider_media(
            **kwargs,
            file_content=BytesIO(source),
        )
    assert replay_error.value.code == "provider_media_upload_failed"
    assert replay_error.value.intent_id == intent.id
    assert len(meta.calls) == 1


@pytest.mark.asyncio
async def test_db_failure_after_provider_acceptance_blocks_duplicate_upload(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"db-checkpoint-failure")
    meta = _RecordingUploadClient()
    service = WhatsAppService(db_session)
    service.meta_client = meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    original_commit = db_session.commit
    commit_calls = 0

    async def fail_provider_checkpoint_once():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 3:
            raise RuntimeError("database unavailable after provider acceptance")
        await original_commit()

    monkeypatch.setattr(db_session, "commit", fail_provider_checkpoint_once)
    kwargs = {
        "workspace_id": auth_context.workspace_id,
        "connection_id": connection.id,
        "filename": "laudo.pdf",
        "content_type": "application/pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_size_bytes": len(source),
        "idempotency_key": "media-upload-db-failure-1",
    }
    with pytest.raises(ProviderMediaReceiptError) as caught:
        await service.upload_provider_media(
            **kwargs,
            file_content=BytesIO(source),
        )
    monkeypatch.setattr(db_session, "commit", original_commit)

    intent = await db_session.get(
        OmnichannelProviderMediaUploadIntent,
        caught.value.intent_id,
    )
    assert intent is not None
    assert intent.state == "dispatching"
    with pytest.raises(ProviderMediaReceiptError) as replay_error:
        await service.upload_provider_media(
            **kwargs,
            file_content=BytesIO(source),
        )
    assert replay_error.value.code == "provider_media_reconciliation_required"
    assert len(meta.calls) == 1

    reconciled = await service.reconcile_provider_media_upload_intent(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        intent_id=intent.id,
        actor_user_id=auth_context.user_id,
        decision="provider_accepted",
        reason="Resposta autenticada recuperada no log técnico da Meta.",
        provider_media_id=meta.response["id"],
        provider_receipt=meta.response,
    )
    assert reconciled.state == "completed"
    assert reconciled.receipt_id is not None
    assert reconciled.reconciliation_decision == "provider_accepted"
    assert len(meta.calls) == 1
    assert await db_session.scalar(
        select(func.count(OmnichannelProviderMediaUploadReconciliation.id))
    ) == 1


@pytest.mark.asyncio
async def test_provider_acceptance_checkpoint_recovers_receipt_without_reupload(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"receipt-commit-failure")
    meta = _RecordingUploadClient()
    service = WhatsAppService(db_session)
    service.meta_client = meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    original_commit = db_session.commit
    commit_calls = 0

    async def fail_receipt_commit_once():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 4:
            raise RuntimeError("receipt commit unavailable")
        await original_commit()

    monkeypatch.setattr(db_session, "commit", fail_receipt_commit_once)
    kwargs = {
        "workspace_id": auth_context.workspace_id,
        "connection_id": connection.id,
        "filename": "laudo.pdf",
        "content_type": "application/pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_size_bytes": len(source),
        "idempotency_key": "media-upload-recover-receipt-1",
    }
    with pytest.raises(ProviderMediaReceiptError) as caught:
        await service.upload_provider_media(
            **kwargs,
            file_content=BytesIO(source),
        )
    monkeypatch.setattr(db_session, "commit", original_commit)
    intent = await db_session.get(
        OmnichannelProviderMediaUploadIntent,
        caught.value.intent_id,
    )
    assert intent is not None
    assert intent.state == "provider_accepted"

    recovered = await service.upload_provider_media(
        **kwargs,
        file_content=BytesIO(source),
    )
    assert recovered.deduplicated is True
    assert recovered.upload_intent_id == intent.id
    assert len(meta.calls) == 1


@pytest.mark.asyncio
async def test_same_provider_media_id_converges_to_one_immutable_receipt(
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"provider-deduplication")
    meta = _RecordingUploadClient({"id": "meta-shared-media-id"})
    service = WhatsAppService(db_session)
    service.meta_client = meta
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    common = {
        "workspace_id": auth_context.workspace_id,
        "connection_id": connection.id,
        "filename": "laudo.pdf",
        "content_type": "application/pdf",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "source_size_bytes": len(source),
    }

    first = await service.upload_provider_media(
        **common,
        file_content=BytesIO(source),
        idempotency_key="provider-id-convergence-1",
    )
    second = await service.upload_provider_media(
        **common,
        file_content=BytesIO(source),
        idempotency_key="provider-id-convergence-2",
    )

    assert first.receipt_id == second.receipt_id
    assert first.upload_intent_id != second.upload_intent_id
    assert len(meta.calls) == 2
    assert await db_session.scalar(select(func.count(OmnichannelProviderMediaReceipt.id))) == 1


@pytest.mark.asyncio
async def test_runtime_upload_rejects_bytes_changed_after_checkpoint(
    db_session,
    auth_context,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    service = WhatsAppService(db_session)
    source = b"current bytes"

    with pytest.raises(WhatsAppMediaUploadError) as caught:
        await service.upload_provider_media(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            filename="laudo.pdf",
            content_type="application/pdf",
            file_content=BytesIO(source),
            source_sha256=hashlib.sha256(b"older bytes").hexdigest(),
            source_size_bytes=len(source),
        )

    assert caught.value.code == "provider_media_source_changed"


@pytest.mark.asyncio
async def test_receipt_rejects_provider_response_with_another_media_id(
    db_session,
    auth_context,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    service = WhatsAppService(db_session)
    await service.connection_service.ensure_omnichannel_projection(connection)
    native = await ProviderMediaReceiptService(db_session).resolve_native_connection(
        workspace_id=auth_context.workspace_id,
        legacy_connection_id=connection.id,
        provider="meta_cloud",
    )

    with pytest.raises(ProviderMediaReceiptError) as caught:
        await ProviderMediaReceiptService(db_session).record_verified_upload(
            workspace_id=auth_context.workspace_id,
            connection_id=native.id,
            provider="meta_cloud",
            provider_media_id="expected-media",
            media_type="image",
            source_sha256="a" * 64,
            source_size_bytes=10,
            source_content_type="image/jpeg",
            provider_receipt={"id": "different-media"},
        )

    assert caught.value.code == "provider_media_provider_id_mismatch"


def test_official_media_matrix_enforces_type_specific_limits():
    image = resolve_media_policy("image/jpeg; charset=binary")
    video = resolve_media_policy("video/mp4")
    document = resolve_media_policy("application/pdf")

    assert (image.media_type, image.max_size_bytes) == ("image", 5 * 1024 * 1024)
    assert (video.media_type, video.max_size_bytes) == ("video", 16 * 1024 * 1024)
    assert document.max_size_bytes == 100 * 1024 * 1024
    with pytest.raises(WhatsAppMediaUploadError) as too_large:
        validate_media_size(image.max_size_bytes + 1, image)
    assert too_large.value.code == "provider_media_too_large"
    with pytest.raises(WhatsAppMediaUploadError) as unsupported:
        resolve_media_policy("application/octet-stream")
    assert unsupported.value.code == "provider_media_content_type_unsupported"


@pytest.mark.parametrize(
    ("filename", "content_type", "source"),
    [
        ("arquivo.pdf", "application/pdf", b"not-a-pdf"),
        ("foto.jpg", "image/jpeg", b"\xff\xd8\xffbroken"),
        ("foto.png", "image/png", b"\x89PNG\r\n\x1a\nbroken"),
    ],
)
def test_media_content_validation_rejects_forged_pdf_jpeg_and_png(
    filename: str,
    content_type: str,
    source: bytes,
):
    policy = resolve_media_policy(content_type)
    with pytest.raises(WhatsAppMediaUploadError) as caught:
        validate_media_content(
            BytesIO(source),
            filename=filename,
            policy=policy,
        )
    assert caught.value.code == "provider_media_content_invalid"


@pytest.mark.parametrize(
    ("filename", "content_type", "source"),
    [
        ("documento.pdf", "application/pdf", _pdf_bytes()),
        ("foto.jpg", "image/jpeg", _image_bytes("JPEG")),
        ("foto.png", "image/png", _image_bytes("PNG")),
    ],
)
def test_media_content_validation_accepts_matching_real_containers(
    filename: str,
    content_type: str,
    source: bytes,
):
    validate_media_content(
        BytesIO(source),
        filename=filename,
        policy=resolve_media_policy(content_type),
    )


@pytest.mark.parametrize(
    ("filename", "content_type", "source"),
    [
        (
            "documento.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _ooxml_bytes(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                "word/document.xml",
            ),
        ),
        (
            "planilha.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _ooxml_bytes(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                "xl/workbook.xml",
            ),
        ),
        (
            "apresentacao.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            _ooxml_bytes(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
                "ppt/presentation.xml",
            ),
        ),
        ("audio.mp3", "audio/mpeg", _mp3_bytes()),
        ("audio.aac", "audio/aac", _aac_bytes()),
        ("audio.amr", "audio/amr", _amr_bytes()),
        ("audio.opus", "audio/ogg", _ogg_opus_bytes()),
        ("audio.m4a", "audio/mp4", _iso_media_bytes(b"soun")),
        ("video.mp4", "video/mp4", _iso_media_bytes(b"vide")),
        ("video.3gp", "video/3gpp", _iso_media_bytes(b"vide", brand=b"3gp6")),
    ],
)
def test_media_content_validation_accepts_bounded_office_audio_and_video_containers(
    filename: str,
    content_type: str,
    source: bytes,
):
    validate_media_content(
        BytesIO(source),
        filename=filename,
        policy=resolve_media_policy(content_type),
    )


@pytest.mark.parametrize(
    ("filename", "content_type", "source"),
    [
        ("audio.mp3", "audio/mpeg", b"ID3\x04\x00\x00\x00\x00\x00\x03bad"),
        ("audio.opus", "audio/ogg", b"OggS\x00OpusHead"),
        ("video.mp4", "video/mp4", b"\x00\x00\x00\x14ftypisomvide"),
    ],
)
def test_media_content_validation_rejects_superficial_audio_and_video_markers(
    filename: str,
    content_type: str,
    source: bytes,
):
    with pytest.raises(WhatsAppMediaUploadError) as caught:
        validate_media_content(
            BytesIO(source),
            filename=filename,
            policy=resolve_media_policy(content_type),
        )
    assert caught.value.code == "provider_media_content_invalid"


@pytest.mark.parametrize(
    "source",
    [
        _oversized_ooxml_manifest(),
        _ooxml_bytes(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            "word/document.xml",
            extra_entry=b"\x00" * (8 * 1024 * 1024),
        ),
    ],
)
def test_media_content_validation_rejects_oversized_manifest_and_zip_bomb(source: bytes):
    with pytest.raises(WhatsAppMediaUploadError) as caught:
        validate_media_content(
            BytesIO(source),
            filename="documento.docx",
            policy=resolve_media_policy(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )
    assert caught.value.code == "provider_media_content_invalid"


def test_media_content_validation_rejects_extension_mismatch_and_polyglot_tail():
    policy = resolve_media_policy("application/pdf")
    with pytest.raises(WhatsAppMediaUploadError) as mismatch:
        validate_media_content(
            BytesIO(_pdf_bytes()),
            filename="documento.jpg",
            policy=policy,
        )
    assert mismatch.value.code == "provider_media_extension_mismatch"

    with pytest.raises(WhatsAppMediaUploadError) as polyglot:
        validate_media_content(
            BytesIO(_pdf_bytes() + b"\nPK\x03\x04embedded-zip"),
            filename="documento.pdf",
            policy=policy,
        )
    assert polyglot.value.code == "provider_media_content_invalid"


@pytest.mark.asyncio
async def test_provider_media_upload_rejects_qr_connection_without_provider_call(
    db_session,
    auth_context,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id, qr=True)
    service = WhatsAppService(db_session)
    meta = _RecordingUploadClient()
    service.meta_client = meta
    source = _pdf_bytes(b"qr")

    with pytest.raises(ProviderMediaReceiptError) as caught:
        await service.upload_provider_media(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            filename="arquivo.pdf",
            content_type="application/pdf",
            file_content=BytesIO(source),
            source_sha256=hashlib.sha256(source).hexdigest(),
            source_size_bytes=len(source),
        )

    assert caught.value.code == "provider_media_official_connection_required"
    assert meta.calls == []


@pytest.mark.asyncio
async def test_native_upload_endpoint_streams_file_and_returns_provider_receipt(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"endpoint exact bytes")
    provider_calls: list[dict] = []

    async def fake_upload(_self, **kwargs):
        provider_calls.append({**kwargs, "content": kwargs["file_content"].read()})
        return {"id": "meta-endpoint-upload-1"}

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.connection_service.WhatsAppConnectionService.get_meta_access_token",
        lambda _self, _connection: "secret-token",
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.upload_media",
        fake_upload,
    )

    response = await client.post(
        f"/api/v1/whatsapp/connections/{connection.id}/media",
        files={"upload": ("laudo.pdf", source, "application/pdf")},
        headers={"Idempotency-Key": "endpoint-media-upload-1"},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["providerMediaId"] == "meta-endpoint-upload-1"
    assert payload["sourceSha256"] == hashlib.sha256(source).hexdigest()
    assert payload["sourceSizeBytes"] == len(source)
    assert payload["deduplicated"] is False
    assert provider_calls[0]["content"] == source

    intent_response = await client.get(
        f"/api/v1/whatsapp/connections/{connection.id}/media-intents/{payload['uploadIntentId']}"
    )
    assert intent_response.status_code == 200, intent_response.text
    assert intent_response.json()["state"] == "completed"
    assert intent_response.json()["receiptId"] == payload["receiptId"]


@pytest.mark.asyncio
async def test_admin_endpoint_records_retry_reconciliation_without_external_delivery(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    source = _pdf_bytes(b"endpoint-reconciliation")
    service = WhatsAppService(db_session)
    service.meta_client = _UncertainUploadClient()
    monkeypatch.setattr(
        service.connection_service,
        "get_meta_access_token",
        lambda _connection: "secret-token",
    )
    with pytest.raises(ProviderMediaReceiptError) as caught:
        await service.upload_provider_media(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            filename="laudo.pdf",
            content_type="application/pdf",
            file_content=BytesIO(source),
            source_sha256=hashlib.sha256(source).hexdigest(),
            source_size_bytes=len(source),
            idempotency_key="endpoint-reconciliation-1",
        )

    response = await client.post(
        f"/api/v1/whatsapp/connections/{connection.id}/media-intents/"
        f"{caught.value.intent_id}/reconciliation",
        json={
            "decision": "retry_authorized",
            "reason": "Conferência humana confirmou ausência no provedor.",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "prepared"
    assert payload["reconciliationDecision"] == "retry_authorized"
    assert payload["reconciledByUserId"] == str(auth_context.user_id)
    assert payload["reconciledAt"] is not None


@pytest.mark.asyncio
async def test_native_upload_endpoint_rejects_unsupported_media_before_provider_call(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    connection = await _legacy_connection(db_session, auth_context.workspace_id)
    provider_calls: list[dict] = []

    async def forbidden_upload(_self, **kwargs):
        provider_calls.append(kwargs)
        raise AssertionError("unsupported media must never reach Meta")

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.upload_media",
        forbidden_upload,
    )

    response = await client.post(
        f"/api/v1/whatsapp/connections/{connection.id}/media",
        files={"upload": ("arquivo.bin", b"binary", "application/octet-stream")},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == ("provider_media_content_type_unsupported")
    assert provider_calls == []


@pytest.mark.asyncio
async def test_meta_media_upload_uses_official_multipart_contract(monkeypatch):
    client = MetaCloudClient()
    calls: list[dict] = []

    async def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        return {"id": "meta-multipart-1"}

    monkeypatch.setattr(client, "_request", fake_request)
    source = BytesIO(b"multipart bytes")

    response = await client.upload_media(
        phone_number_id="phone-123",
        access_token="secret-token",
        filename="prescricao.pdf",
        content_type="application/pdf",
        file_content=source,
    )

    assert response["id"] == "meta-multipart-1"
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/phone-123/media"
    assert calls[0]["data"] == {"messaging_product": "whatsapp"}
    filename, file_content, content_type = calls[0]["files"]["file"]
    assert (filename, content_type) == ("prescricao.pdf", "application/pdf")
    assert file_content is source


@pytest.mark.asyncio
async def test_meta_media_upload_rejects_success_response_without_media_id(monkeypatch):
    client = MetaCloudClient()

    async def fake_request(*_args, **_kwargs):
        return {"success": True}

    monkeypatch.setattr(client, "_request", fake_request)

    with pytest.raises(WhatsAppProviderError) as caught:
        await client.upload_media(
            phone_number_id="phone-123",
            access_token="secret-token",
            filename="prescricao.pdf",
            content_type="application/pdf",
            file_content=BytesIO(b"pdf"),
        )

    assert caught.value.code == "WA-MEDIA-ID-MISSING"
    assert caught.value.delivery_uncertain is True


@pytest.mark.asyncio
async def test_meta_strategy_renders_only_preverified_provider_media():
    class _MessageClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def send_message_payload(self, **kwargs):
            self.calls.append(kwargs)
            return {"messages": [{"id": "wamid-media-1"}]}

    client = _MessageClient()
    strategy = MetaCloudStrategy(client, lambda _connection: "secret-token")
    connection = WhatsAppConnection(
        workspace_id=uuid4(),
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        phone_number_id="phone-123",
    )
    receipt_id = "b672d9d8-5909-4cd6-9649-835f77efb799"

    result = await strategy.send_structured(
        connection,
        "5511999999999",
        {
            "kind": "media",
            "type": "image",
            "providerMediaId": "meta-image-1",
            "providerMediaReceiptId": receipt_id,
        },
        verified_provider_media_receipt_ids={receipt_id},
    )

    assert result.wamid == "wamid-media-1"
    assert client.calls[0]["message"] == {
        "type": "image",
        "image": {"id": "meta-image-1"},
    }
