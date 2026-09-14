"""AWS-backed private document lifecycle with mandatory real AV verdicts."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import BinaryIO
from urllib.parse import quote
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.klm_runtime import aws_private_storage_allowed
from app.core.logging import logger
from app.db.tenant import TenantSession, set_workspace_context
from app.models.client import Client
from app.models.document import Document, DocumentStatus
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.local_object_storage_service import (
    LocalObjectConflictError,
    LocalObjectIntegrityError,
    LocalObjectNotFoundError,
    LocalObjectRetentionError,
    LocalObjectStorageService,
    LocalObjectUnavailableError,
    LocalObjectValidationError,
    _as_utc,
    _normalize_mime,
    _stable_object_lock,
    _validate_upload_contract,
    inspect_payload_handle,
)
from app.services.malware_scanner import ClamAVScanner, MalwareScanReceipt
from app.services.s3_service import S3Service, StorageUnavailableError

_SCAN_AFTER_COMMIT = "private_document_scan_after_commit"
_SCAN_HOOKS_INSTALLED = "private_document_scan_hooks_installed"


def _dispatch_scans_after_commit(session: Session) -> None:
    jobs = list(dict.fromkeys(session.info.pop(_SCAN_AFTER_COMMIT, [])))
    if not jobs:
        return
    from app.workers.storage_tasks import scan_private_document

    for workspace_id, version_id in jobs:
        try:
            scan_private_document.apply_async(
                kwargs={"workspace_id": workspace_id, "version_id": version_id}
            )
        except Exception as exc:  # noqa: BLE001 - beat reconciles the committed row
            logger.error(
                "storage.private_scan_enqueue_failed",
                workspace_id=workspace_id,
                version_id=version_id,
                error_type=type(exc).__name__,
            )


def _clear_scans_after_rollback(session: Session) -> None:
    session.info.pop(_SCAN_AFTER_COMMIT, None)


@dataclass(frozen=True)
class PrivateUploadAuthorization:
    storage_object: StorageObject
    upload_url: str | None
    upload_headers: dict[str, str]
    expires_at: datetime
    upload_required: bool = True


@dataclass(frozen=True)
class PrivateDownloadAuthorization:
    storage_object: StorageObject
    download_url: str
    expires_at: datetime


class PrivateDocumentStorageService:
    """Production/staging adapter. It never falls back to a local filesystem."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        s3: S3Service | None = None,
        authorization_session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self.db = db
        self.s3 = s3 or S3Service()
        if authorization_session_factory is None:
            if db.bind is None:
                raise RuntimeError(
                    "A sessão de storage privado precisa estar vinculada a um banco."
                )
            # A presigned URL is an external-effect capability.  Its ownership
            # row and audit event must be committed before the URL is returned,
            # but that commit must never flush unrelated ORM work owned by the
            # request/command session.  A fresh TenantSession preserves the
            # PostgreSQL RLS hooks while the same AsyncEngine keeps tests and
            # alternate runtimes on their configured database.
            authorization_bind = (
                db.bind.engine if isinstance(db.bind, AsyncConnection) else db.bind
            )
            authorization_session_factory = async_sessionmaker(
                bind=authorization_bind,
                class_=AsyncSession,
                sync_session_class=TenantSession,
                expire_on_commit=False,
                autoflush=False,
            )
        self.authorization_session_factory = authorization_session_factory

    @staticmethod
    def require_remote_runtime() -> None:
        if not aws_private_storage_allowed():
            raise LocalObjectUnavailableError(
                "Armazenamento privado AWS não configurado ou não autorizado."
            )

    def verify_private_bucket(self) -> None:
        try:
            self.s3.verify_private_bucket()
        except (RuntimeError, StorageUnavailableError) as exc:
            raise LocalObjectUnavailableError(
                "Bucket privado não confirmou bloqueio público integral."
            ) from exc

    async def _bind_workspace(self, workspace_id: UUID) -> None:
        if self.db.get_bind().dialect.name == "postgresql":
            await set_workspace_context(self.db, workspace_id)

    def _queue_scan(self, *, workspace_id: UUID, version_id: UUID) -> None:
        sync_session = self.db.sync_session
        if not sync_session.info.get(_SCAN_HOOKS_INSTALLED):
            event.listen(sync_session, "after_commit", _dispatch_scans_after_commit)
            event.listen(sync_session, "after_rollback", _clear_scans_after_rollback)
            sync_session.info[_SCAN_HOOKS_INSTALLED] = True
        sync_session.info.setdefault(_SCAN_AFTER_COMMIT, []).append(
            (str(workspace_id), str(version_id))
        )

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
            existing = await self.db.scalar(
                select(Client.id).where(
                    Client.id == client_id,
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
            if existing is None:
                raise LocalObjectNotFoundError("Cliente vinculado não foi encontrado.")
        return matter_id, client_id

    @staticmethod
    def _clean_key(
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

    @staticmethod
    def _idempotency_key_hash(idempotency_key: str | None) -> str | None:
        if idempotency_key is None:
            return None
        normalized = idempotency_key.strip()
        if not 8 <= len(normalized) <= 200:
            raise LocalObjectValidationError(
                "Idempotency-Key deve conter entre 8 e 200 caracteres."
            )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _same_upload_request(
        record: StorageObject,
        *,
        actor_user_id: UUID | None,
        filename: str,
        content_type: str,
        byte_size: int,
        checksum_sha256: str,
        retention_until: datetime | None,
        object_id: UUID | None,
        matter_id: UUID | None,
        client_id: UUID | None,
    ) -> bool:
        expected_retention = (
            _as_utc(record.retention_until) if record.retention_until is not None else None
        )
        requested_retention = (
            _as_utc(retention_until) if retention_until is not None else None
        )
        return bool(
            record.created_by_user_id == actor_user_id
            and record.original_filename == filename
            and record.declared_content_type == content_type
            and record.expected_byte_size == byte_size
            and record.expected_sha256 == checksum_sha256
            and expected_retention == requested_retention
            and (object_id is None or record.object_id == object_id)
            and record.matter_id == matter_id
            and record.client_id == client_id
        )

    def _presign_pending_upload(
        self,
        record: StorageObject,
        *,
        expires_in: int,
    ) -> tuple[str, dict[str, str], datetime]:
        if (
            record.quarantine_key is None
            or record.expected_sha256 is None
            or record.expected_byte_size is None
        ):
            raise LocalObjectIntegrityError("Contrato de quarentena incompleto.")
        expiry_seconds = min(max(int(expires_in), 30), 900)
        expires_at = datetime.now(UTC) + timedelta(seconds=expiry_seconds)
        try:
            upload_url = self.s3.generate_presigned_private_upload_url(
                record.quarantine_key,
                content_type=record.declared_content_type,
                expires_in=expiry_seconds,
                authorization_granted=True,
                expected_byte_size=record.expected_byte_size,
                checksum_sha256=record.expected_sha256,
            )
        except (StorageUnavailableError, ValueError) as exc:
            raise LocalObjectUnavailableError(
                "Não foi possível autorizar o upload privado."
            ) from exc
        record.upload_ticket_expires_at = expires_at
        headers = {
            "Content-Type": record.declared_content_type,
            "x-amz-meta-storage-state": "quarantine",
            "x-amz-meta-sha256": record.expected_sha256,
            "x-amz-server-side-encryption": "aws:kms",
            "x-amz-server-side-encryption-aws-kms-key-id": settings.S3_KMS_KEY_ID,
        }
        return upload_url, headers, expires_at

    async def _replay_upload_authorization(
        self,
        record: StorageObject,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        filename: str,
        content_type: str,
        byte_size: int,
        checksum_sha256: str,
        retention_until: datetime | None,
        expires_in: int,
        object_id: UUID | None,
        matter_id: UUID | None,
        client_id: UUID | None,
    ) -> PrivateUploadAuthorization:
        requested_matter_id, requested_client_id = await self._validate_acl_binding(
            workspace_id=workspace_id,
            matter_id=matter_id,
            client_id=client_id,
        )
        if not self._same_upload_request(
            record,
            actor_user_id=actor_user_id,
            filename=filename,
            content_type=content_type,
            byte_size=byte_size,
            checksum_sha256=checksum_sha256.lower(),
            retention_until=retention_until,
            object_id=object_id,
            matter_id=requested_matter_id,
            client_id=requested_client_id,
        ):
            raise LocalObjectConflictError(
                "Idempotency-Key já foi usada por outra autorização de upload."
            )
        if record.deleted_at is not None or record.av_status is StorageAVStatus.infected:
            raise LocalObjectConflictError(
                "Upload idempotente já foi bloqueado ou excluído."
            )
        if (
            record.uploaded_at is None
            and record.state is StorageObjectState.pending
            and record.deleted_at is None
        ):
            upload_url, headers, expires_at = self._presign_pending_upload(
                record,
                expires_in=expires_in,
            )
            await self._audit(
                record,
                actor_user_id=actor_user_id,
                event_type="document_upload_authorization_replayed",
                summary="Autorização idempotente de upload privado reemitida.",
                after={"state": record.state.value, "uploadRequired": True},
            )
            return PrivateUploadAuthorization(
                record,
                upload_url,
                headers,
                expires_at,
                True,
            )
        await self._audit(
            record,
            actor_user_id=actor_user_id,
            event_type="document_upload_authorization_replayed",
            summary="Autorização idempotente já consumida foi consultada.",
            after={"state": record.state.value, "uploadRequired": False},
        )
        return PrivateUploadAuthorization(
            record,
            None,
            {},
            record.upload_ticket_expires_at or datetime.now(UTC),
            False,
        )

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
    ) -> PrivateUploadAuthorization:
        """Persist the upload intent without owning the caller's transaction.

        A private PUT URL is returned only after a dedicated tenant transaction
        commits the ``StorageObject`` and its audit event.  Domain entities such
        as ``Document`` and ``SupportTicketAttachment`` stay in the caller's
        request transaction, so a storage authorization can never commit other
        pending changes by accident.  If that later domain transaction fails,
        the durable pending intent is deliberately recoverable by the expired
        upload reconciler and by an idempotent retry.
        """
        self.require_remote_runtime()
        self.verify_private_bucket()
        # Preserve the caller's tenant invariant before opening an isolated
        # session.  In particular, a session already bound to workspace A may
        # not use this method to open a fresh session for workspace B.
        await self._bind_workspace(workspace_id)
        async with self.authorization_session_factory() as authorization_db:
            authorization_service = PrivateDocumentStorageService(
                authorization_db,
                s3=self.s3,
                authorization_session_factory=self.authorization_session_factory,
            )
            try:
                authorization = (
                    await authorization_service._create_upload_authorization_in_transaction(
                        workspace_id=workspace_id,
                        actor_user_id=actor_user_id,
                        filename=filename,
                        content_type=content_type,
                        byte_size=byte_size,
                        checksum_sha256=checksum_sha256,
                        retention_until=retention_until,
                        expires_in=expires_in,
                        object_id=object_id,
                        matter_id=matter_id,
                        client_id=client_id,
                        initial_object_id=initial_object_id,
                        idempotency_key=idempotency_key,
                    )
                )
                # Commit only the dedicated authorization session.  The URL is
                # not exposed until this succeeds.
                await authorization_db.commit()
            except Exception:
                await authorization_db.rollback()
                raise
        # Preserve the previous service ergonomics without sharing transaction
        # ownership: callers receive a clean instance associated with their own
        # session and may continue the domain command, but no caller state was
        # flushed or committed by the authorization transaction.
        caller_record = await self.db.merge(
            authorization.storage_object,
            load=False,
        )
        return PrivateUploadAuthorization(
            caller_record,
            authorization.upload_url,
            authorization.upload_headers,
            authorization.expires_at,
            authorization.upload_required,
        )

    async def _create_upload_authorization_in_transaction(
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
    ) -> PrivateUploadAuthorization:
        self.require_remote_runtime()
        await self._bind_workspace(workspace_id)
        filename, content_type, retention_until = _validate_upload_contract(
            filename=filename,
            content_type=content_type,
            byte_size=byte_size,
            checksum_sha256=checksum_sha256,
            retention_until=retention_until,
        )
        if byte_size is None or byte_size < 1:
            raise LocalObjectValidationError(
                "Tamanho exato é obrigatório no upload privado remoto."
            )
        if byte_size > settings.DOCUMENT_AV_MAX_BYTES:
            raise LocalObjectValidationError("Documento excede o limite do scanner antivírus.")
        if checksum_sha256 is None:
            raise LocalObjectValidationError(
                "Checksum SHA-256 é obrigatório no upload privado remoto."
            )

        idempotency_key_hash = self._idempotency_key_hash(idempotency_key)
        if idempotency_key_hash is not None:
            # Serialize the first authorization by a deterministic advisory lock,
            # before a random object id can split concurrent retries into rows.
            idempotency_lock_id = uuid.uuid5(workspace_id, idempotency_key_hash)
            async with _stable_object_lock(self.db, workspace_id, idempotency_lock_id):
                existing = await self.db.scalar(
                    select(StorageObject)
                    .where(
                        StorageObject.workspace_id == workspace_id,
                        StorageObject.idempotency_key_hash == idempotency_key_hash,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    return await self._replay_upload_authorization(
                        existing,
                        workspace_id=workspace_id,
                        actor_user_id=actor_user_id,
                        filename=filename,
                        content_type=content_type,
                        byte_size=byte_size,
                        checksum_sha256=checksum_sha256,
                        retention_until=retention_until,
                        expires_in=expires_in,
                        object_id=object_id,
                        matter_id=matter_id,
                        client_id=client_id,
                    )

        create_new = object_id is None or initial_object_id
        object_id = object_id or (
            uuid.uuid5(workspace_id, f"storage-upload:{idempotency_key_hash}")
            if idempotency_key_hash is not None
            else uuid.uuid4()
        )
        async with _stable_object_lock(self.db, workspace_id, object_id):
            if idempotency_key_hash is not None:
                # A second process may have observed no row before the first
                # transaction inserted it. The deterministic object lock closes
                # that window without relying on a failed unique insert.
                existing = await self.db.scalar(
                    select(StorageObject)
                    .where(
                        StorageObject.workspace_id == workspace_id,
                        StorageObject.idempotency_key_hash == idempotency_key_hash,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    return await self._replay_upload_authorization(
                        existing,
                        workspace_id=workspace_id,
                        actor_user_id=actor_user_id,
                        filename=filename,
                        content_type=content_type,
                        byte_size=byte_size,
                        checksum_sha256=checksum_sha256,
                        retention_until=retention_until,
                        expires_in=expires_in,
                        object_id=None if not initial_object_id else object_id,
                        matter_id=matter_id,
                        client_id=client_id,
                    )
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
                if not create_new:
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
                    raise LocalObjectConflictError("O vínculo do caso é imutável.")
                if client_id is not None and client_id != latest.client_id:
                    raise LocalObjectConflictError("O vínculo do cliente é imutável.")
                version = latest.version + 1
                matter_id, client_id = latest.matter_id, latest.client_id
                if latest.retention_until is not None and (
                    retention_until is None or retention_until < latest.retention_until
                ):
                    retention_until = latest.retention_until

            version_id = uuid.uuid4()
            record = StorageObject(
                id=version_id,
                workspace_id=workspace_id,
                object_id=object_id,
                version=version,
                matter_id=matter_id,
                client_id=client_id,
                original_filename=filename,
                declared_content_type=content_type,
                expected_byte_size=byte_size,
                expected_sha256=checksum_sha256.lower(),
                idempotency_key_hash=idempotency_key_hash,
                storage_key=self._clean_key(workspace_id, object_id, version_id, version),
                quarantine_key=self._quarantine_key(workspace_id, version_id),
                state=StorageObjectState.pending,
                av_status=StorageAVStatus.pending,
                retention_until=retention_until,
                created_by_user_id=actor_user_id,
            )
            self.db.add(record)
            await self.db.flush()
            upload_url, headers, expires_at = self._presign_pending_upload(
                record,
                expires_in=expires_in,
            )
            await self._audit(
                record,
                actor_user_id=actor_user_id,
                event_type="document_upload_authorized",
                summary="Upload privado autorizado em quarentena.",
                after={"state": record.state.value, "version": record.version},
            )
            return PrivateUploadAuthorization(record, upload_url, headers, expires_at, True)

    async def confirm_upload(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> StorageObject:
        self.require_remote_runtime()
        self.verify_private_bucket()
        await self._bind_workspace(workspace_id)
        enforcer = EntitlementEnforcementService(self.db)
        await enforcer.lock_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            record = await self.db.scalar(
                select(StorageObject)
                .where(
                    StorageObject.workspace_id == workspace_id,
                    StorageObject.object_id == object_id,
                    StorageObject.deleted_at.is_(None),
                )
                .order_by(StorageObject.version.desc())
                .limit(1)
                .with_for_update()
            )
            if record is None:
                raise LocalObjectNotFoundError("Objeto não encontrado.")
            if record.uploaded_at is not None and record.state in {
                StorageObjectState.quarantined,
                StorageObjectState.available,
            }:
                if record.state is StorageObjectState.quarantined:
                    self._queue_scan(workspace_id=workspace_id, version_id=record.id)
                return record
            if record.quarantine_key is None:
                raise LocalObjectIntegrityError("Objeto não possui chave de quarentena.")
            receipt = await asyncio.to_thread(self._head, record.quarantine_key)
            self._verify_receipt(record, receipt, state="quarantine")
            size = int(receipt.get("ContentLength", -1))
            # Reuse the canonical stock enforcement and count this version only now,
            # after S3 has produced an authoritative size receipt.
            await LocalObjectStorageService(self.db)._enforce_stock_capacity(
                workspace_id=workspace_id,
                files=1,
                storage_bytes=size,
                enforcer=enforcer,
                excluded_version_id=record.id,
            )
            before = {"state": record.state.value, "avStatus": record.av_status.value}
            record.byte_size = size
            record.checksum_sha256 = record.expected_sha256
            record.detected_content_type = _normalize_mime(
                str(receipt.get("ContentType") or record.declared_content_type)
            )
            record.uploaded_at = datetime.now(UTC)
            record.upload_ticket_consumed_at = record.uploaded_at
            record.upload_ticket_expires_at = None
            record.state = StorageObjectState.quarantined
            record.av_status = StorageAVStatus.pending
            record.quarantine_reason = "malware_scan_pending"
            await self._audit(
                record,
                actor_user_id=actor_user_id,
                event_type="document_upload_confirmed",
                summary="Upload privado confirmado; documento aguarda antivírus.",
                before=before,
                after={"state": record.state.value, "avStatus": record.av_status.value},
            )
            self._queue_scan(workspace_id=workspace_id, version_id=record.id)
            await self.db.flush()
            return record

    def _head(self, key: str) -> dict:
        try:
            return self.s3.client.head_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise LocalObjectUnavailableError("Objeto privado não foi confirmado no S3.") from exc

    @staticmethod
    def _verify_receipt(
        record: StorageObject,
        receipt: dict,
        *,
        state: str,
    ) -> None:
        metadata = receipt.get("Metadata", {})
        checksum = str(metadata.get("sha256") or "")
        if not record.expected_sha256 or not hmac.compare_digest(
            record.expected_sha256, checksum
        ):
            raise LocalObjectIntegrityError("Recibo S3 diverge do checksum autorizado.")
        if str(metadata.get("storage-state") or "") != state:
            raise LocalObjectIntegrityError("Recibo S3 diverge do estado privado esperado.")
        if receipt.get("ServerSideEncryption") != "aws:kms":
            raise LocalObjectIntegrityError("Objeto privado não confirmou SSE-KMS.")
        size = int(receipt.get("ContentLength", -1))
        if size < 1 or size > settings.DOCUMENT_AV_MAX_BYTES:
            raise LocalObjectIntegrityError("Tamanho do objeto privado é inválido.")
        if record.expected_byte_size is not None and size != record.expected_byte_size:
            raise LocalObjectIntegrityError("Tamanho do objeto diverge do upload autorizado.")
        received_type = _normalize_mime(str(receipt.get("ContentType") or ""))
        if received_type != record.declared_content_type:
            raise LocalObjectIntegrityError("MIME do objeto diverge do upload autorizado.")

    async def scan_and_promote(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID,
        scanner: ClamAVScanner,
    ) -> str:
        self.require_remote_runtime()
        self.verify_private_bucket()
        await self._bind_workspace(workspace_id)
        record = await self.db.scalar(
            select(StorageObject)
            .where(
                StorageObject.id == version_id,
                StorageObject.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if record is None:
            return "missing"
        if record.deleted_at is not None:
            return "deleted"
        if (
            record.state is StorageObjectState.available
            and record.av_status is StorageAVStatus.clean
        ):
            return "already_clean"
        if record.av_status is StorageAVStatus.infected:
            return "infected"
        if record.uploaded_at is None or record.quarantine_key is None:
            return "not_uploaded"

        if record.av_status is not StorageAVStatus.clean:
            payload_handle, inspection = await asyncio.to_thread(
                self._download_and_inspect,
                record,
            )
            try:
                record.byte_size = inspection.byte_size
                record.checksum_sha256 = inspection.checksum_sha256
                record.detected_content_type = inspection.detected_content_type
                record.scanned_at = datetime.now(UTC)
                record.reconciliation_attempts += 1
                if not record.expected_sha256 or not hmac.compare_digest(
                    record.expected_sha256,
                    inspection.checksum_sha256,
                ):
                    await self._record_blocked(record, "checksum_mismatch")
                    await self.db.commit()
                    return "blocked"
                if (
                    inspection.mime_reason
                    or inspection.detected_content_type != record.declared_content_type
                ):
                    await self._record_blocked(
                        record,
                        inspection.mime_reason or "mime_magic_invalid",
                    )
                    await self.db.commit()
                    return "blocked"
                if inspection.av_reason:
                    await self._record_infected(
                        record,
                        inspection.av_reason,
                        "content_policy",
                    )
                    await self.db.commit()
                    return "infected"
                scan_receipt: MalwareScanReceipt = await asyncio.to_thread(
                    scanner.scan,
                    payload_handle,
                )
            finally:
                payload_handle.close()
            if scan_receipt.verdict == "infected":
                await self._record_infected(
                    record,
                    "malware_detected",
                    scan_receipt.scanner,
                    signature=scan_receipt.signature,
                )
                await self.db.commit()
                return "infected"
            record.av_status = StorageAVStatus.clean
            record.scan_method = scan_receipt.scanner
            record.quarantine_reason = "scan_clean_pending_promotion"
            await self._audit(
                record,
                actor_user_id=None,
                event_type="document_malware_scan_clean",
                summary="Scanner real aprovou o documento; promoção pendente.",
                after={"avStatus": "clean", "scanMethod": scan_receipt.scanner},
            )
            await self._set_document_status(record.object_id, DocumentStatus.parcial)
            await self.db.commit()

        # The AV receipt was persisted before this external side effect. A retry can
        # therefore finish an interrupted promotion without rescanning or guessing.
        final_receipt = await asyncio.to_thread(self._head_if_exists, record.storage_key)
        if final_receipt is None:
            try:
                await asyncio.to_thread(
                    self.s3.promote_authorized_quarantine_object,
                    record.quarantine_key,
                    record.storage_key,
                    authorization_granted=True,
                    checksum_sha256=record.checksum_sha256 or "",
                    content_type=record.detected_content_type or record.declared_content_type,
                )
            except (StorageUnavailableError, ValueError) as exc:
                raise LocalObjectUnavailableError("Promoção do documento limpo falhou.") from exc
            final_receipt = await asyncio.to_thread(self._head, record.storage_key)

        # Re-open the transaction and revalidate current state before publishing it.
        record = await self.db.scalar(
            select(StorageObject)
            .where(
                StorageObject.id == version_id,
                StorageObject.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if record is None or record.av_status is not StorageAVStatus.clean:
            raise LocalObjectConflictError("Veredito antivírus mudou durante a promoção.")
        if record.deleted_at is not None:
            raise LocalObjectConflictError(
                "Documento foi excluído durante a promoção e continua bloqueado."
            )
        self._verify_receipt(record, final_receipt, state="clean")
        record.state = StorageObjectState.available
        record.quarantine_key = None
        record.quarantine_reason = None
        record.last_reconciled_at = datetime.now(UTC)
        await self._set_document_status(record.object_id, DocumentStatus.completo)
        from app.services.legal_document_service import LegalDocumentService

        await LegalDocumentService(self.db, s3=self.s3).reconcile_clean_storage_object(
            workspace_id=workspace_id,
            storage_object=record,
        )
        document = await self.db.get(Document, record.object_id)
        if document is not None:
            from app.services.knowledge_service import KnowledgeService

            await KnowledgeService(self.db).index_document(
                workspace_id=workspace_id,
                document=document,
            )
        await self._audit(
            record,
            actor_user_id=None,
            event_type="document_storage_promoted",
            summary="Documento limpo promovido e liberado para download.",
            after={"state": "available", "avStatus": "clean"},
        )
        await self.db.commit()
        return "clean"

    def _head_if_exists(self, key: str) -> dict | None:
        try:
            return self.s3.client.head_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise LocalObjectUnavailableError("Não foi possível verificar o objeto limpo.") from exc
        except BotoCoreError as exc:
            raise LocalObjectUnavailableError("Não foi possível verificar o objeto limpo.") from exc

    def _download_and_inspect(self, record: StorageObject) -> tuple[BinaryIO, object]:
        if record.quarantine_key is None:
            raise LocalObjectIntegrityError("Chave de quarentena ausente.")
        receipt = self.s3.client.get_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=record.quarantine_key,
        )
        self._verify_receipt(record, receipt, state="quarantine")
        body = receipt["Body"]
        handle = tempfile.SpooledTemporaryFile(
            max_size=min(settings.DOCUMENT_AV_MAX_BYTES, 8 * 1024 * 1024),
            mode="w+b",
        )
        received = 0
        try:
            while chunk := body.read(1024 * 1024):
                received += len(chunk)
                if received > settings.DOCUMENT_AV_MAX_BYTES:
                    raise LocalObjectIntegrityError("Documento excede o limite do scanner.")
                handle.write(chunk)
            handle.seek(0)
            inspection = inspect_payload_handle(handle, record.declared_content_type)
            handle.seek(0)
            return handle, inspection
        except Exception:
            handle.close()
            raise
        finally:
            body.close()

    async def mark_scan_error(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID,
        reason: str,
    ) -> None:
        await self._bind_workspace(workspace_id)
        record = await self.db.scalar(
            select(StorageObject)
            .where(
                StorageObject.id == version_id,
                StorageObject.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if record is None or record.state is StorageObjectState.available:
            return
        record.state = StorageObjectState.quarantined
        clean_receipt_preserved = record.av_status is StorageAVStatus.clean
        if clean_receipt_preserved:
            record.quarantine_reason = f"clean_receipt_promotion_retry:{reason}"[:255]
        else:
            record.av_status = StorageAVStatus.error
            record.quarantine_reason = reason[:255]
            record.scanned_at = datetime.now(UTC)
        record.reconciliation_attempts += 1
        await self._set_document_status(record.object_id, DocumentStatus.parcial)
        await self._audit(
            record,
            actor_user_id=None,
            event_type=(
                "document_storage_promotion_failed"
                if clean_receipt_preserved
                else "document_malware_scan_failed"
            ),
            summary=(
                "Promoção falhou; recibo antivírus limpo foi preservado para retry."
                if clean_receipt_preserved
                else "Scanner real não produziu veredito; download continua bloqueado."
            ),
            after={
                "state": "quarantined",
                "avStatus": record.av_status.value,
                "reason": reason[:80],
            },
        )
        await self.db.flush()

    async def expire_abandoned_upload(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID,
        now: datetime | None = None,
    ) -> str:
        """Delete an unconfirmed S3 quarantine object after its ticket expires."""
        self.require_remote_runtime()
        await self._bind_workspace(workspace_id)
        record = await self.db.scalar(
            select(StorageObject)
            .where(
                StorageObject.id == version_id,
                StorageObject.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if record is None:
            return "missing"
        observed_at = now or datetime.now(UTC)
        if (
            record.deleted_at is not None
            or record.uploaded_at is not None
            or record.state is not StorageObjectState.pending
            or record.upload_ticket_expires_at is None
            or record.upload_ticket_expires_at > observed_at
        ):
            return "active"
        if record.quarantine_key:
            try:
                await asyncio.to_thread(
                    self.s3.delete_authorized_private_object,
                    record.quarantine_key,
                    authorization_granted=True,
                )
            except StorageUnavailableError as exc:
                raise LocalObjectUnavailableError(
                    "Limpeza da quarentena privada expirou sem recibo do S3."
                ) from exc
        record.state = StorageObjectState.quarantined
        record.av_status = StorageAVStatus.error
        record.quarantine_reason = "upload_authorization_expired"
        record.deleted_at = observed_at
        record.upload_ticket_expires_at = None
        record.upload_ticket_consumed_at = observed_at
        record.ticket_epoch += 1
        await self._set_document_status(record.object_id, DocumentStatus.invalido)
        await self._audit(
            record,
            actor_user_id=None,
            event_type="document_upload_expired",
            summary="Upload não confirmado expirou e a quarentena privada foi eliminada.",
            after={
                "state": record.state.value,
                "avStatus": record.av_status.value,
                "deleted": True,
            },
        )
        await self.db.flush()
        return "expired"

    async def _record_blocked(self, record: StorageObject, reason: str) -> None:
        record.state = StorageObjectState.quarantined
        record.av_status = StorageAVStatus.error
        record.quarantine_reason = reason[:255]
        record.scan_method = "content_validation"
        await self._set_document_status(record.object_id, DocumentStatus.invalido)
        await self._audit(
            record,
            actor_user_id=None,
            event_type="document_content_validation_blocked",
            summary="Documento bloqueado pela validação de integridade.",
            after={"state": "quarantined", "reason": reason[:80]},
        )

    async def _record_infected(
        self,
        record: StorageObject,
        reason: str,
        method: str,
        *,
        signature: str | None = None,
    ) -> None:
        record.state = StorageObjectState.quarantined
        record.av_status = StorageAVStatus.infected
        record.quarantine_reason = reason[:255]
        record.scan_method = method[:64]
        await self._set_document_status(record.object_id, DocumentStatus.invalido)
        await self._audit(
            record,
            actor_user_id=None,
            event_type="document_malware_detected",
            summary="Documento infectado permaneceu isolado na quarentena.",
            after={
                "state": "quarantined",
                "avStatus": "infected",
                "signature": (signature or "detected")[:200],
            },
        )

    async def _set_document_status(self, object_id: UUID, status: DocumentStatus) -> None:
        document = await self.db.get(Document, object_id)
        if document is not None:
            document.status = status
            document.s3_url = None

    async def create_download_authorization(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        version: int | None,
        expires_in: int,
        actor_user_id: UUID | None = None,
    ) -> PrivateDownloadAuthorization:
        self.require_remote_runtime()
        self.verify_private_bucket()
        await self._bind_workspace(workspace_id)
        statement = select(StorageObject).where(
            StorageObject.workspace_id == workspace_id,
            StorageObject.object_id == object_id,
            StorageObject.deleted_at.is_(None),
        )
        statement = (
            statement.where(StorageObject.version == version)
            if version is not None
            else statement.order_by(StorageObject.version.desc()).limit(1)
        )
        record = await self.db.scalar(statement)
        if (
            record is None
            or record.state is not StorageObjectState.available
            or record.av_status is not StorageAVStatus.clean
            or not record.checksum_sha256
        ):
            raise LocalObjectConflictError("Documento não está limpo para download.")
        receipt = await asyncio.to_thread(self._head, record.storage_key)
        self._verify_receipt(record, receipt, state="clean")
        expiry = min(max(int(expires_in), 30), 900)
        try:
            url = self.s3.generate_authorized_private_download_url(
                record.storage_key,
                expires_in=expiry,
                authorization_granted=True,
                response_content_type=record.detected_content_type
                or record.declared_content_type,
                response_content_disposition=(
                    "attachment; filename*=UTF-8''"
                    f"{quote(record.original_filename, safe='')}"
                ),
            )
        except (StorageUnavailableError, ValueError) as exc:
            raise LocalObjectUnavailableError("Download privado indisponível.") from exc
        await self._audit(
            record,
            actor_user_id=actor_user_id,
            event_type="document_download_authorized",
            summary="Download privado temporário autorizado.",
            after={"version": record.version, "expiresIn": expiry},
        )
        return PrivateDownloadAuthorization(
            record,
            url,
            datetime.now(UTC) + timedelta(seconds=expiry),
        )

    async def soft_delete(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        actor_user_id: UUID | None,
    ) -> StorageObject:
        self.require_remote_runtime()
        await self._bind_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            records = list(
                (
                    await self.db.scalars(
                        select(StorageObject)
                        .where(
                            StorageObject.workspace_id == workspace_id,
                            StorageObject.object_id == object_id,
                        )
                        .order_by(StorageObject.version.desc())
                        .with_for_update()
                    )
                ).all()
            )
            if not records:
                raise LocalObjectNotFoundError("Objeto não encontrado.")

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

            now = datetime.now(UTC)
            retained_until = max(
                (
                    _as_utc(record.retention_until)
                    for record in records
                    if record.retention_until is not None
                ),
                default=None,
            )
            if retained_until is not None and retained_until > now:
                raise LocalObjectRetentionError(
                    f"Objeto retido até {retained_until.isoformat()} e não pode ser excluído."
                )

            for record in records:
                if record.deleted_at is not None:
                    continue
                before = {
                    "deleted": False,
                    "state": record.state.value,
                    "version": record.version,
                }
                record.deleted_at = now
                record.deleted_by_user_id = actor_user_id
                record.ticket_epoch += 1
                if record.state is StorageObjectState.pending:
                    record.upload_ticket_consumed_at = now
                    record.upload_ticket_expires_at = None
                await self._audit(
                    record,
                    actor_user_id=actor_user_id,
                    event_type="document_soft_deleted",
                    summary="Documento privado excluído logicamente; payload permanece privado.",
                    before=before,
                    after={
                        "deleted": True,
                        "state": record.state.value,
                        "version": record.version,
                    },
                )
            await self.db.flush()
            return records[0]

    async def restore(
        self,
        *,
        workspace_id: UUID,
        object_id: UUID,
        actor_user_id: UUID | None,
    ) -> StorageObject:
        self.require_remote_runtime()
        await self._bind_workspace(workspace_id)
        enforcer = EntitlementEnforcementService(self.db)
        await enforcer.lock_workspace(workspace_id)
        async with _stable_object_lock(self.db, workspace_id, object_id):
            records = list(
                (
                    await self.db.scalars(
                        select(StorageObject)
                        .where(
                            StorageObject.workspace_id == workspace_id,
                            StorageObject.object_id == object_id,
                        )
                        .order_by(StorageObject.version.desc())
                        .with_for_update()
                    )
                ).all()
            )
            if not records:
                raise LocalObjectNotFoundError("Objeto não encontrado.")
            deleted_records = [record for record in records if record.deleted_at is not None]
            if not deleted_records:
                return records[0]
            restorable = [
                record
                for record in deleted_records
                if record.byte_size is not None and record.uploaded_at is not None
            ]
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
            await LocalObjectStorageService(self.db)._enforce_stock_capacity(
                workspace_id=workspace_id,
                files=len(restorable),
                storage_bytes=sum(record.byte_size or 0 for record in restorable),
                enforcer=enforcer,
            )
            now = datetime.now(UTC)
            for record in restorable:
                before = {
                    "deleted": True,
                    "state": record.state.value,
                    "version": record.version,
                }
                record.deleted_at = None
                record.deleted_by_user_id = None
                record.restored_at = now
                record.restored_by_user_id = actor_user_id
                record.ticket_epoch += 1
                await self._audit(
                    record,
                    actor_user_id=actor_user_id,
                    event_type="document_restored",
                    summary="Documento privado restaurado com quota revalidada.",
                    before=before,
                    after={
                        "deleted": False,
                        "state": record.state.value,
                        "version": record.version,
                    },
                )
                if (
                    record.state is StorageObjectState.quarantined
                    and record.av_status is not StorageAVStatus.infected
                    and record.quarantine_key is not None
                ):
                    self._queue_scan(workspace_id=workspace_id, version_id=record.id)
            await self.db.flush()
            return restorable[0]

    async def _audit(
        self,
        record: StorageObject,
        *,
        actor_user_id: UUID | None,
        event_type: str,
        summary: str,
        before: dict | None = None,
        after: dict | None = None,
    ) -> None:
        self.db.add(
            KlmAuditEvent(
                workspace_id=record.workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human" if actor_user_id else "system",
                actor_label=str(actor_user_id or "storage-worker"),
                event_type=event_type,
                entity_type="storage_object",
                entity_id=record.id,
                summary=summary,
                correlation_id=f"storage:{record.id}",
                before_data=before,
                after_data=after,
            )
        )
        await self.db.flush()


def sha256_file(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()
