"""
Service: DocumentService
Lógica de negócio para documentos: metadados no PostgreSQL, arquivos no S3.
"""
import asyncio
import hashlib
import io
import mimetypes
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.klm_runtime import aws_private_storage_allowed
from app.core.logging import logger
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.matter import Matter
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.schemas.document import (
    DocumentCreate,
    DocumentDownloadUrlResponse,
    PresignedUrlResponse,
)
from app.services.knowledge_service import KnowledgeService
from app.services.local_object_storage_service import (
    LocalObjectConflictError,
    LocalObjectIntegrityError,
    LocalObjectNotFoundError,
    LocalObjectStorageError,
    LocalObjectStorageService,
    LocalObjectUnavailableError,
    LocalObjectValidationError,
)
from app.services.private_document_storage_service import PrivateDocumentStorageService
from app.services.s3_service import S3Service, StorageUnavailableError

_LEGACY_COMMIT_DELETE_KEY = "document_legacy_commit_delete"
_LEGACY_ROLLBACK_DELETE_KEY = "document_legacy_rollback_delete"
_LEGACY_CLEANUP_HOOKS_KEY = "document_legacy_cleanup_hooks_installed"


def _delete_legacy_cleanup_keys(keys: list[str], *, phase: str) -> None:
    for key in dict.fromkeys(keys):
        try:
            S3Service.delete_local_object(key, missing_ok=True)
        except Exception as exc:
            # The committed database row remains authoritative. A later reconcile can
            # safely remove a duplicate legacy payload or orphaned compensated copy.
            logger.warning(
                "document_legacy_storage_cleanup_failed",
                phase=phase,
                error_type=type(exc).__name__,
            )


def _legacy_cleanup_after_commit(session: Session) -> None:
    commit_keys = list(session.info.pop(_LEGACY_COMMIT_DELETE_KEY, []))
    session.info.pop(_LEGACY_ROLLBACK_DELETE_KEY, None)
    _delete_legacy_cleanup_keys(commit_keys, phase="after_commit")


def _legacy_cleanup_after_rollback(session: Session) -> None:
    rollback_keys = list(session.info.pop(_LEGACY_ROLLBACK_DELETE_KEY, []))
    session.info.pop(_LEGACY_COMMIT_DELETE_KEY, None)
    _delete_legacy_cleanup_keys(rollback_keys, phase="after_rollback")


