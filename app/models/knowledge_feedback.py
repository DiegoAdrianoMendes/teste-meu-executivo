"""
Modelo: KnowledgeArticleFeedback
Feedback (útil / não útil) dos artigos estáticos de Conhecimento
(`frontend/src/content/knowledge`, identificados por `slug`). Fecha o elo
"feedback só em localStorage" do backlog de dormentes (Onda 1 item 16) —
antes disso o produto não tinha visibilidade agregada de quais artigos
funcionam.

`article_id` guarda o `slug` do artigo (conteúdo estático do front, sem
tabela própria), não um UUID.
"""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class KnowledgeFeedbackValue(str, enum.Enum):
    up = "up"
    down = "down"


class KnowledgeArticleFeedback(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "klm_knowledge_article_feedback"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "article_id",
            name="uq_klm_knowledge_feedback_workspace_user_article",
        ),
        Index(
            "ix_klm_knowledge_feedback_workspace_article",
            "workspace_id",
            "article_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    article_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    value: Mapped[KnowledgeFeedbackValue] = mapped_column(
        Enum(KnowledgeFeedbackValue, name="klm_knowledge_feedback_value_enum"),
        nullable=False,
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<KnowledgeArticleFeedback article={self.article_id} "
            f"value={self.value.value} workspace={self.workspace_id}>"
        )
