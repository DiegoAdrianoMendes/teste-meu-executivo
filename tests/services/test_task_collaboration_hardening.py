from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.models.task import (
    TaskParticipantRole,
    TaskParticipantState,
    TaskReviewState,
    TaskStatus,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.task import (
    TaskCommand,
    TaskCommandRequest,
    TaskCreate,
    TaskParticipantsReplace,
    TaskParticipantStateUpdate,
    TaskParticipantWrite,
    TaskUpdate,
)
from app.services.task_service import TaskMutationContext, TaskService


async def _matter(db: AsyncSession, workspace_id, label: str = "Hardening") -> Matter:
    client = Client(workspace_id=workspace_id, name=f"Cliente {label}")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type=f"Demanda {label}",
        practice_area=PracticeArea.civel,
    )
    db.add(matter)
    await db.flush()
    return matter


async def _member(db: AsyncSession, workspace_id, label: str) -> User:
    user = User(
        name=label,
        email=f"task-hardening-{uuid4().hex[:12]}@example.com",
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
        correlation_id=f"test:task-hardening:{suffix}:{uuid4()}",
    )


def test_task_service_rejects_one_user_in_multiple_active_roles() -> None:
    user_id = uuid4()
    duplicate_roles = [
        TaskParticipantWrite(user_id=user_id, role=TaskParticipantRole.owner),
        TaskParticipantWrite(user_id=user_id, role=TaskParticipantRole.observer),
    ]

    create_contract = TaskCreate(title="Contrato inválido", participants=duplicate_roles)
    replace_contract = TaskParticipantsReplace(
        expected_version=1,
        participants=duplicate_roles,
        reason="Rejeitar papéis ativos duplicados para o mesmo participante.",
    )

    for participants in (create_contract.participants, replace_contract.participants):
        assert participants is not None
        with pytest.raises(HTTPException) as conflict:
            TaskService._validate_participant_specs(participants)
        assert conflict.value.detail["code"] == "TASK_PARTICIPANT_DUPLICATE"


def test_owner_can_be_an_explicit_required_executor_without_making_all_owners_required() -> None:
    owner_id = uuid4()

    optional_owner = TaskParticipantWrite(
        user_id=owner_id,
        role=TaskParticipantRole.owner,
    )
    required_owner = TaskParticipantWrite(
        user_id=owner_id,
        role=TaskParticipantRole.owner,
        required_for_completion=True,
    )

    assert optional_owner.required_for_completion is False
    assert required_owner.required_for_completion is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision",
    [TaskCommand.approve, TaskCommand.request_changes],
)
async def test_submitter_cannot_decide_own_tenant_task_review(
    db_session: AsyncSession,
    auth_context: AuthContext,
    decision: TaskCommand,
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id, "Segregação de revisão")
    reviewer = await _member(db_session, auth_context.workspace_id, "Reviewer submitter")
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Entrega com revisão segregada",
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
        context=_context(auth_context.user_id, "self-review-create"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.start,
        TaskCommandRequest(expected_version=task.version),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "self-review-start"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.submit_for_review,
        TaskCommandRequest(expected_version=task.version),
        auth_context.workspace_id,
        context=_context(reviewer.id, "self-review-submit"),
    )

    with pytest.raises(HTTPException) as denied:
        await service.execute_command(
            task.id,
            decision,
            TaskCommandRequest(
                expected_version=task.version,
                reason="A decisão deve ser recusada por auto-revisão.",
            ),
            auth_context.workspace_id,
            context=_context(reviewer.id, f"self-review-{decision.value}"),
        )

    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "TASK_SELF_REVIEW_DENIED"


@pytest.mark.asyncio
async def test_platform_root_cannot_self_approve_a_tenant_task_review(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id, "Segregação root")
    reviewer = await _member(db_session, auth_context.workspace_id, "Reviewer nominal")
    service = TaskService(db_session)
    root_context = TaskMutationContext(
        actor_user_id=auth_context.user_id,
        correlation_id=f"test:task-hardening:root-self-review:{uuid4()}",
        session_type="root_direct",
        administrative_session_id=uuid4(),
        support_grant_id=uuid4(),
        is_platform_root=True,
    )
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Entrega tenant submetida pelo root",
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
        context=root_context,
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.start,
        TaskCommandRequest(expected_version=task.version),
        auth_context.workspace_id,
        context=root_context,
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.submit_for_review,
        TaskCommandRequest(expected_version=task.version),
        auth_context.workspace_id,
        context=root_context,
    )

    with pytest.raises(HTTPException) as denied:
        await service.execute_command(
            task.id,
            TaskCommand.approve,
            TaskCommandRequest(
                expected_version=task.version,
                reason="Root tentou decidir a própria submissão tenant.",
            ),
            auth_context.workspace_id,
            context=root_context,
        )

    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "TASK_SELF_REVIEW_DENIED"


