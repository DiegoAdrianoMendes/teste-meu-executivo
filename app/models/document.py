"""
Modelo: Document
Arquivos e documentos vinculados a um caso.
URLs apontam para o Amazon S3.
"""
import enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.user import User


class DocumentType(str, enum.Enum):
    peticao_inicial = "peticao_inicial"
    procuracao = "procuracao"
    contrato = "contrato"
    comprovante = "comprovante"
    sentenca = "sentenca"
    recurso = "recurso"
    outro = "outro"


class DocumentStatus(str, enum.Enum):
    aguardando = "aguardando"
    parcial = "parcial"
    invalido = "invalido"
    completo = "completo"


class Document(UUIDMixin, TimestampMixin, Base):
    """
    Entidade Document — metadados de um arquivo armazenado no S3.
    """
    __tablename__ = "documents"

    matter_id: Mapped[UUID] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type_enum"), nullable=False, default=DocumentType.outro
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status_enum"),
        nullable=False,
        default=DocumentStatus.aguardando,
        index=True,
    )
    # URL do arquivo no S3 (pode ser None se ainda não foi feito upload)
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_size: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Relacionamentos ──
    matter: Mapped["Matter"] = relationship("Matter", back_populates="documents")
    uploader: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return f"<Document id={self.id} name={self.name} status={self.status}>"
