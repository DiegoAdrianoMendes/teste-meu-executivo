from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.agent_run import AgentRun
from app.models.approval import ApprovalQueueItem, ApprovalStatus
from app.models.client import Client
from app.models.matter import Matter
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.approval import ApprovalCreate, ApprovalResolveRequest
from app.services.approval_service import ApprovalService

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")

if not MASTER_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL; "
        "run scripts/verify_postgresql_qa.sh."
    )


async def _seed_matter(sessions, token: str) -> tuple[UUID, UUID, UUID]:
    async with sessions() as db:
        workspace = Workspace(
            name=f"Approval race {token}",
            slug=f"approval-race-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        user = User(
            name=f"Approval reviewer {token}",
            email=f"approval-reviewer-{token}@example.com",
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials="AR",
            color="#111827",
            is_active=True,
            is_internal_staff=False,
        )
        db.add_all((workspace, user))
        await db.flush()
        client = Client(
            workspace_id=workspace.id,
            name=f"Approval client {token}",
        )
        db.add(client)
        await db.flush()
        matter = Matter(
            workspace_id=workspace.id,
            client_id=client.id,
            demand_type="Approval concurrency",
            responsible_human_id=user.id,
        )
        db.add(matter)
        await db.commit()
        return workspace.id, user.id, matter.id


async def _cleanup(sessions, workspace_id: UUID, user_id: UUID) -> None:
    async with sessions() as db:
        await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


@pytest.mark.asyncio
async def test_concurrent_equivalent_create_returns_one_pending_approval() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id, user_id, matter_id = await _seed_matter(
        sessions,
        uuid4().hex[:12],
    )
    start = asyncio.Event()

    async def create() -> UUID:
        async with sessions() as db:
            await start.wait()
            item = await ApprovalService(db, workspace_id).create(
                ApprovalCreate(
                    matter_id=matter_id,
                    approval_type="petition_review",
                    title="Concurrent petition review",
                    requested_by=user_id,
                )
            )
            await db.commit()
            return item.id

    try:
        tasks = (asyncio.create_task(create()), asyncio.create_task(create()))
        start.set()
        approval_ids = await asyncio.gather(*tasks)

        assert len(set(approval_ids)) == 1
        async with sessions() as db:
            assert (
                await db.scalar(
                    select(func.count(ApprovalQueueItem.id)).where(
                        ApprovalQueueItem.matter_id == matter_id,
                        ApprovalQueueItem.status == ApprovalStatus.pending,
                    )
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count(TimelineEvent.id)).where(
                        TimelineEvent.matter_id == matter_id,
                        TimelineEvent.event_type
                        == TimelineEventType.approval_requested,
                    )
                )
                == 1
            )
    finally:
        await _cleanup(sessions, workspace_id, user_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_divergent_create_has_one_winner_and_one_conflict() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id, user_id, matter_id = await _seed_matter(
        sessions,
        uuid4().hex[:12],
    )
    start = asyncio.Event()

    async def create(title: str) -> str:
        async with sessions() as db:
            await start.wait()
            try:
                await ApprovalService(db, workspace_id).create(
                    ApprovalCreate(
                        matter_id=matter_id,
                        approval_type="petition_review",
                        title=title,
                        requested_by=user_id,
                    )
                )
                await db.commit()
                return "committed"
            except HTTPException as error:
                await db.rollback()
                assert error.status_code == 409
                assert error.detail["code"] == "approval_pending_payload_conflict"
                return "conflict"

    try:
        tasks = (
            asyncio.create_task(create("First payload")),
            asyncio.create_task(create("Second payload")),
        )
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert sorted(outcomes) == ["committed", "conflict"]
        async with sessions() as db:
            assert (
                await db.scalar(
                    select(func.count(ApprovalQueueItem.id)).where(
                        ApprovalQueueItem.matter_id == matter_id,
                        ApprovalQueueItem.status == ApprovalStatus.pending,
                    )
                )
                == 1
            )
    finally:
        await _cleanup(sessions, workspace_id, user_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_resolve_has_one_terminal_winner_and_one_timeline() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    workspace_id, user_id, matter_id = await _seed_matter(
        sessions,
        uuid4().hex[:12],
    )
    async with sessions() as db:
        item = await ApprovalService(db, workspace_id).create(
            ApprovalCreate(
                matter_id=matter_id,
                approval_type="petition_review",
                title="Resolve race",
                requested_by=user_id,
            )
        )
        await db.commit()
        approval_id = item.id

    start = asyncio.Event()

    async def resolve(decision: str) -> tuple[str, str]:
        async with sessions() as db:
            await start.wait()
            try:
                resolved = await ApprovalService(db, workspace_id).resolve(
                    approval_id,
                    ApprovalResolveRequest(
                        status=decision,
                        resolution_reason=f"Decision: {decision}",
                        resolved_by=user_id,
                    )
                )
                await db.commit()
                return "committed", resolved.status.value
            except HTTPException as error:
                await db.rollback()
                assert error.status_code == 409
                assert error.detail["code"] == "approval_already_resolved"
                return "conflict", error.detail["currentStatus"]

    try:
        tasks = (
            asyncio.create_task(resolve("approved")),
            asyncio.create_task(resolve("rejected")),
        )
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert sorted(outcome[0] for outcome in outcomes) == ["committed", "conflict"]
        winner = next(outcome[1] for outcome in outcomes if outcome[0] == "committed")
        async with sessions() as db:
            persisted = await db.get(ApprovalQueueItem, approval_id)
            matter = await db.get(Matter, matter_id)
            timeline_count = await db.scalar(
                select(func.count(TimelineEvent.id)).where(
                    TimelineEvent.matter_id == matter_id,
                    TimelineEvent.event_type == TimelineEventType.approval_resolved,
                )
            )
            agent_run_count = await db.scalar(
                select(func.count(AgentRun.id)).where(
                    AgentRun.matter_id == matter_id,
                    AgentRun.dedupe_key == f"approval:{approval_id}:rejected",
                )
            )

        assert persisted is not None
        assert persisted.status.value == winner
        assert matter is not None
        assert matter.needs_approval is False
        assert timeline_count == 1
        assert agent_run_count == (1 if winner == "rejected" else 0)
    finally:
        await _cleanup(sessions, workspace_id, user_id)
        await engine.dispose()
