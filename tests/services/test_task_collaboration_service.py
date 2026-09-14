from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.db.append_only import ImmutableRecordError
from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.models.task import (
    TaskEvent,
    TaskEventType,
    TaskParticipantRole,
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
    TaskParticipantWrite,
    TaskUpdate,
)
from app.services.task_service import TaskMutationContext, TaskService


async def _matter(db: AsyncSession, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente Colaboração")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type="Demanda colaborativa",
        practice_area=PracticeArea.civel,
    )
    db.add(matter)
    await db.flush()
    return matter


async def _member(db: AsyncSession, workspace_id, label: str) -> User:
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
            workspace_id=workspace_id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
        )
    )
    await db.flush()
    return user


def _context(actor_id, name: str) -> TaskMutationContext:
    return TaskMutationContext(actor_user_id=actor_id, correlation_id=f"test:{name}:{uuid4()}")


@pytest.mark.asyncio
async def test_collaborative_task_supports_roles_and_individual_deadlines(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id)
    assignee = await _member(db_session, auth_context.workspace_id, "Executor")
    reviewer = await _member(db_session, auth_context.workspace_id, "Revisor")
    observer = await _member(db_session, auth_context.workspace_id, "Observador")
    due = datetime.now(UTC) + timedelta(days=3)

    task = await TaskService(db_session).create(
        matter.id,
        TaskCreate(
            title="Preparar defesa",
            review_required=True,
            due_at=due,
            participants=[
                TaskParticipantWrite(user_id=auth_context.user_id, role=TaskParticipantRole.owner),
                TaskParticipantWrite(
                    user_id=assignee.id,
                    role=TaskParticipantRole.assignee,
                    due_at=due - timedelta(days=1),
                ),
                TaskParticipantWrite(
                    user_id=reviewer.id,
                    role=TaskParticipantRole.reviewer,
                    due_at=due - timedelta(hours=4),
                ),
                TaskParticipantWrite(user_id=observer.id, role=TaskParticipantRole.observer),
            ],
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "create"),
    )

    assert task.assigned_to == auth_context.user_id
    assert task.review_state == TaskReviewState.draft
    assert task.version == 1
    assert {item.role for item in task.participants if item.active} == {
        TaskParticipantRole.owner,
        TaskParticipantRole.assignee,
        TaskParticipantRole.reviewer,
        TaskParticipantRole.observer,
    }
    events = list(
        (await db_session.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id))).all()
    )
    assert [item.event_type for item in events] == [TaskEventType.created]
    assert events[0].before_data is None
    assert "participants" in events[0].changed_paths
    actor = await db_session.get(User, auth_context.user_id)
    assert actor is not None
    assert events[0].actor_label_snapshot == actor.name
    assert events[0].actor_role_snapshot == actor.role.value
    assert events[0].actor_type == actor.agent_type.value
    assert events[0].reason_source == "system"
    assert events[0].reason.startswith("Tarefa criada:")


