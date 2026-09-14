"""
Base declarativa do SQLAlchemy e mixins reutilizáveis.

Tipos customizados:
  - FlexibleJSON: usa JSONB no PostgreSQL, JSON no SQLite (para testes)
  - FlexibleUUID: usa UUID nativo no PostgreSQL, String(36) no SQLite
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, CHAR


class FlexibleJSON(TypeDecorator):
    """
    Tipo JSON flexível: JSONB no PostgreSQL, JSON no SQLite.
    Permite que os testes rodem com SQLite sem modificar os modelos.
    """
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class FlexibleUUID(TypeDecorator):
    """
    Tipo UUID flexível: UUID nativo no PostgreSQL, CHAR(36) no SQLite.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value) if isinstance(value, uuid.UUID) else value

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return value
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class Base(DeclarativeBase):
    """Base para todos os modelos ORM."""
    pass


class TimestampMixin:
    """Adiciona created_at e updated_at automáticos."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDMixin:
    """Adiciona id UUID como chave primária (compatível com PostgreSQL e SQLite)."""
    id: Mapped[uuid.UUID] = mapped_column(
        FlexibleUUID(),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )


class SoftDeleteMixin:
    """Adiciona suporte a soft delete (deleted_at)."""
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(timezone.utc)
