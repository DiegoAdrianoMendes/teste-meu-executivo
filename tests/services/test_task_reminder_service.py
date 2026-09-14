from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.deps import AuthContext
from app.db.append_only import ImmutableRecordError
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.client import Client
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.matter import Matter, PracticeArea
from app.models.task import TaskParticipantRole, TaskStatus
from app.models.task_reminder import (
    TaskReminderChannel,
    TaskReminderCommandReceipt,
    TaskReminderDeliveryStatus,
    TaskReminderEvent,
    TaskReminderOutbox,
    TaskReminderRecipient,
    TaskReminderRelativeTo,
    TaskReminderRule,
    TaskReminderRuleState,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.task import (
    TaskCommand,
    TaskCommandRequest,
    TaskCreate,
    TaskParticipantsReplace,
    TaskParticipantWrite,
    TaskUpdate,
)
from app.schemas.task_reminder import TaskReminderRuleCreate
from app.services.entitlement_service import CurrentStockLimitExceededError
from app.services.task_reminder_service import (
    TaskReminderMutationContext,
    TaskReminderService,
)
from app.services.task_service import TaskMutationContext, TaskService


async def _task(db, auth: AuthContext, *, due_at: datetime | None):
    client = Client(workspace_id=auth.workspace_id, name=f"Cliente {uuid4().hex[:8]}")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=auth.workspace_id,
        client_id=client.id,
        demand_type="Prazo com lembrete",
        practice_area=PracticeArea.civel,
    )
    db.add(matter)
    await db.flush()
    task = await TaskService(db).create(
        matter.id,
        TaskCreate(title="Protocolar manifestação", due_at=due_at),
        auth.workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth.user_id,
            correlation_id=f"test:task-reminder:create-task:{uuid4()}",
        ),
    )
    return task


def _context(auth: AuthContext, label: str) -> TaskReminderMutationContext:
    return TaskReminderMutationContext(
        actor_user_id=auth.user_id,
        correlation_id=f"test:task-reminder:{label}:{uuid4()}",
    )


async def _member(db, auth: AuthContext, label: str) -> User:
    user = User(
        name=label,
        email=f"{label.lower()}-{uuid4().hex[:8]}@example.com",
        initials=label[:2].upper(),
        color="#334155",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=auth.workspace_id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
        )
    )
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_service_denies_workspace_member_without_task_visibility(
    db_session, auth_context: AuthContext
) -> None:
    task = await _task(
        db_session,
        auth_context,
        due_at=datetime.now(UTC) + timedelta(days=1),
    )
    outsider = await _member(db_session, auth_context, "Sem vinculo")
    service = TaskReminderService(db_session)

    with pytest.raises(HTTPException) as listed:
        await service.list_rules(
            workspace_id=auth_context.workspace_id,
            task_id=task.id,
            actor_user_id=outsider.id,
        )
    assert listed.value.status_code == 404

    with pytest.raises(HTTPException) as created:
        await service.create_rule(
            workspace_id=auth_context.workspace_id,
            task_id=task.id,
            data=_payload(task),
            idempotency_key="service-reminder-bola-outsider",
            context=TaskReminderMutationContext(
                actor_user_id=outsider.id,
                correlation_id="test:task-reminder:bola-outsider",
            ),
        )
    assert created.value.status_code == 404

    assert await service.list_rules(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        actor_user_id=outsider.id,
        allow_workspace_access=True,
    ) == []


def _payload(task, **updates) -> TaskReminderRuleCreate:
    values = {
        "expected_version": task.version,
        "relative_to": TaskReminderRelativeTo.general_due,
        "offset_minutes_before": 60,
        "channel": TaskReminderChannel.in_app,
        "recipient": TaskReminderRecipient.owner,
    }
    values.update(updates)
    return TaskReminderRuleCreate(**values)


@pytest.mark.asyncio
async def test_rule_schedules_exact_utc_offset_across_dst_and_replays_idempotently(
    db_session, auth_context: AuthContext
) -> None:
    # 01:30 at UTC-04 is deliberately in the US DST fall-back window. The
    # task contract persists an absolute instant, so subtracting 90 minutes is
    # deterministic and cannot pick a different wall-clock fold.
    deadline = datetime(2026, 11, 1, 1, 30, tzinfo=timezone(timedelta(hours=-4)))
    task = await _task(db_session, auth_context, due_at=deadline)
    service = TaskReminderService(db_session)
    payload = _payload(task, offset_minutes_before=90)

    created, replayed = await service.create_rule(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        data=payload,
        idempotency_key="dst-rule-create-001",
        context=_context(auth_context, "dst"),
    )
    replay, was_replayed = await service.create_rule(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        data=payload,
        idempotency_key="dst-rule-create-001",
        context=_context(auth_context, "dst-replay"),
    )

    assert replayed is False
    assert was_replayed is True
    assert replay == created
    assert created["state"] == TaskReminderRuleState.scheduled.value
    assert len(created["deliveries"]) == 1
    delivery = created["deliveries"][0]
    scheduled_for = datetime.fromisoformat(delivery["scheduled_for"])
    deadline_at = datetime.fromisoformat(delivery["deadline_at"])
    assert deadline_at == deadline.astimezone(UTC)
    assert deadline_at - scheduled_for == timedelta(minutes=90)
    assert len((await db_session.scalars(select(TaskReminderRule))).all()) == 1
    assert len((await db_session.scalars(select(TaskReminderOutbox))).all()) == 1


