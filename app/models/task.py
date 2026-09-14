# ruff: noqa: E501, UP042
"""
Modelo: Task
Tarefas vinculadas a um caso, podendo ser humanas ou geradas por IA.
"""

import enum
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.append_only import register_append_only_model
from app.db.base import Base, FlexibleJSON, FlexibleUUID, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.legal_exception import LegalException
    from app.models.matter import Matter
    from app.models.user import User


class UTCDateTime(TypeDecorator[datetime]):
    """TIMESTAMPTZ no PostgreSQL e UTC preservado no SQLite local.

    O dialeto SQLite descarta silenciosamente o offset até quando recebe
    ``DateTime(timezone=True)``. Normalizar no bind e recolocar UTC no result
    mantém comparações de prazo e serialização estáveis entre runtimes.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("UTCDateTime exige datetime com timezone explícito.")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _enum_values(enum_type: type[enum.Enum]) -> list[str]:
    return [str(item.value) for item in enum_type]


class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    blocked = "blocked"


class TaskPriority(str, enum.Enum):
    alta = "alta"
    media = "media"
    baixa = "baixa"


class TaskReviewState(str, enum.Enum):
    not_required = "not_required"
    draft = "draft"
    pending = "pending"
    approved = "approved"
    changes_requested = "changes_requested"


class TaskParticipantRole(str, enum.Enum):
    owner = "owner"
    assignee = "assignee"
    reviewer = "reviewer"
    observer = "observer"


class TaskParticipantState(str, enum.Enum):
    assigned = "assigned"
    in_progress = "in_progress"
    completed = "completed"
    removed = "removed"


class TaskEventType(str, enum.Enum):
    created = "created"
    updated = "updated"
    participants_changed = "participants_changed"
    participant_state_changed = "participant_state_changed"
    started = "started"
    blocked = "blocked"
    unblocked = "unblocked"
    review_submitted = "review_submitted"
    review_approved = "review_approved"
    review_changes_requested = "review_changes_requested"
    completed = "completed"
    reopened = "reopened"
    cancelled = "cancelled"
    comment_added = "comment_added"
    checklist_changed = "checklist_changed"
    labels_changed = "labels_changed"
    dependency_changed = "dependency_changed"
    attachment_changed = "attachment_changed"
    organization_changed = "organization_changed"
    archived = "archived"
    unarchived = "unarchived"


class TaskContextType(str, enum.Enum):
    workspace = "workspace"
    client = "client"
    contract = "contract"
    matter = "matter"


class TaskOrigin(str, enum.Enum):
    legacy = "legacy"
    manual = "manual"
    administrative = "administrative"
    commercial = "commercial"
    publication = "publication"
    process = "process"
    hearing = "hearing"
    deadline = "deadline"
    automation = "automation"


class Task(UUIDMixin, TimestampMixin, Base):
    """
    Entidade Task — tarefa operacional vinculada a um caso.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("matter_id", "id", name="uq_tasks_matter_id_id"),
        UniqueConstraint("workspace_id", "id", name="uq_tasks_workspace_id"),
        ForeignKeyConstraint(
            ["matter_id", "parent_task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_tasks_parent_same_matter",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "parent_task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_tasks_parent_same_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_tasks_workspace_client",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "contract_id"],
            ["client_contracts.workspace_id", "client_contracts.id"],
            name="fk_tasks_workspace_client_contract",
            ondelete="SET NULL",
        ),
        CheckConstraint("version >= 1", name="ck_tasks_version_positive"),
        CheckConstraint("review_round >= 0", name="ck_tasks_review_round_nonnegative"),
        CheckConstraint(
            "review_due_at IS NULL OR "
            "(review_required = true AND due_at IS NOT NULL AND review_due_at <= due_at)",
            name="ck_tasks_review_deadline_coherent",
        ),
        CheckConstraint(
            "review_required = true OR (review_state = 'not_required' AND review_due_at IS NULL)",
            name="ck_tasks_review_disabled_coherent",
        ),
        CheckConstraint(
            "(context_type = 'workspace' AND matter_id IS NULL AND client_id IS NULL "
            "AND contract_id IS NULL) OR "
            "(context_type = 'client' AND matter_id IS NULL AND client_id IS NOT NULL "
            "AND contract_id IS NULL) OR "
            "(context_type = 'matter' AND matter_id IS NOT NULL AND client_id IS NULL "
            "AND contract_id IS NULL) OR "
            "(context_type = 'contract' AND matter_id IS NULL AND client_id IS NOT NULL "
            "AND contract_id IS NOT NULL)",
            name="ck_tasks_context_coherent",
        ),
        CheckConstraint(
            "origin NOT IN ('publication', 'process', 'hearing', 'deadline') "
            "OR context_type = 'matter'",
            name="ck_tasks_legal_origin_requires_matter",
        ),
        Index(
            "ux_tasks_matter_idempotency",
            "matter_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ux_tasks_workspace_idempotency",
            "workspace_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
        # Materialização de exceção (Central de Exceções §6/§3) como task:
        # no máximo 1 task por exceção, garantido pelo banco, não só pela
        # idempotência de aplicação em ExceptionCenterService.open_exception.
        Index(
            "ux_tasks_source_exception_id",
            "source_exception_id",
            unique=True,
            postgresql_where=text("source_exception_id IS NOT NULL"),
            sqlite_where=text("source_exception_id IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    context_type: Mapped[TaskContextType] = mapped_column(
        Enum(
            TaskContextType,
            name="task_context_type_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskContextType.matter,
        server_default=TaskContextType.matter.value,
        index=True,
    )
    origin: Mapped[TaskOrigin] = mapped_column(
        Enum(
            TaskOrigin,
            name="task_origin_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskOrigin.legacy,
        server_default=TaskOrigin.legacy.value,
        index=True,
    )
    matter_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    contract_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    origin_resource_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), nullable=True, index=True
    )
    origin_label: Mapped[str | None] = mapped_column(String(240), nullable=True)
    parent_task_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    assigned_to: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status_enum"),
        nullable=False,
        default=TaskStatus.pending,
        index=True,
    )
    priority: Mapped[TaskPriority | None] = mapped_column(
        Enum(TaskPriority, name="task_priority_enum"), nullable=True
    )
    due_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    legacy_due_text: Mapped[str | None] = mapped_column(String(240), nullable=True)
    review_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    review_state: Mapped[TaskReviewState] = mapped_column(
        Enum(
            TaskReviewState,
            name="task_review_state_enum",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskReviewState.not_required,
        server_default=TaskReviewState.not_required.value,
        index=True,
    )
    review_round: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancelled_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    archived_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Referência cruzada com a Central de Exceções (Plano Mestre §6/§3):
    # preenchida só por ExceptionCenterService.open_exception, quando a
    # exceção tem matter_id — nunca por criação manual de task. SET NULL
    # se a exceção some (hoje nada apaga LegalException; é defesa, não
    # caminho esperado).
    source_exception_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_exceptions.id", ondelete="SET NULL"), nullable=True
    )

    # ── Relacionamentos ──
    matter: Mapped[Optional["Matter"]] = relationship("Matter", back_populates="tasks")
    assignee: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[assigned_to], back_populates="tasks"
    )
    source_exception: Mapped[Optional["LegalException"]] = relationship(
        "LegalException", foreign_keys=[source_exception_id]
    )
    participants: Mapped[list["TaskParticipant"]] = relationship(
        "TaskParticipant",
        back_populates="task",
        primaryjoin="and_(Task.workspace_id == TaskParticipant.workspace_id, Task.id == TaskParticipant.task_id)",
        foreign_keys="[TaskParticipant.workspace_id, TaskParticipant.task_id]",
        cascade="all, delete-orphan",
        order_by="TaskParticipant.created_at.asc()",
    )
    review_rounds: Mapped[list["TaskReviewRound"]] = relationship(
        "TaskReviewRound",
        back_populates="task",
        primaryjoin="and_(Task.workspace_id == TaskReviewRound.workspace_id, Task.id == TaskReviewRound.task_id)",
        foreign_keys="[TaskReviewRound.workspace_id, TaskReviewRound.task_id]",
        cascade="all, delete-orphan",
        order_by="TaskReviewRound.round_number.asc()",
    )
    events: Mapped[list["TaskEvent"]] = relationship(
        "TaskEvent",
        back_populates="task",
        primaryjoin="and_(Task.workspace_id == TaskEvent.workspace_id, Task.id == TaskEvent.task_id)",
        foreign_keys="[TaskEvent.workspace_id, TaskEvent.task_id]",
        passive_deletes=True,
        order_by="TaskEvent.occurred_at.asc()",
    )

    def __repr__(self) -> str:
        return f"<Task id={self.id} title={self.title[:30]} status={self.status}>"