@pytest.mark.asyncio
async def test_required_owner_reports_progress_and_blocks_global_completion(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id, "Owner executor")
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Owner também executa",
            participants=[
                TaskParticipantWrite(
                    user_id=auth_context.user_id,
                    role=TaskParticipantRole.owner,
                    required_for_completion=True,
                )
            ],
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "required-owner-create"),
    )

    with pytest.raises(HTTPException) as incomplete:
        await service.execute_command(
            task.id,
            TaskCommand.complete,
            TaskCommandRequest(expected_version=task.version),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "required-owner-too-soon"),
        )
    assert incomplete.value.detail["code"] == "TASK_ASSIGNEES_INCOMPLETE"

    task = await service.set_participant_state(
        task.id,
        auth_context.user_id,
        TaskParticipantStateUpdate(
            expected_version=task.version,
            state=TaskParticipantState.completed,
            reason="Owner concluiu a execução obrigatória atribuída a ele.",
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "required-owner-finished"),
    )
    assert task is not None
    task = await service.execute_command(
        task.id,
        TaskCommand.complete,
        TaskCommandRequest(expected_version=task.version),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "required-owner-complete"),
    )
    assert task is not None
    assert task.status == TaskStatus.done

    task = await service.execute_command(
        task.id,
        TaskCommand.reopen,
        TaskCommandRequest(
            expected_version=task.version,
            reason="Reabrir para uma nova execução obrigatória do owner.",
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "required-owner-reopen"),
    )
    reopened_owner = next(
        item
        for item in task.participants
        if item.active and item.role == TaskParticipantRole.owner
    )
    assert reopened_owner.state == TaskParticipantState.assigned


def test_task_schema_rejects_naive_and_orders_offsets_by_instant() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        TaskCreate(title="Sem timezone", due_at=datetime(2026, 11, 1, 9, 0))

    zone = ZoneInfo("America/New_York")
    # No fim do horário de verão, 01:45 EDT acontece 45 minutos antes de
    # 01:30 EST. A comparação por UTC deve aceitar essa ordem real.
    individual_due = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
    general_due = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    payload = TaskCreate(
        title="Ordem real no DST",
        due_at=general_due,
        participants=[
            TaskParticipantWrite(
                user_id=uuid4(),
                role=TaskParticipantRole.owner,
            ),
            TaskParticipantWrite(
                user_id=uuid4(),
                role=TaskParticipantRole.assignee,
                due_at=individual_due,
            ),
        ],
    )
    assert payload.due_at == general_due


@pytest.mark.asyncio
async def test_deadline_offsets_survive_sqlite_roundtrip_as_canonical_utc(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id, "UTC roundtrip")
    assignee = await _member(db_session, auth_context.workspace_id, "Executor UTC")
    zone = ZoneInfo("America/New_York")
    individual_due = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
    general_due = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Persistir prazo no DST",
            due_at=general_due,
            participants=[
                TaskParticipantWrite(
                    user_id=auth_context.user_id,
                    role=TaskParticipantRole.owner,
                ),
                TaskParticipantWrite(
                    user_id=assignee.id,
                    role=TaskParticipantRole.assignee,
                    due_at=individual_due,
                ),
            ],
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "utc-roundtrip"),
    )
    task_id = task.id
    assignee_id = assignee.id
    db_session.expire_all()

    reloaded = await service.get(task_id, auth_context.workspace_id)
    assert reloaded is not None
    assert reloaded.due_at == general_due.astimezone(UTC)
    reloaded_assignee = next(
        item for item in reloaded.participants if item.user_id == assignee_id and item.active
    )
    assert reloaded_assignee.due_at == individual_due.astimezone(UTC)
    assert reloaded.due_date == "2026-11-01"


