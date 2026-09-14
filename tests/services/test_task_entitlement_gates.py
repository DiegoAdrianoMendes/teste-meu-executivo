from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
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
from app.models.task import Task, TaskParticipantRole, TaskReviewRound
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.task import (
    TaskCommand,
    TaskCommandRequest,
    TaskCreate,
    TaskParticipantWrite,
)
from app.services.entitlement_service import CurrentStockLimitExceededError
from app.services.task_service import TaskMutationContext, TaskService

_TASK_LIMITS = {
    EntitlementKey.active_tasks: 10,
    EntitlementKey.task_participants: 10,
    EntitlementKey.task_reviews: 1,
    EntitlementKey.review_rounds: 10,
    EntitlementKey.task_reminder_rules: 10,
}


async def _seed_task_entitlements(
    db: AsyncSession,
    workspace_id,
    **overrides: int,
) -> dict[EntitlementKey, PlanEntitlement]:
    plan = Plan(
        name=f"Plano tarefas {uuid4().hex[:8]}",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=1_000_000,
        max_ai_agents=100,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db.add(plan)
    await db.flush()
    db.add(
        Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    limits = dict(_TASK_LIMITS)
    limits.update({EntitlementKey(key): value for key, value in overrides.items()})
    rows = {
        entitlement: PlanEntitlement(
            plan_id=plan.id,
            entitlement=entitlement,
            enabled=True,
            limit_quantity=limit,
            hard_limit=True,
        )
        for entitlement, limit in limits.items()
    }
    db.add_all(rows.values())
    await db.flush()
    return rows


async def _matter(db: AsyncSession, workspace_id, label: str) -> Matter:
    client = Client(workspace_id=workspace_id, name=f"Cliente {label}")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type=label,
        practice_area=PracticeArea.civel,
    )
    db.add(matter)
    await db.flush()
    return matter


async def _member(db: AsyncSession, workspace_id, label: str) -> User:
    user = User(
        name=label,
        email=f"task-quota-{uuid4().hex[:12]}@example.com",
        initials="TQ",
        color="#334155",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
        )
    )
    await db.flush()
    return user


def _context(actor_id, suffix: str) -> TaskMutationContext:
    return TaskMutationContext(
        actor_user_id=actor_id,
        correlation_id=f"test:task-entitlement:{suffix}:{uuid4()}",
    )


@pytest.mark.asyncio
async def test_active_task_quota_zero_then_one_allows_only_one_task(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    rows = await _seed_task_entitlements(
        db_session,
        auth_context.workspace_id,
        active_tasks=0,
    )
    first_matter = await _matter(db_session, auth_context.workspace_id, "Primeira")
    service = TaskService(db_session)

    with pytest.raises(CurrentStockLimitExceededError) as zero_limit:
        await service.create(
            first_matter.id,
            TaskCreate(title="Bloqueada em quota zero"),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "active-zero"),
        )
    assert zero_limit.value.entitlement == EntitlementKey.active_tasks

    rows[EntitlementKey.active_tasks].limit_quantity = 1
    await db_session.flush()
    created = await service.create(
        first_matter.id,
        TaskCreate(title="Única tarefa ativa"),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "active-one"),
    )
    second_matter = await _matter(db_session, auth_context.workspace_id, "Segunda")
    with pytest.raises(CurrentStockLimitExceededError) as one_limit:
        await service.create(
            second_matter.id,
            TaskCreate(title="Excede quota um"),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "active-two"),
        )
    assert one_limit.value.entitlement == EntitlementKey.active_tasks
    assert int(await db_session.scalar(select(func.count(Task.id))) or 0) == 1
    assert created.title == "Única tarefa ativa"


