"""Canonical legal-service contract between a workspace and one client."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin


class ClientContractStatus(StrEnum):
    draft = "draft"
    active = "active"
    ended = "ended"
    cancelled = "cancelled"


class ClientContract(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "client_contracts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_client_contracts_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_client_contracts_workspace_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_client_contracts_workspace_matter",
            ondelete="SET NULL",
        ),
        Index("ix_client_contracts_workspace_client_status", "workspace_id", "client_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID] = mapped_column(nullable=False)
    matter_id: Mapped[UUID | None] = mapped_column()
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[ClientContractStatus] = mapped_column(
        Enum(ClientContractStatus, name="client_contract_status_enum"),
        default=ClientContractStatus.draft,
        nullable=False,
    )
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_until: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
