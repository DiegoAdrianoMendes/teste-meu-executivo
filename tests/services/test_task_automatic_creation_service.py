from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.models.task import Task, TaskEvent, TaskEventType, TaskParticipantRole
from app.models.timeline_event import TimelineEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.task import TaskCreate
from app.services.task_service import TaskService


async def _matter(
    db: AsyncSession,
    workspace_id,
    *,
    responsible_human_id=None,
) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente Automação")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type="Demanda automática",
        practice_area=PracticeArea.civel,
        responsible_human_id=responsible_human_id,
    )
    db.add(matter)
    await db.flush()
    return matter


async def _member(
    db: AsyncSession,
    workspace_id,
    *,
    label: str,
    status: MembershipStatus = MembershipStatus.active,
) -> User:
    user = User(
        name=label,
        email=f"automatic-{uuid4().hex[:12]}@example.com",
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
            status=status,
        )
    )
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_automatic_creation_uses_active_matter_owner_and_is_fully_audited(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    responsible = await _member(
        db_session,
        auth_context.workspace_id,
        label="Responsável",
    )
    matter = await _matter(
        db_session,
        auth_context.workspace_id,
        responsible_human_id=responsible.id,
    )
    payload = TaskCreate(
        title="Revisar ocorrência automática",
        is_ai_generated=True,
        idempotency_key=f"automatic:{matter.id}",
    )
    service = TaskService(db_session)

    task = await service.create_automatic(
        matter.id,
        payload,
        auth_context.workspace_id,
        source="test.automatic",
        correlation_id=f"test:automatic:{matter.id}",
    )
    replay = await service.create_automatic(
        matter.id,
        payload,
        auth_context.workspace_id,
        source="test.automatic",
        correlation_id=f"test:automatic:{matter.id}",
    )

    assert replay.id == task.id
    assert task.assigned_to == responsible.id
    assert task.version == 1
    assert [
        item.user_id
        for item in task.participants
        if item.active and item.role == TaskParticipantRole.owner
    ] == [responsible.id]

    events = list(
        (await db_session.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id))).all()
    )
    assert len(events) == 1
    assert events[0].event_type == TaskEventType.created
    assert events[0].resource_version_before is None
    assert events[0].resource_version_after == 1
    assert events[0].session_type == "system"
    assert events[0].correlation_id == f"test:automatic:{matter.id}"

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.entity_type == "task",
            KlmAuditEvent.entity_id == task.id,
        )
    )
    assert audit is not None
    assert audit.event_type == "task_created"
    assert audit.correlation_id == events[0].correlation_id
    assert (
        await db_session.scalar(
            select(func.count(TimelineEvent.id)).where(
                TimelineEvent.matter_id == matter.id,
                TimelineEvent.event_data["task_id"].as_string() == str(task.id),
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_automatic_creation_falls_back_to_workspace_owner_and_fails_without_active_owner(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    inactive = await _member(
        db_session,
        auth_context.workspace_id,
        label="Suspenso",
        status=MembershipStatus.suspended,
    )
    matter = await _matter(
        db_session,
        auth_context.workspace_id,
        responsible_human_id=inactive.id,
    )
    service = TaskService(db_session)
    created = await service.create_automatic(
        matter.id,
        TaskCreate(
            title="Fallback de owner",
            idempotency_key=f"fallback:{matter.id}",
        ),
        auth_context.workspace_id,
        source="test.owner_fallback",
    )
    assert created.assigned_to == auth_context.user_id

    owner_membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
            WorkspaceMembership.user_id == auth_context.user_id,
        )
    )
    owner_membership.status = MembershipStatus.suspended
    await db_session.flush()
    unowned_matter = await _matter(db_session, auth_context.workspace_id)

    with pytest.raises(HTTPException) as error:
        await service.create_automatic(
            unowned_matter.id,
            TaskCreate(title="Não pode ficar órfã"),
            auth_context.workspace_id,
            source="test.no_active_owner",
        )

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "TASK_OWNER_UNAVAILABLE"
    assert (
        await db_session.scalar(
            select(func.count(Task.id)).where(Task.matter_id == unowned_matter.id)
        )
        == 0
    )


def test_task_model_is_only_instantiated_inside_task_service() -> None:
    app_root = Path(__file__).resolve().parents[2] / "app"
    allowed = app_root / "services" / "task_service.py"
    violations: list[str] = []

    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        task_aliases = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.models.task"
            for alias in node.names
            if alias.name == "Task"
        }
        if not task_aliases:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in task_aliases
                and path != allowed
            ):
                violations.append(f"{path.relative_to(app_root)}:{node.lineno}")

    assert violations == []
