"""
Modelo: Client
Dados pessoais do cliente. Separado do Matter para suportar
um cliente com múltiplos casos no futuro.
"""
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.workspace import Workspace


class Client(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    Entidade Client — dados pessoais e de contato do cliente.
    Um cliente pode ter múltiplos casos (matters).
    """
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_clients_workspace_id",
        ),
        Index(
            "uq_clients_workspace_cpf",
            "workspace_id",
            "cpf",
            unique=True,
            postgresql_where=text("cpf IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    cpf: Mapped[Optional[str]] = mapped_column(String(14), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    employer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    position: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relacionamentos ──
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="clients")
    matters: Mapped[List["Matter"]] = relationship(
        "Matter",
        back_populates="client",
        foreign_keys="Matter.client_id",
    )

    def __repr__(self) -> str:
        return f"<Client id={self.id} name={self.name}>"
