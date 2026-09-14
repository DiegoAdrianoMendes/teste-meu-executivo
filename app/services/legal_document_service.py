"""
Serviço: DMS jurídico (Onda 7, Plano Mestre §11).

O Meu Executivo é a fonte oficial de documentos — integrações externas
são espelho GOVERNADO, nunca a fonte de verdade. Este serviço NUNCA
reimplementa upload, antivírus, quarentena ou versionamento físico: tudo
isso já existe em `LocalObjectStorageService`/`StorageObject` (fundação
local) e é reaproveitado aqui através de `create_upload_authorization` +
`finalize_upload` (o mesmo caminho que `DocumentService.store_generated_document`
já usa). Este serviço só acrescenta a camada de metadados de DMS jurídico
que faltava: classificação, OCR, deduplicação por hash, referências de
ingestão, compartilhamento com expiração e trilha de download.

Deduplicação ("mesmo arquivo 2x = 1 asset + nova referência"): o hash
SHA-256 do conteúdo é a chave. A primeira ingestão de um conteúdo grava
bytes (via `StorageObject`) e cria o `DocumentAsset` canônico. Qualquer
ingestão subsequente com o MESMO hash não grava bytes novamente — só
nasce uma nova `DocumentAssetReference` apontando para o asset existente.

Classificação e OCR são fail-closed honestos: sem um classificador de IA
real plugado, todo asset nasce `nao_classificado`/`indisponivel`; sem uma
biblioteca de OCR instalada neste runtime, todo asset nasce
`ocr_pendente` — nunca fingimos um resultado que não existe (regra global
do titular). Conteúdo colocado em quarentena pelo pipeline de antivírus
NUNCA é classificado ou tem OCR tentado: fica com `nao_classificado`/
`nao_aplicavel` até um humano revisar a quarentena.

Conflito de sincronização com um espelho externo (Drive/OneDrive/
SharePoint) NUNCA é resolvido silenciosamente — vira item na Central de
Exceções (`ExceptionCenterService`) via `report_sync_conflict`.

Nota de escopo (backend-only, Onda 7): `resolve_share_download` é
exercido por um endpoint autenticado (capability `legal`) que registra a
trilha e aplica a expiração — não existe hoje uma rota pública anônima
para o link, porque isso exigiria uma tabela de bootstrap isenta de RLS
(mesmo desenho de `workspace_public_api_tokens`, ver
app/db/rls.py::RLS_EXEMPT_DIRECT_TENANT_TABLES) resolvida ANTES do
contexto de workspace existir. Essa decisão de arquitetura fica para uma
mudança própria e revisada, não incluída nesta fatia.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import aws_private_storage_allowed
from app.models.document_asset import (
    DocumentAsset,
    DocumentAssetReference,
    DocumentAssetSource,
    DocumentAssetType,
    DocumentClassificationMethod,
    DocumentOCRStatus,
    DocumentShareDownload,
    DocumentShareLink,
)
from app.models.legal_exception import ExceptionKind, LegalException
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.services.entitlement_service import EntitlementEnforcementService, EntitlementKey
from app.services.exception_center_service import ExceptionCenterService
from app.services.local_object_storage_service import (
    LocalObjectStorageError,
    LocalObjectStorageService,
)
from app.services.private_document_storage_service import PrivateDocumentStorageService
from app.services.s3_service import S3Service, StorageUnavailableError


class LegalDocumentError(RuntimeError):
    """Base para erros de domínio do DMS jurídico."""


class LegalDocumentNotFoundError(LegalDocumentError):
    pass


class LegalDocumentValidationError(LegalDocumentError):
    pass


class LegalDocumentConflictError(LegalDocumentError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ocr_library_available() -> bool:
    """Honestidade fail-closed: só retorna True se uma lib de OCR real existir.

    Nenhuma biblioteca de OCR está instalada neste runtime (requirements.txt
    só declara Pillow). Enquanto isso for verdade, todo asset novo nasce
    'ocr_pendente' — nunca fingimos 'concluido' sem OCR real.
    """
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def _ai_classifier_available() -> bool:
    """Fail-closed: hoje não há classificador de tipo documental por IA plugado.

    Seam deliberado para o futuro (ver docstring do módulo) — mantém a
    honestidade 'nao_classificado' até existir um classificador real.
    """
    return False


@dataclass(frozen=True)
class IngestResult:
    asset: DocumentAsset
    reference: DocumentAssetReference
    created_new_asset: bool
    deduplicated: bool
    storage_object: StorageObject | None = None


@dataclass(frozen=True)
class ShareLinkIssued:
    share_link: DocumentShareLink
    raw_token: str


@dataclass(frozen=True)
class ShareDownloadResult:
    share_link: DocumentShareLink
    storage_object: StorageObject
    handle: BinaryIO


class LegalDocumentService:
    def __init__(self, db: AsyncSession, *, s3: S3Service | None = None) -> None:
        self.db = db
        self.s3 = s3 or S3Service()
        self.local_storage = LocalObjectStorageService(db, s3=self.s3)
        self.private_storage = PrivateDocumentStorageService(db, s3=self.s3)

    async def _require_legal_suite(self, workspace_id: UUID) -> None:
        """Trava o workspace e recusa contratos sem o Jurídico 360 habilitado."""
        await EntitlementEnforcementService(self.db).enforce_feature(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.legal_suite,
        )

    async def _enforce_new_asset_quota(self, workspace_id: UUID) -> None:
        """Reserva capacidade lógica para um novo asset canônico.

        A contagem é de ``DocumentAsset`` único por hash, não de referências:
        anexar novamente o mesmo conteúdo não consome uma segunda unidade.
        ``enforce_stock`` mantém o lock do workspace até o commit/rollback do
        chamador, serializando a leitura da quantidade com a criação abaixo.
        """

        async def _load_current_quantity() -> int:
            return int(
                await self.db.scalar(
                    select(func.count(DocumentAsset.id)).where(
                        DocumentAsset.workspace_id == workspace_id
                    )
                )
                or 0
            )

        await EntitlementEnforcementService(self.db).enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.legal_documents,
            load_current_quantity=_load_current_quantity,
        )

    # ── Leitura ──────────────────────────────────────────────────────────

    async def get_asset(self, *, workspace_id: UUID, document_asset_id: UUID) -> DocumentAsset:
        asset = await self.db.scalar(
            select(DocumentAsset).where(
                DocumentAsset.workspace_id == workspace_id,
                DocumentAsset.id == document_asset_id,
            )
        )
        if asset is None:
            raise LegalDocumentNotFoundError("Documento não encontrado.")
        return asset

    async def list_assets(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID | None = None,
        document_type: DocumentAssetType | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DocumentAsset]:
        statement = select(DocumentAsset).where(DocumentAsset.workspace_id == workspace_id)
        if matter_id is not None:
            statement = statement.where(DocumentAsset.matter_id == matter_id)
        if document_type is not None:
            statement = statement.where(DocumentAsset.document_type == document_type)
        if query:
            statement = statement.where(DocumentAsset.original_filename.ilike(f"%{query}%"))
        statement = statement.order_by(DocumentAsset.created_at.desc()).limit(limit).offset(offset)
        return list((await self.db.scalars(statement)).all())

    async def list_quarantined(self, *, workspace_id: UUID) -> list[DocumentAsset]:
        statement = (
            select(DocumentAsset)
            .join(
                StorageObject,
                and_(
                    StorageObject.workspace_id == DocumentAsset.workspace_id,
                    StorageObject.id == DocumentAsset.storage_object_id,
                ),
            )
            .where(
                DocumentAsset.workspace_id == workspace_id,
                StorageObject.state == StorageObjectState.quarantined,
            )
            .order_by(DocumentAsset.created_at.desc())
        )
        return list((await self.db.scalars(statement)).all())

    async def list_references(
        self, *, workspace_id: UUID, document_asset_id: UUID
    ) -> list[DocumentAssetReference]:
        await self.get_asset(workspace_id=workspace_id, document_asset_id=document_asset_id)
        statement = (
            select(DocumentAssetReference)
            .where(
                DocumentAssetReference.workspace_id == workspace_id,
                DocumentAssetReference.document_asset_id == document_asset_id,
            )
            .order_by(DocumentAssetReference.created_at.desc())
        )
        return list((await self.db.scalars(statement)).all())

    async def get_storage_object(self, asset: DocumentAsset) -> StorageObject | None:
        return await self.db.scalar(
            select(StorageObject).where(
                StorageObject.workspace_id == asset.workspace_id,
                StorageObject.id == asset.storage_object_id,
            )
        )

    async def reconcile_clean_storage_object(
        self,
        *,
        workspace_id: UUID,
        storage_object: StorageObject,
    ) -> int:
        """Move linked assets out of AV quarantine after a real clean receipt.

        This is state reconciliation for assets already counted at ingestion;
        it creates no stock and invokes no OCR/classifier provider. Any later
        paid processing job must enforce its own entitlement before execution.
        """
        if (
            storage_object.workspace_id != workspace_id
            or storage_object.state is not StorageObjectState.available
            or storage_object.av_status is not StorageAVStatus.clean
        ):
            raise LegalDocumentConflictError(
                "Somente um objeto disponível com antivírus limpo pode ser reconciliado."
            )
        assets = list(
            (
                await self.db.scalars(
                    select(DocumentAsset).where(
                        DocumentAsset.workspace_id == workspace_id,
                        DocumentAsset.storage_object_id == storage_object.id,
                    )
                )
            ).all()
        )
        updated = 0
        for asset in assets:
            if (
                asset.document_type is DocumentAssetType.nao_classificado
                and asset.classification_method
                is DocumentClassificationMethod.indisponivel
                and asset.ocr_status is DocumentOCRStatus.nao_aplicavel
            ):
                self._apply_honest_processing_state(
                    asset,
                    storage_state=StorageObjectState.available,
                )
                updated += 1
        if updated:
            await self.db.flush()
        return updated

    # ── Ingestão ─────────────────────────────────────────────────────────

    async def _existing_reference(
        self,
        *,
        workspace_id: UUID,
        source: DocumentAssetSource,
        external_ref: str,
    ) -> DocumentAssetReference | None:
        return await self.db.scalar(
            select(DocumentAssetReference).where(
                DocumentAssetReference.workspace_id == workspace_id,
                DocumentAssetReference.source == source,
                DocumentAssetReference.external_ref == external_ref,
            )
        )

    async def _find_asset_by_hash(
        self, *, workspace_id: UUID, content_sha256: str
    ) -> DocumentAsset | None:
        return await self.db.scalar(
            select(DocumentAsset).where(
                DocumentAsset.workspace_id == workspace_id,
                DocumentAsset.content_sha256 == content_sha256,
            )
        )

    def _apply_honest_processing_state(
        self, asset: DocumentAsset, *, storage_state: StorageObjectState
    ) -> None:
        if storage_state is StorageObjectState.quarantined:
            # Conteúdo hostil: NUNCA classificar ou tentar OCR — fica pendente
            # de revisão humana da quarentena (ver app/services/
            # local_object_storage_service.py::fake_antivirus_scan).
            asset.document_type = DocumentAssetType.nao_classificado
            asset.classification_method = DocumentClassificationMethod.indisponivel
            asset.classification_confidence = None
            asset.classified_at = None
            asset.ocr_status = DocumentOCRStatus.nao_aplicavel
            return
        if _ai_classifier_available():  # pragma: no cover - seam sem provedor real hoje
            raise NotImplementedError("Classificador de IA ainda não está plugado.")
        asset.document_type = DocumentAssetType.nao_classificado
        asset.classification_method = DocumentClassificationMethod.indisponivel
        asset.classification_confidence = None
        asset.classified_at = None
        # `_ocr_library_available()` é False hoje (nenhuma lib de OCR instalada);
        # o estado honesto é 'ocr_pendente'. Se um dia uma lib real for
        # adicionada, o OCR de fato precisa rodar antes de marcar 'concluido'
        # — isso ainda não está implementado aqui, então nunca marcamos
        # 'concluido' neste caminho.
        asset.ocr_status = (
            DocumentOCRStatus.ocr_pendente
            if not _ocr_library_available()
            else DocumentOCRStatus.indisponivel
        )

    async def _create_reference(
        self,
        *,
        workspace_id: UUID,
        document_asset_id: UUID,
        matter_id: UUID | None,
        client_id: UUID | None,
        source: DocumentAssetSource,
        filename: str,
        external_ref: str | None,
        actor_user_id: UUID | None,
    ) -> DocumentAssetReference:
        reference = DocumentAssetReference(
            workspace_id=workspace_id,
            document_asset_id=document_asset_id,
            matter_id=matter_id,
            client_id=client_id,
            source=source,
            original_filename=filename,
            external_ref=external_ref,
            created_by_user_id=actor_user_id,
        )
        self.db.add(reference)
        await self.db.flush()
        return reference

    async def ingest_bytes(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        filename: str,
        content_type: str,
        payload: bytes,
        source: DocumentAssetSource = DocumentAssetSource.upload_manual,
        matter_id: UUID | None = None,
        client_id: UUID | None = None,
        external_ref: str | None = None,
    ) -> IngestResult:
        """Ingesta bytes crus (upload multipart, anexo de e-mail já baixado etc.).

        Idempotente por `(workspace_id, source, external_ref)` quando
        `external_ref` é informado, e deduplicado por hash de conteúdo
        sempre — reprocessar o mesmo arquivo nunca grava bytes duas vezes.
        """
        await self._require_legal_suite(workspace_id)

        if external_ref is not None:
            existing_reference = await self._existing_reference(
                workspace_id=workspace_id, source=source, external_ref=external_ref
            )
            if existing_reference is not None:
                asset = await self.get_asset(
                    workspace_id=workspace_id,
                    document_asset_id=existing_reference.document_asset_id,
                )
                return IngestResult(
                    asset=asset,
                    reference=existing_reference,
                    created_new_asset=False,
                    deduplicated=True,
                )

        content_sha256 = hashlib.sha256(payload).hexdigest()
        existing_asset = await self._find_asset_by_hash(
            workspace_id=workspace_id, content_sha256=content_sha256
        )
        if existing_asset is not None:
            reference = await self._create_reference(
                workspace_id=workspace_id,
                document_asset_id=existing_asset.id,
                matter_id=matter_id,
                client_id=client_id,
                source=source,
                filename=filename,
                external_ref=external_ref,
                actor_user_id=actor_user_id,
            )
            return IngestResult(
                asset=existing_asset,
                reference=reference,
                created_new_asset=False,
                deduplicated=True,
            )

        object_id = uuid.uuid4()
        try:
            common = {
                "workspace_id": workspace_id,
                "actor_user_id": actor_user_id,
                "filename": filename,
                "content_type": content_type,
                "byte_size": len(payload),
                "checksum_sha256": content_sha256,
                "retention_until": None,
                "expires_in": 300,
                "object_id": object_id,
                "matter_id": matter_id,
                "client_id": client_id,
                "initial_object_id": True,
            }
            if aws_private_storage_allowed():
                # Persist the isolated S3 intent before taking the workspace
                # quota lock.  The authorization uses a dedicated tenant
                # transaction; opening it while this transaction holds
                # ``FOR UPDATE`` on the workspace would make its tenant FK wait
                # on the caller and deadlock the command.  Quota enforcement and
                # DocumentAsset creation still remain in this same outer
                # transaction, so concurrent ingests cannot oversubscribe.
                authorization = await self.private_storage.create_upload_authorization(
                    **common
                )
                await self._enforce_new_asset_quota(workspace_id)
                if authorization.storage_object.quarantine_key is None:
                    raise LegalDocumentValidationError("Chave de quarentena ausente.")
                await asyncio.to_thread(
                    self.s3.upload_authorized_private_file,
                    io.BytesIO(payload),
                    authorization.storage_object.quarantine_key,
                    content_type=content_type,
                    state="quarantine",
                    authorization_granted=True,
                    checksum_sha256=content_sha256,
                )
                stored = await self.private_storage.confirm_upload(
                    workspace_id=workspace_id,
                    object_id=object_id,
                    actor_user_id=actor_user_id,
                )
            else:
                await self._enforce_new_asset_quota(workspace_id)
                authorization = await self.local_storage.create_upload_authorization(
                    **common
                )
                stored = await self.local_storage.finalize_upload(
                    ticket=authorization.ticket.token,
                    data=payload,
                    request_content_type=content_type,
                )
        except LocalObjectStorageError as exc:
            raise LegalDocumentValidationError(str(exc)) from exc
        except StorageUnavailableError as exc:
            raise LegalDocumentValidationError(str(exc)) from exc

        asset = DocumentAsset(
            workspace_id=workspace_id,
            storage_object_id=stored.id,
            content_sha256=content_sha256,
            original_filename=filename,
            matter_id=matter_id,
            client_id=client_id,
            created_by_user_id=actor_user_id,
        )
        self.db.add(asset)
        await self.db.flush()
        self._apply_honest_processing_state(asset, storage_state=stored.state)

        reference = await self._create_reference(
            workspace_id=workspace_id,
            document_asset_id=asset.id,
            matter_id=matter_id,
            client_id=client_id,
            source=source,
            filename=filename,
            external_ref=external_ref,
            actor_user_id=actor_user_id,
        )
        await self.db.flush()
        await self.db.refresh(asset)
        return IngestResult(
            asset=asset,
            reference=reference,
            created_new_asset=True,
            deduplicated=False,
            storage_object=stored,
        )

    async def attach_existing_storage_object(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        storage_object: StorageObject,
        filename: str,
        source: DocumentAssetSource,
        matter_id: UUID | None = None,
        client_id: UUID | None = None,
        external_ref: str | None = None,
    ) -> IngestResult:
        """Reaproveita um `StorageObject` já capturado por um pipeline existente.

        Ponto de integração para produtores que JÁ capturam anexos hoje —
        ex.: reconciliação de mídia inbound do WhatsApp
        (`app.services.sdr_intake_service.resolve_inbound_media_storage`) ou
        um futuro conector de e-mail. Nenhum byte novo é gravado aqui: só
        cria o `DocumentAsset`/`DocumentAssetReference` sobre o objeto que
        o produtor já resolveu.
        """
        await self._require_legal_suite(workspace_id)

        if storage_object.workspace_id != workspace_id:
            raise LegalDocumentValidationError(
                "Objeto de armazenamento não pertence a este workspace."
            )
        if external_ref is not None:
            existing_reference = await self._existing_reference(
                workspace_id=workspace_id, source=source, external_ref=external_ref
            )
            if existing_reference is not None:
                asset = await self.get_asset(
                    workspace_id=workspace_id,
                    document_asset_id=existing_reference.document_asset_id,
                )
                return IngestResult(
                    asset=asset,
                    reference=existing_reference,
                    created_new_asset=False,
                    deduplicated=True,
                )
        if not storage_object.checksum_sha256:
            raise LegalDocumentValidationError(
                "Objeto de armazenamento ainda não possui checksum calculado."
            )

        existing_asset = await self._find_asset_by_hash(
            workspace_id=workspace_id, content_sha256=storage_object.checksum_sha256
        )
        if existing_asset is not None:
            reference = await self._create_reference(
                workspace_id=workspace_id,
                document_asset_id=existing_asset.id,
                matter_id=matter_id,
                client_id=client_id,
                source=source,
                filename=filename,
                external_ref=external_ref,
                actor_user_id=actor_user_id,
            )
            return IngestResult(
                asset=existing_asset,
                reference=reference,
                created_new_asset=False,
                deduplicated=True,
            )

        await self._enforce_new_asset_quota(workspace_id)

        asset = DocumentAsset(
            workspace_id=workspace_id,
            storage_object_id=storage_object.id,
            content_sha256=storage_object.checksum_sha256,
            original_filename=filename,
            matter_id=matter_id,
            client_id=client_id,
            created_by_user_id=actor_user_id,
        )
        self.db.add(asset)
        await self.db.flush()
        self._apply_honest_processing_state(asset, storage_state=storage_object.state)

        reference = await self._create_reference(
            workspace_id=workspace_id,
            document_asset_id=asset.id,
            matter_id=matter_id,
            client_id=client_id,
            source=source,
            filename=filename,
            external_ref=external_ref,
            actor_user_id=actor_user_id,
        )
        await self.db.flush()
        await self.db.refresh(asset)
        return IngestResult(
            asset=asset,
            reference=reference,
            created_new_asset=True,
            deduplicated=False,
            storage_object=storage_object,
        )

    # ── Compartilhamento controlado ─────────────────────────────────────

    async def create_share_link(
        self,
        *,
        workspace_id: UUID,
        document_asset_id: UUID,
        actor_user_id: UUID | None,
        expires_at: datetime,
        watermark_enabled: bool = False,
        max_downloads: int | None = None,
    ) -> ShareLinkIssued:
        await self._require_legal_suite(workspace_id)
        asset = await self.get_asset(workspace_id=workspace_id, document_asset_id=document_asset_id)
        normalized_expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if normalized_expiry <= _utc_now():
            raise LegalDocumentValidationError("A expiração do link deve estar no futuro.")
        if max_downloads is not None and max_downloads <= 0:
            raise LegalDocumentValidationError("O limite de downloads deve ser positivo.")

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        link = DocumentShareLink(
            workspace_id=workspace_id,
            document_asset_id=asset.id,
            token_hash=token_hash,
            expires_at=normalized_expiry,
            watermark_enabled=watermark_enabled,
            max_downloads=max_downloads,
            created_by_user_id=actor_user_id,
        )
        self.db.add(link)
        await self.db.flush()
        await self.db.refresh(link)
        return ShareLinkIssued(share_link=link, raw_token=raw_token)

    async def revoke_share_link(
        self, *, workspace_id: UUID, share_link_id: UUID
    ) -> DocumentShareLink:
        await self._require_legal_suite(workspace_id)
        link = await self.db.scalar(
            select(DocumentShareLink).where(
                DocumentShareLink.workspace_id == workspace_id,
                DocumentShareLink.id == share_link_id,
            )
        )
        if link is None:
            raise LegalDocumentNotFoundError("Link de compartilhamento não encontrado.")
        if link.revoked_at is None:
            link.revoked_at = _utc_now()
            await self.db.flush()
        return link

    async def list_share_downloads(
        self, *, workspace_id: UUID, share_link_id: UUID
    ) -> list[DocumentShareDownload]:
        link = await self.db.scalar(
            select(DocumentShareLink).where(
                DocumentShareLink.workspace_id == workspace_id,
                DocumentShareLink.id == share_link_id,
            )
        )
        if link is None:
            raise LegalDocumentNotFoundError("Link de compartilhamento não encontrado.")
        statement = (
            select(DocumentShareDownload)
            .where(
                DocumentShareDownload.workspace_id == workspace_id,
                DocumentShareDownload.share_link_id == share_link_id,
            )
            .order_by(DocumentShareDownload.downloaded_at.desc())
        )
        return list((await self.db.scalars(statement)).all())

    async def resolve_share_download(
        self,
        *,
        workspace_id: UUID,
        raw_token: str,
        downloaded_by_user_id: UUID | None = None,
        downloader_ip: str | None = None,
        user_agent: str | None = None,
    ) -> ShareDownloadResult:
        """Valida expiração/revogação/limite, registra a trilha e devolve os bytes.

        `workspace_id` é sempre o do CHAMADOR autenticado (nunca inferido a
        partir do token) — o link só resolve se pertencer a esse workspace,
        preservando o isolamento por RLS mesmo neste caminho de leitura.
        """
        await self._require_legal_suite(workspace_id)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        link = await self.db.scalar(
            select(DocumentShareLink)
            .where(
                DocumentShareLink.workspace_id == workspace_id,
                DocumentShareLink.token_hash == token_hash,
            )
            .with_for_update()
        )
        if link is None:
            raise LegalDocumentNotFoundError("Link de compartilhamento não encontrado.")
        if not link.is_usable:
            raise LegalDocumentConflictError(
                "Link de compartilhamento expirado, revogado ou com limite de downloads atingido."
            )

        asset = await self.get_asset(
            workspace_id=workspace_id, document_asset_id=link.document_asset_id
        )
        storage_object = await self.get_storage_object(asset)
        if (
            storage_object is None
            or storage_object.deleted_at is not None
            or storage_object.state is not StorageObjectState.available
            or (
                aws_private_storage_allowed()
                and storage_object.av_status is not StorageAVStatus.clean
            )
            or not storage_object.checksum_sha256
        ):
            raise LegalDocumentConflictError("Documento não está disponível para download.")

        try:
            if aws_private_storage_allowed():
                self.private_storage.verify_private_bucket()
                receipt = self.s3.client.get_object(
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=storage_object.storage_key,
                )
                self.private_storage._verify_receipt(
                    storage_object,
                    receipt,
                    state="clean",
                )
                handle = receipt["Body"]
            else:
                handle = self.s3.open_local_object(storage_object.storage_key)
        except (
            BotoCoreError,
            ClientError,
            LocalObjectStorageError,
            OSError,
            ValueError,
            StorageUnavailableError,
        ) as exc:
            raise LegalDocumentConflictError("Payload físico não foi encontrado.") from exc

        link.download_count += 1
        self.db.add(
            DocumentShareDownload(
                workspace_id=workspace_id,
                share_link_id=link.id,
                downloaded_by_user_id=downloaded_by_user_id,
                downloader_ip=downloader_ip,
                user_agent=user_agent,
            )
        )
        await self.db.flush()
        return ShareDownloadResult(share_link=link, storage_object=storage_object, handle=handle)

    # ── Sincronização com espelhos externos ─────────────────────────────

    async def report_sync_conflict(
        self,
        *,
        workspace_id: UUID,
        provider: str,
        external_id: str,
        matter_id: UUID | None = None,
        local_checksum: str | None = None,
        remote_checksum: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> LegalException:
        """Um espelho (Drive/OneDrive/SharePoint) divergiu do canônico — vira
        exceção humana, NUNCA resolvida automaticamente (Plano Mestre §11 item 2).
        """
        await self._require_legal_suite(workspace_id)
        service = ExceptionCenterService(self.db, workspace_id)
        return await service.open_exception(
            exception_kind=ExceptionKind.conflito_sincronizacao_documental,
            origin="legal_document_service.report_sync_conflict",
            dedupe_key=f"{provider}:{external_id}",
            title=f"Conflito de sincronização documental — {provider}",
            description=(
                "O espelho externo divergiu do documento canônico no Meu Executivo. "
                "Resolução humana obrigatória — nenhuma versão foi sobrescrita "
                "automaticamente."
            ),
            payload={
                "provider": provider,
                "external_id": external_id,
                "local_checksum": local_checksum,
                "remote_checksum": remote_checksum,
            },
            matter_id=matter_id,
            opened_by=actor_user_id,
        )
