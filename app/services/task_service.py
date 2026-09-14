"""Domínio de tarefas colaborativas vinculadas a casos."""

import hashlib
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import logger
from app.models.client import Client
from app.models.document import Document
from app.models.entitlement import EntitlementKey
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_exception import LegalException
from app.models.matter import Matter
from app.models.omnichannel_v6 import WorkspaceTeam, WorkspaceTeamMember
from app.models.task import (
    Task,
    TaskAttachment,
    TaskChecklist,
    TaskChecklistItem,
    TaskContextType,
    TaskDependency,
    TaskEvent,
    TaskEventType,
    TaskLabel,
    TaskLabelAssignment,
    TaskMention,
    TaskOrigin,
    TaskParticipant,
    TaskParticipantRole,
    TaskParticipantState,
    TaskPriority,
    TaskReviewRound,
    TaskReviewState,
    TaskStatus,
)
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.user import AgentType, User
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.task import (
    TaskChecklistArchive,
    TaskChecklistCreate,
    TaskChecklistItemCreate,
    TaskChecklistItemStateUpdate,
    TaskCommand,
    TaskCommandRequest,
    TaskCommentCreate,
    TaskCreate,
    TaskDependencyCreate,
    TaskDependencyRemove,
    TaskLabelsReplace,
    TaskListScope,
    TaskParticipantsReplace,
    TaskParticipantStateUpdate,
    TaskParticipantWrite,
    TaskSubtaskCreate,
    TaskUpdate,
)
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.platform_audit_event_service import sanitize_audit_text


@dataclass(frozen=True)
class TaskListItem:
    task: Task
    matter_label: str | None
    client_label: str | None
    my_roles: list[TaskParticipantRole]


