"""
Modelo: DMS jurídico (Onda 7, Plano Mestre §11).

O Meu Executivo é a fonte oficial de documentos; integrações externas
(Drive/OneDrive/SharePoint) são espelho GOVERNADO, nunca a fonte de
verdade — um conflito de sincronização nunca é resolvido silenciosamente
(ver `LegalDocumentService.report_sync_conflict` → Central de Exceções).

REUSA deliberada (não duplicada aqui):
  * `StorageObject` (app/models/storage_object.py) continua sendo o único
    lugar que guarda bytes, versão física, quarentena e antivírus
    (Onda "fundação local"). `DocumentAsset` NUNCA reimplementa upload,
    scan ou quarentena — ele só referencia a versão física canônica
    (`storage_object_id`) e adiciona os metadados que faltavam no nível
    de documento jurídico (classificação, OCR, custódia, referências de
    ingestão, compartilhamento).
  * A disciplina de hash SHA-256 como ancoragem de custódia é a mesma já
    usada em `StorageObject.checksum_sha256` e em
    `OmnichannelMessageEvidenceEvent.evidence_sha256`
    (app/models/omnichannel_v6.py) — `DocumentAsset.content_sha256` é o
    MESMO hash, apenas denormalizado aqui para permitir a deduplicação
    por conteúdo sem reler bytes (índice único por workspace).

Modelo de deduplicação — "mesmo arquivo 2x = 1 asset + nova referência":
  * `DocumentAsset` é o registro CANÔNICO de um conteúdo (1 por hash único
    por workspace). Ele nasce com a primeira versão física em
    `StorageObject` que guardou aqueles bytes.
  * `DocumentAssetReference` é criado a CADA ingestão (upload manual,
    e-mail, WhatsApp, espelho de sincronização) — inclusive quando o
    conteúdo já existe (mesmo hash): nesse caso nenhum byte novo é
    gravado, só nasce uma nova referência apontando para o mesmo asset.

Classificação e OCR são fail-closed honestos (regra global do titular:
nunca fingir um resultado que não existe):
  * `classification_method='indisponivel'` + `document_type=nao_classificado`
    é o estado inicial e permanece assim enquanto nenhum classificador de
    IA real estiver plugado (ver `LegalDocumentService._classify`).
  * `ocr_status='ocr_pendente'` é o estado honesto atual: não há
    biblioteca de OCR instalada neste runtime (grep em requirements.txt —
    só Pillow, sem pytesseract/equivalente). Nunca marcamos 'concluido'
    sem OCR real.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.workspace import Workspace


class DocumentAssetSource(enum.StrEnum):
    """Origem de uma ingestão (Plano Mestre §11 item 3)."""

    upload_manual = "upload_manual"
    email = "email"
    whatsapp = "whatsapp"
    sync_mirror = "sync_mirror"


class DocumentAssetType(enum.StrEnum):
    """Classificação por tipo de documento jurídico.

    Espelha `app.models.document.DocumentType` (mesmos rótulos, mais
    `correspondencia` e o estado honesto `nao_classificado`) — não
    reutiliza o enum nativo Postgres `document_type_enum` de propósito:
    alterar um ENUM nativo em produção via ALTER TYPE ADD VALUE é uma
    operação de risco desproporcional ao ganho de reaproveitar o nome;
    um enum próprio (native_enum=False, CHECK constraint) é aditivo e
    seguro de migrar.
    """

    peticao_inicial = "peticao_inicial"
    procuracao = "procuracao"
    contrato = "contrato"
    comprovante = "comprovante"
    sentenca = "sentenca"
    recurso = "recurso"
    correspondencia = "correspondencia"
    outro = "outro"
    nao_classificado = "nao_classificado"


class DocumentClassificationMethod(enum.StrEnum):
    ia = "ia"
    manual = "manual"
    indisponivel = "indisponivel"


class DocumentOCRStatus(enum.StrEnum):
    nao_aplicavel = "nao_aplicavel"
    ocr_pendente = "ocr_pendente"
    concluido = "concluido"
    indisponivel = "indisponivel"


def _string_enum(enum_type: type[enum.StrEnum], name: str, length: int = 32) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [value.value for value in values],
        length=length,
    )


class DocumentAsset(UUIDMixin, TimestampMixin, Base):
    """Registro canônico de UM conteúdo (1 por `content_sha256` no workspace)."""

    __tablename__ = "document_assets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "content_sha256", name="uq_document_asset_workspace_content_sha256"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "storage_object_id"],
            ["storage_objects.workspace_id", "storage_objects.id"],
            name="fk_document_asset_workspace_storage_object",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_document_asset_workspace_matter",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_document_asset_workspace_client",
            ondelete="SET NULL",
        ),
        Index("ix_document_asset_workspace_type", "workspace_id", "document_type"),
        Index("ix_document_asset_workspace_matter", "workspace_id", "matter_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_object_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    client_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)

    document_type: Mapped[DocumentAssetType] = mapped_column(
        _string_enum(DocumentAssetType, "document_asset_type_enum"),
        nullable=False,
        default=DocumentAssetType.nao_classificado,
        server_default=DocumentAssetType.nao_classificado.value,
    )
    classification_method: Mapped[DocumentClassificationMethod] = mapped_column(
        _string_enum(DocumentClassificationMethod, "document_classification_method_enum"),
        nullable=False,
        default=DocumentClassificationMethod.indisponivel,
        server_default=DocumentClassificationMethod.indisponivel.value,
    )
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ocr_status: Mapped[DocumentOCRStatus] = mapped_column(
        _string_enum(DocumentOCRStatus, "document_ocr_status_enum"),
        nullable=False,
        default=DocumentOCRStatus.ocr_pendente,
        server_default=DocumentOCRStatus.ocr_pendente.value,
    )
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_metadata: Mapped[dict] = mapped_column(
        FlexibleJSON(), nullable=False, default=dict, server_default="{}"
    )

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        hash_prefix = self.content_sha256[:8]
        return f"<DocumentAsset id={self.id} type={self.document_type} hash={hash_prefix}>"


class DocumentAssetReference(UUIDMixin, TimestampMixin, Base):
    """Cada ingestão (mesmo que o conteúdo já exista) nasce uma referência.

    Idempotência de ingestão automatizada: `(workspace_id, source, external_ref)`
    é único quando `external_ref` não é nulo — reprocessar o mesmo e-mail/
    mensagem de WhatsApp nunca duplica a referência (mesma disciplina de
    `LegalException`/`ApprovalQueueItem`).
    """

    __tablename__ = "document_asset_references"
    __table_args__ = (
        Index(
            "uq_document_asset_reference_source_external",
            "workspace_id",
            "source",
            "external_ref",
            unique=True,
            postgresql_where=text("external_ref IS NOT NULL"),
            sqlite_where=text("external_ref IS NOT NULL"),
        ),
        Index("ix_document_asset_reference_asset", "workspace_id", "document_asset_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_asset_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("document_assets.id", ondelete="CASCADE"), nullable=False
    )
    matter_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("matters.id", ondelete="SET NULL"), nullable=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[DocumentAssetSource] = mapped_column(
        _string_enum(DocumentAssetSource, "document_asset_source_enum"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    document_asset: Mapped[DocumentAsset] = relationship("DocumentAsset")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<DocumentAssetReference id={self.id} source={self.source}>"


class DocumentShareLink(UUIDMixin, TimestampMixin, Base):
    """Link de compartilhamento com expiração obrigatória (Plano Mestre §11 item 2)."""

    __tablename__ = "document_share_links"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_document_share_link_token_hash"),
        Index("ix_document_share_link_workspace_asset", "workspace_id", "document_asset_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_asset_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("document_assets.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    watermark_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    max_downloads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    document_asset: Mapped[DocumentAsset] = relationship("DocumentAsset")

    @property
    def is_usable(self) -> bool:
        from datetime import UTC
        from datetime import datetime as _dt

        now = _dt.now(UTC)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if self.revoked_at is not None:
            return False
        if expires_at <= now:
            return False
        if self.max_downloads is not None and self.download_count >= self.max_downloads:
            return False
        return True


class DocumentShareDownload(UUIDMixin, TimestampMixin, Base):
    """Trilha de download — quem/quando baixou via link compartilhado."""

    __tablename__ = "document_share_downloads"
    __table_args__ = (
        Index("ix_document_share_download_link", "workspace_id", "share_link_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    share_link_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("document_share_links.id", ondelete="CASCADE"), nullable=False
    )
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    downloaded_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    downloader_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