@pytest.mark.asyncio
async def test_deadlines_require_general_bound_and_remain_coherent_on_mutation(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    service = TaskService(db_session)
    matter = await _matter(db_session, auth_context.workspace_id)
    assignee = await _member(db_session, auth_context.workspace_id, "Executor prazo")
    reviewer = await _member(db_session, auth_context.workspace_id, "Revisor prazo")
    now = datetime.now(UTC)

    with pytest.raises(HTTPException) as missing_general:
        await service.create(
            matter.id,
            TaskCreate(
                title="Prazo individual sem geral",
                participants=[
                    TaskParticipantWrite(
                        user_id=auth_context.user_id,
                        role=TaskParticipantRole.owner,
                    ),
                    TaskParticipantWrite(
                        user_id=assignee.id,
                        role=TaskParticipantRole.assignee,
                        due_at=now + timedelta(days=1),
                    ),
                ],
            ),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "missing-general"),
        )
    assert missing_general.value.detail["code"] == "TASK_GENERAL_DEADLINE_REQUIRED"

    task = await service.create(
        matter.id,
        TaskCreate(
            title="Prazo coerente",
            due_at=now + timedelta(days=3),
            review_due_at=now + timedelta(days=2),
            review_required=True,
            participants=[
                TaskParticipantWrite(
                    user_id=auth_context.user_id,
                    role=TaskParticipantRole.owner,
                ),
                TaskParticipantWrite(
                    user_id=assignee.id,
                    role=TaskParticipantRole.assignee,
                    due_at=now + timedelta(days=1),
                ),
                TaskParticipantWrite(
                    user_id=reviewer.id,
                    role=TaskParticipantRole.reviewer,
                ),
            ],
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "coherent-create"),
    )

    with pytest.raises(HTTPException) as shortened:
        await service.update(
            task.id,
            TaskUpdate(
                expected_version=1,
                due_at=now + timedelta(hours=12),
            ),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "shorten"),
        )
    assert shortened.value.detail["code"] == "TASK_DEADLINE_ORDER_INVALID"

    with pytest.raises(HTTPException) as review_after_general:
        await service.update(
            task.id,
            TaskUpdate(
                expected_version=1,
                review_due_at=now + timedelta(days=4),
            ),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "review-after-general"),
        )
    assert review_after_general.value.detail["code"] == "TASK_DEADLINE_ORDER_INVALID"

    with pytest.raises(HTTPException) as active_reviewer:
        await service.update(
            task.id,
            TaskUpdate(expected_version=1, review_required=False),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "disable-review"),
        )
    assert active_reviewer.value.detail["code"] == "TASK_REVIEWER_ACTIVE"

    with pytest.raises(HTTPException) as replacement_after_general:
        await service.replace_participants(
            task.id,
            TaskParticipantsReplace(
                expected_version=1,
                reason="Novo executor com prazo inválido.",
                participants=[
                    TaskParticipantWrite(
                        user_id=auth_context.user_id,
                        role=TaskParticipantRole.owner,
                    ),
                    TaskParticipantWrite(
                        user_id=assignee.id,
                        role=TaskParticipantRole.assignee,
                        due_at=now + timedelta(days=4),
                    ),
                    TaskParticipantWrite(
                        user_id=reviewer.id,
                        role=TaskParticipantRole.reviewer,
                    ),
                ],
            ),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "replace-after-general"),
        )
    assert replacement_after_general.value.detail["code"] == "TASK_DEADLINE_ORDER_INVALID"


@pytest.mark.asyncio
async def test_required_assignee_state_gates_review_and_resets_after_changes(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id, "Workflow")
    required = await _member(db_session, auth_context.workspace_id, "Executor obrigatório")
    optional = await _member(db_session, auth_context.workspace_id, "Executor opcional")
    reviewer = await _member(db_session, auth_context.workspace_id, "Revisor workflow")
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Workflow individual",
            review_required=True,
            participants=[
                TaskParticipantWrite(
                    user_id=auth_context.user_id,
                    role=TaskParticipantRole.owner,
                ),
                TaskParticipantWrite(
                    user_id=required.id,
                    role=TaskParticipantRole.assignee,
                ),
                TaskParticipantWrite(
                    user_id=optional.id,
                    role=TaskParticipantRole.assignee,
                    required_for_completion=False,
                ),
                TaskParticipantWrite(
                    user_id=reviewer.id,
                    role=TaskParticipantRole.reviewer,
                ),
            ],
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "workflow-create"),
    )
    required_item = next(item for item in task.participants if item.user_id == required.id)
    optional_item = next(item for item in task.participants if item.user_id == optional.id)
    assert required_item.required_for_completion is True
    assert optional_item.required_for_completion is False
    assert required_item.label_snapshot == "Executor obrigatório"
    required.name = "Executor renomeado"
    await db_session.flush()
    reloaded = await service.get(task.id, auth_context.workspace_id)
    assert reloaded is not None
    assert (
        next(item for item in reloaded.participants if item.user_id == required.id).label_snapshot
        == "Executor obrigatório"
    )

    task = await service.execute_command(
        task.id,
        TaskCommand.start,
        TaskCommandRequest(expected_version=1),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "workflow-start"),
    )
    with pytest.raises(HTTPException) as incomplete:
        await service.execute_command(
            task.id,
            TaskCommand.submit_for_review,
            TaskCommandRequest(expected_version=2),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "submit-incomplete"),
        )
    assert incomplete.value.detail["code"] == "TASK_ASSIGNEES_INCOMPLETE"
    assert incomplete.value.detail["userIds"] == [str(required.id)]

    task = await service.set_participant_state(
        task.id,
        required.id,
        TaskParticipantStateUpdate(
            expected_version=2,
            state=TaskParticipantState.completed,
            reason="Parte individual concluída.",
        ),
        auth_context.workspace_id,
        context=_context(required.id, "assignee-complete"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.submit_for_review,
        TaskCommandRequest(expected_version=3),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "submit-complete"),
    )
    assert task.review_state == TaskReviewState.pending

    task = await service.execute_command(
        task.id,
        TaskCommand.request_changes,
        TaskCommandRequest(
            expected_version=4,
            reason="Ajustar fundamentação principal.",
        ),
        auth_context.workspace_id,
        context=_context(reviewer.id, "request-changes"),
    )
    refreshed_required = next(
        item for item in task.participants if item.active and item.user_id == required.id
    )
    assert refreshed_required.state == TaskParticipantState.in_progress
    with pytest.raises(HTTPException) as incomplete_again:
        await service.execute_command(
            task.id,
            TaskCommand.submit_for_review,
            TaskCommandRequest(expected_version=5),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "resubmit-incomplete"),
        )
    assert incomplete_again.value.detail["code"] == "TASK_ASSIGNEES_INCOMPLETE"


