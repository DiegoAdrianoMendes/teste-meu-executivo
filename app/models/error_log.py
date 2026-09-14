"""
Modelo de rastreamento de erros da aplicação.
Cada erro capturado recebe código único + hash para identificação.
"""
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ErrorStatus(str, Enum):
    open = "open"           # não corrigido
    in_progress = "in_progress"  # em andamento
    resolved = "resolved"   # corrigido


class ErrorSeverity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Identificação única
    error_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # Ex: ERR-001234

    error_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # SHA-256 de (exception_type + message + location) — agrupa duplicatas

    # Classificação
    severity: Mapped[ErrorSeverity] = mapped_column(
        SAEnum(
            ErrorSeverity,
            native_enum=False,
            create_constraint=False,
            length=20,
        ),
        default=ErrorSeverity.medium,
    )
    status: Mapped[ErrorStatus] = mapped_column(
        SAEnum(
            ErrorStatus,
            native_enum=False,
            create_constraint=False,
            length=20,
        ),
        default=ErrorStatus.open,
        index=True,
    )

    # Detalhes do erro
    exception_type: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Ex: "app/api/v1/endpoints/matters.py:142 in get_matters"

    # Contexto
    endpoint: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    method: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    workspace_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Contagem de ocorrências (mesmo hash)
    occurrences: Mapped[int] = mapped_column(default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Resolução
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_error_logs_status_severity", "status", "severity"),
        Index("ix_error_logs_workspace_status", "workspace_id", "status"),
        Index("ix_error_logs_last_seen", "last_seen_at"),
    )