@dataclass(frozen=True)
class TaskMutationContext:
    actor_user_id: UUID | None
    correlation_id: str
    session_type: str = "normal"
    administrative_session_id: UUID | None = None
    support_grant_id: UUID | None = None
    is_platform_root: bool = False

    @classmethod
    def system(
        cls, actor_user_id: UUID | None = None, *, prefix: str = "task"
    ) -> "TaskMutationContext":
        return cls(
            actor_user_id=actor_user_id,
            correlation_id=f"{prefix}:{uuid4()}",
            session_type="system",
        )


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def build_model(**values) -> Task:
        """Construct the canonical task model through its domain authority.

        Context resolution may live in a small adapter, but construction stays
        centralized here so every producer remains subject to the same model
        invariants and the direct-construction architecture gate.
        """
        return Task(**values)

    async def _loaded_checklist(self, checklist_id: UUID) -> TaskChecklist:
        """Reload a checklist with items before crossing the async API boundary."""
        checklist = await self.db.scalar(
            select(TaskChecklist)
            .options(selectinload(TaskChecklist.items))
            .where(TaskChecklist.id == checklist_id)
            .execution_options(populate_existing=True)
        )
        if checklist is None:
            raise RuntimeError("Checklist persistido não foi encontrado na mesma transação.")
        return checklist

    async def list_checklists(
        self,
        task_id: UUID,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        allow_workspace: bool,
    ) -> list[TaskChecklist] | None:
        task = await self.get_for_actor(
            task_id,
            workspace_id,
            actor_user_id=actor_user_id,
            allow_workspace=allow_workspace,
        )
        if task is None:
            return None
        return list(
            (
                await self.db.scalars(
                    select(TaskChecklist)
                    .options(selectinload(TaskChecklist.items))
                    .where(
                        TaskChecklist.workspace_id == workspace_id,
                        TaskChecklist.task_id == task_id,
                        TaskChecklist.archived_at.is_(None),
                    )
                    .order_by(TaskChecklist.position.asc(), TaskChecklist.created_at.asc())
                )
            )
            .unique()
            .all()
        )

    async def create_checklist(
        self,
        task_id: UUID,
        data: TaskChecklistCreate,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> TaskChecklist | None:
        task = await self._get_for_mutation(task_id, workspace_id, context=context, lock=True)
        if task is None:
            return None
        self._require_version(task, data.expected_task_version)
        before = self._snapshot(task)
        position = int(
            await self.db.scalar(
                select(func.count(TaskChecklist.id)).where(
                    TaskChecklist.workspace_id == workspace_id,
                    TaskChecklist.task_id == task_id,
                    TaskChecklist.archived_at.is_(None),
                )
            )
            or 0
        )
        checklist = TaskChecklist(
            workspace_id=workspace_id,
            matter_id=task.matter_id,
            task_id=task.id,
            title=data.title.strip(),
            position=position,
            created_by_user_id=context.actor_user_id,
        )
        self.db.add(checklist)
        self._bump(task)
        await self.db.flush()
        after = self._snapshot(task)
        after["checklist"] = {"id": str(checklist.id), "title": checklist.title, "items": 0}
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.checklist_changed,
            before=before,
            after=after,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Checklist criado: {checklist.title[:60]}",
        )
        return await self._loaded_checklist(checklist.id)

    async def add_checklist_item(
        self,
        task_id: UUID,
        checklist_id: UUID,
        data: TaskChecklistItemCreate,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> TaskChecklist | None:
        task = await self._get_for_mutation(task_id, workspace_id, context=context, lock=True)
        if task is None:
            return None
        self._require_version(task, data.expected_task_version)
        checklist = await self.db.scalar(
            select(TaskChecklist)
            .where(
                TaskChecklist.id == checklist_id,
                TaskChecklist.workspace_id == workspace_id,
                TaskChecklist.task_id == task_id,
                TaskChecklist.archived_at.is_(None),
            )
            .with_for_update()
        )
        if checklist is None:
            return None
        before = self._snapshot(task)
        position = int(
            await self.db.scalar(
                select(func.count(TaskChecklistItem.id)).where(
                    TaskChecklistItem.workspace_id == workspace_id,
                    TaskChecklistItem.checklist_id == checklist_id,
                )
            )
            or 0
        )
        self.db.add(
            TaskChecklistItem(
                workspace_id=workspace_id,
                checklist_id=checklist.id,
                label=data.label.strip(),
                position=position,
            )
        )
        checklist.version += 1
        self._bump(task)
        await self.db.flush()
        after = self._snapshot(task)
        after["checklist"] = {
            "id": str(checklist.id),
            "title": checklist.title,
            "items": position + 1,
        }
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.checklist_changed,
            before=before,
            after=after,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Item adicionado ao checklist: {checklist.title[:60]}",
        )
        return await self._loaded_checklist(checklist.id)

    async def set_checklist_item_state(
        self,
        task_id: UUID,
        checklist_id: UUID,
        item_id: UUID,
        data: TaskChecklistItemStateUpdate,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> TaskChecklist | None:
        task = await self._get_for_mutation(task_id, workspace_id, context=context, lock=True)
        if task is None:
            return None
        self._require_version(task, data.expected_task_version)
        checklist = await self.db.scalar(
            select(TaskChecklist).where(
                TaskChecklist.id == checklist_id,
                TaskChecklist.workspace_id == workspace_id,
                TaskChecklist.task_id == task_id,
                TaskChecklist.archived_at.is_(None),
            ).with_for_update()
        )
        item = await self.db.scalar(
            select(TaskChecklistItem).where(
                TaskChecklistItem.id == item_id,
                TaskChecklistItem.workspace_id == workspace_id,
                TaskChecklistItem.checklist_id == checklist_id,
            ).with_for_update()
        )
        if checklist is None or item is None:
            return None
        if item.version != data.expected_item_version:
            self._conflict(
                "TASK_CHECKLIST_ITEM_VERSION_CONFLICT",
                "O item mudou. Recarregue antes de atualizar.",
            )
        before = self._snapshot(task)
        item.completed_at = datetime.now(UTC) if data.completed else None
        item.completed_by_user_id = context.actor_user_id if data.completed else None
        item.version += 1
        checklist.version += 1
        self._bump(task)
        await self.db.flush()
        after = self._snapshot(task)
        after["checklistItem"] = {"id": str(item.id), "completed": data.completed}
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.checklist_changed,
            before=before,
            after=after,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Checklist atualizado: {checklist.title[:60]}",
        )
        return await self._loaded_checklist(checklist.id)

    async def archive_checklist(
        self,
        task_id: UUID,
        checklist_id: UUID,
        data: TaskChecklistArchive,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> bool | None:
        task = await self._get_for_mutation(task_id, workspace_id, context=context, lock=True)
        if task is None:
            return None
        self._require_version(task, data.expected_task_version)
        checklist = await self.db.scalar(
            select(TaskChecklist).where(
                TaskChecklist.id == checklist_id,
                TaskChecklist.workspace_id == workspace_id,
                TaskChecklist.task_id == task_id,
                TaskChecklist.archived_at.is_(None),
            ).with_for_update()
        )
        if checklist is None:
            return None
        if checklist.version != data.expected_checklist_version:
            self._conflict(
                "TASK_CHECKLIST_VERSION_CONFLICT",
                "O checklist mudou. Recarregue antes de arquivar.",
            )
        before = self._snapshot(task)
        checklist.archived_at = datetime.now(UTC)
        checklist.version += 1
        self._bump(task)
        await self.db.flush()
        after = self._snapshot(task)
        after["archivedChecklist"] = {"id": str(checklist.id), "title": checklist.title}
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.checklist_changed,
            before=before,
            after=after,
            comment=data.reason,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Checklist arquivado: {checklist.title[:60]}",
        )
        return True

    async def replace_labels(
        self,
        task_id: UUID,
        data: TaskLabelsReplace,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> list[TaskLabel] | None:
        task = await self._get_for_mutation(task_id, workspace_id, context=context, lock=True)
        if task is None:
            return None
        self._require_version(task, data.expected_version)
        before = self._snapshot(task)
        current = list(
            await self.db.scalars(
                select(TaskLabelAssignment)
                .options(selectinload(TaskLabelAssignment.label))
                .where(
                    TaskLabelAssignment.workspace_id == workspace_id,
                    TaskLabelAssignment.task_id == task_id,
                )
            )
        )
        desired_names = {" ".join(item.name.split()).casefold() for item in data.labels}
        current_by_name = {
            assignment.label.normalized_name: assignment for assignment in current
        }
        for normalized, assignment in current_by_name.items():
            if normalized not in desired_names:
                await self.db.delete(assignment)
        labels: list[TaskLabel] = []
        for item in data.labels:
            name = " ".join(item.name.split())
            normalized = name.casefold()
            label = await self.db.scalar(
                select(TaskLabel).where(
                    TaskLabel.workspace_id == workspace_id,
                    TaskLabel.normalized_name == normalized,
                )
            )
            if label is None:
                label = TaskLabel(
                    workspace_id=workspace_id,
                    name=name,
                    normalized_name=normalized,
                    color=item.color.upper(),
                )
                self.db.add(label)
                await self.db.flush()
            elif label.archived_at is not None:
                label.archived_at = None
                label.name = name
                label.color = item.color.upper()
            labels.append(label)
            if normalized not in current_by_name:
                self.db.add(
                    TaskLabelAssignment(
                        workspace_id=workspace_id,
                        matter_id=task.matter_id,
                        task_id=task.id,
                        label_id=label.id,
                        created_by_user_id=context.actor_user_id,
                    )
                )
        self._bump(task)
        await self.db.flush()
        after = self._snapshot(task)
        after["labels"] = [
            {"id": str(label.id), "name": label.name, "color": label.color}
            for label in labels
        ]
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.labels_changed,
            before=before,
            after=after,
            comment=data.reason,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Etiquetas atualizadas: {task.title[:60]}",
        )
        return labels

    async def label_catalog(self, workspace_id: UUID) -> list[TaskLabel]:
        """Catálogo reutilizável de etiquetas ativas do workspace.

        A referência exposta pela API é opaca; o UUID continua restrito ao
        contrato interno do backend.
        """

        return list(
            await self.db.scalars(
                select(TaskLabel)
                .where(
                    TaskLabel.workspace_id == workspace_id,
                    TaskLabel.archived_at.is_(None),
                )
                .order_by(TaskLabel.name.asc(), TaskLabel.id.asc())
            )
        )

    async def create_subtask(
        self,
        parent_task_id: UUID,
        data: TaskSubtaskCreate,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> Task | None:
        parent = await self._get_for_mutation(
            parent_task_id, workspace_id, context=context, lock=True
        )
        if parent is None:
            return None
        self._require_version(parent, data.expected_parent_version)
        before = self._snapshot(parent)
        child_payload = data.model_dump(exclude={"expected_parent_version"})
        if parent.matter_id is not None:
            child = await self.create(
                parent.matter_id,
                TaskCreate.model_validate(child_payload),
                workspace_id,
                created_by=context.actor_user_id,
                context=context,
                parent_task_id=parent.id,
            )
        else:
            # Import local evita ciclo: TaskContextService usa TaskService para
            # as invariantes compartilhadas.
            from app.schemas.task_context import ContextualTaskCreate
            from app.services.task_context_service import TaskContextService

            context_payload: dict[str, object] = {"type": parent.context_type.value}
            if parent.client_id is not None:
                context_payload["clientId"] = str(parent.client_id)
            if parent.contract_id is not None:
                context_payload["contractId"] = str(parent.contract_id)
            contextual_data = ContextualTaskCreate.model_validate(
                {
                    **child_payload,
                    "context": context_payload,
                    "origin": parent.origin.value,
                    "originResourceId": (
                        str(parent.origin_resource_id) if parent.origin_resource_id else None
                    ),
                    "originLabel": parent.origin_label,
                }
            )
            child = await TaskContextService(self.db).create(
                contextual_data,
                workspace_id=workspace_id,
                context=context,
                parent_task_id=parent.id,
            )
        self._bump(parent)
        after = self._snapshot(parent)
        after["subtask"] = {"id": str(child.id), "title": child.title}
        await self._record_event(
            parent,
            context=context,
            event_type=TaskEventType.updated,
            before=before,
            after=after,
            timeline_type=TimelineEventType.task_created,
            timeline_title=f"Subtarefa criada: {child.title[:60]}",
        )
        return child

    async def add_dependency(
        self,
        successor_task_id: UUID,
        data: TaskDependencyCreate,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> TaskDependency | None:
        successor = await self._get_for_mutation(
            successor_task_id, workspace_id, context=context, lock=True
        )
        if successor is None:
            return None
        self._require_version(successor, data.expected_version)
        if data.predecessor_task_id is None:  # endpoint resolve referências opacas
            self._unprocessable(
                "TASK_DEPENDENCY_REFERENCE_REQUIRED",
                "A referência da tarefa predecessora não foi resolvida.",
            )
        predecessor = await self._get_for_mutation(
            data.predecessor_task_id,
            workspace_id,
            context=context,
            lock=True,
        )
        same_context = predecessor is not None and (
            predecessor.context_type == successor.context_type
            and predecessor.matter_id == successor.matter_id
            and predecessor.client_id == successor.client_id
            and predecessor.contract_id == successor.contract_id
        )
        if not same_context:
            self._unprocessable(
                "TASK_DEPENDENCY_SCOPE_INVALID",
                "A dependência deve apontar para uma tarefa do mesmo contexto.",
            )
        if predecessor.id == successor.id:
            self._unprocessable("TASK_DEPENDENCY_SELF", "Uma tarefa não depende de si mesma.")

        rows = list(
            await self.db.scalars(
                select(TaskDependency).where(
                    TaskDependency.workspace_id == workspace_id,
                )
            )
        )
        adjacency: dict[UUID, set[UUID]] = {}
        for row in rows:
            adjacency.setdefault(row.predecessor_task_id, set()).add(row.successor_task_id)
        stack = [successor.id]
        visited: set[UUID] = set()
        while stack:
            current = stack.pop()
            if current == predecessor.id:
                self._conflict(
                    "TASK_DEPENDENCY_CYCLE",
                    "A dependência criaria um ciclo entre tarefas.",
                )
            if current not in visited:
                visited.add(current)
                stack.extend(adjacency.get(current, ()))

        existing = next(
            (
                row
                for row in rows
                if row.predecessor_task_id == predecessor.id
                and row.successor_task_id == successor.id
            ),
            None,
        )
        if existing is not None:
            return existing
        before = self._snapshot(successor)
        dependency = TaskDependency(
            workspace_id=workspace_id,
            matter_id=successor.matter_id,
            predecessor_task_id=predecessor.id,
            successor_task_id=successor.id,
            created_by_user_id=context.actor_user_id,
        )
        self.db.add(dependency)
        self._bump(successor)
        await self.db.flush()
        after = self._snapshot(successor)
        after["dependency"] = {
            "id": str(dependency.id),
            "predecessorTaskId": str(predecessor.id),
        }
        await self._record_event(
            successor,
            context=context,
            event_type=TaskEventType.dependency_changed,
            before=before,
            after=after,
            comment=data.reason,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Dependência adicionada: {successor.title[:60]}",
        )
        return dependency

    async def dependency_candidates(
        self,
        task_id: UUID,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        allow_workspace: bool,
        search: str | None = None,
        limit: int = 50,
    ) -> list[Task] | None:
        """Lista somente predecessoras visíveis e compatíveis com o contexto.

        O endpoint consumidor converte os IDs em referências opacas. Assim, a
        interface tenant não precisa — nem consegue — consultar o escopo
        administrativo completo do workspace para montar o seletor.
        """

        current = await self.get_for_actor(
            task_id,
            workspace_id,
            actor_user_id=actor_user_id,
            allow_workspace=allow_workspace,
        )
        if current is None:
            return None
        context_filters = [
            Task.matter_id.is_(None)
            if current.matter_id is None
            else Task.matter_id == current.matter_id,
            Task.client_id.is_(None)
            if current.client_id is None
            else Task.client_id == current.client_id,
            Task.contract_id.is_(None)
            if current.contract_id is None
            else Task.contract_id == current.contract_id,
        ]
        statement = (
            select(Task)
            .options(*self._task_options())
            .where(
                Task.workspace_id == workspace_id,
                Task.id != current.id,
                Task.context_type == current.context_type,
                *context_filters,
                Task.archived_at.is_(None),
                Task.cancelled_at.is_(None),
            )
            .order_by(Task.title.asc(), Task.id.asc())
            .limit(limit)
        )
        if not allow_workspace:
            statement = statement.where(
                self._normal_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                )
            )
        term = (search or "").strip()
        if term:
            statement = statement.where(Task.title.ilike(f"%{term}%"))
        return list((await self.db.scalars(statement)).unique())

    async def remove_dependency(
        self,
        task_id: UUID,
        dependency_id: UUID,
        data: TaskDependencyRemove,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> bool | None:
        task = await self._get_for_mutation(task_id, workspace_id, context=context, lock=True)
        if task is None:
            return None
        self._require_version(task, data.expected_version)
        dependency = await self.db.scalar(
            select(TaskDependency)
            .where(
                TaskDependency.id == dependency_id,
                TaskDependency.workspace_id == workspace_id,
                TaskDependency.successor_task_id == task.id,
            )
            .with_for_update()
        )
        if dependency is None:
            return None
        before = self._snapshot(task)
        predecessor_id = dependency.predecessor_task_id
        await self.db.delete(dependency)
        self._bump(task)
        after = self._snapshot(task)
        after["removedDependency"] = {
            "id": str(dependency_id),
            "predecessorTaskId": str(predecessor_id),
        }
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.dependency_changed,
            before=before,
            after=after,
            comment=data.reason,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Dependência removida: {task.title[:60]}",
        )
        return True

    async def legal_resources(
        self,
        task_id: UUID,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        allow_workspace: bool,
    ) -> dict | None:
        task = await self.get_for_actor(
            task_id,
            workspace_id,
            actor_user_id=actor_user_id,
            allow_workspace=allow_workspace,
        )
        if task is None:
            return None
        assignments = list(
            await self.db.scalars(
                select(TaskLabelAssignment)
                .options(selectinload(TaskLabelAssignment.label))
                .where(
                    TaskLabelAssignment.workspace_id == workspace_id,
                    TaskLabelAssignment.task_id == task_id,
                )
            )
        )
        subtask_query = (
            select(Task)
            .options(*self._task_options())
            .where(
                Task.workspace_id == workspace_id,
                Task.parent_task_id == task_id,
            )
            .order_by(Task.created_at.asc())
        )
        if not allow_workspace:
            subtask_query = subtask_query.where(
                self._normal_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                )
            )
        subtasks = list((await self.db.scalars(subtask_query)).unique())
        dependency_query = select(TaskDependency).where(
            TaskDependency.workspace_id == workspace_id,
            or_(
                TaskDependency.predecessor_task_id == task_id,
                TaskDependency.successor_task_id == task_id,
            ),
        )
        if not allow_workspace:
            visible_task_ids = select(Task.id).where(
                Task.workspace_id == workspace_id,
                self._normal_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                ),
            )
            dependency_query = dependency_query.where(
                TaskDependency.predecessor_task_id.in_(visible_task_ids),
                TaskDependency.successor_task_id.in_(visible_task_ids),
            )
        dependencies = list(
            await self.db.scalars(
                dependency_query.order_by(TaskDependency.created_at.asc())
            )
        )
        attachments = list(
            await self.db.scalars(
                select(TaskAttachment)
                .where(
                    TaskAttachment.workspace_id == workspace_id,
                    TaskAttachment.task_id == task_id,
                )
                .order_by(TaskAttachment.created_at.asc())
            )
        )
        mentions = list(
            await self.db.scalars(
                select(TaskMention)
                .where(
                    TaskMention.workspace_id == workspace_id,
                    TaskMention.task_id == task_id,
                )
                .order_by(TaskMention.created_at.asc())
            )
        )
        return {
            "labels": [item.label for item in assignments if item.label.archived_at is None],
            "subtasks": subtasks,
            "dependencies": dependencies,
            "attachments": attachments,
            "mentions": mentions,
        }

    @staticmethod
    def _task_options():
        return (
            selectinload(Task.participants),
            selectinload(Task.review_rounds),
        )

    async def get_by_matter(self, matter_id: UUID, workspace_id: UUID) -> list[Task]:
        result = await self.db.execute(
            select(Task)
            .join(Task.matter)
            .options(*self._task_options())
            .where(
                Task.matter_id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
            .order_by(Task.created_at.asc())
        )
        return list(result.scalars().unique().all())

    async def get(self, task_id: UUID, workspace_id: UUID, *, lock: bool = False) -> Task | None:
        query = (
            select(Task)
            .options(*self._task_options())
            .where(
                Task.id == task_id,
                Task.workspace_id == workspace_id,
            )
        )
        if lock:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalars().unique().one_or_none()

    async def get_for_actor(
        self,
        task_id: UUID,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        allow_workspace: bool = False,
        lock: bool = False,
    ) -> Task | None:
        """Carrega uma tarefa sem transformar ``tasks:view`` em acesso global.

        Usuários tenant só alcançam tarefas próprias, criadas por eles ou
        pertencentes às suas equipes ativas. O acesso integral ao workspace é
        um sinal explícito fornecido exclusivamente por uma sessão
        administrativa já revalidada no request.
        """

        query = (
            select(Task)
            .options(*self._task_options())
            .where(
                Task.id == task_id,
                Task.workspace_id == workspace_id,
            )
        )
        if not allow_workspace:
            query = query.where(
                self._normal_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                )
            )
        if lock:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalars().unique().one_or_none()

    async def get_by_matter_for_actor(
        self,
        matter_id: UUID,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        allow_workspace: bool = False,
    ) -> list[Task]:
        statement = (
            select(Task)
            .join(Task.matter)
            .options(*self._task_options())
            .where(
                Task.matter_id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if not allow_workspace:
            statement = statement.where(
                self._normal_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                )
            )
        result = await self.db.execute(statement.order_by(Task.created_at.asc()))
        return list(result.scalars().unique().all())

    async def _get_for_mutation(
        self,
        task_id: UUID,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
        lock: bool,
    ) -> Task | None:
        if context.session_type == "system":
            return await self.get(task_id, workspace_id, lock=lock)
        if context.session_type == "normal":
            if context.actor_user_id is None:
                return None
            return await self.get_for_actor(
                task_id,
                workspace_id,
                actor_user_id=context.actor_user_id,
                lock=lock,
            )
        valid_administrative_context = (
            context.session_type in {"root_direct", "support_approved"}
            and context.actor_user_id is not None
            and context.administrative_session_id is not None
            and context.support_grant_id is not None
            and (
                context.session_type != "root_direct"
                or context.is_platform_root
            )
        )
        if not valid_administrative_context:
            return None
        return await self.get(task_id, workspace_id, lock=lock)

    async def list_workspace_tasks(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        scope: TaskListScope,
        limit: int,
        cursor: str | None = None,
        search: str | None = None,
        include_sensitive_identity: bool,
        task_status: TaskStatus | None = None,
        review_state: TaskReviewState | None = None,
        matter_id: UUID | None = None,
        client_id: UUID | None = None,
        owner_id: UUID | None = None,
        assignee_id: UUID | None = None,
        reviewer_id: UUID | None = None,
        label_id: UUID | None = None,
        priority: TaskPriority | None = None,
        due_from: datetime | None = None,
        due_to: datetime | None = None,
    ) -> tuple[list[TaskListItem], str | None, bool]:
        statement = (
            select(Task, Matter.demand_type, Client.name)
            .outerjoin(
                Matter,
                and_(
                    Matter.id == Task.matter_id,
                    Matter.workspace_id == Task.workspace_id,
                    Matter.deleted_at.is_(None),
                ),
            )
            .outerjoin(
                Client,
                and_(
                    Client.id == func.coalesce(Task.client_id, Matter.client_id),
                    Client.workspace_id == Task.workspace_id,
                    Client.deleted_at.is_(None),
                ),
            )
            .options(*self._task_options())
            .where(Task.workspace_id == workspace_id, Task.archived_at.is_(None))
        )
        statement = self._apply_scope(
            statement,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            scope=scope,
        )
        if task_status is not None:
            statement = statement.where(Task.status == task_status)
        if review_state is not None:
            statement = statement.where(Task.review_state == review_state)
        if matter_id is not None:
            statement = statement.where(Task.matter_id == matter_id)
        if client_id is not None:
            statement = statement.where(
                or_(Task.client_id == client_id, Matter.client_id == client_id)
            )
        if owner_id is not None:
            statement = statement.where(
                or_(
                    Task.assigned_to == owner_id,
                    self._participant_match(owner_id, TaskParticipantRole.owner),
                )
            )
        if assignee_id is not None:
            statement = statement.where(
                self._participant_match(assignee_id, TaskParticipantRole.assignee)
            )
        if reviewer_id is not None:
            statement = statement.where(
                self._participant_match(reviewer_id, TaskParticipantRole.reviewer)
            )
        if label_id is not None:
            statement = statement.where(
                select(TaskLabelAssignment.id)
                .where(
                    TaskLabelAssignment.workspace_id == workspace_id,
                    TaskLabelAssignment.task_id == Task.id,
                    TaskLabelAssignment.label_id == label_id,
                )
                .exists()
            )
        if priority is not None:
            statement = statement.where(Task.priority == priority)
        normalized_from = self._filter_datetime(due_from, label="dueFrom")
        normalized_to = self._filter_datetime(due_to, label="dueTo")
        if normalized_from is not None and normalized_to is not None:
            if normalized_from >= normalized_to:
                self._unprocessable(
                    "TASK_PERIOD_INVALID",
                    "O início do período deve ser anterior ao fim.",
                )
        if normalized_from is not None:
            statement = statement.where(Task.due_at >= normalized_from)
        if normalized_to is not None:
            # Limite superior exclusivo. A UI envia o início do dia seguinte
            # para que o campo humano “Até” continue inclusivo.
            statement = statement.where(Task.due_at < normalized_to)

        term = (search or "").strip()
        if term:
            pattern = f"%{term}%"
            searchable_content = or_(
                Task.title.ilike(pattern),
                Matter.demand_type.ilike(pattern),
            )
            if include_sensitive_identity:
                searchable_content = or_(searchable_content, Client.name.ilike(pattern))
                statement = statement.where(searchable_content)
            else:
                # Mesmo que o nome tenha sido repetido no título/demanda, a
                # existência da linha não pode confirmar a identidade oculta.
                statement = statement.where(
                    searchable_content,
                    or_(Client.id.is_(None), ~Client.name.ilike(pattern)),
                )
        if cursor:
            created_at, task_id = self._decode_cursor(cursor)
            statement = statement.where(self._cursor_predicate(created_at, task_id))
        # A ordenação faz parte do contrato do cursor: não remover o
        # desempate por id nem aceitar uma ordenação incompatível com ele.
        statement = statement.order_by(Task.created_at.desc(), Task.id.desc()).limit(limit + 1)
        rows = list((await self.db.execute(statement)).unique().all())
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [
            TaskListItem(
                task=task,
                matter_label=matter_label,
                client_label=client_label,
                my_roles=[
                    item.role
                    for item in task.participants
                    if item.active and item.user_id == actor_user_id
                ],
            )
            for task, matter_label, client_label in visible
        ]
        next_cursor = (
            self._encode_cursor(visible[-1][0].created_at, visible[-1][0].id)
            if has_more and visible
            else None
        )
        return items, next_cursor, has_more

    async def filter_matter_options(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        scope: TaskListScope,
        search: str | None,
        selected_ids: Sequence[UUID] = (),
        limit: int = 50,
        include_sensitive_identity: bool,
    ) -> list[tuple[UUID, str, str]]:
        """Casos presentes na coleção que o ator já pode consultar.

        O lookup não usa a coleção geral de casos: aplicar o mesmo escopo da
        listagem impede que um filtro revele casos fora da fila autorizada.
        """

        selected = tuple(dict.fromkeys(selected_ids))
        statement = (
            select(
                Matter.id,
                Matter.demand_type,
                Client.name,
                func.max(Matter.updated_at).label("last_activity"),
            )
            .join(Task, Task.matter_id == Matter.id)
            .join(Client, Client.id == Matter.client_id)
            .where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
            .group_by(Matter.id, Matter.demand_type, Client.name)
        )
        statement = self._apply_scope(
            statement,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            scope=scope,
        )
        term = (search or "").strip()
        if term:
            pattern = f"%{term}%"
            search_filter = Matter.demand_type.ilike(pattern)
            if include_sensitive_identity:
                search_filter = or_(search_filter, Client.name.ilike(pattern))
            else:
                search_filter = and_(search_filter, ~Client.name.ilike(pattern))
            if selected:
                search_filter = or_(search_filter, Matter.id.in_(selected))
            statement = statement.where(search_filter)
        selected_order = (
            case((Matter.id.in_(selected), 0), else_=1) if selected else literal(0)
        )
        rows = (
            await self.db.execute(
                statement.order_by(
                    selected_order,
                    func.max(Matter.updated_at).desc(),
                    Matter.id.desc(),
                ).limit(limit)
            )
        ).all()
        return [
            (matter_id, matter_label, client_label)
            for matter_id, matter_label, client_label, _ in rows
        ]

    async def filter_client_options(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        scope: TaskListScope,
        search: str | None,
        selected_ids: Sequence[UUID] = (),
        limit: int = 50,
        include_sensitive_identity: bool,
    ) -> list[tuple[UUID, str]]:
        """Clientes presentes nas tarefas visíveis, sem criar oráculo de PII."""

        selected = tuple(dict.fromkeys(selected_ids))
        term = (search or "").strip()
        if term and not include_sensitive_identity and not selected:
            return []
        statement = (
            select(
                Client.id,
                Client.name,
                func.max(Matter.updated_at).label("last_activity"),
            )
            .join(Matter, Matter.client_id == Client.id)
            .join(Task, Task.matter_id == Matter.id)
            .where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
            .group_by(Client.id, Client.name)
        )
        statement = self._apply_scope(
            statement,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            scope=scope,
        )
        if term:
            # Sem acesso sensível, apenas hidrata escolhas já autorizadas; o
            # texto digitado nunca é comparado com o nome real.
            search_filter = (
                Client.name.ilike(f"%{term}%")
                if include_sensitive_identity
                else Client.id.in_(selected)
            )
            if selected and include_sensitive_identity:
                search_filter = or_(search_filter, Client.id.in_(selected))
            statement = statement.where(search_filter)
        selected_order = (
            case((Client.id.in_(selected), 0), else_=1) if selected else literal(0)
        )
        rows = (
            await self.db.execute(
                statement.order_by(
                    selected_order,
                    func.max(Matter.updated_at).desc(),
                    Client.id.desc(),
                ).limit(limit)
            )
        ).all()
        return [(client_id, client_label) for client_id, client_label, _ in rows]

    def _actor_team_ids(self, *, workspace_id: UUID, actor_user_id: UUID):
        return (
            select(WorkspaceTeamMember.team_id)
            .join(
                WorkspaceTeam,
                and_(
                    WorkspaceTeam.id == WorkspaceTeamMember.team_id,
                    WorkspaceTeam.workspace_id == WorkspaceTeamMember.workspace_id,
                ),
            )
            .join(
                WorkspaceMembership,
                and_(
                    WorkspaceMembership.id == WorkspaceTeamMember.membership_id,
                    WorkspaceMembership.workspace_id == WorkspaceTeamMember.workspace_id,
                    WorkspaceMembership.user_id == WorkspaceTeamMember.user_id,
                ),
            )
            .where(
                WorkspaceTeamMember.workspace_id == workspace_id,
                WorkspaceTeamMember.user_id == actor_user_id,
                WorkspaceTeam.is_active.is_(True),
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )

    def _team_user_ids(self, *, workspace_id: UUID, actor_user_id: UUID):
        actor_team_ids = self._actor_team_ids(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )
        return (
            select(WorkspaceTeamMember.user_id)
            .join(
                WorkspaceTeam,
                and_(
                    WorkspaceTeam.id == WorkspaceTeamMember.team_id,
                    WorkspaceTeam.workspace_id == WorkspaceTeamMember.workspace_id,
                ),
            )
            .join(
                WorkspaceMembership,
                and_(
                    WorkspaceMembership.id == WorkspaceTeamMember.membership_id,
                    WorkspaceMembership.workspace_id == WorkspaceTeamMember.workspace_id,
                    WorkspaceMembership.user_id == WorkspaceTeamMember.user_id,
                ),
            )
            .join(User, User.id == WorkspaceTeamMember.user_id)
            .where(
                WorkspaceTeamMember.workspace_id == workspace_id,
                WorkspaceTeamMember.team_id.in_(actor_team_ids),
                WorkspaceTeam.is_active.is_(True),
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )

    @staticmethod
    def _participant_access(user_ids):
        return Task.participants.any(
            and_(
                TaskParticipant.user_id.in_(user_ids),
                TaskParticipant.active.is_(True),
            )
        )

    def _team_access_predicate(self, *, workspace_id: UUID, actor_user_id: UUID):
        team_user_ids = self._team_user_ids(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )
        return or_(
            Task.assigned_to.in_(team_user_ids),
            self._participant_access(team_user_ids),
        )

    def _normal_access_predicate(self, *, workspace_id: UUID, actor_user_id: UUID):
        """Coleção máxima normal: própria, criada pelo ator ou da equipe ativa."""

        return or_(
            Task.assigned_to == actor_user_id,
            Task.created_by_user_id == actor_user_id,
            Task.participants.any(
                and_(
                    TaskParticipant.user_id == actor_user_id,
                    TaskParticipant.active.is_(True),
                )
            ),
            self._team_access_predicate(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
            ),
        )

    def _apply_scope(
        self,
        statement,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        scope: TaskListScope,
    ):
        active_participant = Task.participants.any(
            and_(
                TaskParticipant.user_id == actor_user_id,
                TaskParticipant.active.is_(True),
            )
        )
        active_reviewer = Task.participants.any(
            and_(
                TaskParticipant.user_id == actor_user_id,
                TaskParticipant.role == TaskParticipantRole.reviewer,
                TaskParticipant.active.is_(True),
            )
        )
        if scope == TaskListScope.mine:
            statement = statement.where(or_(Task.assigned_to == actor_user_id, active_participant))
        elif scope == TaskListScope.assigned_by_me:
            statement = statement.where(Task.created_by_user_id == actor_user_id)
        elif scope == TaskListScope.reviews:
            statement = statement.where(active_reviewer)
        elif scope == TaskListScope.overdue:
            statement = statement.where(
                self._normal_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                ),
                Task.due_at < datetime.now(UTC),
                Task.status != TaskStatus.done,
                Task.cancelled_at.is_(None),
            )
        elif scope == TaskListScope.blocked:
            statement = statement.where(
                self._normal_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                ),
                Task.status == TaskStatus.blocked,
            )
        elif scope == TaskListScope.completed:
            statement = statement.where(
                self._normal_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                ),
                Task.status == TaskStatus.done,
                Task.cancelled_at.is_(None),
            )
        elif scope == TaskListScope.team:
            statement = statement.where(
                self._team_access_predicate(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                )
            )
        elif scope == TaskListScope.workspace:
            # A API só permite este escopo depois de validar uma sessão
            # administrativa ativa, vinculada ao workspace e com tasks:read.
            # Não há predicado por participante porque a finalidade é prestar
            # suporte dentro dessa sessão explicitamente identificada.
            pass
        return statement

    @staticmethod
    def _participant_match(user_id: UUID, role: TaskParticipantRole):
        return Task.participants.any(
            and_(
                TaskParticipant.user_id == user_id,
                TaskParticipant.role == role,
                TaskParticipant.active.is_(True),
            )
        )

    @staticmethod
    def _filter_datetime(value: datetime | None, *, label: str) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            TaskService._unprocessable(
                "TASK_TIMEZONE_REQUIRED",
                f"{label} deve incluir timezone explícito.",
            )
        return value.astimezone(UTC)

    @staticmethod
    def _filter_reference_cipher() -> Fernet:
        material = f"{settings.SECRET_KEY}|task-filter-reference|v1".encode()
        return Fernet(urlsafe_b64encode(hashlib.sha256(material).digest()))

    @classmethod
    def encode_filter_reference(
        cls,
        *,
        kind: str,
        resource_id: UUID,
        workspace_id: UUID,
    ) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "kind": kind,
                "resourceId": str(resource_id),
                "workspaceId": str(workspace_id),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return cls._filter_reference_cipher().encrypt(payload.encode("utf-8")).decode("ascii")

    @classmethod
    def decode_filter_reference(
        cls,
        reference: str,
        *,
        expected_kind: str,
        workspace_id: UUID,
    ) -> UUID:
        try:
            raw = cls._filter_reference_cipher().decrypt(reference.encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
            if (
                payload.get("v") != 1
                or payload.get("kind") != expected_kind
                or UUID(str(payload.get("workspaceId"))) != workspace_id
            ):
                raise ValueError("context mismatch")
            return UUID(str(payload["resourceId"]))
        except (
            InvalidToken,
            UnicodeDecodeError,
            UnicodeEncodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "TASK_FILTER_REFERENCE_INVALID",
                    "message": "Referência de filtro inválida ou expirada.",
                },
            ) from None

    def _cursor_predicate(self, created_at: datetime, task_id: UUID):
        if self.db.get_bind().dialect.name == "sqlite":
            # SQLite grava CURRENT_TIMESTAMP sem fração, enquanto o bind de
            # DateTime usa ``.000000``. Comparar os textos crus faria a própria
            # linha do cursor reaparecer. Normalizar ambos preserva o contrato
            # (created_at DESC, id DESC) usado em PostgreSQL.
            timestamp_column = func.strftime("%Y-%m-%dT%H:%M:%f", Task.created_at)
            timestamp_value = created_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            return or_(
                timestamp_column < timestamp_value,
                and_(timestamp_column == timestamp_value, Task.id < task_id),
            )
        return or_(
            Task.created_at < created_at,
            and_(Task.created_at == created_at, Task.id < task_id),
        )

    def _member_scope_filter(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        scope: TaskListScope,
    ):
        if scope == TaskListScope.workspace:
            return None
        if scope == TaskListScope.team:
            return User.id.in_(
                self._team_user_ids(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                )
            )

        visible_task_ids = (
            select(Task.id)
            .where(Task.workspace_id == workspace_id)
        )
        visible_task_ids = self._apply_scope(
            visible_task_ids,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            scope=scope,
        )
        participant_user_ids = select(TaskParticipant.user_id).where(
            TaskParticipant.task_id.in_(visible_task_ids),
            TaskParticipant.active.is_(True),
        )
        owner_user_ids = select(Task.assigned_to).where(
            Task.id.in_(visible_task_ids),
            Task.assigned_to.is_not(None),
        )
        creator_user_ids = select(Task.created_by_user_id).where(
            Task.id.in_(visible_task_ids),
            Task.created_by_user_id.is_not(None),
        )
        return or_(
            User.id.in_(participant_user_ids),
            User.id.in_(owner_user_ids),
            User.id.in_(creator_user_ids),
        )

    async def member_options(
        self,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        scope: TaskListScope,
        search: str | None = None,
        limit: int = 20,
    ) -> list[tuple[UUID, str, str]]:
        statement = (
            select(User.id, User.name, WorkspaceMembership.role)
            .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(User.name.asc(), User.id.asc())
            .limit(limit)
        )
        scope_filter = self._member_scope_filter(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            scope=scope,
        )
        if scope_filter is not None:
            statement = statement.where(scope_filter)
        term = (search or "").strip()
        if term:
            statement = statement.where(User.name.ilike(f"%{term}%"))
        return [
            (user_id, label, role.value)
            for user_id, label, role in (await self.db.execute(statement)).all()
        ]

    async def member_options_page(
        self,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        scope: TaskListScope,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
        selected_ids: Sequence[UUID] = (),
    ) -> tuple[list[tuple[UUID, str, str]], int]:
        filters = [
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.status == MembershipStatus.active,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        ]
        scope_filter = self._member_scope_filter(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            scope=scope,
        )
        if scope_filter is not None:
            filters.append(scope_filter)
        selected = tuple(dict.fromkeys(selected_ids))
        term = (search or "").strip()
        if term:
            search_filter = User.name.ilike(f"%{term}%")
            if selected:
                search_filter = or_(search_filter, User.id.in_(selected))
            filters.append(search_filter)
        total = int(
            await self.db.scalar(
                select(func.count(WorkspaceMembership.id))
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(*filters)
            )
            or 0
        )
        rows = (
            await self.db.execute(
                select(User.id, User.name, WorkspaceMembership.role)
                .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
                .where(*filters)
                .order_by(
                    (
                        case((User.id.in_(selected), 0), else_=1)
                        if selected
                        else literal(0)
                    ),
                    User.name.asc(),
                    User.id.asc(),
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [(user_id, label, role.value) for user_id, label, role in rows], total

    async def matter_options(
        self,
        workspace_id: UUID,
        *,
        search: str | None = None,
        limit: int = 20,
        include_sensitive_identity: bool,
    ) -> list[tuple[UUID, str, str]]:
        statement = (
            select(Matter.id, Matter.demand_type, Client.name)
            .join(Client, Client.id == Matter.client_id)
            .where(
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
            .order_by(Matter.updated_at.desc(), Matter.id.desc())
            .limit(limit)
        )
        term = (search or "").strip()
        if term:
            pattern = f"%{term}%"
            if include_sensitive_identity:
                statement = statement.where(
                    or_(Matter.demand_type.ilike(pattern), Client.name.ilike(pattern))
                )
            else:
                statement = statement.where(
                    Matter.demand_type.ilike(pattern),
                    ~Client.name.ilike(pattern),
                )
        return list((await self.db.execute(statement)).all())

    @staticmethod
    def _encode_cursor(created_at: datetime, task_id: UUID) -> str:
        raw = json.dumps(
            {"createdAt": created_at.isoformat(), "id": str(task_id)},
            separators=(",", ":"),
        ).encode()
        return urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(urlsafe_b64decode(padded.encode()).decode())
            created_at = datetime.fromisoformat(value["createdAt"])
            task_id = UUID(value["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "TASK_CURSOR_INVALID", "message": "Cursor inválido."},
            ) from None
        return created_at, task_id

    async def create(
        self,
        matter_id: UUID,
        data: TaskCreate,
        workspace_id: UUID,
        created_by: UUID | None = None,
        *,
        context: TaskMutationContext | None = None,
        source_exception_id: UUID | None = None,
        parent_task_id: UUID | None = None,
    ) -> Task:
        context = context or TaskMutationContext.system(created_by, prefix="task-create")
        matter = await self.db.get(Matter, matter_id)
        if not matter or matter.deleted_at is not None or matter.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado."
            )

        default_owner_id = (
            matter.responsible_human_id if context.is_platform_root else context.actor_user_id
        )
        participant_specs = self._create_participant_specs(data, default_owner_id)
        self._validate_participant_specs(participant_specs)
        member_bindings = await self._require_active_members(
            workspace_id, {item.user_id for item in participant_specs}
        )
        owner_id = next(
            item.user_id for item in participant_specs if item.role == TaskParticipantRole.owner
        )

        idempotency_key = (data.idempotency_key or "").strip() or None
        request_sha256 = (
            self._request_hash(
                data,
                source_exception_id=source_exception_id,
                parent_task_id=parent_task_id,
            )
            if idempotency_key
            else None
        )
        if idempotency_key:
            existing = await self._get_by_idempotency(matter_id, idempotency_key)
            if existing:
                self._validate_idempotent_replay(existing, request_sha256)
                return existing

        due_at, due_date, legacy_due_text = self._normalized_due(data.due_at, data.due_date)
        review_due_at = self._deadline_utc(
            data.review_due_at,
            label="reviewDueAt",
        )
        has_reviewer = any(item.role == TaskParticipantRole.reviewer for item in participant_specs)
        review_required = bool(data.review_required or has_reviewer)
        if review_required and not has_reviewer:
            self._unprocessable(
                "TASK_REVIEWER_REQUIRED", "Uma tarefa com revisão exige um reviewer ativo."
            )
        self._validate_deadlines(
            due_at=due_at,
            review_due_at=review_due_at,
            participant_specs=participant_specs,
            review_required=review_required,
        )

        enforcement = EntitlementEnforcementService(self.db)
        # O lock do workspace serializa todos os produtores que aumentam
        # estoque. Rechecamos a idempotência depois do lock para que um replay
        # concorrente não seja recusado pela quota consumida pela primeira
        # requisição.
        await enforcement.lock_workspace(workspace_id)
        if idempotency_key:
            existing = await self._get_by_idempotency(matter_id, idempotency_key)
            if existing:
                self._validate_idempotent_replay(existing, request_sha256)
                return existing
        await self._enforce_active_task_capacity(
            workspace_id=workspace_id,
            enforcement=enforcement,
        )
        await self._enforce_task_shape_capacity(
            workspace_id=workspace_id,
            participant_specs=participant_specs,
            enforcement=enforcement,
        )

        task = Task(
            workspace_id=workspace_id,
            context_type=TaskContextType.matter,
            origin=TaskOrigin.legacy,
            matter_id=matter_id,
            parent_task_id=parent_task_id,
            assigned_to=owner_id,
            title=data.title,
            description=data.description,
            priority=data.priority,
            due_date=due_date,
            due_at=due_at,
            legacy_due_text=legacy_due_text,
            review_due_at=review_due_at,
            review_required=review_required,
            review_state=TaskReviewState.draft if review_required else TaskReviewState.not_required,
            created_by_user_id=context.actor_user_id,
            is_ai_generated=data.is_ai_generated,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            source_exception_id=source_exception_id,
        )
        for spec in participant_specs:
            task.participants.append(
                TaskParticipant(
                    workspace_id=workspace_id,
                    matter_id=matter_id,
                    membership_id=member_bindings[spec.user_id][0],
                    user_id=spec.user_id,
                    role=spec.role,
                    due_at=self._deadline_utc(
                        spec.due_at,
                        label="participant.dueAt",
                    ),
                    state=TaskParticipantState.assigned,
                    required_for_completion=spec.required_for_completion,
                    label_snapshot=member_bindings[spec.user_id][1],
                    created_by_user_id=context.actor_user_id,
                    active=True,
                )
            )

        try:
            async with self.db.begin_nested():
                self.db.add(task)
                await self.db.flush()
        except IntegrityError as error:
            if not idempotency_key:
                raise
            existing = await self._get_by_idempotency(matter_id, idempotency_key)
            if not existing:
                raise error
            self._validate_idempotent_replay(existing, request_sha256)
            return existing

        await self.refresh_open_tasks_count(matter_id)
        after = self._snapshot(task)
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.created,
            before=None,
            after=after,
            timeline_type=TimelineEventType.task_created,
            timeline_title=f"Tarefa criada: {data.title[:60]}",
        )
        logger.info("task_created", task_id=str(task.id), matter_id=str(matter_id))
        # Recarrega as coleções vazias (review_rounds) para serialização async
        # sem lazy-load fora do greenlet do SQLAlchemy.
        return await self.get(task.id, workspace_id) or task

    async def create_automatic(
        self,
        matter_id: UUID,
        data: TaskCreate,
        workspace_id: UUID,
        *,
        source: str,
        actor_user_id: UUID | None = None,
        preferred_owner_id: UUID | None = None,
        source_exception_id: UUID | None = None,
        correlation_id: str | None = None,
    ) -> Task:
        """Cria tarefa de automacao sem assumir a fronteira de ``commit``.

        Produtores internos nao podem instanciar ``Task``: este comando aplica
        os mesmos invariantes, versionamento e eventos append-only da criacao
        interativa. Quando o produtor nao informa owner, a selecao permanece
        tenant-scoped e considera somente membros humanos ativos.
        """

        source_label = source.strip()
        if not source_label:
            self._unprocessable("TASK_SOURCE_REQUIRED", "Informe a origem da automacao.")

        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Caso nao encontrado."
            )

        if source_exception_id is not None:
            matching_exception_id = await self.db.scalar(
                select(LegalException.id).where(
                    LegalException.id == source_exception_id,
                    LegalException.workspace_id == workspace_id,
                    LegalException.matter_id == matter_id,
                )
            )
            if matching_exception_id is None:
                self._unprocessable(
                    "TASK_SOURCE_EXCEPTION_MISMATCH",
                    "A excecao de origem deve pertencer ao mesmo caso e workspace.",
                )

        has_explicit_owner = (
            data.participants is not None
            or data.owner_id is not None
            or data.assigned_to is not None
        )
        if not has_explicit_owner:
            owner_id = await self._resolve_automatic_owner(
                workspace_id=workspace_id,
                matter=matter,
                preferred_owner_id=preferred_owner_id,
                actor_user_id=actor_user_id,
            )
            data = data.model_copy(update={"owner_id": owner_id})

        resolved_correlation_id = (
            correlation_id or f"{source_label}:{data.idempotency_key or uuid4()}"
        ).strip()[:120]
        if not resolved_correlation_id:
            self._unprocessable("TASK_CORRELATION_REQUIRED", "A automacao exige correlation_id.")

        return await self.create(
            matter_id,
            data,
            workspace_id,
            created_by=actor_user_id,
            context=TaskMutationContext(
                actor_user_id=actor_user_id,
                correlation_id=resolved_correlation_id,
                session_type="system",
            ),
            source_exception_id=source_exception_id,
        )

    async def update(
        self,
        task_id: UUID,
        data: TaskUpdate,
        workspace_id: UUID,
        *,
        actor_user_id: UUID | None = None,
        context: TaskMutationContext | None = None,
    ) -> Task | None:
        context = context or TaskMutationContext.system(actor_user_id, prefix="task-update")
        task = await self._get_for_mutation(
            task_id,
            workspace_id,
            context=context,
            lock=True,
        )
        if not task:
            return None
        self._require_version(task, data.expected_version)
        self._require_not_terminal(task)
        before = self._snapshot(task)
        fields = data.model_fields_set
        mutation_fields = fields - {"expected_version"}
        if task.review_state == TaskReviewState.pending and mutation_fields:
            self._conflict(
                "TASK_REVIEW_PENDING",
                "A tarefa não pode ser editada durante uma revisão pendente.",
            )

        candidate_due_at = self._deadline_utc(
            task.due_at,
            label="dueAt",
            persisted_naive=True,
        )
        candidate_due_date = task.due_date
        candidate_legacy_due_text = task.legacy_due_text
        if "due_at" in fields or "due_date" in fields:
            due_at_input = data.due_at if "due_at" in fields else None
            due_date_input = data.due_date if "due_date" in fields else None
            (
                candidate_due_at,
                candidate_due_date,
                candidate_legacy_due_text,
            ) = self._normalized_due(due_at_input, due_date_input)

        reviewer = self._active_reviewer(task)
        candidate_review_required = (
            bool(data.review_required) if "review_required" in fields else task.review_required
        )
        candidate_review_due_at = self._deadline_utc(
            data.review_due_at if "review_due_at" in fields else task.review_due_at,
            label="reviewDueAt",
            persisted_naive="review_due_at" not in fields,
        )
        if not candidate_review_required:
            if reviewer is not None:
                self._unprocessable(
                    "TASK_REVIEWER_ACTIVE",
                    "Remova o reviewer antes de desativar a revisão.",
                )
            candidate_review_due_at = None
        elif reviewer is None:
            self._unprocessable(
                "TASK_REVIEWER_REQUIRED", "Ative um reviewer antes de exigir revisão."
            )

        self._validate_deadlines(
            due_at=candidate_due_at,
            review_due_at=candidate_review_due_at,
            participant_specs=[item for item in task.participants if item.active],
            review_required=candidate_review_required,
        )

        if "status" in fields and data.status is not None and data.status != task.status:
            self._unprocessable(
                "TASK_COMMAND_REQUIRED", "Mudanças de status devem usar um comando explícito."
            )
        if "assigned_to" in fields:
            if data.assigned_to is None:
                self._unprocessable(
                    "TASK_OWNER_REQUIRED", "A tarefa deve manter exatamente um owner ativo."
                )
            await self._replace_legacy_owner(task, workspace_id, data.assigned_to, context)
        if "title" in fields:
            task.title = data.title  # type: ignore[assignment]
        if "description" in fields:
            task.description = data.description
        if "priority" in fields:
            task.priority = data.priority
        task.review_due_at = candidate_review_due_at
        if "review_required" in fields:
            was_required = task.review_required
            task.review_required = candidate_review_required
            if not task.review_required:
                task.review_state = TaskReviewState.not_required
            elif not was_required:
                task.review_state = TaskReviewState.draft
        if "due_at" in fields or "due_date" in fields:
            task.due_at = candidate_due_at
            task.due_date = candidate_due_date
            task.legacy_due_text = candidate_legacy_due_text

        if task.review_required and task.review_state == TaskReviewState.approved:
            changed_before_bump = self._changed_paths(before, self._snapshot(task))
            if changed_before_bump:
                task.review_state = TaskReviewState.draft

        self._bump(task)
        after = self._snapshot(task)
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.updated,
            before=before,
            after=after,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Tarefa atualizada: {task.title[:60]}",
        )
        if mutation_fields & {
            "assigned_to",
            "due_at",
            "due_date",
            "review_due_at",
            "review_required",
        }:
            await self._sync_reminders(
                task,
                context=context,
                reason="task_deadline_or_recipient_changed",
            )
        return task

    async def replace_participants(
        self,
        task_id: UUID,
        data: TaskParticipantsReplace,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> Task | None:
        enforcement = EntitlementEnforcementService(self.db)
        # Ordem global de locks: workspace antes da tarefa. Assim, duas
        # substituições ou uma criação concorrente não podem furar a quota.
        await enforcement.lock_workspace(workspace_id)
        task = await self._get_for_mutation(
            task_id,
            workspace_id,
            context=context,
            lock=True,
        )
        if task is None:
            return None
        self._require_version(task, data.expected_version)
        if task.review_state == TaskReviewState.pending:
            self._conflict(
                "TASK_REVIEW_PENDING", "Não altere participantes durante uma revisão pendente."
            )
        self._validate_participant_specs(data.participants)
        member_bindings = await self._require_active_members(
            workspace_id, {item.user_id for item in data.participants}
        )
        replacement_reviewer = next(
            (item for item in data.participants if item.role == TaskParticipantRole.reviewer),
            None,
        )
        task_due_at = self._deadline_utc(
            task.due_at,
            label="dueAt",
            persisted_naive=True,
        )
        replacement_review_due_at = self._deadline_utc(
            task.review_due_at if replacement_reviewer is not None else None,
            label="reviewDueAt",
            persisted_naive=True,
        )
        self._validate_deadlines(
            due_at=task_due_at,
            review_due_at=replacement_review_due_at,
            participant_specs=data.participants,
            review_required=replacement_reviewer is not None,
        )
        await self._enforce_task_shape_capacity(
            workspace_id=workspace_id,
            participant_specs=data.participants,
            enforcement=enforcement,
        )
        before = self._snapshot(task)
        now = datetime.now(UTC)
        for participant in task.participants:
            if participant.active:
                participant.active = False
                participant.state = TaskParticipantState.removed
                participant.deactivated_at = now
                participant.updated_at = now
        # Garante que os índices parciais de owner/reviewer vejam os antigos
        # como inativos antes dos INSERTs substitutos no PostgreSQL.
        await self.db.flush()
        for spec in data.participants:
            task.participants.append(
                TaskParticipant(
                    workspace_id=workspace_id,
                    matter_id=task.matter_id,
                    task_id=task.id,
                    membership_id=member_bindings[spec.user_id][0],
                    user_id=spec.user_id,
                    role=spec.role,
                    due_at=self._deadline_utc(
                        spec.due_at,
                        label="participant.dueAt",
                    ),
                    state=TaskParticipantState.assigned,
                    required_for_completion=spec.required_for_completion,
                    label_snapshot=member_bindings[spec.user_id][1],
                    active=True,
                    created_by_user_id=context.actor_user_id,
                )
            )
        owner = next(item for item in data.participants if item.role == TaskParticipantRole.owner)
        task.assigned_to = owner.user_id
        reviewer = next(
            (item for item in data.participants if item.role == TaskParticipantRole.reviewer), None
        )
        if reviewer is None:
            task.review_required = False
            task.review_state = TaskReviewState.not_required
            task.review_due_at = None
        else:
            task.review_required = True
            task.review_due_at = replacement_review_due_at
            # Toda redistribuição invalida uma aprovação anterior, mesmo que o
            # reviewer permaneça: escopo, executores ou prazos podem ter mudado.
            task.review_state = TaskReviewState.draft
        self._bump(task)
        await self.db.flush()
        after = self._snapshot(task)
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.participants_changed,
            before=before,
            after=after,
            comment=data.reason,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Equipe da tarefa atualizada: {task.title[:60]}",
        )
        await self._sync_reminders(
            task,
            context=context,
            reason="task_participants_changed",
        )
        return task

    async def execute_command(
        self,
        task_id: UUID,
        command: TaskCommand,
        data: TaskCommandRequest,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> Task | None:
        enforcement: EntitlementEnforcementService | None = None
        if command in {TaskCommand.submit_for_review, TaskCommand.reopen}:
            enforcement = EntitlementEnforcementService(self.db)
            # As duas transições aumentam estoque (rodada ou tarefa ativa).
            # Serialize no workspace antes de bloquear a linha da tarefa.
            await enforcement.lock_workspace(workspace_id)
        task = await self._get_for_mutation(
            task_id,
            workspace_id,
            context=context,
            lock=True,
        )
        if task is None:
            return None
        self._require_version(task, data.expected_version)
        before = self._snapshot(task)
        now = datetime.now(UTC)
        event_type: TaskEventType
        title: str

        if command == TaskCommand.start:
            self._require_not_terminal(task)
            if task.status not in {TaskStatus.pending, TaskStatus.in_progress}:
                self._conflict(
                    "TASK_INVALID_TRANSITION", "Somente tarefa pendente pode ser iniciada."
                )
            task.status = TaskStatus.in_progress
            event_type, title = TaskEventType.started, "Tarefa iniciada"
        elif command == TaskCommand.block:
            self._require_reason(data.reason, command)
            self._require_not_terminal(task)
            if task.status == TaskStatus.blocked:
                self._conflict("TASK_ALREADY_BLOCKED", "A tarefa já está bloqueada.")
            task.status = TaskStatus.blocked
            event_type, title = TaskEventType.blocked, "Tarefa bloqueada"
        elif command == TaskCommand.unblock:
            if task.status != TaskStatus.blocked or task.cancelled_at is not None:
                self._conflict(
                    "TASK_NOT_BLOCKED", "Somente uma tarefa bloqueada pode ser desbloqueada."
                )
            task.status = TaskStatus.in_progress
            event_type, title = TaskEventType.unblocked, "Tarefa desbloqueada"
        elif command == TaskCommand.submit_for_review:
            self._require_not_terminal(task)
            reviewer = self._active_reviewer(task)
            if not task.review_required or reviewer is None:
                self._conflict("TASK_REVIEWER_REQUIRED", "A tarefa não possui reviewer ativo.")
            if task.status != TaskStatus.in_progress:
                self._conflict("TASK_NOT_IN_PROGRESS", "Inicie a tarefa antes de submetê-la.")
            if task.review_state == TaskReviewState.pending:
                self._conflict("TASK_REVIEW_PENDING", "A tarefa já aguarda revisão.")
            self._require_required_assignees_complete(task)
            assert enforcement is not None  # estreitado pelo comando acima
            await self._enforce_review_round_capacity(
                workspace_id=workspace_id,
                task_id=task.id,
                enforcement=enforcement,
            )
            task.review_round += 1
            task.review_state = TaskReviewState.pending
            task.review_rounds.append(
                TaskReviewRound(
                    workspace_id=workspace_id,
                    matter_id=task.matter_id,
                    round_number=task.review_round,
                    reviewer_user_id=reviewer.user_id,
                    state=TaskReviewState.pending,
                    due_at=self._deadline_utc(
                        reviewer.due_at or task.review_due_at,
                        label="reviewRound.dueAt",
                        persisted_naive=True,
                    ),
                    submitted_by_user_id=context.actor_user_id,
                )
            )
            event_type, title = TaskEventType.review_submitted, "Tarefa enviada para revisão"
        elif command in {TaskCommand.approve, TaskCommand.request_changes}:
            reviewer = self._active_reviewer(task)
            reviewer_matches_actor = (
                reviewer is not None and reviewer.user_id == context.actor_user_id
            )
            if not reviewer_matches_actor and not context.is_platform_root:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "TASK_REVIEWER_ONLY",
                        "message": "Somente o reviewer ativo pode decidir.",
                    },
                )
            if context.is_platform_root and not reviewer_matches_actor:
                self._require_reason(data.reason or data.comment, command)
            if task.review_state != TaskReviewState.pending:
                self._conflict("TASK_REVIEW_NOT_PENDING", "Não existe revisão pendente.")
            round_item = self._current_round(task)
            if round_item is None:
                self._conflict(
                    "TASK_REVIEW_ROUND_MISSING", "A rodada de revisão não foi encontrada."
                )
            if round_item.submitted_by_user_id == context.actor_user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "TASK_SELF_REVIEW_DENIED",
                        "message": (
                            "Quem submeteu a entrega não pode decidir "
                            "a própria revisão."
                        ),
                    },
                )
            round_item.resolved_at = now
            round_item.resolved_by_user_id = context.actor_user_id
            round_item.decision_comment = data.comment or data.reason
            if command == TaskCommand.approve:
                task.review_state = TaskReviewState.approved
                round_item.state = TaskReviewState.approved
                event_type, title = TaskEventType.review_approved, "Revisão aprovada"
            else:
                self._require_reason(data.reason or data.comment, command)
                task.review_state = TaskReviewState.changes_requested
                task.status = TaskStatus.in_progress
                for participant in task.participants:
                    if (
                        participant.active
                        and participant.role
                        in {TaskParticipantRole.owner, TaskParticipantRole.assignee}
                        and participant.required_for_completion
                        and participant.state == TaskParticipantState.completed
                    ):
                        participant.state = TaskParticipantState.in_progress
                        participant.updated_at = now
                round_item.state = TaskReviewState.changes_requested
                event_type, title = (
                    TaskEventType.review_changes_requested,
                    "Ajustes solicitados na revisão",
                )
        elif command == TaskCommand.complete:
            self._require_not_terminal(task)
            if task.status == TaskStatus.blocked:
                self._conflict("TASK_BLOCKED", "Desbloqueie a tarefa antes de concluí-la.")
            self._require_required_assignees_complete(task)
            if task.review_required and task.review_state != TaskReviewState.approved:
                self._conflict(
                    "TASK_REVIEW_REQUIRED", "A revisão precisa ser aprovada antes da conclusão."
                )
            task.status = TaskStatus.done
            task.completed_at = now
            event_type, title = TaskEventType.completed, "Tarefa concluída"
        elif command == TaskCommand.reopen:
            self._require_reason(data.reason, command)
            if task.status != TaskStatus.done and task.cancelled_at is None:
                self._conflict(
                    "TASK_NOT_TERMINAL", "Somente uma tarefa encerrada pode ser reaberta."
                )
            assert enforcement is not None  # estreitado pelo comando acima
            await self._enforce_active_task_capacity(
                workspace_id=workspace_id,
                enforcement=enforcement,
            )
            task.status = TaskStatus.pending
            task.completed_at = None
            task.cancelled_at = None
            task.cancelled_by_user_id = None
            task.cancellation_reason = None
            task.review_state = (
                TaskReviewState.draft if task.review_required else TaskReviewState.not_required
            )
            for participant in task.participants:
                if (
                    participant.active
                    and participant.role
                    in {TaskParticipantRole.owner, TaskParticipantRole.assignee}
                    and participant.required_for_completion
                ):
                    participant.state = TaskParticipantState.assigned
                    participant.updated_at = now
            event_type, title = TaskEventType.reopened, "Tarefa reaberta"
        elif command == TaskCommand.cancel:
            self._require_reason(data.reason, command)
            self._require_not_terminal(task)
            task.status = TaskStatus.done
            task.completed_at = None
            task.cancelled_at = now
            task.cancelled_by_user_id = context.actor_user_id
            task.cancellation_reason = data.reason
            event_type, title = TaskEventType.cancelled, "Tarefa cancelada"
        else:  # pragma: no cover - Enum fecha o conjunto
            self._unprocessable("TASK_COMMAND_UNKNOWN", "Comando de tarefa desconhecido.")

        self._bump(task)
        await self.refresh_open_tasks_count(task.matter_id)
        after = self._snapshot(task)
        await self._record_event(
            task,
            context=context,
            event_type=event_type,
            before=before,
            after=after,
            comment=data.comment or data.reason,
            timeline_type=(
                TimelineEventType.task_completed
                if event_type == TaskEventType.completed
                else TimelineEventType.status_change
            ),
            timeline_title=f"{title}: {task.title[:60]}",
        )
        if command in {TaskCommand.complete, TaskCommand.cancel, TaskCommand.reopen}:
            await self._sync_reminders(
                task,
                context=context,
                reason=f"task_{command.value.replace('-', '_')}",
            )
        if command == TaskCommand.complete:
            await self._propagate_completion_to_exception(
                task, workspace_id=workspace_id, actor_user_id=context.actor_user_id
            )
        return task

    async def set_participant_state(
        self,
        task_id: UUID,
        participant_user_id: UUID,
        data: TaskParticipantStateUpdate,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
        allow_other_user: bool = False,
    ) -> Task | None:
        """Atualiza o progresso de um executor sem concluir a tarefa global."""

        task = await self._get_for_mutation(
            task_id,
            workspace_id,
            context=context,
            lock=True,
        )
        if task is None:
            return None
        self._require_version(task, data.expected_version)
        self._require_not_terminal(task)
        if (
            context.actor_user_id != participant_user_id
            and not allow_other_user
            and not context.is_platform_root
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "TASK_PARTICIPANT_SELF_ONLY",
                    "message": "Sem tasks:assign, o executor só pode atualizar o próprio estado.",
                },
            )
        if task.review_state == TaskReviewState.pending:
            self._conflict(
                "TASK_REVIEW_PENDING",
                "O estado dos executores não pode mudar durante uma revisão pendente.",
            )
        participant = next(
            (
                item
                for item in task.participants
                if item.active
                and item.user_id == participant_user_id
                and item.role in {TaskParticipantRole.owner, TaskParticipantRole.assignee}
            ),
            None,
        )
        if participant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "TASK_ASSIGNEE_NOT_FOUND",
                    "message": "Owner ou executor ativo não encontrado nesta tarefa.",
                },
            )
        if data.state == TaskParticipantState.removed:
            self._unprocessable(
                "TASK_PARTICIPANT_REMOVAL_REQUIRES_REPLACEMENT",
                "Remova participantes pela substituição versionada da equipe.",
            )
        if participant.state == data.state:
            self._conflict(
                "TASK_PARTICIPANT_STATE_UNCHANGED",
                "O executor já está no estado informado.",
            )

        before = self._snapshot(task)
        participant.state = data.state
        participant.updated_at = datetime.now(UTC)
        if task.review_required and task.review_state == TaskReviewState.approved:
            task.review_state = TaskReviewState.draft
        self._bump(task)
        after = self._snapshot(task)
        await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.participant_state_changed,
            before=before,
            after=after,
            comment=data.reason,
            timeline_type=TimelineEventType.status_change,
            timeline_title=f"Executor atualizado: {participant.label_snapshot[:60]}",
        )
        return task

    async def add_comment(
        self,
        task_id: UUID,
        data: TaskCommentCreate,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> Task | None:
        task = await self._get_for_mutation(
            task_id,
            workspace_id,
            context=context,
            lock=True,
        )
        if task is None:
            return None
        self._require_version(task, data.expected_version)
        documents: list[Document] = []
        if data.document_ids:
            documents = list(
                await self.db.scalars(
                    select(Document).where(
                        Document.id.in_(data.document_ids),
                        Document.matter_id == task.matter_id,
                    )
                )
            )
            if {item.id for item in documents} != set(data.document_ids):
                self._unprocessable(
                    "TASK_ATTACHMENT_SCOPE_INVALID",
                    "Todos os documentos anexados devem pertencer ao mesmo caso da tarefa.",
                )
        mention_bindings = await self._require_active_members(
            workspace_id, set(data.mentioned_user_ids)
        )
        before = self._snapshot(task)
        self._bump(task)
        after = self._snapshot(task)
        event = await self._record_event(
            task,
            context=context,
            event_type=TaskEventType.comment_added,
            before=before,
            after=after,
            comment=data.comment,
            timeline_type=TimelineEventType.comment,
            timeline_title=f"Comentário na tarefa: {task.title[:60]}",
        )
        for document in documents:
            self.db.add(
                TaskAttachment(
                    workspace_id=workspace_id,
                    matter_id=task.matter_id,
                    task_id=task.id,
                    task_event_id=event.id,
                    document_id=document.id,
                    name_snapshot=document.name,
                    mime_type_snapshot=document.mime_type,
                    created_by_user_id=context.actor_user_id,
                )
            )
        for user_id in data.mentioned_user_ids:
            self.db.add(
                TaskMention(
                    workspace_id=workspace_id,
                    matter_id=task.matter_id,
                    task_id=task.id,
                    task_event_id=event.id,
                    mentioned_user_id=user_id,
                    label_snapshot=mention_bindings[user_id][1],
                    mentioned_by_user_id=context.actor_user_id,
                )
            )
        if documents or data.mentioned_user_ids:
            await self.db.flush()
        return task

    async def list_events(
        self,
        task_id: UUID,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        allow_workspace: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaskEvent], int] | None:
        if (
            await self.get_for_actor(
                task_id,
                workspace_id,
                actor_user_id=actor_user_id,
                allow_workspace=allow_workspace,
            )
            is None
        ):
            return None
        query = select(TaskEvent).where(
            TaskEvent.workspace_id == workspace_id, TaskEvent.task_id == task_id
        )
        total = int(await self.db.scalar(select(func.count()).select_from(query.subquery())) or 0)
        rows = await self.db.scalars(
            query.order_by(TaskEvent.occurred_at.desc(), TaskEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), total

    async def toggle_status(
        self,
        task_id: UUID,
        workspace_id: UUID,
        toggled_by: UUID | None = None,
        *,
        expected_version: int | None = None,
        context: TaskMutationContext | None = None,
    ) -> Task | None:
        """Compatibilidade: só conclui tarefa simples; jamais reabre `done`."""
        resolved_context = context or TaskMutationContext.system(
            toggled_by,
            prefix="task-toggle",
        )
        task = await self._get_for_mutation(
            task_id,
            workspace_id,
            context=resolved_context,
            lock=True,
        )
        if task is None:
            return None
        if expected_version is not None:
            self._require_version(task, expected_version)
        if task.status == TaskStatus.done or task.cancelled_at is not None:
            return task
        active = [item for item in task.participants if item.active]
        if task.review_required or any(
            item.role
            in {
                TaskParticipantRole.assignee,
                TaskParticipantRole.reviewer,
                TaskParticipantRole.observer,
            }
            for item in active
        ):
            self._conflict(
                "TASK_TOGGLE_COLLABORATIVE_FORBIDDEN",
                "Use comandos explícitos para tarefas colaborativas.",
            )
        command_data = TaskCommandRequest(
            expected_version=task.version,
            reason="Conclusão pela ação legada de tarefa simples.",
        )
        return await self.execute_command(
            task_id,
            TaskCommand.complete,
            command_data,
            workspace_id,
            context=resolved_context,
        )

    async def mark_done(
        self,
        task_id: UUID,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> Task | None:
        """Conclusão interna idempotente usada pela Central de Exceções."""
        task = await self.get(task_id, workspace_id, lock=True)
        if task is None or task.status == TaskStatus.done:
            return task
        if task.status == TaskStatus.blocked:
            self._conflict("TASK_BLOCKED", "Desbloqueie a tarefa antes de concluí-la.")
        if task.review_required and task.review_state != TaskReviewState.approved:
            self._conflict(
                "TASK_REVIEW_REQUIRED",
                "A integração não pode ignorar a revisão obrigatória da tarefa.",
            )
        self._require_required_assignees_complete(task)
        before = self._snapshot(task)
        task.status = TaskStatus.done
        task.completed_at = datetime.now(UTC)
        self._bump(task)
        await self.refresh_open_tasks_count(task.matter_id)
        await self._record_event(
            task,
            context=TaskMutationContext.system(actor_user_id, prefix="exception-task-complete"),
            event_type=TaskEventType.completed,
            before=before,
            after=self._snapshot(task),
            comment="Concluída pela resolução da exceção vinculada.",
            timeline_type=TimelineEventType.task_completed,
            timeline_title=f"Tarefa concluída: {task.title[:60]}",
        )
        await self._sync_reminders(
            task,
            context=TaskMutationContext.system(
                actor_user_id, prefix="exception-task-complete"
            ),
            reason="task_completed",
        )
        return task

    async def _get_by_idempotency(self, matter_id: UUID, idempotency_key: str) -> Task | None:
        result = await self.db.execute(
            select(Task)
            .options(*self._task_options())
            .where(Task.matter_id == matter_id, Task.idempotency_key == idempotency_key)
        )
        return result.scalars().unique().one_or_none()

    @staticmethod
    def _request_hash(
        data: TaskCreate,
        *,
        source_exception_id: UUID | None = None,
        parent_task_id: UUID | None = None,
    ) -> str:
        payload = data.model_dump(mode="json", exclude={"idempotency_key"})
        if source_exception_id is not None:
            payload["sourceExceptionId"] = str(source_exception_id)
        if parent_task_id is not None:
            payload["parentTaskId"] = str(parent_task_id)
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _validate_idempotent_replay(task: Task, request_sha256: str | None) -> None:
        if task.request_sha256 != request_sha256:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A chave idempotente já foi usada com outro conteúdo de tarefa.",
            )

    @staticmethod
    def _create_participant_specs(
        data: TaskCreate, actor_user_id: UUID | None
    ) -> list[TaskParticipantWrite]:
        if data.participants is not None:
            specs = list(data.participants)
            owners = [item for item in specs if item.role == TaskParticipantRole.owner]
            requested_owner = data.owner_id or data.assigned_to
            if requested_owner is not None and (
                len(owners) != 1 or owners[0].user_id != requested_owner
            ):
                TaskService._unprocessable(
                    "TASK_OWNER_CONFLICT",
                    "ownerId/assignedTo diverge do owner informado em participants.",
                )
            return specs
        owner_id = data.owner_id or data.assigned_to or actor_user_id
        if owner_id is None:
            TaskService._unprocessable("TASK_OWNER_REQUIRED", "Informe o owner da tarefa.")
        return [TaskParticipantWrite(user_id=owner_id, role=TaskParticipantRole.owner)]

    @staticmethod
    def _validate_participant_specs(specs: Sequence[TaskParticipantWrite]) -> None:
        owners = [item for item in specs if item.role == TaskParticipantRole.owner]
        reviewers = [item for item in specs if item.role == TaskParticipantRole.reviewer]
        if len(owners) != 1:
            TaskService._unprocessable(
                "TASK_OWNER_REQUIRED", "Deve existir exatamente um owner ativo."
            )
        if len(reviewers) > 1:
            TaskService._unprocessable("TASK_REVIEWER_LIMIT", "Só pode existir um reviewer ativo.")
        owner_assignee_ids = {
            item.user_id
            for item in specs
            if item.role in {TaskParticipantRole.owner, TaskParticipantRole.assignee}
        }
        if reviewers and reviewers[0].user_id in owner_assignee_ids:
            TaskService._unprocessable(
                "TASK_REVIEWER_CONFLICT", "Reviewer não pode ser owner ou assignee."
            )
        user_ids = [item.user_id for item in specs]
        if len(user_ids) != len(set(user_ids)):
            TaskService._unprocessable(
                "TASK_PARTICIPANT_DUPLICATE",
                "Cada participante pode ocupar somente um papel ativo.",
            )

    async def _require_active_members(
        self, workspace_id: UUID, user_ids: Iterable[UUID]
    ) -> dict[UUID, tuple[UUID, str]]:
        requested = set(user_ids)
        if not requested:
            return {}
        member_bindings = {
            user_id: (membership_id, label)
            for membership_id, user_id, label in (
                await self.db.execute(
                    select(WorkspaceMembership.id, WorkspaceMembership.user_id, User.name)
                    .join(User, User.id == WorkspaceMembership.user_id)
                    .where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.user_id.in_(requested),
                        WorkspaceMembership.status == MembershipStatus.active,
                        User.is_active.is_(True),
                        User.deleted_at.is_(None),
                    )
                )
            ).all()
        }
        missing = requested - set(member_bindings)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "TASK_PARTICIPANT_NOT_ACTIVE",
                    "message": "Todos os participantes devem ser membros ativos do workspace.",
                    "userIds": sorted(str(item) for item in missing),
                },
            )
        return member_bindings

    async def _enforce_active_task_capacity(
        self,
        *,
        workspace_id: UUID,
        enforcement: EntitlementEnforcementService,
    ) -> TaskEvent:
        async def load_current_quantity() -> int:
            value = await self.db.scalar(
                select(func.count(Task.id))
                .where(
                    Task.workspace_id == workspace_id,
                    Task.status != TaskStatus.done,
                    Task.cancelled_at.is_(None),
                )
            )
            return int(value or 0)

        await enforcement.enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.active_tasks,
            load_current_quantity=load_current_quantity,
        )

    async def _enforce_task_shape_capacity(
        self,
        *,
        workspace_id: UUID,
        participant_specs: Sequence[TaskParticipantWrite],
        enforcement: EntitlementEnforcementService,
    ) -> None:
        async def empty_task_quantity() -> int:
            # Participantes e reviewers são limites concorrentes por tarefa,
            # conforme o catálogo comercial. Na criação/substituição, o payload
            # representa o conjunto final completo, não um delta incremental.
            return 0

        await enforcement.enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.task_participants,
            load_current_quantity=empty_task_quantity,
            requested_quantity=len(participant_specs),
        )
        reviewer_count = sum(
            item.role == TaskParticipantRole.reviewer for item in participant_specs
        )
        if reviewer_count:
            await enforcement.enforce_stock(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.task_reviews,
                load_current_quantity=empty_task_quantity,
                requested_quantity=reviewer_count,
            )

    async def _enforce_review_round_capacity(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        enforcement: EntitlementEnforcementService,
    ) -> None:
        async def load_current_quantity() -> int:
            value = await self.db.scalar(
                select(func.count(TaskReviewRound.id)).where(
                    TaskReviewRound.workspace_id == workspace_id,
                    TaskReviewRound.task_id == task_id,
                )
            )
            return int(value or 0)

        await enforcement.enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.review_rounds,
            load_current_quantity=load_current_quantity,
        )

    async def _resolve_automatic_owner(
        self,
        *,
        workspace_id: UUID,
        matter: Matter,
        preferred_owner_id: UUID | None,
        actor_user_id: UUID | None,
    ) -> UUID:
        candidates: list[UUID] = []
        for candidate in (
            preferred_owner_id,
            matter.responsible_human_id,
            actor_user_id,
        ):
            if candidate is not None and candidate not in candidates:
                candidates.append(candidate)

        if candidates:
            active_candidates = set(
                (
                    await self.db.scalars(
                        select(WorkspaceMembership.user_id)
                        .join(User, User.id == WorkspaceMembership.user_id)
                        .where(
                            WorkspaceMembership.workspace_id == workspace_id,
                            WorkspaceMembership.user_id.in_(candidates),
                            WorkspaceMembership.status == MembershipStatus.active,
                            User.agent_type == AgentType.human,
                            User.is_active.is_(True),
                            User.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            for candidate in candidates:
                if candidate in active_candidates:
                    return candidate

        role_order = case(
            (WorkspaceMembership.role == WorkspaceRole.workspace_owner, 0),
            (WorkspaceMembership.role == WorkspaceRole.workspace_admin, 1),
            (WorkspaceMembership.role == WorkspaceRole.legal_manager, 2),
            (WorkspaceMembership.role == WorkspaceRole.operator, 3),
            (WorkspaceMembership.role == WorkspaceRole.finance_manager, 4),
            else_=5,
        )
        fallback_owner_id = await self.db.scalar(
            select(WorkspaceMembership.user_id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.agent_type == AgentType.human,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(
                role_order.asc(),
                WorkspaceMembership.created_at.asc(),
                WorkspaceMembership.user_id.asc(),
            )
            .limit(1)
        )
        if fallback_owner_id is None:
            self._unprocessable(
                "TASK_OWNER_UNAVAILABLE",
                "Nenhum membro humano ativo esta disponivel para assumir a tarefa.",
            )
        return fallback_owner_id

    async def _replace_legacy_owner(
        self,
        task: Task,
        workspace_id: UUID,
        owner_id: UUID,
        context: TaskMutationContext,
    ) -> None:
        member_bindings = await self._require_active_members(workspace_id, {owner_id})
        reviewer = self._active_reviewer(task)
        if reviewer and reviewer.user_id == owner_id:
            self._unprocessable("TASK_REVIEWER_CONFLICT", "Reviewer não pode se tornar owner.")
        now = datetime.now(UTC)
        for item in task.participants:
            if item.active and item.role == TaskParticipantRole.owner:
                item.active = False
                item.state = TaskParticipantState.removed
                item.deactivated_at = now
                item.updated_at = now
        await self.db.flush()
        task.participants.append(
            TaskParticipant(
                workspace_id=workspace_id,
                matter_id=task.matter_id,
                task_id=task.id,
                membership_id=member_bindings[owner_id][0],
                user_id=owner_id,
                role=TaskParticipantRole.owner,
                state=TaskParticipantState.assigned,
                required_for_completion=False,
                label_snapshot=member_bindings[owner_id][1],
                active=True,
                created_by_user_id=context.actor_user_id,
            )
        )
        task.assigned_to = owner_id

    @staticmethod
    def _normalized_due(
        due_at: datetime | None, due_date: str | None
    ) -> tuple[datetime | None, str | None, str | None]:
        if due_at is not None:
            local_due_date = due_at.date()
            normalized = TaskService._deadline_utc(due_at, label="dueAt")
            assert normalized is not None
            if due_date:
                try:
                    parsed_date = date.fromisoformat(due_date)
                except ValueError:
                    TaskService._unprocessable(
                        "TASK_DUE_CONFLICT", "dueDate textual não pode coexistir com dueAt."
                    )
                if parsed_date != local_due_date:
                    TaskService._unprocessable("TASK_DUE_CONFLICT", "dueDate e dueAt divergem.")
            return normalized, local_due_date.isoformat(), None
        if due_date is None:
            return None, None, None
        try:
            parsed = date.fromisoformat(due_date)
        except ValueError:
            return None, due_date, due_date
        return datetime.combine(parsed, time.min, tzinfo=UTC), parsed.isoformat(), None

    @staticmethod
    def _deadline_utc(
        value: datetime | None,
        *,
        label: str,
        persisted_naive: bool = False,
    ) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            if persisted_naive:
                # Compatibilidade com linhas carregadas antes do UTCDateTime:
                # o domínio sempre persistiu estes novos campos em UTC.
                return value.replace(tzinfo=UTC)
            TaskService._unprocessable(
                "TASK_TIMEZONE_REQUIRED", f"{label} deve incluir timezone explícito."
            )
        return value.astimezone(UTC)

    @staticmethod
    def _validate_deadlines(
        *,
        due_at: datetime | None,
        review_due_at: datetime | None,
        participant_specs: Sequence[TaskParticipantWrite | TaskParticipant],
        review_required: bool,
    ) -> None:
        deadlines: list[tuple[str, datetime]] = []
        if review_due_at is not None:
            if not review_required:
                TaskService._unprocessable(
                    "TASK_REVIEW_DEADLINE_WITHOUT_REVIEW",
                    "Prazo de revisão exige revisão ativa.",
                )
            deadlines.append(("reviewDueAt", review_due_at))
        deadlines.extend(
            (f"participants[{index}].dueAt", item.due_at)
            for index, item in enumerate(participant_specs)
            if item.due_at is not None
        )
        for label, value in ([("dueAt", due_at)] if due_at is not None else []) + deadlines:
            if value.utcoffset() is None:
                TaskService._unprocessable(
                    "TASK_TIMEZONE_REQUIRED", f"{label} deve incluir timezone explícito."
                )
        if deadlines and due_at is None:
            TaskService._unprocessable(
                "TASK_GENERAL_DEADLINE_REQUIRED",
                "Defina o prazo geral antes de informar prazos individuais ou de revisão.",
            )
        if due_at is None:
            return
        general_utc = due_at.astimezone(UTC)
        invalid = [label for label, value in deadlines if value.astimezone(UTC) > general_utc]
        if invalid:
            TaskService._unprocessable(
                "TASK_DEADLINE_ORDER_INVALID",
                "Prazo individual ou de revisão não pode superar o prazo geral: "
                + ", ".join(invalid),
            )

    @staticmethod
    def _active_reviewer(task: Task) -> TaskParticipant | None:
        return next(
            (
                item
                for item in task.participants
                if item.active and item.role == TaskParticipantRole.reviewer
            ),
            None,
        )

    @staticmethod
    def _current_round(task: Task) -> TaskReviewRound | None:
        return next(
            (item for item in task.review_rounds if item.round_number == task.review_round), None
        )

    @staticmethod
    def _require_required_assignees_complete(task: Task) -> None:
        incomplete = [
            item
            for item in task.participants
            if item.active
            and item.role in {TaskParticipantRole.owner, TaskParticipantRole.assignee}
            and item.required_for_completion
            and item.state != TaskParticipantState.completed
        ]
        if incomplete:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TASK_ASSIGNEES_INCOMPLETE",
                    "message": (
                        "Todos os owners e executores obrigatórios "
                        "devem concluir sua parte."
                    ),
                    "userIds": sorted(str(item.user_id) for item in incomplete),
                },
            )

    @staticmethod
    def _require_version(task: Task, expected_version: int) -> None:
        if task.version != expected_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TASK_VERSION_CONFLICT",
                    "message": "A tarefa foi alterada por outra operação.",
                    "expectedVersion": expected_version,
                    "currentVersion": task.version,
                },
            )

    @staticmethod
    def _require_reason(reason: str | None, command: TaskCommand) -> None:
        if reason is None or len(reason.strip()) < 8:
            TaskService._unprocessable(
                "TASK_REASON_REQUIRED", f"O comando {command.value} exige motivo com 8 caracteres."
            )

    @staticmethod
    def _require_not_terminal(task: Task) -> None:
        if task.status == TaskStatus.done or task.cancelled_at is not None:
            TaskService._conflict(
                "TASK_TERMINAL", "Use o comando reopen antes de alterar uma tarefa encerrada."
            )

    @staticmethod
    def _bump(task: Task) -> None:
        task.version += 1
        task.updated_at = datetime.now(UTC)

    @staticmethod
    def _snapshot(task: Task) -> dict:
        def iso(value):
            return value.isoformat() if isinstance(value, datetime) else value

        participants = sorted(
            (
                {
                    "id": str(item.id),
                    "userId": str(item.user_id),
                    "role": item.role.value,
                    "dueAt": iso(item.due_at),
                    "state": item.state.value,
                    "requiredForCompletion": item.required_for_completion,
                    "labelSnapshot": item.label_snapshot,
                    "active": item.active,
                }
                for item in task.participants
                if item.active
            ),
            key=lambda item: (item["role"], item["userId"]),
        )
        return {
            "id": str(task.id),
            "workspaceId": str(task.workspace_id),
            "contextType": task.context_type.value,
            "origin": task.origin.value,
            "matterId": str(task.matter_id) if task.matter_id else None,
            "clientId": str(task.client_id) if task.client_id else None,
            "contractId": str(task.contract_id) if task.contract_id else None,
            "originResourceId": (
                str(task.origin_resource_id) if task.origin_resource_id else None
            ),
            "originLabel": task.origin_label,
            "parentTaskId": str(task.parent_task_id) if task.parent_task_id else None,
            "title": task.title,
            "description": task.description,
            "status": task.status.value,
            "priority": task.priority.value if task.priority else None,
            "assignedTo": str(task.assigned_to) if task.assigned_to else None,
            "dueDate": task.due_date,
            "dueAt": iso(task.due_at),
            "legacyDueText": task.legacy_due_text,
            "reviewDueAt": iso(task.review_due_at),
            "reviewRequired": task.review_required,
            "reviewState": task.review_state.value,
            "reviewRound": task.review_round,
            "completedAt": iso(task.completed_at),
            "cancelledAt": iso(task.cancelled_at),
            "cancellationReason": task.cancellation_reason,
            "archivedAt": iso(task.archived_at),
            "archivedByUserId": (
                str(task.archived_by_user_id) if task.archived_by_user_id else None
            ),
            "archiveReason": task.archive_reason,
            "sourceExceptionId": (
                str(task.source_exception_id) if task.source_exception_id else None
            ),
            "participants": participants,
            "version": task.version,
        }

    @staticmethod
    def _changed_paths(before: dict | None, after: dict | None) -> list[str]:
        if before is None:
            return sorted(after.keys()) if after else []
        if after is None:
            return sorted(before.keys())
        return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))

    async def _record_event(
        self,
        task: Task,
        *,
        context: TaskMutationContext,
        event_type: TaskEventType,
        before: dict | None,
        after: dict | None,
        timeline_type: TimelineEventType,
        timeline_title: str,
        comment: str | None = None,
        reason: str | None = None,
        reason_source: str | None = None,
    ) -> TaskEvent:
        changed_paths = self._changed_paths(before, after)
        actor = (
            await self.db.get(User, context.actor_user_id)
            if context.actor_user_id is not None
            else None
        )
        actor_label = sanitize_audit_text(actor.name) if actor is not None else "Sistema"
        actor_role = (
            actor.platform_role_package
            if actor is not None and actor.platform_role_package
            else actor.role.value
            if actor is not None
            else "system"
        )
        actor_type = actor.agent_type.value if actor is not None else "system"
        safe_comment = sanitize_audit_text(comment) if comment is not None else None
        safe_reason = sanitize_audit_text(reason or comment or timeline_title)
        resolved_reason_source = reason_source or (
            "user"
            if (reason is not None or comment is not None)
            and context.session_type != "system"
            else "system"
        )
        event = TaskEvent(
                workspace_id=task.workspace_id,
                matter_id=task.matter_id,
                task_id=task.id,
                actor_user_id=context.actor_user_id,
                actor_label_snapshot=actor_label,
                actor_role_snapshot=actor_role,
                actor_type=actor_type,
                event_type=event_type,
                before_data=before,
                after_data=after,
                changed_paths=changed_paths,
                comment=safe_comment,
                reason=safe_reason,
                reason_source=resolved_reason_source,
                correlation_id=context.correlation_id,
                session_type=context.session_type,
                administrative_session_id=context.administrative_session_id,
                support_grant_id=context.support_grant_id,
                resource_version_before=before.get("version") if before else None,
            resource_version_after=after.get("version") if after else None,
        )
        self.db.add(event)
        self.db.add(
            KlmAuditEvent(
                workspace_id=task.workspace_id,
                actor_user_id=context.actor_user_id,
                actor_type=actor_type,
                actor_label=actor_label,
                event_type=f"task_{event_type.value}",
                entity_type="task",
                entity_id=task.id,
                summary=timeline_title,
                correlation_id=context.correlation_id,
                before_data=before,
                after_data=after,
            )
        )
        if task.matter_id is not None:
            self.db.add(
                TimelineEvent(
                    matter_id=task.matter_id,
                    agent_id=context.actor_user_id,
                    event_type=timeline_type,
                    title=timeline_title,
                    description=safe_comment,
                    event_data={
                        "task_id": str(task.id),
                        "task_event_type": event_type.value,
                        "changed_paths": changed_paths,
                        "correlation_id": context.correlation_id,
                        "version": task.version,
                    },
                )
            )
        await self.db.flush()
        return event
        return event

    async def _workspace_id(self, matter_id: UUID) -> UUID:
        workspace_id = await self.db.scalar(
            select(Matter.workspace_id).where(Matter.id == matter_id)
        )
        if workspace_id is None:  # pragma: no cover - FK e lock já protegem
            raise RuntimeError("Matter sem workspace durante evento de tarefa.")
        return workspace_id

    async def _propagate_completion_to_exception(
        self, task: Task, *, workspace_id: UUID, actor_user_id: UUID | None
    ) -> None:
        if task.source_exception_id is None or actor_user_id is None:
            return
        from app.services.exception_center_service import (
            ExceptionCenterAlreadyResolvedError,
            ExceptionCenterNotFoundError,
            ExceptionCenterService,
        )

        exceptions = ExceptionCenterService(self.db, workspace_id)
        try:
            await exceptions.resolve(
                task.source_exception_id,
                resolved_by=actor_user_id,
                resolution_reason="Concluída via lista de tarefas.",
            )
        except (ExceptionCenterAlreadyResolvedError, ExceptionCenterNotFoundError):
            logger.info(
                "task_completion_exception_propagation_skipped",
                task_id=str(task.id),
                source_exception_id=str(task.source_exception_id),
            )

    async def refresh_open_tasks_count(self, matter_id: UUID | None) -> None:
        if matter_id is None:
            return
        open_count = int(
            await self.db.scalar(
                select(func.count(Task.id)).where(
                    Task.matter_id == matter_id,
                    Task.status != TaskStatus.done,
                    Task.cancelled_at.is_(None),
                )
            )
            or 0
        )
        matter = await self.db.get(Matter, matter_id)
        if matter:
            matter.open_tasks = open_count
            matter.updated_at = datetime.now(UTC)

    async def _sync_reminders(
        self,
        task: Task,
        *,
        context: TaskMutationContext,
        reason: str,
    ) -> None:
        if task.matter_id is None:
            return
        # Local import keeps the core task model usable in isolated migration
        # tooling while reminder lifecycle remains part of the same transaction.
        from app.services.task_reminder_service import (
            TaskReminderMutationContext,
            TaskReminderService,
        )

        await TaskReminderService(self.db).sync_task(
            task,
            context=TaskReminderMutationContext.from_task_context(context),
            reason=reason,
        )

    @staticmethod
    def _unprocessable(code: str, message: str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": code, "message": message},
        )

    @staticmethod
    def _conflict(code: str, message: str):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": code, "message": message},
        )


__all__ = ["TaskMutationContext", "TaskService"]