@pytest.mark.asyncio
async def test_missing_due_is_pending_and_external_channels_are_honestly_unavailable(
    db_session, auth_context: AuthContext
) -> None:
    undated = await _task(db_session, auth_context, due_at=None)
    service = TaskReminderService(db_session)
    pending, _ = await service.create_rule(
        workspace_id=auth_context.workspace_id,
        task_id=undated.id,
        data=_payload(undated),
        idempotency_key="missing-due-rule-001",
        context=_context(auth_context, "missing-due"),
    )
    assert pending["state"] == TaskReminderRuleState.pending_due.value
    assert pending["next_scheduled_at"] is None
    assert pending["deliveries"] == []

    dated = await _task(db_session, auth_context, due_at=datetime.now(UTC) + timedelta(days=1))
    unavailable, _ = await service.create_rule(
        workspace_id=auth_context.workspace_id,
        task_id=dated.id,
        data=_payload(dated, channel=TaskReminderChannel.email),
        idempotency_key="email-unavailable-rule-001",
        context=_context(auth_context, "email"),
    )
    assert unavailable["state"] == TaskReminderRuleState.unavailable.value
    assert unavailable["delivery_available"] is False
    assert unavailable["unavailability_reason"] == "channel_not_configured"
    assert unavailable["deliveries"][0]["status"] == "unavailable"
    assert unavailable["deliveries"][0]["last_error_code"] == "channel_not_configured"


@pytest.mark.asyncio
async def test_due_change_reschedules_and_cancel_reopen_invalidates_prior_intents(
    db_session, auth_context: AuthContext
) -> None:
    first_due = datetime.now(UTC) + timedelta(days=2)
    task = await _task(db_session, auth_context, due_at=first_due)
    reminder_service = TaskReminderService(db_session)
    created, _ = await reminder_service.create_rule(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        data=_payload(task),
        idempotency_key="reschedule-rule-001",
        context=_context(auth_context, "reschedule-create"),
    )
    first_rule_version = created["version"]
    first_outbox_id = created["deliveries"][0]["id"]

    second_due = first_due + timedelta(hours=6)
    task = await TaskService(db_session).update(
        task.id,
        TaskUpdate(expected_version=task.version, due_at=second_due),
        auth_context.workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth_context.user_id,
            correlation_id="test:task-reminder:due-change",
        ),
    )
    assert task is not None
    # Reload through the public service so selectin collections reflect both
    # the cancelled generation and the active one.
    current = (await reminder_service.list_rules(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        actor_user_id=auth_context.user_id,
    ))[0]
    assert current["version"] == first_rule_version + 1
    assert current["deliveries"][0]["scheduled_for"] == (
        second_due - timedelta(minutes=60)
    ).isoformat()
    first_outbox = await db_session.get(TaskReminderOutbox, first_outbox_id)
    assert first_outbox is not None
    assert first_outbox.status == TaskReminderDeliveryStatus.cancelled

    task = await TaskService(db_session).execute_command(
        task.id,
        TaskCommand.cancel,
        TaskCommandRequest(expected_version=task.version, reason="Prazo cancelado pelo cliente."),
        auth_context.workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth_context.user_id,
            correlation_id="test:task-reminder:cancel",
        ),
    )
    assert task is not None and task.status == TaskStatus.done
    cancelled = (await reminder_service.list_rules(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        actor_user_id=auth_context.user_id,
    ))[0]
    assert cancelled["state"] == TaskReminderRuleState.disabled.value
    assert cancelled["deliveries"] == []

    task = await TaskService(db_session).execute_command(
        task.id,
        TaskCommand.reopen,
        TaskCommandRequest(expected_version=task.version, reason="Prazo retomado pelo cliente."),
        auth_context.workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth_context.user_id,
            correlation_id="test:task-reminder:reopen",
        ),
    )
    assert task is not None and task.status == TaskStatus.pending
    reopened = (await reminder_service.list_rules(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        actor_user_id=auth_context.user_id,
    ))[0]
    assert reopened["state"] == TaskReminderRuleState.scheduled.value
    assert len(reopened["deliveries"]) == 1


