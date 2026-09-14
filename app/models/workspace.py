"""
Modelo: Workspace
Representa o tenant raiz do SaaS jurídico.
"""
import enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.billing import Subscription, TokenBudget
    from app.models.client import Client
    from app.models.finance import FinanceEntry, FinanceReminder, IntegrationConnection
    from app.models.meeting import Meeting
    from app.models.matter import Matter
    from app.models.whatsapp import WhatsAppConnection, WhatsAppContact
    from app.models.workspace_membership import WorkspaceMembership


class WorkspaceStatus(str, enum.Enum):
    provisioning = "provisioning"
    active = "active"
    suspended = "suspended"


class WorkspaceSetupMode(str, enum.Enum):
    self_service = "self_service"
    assisted = "assisted"


class Workspace(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    legal_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[WorkspaceStatus] = mapped_column(
        Enum(WorkspaceStatus, name="workspace_status_enum"),
        nullable=False,
        default=WorkspaceStatus.active,
    )
    setup_mode: Mapped[WorkspaceSetupMode] = mapped_column(
        Enum(WorkspaceSetupMode, name="workspace_setup_mode_enum"),
        nullable=False,
        default=WorkspaceSetupMode.self_service,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settings: Mapped[dict] = mapped_column(FlexibleJSON(), nullable=False, default=dict)
    # Canal do dono (Sprint S1b): telefone WhatsApp do dono do escritório para as
    # notificações de rotina do Hermes. Somente dígitos (formato E.164 sem "+").
    # A entrega real continua atrás dos gates externos + MetaPolicyGate.
    owner_whatsapp_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    memberships: Mapped[List["WorkspaceMembership"]] = relationship(
        "WorkspaceMembership",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    clients: Mapped[List["Client"]] = relationship("Client", back_populates="workspace")
    matters: Mapped[List["Matter"]] = relationship("Matter", back_populates="workspace")
    finance_entries: Mapped[List["FinanceEntry"]] = relationship(
        "FinanceEntry",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    finance_reminders: Mapped[List["FinanceReminder"]] = relationship(
        "FinanceReminder",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    integration_connections: Mapped[List["IntegrationConnection"]] = relationship(
        "IntegrationConnection",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    whatsapp_contacts: Mapped[List["WhatsAppContact"]] = relationship(
        "WhatsAppContact",
        back_populates="workspace",
    )
    whatsapp_connections: Mapped[List["WhatsAppConnection"]] = relationship(
        "WhatsAppConnection",
        back_populates="workspace",
    )
    meetings: Mapped[List["Meeting"]] = relationship(
        "Meeting",
        back_populates="workspace",
        cascade="all, delete-orphan",
    )

    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription",
        back_populates="workspace",
        uselist=False,
        cascade="all, delete-orphan",
    )
    token_budget: Mapped[Optional["TokenBudget"]] = relationship(
        "TokenBudget",
        back_populates="workspace",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Workspace id={self.id} slug={self.slug} status={self.status}>"
