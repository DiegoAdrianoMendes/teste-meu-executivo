"""Serviço canônico para listas, quadros, colunas e arquivamento de tarefas."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.klm_operations import KlmAuditEvent
from app.models.task import Task, TaskEventType
from app.models.task_organization import (
    TaskBoard,
    TaskBoardColumn,
    TaskBoardPlacement,
    TaskList,
    TaskListMembership,
)
from app.models.timeline_event import TimelineEventType
from app.models.user import User
from app.schemas.task_organization import TaskOrganizationCreate
from app.services.task_service import TaskMutationContext, TaskService


def _normalized_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", ascii_value.strip().lower())


class TaskOrganizationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _conflict(code: str, message: str) -> None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message}
        )

    async def _audit(
        self,
        *,
        workspace_id: UUID,
        context: TaskMutationContext,
        event_type: str,
        entity_type: str,
        entity_id: UUID,
        summary: str,
        before: dict | None,
        after: dict | None,
    ) -> None:
        actor = await self.db.get(User, context.actor_user_id) if context.actor_user_id else None
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=context.actor_user_id,
                actor_type=actor.agent_type.value if actor else "system",
                actor_label=actor.name if actor else "Sistema",
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                correlation_id=context.correlation_id,
                before_data=before,
                after_data=after,
            )
        )
        await self.db.flush()

    async def organization(
        self,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        allow_workspace: bool,
    ) -> tuple[list[TaskList], list[TaskBoard], list[TaskListMembership], list[TaskBoardPlacement]]:
        lists = list(
            (
                await self.db.scalars(
                    select(TaskList)
                    .where(TaskList.workspace_id == workspace_id, TaskList.archived_at.is_(None))
                    .order_by(TaskList.position, TaskList.name)
                )
            ).all()
        )
        boards = list(
            (
                await self.db.scalars(
                    select(TaskBoard)
                    .options(selectinload(TaskBoard.columns))
                    .where(TaskBoard.workspace_id == workspace_id, TaskBoard.archived_at.is_(None))
                    .order_by(TaskBoard.position, TaskBoard.name)
                )
            ).all()
        )
        visible = [Task.workspace_id == workspace_id, Task.archived_at.is_(None)]
        if not allow_workspace:
            visible.append(
                TaskService(self.db)._normal_access_predicate(
                    workspace_id=workspace_id, actor_user_id=actor_user_id
                )
            )
        visible_task_ids = select(Task.id).where(*visible)
        memberships = list(
            (
                await self.db.scalars(
                    select(TaskListMembership)
                    .where(
                        TaskListMembership.workspace_id == workspace_id,
                        TaskListMembership.task_id.in_(visible_task_ids),
                    )
                    .order_by(TaskListMembership.position, TaskListMembership.created_at)
                )
            ).all()
        )
        placements = list(
            (
                await self.db.scalars(
                    select(TaskBoardPlacement)
                    .where(
                        TaskBoardPlacement.workspace_id == workspace_id,
                        TaskBoardPlacement.task_id.in_(visible_task_ids),
                    )
                    .order_by(TaskBoardPlacement.position, TaskBoardPlacement.created_at)
                )
            ).all()
        )
        return lists, boards, memberships, placements

    async def create_list(
        self, workspace_id: UUID, data: TaskOrganizationCreate, context: TaskMutationContext
    ) -> TaskList:
        normalized = _normalized_name(data.name)
        if await self.db.scalar(
            select(TaskList.id).where(
                TaskList.workspace_id == workspace_id, TaskList.normalized_name == normalized
            )
        ):
            self._conflict("TASK_LIST_NAME_CONFLICT", "Já existe uma lista com este nome.")
        position = int(
            await self.db.scalar(
                select(func.count())
                .select_from(TaskList)
                .where(TaskList.workspace_id == workspace_id)
            )
            or 0
        )
        item = TaskList(
            workspace_id=workspace_id,
            name=data.name.strip(),
            normalized_name=normalized,
            description=data.description.strip() if data.description else None,
            position=position,
            created_by_user_id=context.actor_user_id,
        )
        self.db.add(item)
        await self.db.flush()
        after = {"name": item.name, "position": item.position, "version": item.version}
        await self._audit(
            workspace_id=workspace_id,
            context=context,
            event_type="task_list_created",
            entity_type="task_list",
            entity_id=item.id,
            summary="Lista de tarefas criada.",
            before=None,
            after=after,
        )
        return item

    async def create_board(
        self, workspace_id: UUID, data: TaskOrganizationCreate, context: TaskMutationContext
    ) -> TaskBoard:
        normalized = _normalized_name(data.name)
        if await self.db.scalar(
            select(TaskBoard.id).where(
                TaskBoard.workspace_id == workspace_id, TaskBoard.normalized_name == normalized
            )
        ):
            self._conflict("TASK_BOARD_NAME_CONFLICT", "Já existe um quadro com este nome.")
        position = int(
            await self.db.scalar(
                select(func.count())
                .select_from(TaskBoard)
                .where(TaskBoard.workspace_id == workspace_id)
            )
            or 0
        )
        board = TaskBoard(
            workspace_id=workspace_id,
            name=data.name.strip(),
            normalized_name=normalized,
            description=data.description.strip() if data.description else None,
            position=position,
            created_by_user_id=context.actor_user_id,
        )
        self.db.add(board)
        await self.db.flush()
        await self._audit(
            workspace_id=workspace_id,
            context=context,
            event_type="task_board_created",
            entity_type="task_board",
            entity_id=board.id,
            summary="Quadro de tarefas criado.",
            before=None,
            after={"name": board.name, "position": board.position, "version": board.version},
        )
        return board

    async def create_column(
        self,
        workspace_id: UUID,
        board_id: UUID,
        *,
        name: str,
        expected_board_version: int,
        context: TaskMutationContext,
    ) -> TaskBoardColumn:
        board = await self.db.scalar(
            select(TaskBoard)
            .where(TaskBoard.id == board_id, TaskBoard.workspace_id == workspace_id)
            .with_for_update()
        )
        if board is None or board.archived_at is not None:
            raise HTTPException(status_code=404, detail="Quadro não encontrado.")
        if board.version != expected_board_version:
            self._conflict(
                "TASK_BOARD_VERSION_CONFLICT", "O quadro foi alterado por outra operação."
            )
        normalized = _normalized_name(name)
        if await self.db.scalar(
            select(TaskBoardColumn.id).where(
                TaskBoardColumn.board_id == board_id,
                TaskBoardColumn.normalized_name == normalized,
            )
        ):
            self._conflict("TASK_COLUMN_NAME_CONFLICT", "Já existe uma coluna com este nome.")
        position = int(
            await self.db.scalar(
                select(func.count())
                .select_from(TaskBoardColumn)
                .where(TaskBoardColumn.board_id == board_id)
            )
            or 0
        )
        column = TaskBoardColumn(
            workspace_id=workspace_id,
            board_id=board_id,
            name=name.strip(),
            normalized_name=normalized,
            position=position,
        )
        self.db.add(column)
        board.version += 1
        await self.db.flush()
        await self._audit(
            workspace_id=workspace_id,
            context=context,
            event_type="task_board_column_created",
            entity_type="task_board_column",
            entity_id=column.id,
            summary="Coluna do quadro criada.",
            before=None,
            after={"boardId": str(board.id), "name": column.name, "position": position},
        )
        return column

    async def assign_list(
        self,
        workspace_id: UUID,
        *,
        task_id: UUID,
        list_id: UUID,
        expected_task_version: int,
        context: TaskMutationContext,
        allow_workspace: bool,
    ) -> TaskListMembership:
        tasks = TaskService(self.db)
        task = await tasks.get_for_actor(
            task_id,
            workspace_id,
            actor_user_id=context.actor_user_id,
            allow_workspace=allow_workspace,
            lock=True,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        tasks._require_version(task, expected_task_version)
        target = await self.db.scalar(
            select(TaskList).where(TaskList.id == list_id, TaskList.workspace_id == workspace_id)
        )
        if target is None or target.archived_at is not None:
            raise HTTPException(status_code=404, detail="Lista não encontrada.")
        existing = await self.db.scalar(
            select(TaskListMembership).where(
                TaskListMembership.list_id == list_id, TaskListMembership.task_id == task_id
            )
        )
        if existing is not None:
            return existing
        before = tasks._snapshot(task)
        membership = TaskListMembership(
            workspace_id=workspace_id,
            list_id=list_id,
            task_id=task_id,
            created_by_user_id=context.actor_user_id,
        )
        self.db.add(membership)
        tasks._bump(task)
        after = tasks._snapshot(task)
        after["listId"] = str(list_id)
        await tasks._record_event(
            task,
            context=context,
            event_type=TaskEventType.organization_changed,
            before=before,
            after=after,
            timeline_type=TimelineEventType.status_change,
            timeline_title="Tarefa adicionada a uma lista.",
        )
        return membership

    async def place_task(
        self,
        workspace_id: UUID,
        *,
        task_id: UUID,
        board_id: UUID,
        column_id: UUID,
        expected_task_version: int,
        expected_placement_version: int | None,
        context: TaskMutationContext,
        allow_workspace: bool,
    ) -> TaskBoardPlacement:
        tasks = TaskService(self.db)
        task = await tasks.get_for_actor(
            task_id,
            workspace_id,
            actor_user_id=context.actor_user_id,
            allow_workspace=allow_workspace,
            lock=True,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        tasks._require_version(task, expected_task_version)
        column = await self.db.scalar(
            select(TaskBoardColumn).where(
                TaskBoardColumn.id == column_id,
                TaskBoardColumn.board_id == board_id,
                TaskBoardColumn.workspace_id == workspace_id,
                TaskBoardColumn.archived_at.is_(None),
            )
        )
        if column is None:
            raise HTTPException(status_code=404, detail="Coluna não encontrada neste quadro.")
        placement = await self.db.scalar(
            select(TaskBoardPlacement)
            .where(TaskBoardPlacement.board_id == board_id, TaskBoardPlacement.task_id == task_id)
            .with_for_update()
        )
        before_status = task.status
        before = tasks._snapshot(task)
        previous_column = placement.column_id if placement else None
        if placement is None:
            if expected_placement_version is not None:
                self._conflict("TASK_PLACEMENT_MISSING", "A posição esperada não existe mais.")
            placement = TaskBoardPlacement(
                workspace_id=workspace_id,
                board_id=board_id,
                column_id=column_id,
                task_id=task_id,
                moved_by_user_id=context.actor_user_id,
            )
            self.db.add(placement)
        else:
            if (
                expected_placement_version is None
                or placement.version != expected_placement_version
            ):
                self._conflict(
                    "TASK_PLACEMENT_VERSION_CONFLICT", "A posição foi alterada por outra operação."
                )
            placement.column_id = column_id
            placement.moved_by_user_id = context.actor_user_id
            placement.version += 1
        tasks._bump(task)
        if task.status != before_status:  # pragma: no cover - defesa contra acoplamento futuro
            raise RuntimeError("Mover coluna não pode alterar o status da tarefa.")
        after = tasks._snapshot(task)
        after["boardPlacement"] = {
            "boardId": str(board_id),
            "columnId": str(column_id),
            "previousColumnId": str(previous_column) if previous_column else None,
        }
        await tasks._record_event(
            task,
            context=context,
            event_type=TaskEventType.organization_changed,
            before=before,
            after=after,
            timeline_type=TimelineEventType.status_change,
            timeline_title="Tarefa movida no quadro sem alterar seu estado.",
        )
        return placement

    async def set_archived(
        self,
        workspace_id: UUID,
        task_id: UUID,
        *,
        expected_version: int,
        reason: str,
        archived: bool,
        context: TaskMutationContext,
        allow_workspace: bool,
    ) -> Task:
        tasks = TaskService(self.db)
        task = await tasks.get_for_actor(
            task_id,
            workspace_id,
            actor_user_id=context.actor_user_id,
            allow_workspace=allow_workspace,
            lock=True,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        tasks._require_version(task, expected_version)
        if archived == (task.archived_at is not None):
            self._conflict("TASK_ARCHIVE_NOOP", "A tarefa já está neste estado de arquivamento.")
        before = tasks._snapshot(task)
        now = datetime.now(UTC)
        task.archived_at = now if archived else None
        task.archived_by_user_id = context.actor_user_id if archived else None
        task.archive_reason = reason.strip() if archived else None
        tasks._bump(task)
        after = tasks._snapshot(task)
        await tasks._record_event(
            task,
            context=context,
            event_type=TaskEventType.archived if archived else TaskEventType.unarchived,
            before=before,
            after=after,
            timeline_type=TimelineEventType.status_change,
            timeline_title="Tarefa arquivada." if archived else "Tarefa restaurada do arquivo.",
            reason=reason,
        )
        return task
