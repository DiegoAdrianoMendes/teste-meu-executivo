"""
Modelo: credencial de provedor de IA da plataforma, gerida pelo Blackbox.

É a segunda camada da precedência de chave por provider no gateway:
BYOK do workspace (``workspace_model_credentials``) -> credencial da plataforma
(esta tabela) -> variável de ambiente. Mesmo molde criptográfico do BYOK: chave
cifrada em repouso (Fernet/MultiFernet via INTEGRATION_SECRET_ENCRYPTION_KEY) e
write-only; só o hint (últimos 4 caracteres) é armazenado em claro para
exibição redigida. Tabela de plataforma: não tem coluna de tenant nem RLS.
"""
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, FlexibleUUID, TimestampMixin, UUIDMixin


class PlatformModelCredential(UUIDMixin, TimestampMixin, Base):
    """Chave de API de LLM cifrada, única por provider, válida para toda a plataforma."""

    __tablename__ = "platform_model_credentials"
    __table_args__ = (
        UniqueConstraint("provider", name="uq_platform_model_credentials_provider"),
        CheckConstraint(
            "provider IN ('anthropic', 'openai', 'gemini', 'vertex', 'maritaca')",
            name="ck_platform_model_credentials_provider",
        ),
    )

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    # Chave cifrada em repouso; nunca retorna em resposta de API nem em log.
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Últimos 4 caracteres em claro, exclusivamente para exibição redigida.
    api_key_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Quem gravou ou desativou por último (staff da plataforma); SET NULL se o usuário sair.
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<PlatformModelCredential provider={self.provider} "
            f"is_active={self.is_active}>"
        )
