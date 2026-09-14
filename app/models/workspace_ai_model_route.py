"""
Modelo: roteamento de modelos de IA por classe de tarefa e workspace.

Cada workspace pode fixar o par provider+modelo usado por uma classe de
tarefa do gateway (simple/medium/complex/premium). A ausência de linha para
uma classe significa "usar o padrão da plataforma" (catálogo de rotas
versionado). A escrita é sempre validada contra o catálogo do gateway —
nenhum modelo fora do catálogo/allowlist é persistido (fail-closed).
"""
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.workspace import Workspace

# Classes de tarefa aceitas — espelham TaskClass do gateway (app.ai.models).
AI_MODEL_ROUTE_TASK_CLASSES = ("simple", "medium", "complex", "premium")


class WorkspaceAiModelRoute(UUIDMixin, TimestampMixin, Base):
    """Override de rota de modelo, único por (workspace, task_class)."""

    __tablename__ = "workspace_ai_model_routes"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "task_class",
            name="uq_workspace_ai_model_routes_workspace_task_class",
        ),
        CheckConstraint(
            "task_class IN ('simple', 'medium', 'complex', 'premium')",
            name="ck_workspace_ai_model_routes_task_class",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_class: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)

    workspace: Mapped["Workspace"] = relationship("Workspace")

    def __repr__(self) -> str:
        return (
            f"<WorkspaceAiModelRoute workspace_id={self.workspace_id} "
            f"task_class={self.task_class} provider={self.provider} model={self.model}>"
        )
