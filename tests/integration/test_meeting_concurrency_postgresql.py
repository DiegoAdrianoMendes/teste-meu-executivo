from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.client import Client
from app.models.entitlement import (
    EntitlementKey,
    OverrideMode,
    WorkspaceEntitlementOverride,
)
from app.models.matter import Matter, PracticeArea
from app.models.meeting import (
    Meeting,
    MeetingCreationReceipt,
    MeetingParticipantRole,
    MeetingStatus,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.control_plane import MeetingCreate, MeetingParticipantWrite, MeetingUpdate
from app.services.meeting_service import MeetingService

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")

if not MASTER_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL; "
        "run scripts/verify_postgresql_qa.sh."
    )

if os.getenv("KLM_TEST_POSTGRES_QA_DISPOSABLE") != "1":
    raise RuntimeError(
        "Meeting concurrency commits immutable audit evidence and therefore "
        "requires the disposable PostgreSQL QA harness."
    )


async def _seed_calendar_owner(
    sessions: async_sessionmaker[AsyncSession],
    *,
    token: str,
) -> tuple[UUID, UUID]:
    async with sessions() as db:
        workspace = Workspace(
            name=f"Calendar owner {token}",
            slug=f"calendar-owner-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        user = User(
            name=f"Calendar Owner {token}",
            email=f"calendar-owner-{token}@example.test",
            agent_type=AgentType.human,
            role=UserRole.lawyer,
            initials="CO",
            color="#164e63",
            is_active=True,
            email_verified_at=datetime.now(UTC),
        )
        db.add_all((workspace, user))
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.operator,
                status=MembershipStatus.active,
                is_default=True,
            )
        )
        db.add(
            WorkspaceEntitlementOverride(
                workspace_id=workspace.id,
                entitlement=EntitlementKey.meeting_participants,
                mode=OverrideMode.grant,
                quantity=10,
                reason="PostgreSQL meeting concurrency test.",
            )
        )
        await db.commit()
        return workspace.id, user.id


