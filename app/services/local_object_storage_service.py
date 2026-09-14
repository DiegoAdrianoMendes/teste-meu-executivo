"""Local, tenant-safe lifecycle for versioned document objects."""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import hmac
import io
import json
import os
import re
import uuid
import weakref
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import aws_external_access_allowed
from app.db.tenant import set_workspace_context
from app.models.client import Client
from app.models.entitlement import EntitlementKey
from app.models.matter import Matter
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.s3_service import (
    IssuedLocalStorageTicket,
    S3Service,
    StorageUnavailableError,
)

MAX_ZIP_ENTRIES = 5_000
MAX_ZIP_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_EXPORT_BYTES = 250 * 1024 * 1024
MAX_STREAM_HEAD_BYTES = 64 * 1024
MAX_JSON_INSPECTION_BYTES = 8 * 1024 * 1024
SCAN_OVERLAP_BYTES = 256

_FAKE_AV_MARKERS = (
    b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE",
    b"KLM-FAKE-AV:INFECTED",
)
_PDF_ACTIVE_MARKERS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rb"/JavaScript\b",
        rb"/JS\b",
        rb"/Launch\b",
        rb"/OpenAction\b",
        rb"/AA\b",
        rb"/RichMedia\b",
        rb"/EmbeddedFile\b",
    )
)
_MACRO_MARKERS = (
    b"vbaProject.bin",
    b"AutoOpen",
    b"Document_Open",
    b"Workbook_Open",
    b"KLM-FAKE-AV:MACRO",
)
_SIMULATED_SCAN_METHOD = "local_heuristic_v1"
_OLE_MIME_TYPES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}
_MIME_ALIASES = {
    "application/x-pdf": "application/pdf",
    "image/jpg": "image/jpeg",
    "application/x-zip-compressed": "application/zip",
}
_ALLOWED_MIME_TYPES = {
    "application/json",
    "application/msword",
    "application/pdf",
    "application/rtf",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/zip",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/csv",
    "text/plain",
    "video/mp4",
    "video/quicktime",
}


class LocalObjectStorageError(RuntimeError):
    """Base error for the local object lifecycle."""


class LocalObjectNotFoundError(LocalObjectStorageError):
    pass


class LocalObjectConflictError(LocalObjectStorageError):
    pass


class LocalObjectRetentionError(LocalObjectConflictError):
    pass


class LocalObjectTicketError(LocalObjectStorageError):
    pass


class LocalObjectValidationError(LocalObjectStorageError):
    pass


class LocalObjectIntegrityError(LocalObjectStorageError):
    pass


class LocalObjectUnavailableError(LocalObjectStorageError):
    pass


@dataclass(frozen=True)
class UploadAuthorization:
    storage_object: StorageObject
    ticket: IssuedLocalStorageTicket | None
    upload_required: bool = True

    @property
    def upload_url(self) -> str | None:
        return self.ticket.url if self.ticket is not None else None

    @property
    def upload_headers(self) -> dict[str, str]:
        return (
            {"Content-Type": self.storage_object.declared_content_type}
            if self.upload_required
            else {}
        )

    @property
    def expires_at(self) -> datetime:
        return (
            self.ticket.expires_at
            if self.ticket is not None
            else self.storage_object.upload_ticket_expires_at or _utc_now()
        )


@dataclass(frozen=True)
class DownloadAuthorization:
    storage_object: StorageObject
    ticket: IssuedLocalStorageTicket


@dataclass(frozen=True)
class DownloadedObject:
    storage_object: StorageObject
    path: Path
    handle: BinaryIO


@dataclass(frozen=True)
class ExportedObject:
    path: Path
    storage_key: str
    object_id: UUID
    version_count: int
    handle: BinaryIO


@dataclass(frozen=True)
class ReconciliationReport:
    pending_recovered: int = 0
    pending_quarantined: int = 0
    quarantine_repaired: int = 0
    orphaned_files_removed: int = 0
    temporary_files_removed: int = 0
    exports_removed: int = 0


@dataclass(frozen=True)
class BoundedReconciliationResult:
    report: ReconciliationReport
    examined: int


@dataclass(frozen=True)
class PayloadInspection:
    byte_size: int
    checksum_sha256: str
    detected_content_type: str | None
    av_status: StorageAVStatus
    av_reason: str | None
    mime_reason: str | None


_PROCESS_OBJECT_LOCKS: weakref.WeakValueDictionary[
    tuple[int, UUID, UUID],
    asyncio.Lock,
] = weakref.WeakValueDictionary()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalize_mime(value: str) -> str:
    normalized = value.split(";", 1)[0].strip().lower()
    return _MIME_ALIASES.get(normalized, normalized)