class TaskParticipant(UUIDMixin, TimestampMixin, Base):
    """Participação explícita; nunca concede acesso ao caso por si só."""

    __tablename__ = "task_participants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_participants_matter_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_participants_workspace_task_context",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_participants_workspace_matter",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["membership_id", "workspace_id", "user_id"],
            [
                "workspace_memberships.id",
                "workspace_memberships.workspace_id",
                "workspace_memberships.user_id",
            ],
            name="fk_task_participants_membership_binding",
            ondelete="RESTRICT",
        ),
        Index(
            "ux_task_participants_active_owner",
            "task_id",
            unique=True,
            postgresql_where=text("active = true AND role = 'owner'"),
            sqlite_where=text("active = 1 AND role = 'owner'"),
        ),
        Index(
            "ux_task_participants_active_reviewer",
            "task_id",
            unique=True,
            postgresql_where=text("active = true AND role = 'reviewer'"),
            sqlite_where=text("active = 1 AND role = 'reviewer'"),
        ),
        Index(
            "ux_task_participants_active_user",
            "task_id",
            "user_id",
            unique=True,
            postgresql_where=text("active = true"),
            sqlite_where=text("active = 1"),
        ),
        Index("ix_task_participants_workspace_task", "workspace_id", "task_id"),
        CheckConstraint(
            "required_for_completion = false OR role IN ('owner', 'assignee')",
            name="ck_task_participants_required_assignee",
        ),
        CheckConstraint(
            "(active = true AND state <> 'removed') OR (active = false AND state = 'removed')",
            name="ck_task_participants_active_state",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    membership_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    role: Mapped[TaskParticipantRole] = mapped_column(
        Enum(
            TaskParticipantRole,
            name="task_participant_role_enum",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        index=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    state: Mapped[TaskParticipantState] = mapped_column(
        Enum(
            TaskParticipantState,
            name="task_participant_state_enum",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskParticipantState.assigned,
        server_default=TaskParticipantState.assigned.value,
        index=True,
    )
    required_for_completion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    label_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    task: Mapped["Task"] = relationship(
        "Task",
        back_populates="participants",
        primaryjoin="and_(TaskParticipant.workspace_id == Task.workspace_id, TaskParticipant.task_id == Task.id)",
        foreign_keys="[TaskParticipant.workspace_id, TaskParticipant.task_id]",
    )


class TaskReviewRound(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_review_rounds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_review_rounds_matter_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_review_rounds_workspace_task_context",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_review_rounds_workspace_matter",
            ondelete="CASCADE",
        ),
        UniqueConstraint("task_id", "round_number", name="uq_task_review_round_number"),
        Index("ix_task_review_rounds_workspace_task", "workspace_id", "task_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[TaskReviewState] = mapped_column(
        Enum(
            TaskReviewState,
            name="task_review_round_state_enum",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TaskReviewState.pending,
    )
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=func.now()
    )
    submitted_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped["Task"] = relationship(
        "Task",
        back_populates="review_rounds",
        primaryjoin="and_(TaskReviewRound.workspace_id == Task.workspace_id, TaskReviewRound.task_id == Task.id)",
        foreign_keys="[TaskReviewRound.workspace_id, TaskReviewRound.task_id]",
    )


class TaskEvent(UUIDMixin, Base):
    """Histórico canônico e imutável da tarefa colaborativa."""

    __tablename__ = "task_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_events_matter_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_events_workspace_task_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_events_workspace_matter",
            ondelete="RESTRICT",
        ),
        Index("ix_task_events_workspace_task_occurred", "workspace_id", "task_id", "occurred_at"),
        Index("ix_task_events_correlation", "correlation_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_label_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    actor_role_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[TaskEventType] = mapped_column(
        Enum(
            TaskEventType,
            name="task_event_type_enum",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        index=True,
    )
    before_data: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    after_data: Mapped[dict | None] = mapped_column(FlexibleJSON(), nullable=True)
    changed_paths: Mapped[list] = mapped_column(FlexibleJSON(), nullable=False, default=list)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reason_source: Mapped[str] = mapped_column(String(16), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    session_type: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    administrative_session_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    support_grant_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True)
    resource_version_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource_version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=func.now(), index=True
    )

    task: Mapped["Task"] = relationship(
        "Task",
        back_populates="events",
        primaryjoin="and_(TaskEvent.workspace_id == Task.workspace_id, TaskEvent.task_id == Task.id)",
        foreign_keys="[TaskEvent.workspace_id, TaskEvent.task_id]",
    )


register_append_only_model(TaskEvent)


class TaskChecklist(UUIDMixin, TimestampMixin, Base):
    """Checklist estruturado da tarefa; não é subtarefa nem altera o status."""

    __tablename__ = "task_checklists"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_checklists_matter_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_checklists_workspace_task_context",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "matter_id"],
            ["matters.workspace_id", "matters.id"],
            name="fk_task_checklists_workspace_matter",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_task_checklists_workspace_id"),
        Index("ix_task_checklists_workspace_task", "workspace_id", "task_id"),
        CheckConstraint("version >= 1", name="ck_task_checklists_version_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    items: Mapped[list["TaskChecklistItem"]] = relationship(
        "TaskChecklistItem",
        back_populates="checklist",
        cascade="all, delete-orphan",
        order_by="TaskChecklistItem.position.asc(), TaskChecklistItem.created_at.asc()",
    )


class TaskChecklistItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_checklist_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "checklist_id"],
            ["task_checklists.workspace_id", "task_checklists.id"],
            name="fk_task_checklist_items_workspace_checklist",
            ondelete="CASCADE",
        ),
        Index("ix_task_checklist_items_workspace_checklist", "workspace_id", "checklist_id"),
        CheckConstraint("version >= 1", name="ck_task_checklist_items_version_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    checklist_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    checklist: Mapped["TaskChecklist"] = relationship("TaskChecklist", back_populates="items")


class TaskLabel(UUIDMixin, TimestampMixin, Base):
    """Etiqueta reutilizável no workspace; arquivar não remove o histórico das tarefas."""

    __tablename__ = "task_labels"
    __table_args__ = (
        UniqueConstraint("workspace_id", "normalized_name", name="uq_task_labels_workspace_name"),
        UniqueConstraint("workspace_id", "id", name="uq_task_labels_workspace_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class TaskLabelAssignment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_label_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_label_assignments_matter_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_label_assignments_workspace_task_context",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "label_id"],
            ["task_labels.workspace_id", "task_labels.id"],
            name="fk_task_label_assignments_workspace_label",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_id", "label_id", name="uq_task_label_assignment"),
        Index("ix_task_label_assignments_workspace_task", "workspace_id", "task_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    label_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped["TaskLabel"] = relationship("TaskLabel")


class TaskDependency(UUIDMixin, TimestampMixin, Base):
    """A predecessora deve terminar antes da sucessora; o serviço impede ciclos."""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "predecessor_task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_dependencies_predecessor",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "predecessor_task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_dependencies_workspace_predecessor_context",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["matter_id", "successor_task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_dependencies_successor",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "successor_task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_dependencies_workspace_successor_context",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "predecessor_task_id <> successor_task_id", name="ck_task_dependency_distinct"
        ),
        UniqueConstraint(
            "predecessor_task_id", "successor_task_id", name="uq_task_dependency_edge"
        ),
        Index("ix_task_dependencies_workspace_matter", "workspace_id", "matter_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    predecessor_task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    successor_task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class TaskAttachment(UUIDMixin, TimestampMixin, Base):
    """Vínculo com documento canônico do mesmo caso; não duplica binário."""

    __tablename__ = "task_attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["matter_id", "task_id"],
            ["tasks.matter_id", "tasks.id"],
            name="fk_task_attachments_matter_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_attachments_workspace_task_context",
            ondelete="CASCADE",
        ),
        UniqueConstraint("task_event_id", "document_id", name="uq_task_event_document"),
        Index("ix_task_attachments_workspace_task", "workspace_id", "task_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID | None] = mapped_column(FlexibleUUID(), nullable=True, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    task_event_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("task_events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    document_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name_snapshot: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class TaskMention(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_mentions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_mentions_workspace_task_context",
            ondelete="CASCADE",
        ),
        UniqueConstraint("task_event_id", "mentioned_user_id", name="uq_task_event_mention"),
        Index("ix_task_mentions_workspace_task", "workspace_id", "task_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matter_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("matters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_event_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("task_events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    mentioned_user_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    label_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    mentioned_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