@pytest.mark.asyncio
async def test_required_assignee_gates_direct_completion(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id, "Completion")
    assignee = await _member(db_session, auth_context.workspace_id, "Executor conclusão")
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Conclusão protegida",
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
        context=_context(auth_context.user_id, "completion-create"),
    )
    with pytest.raises(HTTPException) as incomplete:
        await service.execute_command(
            task.id,
            TaskCommand.complete,
            TaskCommandRequest(expected_version=1),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "completion-blocked"),
        )
    assert incomplete.value.detail["code"] == "TASK_ASSIGNEES_INCOMPLETE"

    task = await service.set_participant_state(
        task.id,
        assignee.id,
        TaskParticipantStateUpdate(
            expected_version=1,
            state=TaskParticipantState.completed,
            reason="Execução individual encerrada.",
        ),
        auth_context.workspace_id,
        context=_context(assignee.id, "completion-assignee"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.complete,
        TaskCommandRequest(expected_version=2),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "completion-global"),
    )
    assert task.status == TaskStatus.done


@pytest.mark.asyncio
async def test_replacing_participants_invalidates_prior_review_approval(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id, "Review reset")
    reviewer = await _member(db_session, auth_context.workspace_id, "Revisor reset")
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Revisão a invalidar",
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
        context=_context(auth_context.user_id, "review-reset-create"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.start,
        TaskCommandRequest(expected_version=1),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "review-reset-start"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.submit_for_review,
        TaskCommandRequest(expected_version=2),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "review-reset-submit"),
    )
    with pytest.raises(HTTPException) as pending_edit:
        await service.update(
            task.id,
            TaskUpdate(expected_version=3, title="Não pode mudar em revisão"),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "review-pending-edit"),
        )
    assert pending_edit.value.detail["code"] == "TASK_REVIEW_PENDING"
    task = await service.execute_command(
        task.id,
        TaskCommand.approve,
        TaskCommandRequest(expected_version=3, comment="Aprovado antes da redistribuição."),
        auth_context.workspace_id,
        context=_context(reviewer.id, "review-reset-approve"),
    )
    assert task.review_state == TaskReviewState.approved

    task = await service.update(
        task.id,
        TaskUpdate(expected_version=4, title="Revisão alterada depois da aprovação"),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "review-reset-update"),
    )
    assert task.review_state == TaskReviewState.draft
    task = await service.execute_command(
        task.id,
        TaskCommand.submit_for_review,
        TaskCommandRequest(expected_version=5),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "review-reset-resubmit"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.approve,
        TaskCommandRequest(expected_version=6, comment="Nova aprovação registrada."),
        auth_context.workspace_id,
        context=_context(reviewer.id, "review-reset-reapprove"),
    )
    assert task.review_state == TaskReviewState.approved

    task = await service.replace_participants(
        task.id,
        TaskParticipantsReplace(
            expected_version=7,
            reason="Redistribuição exige uma nova revisão.",
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
        context=_context(auth_context.user_id, "review-reset-replace"),
    )
    assert task.review_state == TaskReviewState.draft
