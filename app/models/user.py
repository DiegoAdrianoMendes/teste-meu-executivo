"""
Modelo: User / Agent
Representa tanto usuários humanos (advogados, estagiários) quanto agentes de IA.
"""
import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    and_,
    cast,
    func,
    or_,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base, FlexibleJSON, SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.meeting import Meeting
    from app.models.task import Task
    from app.models.timeline_event import TimelineEvent
    from app.models.workspace_membership import WorkspaceMembership


class AgentType(str, enum.Enum):
    human = "human"
    ai = "ai"


class UserRole(str, enum.Enum):
    senior_lawyer = "senior_lawyer"
    lawyer = "lawyer"
    intern = "intern"
    ai_analyst = "ai_analyst"
    ai_drafter = "ai_drafter"
    admin = "admin"


def normalize_email(value: str) -> str:
    """Return the single canonical representation accepted for identity emails."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("Email must not be empty.")
    if not normalized.isascii():
        raise ValueError("Email local-part and domain must contain only ASCII characters.")
    return normalized.lower()


class User(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    Entidade User/Agent — humanos e IAs compartilham a mesma tabela,
    diferenciados pelo campo `agent_type`.
    """
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    password_change_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    temporary_password_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    agent_type: Mapped[AgentType] = mapped_column(
        Enum(AgentType, name="agent_type_enum"), nullable=False, default=AgentType.human
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum"), nullable=False, default=UserRole.lawyer
    )
    initials: Mapped[str] = mapped_column(String(4), nullable=False)
    color: Mapped[str] = mapped_column(String(50), nullable=False, default="hsl(217 50% 22%)")
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    auth_epoch: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    is_internal_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    platform_role_package: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    platform_access_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    platform_permission_grants: Mapped[list] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=list,
    )
    platform_permission_denials: Mapped[list] = mapped_column(
        FlexibleJSON(),
        nullable=False,
        default=list,
    )

    # ── Relacionamentos ──
    responsible_matters: Mapped[List["Matter"]] = relationship(
        "Matter", foreign_keys="Matter.responsible_human_id", back_populates="responsible_human"
    )
    current_agent_matters: Mapped[List["Matter"]] = relationship(
        "Matter", foreign_keys="Matter.current_agent_id", back_populates="current_agent"
    )
    tasks: Mapped[List["Task"]] = relationship(
        "Task", foreign_keys="Task.assigned_to", back_populates="assignee"
    )
    timeline_events: Mapped[List["TimelineEvent"]] = relationship(
        "TimelineEvent", back_populates="agent"
    )
    memberships: Mapped[List["WorkspaceMembership"]] = relationship(
        "WorkspaceMembership",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    responsible_meetings: Mapped[List["Meeting"]] = relationship(
        "Meeting",
        foreign_keys="Meeting.responsible_user_id",
        back_populates="responsible_user",
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: Optional[str]) -> Optional[str]:
        return normalize_email(value) if value is not None else None

    def __repr__(self) -> str:
        return f"<User id={self.id} name={self.name} type={self.agent_type}>"


_user_email = User.__table__.c.email
User.__table__.append_constraint(
    CheckConstraint(
        or_(
            _user_email.is_(None),
            and_(
                _user_email == func.lower(_user_email),
                _user_email == func.trim(_user_email),
                func.length(_user_email) == func.length(cast(_user_email, LargeBinary)),
            ),
        ),
        name="ck_users_email_canonical_ascii_lowercase",
    )
)

User.__table__.append_constraint(
    CheckConstraint(
        or_(
            User.__table__.c.password_change_required.is_(False),
            and_(
                User.__table__.c.hashed_password.is_not(None),
                User.__table__.c.temporary_password_issued_at.is_not(None),
            ),
        ),
        name="ck_users_required_password_change_has_temporary_password",
    )
)


Index(
    "uq_users_email_lower",
    func.lower(User.__table__.c.email),
    unique=True,
    postgresql_where=User.__table__.c.email.is_not(None),
    sqlite_where=User.__table__.c.email.is_not(None),
)

_platform_owner_predicate = User.__table__.c.platform_role_package == "platform_owner"
Index(
    "uq_users_single_platform_owner",
    User.__table__.c.platform_role_package,
    unique=True,
    postgresql_where=_platform_owner_predicate,
    sqlite_where=_platform_owner_predicate,
)
