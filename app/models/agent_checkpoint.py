"""
Modelo: AgentCheckpoint
Tabela de persistência de estado para agentes LangGraph.
Permite que agentes de IA retomem o raciocínio de onde pararam.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from app.db.base import FlexibleJSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class AgentCheckpoint(UUIDMixin, Base):
    """
    Entidade AgentCheckpoint — estado serializado de uma thread de agente.
    Compatível com o padrão de checkpointing do LangGraph.
    """
    __tablename__ = "agent_checkpoints"

    matter_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Nome do agente (ex: "klm_drafter", "klm_analyst", "klm_sdr")
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # ID da thread do LangGraph (permite múltiplas execuções por caso)
    thread_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # Estado serializado do grafo (JSON)
    state: Mapped[Optional[dict]] = mapped_column(FlexibleJSON(), nullable=True)
    # Metadados da execução (coluna 'metadata' no banco)
    checkpoint_data: Mapped[Optional[dict]] = mapped_column("metadata", FlexibleJSON(), nullable=True)
    # Status da execução
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="running"
    )  # running | completed | failed | waiting_human
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AgentCheckpoint agent={self.agent_name} thread={self.thread_id} status={self.status}>"