@pytest.mark.asyncio
async def test_entitlement_caps_active_rules_per_task(
    db_session, auth_context: AuthContext
) -> None:
    task = await _task(
        db_session, auth_context, due_at=datetime.now(UTC) + timedelta(days=1)
    )
    plan = Plan(
        name=f"Reminder limit {uuid4().hex[:8]}",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=10,
        max_matters=None,
        max_tokens_per_month=1000,
        max_ai_agents=1,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()
    db_session.add_all(
        [
            Subscription(
                workspace_id=auth_context.workspace_id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=0,
                currency="BRL",
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.task_reminder_rules,
                enabled=True,
                limit_quantity=1,
                hard_limit=True,
            ),
        ]
    )
    await db_session.flush()
    service = TaskReminderService(db_session)
    await service.create_rule(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        data=_payload(task),
        idempotency_key="entitled-rule-first",
        context=_context(auth_context, "quota-first"),
    )
    with pytest.raises(CurrentStockLimitExceededError) as raised:
        await service.create_rule(
            workspace_id=auth_context.workspace_id,
            task_id=task.id,
            data=_payload(task, offset_minutes_before=120),
            idempotency_key="entitled-rule-second",
            context=_context(auth_context, "quota-second"),
        )
    assert raised.value.entitlement == EntitlementKey.task_reminder_rules


@pytest.mark.asyncio
async def test_specific_recipient_must_be_active_task_participant(
    db_session, auth_context: AuthContext
) -> None:
    task = await _task(
        db_session, auth_context, due_at=datetime.now(UTC) + timedelta(days=1)
    )
    with pytest.raises(HTTPException) as raised:
        await TaskReminderService(db_session).create_rule(
            workspace_id=auth_context.workspace_id,
            task_id=task.id,
            data=_payload(
                task,
                recipient=TaskReminderRecipient.specific_user,
                recipient_user_id=uuid4(),
            ),
            idempotency_key="specific-invalid-rule",
            context=_context(auth_context, "specific-invalid"),
        )
    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "TASK_REMINDER_RECIPIENT_NOT_PARTICIPANT"


@pytest.mark.asyncio
async def test_removing_specific_recipient_cancels_pending_delivery_transactionally(
    db_session, auth_context: AuthContext
) -> None:
    task = await _task(
        db_session, auth_context, due_at=datetime.now(UTC) + timedelta(days=1)
    )
    assignee = await _member(db_session, auth_context, "Executor")
    task_service = TaskService(db_session)
    task = await task_service.replace_participants(
        task.id,
        TaskParticipantsReplace(
            expected_version=task.version,
            reason="Incluir executor para o lembrete.",
            participants=[
                TaskParticipantWrite(
                    user_id=auth_context.user_id,
                    role=TaskParticipantRole.owner,
                ),
                TaskParticipantWrite(
                    user_id=assignee.id,
                    role=TaskParticipantRole.assignee,
                    due_at=task.due_at,
                ),
            ],
        ),
        auth_context.workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth_context.user_id,
            correlation_id="test:task-reminder:add-recipient",
        ),
    )
    assert task is not None
    service = TaskReminderService(db_session)
    created, _ = await service.create_rule(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        data=_payload(
            task,
            relative_to=TaskReminderRelativeTo.personal_due,
            recipient=TaskReminderRecipient.specific_user,
            recipient_user_id=assignee.id,
        ),
        idempotency_key="specific-recipient-remove-rule",
        context=_context(auth_context, "specific-recipient-create"),
    )
    delivery_id = created["deliveries"][0]["id"]

    task = await task_service.replace_participants(
        task.id,
        TaskParticipantsReplace(
            expected_version=task.version,
            reason="Remover executor da tarefa.",
            participants=[
                TaskParticipantWrite(
                    user_id=auth_context.user_id,
                    role=TaskParticipantRole.owner,
                )
            ],
        ),
        auth_context.workspace_id,
        context=TaskMutationContext(
            actor_user_id=auth_context.user_id,
            correlation_id="test:task-reminder:remove-recipient",
        ),
    )
    assert task is not None
    current = (
        await service.list_rules(
            workspace_id=auth_context.workspace_id,
            task_id=task.id,
            actor_user_id=auth_context.user_id,
        )
    )[0]
    delivery = await db_session.get(TaskReminderOutbox, delivery_id)

    assert current["state"] == TaskReminderRuleState.pending_recipient.value
    assert current["deliveries"] == []
    assert delivery is not None
    assert delivery.status == TaskReminderDeliveryStatus.cancelled
    assert delivery.last_error_code == "task_participants_changed"


@pytest.mark.asyncio
async def test_rule_events_and_command_receipts_are_append_only(
    db_session, auth_context: AuthContext
) -> None:
    task = await _task(
        db_session, auth_context, due_at=datetime.now(UTC) + timedelta(days=1)
    )
    await TaskReminderService(db_session).create_rule(
        workspace_id=auth_context.workspace_id,
        task_id=task.id,
        data=_payload(task),
        idempotency_key="append-only-reminder-rule",
        context=_context(auth_context, "append-only"),
    )
    task_id = task.id
    await db_session.commit()

    event = await db_session.scalar(
        select(TaskReminderEvent).where(TaskReminderEvent.task_id == task_id)
    )
    assert event is not None
    event.reason = "Tentativa de adulteração"
    with pytest.raises(ImmutableRecordError, match="immutable"):
        await db_session.flush()
    await db_session.rollback()

    receipt = await db_session.scalar(
        select(TaskReminderCommandReceipt).where(
            TaskReminderCommandReceipt.task_id == task_id
        )
    )
    assert receipt is not None
    receipt.response_status = 500
    with pytest.raises(ImmutableRecordError, match="immutable"):
        await db_session.flush()
    await db_session.rollback()
