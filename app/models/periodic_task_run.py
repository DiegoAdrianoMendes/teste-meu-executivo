"""Estado de execução por task periódica do Celery Beat.

Tabela de INFRAESTRUTURA GLOBAL (sem `workspace_id`): o beat_schedule é
único por deploy e não pertence a nenhum tenant — exatamente como
`system_status_observations` (99d0e1f2a3b4). Por isso NÃO entra no
inventário RLS de `app/db/rls.py`.

Guarda uma linha por `task_name` (a última execução), e não um histórico
completo, porque o risco operacional que ela fecha é "uma task periódica
falhou silenciosamente e ninguém viu" — o que se responde com o último
estado conhecido, sem custo de retenção ilimitada.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin

TASK_RUN_STATUS_RUNNING = "running"
TASK_RUN_STATUS_SUCCESS = "success"
TASK_RUN_STATUS_ERROR = "error"

#: Limite de persistência da mensagem de erro. Erro de task pode carregar
#: payload inteiro no `str(exc)`; truncar evita transformar a tabela de
#: saúde em depósito de dado sensível.
TASK_RUN_ERROR_MESSAGE_MAX_CHARS = 500


class PeriodicTaskRun(UUIDMixin, Base):
    """Última execução observada de uma task periódica do beat_schedule."""

    __tablename__ = "periodic_task_runs"

    task_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    success_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_periodic_task_runs_task_name", "task_name", unique=True),
        Index("ix_periodic_task_runs_last_status", "last_status"),
    )