class DocumentService:

    def __init__(self, db: AsyncSession):
        self.db = db
        self.s3 = S3Service()
        self.local_storage = LocalObjectStorageService(db, s3=self.s3)
        self.private_storage = PrivateDocumentStorageService(db, s3=self.s3)

    @staticmethod
    def _uses_private_aws() -> bool:
        return aws_private_storage_allowed()

    @staticmethod
    def _require_configured_backend() -> None:
        if settings.is_production and not aws_private_storage_allowed():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "DOC-008",
                    "message": "Armazenamento privado AWS não configurado.",
                },
            )

    def _queue_legacy_cleanup(
        self,
        *,
        after_commit: list[str],
        after_rollback: list[str],
    ) -> None:
        sync_session = self.db.sync_session
        if not sync_session.info.get(_LEGACY_CLEANUP_HOOKS_KEY):
            event.listen(sync_session, "after_commit", _legacy_cleanup_after_commit)
            event.listen(sync_session, "after_rollback", _legacy_cleanup_after_rollback)
            sync_session.info[_LEGACY_CLEANUP_HOOKS_KEY] = True
        sync_session.info.setdefault(_LEGACY_COMMIT_DELETE_KEY, []).extend(after_commit)
        sync_session.info.setdefault(_LEGACY_ROLLBACK_DELETE_KEY, []).extend(after_rollback)

    @staticmethod
    def _canonical_legacy_keys(
        *,
        document: Document,
        matter: Matter,
        workspace_id: UUID,
    ) -> frozenset[str]:
        filename = document.name
        return frozenset(
            {
                # Historical key emitted by DocumentService before StorageObject.
                f"matters/{matter.id}/documents/{document.id}/{filename}",
                # Historical local bootstrap used one fixed fixture filename.
                f"matters/{matter.id}/documents/{document.id}/fixture.pdf",
                # Tenant-scoped variants accepted by local migration tooling.
                (
                    f"tenants/{workspace_id}/matters/{matter.id}/"
                    f"documents/{document.id}/{filename}"
                ),
                (
                    f"tenants/{workspace_id.hex}/matters/{matter.id.hex}/"
                    f"documents/{document.id.hex}/{filename}"
                ),
            }
        )

    @staticmethod
    def _document_mime_type(data: DocumentCreate) -> str:
        guessed, _ = mimetypes.guess_type(data.name)
        return (data.mime_type or guessed or "").split(";", 1)[0].strip().lower()

    @staticmethod
    def _declared_size(value: str | None) -> int | None:
        if value is None:
            return None
        normalized = value.strip()
        return int(normalized) if normalized.isdecimal() else None

    @staticmethod
    def _raise_storage_http(error: LocalObjectStorageError) -> None:
        if isinstance(error, LocalObjectUnavailableError):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            code = "DOC-003"
        elif isinstance(error, LocalObjectNotFoundError):
            status_code = status.HTTP_404_NOT_FOUND
            code = "DOC-005"
        elif isinstance(error, LocalObjectConflictError):
            status_code = status.HTTP_409_CONFLICT
            code = "DOC-006"
        elif isinstance(error, LocalObjectValidationError):
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            code = "DOC-004"
        else:
            status_code = status.HTTP_409_CONFLICT
            code = "DOC-007"
        raise HTTPException(
            status_code=status_code,
            detail={"code": code, "message": str(error)},
        ) from error

    async def _storage_version(
        self,
        *,
        document_id: UUID,
        workspace_id: UUID,
        include_deleted: bool = False,
    ) -> StorageObject | None:
        statement = select(StorageObject).where(
            StorageObject.workspace_id == workspace_id,
            StorageObject.object_id == document_id,
        )
        if not include_deleted:
            statement = statement.where(StorageObject.deleted_at.is_(None))
        return await self.db.scalar(statement.order_by(StorageObject.version.desc()).limit(1))

    @staticmethod
    def _status_for_storage(storage_object: StorageObject) -> DocumentStatus:
        if storage_object.state is StorageObjectState.available and (
            storage_object.av_status is StorageAVStatus.clean
            or (
                not settings.is_production
                and storage_object.av_status is StorageAVStatus.simulated_unverified
            )
        ):
            return DocumentStatus.completo
        if storage_object.state is StorageObjectState.quarantined and (
            storage_object.av_status
            in {StorageAVStatus.infected, StorageAVStatus.simulated_blocked}
            or storage_object.quarantine_reason
            in {
                "checksum_mismatch",
                "size_mismatch",
                "mime_magic_invalid",
                "malware_detected",
            }
        ):
            return DocumentStatus.invalido
        return DocumentStatus.parcial

    async def get_by_matter(self, matter_id: UUID, workspace_id: UUID) -> list[Document]:
        result = await self.db.execute(
            select(Document)
            .join(Document.matter)
            .where(Document.matter_id == matter_id)
            .where(Matter.workspace_id == workspace_id, Matter.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
        )
        documents = list(result.scalars().all())
        if not documents:
            return []
        storage_rows = await self.db.execute(
            select(StorageObject.object_id, StorageObject.deleted_at).where(
                StorageObject.workspace_id == workspace_id,
                StorageObject.object_id.in_([document.id for document in documents]),
            )
        )
        object_ids_with_storage: set[UUID] = set()
        active_object_ids: set[UUID] = set()
        for object_id, deleted_at in storage_rows:
            object_ids_with_storage.add(object_id)
            if deleted_at is None:
                active_object_ids.add(object_id)
        return [
            document
            for document in documents
            if document.id not in object_ids_with_storage or document.id in active_object_ids
        ]

    async def create_with_presigned_url(
        self,
        matter_id: UUID,
        data: DocumentCreate,
        workspace_id: UUID,
        uploaded_by: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> PresignedUrlResponse:
        """
        Cria o registro do documento no banco e retorna uma URL pré-assinada
        do S3 para que o frontend faça o upload direto (sem passar pelo backend).
        """
        matter = await self.db.get(Matter, matter_id)
        if not matter or matter.deleted_at is not None or matter.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Caso não encontrado.",
            )
        doc_id = uuid.uuid4()
        mime_type = self._document_mime_type(data)
        self._require_configured_backend()
        try:
            common = {
                "workspace_id": workspace_id,
                "actor_user_id": uploaded_by,
                "filename": data.name,
                "content_type": mime_type,
                "byte_size": self._declared_size(data.file_size),
                "checksum_sha256": data.checksum_sha256,
                "retention_until": None,
                "expires_in": min(settings.S3_PRESIGNED_URL_EXPIRY, 900),
                "object_id": doc_id,
                "matter_id": matter.id,
                "client_id": matter.client_id,
                "initial_object_id": True,
            }
            if self._uses_private_aws():
                # The storage service owns the deterministic id on the first
                # authorization. That lets a retried POST resolve the same
                # document even after the original HTTP response was lost.
                common["object_id"] = None
                common["initial_object_id"] = False
                common["idempotency_key"] = idempotency_key
                authorization = await self.private_storage.create_upload_authorization(
                    **common
                )
                doc_id = authorization.storage_object.object_id
                upload_url = authorization.upload_url
                upload_headers = authorization.upload_headers
            else:
                if idempotency_key is not None:
                    common["object_id"] = None
                    common["initial_object_id"] = False
                    common["idempotency_key"] = idempotency_key
                authorization = await self.local_storage.create_upload_authorization(
                    **common
                )
                doc_id = authorization.storage_object.object_id
                upload_url = (
                    authorization.upload_url.replace(
                    "/api/v1/local-storage/object-upload",
                    "/api/v1/local-storage/upload",
                    )
                    if authorization.upload_url is not None
                    else None
                )
                upload_headers = authorization.upload_headers
        except LocalObjectStorageError as exc:
            self._raise_storage_http(exc)
        s3_key = authorization.storage_object.storage_key

        existing_document = await self.db.get(Document, doc_id)
        if existing_document is not None:
            if (
                existing_document.matter_id != matter_id
                or existing_document.name != data.name
                or existing_document.doc_type != data.doc_type
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "DOC-IDEMPOTENCY-CONFLICT",
                        "message": "Idempotency-Key já foi usada por outro documento.",
                    },
                )
            return PresignedUrlResponse(
                upload_url=upload_url,
                upload_required=getattr(authorization, "upload_required", True),
                document_id=doc_id,
                s3_key=s3_key,
                expires_in=min(settings.S3_PRESIGNED_URL_EXPIRY, 900),
                upload_headers=upload_headers,
            )

        # Criar registro no banco (status: aguardando upload)
        doc = Document(
            id=doc_id,
            matter_id=matter_id,
            created_by=uploaded_by,
            name=data.name,
            doc_type=data.doc_type,
            status=DocumentStatus.aguardando,
            s3_key=s3_key,
            file_size=data.file_size,
            mime_type=data.mime_type,
        )
        self.db.add(doc)

        # Registrar na timeline
        event = TimelineEvent(
            matter_id=matter_id,
            agent_id=uploaded_by,
            event_type=TimelineEventType.document_added,
            title=f"Documento adicionado: {data.name}",
            event_data={"document_id": str(doc_id), "type": data.doc_type.value},
        )
        self.db.add(event)
        await self.db.flush()

        logger.info("document_created", doc_id=str(doc_id), matter_id=str(matter_id))
        return PresignedUrlResponse(
            upload_url=upload_url,
            upload_required=getattr(authorization, "upload_required", True),
            document_id=doc_id,
            s3_key=s3_key,
            expires_in=min(settings.S3_PRESIGNED_URL_EXPIRY, 900),
            upload_headers=upload_headers,
        )

    async def confirm_upload(
        self,
        document_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> Document | None:
        """
        Chamado após o frontend confirmar que o upload para o S3 foi concluído.
        Atualiza o status do documento sem persistir uma URL pública.
        """
        doc = await self.db.get(Document, document_id)
        if not doc:
            return None

        matter = await self.db.get(Matter, doc.matter_id)
        if not matter or matter.deleted_at is not None or matter.workspace_id != workspace_id:
            return None

        self._require_configured_backend()
        if self._uses_private_aws():
            try:
                storage_object = await self.private_storage.confirm_upload(
                    workspace_id=workspace_id,
                    object_id=document_id,
                    actor_user_id=actor_user_id,
                )
            except LocalObjectStorageError as exc:
                self._raise_storage_http(exc)
        else:
            storage_object = await self._storage_version(
                document_id=document_id,
                workspace_id=workspace_id,
            )
            if storage_object is None:
                storage_object = await self._adopt_legacy_payload(
                    document=doc,
                    matter=matter,
                    workspace_id=workspace_id,
                )
        if storage_object is None:
            return None
        doc.status = self._status_for_storage(storage_object)
        doc.s3_url = None
        doc.s3_key = storage_object.storage_key
        doc.file_size = (
            str(storage_object.byte_size) if storage_object.byte_size is not None else doc.file_size
        )
        doc.mime_type = storage_object.detected_content_type or storage_object.declared_content_type
        if doc.status is DocumentStatus.completo and not self._uses_private_aws():
            await KnowledgeService(self.db).index_document(
                workspace_id=matter.workspace_id,
                document=doc,
            )
        await self.db.flush()
        return doc

    async def create_download_url(
        self,
        document_id: UUID,
        workspace_id: UUID,
        *,
        purpose: str = "content_download",
        actor_user_id: UUID | None = None,
    ) -> DocumentDownloadUrlResponse:
        doc = await self.db.get(Document, document_id)
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Documento não encontrado.",
            )
        matter = await self.db.get(Matter, doc.matter_id)
        if not matter or matter.deleted_at is not None or matter.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Documento não encontrado.",
            )
        self._require_configured_backend()
        storage_object = await self._storage_version(
            document_id=document_id,
            workspace_id=workspace_id,
        )
        if storage_object is None:
            storage_object = await self._adopt_legacy_payload(
                document=doc,
                matter=matter,
                workspace_id=workspace_id,
            )
        if (
            storage_object is None
            or storage_object.state is not StorageObjectState.available
            or (
                self._uses_private_aws()
                and storage_object.av_status is not StorageAVStatus.clean
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DOC-001",
                    "message": "Documento ainda não está disponível para download.",
                },
            )
        try:
            if self._uses_private_aws():
                authorization = await self.private_storage.create_download_authorization(
                    workspace_id=workspace_id,
                    object_id=document_id,
                    version=storage_object.version,
                    expires_in=min(settings.S3_PRESIGNED_URL_EXPIRY, 900),
                    actor_user_id=actor_user_id,
                )
                download_url = authorization.download_url
            else:
                authorization = await self.local_storage.create_download_authorization(
                    workspace_id=workspace_id,
                    object_id=document_id,
                    version=storage_object.version,
                    expires_in=min(settings.S3_PRESIGNED_URL_EXPIRY, 900),
                    purpose=purpose,
                )
                download_url = authorization.ticket.url
        except LocalObjectStorageError as exc:
            self._raise_storage_http(exc)
        return DocumentDownloadUrlResponse(
            download_url=download_url,
            expires_in=min(settings.S3_PRESIGNED_URL_EXPIRY, 900),
        )

    async def create_media_delivery_url(
        self,
        document_id: UUID,
        workspace_id: UUID,
    ) -> str:
        download_url, _storage_object = await self.create_media_delivery_authorization(
            document_id,
            workspace_id,
        )
        return download_url

    async def create_media_delivery_authorization(
        self,
        document_id: UUID,
        workspace_id: UUID,
    ) -> tuple[str, StorageObject]:
        """Authorize provider delivery and retain only the immutable storage identity.

        The short-lived URL is returned solely for the immediate provider call.
        Callers must persist ``storage_key``/version id, never ``download_url``.
        """
        authorization = await self.create_download_url(
            document_id,
            workspace_id,
            purpose="media_delivery",
        )
        storage_object = await self._storage_version(
            document_id=document_id,
            workspace_id=workspace_id,
        )
        if (
            storage_object is None
            or storage_object.state is not StorageObjectState.available
            or (
                self._uses_private_aws()
                and storage_object.av_status is not StorageAVStatus.clean
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DOC-001",
                    "message": "Documento ainda não está disponível para envio.",
                },
            )
        return authorization.download_url, storage_object

    async def store_generated_document(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID | None,
        actor_user_id: UUID | None,
        filename: str,
        doc_type: DocumentType,
        mime_type: str,
        payload: bytes,
        idempotency_key: str | None = None,
    ) -> tuple[Document | None, StorageObject]:
        matter: Matter | None = None
        if matter_id is not None:
            matter = await self.db.get(Matter, matter_id)
            if (
                matter is None
                or matter.deleted_at is not None
                or matter.workspace_id != workspace_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Caso não encontrado.",
                )

        document_id = uuid.uuid4()
        self._require_configured_backend()
        try:
            common = {
                "workspace_id": workspace_id,
                "actor_user_id": actor_user_id,
                "filename": filename,
                "content_type": mime_type,
                "byte_size": len(payload),
                "checksum_sha256": hashlib.sha256(payload).hexdigest(),
                "retention_until": None,
                "expires_in": 300,
                "object_id": document_id,
                "matter_id": matter.id if matter else None,
                "client_id": matter.client_id if matter else None,
                "initial_object_id": True,
            }
            if self._uses_private_aws():
                if idempotency_key is not None:
                    common["object_id"] = None
                    common["initial_object_id"] = False
                    common["idempotency_key"] = idempotency_key
                authorization = await self.private_storage.create_upload_authorization(
                    **common
                )
                document_id = authorization.storage_object.object_id
                if authorization.upload_required:
                    if authorization.storage_object.quarantine_key is None:
                        raise LocalObjectIntegrityError("Chave de quarentena ausente.")
                    await asyncio.to_thread(
                        self.s3.upload_authorized_private_file,
                        io.BytesIO(payload),
                        authorization.storage_object.quarantine_key,
                        content_type=mime_type,
                        state="quarantine",
                        authorization_granted=True,
                        checksum_sha256=common["checksum_sha256"],
                    )
                    stored = await self.private_storage.confirm_upload(
                        workspace_id=workspace_id,
                        object_id=document_id,
                        actor_user_id=actor_user_id,
                    )
                else:
                    stored = authorization.storage_object
            else:
                authorization = await self.local_storage.create_upload_authorization(
                    **common
                )
                stored = await self.local_storage.finalize_upload(
                    ticket=authorization.ticket.token,
                    data=payload,
                    request_content_type=mime_type,
                )
        except LocalObjectStorageError as exc:
            self._raise_storage_http(exc)
        except StorageUnavailableError as exc:
            self._raise_storage_http(LocalObjectUnavailableError(str(exc)))

        document: Document | None = None
        if matter is not None:
            document = await self.db.get(Document, document_id)
        if matter is not None and document is None:
            document = Document(
                id=document_id,
                matter_id=matter.id,
                created_by=actor_user_id,
                name=filename,
                doc_type=doc_type,
                status=self._status_for_storage(stored),
                s3_key=stored.storage_key,
                s3_url=None,
                file_size=str(stored.byte_size) if stored.byte_size is not None else None,
                mime_type=stored.detected_content_type or stored.declared_content_type,
            )
            self.db.add(document)
            self.db.add(
                TimelineEvent(
                    matter_id=matter.id,
                    agent_id=actor_user_id,
                    event_type=TimelineEventType.document_added,
                    title=f"Documento gerado: {filename}",
                    event_data={
                        "document_id": str(document_id),
                        "storage_version": stored.version,
                    },
                )
            )
            await self.db.flush()
        return document, stored

    async def delete(
        self,
        document_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> bool:
        doc = await self.db.get(Document, document_id)
        if not doc:
            return False
        matter = await self.db.get(Matter, doc.matter_id)
        if not matter or matter.workspace_id != workspace_id:
            return False
        storage_object = await self._storage_version(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=True,
        )
        if storage_object is None:
            storage_object = await self._adopt_legacy_payload(
                document=doc,
                matter=matter,
                workspace_id=workspace_id,
            )
        if storage_object is None:
            self._raise_storage_http(
                LocalObjectConflictError(
                    "Documento sem StorageObject não pode ser excluído silenciosamente."
                )
            )
        was_deleted = storage_object.deleted_at is not None
        try:
            self._require_configured_backend()
            if self._uses_private_aws():
                await self.private_storage.soft_delete(
                    workspace_id=workspace_id,
                    object_id=document_id,
                    actor_user_id=actor_user_id,
                )
            else:
                await self.local_storage.soft_delete(
                    workspace_id=workspace_id,
                    object_id=document_id,
                    actor_user_id=actor_user_id,
                )
        except LocalObjectStorageError as exc:
            self._raise_storage_http(exc)
        doc.status = DocumentStatus.invalido
        doc.s3_url = None
        if not was_deleted:
            self.db.add(
                TimelineEvent(
                    matter_id=matter.id,
                    agent_id=actor_user_id,
                    event_type=TimelineEventType.agent_action,
                    title=f"Documento excluído: {doc.name}",
                    event_data={"document_id": str(document_id), "action": "soft_delete"},
                )
            )
        await self.db.flush()
        return True

    async def restore(
        self,
        document_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> Document | None:
        doc = await self.db.get(Document, document_id)
        if doc is None:
            return None
        matter = await self.db.get(Matter, doc.matter_id)
        if matter is None or matter.workspace_id != workspace_id:
            return None
        if matter.deleted_at is not None:
            self._raise_storage_http(
                LocalObjectConflictError(
                    "Restaure o caso antes de restaurar um documento vinculado."
                )
            )
        storage_object = await self._storage_version(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=True,
        )
        if storage_object is None:
            return None
        was_deleted = storage_object.deleted_at is not None
        try:
            self._require_configured_backend()
            if self._uses_private_aws():
                restored = await self.private_storage.restore(
                    workspace_id=workspace_id,
                    object_id=document_id,
                    actor_user_id=actor_user_id,
                )
            else:
                restored = await self.local_storage.restore(
                    workspace_id=workspace_id,
                    object_id=document_id,
                    actor_user_id=actor_user_id,
                )
        except LocalObjectStorageError as exc:
            self._raise_storage_http(exc)
        doc.status = self._status_for_storage(restored)
        doc.s3_key = restored.storage_key
        doc.s3_url = None
        doc.file_size = str(restored.byte_size) if restored.byte_size is not None else None
        doc.mime_type = restored.detected_content_type or restored.declared_content_type
        if was_deleted:
            self.db.add(
                TimelineEvent(
                    matter_id=matter.id,
                    agent_id=actor_user_id,
                    event_type=TimelineEventType.agent_action,
                    title=f"Documento restaurado: {doc.name}",
                    event_data={"document_id": str(document_id), "action": "restore"},
                )
            )
        await self.db.flush()
        return doc

    async def _adopt_legacy_payload(
        self,
        *,
        document: Document,
        matter: Matter,
        workspace_id: UUID,
    ) -> StorageObject | None:
        """Move a pre-StorageObject local document through the hardened lifecycle."""
        if settings.is_production or not document.s3_key:
            return None
        legacy_key = document.s3_key
        if legacy_key not in self._canonical_legacy_keys(
            document=document,
            matter=matter,
            workspace_id=workspace_id,
        ):
            self._raise_storage_http(
                LocalObjectConflictError(
                    "Chave legada não corresponde ao tenant, caso e documento esperados."
                )
            )
        try:
            source = await asyncio.to_thread(self.s3.open_local_object, legacy_key)
        except (FileNotFoundError, ValueError, StorageUnavailableError):
            return None
        try:
            digest = hashlib.sha256()
            size = 0
            while chunk := await asyncio.to_thread(source.read, 1024 * 1024):
                size += len(chunk)
                if size > settings.LOCAL_STORAGE_MAX_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail={"code": "DOC-009", "message": "Documento legado excede o limite."},
                    )
                digest.update(chunk)
            source.seek(0)
            mime_type = (document.mime_type or mimetypes.guess_type(document.name)[0] or "").lower()
            try:
                authorization = await self.local_storage.create_upload_authorization(
                    workspace_id=workspace_id,
                    actor_user_id=document.created_by,
                    filename=document.name,
                    content_type=mime_type,
                    byte_size=size,
                    checksum_sha256=digest.hexdigest(),
                    retention_until=None,
                    expires_in=300,
                    object_id=document.id,
                    matter_id=matter.id,
                    client_id=matter.client_id,
                    initial_object_id=True,
                )
            except LocalObjectStorageError as exc:
                self._raise_storage_http(exc)
            staging_key = self.local_storage.staging_key(workspace_id)
            await asyncio.to_thread(
                self.s3.write_local_object_atomic,
                staging_key,
                source,
            )
        finally:
            source.close()
        try:
            adopted = await self.local_storage.finalize_staged_upload(
                ticket=authorization.ticket.token,
                staging_key=staging_key,
                request_content_type=mime_type,
            )
        except LocalObjectStorageError as exc:
            self._raise_storage_http(exc)
        rollback_keys = [adopted.storage_key]
        if adopted.quarantine_key:
            rollback_keys.append(adopted.quarantine_key)
        self._queue_legacy_cleanup(
            after_commit=[legacy_key] if legacy_key != adopted.storage_key else [],
            after_rollback=rollback_keys,
        )
        document.s3_key = adopted.storage_key
        document.mime_type = adopted.detected_content_type or adopted.declared_content_type
        document.file_size = str(adopted.byte_size) if adopted.byte_size is not None else None
        document.status = (
            DocumentStatus.completo
            if adopted.state is StorageObjectState.available
            else DocumentStatus.invalido
        )
        await self.db.flush()
        return adopted