def _ticket_digest(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def _validate_filename(filename: str) -> str:
    cleaned = filename.strip()
    if (
        not cleaned
        or len(cleaned) > 255
        or cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or any(ord(character) < 32 for character in cleaned)
    ):
        raise LocalObjectValidationError("Nome de arquivo inválido.")
    return cleaned


def _validate_upload_contract(
    *,
    filename: str,
    content_type: str,
    byte_size: int | None,
    checksum_sha256: str | None,
    retention_until: datetime | None,
) -> tuple[str, str, datetime | None]:
    clean_filename = _validate_filename(filename)
    normalized_type = _normalize_mime(content_type)
    if normalized_type not in _ALLOWED_MIME_TYPES:
        raise LocalObjectValidationError("Tipo MIME não permitido para documentos locais.")
    if byte_size is not None and not 0 <= byte_size <= settings.LOCAL_STORAGE_MAX_BYTES:
        raise LocalObjectValidationError("Tamanho declarado excede o limite local.")
    if checksum_sha256 is not None:
        checksum_sha256 = checksum_sha256.lower()
        if len(checksum_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in checksum_sha256
        ):
            raise LocalObjectValidationError("Checksum SHA-256 declarado é inválido.")
    normalized_retention = _as_utc(retention_until) if retention_until else None
    if normalized_retention is not None and normalized_retention <= _utc_now():
        raise LocalObjectValidationError("A retenção deve terminar no futuro.")
    return clean_filename, normalized_type, normalized_retention


def _inspect_zip_handle(handle: BinaryIO) -> str:
    try:
        handle.seek(0)
        with zipfile.ZipFile(handle) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise LocalObjectValidationError("Arquivo ZIP excede o limite de entradas.")
            total_size = 0
            names: set[str] = set()
            for entry in entries:
                normalized_name = entry.filename.replace("\\", "/")
                pure_name = PurePosixPath(normalized_name)
                if pure_name.is_absolute() or ".." in pure_name.parts:
                    raise LocalObjectValidationError("Arquivo ZIP contém caminho inseguro.")
                if entry.flag_bits & 0x1:
                    raise LocalObjectValidationError("Arquivo ZIP criptografado não é aceito.")
                total_size += entry.file_size
                if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise LocalObjectValidationError("Arquivo ZIP excede o limite descompactado.")
                names.add(normalized_name)
    except zipfile.BadZipFile as exc:
        raise LocalObjectValidationError("Assinatura ZIP inválida.") from exc
    finally:
        handle.seek(0)

    if "[Content_Types].xml" in names:
        if any(name.startswith("word/") for name in names):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if any(name.startswith("xl/") for name in names):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if any(name.startswith("ppt/") for name in names):
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return "application/zip"


def _inspect_zip(data: bytes) -> str:
    return _inspect_zip_handle(io.BytesIO(data))


def detect_mime_type(data: bytes, declared_content_type: str) -> str:
    """Detect supported file families from bytes without calling external tooling."""
    declared = _normalize_mime(declared_content_type)
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        if declared == "audio/mp4":
            return "audio/mp4"
        if declared == "video/quicktime":
            return "video/quicktime"
        return "video/mp4"
    if data.startswith(b"PK\x03\x04"):
        return _inspect_zip(data)
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and declared in _OLE_MIME_TYPES:
        return declared
    if data.startswith(b"{\\rtf"):
        return "application/rtf"
    if not data or b"\x00" in data:
        raise LocalObjectValidationError("Magic bytes não correspondem a formato permitido.")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LocalObjectValidationError(
            "Magic bytes não correspondem a formato permitido."
        ) from exc
    if declared == "application/json":
        try:
            json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LocalObjectValidationError("Conteúdo JSON inválido.") from exc
        return "application/json"
    if declared == "text/csv":
        return "text/csv"
    return "text/plain"


def fake_antivirus_scan(data: bytes) -> tuple[StorageAVStatus, str | None]:
    """Deterministic local screening; it never claims a real clean verdict."""
    if any(marker in data for marker in _FAKE_AV_MARKERS):
        return StorageAVStatus.simulated_blocked, "simulated_eicar_signature"
    if any(marker.lower() in data.lower() for marker in _MACRO_MARKERS):
        return StorageAVStatus.simulated_blocked, "simulated_macro_detected"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return StorageAVStatus.simulated_blocked, "simulated_macro_unverifiable"
    if data.startswith(b"%PDF-") and any(marker.search(data) for marker in _PDF_ACTIVE_MARKERS):
        return StorageAVStatus.simulated_blocked, "simulated_pdf_active_content"
    if data.startswith(b"%PDF-") and any(
        marker in data[5:]
        for marker in (b"PK\x03\x04", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
    ):
        return StorageAVStatus.simulated_blocked, "simulated_polyglot_detected"
    if data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")):
        if b"%PDF-" in data[8:] or b"PK\x03\x04" in data[8:]:
            return StorageAVStatus.simulated_blocked, "simulated_polyglot_detected"
    return StorageAVStatus.simulated_unverified, None


def _detect_streamed_mime_type(
    *,
    head: bytes,
    declared_content_type: str,
    handle: BinaryIO,
    byte_size: int,
    contains_nul: bool,
    utf8_valid: bool,
    json_payload: bytearray | None,
    json_too_large: bool,
) -> str:
    declared = _normalize_mime(declared_content_type)
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"ID3") or (
        len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        if declared == "audio/mp4":
            return "audio/mp4"
        if declared == "video/quicktime":
            return "video/quicktime"
        return "video/mp4"
    if head.startswith(b"PK\x03\x04"):
        return _inspect_zip_handle(handle)
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and declared in _OLE_MIME_TYPES:
        return declared
    if head.startswith(b"{\\rtf"):
        return "application/rtf"
    if byte_size == 0 or contains_nul or not utf8_valid:
        raise LocalObjectValidationError(
            "Magic bytes não correspondem a formato permitido."
        )
    if declared == "application/json":
        if json_too_large or json_payload is None:
            raise LocalObjectValidationError(
                "Conteúdo JSON excede o limite de inspeção segura."
            )
        try:
            json.loads(bytes(json_payload).decode("utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LocalObjectValidationError("Conteúdo JSON inválido.") from exc
        return "application/json"
    if declared == "text/csv":
        return "text/csv"
    return "text/plain"


def inspect_payload_handle(
    handle: BinaryIO,
    declared_content_type: str,
) -> PayloadInspection:
    digest = hashlib.sha256()
    received = 0
    head = bytearray()
    overlap = b""
    contains_nul = False
    found_fake_marker = False
    found_macro_marker = False
    found_pdf_active_marker = False
    found_zip_signature = False
    found_image_signature = False
    found_pdf_signature = False
    declared = _normalize_mime(declared_content_type)
    json_payload = bytearray() if declared == "application/json" else None
    json_too_large = False
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    utf8_valid = True

    handle.seek(0)
    while chunk := handle.read(1024 * 1024):
        received += len(chunk)
        if received > settings.LOCAL_STORAGE_MAX_BYTES:
            raise LocalObjectValidationError("Upload excede o limite local.")
        digest.update(chunk)
        if len(head) < MAX_STREAM_HEAD_BYTES:
            head.extend(chunk[: MAX_STREAM_HEAD_BYTES - len(head)])
        contains_nul = contains_nul or b"\x00" in chunk
        if decoder is not None:
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError:
                utf8_valid = False
                decoder = None
        if json_payload is not None and not json_too_large:
            if len(json_payload) + len(chunk) > MAX_JSON_INSPECTION_BYTES:
                json_payload = None
                json_too_large = True
            else:
                json_payload.extend(chunk)

        window = overlap + chunk
        lowered = window.lower()
        found_fake_marker = found_fake_marker or any(
            marker in window for marker in _FAKE_AV_MARKERS
        )
        found_macro_marker = found_macro_marker or any(
            marker.lower() in lowered for marker in _MACRO_MARKERS
        )
        found_pdf_active_marker = found_pdf_active_marker or any(
            marker.search(window) for marker in _PDF_ACTIVE_MARKERS
        )
        found_zip_signature = found_zip_signature or b"PK\x03\x04" in window
        found_image_signature = found_image_signature or any(
            marker in window
            for marker in (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
        )
        found_pdf_signature = found_pdf_signature or b"%PDF-" in window
        overlap = window[-SCAN_OVERLAP_BYTES:]

    if decoder is not None:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            utf8_valid = False

    head_bytes = bytes(head)
    if found_fake_marker:
        av_status, av_reason = (
            StorageAVStatus.simulated_blocked,
            "simulated_eicar_signature",
        )
    elif found_macro_marker:
        av_status, av_reason = (
            StorageAVStatus.simulated_blocked,
            "simulated_macro_detected",
        )
    elif head_bytes.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        av_status, av_reason = (
            StorageAVStatus.simulated_blocked,
            "simulated_macro_unverifiable",
        )
    elif head_bytes.startswith(b"%PDF-") and found_pdf_active_marker:
        av_status, av_reason = (
            StorageAVStatus.simulated_blocked,
            "simulated_pdf_active_content",
        )
    elif head_bytes.startswith(b"%PDF-") and (
        found_zip_signature or found_image_signature
    ):
        av_status, av_reason = (
            StorageAVStatus.simulated_blocked,
            "simulated_polyglot_detected",
        )
    elif head_bytes.startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")
    ) and (found_pdf_signature or found_zip_signature):
        av_status, av_reason = (
            StorageAVStatus.simulated_blocked,
            "simulated_polyglot_detected",
        )
    else:
        av_status, av_reason = StorageAVStatus.simulated_unverified, None

    detected_type: str | None = None
    mime_reason: str | None = None
    try:
        detected_type = _detect_streamed_mime_type(
            head=head_bytes,
            declared_content_type=declared,
            handle=handle,
            byte_size=received,
            contains_nul=contains_nul,
            utf8_valid=utf8_valid,
            json_payload=json_payload,
            json_too_large=json_too_large,
        )
        if detected_type != declared:
            mime_reason = "mime_magic_mismatch"
    except LocalObjectValidationError:
        mime_reason = "mime_magic_invalid"
    finally:
        handle.seek(0)

    return PayloadInspection(
        byte_size=received,
        checksum_sha256=digest.hexdigest(),
        detected_content_type=detected_type,
        av_status=av_status,
        av_reason=av_reason,
        mime_reason=mime_reason,
    )


def _sha256_handle(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


def _advisory_lock_key(workspace_id: UUID, object_id: UUID) -> int:
    raw = hashlib.sha256(workspace_id.bytes + object_id.bytes).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


@asynccontextmanager
async def _stable_object_lock(
    db: AsyncSession,
    workspace_id: UUID,
    object_id: UUID,
):
    loop_key = id(asyncio.get_running_loop())
    process_lock = _PROCESS_OBJECT_LOCKS.setdefault(
        (loop_key, workspace_id, object_id),
        asyncio.Lock(),
    )
    async with process_lock:
        if db.get_bind().dialect.name == "postgresql":
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _advisory_lock_key(workspace_id, object_id)},
            )
        yield


class LocalObjectStorageService:
    def __init__(self, db: AsyncSession, *, s3: S3Service | None = None) -> None:
        self.db = db
        self.s3 = s3 or S3Service()

    @staticmethod
    def _require_local_mode() -> None:
        if settings.is_production:
            raise LocalObjectUnavailableError(
                "O pipeline local simulado de documentos não pode operar em produção."
            )
        if aws_external_access_allowed():
            raise LocalObjectConflictError("Armazenamento documental local está desabilitado.")

    def require_writable_runtime(self) -> None:
        """Fail before callers perform network or filesystem side effects."""
        self._require_local_mode()

    async def _bind_authenticated_workspace(self, workspace_id: UUID) -> None:
        if self.db.get_bind().dialect.name == "postgresql":
            await set_workspace_context(self.db, workspace_id)

    async def _current_stock_quantity(
        self,
        *,
        workspace_id: UUID,
        entitlement: EntitlementKey,
        excluded_version_id: UUID | None = None,
    ) -> int:
        aggregate = (
            func.count(StorageObject.id)
            if entitlement == EntitlementKey.files
            else func.coalesce(func.sum(StorageObject.byte_size), 0)
        )
        statement = select(aggregate).where(
            StorageObject.workspace_id == workspace_id,
            StorageObject.deleted_at.is_(None),
            StorageObject.byte_size.is_not(None),
        )
        if excluded_version_id is not None:
            statement = statement.where(StorageObject.id != excluded_version_id)
        quantity = await self.db.scalar(statement)
        return int(quantity or 0)

    async def _enforce_stock_capacity(
        self,
        *,
        workspace_id: UUID,
        files: int,
        storage_bytes: int,
        enforcer: EntitlementEnforcementService | None = None,
        excluded_version_id: UUID | None = None,
    ) -> None:
        enforcement = enforcer or EntitlementEnforcementService(self.db)
        if files:
            await enforcement.enforce_stock(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.files,
                load_current_quantity=lambda: self._current_stock_quantity(
                    workspace_id=workspace_id,
                    entitlement=EntitlementKey.files,
                    excluded_version_id=excluded_version_id,
                ),
                requested_quantity=files,
            )
        if storage_bytes:
            await enforcement.enforce_stock(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.storage_bytes,
                load_current_quantity=lambda: self._current_stock_quantity(
                    workspace_id=workspace_id,
                    entitlement=EntitlementKey.storage_bytes,
                    excluded_version_id=excluded_version_id,
                ),
                requested_quantity=storage_bytes,
            )

    async def _decode_ticket(self, ticket: str, *, operation: str) -> dict:
        try:
            payload = self.s3.decode_local_object_ticket(ticket, operation=operation)
            workspace_id = UUID(str(payload["workspace_id"]))
            UUID(str(payload["object_id"]))
            UUID(str(payload["version_id"]))
        except (TypeError, ValueError) as exc:
            raise LocalObjectTicketError("Ticket de arquivo inválido ou expirado.") from exc
        await set_workspace_context(self.db, workspace_id)
        return payload

    async def validate_upload_ticket(self, ticket: str) -> dict:
        """Validate signed claims and install RLS before request-body staging."""
        self._require_local_mode()
        return await self._decode_ticket(ticket, operation="object_upload")

    async def preflight_upload_ticket(
        self,
        ticket: str,
        *,
        validated_payload: dict | None = None,
    ) -> dict:
        """Reject known replays without taking a row lock while the body is read."""
        payload = validated_payload or await self.validate_upload_ticket(ticket)
        record = await self._bound_ticket_record(payload, for_update=False)
        self._assert_upload_ticket_usable(record, payload, now=_utc_now())
        return payload

    @staticmethod
    def staging_key(workspace_id: UUID) -> str:
        return f"staging/{workspace_id.hex}/{uuid.uuid4().hex}/payload"

    @staticmethod
    def _storage_key(
        workspace_id: UUID,
        object_id: UUID,
        version_id: UUID,
        version: int,
    ) -> str:
        return (
            f"tenants/{workspace_id.hex}/objects/{object_id.hex}/"
            f"versions/{version:08d}-{version_id.hex}/payload"
        )

    @staticmethod
    def _quarantine_key(workspace_id: UUID, version_id: UUID) -> str:
        return f"quarantine/{workspace_id.hex}/{version_id.hex}/payload"

    async def _validate_acl_binding(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID | None,
        client_id: UUID | None,
    ) -> tuple[UUID | None, UUID | None]:
        if matter_id is not None:
            matter_client_id = await self.db.scalar(
                select(Matter.client_id).where(
                    Matter.id == matter_id,
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            if matter_client_id is None:
                raise LocalObjectNotFoundError("Caso vinculado não foi encontrado.")
            if client_id is not None and client_id != matter_client_id:
                raise LocalObjectValidationError(
                    "Cliente vinculado não corresponde ao cliente do caso."
                )
            client_id = matter_client_id
        if client_id is not None:
            existing_client = await self.db.scalar(
                select(Client.id).where(
                    Client.id == client_id,
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
            if existing_client is None:
                raise LocalObjectNotFoundError("Cliente vinculado não foi encontrado.")
        return matter_id, client_id

    async def create_upload_authorization(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        filename: str,
        content_type: str,
        byte_size: int | None,
        checksum_sha256: str | None,
        retention_until: datetime | None,
        expires_in: int,
        object_id: UUID | None = None,
        matter_id: UUID | None = None,
        client_id: UUID | None = None,
        initial_object_id: bool = False,
        idempotency_key: str | None = None,
    ) -> UploadAuthorization:
        self._require_local_mode()
        await self._bind_authenticated_workspace(workspace_id)
        filename, content_type, retention_until = _validate_upload_contract(
            filename=filename,
            content_type=content_type,
            byte_size=byte_size,
            checksum_sha256=checksum_sha256,
            retention_until=retention_until,
        )

        idempotency_key_hash: str | None = None
        if idempotency_key is not None:
            normalized_key = idempotency_key.strip()
            if not 8 <= len(normalized_key) <= 200:
                raise LocalObjectValidationError(
                    "Idempotency-Key deve conter entre 8 e 200 caracteres."
                )
            idempotency_key_hash = hashlib.sha256(
                normalized_key.encode("utf-8")
            ).hexdigest()

        create_new_object = object_id is None or initial_object_id
        object_id = object_id or (
            uuid.uuid5(workspace_id, f"storage-upload:{idempotency_key_hash}")
            if idempotency_key_hash is not None
            else uuid.uuid4()
        )
        async with _stable_object_lock(self.db, workspace_id, object_id):
            if idempotency_key_hash is not None:
                replay = await self.db.scalar(
                    select(StorageObject)
                    .where(
                        StorageObject.workspace_id == workspace_id,
                        StorageObject.idempotency_key_hash == idempotency_key_hash,
                    )
                    .with_for_update()
                )
                if replay is not None:
                    requested_matter_id, requested_client_id = (
                        await self._validate_acl_binding(
                            workspace_id=workspace_id,
                            matter_id=matter_id,
                            client_id=client_id,
                        )
                    )
                    expected_retention = (
                        _as_utc(replay.retention_until)
                        if replay.retention_until is not None
                        else None
                    )
                    if not (
                        replay.created_by_user_id == actor_user_id
                        and replay.original_filename == filename
                        and replay.declared_content_type == content_type
                        and replay.expected_byte_size == byte_size
                        and replay.expected_sha256
                        == (checksum_sha256.lower() if checksum_sha256 else None)
                        and expected_retention == retention_until
                        and replay.matter_id == requested_matter_id
                        and replay.client_id == requested_client_id
                        and (not initial_object_id or replay.object_id == object_id)
                    ):
                        raise LocalObjectConflictError(
                            "Idempotency-Key já foi usada por outra autorização de upload."
                        )
                    if replay.deleted_at is not None or replay.av_status in {
                        StorageAVStatus.infected,
                        StorageAVStatus.simulated_blocked,
                    }:
                        raise LocalObjectConflictError(
                            "Upload idempotente já foi bloqueado ou excluído."
                        )
                    if replay.uploaded_at is not None:
                        return UploadAuthorization(
                            storage_object=replay,
                            ticket=None,
                            upload_required=False,
                        )
                    ticket = self._issue_upload_ticket(replay, expires_in=expires_in)
                    await self.db.flush()
                    return UploadAuthorization(storage_object=replay, ticket=ticket)

            latest = await self.db.scalar(
                select(StorageObject)
                .where(
                    StorageObject.workspace_id == workspace_id,
                    StorageObject.object_id == object_id,
                )
                .order_by(StorageObject.version.desc())
                .limit(1)
                .with_for_update()
            )
            if latest is None:
                if not create_new_object:
                    raise LocalObjectNotFoundError("Objeto não encontrado.")
                version = 1
                matter_id, client_id = await self._validate_acl_binding(
                    workspace_id=workspace_id,
                    matter_id=matter_id,
                    client_id=client_id,
                )
            else:
                if initial_object_id:
                    raise LocalObjectConflictError("Identificador de objeto já está em uso.")
                if latest.deleted_at is not None:
                    raise LocalObjectConflictError(
                        "Restaure o objeto antes de criar nova versão."
                    )
                if matter_id is not None and matter_id != latest.matter_id:
                    raise LocalObjectConflictError("O vínculo do caso é imutável entre versões.")
                if client_id is not None and client_id != latest.client_id:
                    raise LocalObjectConflictError("O vínculo do cliente é imutável entre versões.")
                matter_id, client_id = latest.matter_id, latest.client_id
                version = latest.version + 1
                if latest.retention_until is not None:
                    inherited = _as_utc(latest.retention_until)
                    if retention_until is None or retention_until < inherited:
                        retention_until = inherited

            version_id = uuid.uuid4()
            storage_object = StorageObject(
                id=version_id,
                workspace_id=workspace_id,
                object_id=object_id,
                version=version,
                matter_id=matter_id,
                client_id=client_id,
                original_filename=filename,
                declared_content_type=content_type,
                expected_byte_size=byte_size,
                expected_sha256=checksum_sha256.lower() if checksum_sha256 else None,
                idempotency_key_hash=idempotency_key_hash,
                storage_key=self._storage_key(workspace_id, object_id, version_id, version),
                quarantine_key=self._quarantine_key(workspace_id, version_id),
                state=StorageObjectState.pending,
                av_status=StorageAVStatus.pending,
                retention_until=retention_until,
                created_by_user_id=actor_user_id,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(storage_object)
                    await self.db.flush()
            except IntegrityError as exc:
                raise LocalObjectConflictError(
                    "Outra operação criou esta versão; tente novamente."
                ) from exc

            ticket = self._issue_upload_ticket(storage_object, expires_in=expires_in)
            await self.db.flush()
            await self.db.refresh(storage_object)
            return UploadAuthorization(storage_object=storage_object, ticket=ticket)

    def _issue_upload_ticket(
        self,
        storage_object: StorageObject,
        *,
        expires_in: int,
    ) -> IssuedLocalStorageTicket:
        try:
            ticket = self.s3.issue_local_object_ticket(
                operation="object_upload",
                workspace_id=storage_object.workspace_id,
                object_id=storage_object.object_id,
                version_id=storage_object.id,
                version=storage_object.version,
                s3_key=storage_object.storage_key,
                content_type=storage_object.declared_content_type,
                expires_in=expires_in,
                expected_byte_size=storage_object.expected_byte_size,
                expected_sha256=storage_object.expected_sha256,
                ticket_epoch=storage_object.ticket_epoch,
            )
        except (StorageUnavailableError, ValueError) as exc:
            raise LocalObjectUnavailableError(
                "Não foi possível emitir o ticket do armazenamento local."
            ) from exc
        storage_object.upload_ticket_hash = _ticket_digest(ticket.jti)
        storage_object.upload_ticket_expires_at = ticket.expires_at
        return ticket

    async def _bound_ticket_record(
        self,
        payload: dict,
        *,
        for_update: bool = True,
    ) -> StorageObject:
        workspace_id = UUID(str(payload["workspace_id"]))
        object_id = UUID(str(payload["object_id"]))
        version_id = UUID(str(payload["version_id"]))
        statement = select(StorageObject).where(
            StorageObject.id == version_id,
            StorageObject.workspace_id == workspace_id,
            StorageObject.object_id == object_id,
            StorageObject.version == int(payload["version"]),
        )
        if for_update:
            statement = statement.with_for_update()
        record = await self.db.scalar(statement)
        if record is None:
            raise LocalObjectTicketError("Ticket não corresponde a um objeto acessível.")
        if not hmac.compare_digest(record.storage_key, str(payload["s3_key"])):
            raise LocalObjectTicketError("Ticket não corresponde à chave do objeto.")
        is_download = payload.get("operation") in {"object_download", "media_download"}
        bound_content_type = (
            record.detected_content_type or record.declared_content_type
            if is_download
            else record.declared_content_type
        )
        bound_size = record.byte_size if is_download else record.expected_byte_size
        bound_checksum = record.checksum_sha256 if is_download else record.expected_sha256
        if not hmac.compare_digest(
            bound_content_type,
            _normalize_mime(str(payload["content_type"])),
        ):
            raise LocalObjectTicketError("Ticket não corresponde ao tipo MIME do objeto.")
        if payload.get("expected_byte_size") != bound_size:
            raise LocalObjectTicketError("Ticket não corresponde ao tamanho declarado.")
        ticket_checksum = payload.get("expected_sha256")
        if ticket_checksum != bound_checksum:
            raise LocalObjectTicketError("Ticket não corresponde ao checksum declarado.")
        if int(payload["ticket_epoch"]) != record.ticket_epoch:
            raise LocalObjectTicketError("Ticket foi revogado pelo ciclo de vida do objeto.")
        return record

    @staticmethod
    def _assert_upload_ticket_usable(
        record: StorageObject,
        payload: dict,
        *,
        now: datetime,
    ) -> None:
        if record.deleted_at is not None:
            raise LocalObjectConflictError("Objeto excluído não aceita upload.")
        if record.state is not StorageObjectState.pending:
            raise LocalObjectConflictError("Ticket de upload já foi consumido.")
        if (
            record.upload_ticket_hash is None
            or not hmac.compare_digest(
                record.upload_ticket_hash,
                _ticket_digest(str(payload["jti"])),
            )
            or record.upload_ticket_consumed_at is not None
            or record.upload_ticket_expires_at is None
            or _as_utc(record.upload_ticket_expires_at) <= now
        ):
            raise LocalObjectTicketError(
                "Ticket de upload revogado, consumido ou expirado."
            )

    async def _inspect_local_payload(
        self,
        key: str,
        declared_content_type: str,
    ) -> PayloadInspection:
        try:
            with self.s3.open_local_object(key) as handle:
                return await asyncio.to_thread(
                    inspect_payload_handle,
                    handle,
                    declared_content_type,
                )
        except LocalObjectStorageError:
            raise
        except (OSError, ValueError, StorageUnavailableError) as exc:
            raise LocalObjectIntegrityError("Payload local inseguro ou indisponível.") from exc

    async def _sha256_key(self, key: str) -> str:
        try:
            with self.s3.open_local_object(key) as handle:
                return await asyncio.to_thread(_sha256_handle, handle)
        except (OSError, ValueError, StorageUnavailableError) as exc:
            raise LocalObjectIntegrityError("Payload local inseguro ou indisponível.") from exc

    async def _local_exists(self, key: str) -> bool:
        try:
            return await asyncio.to_thread(self.s3.local_object_exists, key)
        except (OSError, ValueError, StorageUnavailableError) as exc:
            raise LocalObjectIntegrityError(
                "Filesystem local inseguro durante reconciliação."
            ) from exc

    async def _local_inventory(self, prefix: str) -> list[tuple[str, os.stat_result]]:
        try:
            return await asyncio.to_thread(
                lambda: list(self.s3.iter_local_objects(prefix))
            )
        except (OSError, ValueError, StorageUnavailableError) as exc:
            raise LocalObjectIntegrityError(
                "Filesystem local inseguro durante varredura."
            ) from exc

    async def _delete_local_key(self, key: str) -> None:
        try:
            await asyncio.to_thread(
                self.s3.delete_local_object,
                key,
                missing_ok=True,
            )
        except (OSError, ValueError, StorageUnavailableError) as exc:
            raise LocalObjectIntegrityError(
                "Não foi possível remover artefato local inseguro."
            ) from exc

    async def _replace_local_key(self, source_key: str, destination_key: str) -> None:
        try:
            await asyncio.to_thread(
                self.s3.replace_local_object,
                source_key,
                destination_key,
            )
        except (OSError, ValueError, StorageUnavailableError) as exc:
            raise LocalObjectIntegrityError(
                "Não foi possível recuperar a transição física do objeto."
            ) from exc

    async def _place_in_quarantine(
        self,
        *,
        staging_key: str,
        quarantine_key: str,
    ) -> None:
        try:
            quarantine_exists = await asyncio.to_thread(
                self.s3.local_object_exists,
                quarantine_key,
            )
            if quarantine_exists:
                staged_checksum, quarantine_checksum = await asyncio.gather(
                    self._sha256_key(staging_key),
                    self._sha256_key(quarantine_key),
                )
                if not hmac.compare_digest(staged_checksum, quarantine_checksum):
                    raise LocalObjectIntegrityError(
                        "A quarentena já contém bytes diferentes para esta versão."
                    )
                await asyncio.to_thread(
                    self.s3.delete_local_object,
                    staging_key,
                    missing_ok=True,
                )
                return
            await asyncio.to_thread(
                self.s3.replace_local_object,
                staging_key,
                quarantine_key,
            )
        except (OSError, ValueError, StorageUnavailableError) as exc:
            if isinstance(exc, LocalObjectStorageError):
                raise
            raise LocalObjectIntegrityError(
                "Não foi possível mover o payload para a quarentena segura."
            ) from exc

    async def _evaluate_quarantine_payload(
        self,
        record: StorageObject,
        *,
        now: datetime,
        enforcer: EntitlementEnforcementService | None = None,
        quota_prevalidated: bool = False,
    ) -> StorageObject:
        if record.quarantine_key is None:
            raise LocalObjectIntegrityError("Objeto pendente não possui chave de quarentena.")
        enforcement = enforcer or EntitlementEnforcementService(self.db)
        await enforcement.lock_workspace(record.workspace_id)
        inspection = await self._inspect_local_payload(
            record.quarantine_key,
            record.declared_content_type,
        )
        if not quota_prevalidated:
            await self._enforce_stock_capacity(
                workspace_id=record.workspace_id,
                files=1,
                storage_bytes=inspection.byte_size,
                enforcer=enforcement,
                excluded_version_id=record.id,
            )

        size_reason = None
        if (
            record.expected_byte_size is not None
            and inspection.byte_size != record.expected_byte_size
        ):
            size_reason = "size_mismatch"
        checksum_reason = None
        if record.expected_sha256 and not hmac.compare_digest(
            record.expected_sha256,
            inspection.checksum_sha256,
        ):
            checksum_reason = "checksum_mismatch"

        record.byte_size = inspection.byte_size
        record.checksum_sha256 = inspection.checksum_sha256
        record.detected_content_type = inspection.detected_content_type
        record.av_status = inspection.av_status
        record.scan_method = _SIMULATED_SCAN_METHOD
        record.uploaded_at = record.uploaded_at or now
        record.scanned_at = now
        record.upload_ticket_consumed_at = record.upload_ticket_consumed_at or now
        record.upload_ticket_hash = None

        quarantine_reason = (
            inspection.av_reason
            or size_reason
            or checksum_reason
            or inspection.mime_reason
        )
        if quarantine_reason:
            record.state = StorageObjectState.quarantined
            record.quarantine_reason = quarantine_reason
            return record
        if (
            settings.is_production
            or inspection.av_status is not StorageAVStatus.simulated_unverified
        ):
            record.state = StorageObjectState.quarantined
            record.quarantine_reason = "av_real_verdict_required"
            return record

        await self._promote_quarantine(record, inspection.checksum_sha256)
        record.state = StorageObjectState.available
        record.quarantine_key = None
        record.quarantine_reason = None
        return record

    async def _promote_quarantine(self, record: StorageObject, checksum: str) -> None:
        if record.quarantine_key is None:
            raise LocalObjectIntegrityError("Objeto não possui payload em quarentena.")
        try:
            final_exists = await asyncio.to_thread(
                self.s3.local_object_exists,
                record.storage_key,
            )
            if final_exists:
                existing_checksum = await self._sha256_key(record.storage_key)
                if not hmac.compare_digest(existing_checksum, checksum):
                    raise LocalObjectIntegrityError(
                        "Versão física já existe com outro checksum."
                    )
                await asyncio.to_thread(
                    self.s3.delete_local_object,
                    record.quarantine_key,
                    missing_ok=True,
                )
                return
            await asyncio.to_thread(
                self.s3.replace_local_object,
                record.quarantine_key,
                record.storage_key,
            )
        except (OSError, ValueError, StorageUnavailableError) as exc:
            if isinstance(exc, LocalObjectStorageError):
                raise
            raise LocalObjectIntegrityError("Promoção física do objeto falhou.") from exc

    async def finalize_upload(
        self,
        *,
        ticket: str,
        data: bytes,
        request_content_type: str | None,
    ) -> StorageObject:
        self._require_local_mode()
        if len(data) > settings.LOCAL_STORAGE_MAX_BYTES:
            raise LocalObjectValidationError("Upload excede o limite local.")
        payload = await self._decode_ticket(ticket, operation="object_upload")
        workspace_id = UUID(str(payload["workspace_id"]))
        staging_key = self.staging_key(workspace_id)
        try:
            await asyncio.to_thread(self.s3.write_local_object_atomic, staging_key, data)
            return await self._finalize_staged_payload(
                payload=payload,
                staging_key=staging_key,
                request_content_type=request_content_type,
            )
        finally:
            try:
                await asyncio.to_thread(
                    self.s3.delete_local_object,
                    staging_key,
                    missing_ok=True,
                )
            except (OSError, ValueError, StorageUnavailableError):
                pass

    async def finalize_staged_upload(
        self,
        *,
        ticket: str,
        staging_key: str,
        request_content_type: str | None,
        validated_payload: dict | None = None,
    ) -> StorageObject:
        self._require_local_mode()
        payload = validated_payload or await self._decode_ticket(
            ticket,
            operation="object_upload",
        )
        workspace_id = UUID(str(payload["workspace_id"]))
        expected_prefix = f"staging/{workspace_id.hex}/"
        if not staging_key.startswith(expected_prefix):
            raise LocalObjectIntegrityError("Staging não corresponde ao workspace do ticket.")
        try:
            return await self._finalize_staged_payload(
                payload=payload,
                staging_key=staging_key,
                request_content_type=request_content_type,
            )
        finally:
            try:
                await asyncio.to_thread(
                    self.s3.delete_local_object,
                    staging_key,
                    missing_ok=True,
                )
            except (OSError, ValueError, StorageUnavailableError):
                pass

    async def _finalize_staged_payload(
        self,
        *,
        payload: dict,
        staging_key: str,
        request_content_type: str | None,
    ) -> StorageObject:
        workspace_id = UUID(str(payload["workspace_id"]))
        object_id = UUID(str(payload["object_id"]))
        try:
            staged_size = (
                await asyncio.to_thread(self.s3.local_object_stat, staging_key)
            ).st_size
        except (OSError, ValueError, StorageUnavailableError) as exc:
            raise LocalObjectIntegrityError("Staging de upload não foi encontrado.") from exc
        if staged_size > settings.LOCAL_STORAGE_MAX_BYTES:
            raise LocalObjectValidationError("Upload excede o limite local.")

        enforcer = EntitlementEnforcementService(self.db)
        await enforcer.lock_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            record = await self._bound_ticket_record(payload)
            now = _utc_now()
            self._assert_upload_ticket_usable(record, payload, now=now)
            actual_header_type = _normalize_mime(request_content_type or "")
            if actual_header_type != record.declared_content_type:
                raise LocalObjectValidationError("Content-Type diverge do upload autorizado.")
            if record.quarantine_key is None:
                raise LocalObjectIntegrityError("Objeto pendente não possui chave de quarentena.")

            await self._enforce_stock_capacity(
                workspace_id=workspace_id,
                files=1,
                storage_bytes=staged_size,
                enforcer=enforcer,
                excluded_version_id=record.id,
            )
            await self._place_in_quarantine(
                staging_key=staging_key,
                quarantine_key=record.quarantine_key,
            )
            record.uploaded_at = now
            record.upload_ticket_consumed_at = now
            record.upload_ticket_hash = None
            await self._evaluate_quarantine_payload(
                record,
                now=now,
                enforcer=enforcer,
                quota_prevalidated=True,
            )
            await self.db.flush()
            await self.db.refresh(record)
            return record

    async def list_objects(
        self,
        *,
        workspace_id: UUID,
        include_deleted: bool = False,
        include_sensitive: bool = True,
    ) -> list[StorageObject]:
        await self._bind_authenticated_workspace(workspace_id)
        latest_versions = (
            select(
                StorageObject.object_id.label("object_id"),
                func.max(StorageObject.version).label("version"),
            )
            .where(StorageObject.workspace_id == workspace_id)
            .group_by(StorageObject.object_id)
            .subquery()
        )
        statement = (
            select(StorageObject)
            .join(
                latest_versions,
                (StorageObject.object_id == latest_versions.c.object_id)
                & (StorageObject.version == latest_versions.c.version),
            )
            .where(StorageObject.workspace_id == workspace_id)
            .order_by(StorageObject.created_at.desc())
        )
        if not include_deleted:
            statement = statement.where(StorageObject.deleted_at.is_(None))
        if not include_sensitive:
            statement = statement.where(
                StorageObject.matter_id.is_(None),
                StorageObject.client_id.is_(None),
            )
        return list((await self.db.scalars(statement)).all())

    async def list_versions(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        include_deleted: bool = False,
    ) -> list[StorageObject]:
        await self._bind_authenticated_workspace(workspace_id)
        return await self._list_versions_unlocked(
            workspace_id=workspace_id,
            object_id=object_id,
            include_deleted=include_deleted,
        )

    async def _list_versions_unlocked(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        include_deleted: bool,
    ) -> list[StorageObject]:
        statement = (
            select(StorageObject)
            .where(
                StorageObject.workspace_id == workspace_id,
                StorageObject.object_id == object_id,
            )
            .order_by(StorageObject.version.desc())
        )
        if not include_deleted:
            statement = statement.where(StorageObject.deleted_at.is_(None))
        records = list((await self.db.scalars(statement)).all())
        if not records:
            raise LocalObjectNotFoundError("Objeto não encontrado.")
        return records

    async def get_version(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        version: int | None = None,
        include_deleted: bool = False,
    ) -> StorageObject:
        await self._bind_authenticated_workspace(workspace_id)
        statement = select(StorageObject).where(
            StorageObject.workspace_id == workspace_id,
            StorageObject.object_id == object_id,
        )
        if version is None:
            statement = statement.order_by(StorageObject.version.desc()).limit(1)
        else:
            statement = statement.where(StorageObject.version == version)
        if not include_deleted:
            statement = statement.where(StorageObject.deleted_at.is_(None))
        record = await self.db.scalar(statement)
        if record is None:
            raise LocalObjectNotFoundError("Objeto não encontrado.")
        return record

    async def create_download_authorization(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        version: int | None,
        expires_in: int,
        purpose: str = "content_download",
    ) -> DownloadAuthorization:
        self._require_local_mode()
        operation = {
            "content_download": "object_download",
            "media_delivery": "media_download",
        }.get(purpose)
        if operation is None:
            raise LocalObjectValidationError("Finalidade de download inválida.")
        await self._bind_authenticated_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            record = await self.get_version(
                workspace_id=workspace_id,
                object_id=object_id,
                version=version,
            )
            if record.state is not StorageObjectState.available or not record.checksum_sha256:
                raise LocalObjectConflictError("Versão não está disponível para download.")
            try:
                ticket = self.s3.issue_local_object_ticket(
                    operation=operation,
                    workspace_id=workspace_id,
                    object_id=object_id,
                    version_id=record.id,
                    version=record.version,
                    s3_key=record.storage_key,
                    content_type=record.detected_content_type or record.declared_content_type,
                    expires_in=expires_in,
                    expected_byte_size=record.byte_size,
                    expected_sha256=record.checksum_sha256,
                    ticket_epoch=record.ticket_epoch,
                )
            except (StorageUnavailableError, ValueError) as exc:
                raise LocalObjectUnavailableError(
                    "Não foi possível emitir o ticket do armazenamento local."
                ) from exc
            return DownloadAuthorization(storage_object=record, ticket=ticket)

    async def resolve_download(
        self,
        *,
        ticket: str,
        purpose: str = "content_download",
    ) -> DownloadedObject:
        self._require_local_mode()
        operation = {
            "content_download": "object_download",
            "media_delivery": "media_download",
        }.get(purpose)
        if operation is None:
            raise LocalObjectValidationError("Finalidade de download inválida.")
        payload = await self._decode_ticket(ticket, operation=operation)
        workspace_id = UUID(str(payload["workspace_id"]))
        object_id = UUID(str(payload["object_id"]))
        async with _stable_object_lock(self.db, workspace_id, object_id):
            snapshot = await self._bound_ticket_record(payload, for_update=False)
            if (
                snapshot.deleted_at is not None
                or snapshot.state is not StorageObjectState.available
            ):
                raise LocalObjectNotFoundError("Objeto não está disponível.")
            if not snapshot.checksum_sha256:
                raise LocalObjectIntegrityError("Objeto disponível não possui checksum.")
            expected_checksum = str(payload.get("expected_sha256") or "")
            if not hmac.compare_digest(snapshot.checksum_sha256, expected_checksum):
                raise LocalObjectTicketError("Ticket não corresponde ao checksum atual.")
            try:
                handle = await asyncio.to_thread(
                    self.s3.open_local_object,
                    snapshot.storage_key,
                )
            except (OSError, ValueError, StorageUnavailableError) as exc:
                raise LocalObjectIntegrityError("Payload físico não foi encontrado.") from exc
            try:
                actual_checksum = await asyncio.to_thread(_sha256_handle, handle)
                if not hmac.compare_digest(snapshot.checksum_sha256, actual_checksum):
                    raise LocalObjectIntegrityError(
                        "Checksum do payload físico diverge dos metadados."
                    )
                record = await self._bound_ticket_record(payload, for_update=True)
                if (
                    record.deleted_at is not None
                    or record.state is not StorageObjectState.available
                ):
                    raise LocalObjectNotFoundError("Objeto não está disponível.")
                if not record.checksum_sha256 or not hmac.compare_digest(
                    record.checksum_sha256,
                    actual_checksum,
                ):
                    raise LocalObjectIntegrityError(
                        "Checksum do objeto mudou durante a autorização."
                    )
                record.ticket_epoch += 1
                path = self.s3.local_object_path(record.storage_key)
                await self.db.commit()
                return DownloadedObject(
                    storage_object=record,
                    path=path,
                    handle=handle,
                )
            except Exception:
                handle.close()
                raise

    async def soft_delete(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        actor_user_id: UUID | None,
    ) -> StorageObject:
        await self._bind_authenticated_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            records = await self._list_versions_unlocked(
                workspace_id=workspace_id,
                object_id=object_id,
                include_deleted=True,
            )
            # Evidence-referenced payloads leave the ordinary document lifecycle.
            # A formal erasure workflow must be explicit, platform-privileged and
            # independently audited; this generic endpoint cannot make evidence
            # unavailable after it has entered a custody chain.
            from app.models.omnichannel_v6 import OmnichannelMessageEvidenceEvent

            evidence_reference = await self.db.scalar(
                select(OmnichannelMessageEvidenceEvent.id)
                .where(
                    OmnichannelMessageEvidenceEvent.workspace_id == workspace_id,
                    OmnichannelMessageEvidenceEvent.storage_object_id.in_(
                        [record.id for record in records]
                    ),
                )
                .limit(1)
            )
            if evidence_reference is not None:
                raise LocalObjectRetentionError(
                    "Objeto vinculado a uma cadeia de evidência não pode ser excluído "
                    "pelo fluxo comum."
                )
            now = _utc_now()
            retained_until = max(
                (
                    _as_utc(record.retention_until)
                    for record in records
                    if record.retention_until
                ),
                default=None,
            )
            if retained_until is not None and retained_until > now:
                raise LocalObjectRetentionError(
                    f"Objeto retido até {retained_until.isoformat()} e não pode ser excluído."
                )
            for record in records:
                if record.deleted_at is None:
                    record.deleted_at = now
                    record.deleted_by_user_id = actor_user_id
                    record.upload_ticket_hash = None
                    record.ticket_epoch += 1
                    if record.state is StorageObjectState.pending:
                        record.upload_ticket_consumed_at = now
            await self.db.flush()
            await self.db.refresh(records[0])
            return records[0]

    async def restore(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        actor_user_id: UUID | None,
    ) -> StorageObject:
        await self._bind_authenticated_workspace(workspace_id)
        enforcer = EntitlementEnforcementService(self.db)
        await enforcer.lock_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            records = await self._list_versions_unlocked(
                workspace_id=workspace_id,
                object_id=object_id,
                include_deleted=True,
            )
            deleted_records = [record for record in records if record.deleted_at is not None]
            if not deleted_records:
                return records[0]
            restorable = [record for record in deleted_records if record.byte_size is not None]
            if not restorable:
                active = next(
                    (record for record in records if record.deleted_at is None),
                    None,
                )
                if active is not None:
                    return active
                raise LocalObjectConflictError(
                    "Objeto sem payload materializado não pode ser restaurado."
                )
            await self._enforce_stock_capacity(
                workspace_id=workspace_id,
                files=len(restorable),
                storage_bytes=sum(record.byte_size or 0 for record in restorable),
                enforcer=enforcer,
            )
            now = _utc_now()
            for record in restorable:
                record.deleted_at = None
                record.restored_at = now
                record.restored_by_user_id = actor_user_id
                record.ticket_epoch += 1
            await self.db.flush()
            await self.db.refresh(restorable[0])
            return restorable[0]

    async def bind_acl(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        matter_id: UUID,
        client_id: UUID | None = None,
    ) -> StorageObject:
        """Bind a workspace-only object after its owning matter becomes known."""
        await self._bind_authenticated_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            records = await self._list_versions_unlocked(
                workspace_id=workspace_id,
                object_id=object_id,
                include_deleted=True,
            )
            matter_id, client_id = await self._validate_acl_binding(
                workspace_id=workspace_id,
                matter_id=matter_id,
                client_id=client_id,
            )
            for record in records:
                if record.matter_id not in {None, matter_id}:
                    raise LocalObjectConflictError("Objeto já pertence a outro caso.")
                if record.client_id not in {None, client_id}:
                    raise LocalObjectConflictError("Objeto já pertence a outro cliente.")
            for record in records:
                record.matter_id = matter_id
                record.client_id = client_id
            await self.db.flush()
            await self.db.refresh(records[0])
            return records[0]

    async def extend_retention(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        retention_until: datetime,
    ) -> StorageObject:
        await self._bind_authenticated_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            records = await self._list_versions_unlocked(
                workspace_id=workspace_id,
                object_id=object_id,
                include_deleted=True,
            )
            requested = _as_utc(retention_until)
            if requested <= _utc_now():
                raise LocalObjectValidationError("A retenção deve terminar no futuro.")
            for record in records:
                if record.retention_until and requested < _as_utc(record.retention_until):
                    raise LocalObjectRetentionError(
                        "O prazo de retenção não pode ser reduzido."
                    )
            for record in records:
                record.retention_until = requested
            await self.db.flush()
            await self.db.refresh(records[0])
            return records[0]

    async def export_object(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
    ) -> ExportedObject:
        self._require_local_mode()
        await self._bind_authenticated_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            records = await self._list_versions_unlocked(
                workspace_id=workspace_id,
                object_id=object_id,
                include_deleted=True,
            )
            available = [
                record
                for record in reversed(records)
                if record.state is StorageObjectState.available and record.deleted_at is None
            ]
            if not available:
                raise LocalObjectConflictError(
                    "Objeto não possui versões disponíveis para exportação."
                )
            export_key = f"exports/{workspace_id.hex}/{uuid.uuid4().hex}.zip"
            await asyncio.to_thread(self._build_export, export_key, records, available)
            try:
                handle = await asyncio.to_thread(self.s3.open_local_object, export_key)
            except (OSError, ValueError, StorageUnavailableError) as exc:
                raise LocalObjectIntegrityError("Exportação temporária não foi criada.") from exc
            return ExportedObject(
                path=self.s3.local_object_path(export_key),
                storage_key=export_key,
                object_id=object_id,
                version_count=len(available),
                handle=handle,
            )

    def _build_export(
        self,
        export_key: str,
        records: list[StorageObject],
        available: list[StorageObject],
    ) -> None:
        total_size = sum(record.byte_size or 0 for record in available)
        if total_size > MAX_EXPORT_BYTES:
            raise LocalObjectValidationError("Exportação excede o limite local.")

        manifest = {
            "schemaVersion": 1,
            "objectId": str(records[0].object_id),
            "exportedAt": _utc_now().isoformat(),
            "versions": [self._manifest_entry(record) for record in reversed(records)],
        }
        handle: BinaryIO | None = None
        try:
            handle = self.s3.create_local_object(export_key)
            with zipfile.ZipFile(
                handle,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                for record in available:
                    if not record.checksum_sha256:
                        raise LocalObjectIntegrityError("Versão física ausente na exportação.")
                    filename = _validate_filename(record.original_filename)
                    digest = hashlib.sha256()
                    copied = 0
                    with self.s3.open_local_object(record.storage_key) as source:
                        with archive.open(
                            f"versions/{record.version:08d}/{filename}",
                            mode="w",
                            force_zip64=True,
                        ) as destination:
                            while chunk := source.read(1024 * 1024):
                                copied += len(chunk)
                                if copied > MAX_EXPORT_BYTES:
                                    raise LocalObjectValidationError(
                                        "Exportação excede o limite local."
                                    )
                                digest.update(chunk)
                                destination.write(chunk)
                    if record.byte_size is not None and copied != record.byte_size:
                        raise LocalObjectIntegrityError(
                            "Tamanho divergente durante exportação."
                        )
                    if not hmac.compare_digest(record.checksum_sha256, digest.hexdigest()):
                        raise LocalObjectIntegrityError(
                            "Checksum divergente durante exportação."
                        )
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True),
                )
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            if handle is not None:
                handle.close()
            self.s3.delete_local_object(export_key, missing_ok=True)
            raise
        else:
            handle.close()

    async def _reconcile_records(
        self,
        *,
        workspace_id: UUID,
        records: list[StorageObject],
        now: datetime,
        enforcer: EntitlementEnforcementService,
        stale_after: timedelta,
    ) -> ReconciliationReport:
        stale_cutoff = now - stale_after
        pending_recovered = 0
        pending_quarantined = 0
        quarantine_repaired = 0

        for snapshot in records:
            async with _stable_object_lock(self.db, workspace_id, snapshot.object_id):
                record = await self.db.scalar(
                    select(StorageObject)
                    .where(
                        StorageObject.id == snapshot.id,
                        StorageObject.workspace_id == workspace_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    continue
                record.reconciliation_attempts += 1
                record.last_reconciled_at = now
                final_exists = await self._local_exists(record.storage_key)
                quarantine_exists = bool(
                    record.quarantine_key
                    and await self._local_exists(record.quarantine_key)
                )
                if record.state is StorageObjectState.pending:
                    if record.deleted_at is not None:
                        if final_exists and record.quarantine_key:
                            if quarantine_exists:
                                await self._delete_local_key(record.storage_key)
                            else:
                                await self._replace_local_key(
                                    record.storage_key,
                                    record.quarantine_key,
                                )
                        record.state = StorageObjectState.quarantined
                        record.av_status = StorageAVStatus.error
                        record.quarantine_reason = "deleted_while_pending"
                        pending_quarantined += 1
                    elif final_exists or quarantine_exists:
                        if record.quarantine_key is None:
                            record.quarantine_key = self._quarantine_key(
                                workspace_id,
                                record.id,
                            )
                        if final_exists and not quarantine_exists:
                            await self._replace_local_key(
                                record.storage_key,
                                record.quarantine_key,
                            )
                            final_exists = False
                            quarantine_exists = True
                        try:
                            await self._evaluate_quarantine_payload(
                                record,
                                now=now,
                                enforcer=enforcer,
                            )
                        except LocalObjectIntegrityError:
                            if final_exists:
                                await self._delete_local_key(record.storage_key)
                            record.state = StorageObjectState.quarantined
                            record.av_status = StorageAVStatus.error
                            record.quarantine_reason = "recovery_payload_conflict"
                            pending_quarantined += 1
                        else:
                            if record.state is StorageObjectState.available:
                                pending_recovered += 1
                            else:
                                if final_exists:
                                    await self._delete_local_key(record.storage_key)
                                pending_quarantined += 1
                    elif (
                        record.upload_ticket_expires_at is not None
                        and _as_utc(record.upload_ticket_expires_at) <= stale_cutoff
                    ):
                        record.state = StorageObjectState.quarantined
                        record.av_status = StorageAVStatus.error
                        record.quarantine_reason = "pending_payload_missing"
                        record.upload_ticket_hash = None
                        record.upload_ticket_consumed_at = now
                        pending_quarantined += 1
                elif record.state is StorageObjectState.available:
                    if not final_exists:
                        if quarantine_exists:
                            previous_state = record.state
                            await self._evaluate_quarantine_payload(
                                record,
                                now=now,
                                enforcer=enforcer,
                            )
                            if record.state is StorageObjectState.available:
                                quarantine_repaired += 1
                            elif previous_state is StorageObjectState.available:
                                quarantine_repaired += 1
                        elif record.quarantine_reason != "available_payload_missing":
                            record.state = StorageObjectState.quarantined
                            record.av_status = StorageAVStatus.error
                            record.quarantine_reason = "available_payload_missing"
                            quarantine_repaired += 1
                    elif not record.checksum_sha256 or not hmac.compare_digest(
                        record.checksum_sha256,
                        await self._sha256_key(record.storage_key),
                    ):
                        quarantine_key = record.quarantine_key or self._quarantine_key(
                            workspace_id,
                            record.id,
                        )
                        await self._replace_local_key(record.storage_key, quarantine_key)
                        record.quarantine_key = quarantine_key
                        record.state = StorageObjectState.quarantined
                        record.av_status = StorageAVStatus.error
                        record.quarantine_reason = "available_checksum_mismatch"
                        quarantine_repaired += 1
                elif record.state is StorageObjectState.quarantined:
                    if final_exists:
                        if record.quarantine_key and not quarantine_exists:
                            await self._replace_local_key(
                                record.storage_key,
                                record.quarantine_key,
                            )
                            quarantine_exists = True
                        else:
                            await self._delete_local_key(record.storage_key)
                        quarantine_repaired += 1
                    known_missing_reasons = {
                        "available_payload_missing",
                        "deleted_while_pending",
                        "pending_payload_missing",
                        "quarantine_payload_missing",
                    }
                    if (
                        not quarantine_exists
                        and record.quarantine_reason not in known_missing_reasons
                    ):
                        record.av_status = StorageAVStatus.error
                        record.quarantine_reason = "quarantine_payload_missing"
                        quarantine_repaired += 1

        await self.db.flush()
        return ReconciliationReport(
            pending_recovered=pending_recovered,
            pending_quarantined=pending_quarantined,
            quarantine_repaired=quarantine_repaired,
        )

    async def reconcile_object(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        stale_after: timedelta = timedelta(minutes=15),
    ) -> StorageObject:
        """Repair the latest version of one proven tenant-bound object."""
        self._require_local_mode()
        await self._bind_authenticated_workspace(workspace_id)
        enforcer = EntitlementEnforcementService(self.db)
        await enforcer.lock_workspace(workspace_id)
        record = await self.db.scalar(
            select(StorageObject)
            .where(
                StorageObject.workspace_id == workspace_id,
                StorageObject.object_id == object_id,
            )
            .order_by(StorageObject.version.desc())
            .limit(1)
        )
        if record is None:
            raise LocalObjectNotFoundError("Objeto não encontrado.")
        await self._reconcile_records(
            workspace_id=workspace_id,
            records=[record],
            now=_utc_now(),
            enforcer=enforcer,
            stale_after=stale_after,
        )
        await self.db.refresh(record)
        return record

    async def reconcile_workspace_batch(
        self,
        *,
        workspace_id: UUID,
        limit: int = 100,
        min_interval: timedelta = timedelta(minutes=5),
        stale_after: timedelta = timedelta(minutes=15),
    ) -> BoundedReconciliationResult:
        """Repair one fair SQL-limited batch without inventorying the filesystem."""
        self._require_local_mode()
        if not 1 <= limit <= 500:
            raise LocalObjectValidationError("Lote de reconciliação deve ficar entre 1 e 500.")
        if min_interval < timedelta(0):
            raise LocalObjectValidationError("Intervalo de reconciliação não pode ser negativo.")
        await self._bind_authenticated_workspace(workspace_id)
        enforcer = EntitlementEnforcementService(self.db)
        await enforcer.lock_workspace(workspace_id)
        now = _utc_now()
        eligible_before = now - min_interval
        records = list(
            (
                await self.db.scalars(
                    select(StorageObject)
                    .where(
                        StorageObject.workspace_id == workspace_id,
                        or_(
                            StorageObject.state == StorageObjectState.pending,
                            StorageObject.last_reconciled_at.is_(None),
                            StorageObject.last_reconciled_at <= eligible_before,
                        ),
                    )
                    .order_by(
                        case(
                            (StorageObject.last_reconciled_at.is_(None), 0),
                            else_=1,
                        ),
                        StorageObject.last_reconciled_at,
                        StorageObject.created_at,
                        StorageObject.id,
                    )
                    .limit(limit)
                )
            ).all()
        )
        report = await self._reconcile_records(
            workspace_id=workspace_id,
            records=records,
            now=now,
            enforcer=enforcer,
            stale_after=stale_after,
        )
        return BoundedReconciliationResult(report=report, examined=len(records))

    async def reconcile_workspace(
        self,
        *,
        workspace_id: UUID,
        stale_after: timedelta = timedelta(minutes=15),
        export_ttl: timedelta = timedelta(hours=1),
    ) -> ReconciliationReport:
        """Run the explicit full repair, including stale filesystem inventory."""
        self._require_local_mode()
        await self._bind_authenticated_workspace(workspace_id)
        enforcer = EntitlementEnforcementService(self.db)
        await enforcer.lock_workspace(workspace_id)
        now = _utc_now()
        stale_cutoff = now - stale_after
        export_cutoff = now - export_ttl
        records = list(
            (
                await self.db.scalars(
                    select(StorageObject).where(StorageObject.workspace_id == workspace_id)
                )
            ).all()
        )
        repair_report = await self._reconcile_records(
            workspace_id=workspace_id,
            records=records,
            now=now,
            enforcer=enforcer,
            stale_after=stale_after,
        )
        expected_keys: set[str] = set()
        for record in records:
            if record.state in {StorageObjectState.pending, StorageObjectState.available}:
                expected_keys.add(record.storage_key)
            if (
                record.state in {StorageObjectState.pending, StorageObjectState.quarantined}
                and record.quarantine_key
            ):
                expected_keys.add(record.quarantine_key)
        orphaned_files_removed = 0
        temporary_files_removed = 0
        for prefix in (
            f"tenants/{workspace_id.hex}",
            f"quarantine/{workspace_id.hex}",
            f"staging/{workspace_id.hex}",
        ):
            for key, file_stat in await self._local_inventory(prefix):
                modified_at = datetime.fromtimestamp(file_stat.st_mtime, tz=UTC)
                if modified_at > stale_cutoff:
                    continue
                if key.endswith(".tmp"):
                    await self._delete_local_key(key)
                    temporary_files_removed += 1
                elif key not in expected_keys:
                    await self._delete_local_key(key)
                    orphaned_files_removed += 1

        exports_removed = 0
        for key, file_stat in await self._local_inventory(
            f"exports/{workspace_id.hex}"
        ):
            if datetime.fromtimestamp(file_stat.st_mtime, tz=UTC) <= export_cutoff:
                await self._delete_local_key(key)
                exports_removed += 1
        return ReconciliationReport(
            pending_recovered=repair_report.pending_recovered,
            pending_quarantined=repair_report.pending_quarantined,
            quarantine_repaired=repair_report.quarantine_repaired,
            orphaned_files_removed=orphaned_files_removed,
            temporary_files_removed=temporary_files_removed,
            exports_removed=exports_removed,
        )

    @staticmethod
    def _manifest_entry(record: StorageObject) -> dict:
        return {
            "id": str(record.id),
            "version": record.version,
            "filename": record.original_filename,
            "declaredContentType": record.declared_content_type,
            "detectedContentType": record.detected_content_type,
            "byteSize": record.byte_size,
            "checksumSha256": record.checksum_sha256,
            "state": record.state.value,
            "avStatus": record.av_status.value,
            "quarantineReason": record.quarantine_reason,
            "retentionUntil": (
                _as_utc(record.retention_until).isoformat()
                if record.retention_until
                else None
            ),
            "deletedAt": _as_utc(record.deleted_at).isoformat() if record.deleted_at else None,
            "createdAt": _as_utc(record.created_at).isoformat(),
        }