@pytest.mark.asyncio
async def test_participant_and_reviewer_quotas_enforce_final_task_shape(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    rows = await _seed_task_entitlements(
        db_session,
        auth_context.workspace_id,
        task_participants=1,
        task_reviews=0,
    )
    assignee = await _member(db_session, auth_context.workspace_id, "Executor quota")
    reviewer = await _member(db_session, auth_context.workspace_id, "Revisor quota")
    service = TaskService(db_session)

    participant_matter = await _matter(db_session, auth_context.workspace_id, "Participantes")
    with pytest.raises(CurrentStockLimitExceededError) as participant_limit:
        await service.create(
            participant_matter.id,
            TaskCreate(
                title="Dois participantes",
                participants=[
                    TaskParticipantWrite(
                        user_id=auth_context.user_id,
                        role=TaskParticipantRole.owner,
                    ),
                    TaskParticipantWrite(
                        user_id=assignee.id,
                        role=TaskParticipantRole.assignee,
                    ),
                ],
            ),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "participant-zero"),
        )
    assert participant_limit.value.entitlement == EntitlementKey.task_participants

    rows[EntitlementKey.task_participants].limit_quantity = 2
    await db_session.flush()
    review_matter = await _matter(db_session, auth_context.workspace_id, "Reviewer")
    review_payload = TaskCreate(
        title="Tarefa revisável",
        review_required=True,
        participants=[
            TaskParticipantWrite(
                user_id=auth_context.user_id,
                role=TaskParticipantRole.owner,
            ),
            TaskParticipantWrite(
                user_id=reviewer.id,
                role=TaskParticipantRole.reviewer,
            ),
        ],
    )
    with pytest.raises(CurrentStockLimitExceededError) as review_zero:
        await service.create(
            review_matter.id,
            review_payload,
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "review-zero"),
        )
    assert review_zero.value.entitlement == EntitlementKey.task_reviews

    rows[EntitlementKey.task_reviews].limit_quantity = 1
    await db_session.flush()
    task = await service.create(
        review_matter.id,
        review_payload,
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "review-one"),
    )
    assert len([item for item in task.participants if item.active]) == 2


@pytest.mark.asyncio
async def test_review_round_quota_zero_then_one_blocks_second_round(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    rows = await _seed_task_entitlements(
        db_session,
        auth_context.workspace_id,
        task_participants=2,
        task_reviews=1,
        review_rounds=0,
    )
    reviewer = await _member(db_session, auth_context.workspace_id, "Revisor rodada")
    matter = await _matter(db_session, auth_context.workspace_id, "Rodadas")
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Revisão limitada",
            review_required=True,
            participants=[
                TaskParticipantWrite(
                    user_id=auth_context.user_id,
                    role=TaskParticipantRole.owner,
                ),
                TaskParticipantWrite(
                    user_id=reviewer.id,
                    role=TaskParticipantRole.reviewer,
                ),
            ],
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "round-create"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.start,
        TaskCommandRequest(expected_version=1),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "round-start"),
    )

    with pytest.raises(CurrentStockLimitExceededError) as round_zero:
        await service.execute_command(
            task.id,
            TaskCommand.submit_for_review,
            TaskCommandRequest(expected_version=2),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "round-zero"),
        )
    assert round_zero.value.entitlement == EntitlementKey.review_rounds

    rows[EntitlementKey.review_rounds].limit_quantity = 1
    await db_session.flush()
    task = await service.execute_command(
        task.id,
        TaskCommand.submit_for_review,
        TaskCommandRequest(expected_version=2),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "round-one"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.request_changes,
        TaskCommandRequest(
            expected_version=3,
            reason="Ajustar fundamentação antes de reenviar.",
        ),
        auth_context.workspace_id,
        context=_context(reviewer.id, "round-changes"),
    )
    with pytest.raises(CurrentStockLimitExceededError) as second_round:
        await service.execute_command(
            task.id,
            TaskCommand.submit_for_review,
            TaskCommandRequest(expected_version=4),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "round-two"),
        )
    assert second_round.value.entitlement == EntitlementKey.review_rounds
    assert (
        int(
            await db_session.scalar(
                select(func.count(TaskReviewRound.id)).where(TaskReviewRound.task_id == task.id)
            )
            or 0
        )
        == 1
    )


@pytest.mark.asyncio
async def test_reopen_rechecks_active_task_capacity(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    await _seed_task_entitlements(
        db_session,
        auth_context.workspace_id,
        active_tasks=1,
    )
    service = TaskService(db_session)
    first_matter = await _matter(db_session, auth_context.workspace_id, "Reabrir")
    first = await service.create(
        first_matter.id,
        TaskCreate(title="Tarefa encerrada"),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "reopen-create"),
    )
    first = await service.execute_command(
        first.id,
        TaskCommand.complete,
        TaskCommandRequest(expected_version=1),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "reopen-complete"),
    )
    second_matter = await _matter(db_session, auth_context.workspace_id, "Ocupante")
    await service.create(
        second_matter.id,
        TaskCreate(title="Ocupa a vaga"),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "reopen-occupy"),
    )

    with pytest.raises(CurrentStockLimitExceededError) as blocked:
        await service.execute_command(
            first.id,
            TaskCommand.reopen,
            TaskCommandRequest(
                expected_version=2,
                reason="Retomar a tarefa após nova evidência.",
            ),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "reopen-blocked"),
        )
    assert blocked.value.entitlement == EntitlementKey.active_tasks
