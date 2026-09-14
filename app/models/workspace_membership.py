"""
Modelo: WorkspaceMembership
Liga usuários a workspaces com papel e estado operacional.
"""
import enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleJSON, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class WorkspaceRole(str, enum.Enum):
    workspace_owner = "workspace_owner"
    workspace_admin = "workspace_admin"
    legal_manager = "legal_manager"
    finance_manager = "finance_manager"
    operator = "operator"
    viewer = "viewer"


class MembershipStatus(str, enum.Enum):
    active = "active"
    invited = "invited"
    suspended = "suspended"


class WorkspaceMembership(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_membership_workspace_user"),
        UniqueConstraint(
            "id",
            "workspace_id",
            "user_id",
            name="uq_workspace_membership_identity_binding",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, name="workspace_role_enum"),
        nullable=False,
        default=WorkspaceRole.operator,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status_enum"),
        nullable=False,
        default=MembershipStatus.active,
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    access_package: Mapped[str | None] = mapped_column(String(80), nullable=True)
    permission_grants: Mapped[list] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=list,
    )
    permission_denials: Mapped[list] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=list,
    )

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="memberships")
    user: Mapped["User"] = relationship("User", back_populates="memberships")

    def __repr__(self) -> str:
        return f"<WorkspaceMembership workspace_id={self.workspace_id} user_id={self.user_id} role={self.role}>"
