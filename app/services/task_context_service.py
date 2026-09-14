"""Expand-phase creation of canonical tasks outside a Matter.

The regular ``TaskService`` remains the authority for collaboration,
authorization, audit, quotas, and all mutations.  This service only resolves
the requested tenant context and constructs the same canonical ``Task`` row.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.client_contract import ClientContract, ClientContractStatus
from app.models.matter import Matter
from app.models.task import (
    Task,
    TaskEventType,
    TaskParticipant,
    TaskParticipantRole,
    TaskParticipantState,
    TaskReviewState,
)
from app.models.task import (
    TaskContextType as ModelTaskContextType,
)
from app.models.task import (
    TaskOrigin as ModelTaskOrigin,
)
from app.models.timeline_event import TimelineEventType
from app.schemas.task_context import ContextualTaskCreate, TaskContextType
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.task_service import TaskMutationContext, TaskService


class TaskContextService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.tasks = TaskService(db)

    @staticmethod
    def _request_hash(data: ContextualTaskCreate) -> str:
        raw = json.dumps(
            data.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    async def _resolve(
        self, data: ContextualTaskCreate, workspace_id: UUID
    ) -> tuple[UUID | None, UUID | None, UUID | None]:
        requested = data.context
        if requested.type == TaskContextType.workspace:
            return None, None, None
        if requested.type == TaskContextType.contract:
            contract = await self.db.scalar(
                select(ClientContract).where(
                    ClientContract.id == requested.contract_id,
                    ClientContract.client_id == requested.client_id,
                    ClientContract.workspace_id == workspace_id,
                    ClientContract.deleted_at.is_(None),
                    ClientContract.status.in_(
                        (ClientContractStatus.draft, ClientContractStatus.active)
                    ),
                )
            )
            if contract is None:
                raise HTTPException(status_code=404, detail="Contrato do cliente não encontrado.")
            # The contract may itself reference a Matter, but the task has one
            # explicit anchor.  Keeping matter_id empty preserves the canonical
            # contract context and the database coherence constraint.
            return None, contract.client_id, contract.id
        if requested.type == TaskContextType.client:
            client = await self.db.scalar(
                select(Client).where(
                    Client.id == requested.client_id,
                    Client.workspace_id == workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
            if client is None:
                raise HTTPException(status_code=404, detail="Cliente não encontrado.")
            return None, client.id, None

        matter = await self.db.scalar(
            select(Matter).where(
                Matter.id == requested.matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise HTTPException(status_code=404, detail="Caso não encontrado.")
        return matter.id, None, None

    async def create(
        self,
        data: ContextualTaskCreate,
        *,
        workspace_id: UUID,
        context: TaskMutationContext,
        parent_task_id: UUID | None = None,
    ) -> Task:
        matter_id, client_id, contract_id = await self._resolve(data, workspace_id)
        default_owner_id = context.actor_user_id
        participant_specs = self.tasks._create_participant_specs(data, default_owner_id)
        self.tasks._validate_participant_specs(participant_specs)
        member_bindings = await self.tasks._require_active_members(
            workspace_id, {item.user_id for item in participant_specs}
        )
        owner_id = next(
            item.user_id for item in participant_specs if item.role == TaskParticipantRole.owner
        )

        idempotency_key = (data.idempotency_key or "").strip() or None
        request_sha256 = self._request_hash(data) if idempotency_key else None
        if idempotency_key:
            existing = await self.db.scalar(
                select(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                self.tasks._validate_idempotent_replay(existing, request_sha256)
                return await self.tasks.get(existing.id, workspace_id) or existing

        due_at, due_date, legacy_due_text = self.tasks._normalized_due(data.due_at, data.due_date)
        review_due_at = self.tasks._deadline_utc(data.review_due_at, label="reviewDueAt")
        has_reviewer = any(item.role == TaskParticipantRole.reviewer for item in participant_specs)
        review_required = bool(data.review_required or has_reviewer)
        if review_required and not has_reviewer:
            self.tasks._unprocessable(
                "TASK_REVIEWER_REQUIRED", "Uma tarefa com revisão exige um reviewer ativo."
            )
        self.tasks._validate_deadlines(
            due_at=due_at,
            review_due_at=review_due_at,
            participant_specs=participant_specs,
            review_required=review_required,
        )

        enforcement = EntitlementEnforcementService(self.db)
        await enforcement.lock_workspace(workspace_id)
        if idempotency_key:
            existing = await self.db.scalar(
                select(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                self.tasks._validate_idempotent_replay(existing, request_sha256)
                return await self.tasks.get(existing.id, workspace_id) or existing
        await self.tasks._enforce_active_task_capacity(
            workspace_id=workspace_id, enforcement=enforcement
        )
        await self.tasks._enforce_task_shape_capacity(
            workspace_id=workspace_id,
            participant_specs=participant_specs,
            enforcement=enforcement,
        )

        task = self.tasks.build_model(
            workspace_id=workspace_id,
            context_type=ModelTaskContextType(data.context.type.value),
            origin=ModelTaskOrigin(data.origin.value),
            origin_resource_id=data.origin_resource_id,
            origin_label=data.origin_label,
            matter_id=matter_id,
            client_id=client_id,
            contract_id=contract_id,
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
            review_state=(
                TaskReviewState.draft if review_required else TaskReviewState.not_required
            ),
            created_by_user_id=context.actor_user_id,
            is_ai_generated=data.is_ai_generated,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
        for spec in participant_specs:
            task.participants.append(
                TaskParticipant(
                    workspace_id=workspace_id,
                    matter_id=matter_id,
                    membership_id=member_bindings[spec.user_id][0],
                    user_id=spec.user_id,
                    role=spec.role,
                    due_at=self.tasks._deadline_utc(spec.due_at, label="participant.dueAt"),
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
            if idempotency_key is None:
                raise
            existing = await self.db.scalar(
                select(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise error
            self.tasks._validate_idempotent_replay(existing, request_sha256)
            return await self.tasks.get(existing.id, workspace_id) or existing

        await self.tasks.refresh_open_tasks_count(matter_id)
        await self.tasks._record_event(
            task,
            context=context,
            event_type=TaskEventType.created,
            before=None,
            after=self.tasks._snapshot(task),
            timeline_type=TimelineEventType.task_created,
            timeline_title=f"Tarefa criada: {data.title[:60]}",
        )
        return await self.tasks.get(task.id, workspace_id) or task
