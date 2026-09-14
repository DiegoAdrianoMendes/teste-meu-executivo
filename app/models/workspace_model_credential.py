"""
Modelo: credencial BYOK de LLM por workspace.

Cada workspace pode trazer a própria chave de API por provider de modelo
(BYOK). A chave é cifrada em repouso (molde BYOA — Fernet/MultiFernet via
INTEGRATION_SECRET_ENCRYPTION_KEY) e é write-only: nenhuma resposta de API
ou log expõe o valor em claro; apenas o hint (últimos 4 caracteres) é
armazenado para exibição redigida.
"""
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.workspace import Workspace

# Providers aceitos para credencial BYOK. "vertex" e "maritaca" são aceitos
# no armazenamento hoje; a resolução no gateway aplica a chave apenas aos
# adapters baseados em API key já existentes (anthropic, openai, gemini).
MODEL_CREDENTIAL_PROVIDERS = (
    "anthropic",
    "openai",
    "gemini",
    "vertex",
    "maritaca",
)


class WorkspaceModelCredential(UUIDMixin, TimestampMixin, Base):
    """Chave de API de LLM cifrada, única por (workspace, provider)."""

    __tablename__ = "workspace_model_credentials"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            name="uq_workspace_model_credentials_workspace_provider",
        ),
        CheckConstraint(
            "provider IN ('anthropic', 'openai', 'gemini', 'vertex', 'maritaca')",
            name="ck_workspace_model_credentials_provider",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Chave cifrada em repouso; nunca retorna em resposta de API nem em log.
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Últimos 4 caracteres em claro, exclusivamente para exibição redigida.
    api_key_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    workspace: Mapped["Workspace"] = relationship("Workspace")

    def __repr__(self) -> str:
        return (
            f"<WorkspaceModelCredential workspace_id={self.workspace_id} "
            f"provider={self.provider} is_active={self.is_active}>"
        )