@pytest.mark.asyncio
async def test_owner_reviewer_invariants_and_active_membership_are_enforced(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id)
    service = TaskService(db_session)
    with pytest.raises(HTTPException) as conflict:
        await service.create(
            matter.id,
            TaskCreate(
                title="Revisão própria",
                participants=[
                    TaskParticipantWrite(
                        user_id=auth_context.user_id, role=TaskParticipantRole.owner
                    ),
                    TaskParticipantWrite(
                        user_id=auth_context.user_id, role=TaskParticipantRole.reviewer
                    ),
                ],
            ),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "invalid-role"),
        )
    assert conflict.value.status_code == 422
    assert conflict.value.detail["code"] == "TASK_REVIEWER_CONFLICT"

    inactive = await _member(db_session, auth_context.workspace_id, "Suspenso")
    membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
            WorkspaceMembership.user_id == inactive.id,
        )
    )
    membership.status = MembershipStatus.suspended
    await db_session.flush()
    with pytest.raises(HTTPException) as inactive_error:
        await service.create(
            matter.id,
            TaskCreate(title="Owner inativo", owner_id=inactive.id),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "inactive"),
        )
    assert inactive_error.value.detail["code"] == "TASK_PARTICIPANT_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_review_workflow_is_versioned_and_explicit(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id)
    reviewer = await _member(db_session, auth_context.workspace_id, "Revisor")
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(
            title="Peça para revisão",
            review_required=True,
            participants=[
                TaskParticipantWrite(user_id=auth_context.user_id, role=TaskParticipantRole.owner),
                TaskParticipantWrite(user_id=reviewer.id, role=TaskParticipantRole.reviewer),
            ],
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "create-flow"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.start,
        TaskCommandRequest(expected_version=1),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "start"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.submit_for_review,
        TaskCommandRequest(expected_version=2),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "submit"),
    )
    assert task.review_state == TaskReviewState.pending
    assert task.review_round == 1

    with pytest.raises(HTTPException) as not_reviewer:
        await service.execute_command(
            task.id,
            TaskCommand.approve,
            TaskCommandRequest(expected_version=3),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "wrong-reviewer"),
        )
    assert not_reviewer.value.status_code == 403

    task = await service.execute_command(
        task.id,
        TaskCommand.approve,
        TaskCommandRequest(expected_version=3, comment="Revisão aprovada."),
        auth_context.workspace_id,
        context=_context(reviewer.id, "approve"),
    )
    task = await service.execute_command(
        task.id,
        TaskCommand.complete,
        TaskCommandRequest(expected_version=4),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "complete"),
    )
    assert task.status == TaskStatus.done
    assert task.review_state == TaskReviewState.approved
    assert task.version == 5
    assert task.completed_at is not None


@pytest.mark.asyncio
async def test_stale_mutation_and_collaborative_toggle_fail_closed(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id)
    assignee = await _member(db_session, auth_context.workspace_id, "Executor")
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(title="Tarefa simples"),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "create-stale"),
    )
    with pytest.raises(HTTPException) as stale:
        await service.update(
            task.id,
            TaskUpdate(expected_version=99, title="Não deve gravar"),
            auth_context.workspace_id,
            context=_context(auth_context.user_id, "stale"),
        )
    assert stale.value.status_code == 409
    assert stale.value.detail["code"] == "TASK_VERSION_CONFLICT"

    task = await service.replace_participants(
        task.id,
        TaskParticipantsReplace(
            expected_version=1,
            reason="Distribuição inicial da tarefa.",
            participants=[
                TaskParticipantWrite(user_id=auth_context.user_id, role=TaskParticipantRole.owner),
                TaskParticipantWrite(user_id=assignee.id, role=TaskParticipantRole.assignee),
            ],
        ),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "participants"),
    )
    with pytest.raises(HTTPException) as collaborative:
        await service.toggle_status(
            task.id,
            auth_context.workspace_id,
            expected_version=2,
            context=_context(auth_context.user_id, "toggle"),
        )
    assert collaborative.value.detail["code"] == "TASK_TOGGLE_COLLABORATIVE_FORBIDDEN"


@pytest.mark.asyncio
async def test_simple_toggle_never_reopens_done_and_events_are_immutable(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _matter(db_session, auth_context.workspace_id)
    service = TaskService(db_session)
    task = await service.create(
        matter.id,
        TaskCreate(title="Concluir uma única vez"),
        auth_context.workspace_id,
        context=_context(auth_context.user_id, "simple-create"),
    )
    task = await service.toggle_status(
        task.id,
        auth_context.workspace_id,
        expected_version=1,
        context=_context(auth_context.user_id, "simple-toggle"),
    )
    assert task.status == TaskStatus.done
    assert task.version == 2

    unchanged = await service.toggle_status(
        task.id,
        auth_context.workspace_id,
        expected_version=2,
        context=_context(auth_context.user_id, "must-not-reopen"),
    )
    assert unchanged.status == TaskStatus.done
    assert unchanged.version == 2

    event = await db_session.scalar(
        select(TaskEvent).where(
            TaskEvent.task_id == task.id,
            TaskEvent.event_type == TaskEventType.completed,
        )
    )
    event.comment = "Tentativa de adulteração"
    with pytest.raises(ImmutableRecordError):
        await db_session.flush()
