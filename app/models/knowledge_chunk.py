"""
Modelo: KnowledgeChunk
Memória recuperável por card/task para RAG e contexto operacional dos agentes.
"""
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.task import Task
    from app.models.user import User


class KnowledgeChunk(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "matter_id",
            "source_type",
            "source_ref",
            name="uq_knowledge_chunks_source",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rank_hint: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tags: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    chunk_metadata: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    embedding_data: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)

    matter: Mapped["Matter"] = relationship("Matter", back_populates="knowledge_chunks")
    task: Mapped[Optional["Task"]] = relationship("Task")
    created_by: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return f"<KnowledgeChunk id={self.id} source={self.source_type} matter={self.matter_id}>"
