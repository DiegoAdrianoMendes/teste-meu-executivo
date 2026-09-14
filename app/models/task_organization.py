"""Organização visual das tarefas jurídicas, independente do estado operacional."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, FlexibleUUID, TimestampMixin, UUIDMixin
from app.models.task import UTCDateTime


class TaskList(UUIDMixin, TimestampMixin, Base):
    """Coleção nomeada. Participar de uma lista não altera status nem acesso ao caso."""

    __tablename__ = "task_lists"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_task_lists_workspace_id"),
        UniqueConstraint("workspace_id", "normalized_name", name="uq_task_lists_workspace_name"),
        CheckConstraint("version >= 1", name="ck_task_lists_version_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL")
    )


class TaskListMembership(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_list_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "list_id"],
            ["task_lists.workspace_id", "task_lists.id"],
            name="fk_task_list_memberships_workspace_list",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_list_memberships_workspace_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint("list_id", "task_id", name="uq_task_list_membership"),
        Index("ix_task_list_memberships_workspace_list", "workspace_id", "list_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    list_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL")
    )


class TaskBoard(UUIDMixin, TimestampMixin, Base):
    """Quadro jurídico versionado; suas colunas são organização, não Task.status."""

    __tablename__ = "task_boards"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_task_boards_workspace_id"),
        UniqueConstraint("workspace_id", "normalized_name", name="uq_task_boards_workspace_name"),
        CheckConstraint("version >= 1", name="ck_task_boards_version_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        FlexibleUUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    columns: Mapped[list[TaskBoardColumn]] = relationship(
        "TaskBoardColumn", cascade="all, delete-orphan", order_by="TaskBoardColumn.position.asc()"
    )


class TaskBoardColumn(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_board_columns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "board_id"],
            ["task_boards.workspace_id", "task_boards.id"],
            name="fk_task_board_columns_workspace_board",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_task_board_columns_workspace_id"),
        UniqueConstraint("board_id", "normalized_name", name="uq_task_board_columns_board_name"),
        UniqueConstraint("board_id", "position", name="uq_task_board_columns_board_position"),
        CheckConstraint("version >= 1", name="ck_task_board_columns_version_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    board_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)


class TaskBoardPlacement(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "task_board_placements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "board_id"],
            ["task_boards.workspace_id", "task_boards.id"],
            name="fk_task_board_placements_workspace_board",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "column_id"],
            ["task_board_columns.workspace_id", "task_board_columns.id"],
            name="fk_task_board_placements_workspace_column",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["tasks.workspace_id", "tasks.id"],
            name="fk_task_board_placements_workspace_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint("board_id", "task_id", name="uq_task_board_placement"),
        CheckConstraint("version >= 1", name="ck_task_board_placements_version_positive"),
        Index("ix_task_board_placements_workspace_column", "workspace_id", "column_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    board_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    column_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    task_id: Mapped[UUID] = mapped_column(FlexibleUUID(), nullable=False, index=True)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    moved_by_user_id: Mapped[UUID | None] = mapped_column(
        FlexibleUUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
