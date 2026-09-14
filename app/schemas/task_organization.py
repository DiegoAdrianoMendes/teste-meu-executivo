"""Contratos tipados da organização visual de Tarefas 360."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.task import TaskSummaryRead


class TaskOrganizationCreate(WriteSchema):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class TaskBoardColumnCreate(WriteSchema):
    expected_board_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)


class TaskListMembershipWrite(WriteSchema):
    task_id: UUID
    list_id: UUID
    expected_task_version: int = Field(ge=1)


class TaskBoardPlacementWrite(WriteSchema):
    task_id: UUID
    board_id: UUID
    column_id: UUID
    expected_task_version: int = Field(ge=1)
    expected_placement_version: int | None = Field(default=None, ge=1)


class TaskArchiveWrite(WriteSchema):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)


class TaskBoardColumnRead(CamelCaseSchema):
    id: UUID
    board_id: UUID
    name: str
    position: int
    version: int
    archived_at: datetime | None = None


class TaskBoardRead(CamelCaseSchema):
    id: UUID
    name: str
    description: str | None = None
    position: int
    version: int
    archived_at: datetime | None = None
    columns: list[TaskBoardColumnRead] = Field(default_factory=list)


class TaskListRead(CamelCaseSchema):
    id: UUID
    name: str
    description: str | None = None
    position: int
    version: int
    archived_at: datetime | None = None


class TaskListMembershipRead(CamelCaseSchema):
    id: UUID
    list_id: UUID
    task_id: UUID
    position: int


class TaskBoardPlacementRead(CamelCaseSchema):
    id: UUID
    board_id: UUID
    column_id: UUID
    task_id: UUID
    position: int
    version: int


class TaskOrganizationRead(CamelCaseSchema):
    lists: list[TaskListRead] = Field(default_factory=list)
    boards: list[TaskBoardRead] = Field(default_factory=list)
    list_memberships: list[TaskListMembershipRead] = Field(default_factory=list)
    placements: list[TaskBoardPlacementRead] = Field(default_factory=list)


class TaskBoardView(CamelCaseSchema):
    board: TaskBoardRead
    placements: list[TaskBoardPlacementRead] = Field(default_factory=list)
    tasks: list[TaskSummaryRead] = Field(default_factory=list)