async def _add_calendar_member(
    sessions: async_sessionmaker[AsyncSession],
    *,
    workspace_id: UUID,
    token: str,
) -> UUID:
    async with sessions() as db:
        user = User(
            name=f"Calendar Member {token}",
            email=f"calendar-member-{token}@example.test",
            agent_type=AgentType.human,
            role=UserRole.lawyer,
            initials="CM",
            color="#0f766e",
            is_active=True,
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace_id,
                user_id=user.id,
                role=WorkspaceRole.operator,
                status=MembershipStatus.active,
                is_default=False,
            )
        )
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_concurrent_cancel_and_format_change_preserve_online_first_meeting() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    token = uuid4().hex[:12]

    async with sessions() as db:
        workspace = Workspace(
            name=f"Meeting lock {token}",
            slug=f"meeting-lock-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.flush()
        client = Client(
            workspace_id=workspace.id,
            name=f"Cliente lock {token}",
            email=f"meeting-lock-{token}@example.com",
        )
        db.add(client)
        await db.flush()
        matter = Matter(
            workspace_id=workspace.id,
            client_id=client.id,
            practice_area=PracticeArea.saude_suplementar,
            demand_type="Negativa concorrente",
        )
        db.add(matter)
        await db.flush()
        starts_at = datetime.now(UTC) + timedelta(days=1)
        first = Meeting(
            workspace_id=workspace.id,
            matter_id=matter.id,
            title="Primeira online",
            starts_at=starts_at,
            location="Online",
            status=MeetingStatus.scheduled,
        )
        second = Meeting(
            workspace_id=workspace.id,
            matter_id=matter.id,
            title="Segunda online",
            starts_at=starts_at + timedelta(days=1),
            location="Google Meet",
            status=MeetingStatus.scheduled,
        )
        third = Meeting(
            workspace_id=workspace.id,
            matter_id=matter.id,
            title="Terceira presencial",
            starts_at=starts_at + timedelta(days=2),
            location="Escritório",
            status=MeetingStatus.scheduled,
        )
        db.add_all((first, second, third))
        await db.commit()
        workspace_id = workspace.id
        matter_id = matter.id
        first_id = first.id
        second_id = second.id

    start = asyncio.Event()

    async def update(meeting_id, payload: MeetingUpdate) -> str:
        async with sessions() as db:
            await start.wait()
            try:
                await MeetingService(db).update_for_workspace(
                    workspace_id,
                    meeting_id,
                    payload,
                )
                await db.commit()
                return "committed"
            except HTTPException as error:
                await db.rollback()
                assert error.status_code == 422
                assert error.detail["code"] == "MEE-003"
                return "rejected"

    try:
        cancel_task = asyncio.create_task(
            update(
                first_id,
                MeetingUpdate(expected_version=1, status=MeetingStatus.cancelled),
            )
        )
        physical_task = asyncio.create_task(
            update(
                second_id,
                MeetingUpdate(expected_version=1, location="Escritório"),
            )
        )
        start.set()
        outcomes = await asyncio.gather(cancel_task, physical_task)

        assert sorted(outcomes) == ["committed", "rejected"]
        async with sessions() as db:
            effective_first = await db.scalar(
                select(Meeting)
                .where(
                    Meeting.workspace_id == workspace_id,
                    Meeting.matter_id == matter_id,
                    Meeting.status != MeetingStatus.cancelled,
                )
                .order_by(Meeting.starts_at.asc(), Meeting.created_at.asc(), Meeting.id.asc())
                .limit(1)
            )
            assert effective_first is not None
            assert MeetingService._has_online_evidence(
                effective_first.location,
                effective_first.meeting_url,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_creates_serialize_one_organizer_calendar() -> None:
    """Two empty-window checks cannot both commit for the same organizer."""

    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    token = uuid4().hex[:12]
    workspace_id, organizer_id = await _seed_calendar_owner(sessions, token=token)
    starts_at = datetime.now(UTC) + timedelta(days=3)
    ends_at = starts_at + timedelta(minutes=30)
    start = asyncio.Event()

    async def create(title: str) -> str:
        async with sessions() as db:
            await start.wait()
            try:
                await MeetingService(db).create_for_workspace(
                    workspace_id,
                    MeetingCreate(
                        responsible_user_id=organizer_id,
                        title=title,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        location="Online",
                    ),
                )
                await db.commit()
                return "committed"
            except HTTPException as error:
                await db.rollback()
                assert error.status_code == 409
                assert error.detail["code"] == "MEE-007"
                return "rejected"

    try:
        tasks = (
            asyncio.create_task(create("Agenda concorrente A")),
            asyncio.create_task(create("Agenda concorrente B")),
        )
        start.set()
        assert sorted(await asyncio.gather(*tasks)) == ["committed", "rejected"]

        async with sessions() as db:
            meetings = (
                await db.scalars(
                    select(Meeting).where(
                        Meeting.workspace_id == workspace_id,
                        Meeting.responsible_user_id == organizer_id,
                        Meeting.starts_at == starts_at,
                        Meeting.status.notin_((MeetingStatus.cancelled, MeetingStatus.no_show)),
                    )
                )
            ).all()
            assert len(meetings) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_creation_key_commits_one_meeting_and_one_receipt() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    token = uuid4().hex[:12]
    workspace_id, actor_user_id = await _seed_calendar_owner(sessions, token=token)
    starts_at = datetime.now(UTC) + timedelta(days=9)
    payload = MeetingCreate(
        title="Criação idempotente concorrente",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        location="Online",
    )
    idempotency_key = f"meeting-pg-concurrent-{token}"
    start = asyncio.Event()

    async def create() -> UUID:
        async with sessions() as db:
            await start.wait()
            meeting = await MeetingService(db).create_for_workspace(
                workspace_id,
                payload,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
            )
            await db.commit()
            return meeting.id

    try:
        first = asyncio.create_task(create())
        second = asyncio.create_task(create())
        start.set()
        meeting_ids = await asyncio.gather(first, second)

        assert meeting_ids[0] == meeting_ids[1]
        async with sessions() as db:
            assert (
                await db.scalar(select(func.count(Meeting.id)).where(Meeting.id == meeting_ids[0]))
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count(MeetingCreationReceipt.id)).where(
                        MeetingCreationReceipt.workspace_id == workspace_id,
                        MeetingCreationReceipt.actor_user_id == actor_user_id,
                        MeetingCreationReceipt.idempotency_key == idempotency_key,
                    )
                )
                == 1
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_creates_serialize_shared_attendee_and_allow_reasoned_override() -> None:
    """The shared attendee lock prevents a phantom conflict from slipping through."""

    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    token = uuid4().hex[:12]
    workspace_id, organizer_a = await _seed_calendar_owner(sessions, token=token)
    organizer_b = await _add_calendar_member(
        sessions,
        workspace_id=workspace_id,
        token=f"organizer-{token}",
    )
    attendee_id = await _add_calendar_member(
        sessions,
        workspace_id=workspace_id,
        token=f"attendee-{token}",
    )
    starts_at = datetime.now(UTC) + timedelta(days=3, hours=2)
    ends_at = starts_at + timedelta(minutes=30)
    start = asyncio.Event()

    def payload(
        *,
        organizer_id: UUID,
        title: str,
        confirmed: bool = False,
    ) -> MeetingCreate:
        return MeetingCreate(
            responsible_user_id=organizer_id,
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            location="Online",
            participants=[
                MeetingParticipantWrite(
                    user_id=organizer_id,
                    role=MeetingParticipantRole.organizer,
                ),
                MeetingParticipantWrite(
                    user_id=attendee_id,
                    role=MeetingParticipantRole.attendee,
                ),
            ],
            confirm_participant_conflicts=confirmed,
            conflict_reason=(
                "Participante confirmou presença nos dois compromissos." if confirmed else None
            ),
        )

    async def create(organizer_id: UUID, title: str) -> str:
        async with sessions() as db:
            await start.wait()
            try:
                await MeetingService(db).create_for_workspace(
                    workspace_id,
                    payload(organizer_id=organizer_id, title=title),
                )
                await db.commit()
                return "committed"
            except HTTPException as error:
                await db.rollback()
                assert error.status_code == 409
                assert error.detail["code"] == "MEE-008"
                assert error.detail["participantUserIds"] == [str(attendee_id)]
                return "rejected"

    try:
        tasks = (
            asyncio.create_task(create(organizer_a, "Attendee concorrente A")),
            asyncio.create_task(create(organizer_b, "Attendee concorrente B")),
        )
        start.set()
        assert sorted(await asyncio.gather(*tasks)) == ["committed", "rejected"]

        async with sessions() as db:
            committed_organizer = await db.scalar(
                select(Meeting.responsible_user_id).where(
                    Meeting.workspace_id == workspace_id,
                    Meeting.starts_at == starts_at,
                    Meeting.status.notin_((MeetingStatus.cancelled, MeetingStatus.no_show)),
                )
            )
        retry_organizer = organizer_b if committed_organizer == organizer_a else organizer_a

        # A business-authorized conflict is explicit, reasoned and still uses
        # the same lock/check path; it is never a silent race bypass.
        async with sessions() as db:
            await MeetingService(db).create_for_workspace(
                workspace_id,
                payload(
                    organizer_id=retry_organizer,
                    title="Attendee concorrente confirmado",
                    confirmed=True,
                ),
            )
            await db.commit()

        async with sessions() as db:
            attendee_meeting_ids = select(Meeting.id).where(
                Meeting.workspace_id == workspace_id,
                Meeting.starts_at == starts_at,
                Meeting.status.notin_((MeetingStatus.cancelled, MeetingStatus.no_show)),
                # Relationship join keeps the assertion on the canonical
                # participant table rather than on organizer compatibility.
                Meeting.participants.any(user_id=attendee_id, active=True),
            )
            assert len((await db.scalars(attendee_meeting_ids)).all()) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_reschedules_serialize_one_organizer_calendar() -> None:
    """Concurrent updates use the same per-person lock as creates."""

    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    token = uuid4().hex[:12]
    workspace_id, organizer_id = await _seed_calendar_owner(sessions, token=token)
    baseline = datetime.now(UTC) + timedelta(days=4)
    meeting_ids: list[UUID] = []

    async with sessions() as db:
        for index in range(2):
            starts_at = baseline + timedelta(hours=index * 2)
            meeting = await MeetingService(db).create_for_workspace(
                workspace_id,
                MeetingCreate(
                    responsible_user_id=organizer_id,
                    title=f"Agenda original {index}",
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=30),
                    location="Online",
                ),
            )
            meeting_ids.append(meeting.id)
            await db.commit()

    target_start = baseline + timedelta(days=1)
    target_end = target_start + timedelta(minutes=30)
    start = asyncio.Event()

    async def reschedule(meeting_id: UUID) -> str:
        async with sessions() as db:
            await start.wait()
            try:
                await MeetingService(db).update_for_workspace(
                    workspace_id,
                    meeting_id,
                    MeetingUpdate(
                        expected_version=1,
                        starts_at=target_start,
                        ends_at=target_end,
                    ),
                )
                await db.commit()
                return "committed"
            except HTTPException as error:
                await db.rollback()
                assert error.status_code == 409
                assert error.detail["code"] == "MEE-007"
                return "rejected"

    try:
        tasks = tuple(asyncio.create_task(reschedule(meeting_id)) for meeting_id in meeting_ids)
        start.set()
        assert sorted(await asyncio.gather(*tasks)) == ["committed", "rejected"]

        async with sessions() as db:
            assert (
                await db.scalar(
                    select(Meeting.id).where(
                        Meeting.workspace_id == workspace_id,
                        Meeting.responsible_user_id == organizer_id,
                        Meeting.starts_at == target_start,
                        Meeting.status.notin_((MeetingStatus.cancelled, MeetingStatus.no_show)),
                    )
                )
                in meeting_ids
            )
    finally:
        await engine.dispose()
