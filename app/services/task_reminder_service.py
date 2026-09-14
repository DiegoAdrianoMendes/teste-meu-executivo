"""Transactional scheduling and lifecycle rules for task reminders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.post_commit import queue_task_reminder_wakeup
from app.models.entitlement import EntitlementKey
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.task import (
    Task,
    TaskParticipant,
    TaskParticipantRole,
    TaskStatus,
)
from app.models.task_reminder import (
    TaskReminderChannel,
    TaskReminderCommandReceipt,
    TaskReminderDeliveryStatus,
    TaskReminderEvent,
    TaskReminderEventType,
    TaskReminderOutbox,
    TaskReminderRecipient,
    TaskReminderRelativeTo,
    TaskReminderRule,
    TaskReminderRuleState,
)
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.task_reminder import TaskReminderRuleCreate, TaskReminderRuleUpdate
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.task_service import TaskService

OPEN_DELIVERY_STATUSES = frozenset(
    {
        TaskReminderDeliveryStatus.pending,
        TaskReminderDeliveryStatus.processing,
        TaskReminderDeliveryStatus.failed,
    }
)


@dataclass(frozen=True, slots=True)
class TaskReminderMutationContext:
    actor_user_id: UUID | None
    correlation_id: str
    session_type: str = "normal"
    administrative_session_id: UUID | None = None
    support_grant_id: UUID | None = None

    @classmethod
    def system(cls, *, prefix: str = "task-reminder") -> TaskReminderMutationContext:
        return cls(actor_user_id=None, correlation_id=f"{prefix}:{uuid4()}", session_type="system")

    @classmethod
    def from_task_context(cls, context: Any) -> TaskReminderMutationContext:
        return cls(
            actor_user_id=getattr(context, "actor_user_id", None),
            correlation_id=str(getattr(context, "correlation_id", "") or uuid4())[:120],
            session_type=str(getattr(context, "session_type", "normal"))[:32],
            administrative_session_id=getattr(context, "administrative_session_id", None),
            support_grant_id=getattr(context, "support_grant_id", None),
        )


class TaskReminderService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _rule_options():
        return (selectinload(TaskReminderRule.deliveries),)

    async def list_rules(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        allow_workspace_access: bool = False,
    ) -> list[dict]:
        if await self._get_visible_task(
            workspace_id,
            task_id,
            actor_user_id=actor_user_id,
            allow_workspace_access=allow_workspace_access,
        ) is None:
            raise self._not_found()
        rows = await self.db.scalars(
            select(TaskReminderRule)
            .options(*self._rule_options())
            .where(
                TaskReminderRule.workspace_id == workspace_id,
                TaskReminderRule.task_id == task_id,
                TaskReminderRule.active.is_(True),
            )
            .order_by(TaskReminderRule.created_at, TaskReminderRule.id)
        )
        return [self.presentation(row) for row in rows.unique().all()]

    async def list_events(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        rule_id: UUID,
        actor_user_id: UUID,
        allow_workspace_access: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaskReminderEvent], int]:
        if await self._get_visible_task(
            workspace_id,
            task_id,
            actor_user_id=actor_user_id,
            allow_workspace_access=allow_workspace_access,
        ) is None or await self._get_rule(workspace_id, task_id, rule_id) is None:
            raise self._not_found()
        query = select(TaskReminderEvent).where(
            TaskReminderEvent.workspace_id == workspace_id,
            TaskReminderEvent.task_id == task_id,
            TaskReminderEvent.rule_id == rule_id,
        )
        total = int(await self.db.scalar(select(func.count()).select_from(query.subquery())) or 0)
        events = await self.db.scalars(
            query.order_by(TaskReminderEvent.occurred_at.desc(), TaskReminderEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(events.all()), total

    async def create_rule(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        data: TaskReminderRuleCreate,
        idempotency_key: str,
        context: TaskReminderMutationContext,
        allow_workspace_access: bool = False,
    ) -> tuple[dict, bool]:
        key = self._normalize_idempotency_key(idempotency_key)
        digest = self._request_digest("create", task_id, None, data.model_dump(mode="json"))
        visible_task = await self._get_visible_task_for_context(
            workspace_id,
            task_id,
            context=context,
            allow_workspace_access=allow_workspace_access,
        )
        if visible_task is None:
            raise self._not_found()
        replay = await self._receipt_replay(
            workspace_id=workspace_id,
            task_id=task_id,
            idempotency_key=key,
            operation="create",
            request_sha256=digest,
        )
        if replay is not None:
            return replay, True

        enforcement = EntitlementEnforcementService(self.db)
        await enforcement.lock_workspace(workspace_id)
        task = await self._get_visible_task_for_context(
            workspace_id,
            task_id,
            context=context,
            allow_workspace_access=allow_workspace_access,
            lock=True,
        )
        if task is None:
            raise self._not_found()
        replay = await self._receipt_replay(
            workspace_id=workspace_id,
            task_id=task_id,
            idempotency_key=key,
            operation="create",
            request_sha256=digest,
        )
        if replay is not None:
            return replay, True
        self._require_task_version(task, data.expected_version)
        self._require_open_task(task)
        await self._validate_specific_recipient(
            task, workspace_id, data.recipient, data.recipient_user_id
        )

        async def current_quantity() -> int:
            return int(
                await self.db.scalar(
                    select(func.count(TaskReminderRule.id)).where(
                        TaskReminderRule.workspace_id == workspace_id,
                        TaskReminderRule.task_id == task_id,
                        TaskReminderRule.active.is_(True),
                    )
                )
                or 0
            )

        await enforcement.enforce_stock(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.task_reminder_rules,
            load_current_quantity=current_quantity,
        )
        recipient_key = self._recipient_key(data.recipient, data.recipient_user_id)
        await self._require_unique_semantics(
            task_id=task_id,
            relative_to=data.relative_to,
            offset_minutes_before=data.offset_minutes_before,
            channel=data.channel,
            recipient_key=recipient_key,
        )
        rule = TaskReminderRule(
            workspace_id=workspace_id,
            matter_id=task.matter_id,
            task_id=task.id,
            relative_to=data.relative_to,
            offset_minutes_before=data.offset_minutes_before,
            channel=data.channel,
            recipient=data.recipient,
            recipient_user_id=data.recipient_user_id,
            recipient_key=recipient_key,
            state=TaskReminderRuleState.pending_due,
            active=True,
            version=1,
            created_by_user_id=context.actor_user_id,
            deliveries=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.db.add(rule)
        await self.db.flush()
        await self._schedule_rule(rule, task, context=context)
        after = self.snapshot(rule)
        await self._record_event(
            rule,
            context=context,
            event_type=TaskReminderEventType.created,
            before=None,
            after=after,
            reason="Regra de lembrete criada.",
        )
        await self.db.refresh(rule, attribute_names=["created_at", "updated_at"])
        response = self.presentation(rule)
        await self._store_receipt(
            workspace_id=workspace_id,
            matter_id=rule.matter_id,
            task_id=task_id,
            rule_id=rule.id,
            idempotency_key=key,
            operation="create",
            request_sha256=digest,
            response_status=status.HTTP_201_CREATED,
            response_data=response,
        )
        return response, False

    async def update_rule(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        rule_id: UUID,
        data: TaskReminderRuleUpdate,
        idempotency_key: str,
        context: TaskReminderMutationContext,
        allow_workspace_access: bool = False,
    ) -> tuple[dict, bool]:
        key = self._normalize_idempotency_key(idempotency_key)
        digest = self._request_digest("update", task_id, rule_id, data.model_dump(mode="json"))
        if await self._get_visible_task_for_context(
            workspace_id,
            task_id,
            context=context,
            allow_workspace_access=allow_workspace_access,
        ) is None:
            raise self._not_found()
        replay = await self._receipt_replay(
            workspace_id=workspace_id,
            task_id=task_id,
            idempotency_key=key,
            operation="update",
            request_sha256=digest,
        )
        if replay is not None:
            return replay, True
        enforcement = EntitlementEnforcementService(self.db)
        await enforcement.lock_workspace(workspace_id)
        task = await self._get_visible_task_for_context(
            workspace_id,
            task_id,
            context=context,
            allow_workspace_access=allow_workspace_access,
            lock=True,
        )
        if task is None:
            raise self._not_found()
        replay = await self._receipt_replay(
            workspace_id=workspace_id,
            task_id=task_id,
            idempotency_key=key,
            operation="update",
            request_sha256=digest,
        )
        if replay is not None:
            return replay, True
        self._require_open_task(task)
        rule = await self._get_rule(workspace_id, task_id, rule_id, lock=True)
        if rule is None or not rule.active:
            raise self._not_found()
        self._require_rule_version(rule, data.expected_version)

        candidate_relative_to = data.relative_to or rule.relative_to
        candidate_offset = (
            data.offset_minutes_before
            if data.offset_minutes_before is not None
            else rule.offset_minutes_before
        )
        candidate_channel = data.channel or rule.channel
        candidate_recipient = data.recipient or rule.recipient
        candidate_user_id = (
            data.recipient_user_id
            if "recipient" in data.model_fields_set
            else rule.recipient_user_id
        )
        await self._validate_specific_recipient(
            task, workspace_id, candidate_recipient, candidate_user_id
        )
        recipient_key = self._recipient_key(candidate_recipient, candidate_user_id)
        await self._require_unique_semantics(
            task_id=task_id,
            relative_to=candidate_relative_to,
            offset_minutes_before=candidate_offset,
            channel=candidate_channel,
            recipient_key=recipient_key,
            exclude_rule_id=rule.id,
        )

        before = self.snapshot(rule)
        await self._cancel_open_deliveries(rule, reason="rule_updated")
        rule.relative_to = candidate_relative_to
        rule.offset_minutes_before = candidate_offset
        rule.channel = candidate_channel
        rule.recipient = candidate_recipient
        rule.recipient_user_id = candidate_user_id
        rule.recipient_key = recipient_key
        self._bump(rule)
        await self._schedule_rule(rule, task, context=context)
        after = self.snapshot(rule)
        await self._record_event(
            rule,
            context=context,
            event_type=TaskReminderEventType.updated,
            before=before,
            after=after,
            reason="Regra de lembrete atualizada.",
        )
        await self.db.refresh(rule, attribute_names=["created_at", "updated_at"])
        response = self.presentation(rule)
        await self._store_receipt(
            workspace_id=workspace_id,
            matter_id=rule.matter_id,
            task_id=task_id,
            rule_id=rule.id,
            idempotency_key=key,
            operation="update",
            request_sha256=digest,
            response_status=status.HTTP_200_OK,
            response_data=response,
        )
        return response, False

    async def delete_rule(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        rule_id: UUID,
        expected_version: int,
        idempotency_key: str,
        context: TaskReminderMutationContext,
        allow_workspace_access: bool = False,
    ) -> tuple[dict, bool]:
        key = self._normalize_idempotency_key(idempotency_key)
        payload = {"expectedVersion": expected_version}
        digest = self._request_digest("delete", task_id, rule_id, payload)
        if await self._get_visible_task_for_context(
            workspace_id,
            task_id,
            context=context,
            allow_workspace_access=allow_workspace_access,
        ) is None:
            raise self._not_found()
        replay = await self._receipt_replay(
            workspace_id=workspace_id,
            task_id=task_id,
            idempotency_key=key,
            operation="delete",
            request_sha256=digest,
        )
        if replay is not None:
            return replay, True
        enforcement = EntitlementEnforcementService(self.db)
        await enforcement.lock_workspace(workspace_id)
        task = await self._get_visible_task_for_context(
            workspace_id,
            task_id,
            context=context,
            allow_workspace_access=allow_workspace_access,
            lock=True,
        )
        if task is None:
            raise self._not_found()
        replay = await self._receipt_replay(
            workspace_id=workspace_id,
            task_id=task_id,
            idempotency_key=key,
            operation="delete",
            request_sha256=digest,
        )
        if replay is not None:
            return replay, True
        rule = await self._get_rule(workspace_id, task_id, rule_id, lock=True)
        if rule is None or not rule.active:
            raise self._not_found()
        self._require_rule_version(rule, expected_version)
        before = self.snapshot(rule)
        await self._cancel_open_deliveries(rule, reason="rule_deleted")
        rule.active = False
        rule.state = TaskReminderRuleState.disabled
        rule.deleted_at = datetime.now(UTC)
        rule.deleted_by_user_id = context.actor_user_id
        self._bump(rule)
        after = self.snapshot(rule)
        await self._record_event(
            rule,
            context=context,
            event_type=TaskReminderEventType.deleted,
            before=before,
            after=after,
            reason="Regra de lembrete removida.",
        )
        response = {
            "id": str(rule.id),
            "task_id": str(rule.task_id),
            "deleted": True,
            "version": rule.version,
        }
        await self._store_receipt(
            workspace_id=workspace_id,
            matter_id=rule.matter_id,
            task_id=task_id,
            rule_id=rule.id,
            idempotency_key=key,
            operation="delete",
            request_sha256=digest,
            response_status=status.HTTP_200_OK,
            response_data=response,
        )
        return response, False

    async def sync_task(
        self,
        task: Task,
        *,
        context: TaskReminderMutationContext,
        reason: str,
    ) -> int:
        """Invalidate prior intents and derive new ones inside the task transaction."""
        rules = list(
            (
                await self.db.scalars(
                    select(TaskReminderRule)
                    .options(*self._rule_options())
                    .where(
                        TaskReminderRule.task_id == task.id,
                        TaskReminderRule.active.is_(True),
                    )
                    .order_by(TaskReminderRule.id)
                    .with_for_update()
                )
            )
            .unique()
            .all()
        )
        terminal = task.status == TaskStatus.done or task.cancelled_at is not None
        for rule in rules:
            before = self.snapshot(rule)
            await self._cancel_open_deliveries(rule, reason=reason)
            self._bump(rule)
            if terminal:
                rule.state = TaskReminderRuleState.disabled
                event_type = TaskReminderEventType.cancelled
            else:
                await self._schedule_rule(rule, task, context=context)
                event_type = TaskReminderEventType.rescheduled
            await self._record_event(
                rule,
                context=context,
                event_type=event_type,
                before=before,
                after=self.snapshot(rule),
                reason=reason,
            )
        return len(rules)

    async def _schedule_rule(
        self,
        rule: TaskReminderRule,
        task: Task,
        *,
        context: TaskReminderMutationContext,
    ) -> None:
        # Keep the timestamp available in the async ORM identity map.  The
        # TimestampMixin server-side ``onupdate`` value is otherwise expired
        # after the scheduling flush and a synchronous response projection
        # would attempt an implicit (and invalid) async lazy load.
        rule.updated_at = datetime.now(UTC)
        if not rule.active:
            rule.state = TaskReminderRuleState.disabled
            return
        if task.status == TaskStatus.done or task.cancelled_at is not None:
            rule.state = TaskReminderRuleState.disabled
            return

        targets = self._recipient_participants(task, rule)
        if not targets:
            rule.state = TaskReminderRuleState.pending_recipient
            return
        active_user_ids = await self._active_workspace_user_ids(
            rule.workspace_id, {participant.user_id for participant in targets}
        )
        targets = [item for item in targets if item.user_id in active_user_ids]
        if not targets:
            rule.state = TaskReminderRuleState.pending_recipient
            return

        scheduled = 0
        missing_deadline = False
        external_unavailable = rule.channel != TaskReminderChannel.in_app
        for participant in targets:
            deadline = self._deadline_for(task, participant, rule.relative_to)
            if deadline is None:
                missing_deadline = True
                continue
            deadline_utc = self._as_utc(deadline)
            scheduled_for = deadline_utc - timedelta(minutes=rule.offset_minutes_before)
            delivery_status = (
                TaskReminderDeliveryStatus.unavailable
                if external_unavailable
                else TaskReminderDeliveryStatus.pending
            )
            dedupe_key = (
                f"task-reminder:{rule.id}:v{rule.version}:{participant.user_id}:"
                f"{deadline_utc.isoformat()}:{rule.channel.value}"
            )
            delivery = TaskReminderOutbox(
                workspace_id=rule.workspace_id,
                matter_id=rule.matter_id,
                task_id=rule.task_id,
                rule_id=rule.id,
                rule_version=rule.version,
                task_version=task.version,
                recipient_user_id=participant.user_id,
                deadline_at=deadline_utc,
                scheduled_for=scheduled_for,
                channel=rule.channel,
                status=delivery_status,
                dedupe_key=dedupe_key,
                correlation_id=context.correlation_id,
                attempts=0,
                available_at=scheduled_for,
                last_error_code="channel_not_configured" if external_unavailable else None,
            )
            rule.deliveries.append(delivery)
            scheduled += 1
        if not scheduled:
            rule.state = TaskReminderRuleState.pending_due
            return
        if external_unavailable:
            rule.state = TaskReminderRuleState.unavailable
        elif missing_deadline:
            # Group rules can be partially schedulable (for example, only one
            # assignee has a personal due date). Keep the valid intents but
            # expose that at least one target still lacks its deadline.
            rule.state = TaskReminderRuleState.pending_due
        else:
            rule.state = TaskReminderRuleState.scheduled
        await self.db.flush()
        if not external_unavailable:
            queue_task_reminder_wakeup(self.db, workspace_id=rule.workspace_id)

    @staticmethod
    def _recipient_participants(
        task: Task, rule: TaskReminderRule
    ) -> list[TaskParticipant]:
        active = [item for item in task.participants if item.active]
        if rule.recipient == TaskReminderRecipient.owner:
            return [item for item in active if item.role == TaskParticipantRole.owner]
        if rule.recipient == TaskReminderRecipient.assignees:
            return [item for item in active if item.role == TaskParticipantRole.assignee]
        if rule.recipient == TaskReminderRecipient.required_assignees:
            return [
                item
                for item in active
                if item.role == TaskParticipantRole.assignee and item.required_for_completion
            ]
        if rule.recipient == TaskReminderRecipient.reviewer:
            return [item for item in active if item.role == TaskParticipantRole.reviewer]
        return [item for item in active if item.user_id == rule.recipient_user_id]

    @staticmethod
    def _deadline_for(
        task: Task,
        participant: TaskParticipant,
        relative_to: TaskReminderRelativeTo,
    ) -> datetime | None:
        if relative_to == TaskReminderRelativeTo.general_due:
            return task.due_at
        if relative_to == TaskReminderRelativeTo.review_due:
            return task.review_due_at
        return participant.due_at

    async def _cancel_open_deliveries(self, rule: TaskReminderRule, *, reason: str) -> int:
        now = datetime.now(UTC)
        cancelled = 0
        for delivery in rule.deliveries:
            if delivery.status not in OPEN_DELIVERY_STATUSES:
                continue
            delivery.status = TaskReminderDeliveryStatus.cancelled
            delivery.cancelled_at = now
            delivery.last_error_code = reason[:80]
            delivery.claim_token = None
            delivery.claimed_at = None
            cancelled += 1
        return cancelled

    async def _get_task(
        self, workspace_id: UUID, task_id: UUID, *, lock: bool = False
    ) -> Task | None:
        statement = (
            select(Task)
            .join(Matter, Matter.id == Task.matter_id)
            .options(selectinload(Task.participants))
            .where(
                Task.id == task_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if lock:
            statement = statement.with_for_update()
        return (await self.db.execute(statement)).scalars().unique().one_or_none()

    async def _get_visible_task(
        self,
        workspace_id: UUID,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        allow_workspace_access: bool,
        lock: bool = False,
    ) -> Task | None:
        return await TaskService(self.db).get_for_actor(
            task_id,
            workspace_id,
            actor_user_id=actor_user_id,
            allow_workspace=allow_workspace_access,
            lock=lock,
        )

    async def _get_visible_task_for_context(
        self,
        workspace_id: UUID,
        task_id: UUID,
        *,
        context: TaskReminderMutationContext,
        allow_workspace_access: bool,
        lock: bool = False,
    ) -> Task | None:
        if context.session_type == "system":
            return await self._get_task(workspace_id, task_id, lock=lock)
        if context.actor_user_id is None:
            return None
        return await self._get_visible_task(
            workspace_id,
            task_id,
            actor_user_id=context.actor_user_id,
            allow_workspace_access=allow_workspace_access,
            lock=lock,
        )

    async def _get_rule(
        self,
        workspace_id: UUID,
        task_id: UUID,
        rule_id: UUID,
        *,
        lock: bool = False,
    ) -> TaskReminderRule | None:
        statement = (
            select(TaskReminderRule)
            .join(Matter, Matter.id == TaskReminderRule.matter_id)
            .options(*self._rule_options())
            .where(
                TaskReminderRule.id == rule_id,
                TaskReminderRule.workspace_id == workspace_id,
                TaskReminderRule.task_id == task_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if lock:
            statement = statement.with_for_update()
        return (await self.db.execute(statement)).scalars().unique().one_or_none()

    async def _validate_specific_recipient(
        self,
        task: Task,
        workspace_id: UUID,
        recipient: TaskReminderRecipient,
        recipient_user_id: UUID | None,
    ) -> None:
        if recipient != TaskReminderRecipient.specific_user:
            if recipient_user_id is not None:
                raise self._unprocessable(
                    "TASK_REMINDER_RECIPIENT_INVALID",
                    "recipientUserId só pode ser usado com specific_user.",
                )
            return
        if recipient_user_id is None or not any(
            item.active and item.user_id == recipient_user_id for item in task.participants
        ):
            raise self._unprocessable(
                "TASK_REMINDER_RECIPIENT_NOT_PARTICIPANT",
                "O destinatário específico deve ser participante ativo da tarefa.",
            )
        active_ids = await self._active_workspace_user_ids(workspace_id, {recipient_user_id})
        if recipient_user_id not in active_ids:
            raise self._unprocessable(
                "TASK_REMINDER_RECIPIENT_NOT_ACTIVE",
                "O destinatário específico deve ser membro humano ativo do workspace.",
            )

    async def _active_workspace_user_ids(
        self, workspace_id: UUID, user_ids: set[UUID]
    ) -> set[UUID]:
        if not user_ids:
            return set()
        rows = await self.db.scalars(
            select(WorkspaceMembership.user_id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id.in_(user_ids),
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        return set(rows.all())

    async def _require_unique_semantics(
        self,
        *,
        task_id: UUID,
        relative_to: TaskReminderRelativeTo,
        offset_minutes_before: int,
        channel: TaskReminderChannel,
        recipient_key: str,
        exclude_rule_id: UUID | None = None,
    ) -> None:
        predicates = [
            TaskReminderRule.task_id == task_id,
            TaskReminderRule.relative_to == relative_to,
            TaskReminderRule.offset_minutes_before == offset_minutes_before,
            TaskReminderRule.channel == channel,
            TaskReminderRule.recipient_key == recipient_key,
            TaskReminderRule.active.is_(True),
        ]
        if exclude_rule_id is not None:
            predicates.append(TaskReminderRule.id != exclude_rule_id)
        if await self.db.scalar(select(TaskReminderRule.id).where(*predicates)) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TASK_REMINDER_DUPLICATE",
                    "message": "Já existe uma regra ativa com a mesma semântica.",
                },
            )

    async def _record_event(
        self,
        rule: TaskReminderRule,
        *,
        context: TaskReminderMutationContext,
        event_type: TaskReminderEventType,
        before: dict | None,
        after: dict | None,
        reason: str,
        outbox_id: UUID | None = None,
    ) -> None:
        self.db.add(
            TaskReminderEvent(
                workspace_id=rule.workspace_id,
                matter_id=rule.matter_id,
                task_id=rule.task_id,
                rule_id=rule.id,
                outbox_id=outbox_id,
                actor_user_id=context.actor_user_id,
                event_type=event_type,
                before_data=before,
                after_data=after,
                reason=reason,
                correlation_id=context.correlation_id,
                session_type=context.session_type,
                administrative_session_id=context.administrative_session_id,
                support_grant_id=context.support_grant_id,
                resource_version_before=before.get("version") if before else None,
                resource_version_after=after.get("version") if after else None,
            )
        )
        self.db.add(
            KlmAuditEvent(
                workspace_id=rule.workspace_id,
                actor_user_id=context.actor_user_id,
                actor_type="human" if context.actor_user_id else "system",
                actor_label=str(context.actor_user_id or "task_reminder_worker"),
                event_type=f"task_reminder_{event_type.value}",
                entity_type="task_reminder_rule",
                entity_id=rule.id,
                summary=reason,
                correlation_id=context.correlation_id,
                before_data=before,
                after_data=after,
            )
        )
        await self.db.flush()

    async def record_delivery_event(
        self,
        rule: TaskReminderRule,
        delivery: TaskReminderOutbox,
        *,
        delivered: bool,
        reason: str,
    ) -> None:
        event_type = (
            TaskReminderEventType.delivered
            if delivered
            else TaskReminderEventType.delivery_unavailable
        )
        await self._record_event(
            rule,
            context=TaskReminderMutationContext.system(prefix="task-reminder-delivery"),
            event_type=event_type,
            before=None,
            after={
                "ruleId": str(rule.id),
                "outboxId": str(delivery.id),
                "recipientUserId": str(delivery.recipient_user_id),
                "channel": delivery.channel.value,
                "status": delivery.status.value,
                "notificationId": (
                    str(delivery.notification_id) if delivery.notification_id else None
                ),
                "version": rule.version,
            },
            reason=reason,
            outbox_id=delivery.id,
        )

    @staticmethod
    def snapshot(rule: TaskReminderRule) -> dict:
        return {
            "id": str(rule.id),
            "workspaceId": str(rule.workspace_id),
            "matterId": str(rule.matter_id),
            "taskId": str(rule.task_id),
            "relativeTo": rule.relative_to.value,
            "offsetMinutesBefore": rule.offset_minutes_before,
            "channel": rule.channel.value,
            "recipient": rule.recipient.value,
            "recipientUserId": str(rule.recipient_user_id) if rule.recipient_user_id else None,
            "state": rule.state.value,
            "active": rule.active,
            "version": rule.version,
            "deletedAt": rule.deleted_at.isoformat() if rule.deleted_at else None,
        }

    @staticmethod
    def presentation(rule: TaskReminderRule) -> dict:
        active_deliveries = [
            item
            for item in rule.deliveries
            if item.rule_version == rule.version
            and item.status
            in {
                TaskReminderDeliveryStatus.pending,
                TaskReminderDeliveryStatus.processing,
                TaskReminderDeliveryStatus.failed,
                TaskReminderDeliveryStatus.delivered,
                TaskReminderDeliveryStatus.unavailable,
            }
        ]
        active_deliveries.sort(key=lambda item: (item.scheduled_for, str(item.id)))
        pending_times = [
            item.scheduled_for
            for item in active_deliveries
            if item.status
            in {
                TaskReminderDeliveryStatus.pending,
                TaskReminderDeliveryStatus.processing,
                TaskReminderDeliveryStatus.failed,
            }
        ]
        return {
            "id": str(rule.id),
            "workspace_id": str(rule.workspace_id),
            "matter_id": str(rule.matter_id),
            "task_id": str(rule.task_id),
            "relative_to": rule.relative_to.value,
            "offset_minutes_before": rule.offset_minutes_before,
            "channel": rule.channel.value,
            "recipient": rule.recipient.value,
            "recipient_user_id": (
                str(rule.recipient_user_id) if rule.recipient_user_id else None
            ),
            "state": rule.state.value,
            "active": rule.active,
            "version": rule.version,
            "delivery_available": rule.channel == TaskReminderChannel.in_app,
            "unavailability_reason": (
                None
                if rule.channel == TaskReminderChannel.in_app
                else "channel_not_configured"
            ),
            "next_scheduled_at": min(pending_times).isoformat() if pending_times else None,
            "deliveries": [
                {
                    "id": str(item.id),
                    "recipient_user_id": str(item.recipient_user_id),
                    "deadline_at": item.deadline_at.isoformat(),
                    "scheduled_for": item.scheduled_for.isoformat(),
                    "channel": item.channel.value,
                    "status": item.status.value,
                    "attempts": item.attempts,
                    "delivered_at": item.delivered_at.isoformat() if item.delivered_at else None,
                    "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
                    "last_error_code": item.last_error_code,
                    "notification_id": (
                        str(item.notification_id) if item.notification_id else None
                    ),
                }
                for item in active_deliveries
            ],
            "created_at": rule.created_at.isoformat(),
            "updated_at": rule.updated_at.isoformat(),
            "deleted_at": rule.deleted_at.isoformat() if rule.deleted_at else None,
        }

    async def _receipt_replay(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        idempotency_key: str,
        operation: str,
        request_sha256: str,
    ) -> dict | None:
        receipt = await self.db.scalar(
            select(TaskReminderCommandReceipt).where(
                TaskReminderCommandReceipt.workspace_id == workspace_id,
                TaskReminderCommandReceipt.idempotency_key == idempotency_key,
            )
        )
        if receipt is None:
            return None
        if (
            receipt.task_id != task_id
            or receipt.operation != operation
            or receipt.request_sha256 != request_sha256
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TASK_REMINDER_IDEMPOTENCY_CONFLICT",
                    "message": "Idempotency-Key já foi usada com outra mutação.",
                },
            )
        return dict(receipt.response_data)

    async def _store_receipt(
        self,
        *,
        workspace_id: UUID,
        matter_id: UUID,
        task_id: UUID,
        rule_id: UUID,
        idempotency_key: str,
        operation: str,
        request_sha256: str,
        response_status: int,
        response_data: dict,
    ) -> None:
        self.db.add(
            TaskReminderCommandReceipt(
                workspace_id=workspace_id,
                matter_id=matter_id,
                task_id=task_id,
                rule_id=rule_id,
                idempotency_key=idempotency_key,
                operation=operation,
                request_sha256=request_sha256,
                response_status=response_status,
                response_data=response_data,
            )
        )
        await self.db.flush()

    @staticmethod
    def _request_digest(
        operation: str, task_id: UUID, rule_id: UUID | None, payload: dict
    ) -> str:
        canonical = {
            "operation": operation,
            "taskId": str(task_id),
            "ruleId": str(rule_id) if rule_id else None,
            "payload": payload,
        }
        return hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _normalize_idempotency_key(value: str) -> str:
        normalized = value.strip()
        if not 8 <= len(normalized) <= 180:
            raise TaskReminderService._unprocessable(
                "TASK_REMINDER_IDEMPOTENCY_KEY_INVALID",
                "Idempotency-Key deve ter entre 8 e 180 caracteres.",
            )
        return normalized

    @staticmethod
    def _recipient_key(
        recipient: TaskReminderRecipient, recipient_user_id: UUID | None
    ) -> str:
        return (
            f"specific_user:{recipient_user_id}"
            if recipient == TaskReminderRecipient.specific_user
            else recipient.value
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise RuntimeError("Prazo persistido sem timezone explícito.")
        return value.astimezone(UTC)

    @staticmethod
    def _bump(rule: TaskReminderRule) -> None:
        rule.version += 1
        rule.updated_at = datetime.now(UTC)

    @staticmethod
    def _require_task_version(task: Task, expected_version: int) -> None:
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
    def _require_rule_version(rule: TaskReminderRule, expected_version: int) -> None:
        if rule.version != expected_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TASK_REMINDER_VERSION_CONFLICT",
                    "message": "A regra de lembrete foi alterada por outra operação.",
                    "expectedVersion": expected_version,
                    "currentVersion": rule.version,
                },
            )

    @staticmethod
    def _require_open_task(task: Task) -> None:
        if task.status == TaskStatus.done or task.cancelled_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TASK_REMINDER_TASK_TERMINAL",
                    "message": "Reabra a tarefa antes de configurar lembretes.",
                },
            )

    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TASK_REMINDER_NOT_FOUND", "message": "Regra não encontrada."},
        )

    @staticmethod
    def _unprocessable(code: str, message: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": code, "message": message},
        )


__all__ = ["TaskReminderMutationContext", "TaskReminderService"]
