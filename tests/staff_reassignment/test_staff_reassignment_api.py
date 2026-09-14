"""Provas da continuidade de funcionários nas duas superfícies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.booking_link import BookingLink
from app.models.client import Client
from app.models.growth_crm import GrowthProspect
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.models.meeting import (
    Meeting,
    MeetingNotificationOutbox,
    MeetingNotificationStatus,
    MeetingParticipant,
    MeetingParticipantRole,
)
from app.models.staff_reassignment import (
    PlatformStaffReassignmentEvent,
    PlatformStaffReassignmentItem,
    WorkspaceStaffReassignmentEvent,
    WorkspaceStaffReassignmentItem,
)
from app.models.task import (
    Task,
    TaskEvent,
    TaskParticipant,
    TaskParticipantRole,
)
from app.models.task_reminder import (
    TaskReminderChannel,
    TaskReminderDeliveryStatus,
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
from app.services.meeting_service import MeetingService
from app.services.staff_reassignment_service import StaffReassignmentService
from app.services.task_reminder_service import (
    TaskReminderMutationContext,
    TaskReminderService,
)
from app.services.task_service import TaskService

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("active_subscription"),
]


async def _workspace_employee(
    db_session,
    *,
    workspace_id,
    name: str,
    status: MembershipStatus,
    is_active: bool,
    role: WorkspaceRole = WorkspaceRole.operator,
) -> tuple[User, WorkspaceMembership]:
    suffix = uuid4().hex
    user = User(
        name=name,
        email=f"{suffix}@continuidade.local",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials=name[:2].upper(),
        color="#0e3d5c",
        is_active=is_active,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user.id,
        role=role,
        status=status,
        is_default=False,
    )
    db_session.add(membership)
    await db_session.flush()
    return user, membership


async def _platform_employee(
    db_session,
    *,
    name: str,
    active: bool,
) -> User:
    user = User(
        name=name,
        email=f"{uuid4().hex}@blackbox.local",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials=name[:2].upper(),
        color="#0e3d5c",
        is_active=active,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_access_active=active,
        platform_role_package="technical_support",
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_workspace_preview_apply_idempotency_and_safe_restore(
    client,
    db_session,
    auth_context,
):
    source, source_membership = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Funcionário afastado",
        status=MembershipStatus.suspended,
        is_active=False,
    )
    target, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Funcionário substituto",
        status=MembershipStatus.active,
        is_active=True,
    )
    link = BookingLink(
        workspace_id=auth_context.workspace_id,
        created_by_user_id=auth_context.user_id,
        responsible_user_id=source.id,
        public_key=uuid4().hex,
        title="Reunião de continuidade",
        duration_minutes=45,
        meeting_provider="manual",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        is_active=True,
        page_overrides={},
    )
    db_session.add(link)
    await db_session.flush()

    preview = await client.post(
        "/api/v1/admin/staff-reassignments/preview",
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["booking_links"],
        },
    )
    assert preview.status_code == 200
    assert preview.json()["counts"] == {"booking_links": 1}

    starts_at = datetime.now(UTC)
    payload = {
        "sourceUserId": str(source.id),
        "targetUserId": str(target.id),
        "scopes": ["booking_links"],
        "mode": "temporary",
        "reason": "Cobertura temporária durante o afastamento.",
        "startsAt": starts_at.isoformat(),
        "endsAt": (starts_at + timedelta(hours=2)).isoformat(),
    }
    headers = {"Idempotency-Key": f"workspace-{uuid4().hex}"}
    created = await client.post(
        "/api/v1/admin/staff-reassignments",
        json=payload,
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["surface"] == "workspace"
    assert body["assignmentSummary"] == {"booking_links": 1}
    assert body["auditEvents"][0]["action"] == "applied"
    await db_session.refresh(link)
    assert link.responsible_user_id == target.id

    item = await db_session.scalar(
        select(WorkspaceStaffReassignmentItem).where(
            WorkspaceStaffReassignmentItem.plan_id == body["id"]
        )
    )
    assert item is not None
    assert item.tag_label == body["tagLabel"]
    assert item.previous_user_id == source.id

    repeated = await client.post(
        "/api/v1/admin/staff-reassignments",
        json=payload,
        headers=headers,
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == body["id"]

    conflicting_payload = {**payload, "reason": "Outro pedido incompatível."}
    conflict = await client.post(
        "/api/v1/admin/staff-reassignments",
        json=conflicting_payload,
        headers=headers,
    )
    assert conflict.status_code == 409

    source.is_active = True
    source_membership.status = MembershipStatus.active
    restored = await client.post(
        f"/api/v1/admin/staff-reassignments/{body['id']}/restore",
        json={
            "expectedVersion": body["version"],
            "reason": "Funcionário retornou ao trabalho.",
        },
    )
    assert restored.status_code == 200
    restored_body = restored.json()
    assert restored_body["status"] == "restored"
    assert [event["action"] for event in restored_body["auditEvents"]] == [
        "applied",
        "restored",
    ]
    await db_session.refresh(link)
    assert link.responsible_user_id == source.id


async def test_workspace_restore_preserves_manual_reassignment_as_conflict(
    client,
    db_session,
    auth_context,
):
    source, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Origem ativa",
        status=MembershipStatus.active,
        is_active=True,
    )
    target, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Destino temporário",
        status=MembershipStatus.active,
        is_active=True,
    )
    third_party, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Responsável manual",
        status=MembershipStatus.active,
        is_active=True,
    )
    link = BookingLink(
        workspace_id=auth_context.workspace_id,
        created_by_user_id=auth_context.user_id,
        responsible_user_id=source.id,
        public_key=uuid4().hex,
        title="Reunião com conflito",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        page_overrides={},
    )
    db_session.add(link)
    await db_session.flush()
    starts_at = datetime.now(UTC)
    created = await client.post(
        "/api/v1/admin/staff-reassignments",
        headers={"Idempotency-Key": f"conflict-{uuid4().hex}"},
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["booking_links"],
            "mode": "temporary",
            "reason": "Cobertura temporária testada.",
            "startsAt": starts_at.isoformat(),
            "endsAt": (starts_at + timedelta(hours=1)).isoformat(),
        },
    )
    assert created.status_code == 201
    link.responsible_user_id = third_party.id
    await db_session.flush()

    restored = await client.post(
        f"/api/v1/admin/staff-reassignments/{created.json()['id']}/restore",
        json={
            "expectedVersion": 1,
            "reason": "Tentativa segura de retorno.",
        },
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "partially_restored"
    assert restored.json()["conflictCount"] == 1
    await db_session.refresh(link)
    assert link.responsible_user_id == third_party.id


async def test_workspace_expired_plan_is_restored_automatically(
    client,
    db_session,
    auth_context,
):
    source, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Origem do retorno automático",
        status=MembershipStatus.active,
        is_active=True,
    )
    target, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Destino do retorno automático",
        status=MembershipStatus.active,
        is_active=True,
    )
    link = BookingLink(
        workspace_id=auth_context.workspace_id,
        created_by_user_id=auth_context.user_id,
        responsible_user_id=source.id,
        public_key=uuid4().hex,
        title="Reunião com retorno automático",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        page_overrides={},
    )
    db_session.add(link)
    await db_session.flush()
    starts_at = datetime.now(UTC) - timedelta(hours=2)
    created = await client.post(
        "/api/v1/admin/staff-reassignments",
        headers={"Idempotency-Key": f"expired-{uuid4().hex}"},
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["booking_links"],
            "mode": "temporary",
            "reason": "Cobertura temporária já encerrada.",
            "startsAt": starts_at.isoformat(),
            "endsAt": (starts_at + timedelta(hours=1)).isoformat(),
        },
    )
    assert created.status_code == 201
    await db_session.refresh(link)
    assert link.responsible_user_id == target.id

    restored_count = await StaffReassignmentService(db_session).restore_due_workspace(
        workspace_id=auth_context.workspace_id
    )

    assert restored_count == 1
    await db_session.refresh(link)
    assert link.responsible_user_id == source.id
    plan = await client.get(f"/api/v1/admin/staff-reassignments/{created.json()['id']}")
    assert plan.status_code == 200
    assert plan.json()["status"] == "restored"
    assert plan.json()["auditEvents"][-1]["details"]["automatic"] is True


async def test_workspace_task_and_meeting_transfer_use_canonical_boundaries(
    client,
    db_session,
    auth_context,
):
    source, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Owner operacional",
        status=MembershipStatus.active,
        is_active=True,
    )
    target, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Owner substituto",
        status=MembershipStatus.active,
        is_active=True,
    )
    observer, observer_membership = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Participante preservado",
        status=MembershipStatus.active,
        is_active=True,
    )
    source_membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
            WorkspaceMembership.user_id == source.id,
        )
    )
    assert source_membership is not None
    customer = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente da continuidade",
    )
    db_session.add(customer)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=customer.id,
        demand_type="Operação colaborativa",
        practice_area=PracticeArea.civel,
    )
    db_session.add(matter)
    await db_session.flush()
    due_at = datetime.now(UTC) + timedelta(days=2)
    task = Task(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        assigned_to=source.id,
        title="Tarefa transferida pelo domínio",
        due_at=due_at,
        due_date=due_at.date().isoformat(),
        participants=[
            TaskParticipant(
                workspace_id=auth_context.workspace_id,
                matter_id=matter.id,
                membership_id=source_membership.id,
                user_id=source.id,
                role=TaskParticipantRole.owner,
                required_for_completion=False,
                label_snapshot=source.name,
                active=True,
            ),
            TaskParticipant(
                workspace_id=auth_context.workspace_id,
                matter_id=matter.id,
                membership_id=observer_membership.id,
                user_id=observer.id,
                role=TaskParticipantRole.observer,
                required_for_completion=False,
                label_snapshot=observer.name,
                active=True,
            ),
        ],
    )
    db_session.add(task)
    await db_session.flush()
    rule = TaskReminderRule(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        task_id=task.id,
        relative_to=TaskReminderRelativeTo.general_due,
        offset_minutes_before=60,
        channel=TaskReminderChannel.in_app,
        recipient=TaskReminderRecipient.owner,
        recipient_user_id=None,
        recipient_key=TaskReminderRecipient.owner.value,
        state=TaskReminderRuleState.pending_due,
        active=True,
        version=1,
        created_by_user_id=auth_context.user_id,
    )
    db_session.add(rule)
    await db_session.flush()
    await TaskReminderService(db_session).sync_task(
        task,
        context=TaskReminderMutationContext(
            actor_user_id=auth_context.user_id,
            correlation_id="test:staff-reassignment:initial-reminder",
        ),
        reason="Preparação da tarefa para transferência.",
    )
    starts_at = datetime.now(UTC) + timedelta(days=1)
    meeting = Meeting(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        responsible_user_id=source.id,
        title="Compromisso transferido pelo domínio",
        meeting_url="https://meet.example.com/continuity",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        participants=[
            MeetingParticipant(
                workspace_id=auth_context.workspace_id,
                membership_id=source_membership.id,
                user_id=source.id,
                role=MeetingParticipantRole.organizer,
                label_snapshot=source.name,
                active=True,
            ),
            MeetingParticipant(
                workspace_id=auth_context.workspace_id,
                membership_id=observer_membership.id,
                user_id=observer.id,
                role=MeetingParticipantRole.attendee,
                label_snapshot=observer.name,
                active=True,
            ),
        ],
    )
    db_session.add(meeting)
    await db_session.flush()

    operation_start = datetime.now(UTC)
    created = await client.post(
        "/api/v1/admin/staff-reassignments",
        headers={"Idempotency-Key": f"canonical-{uuid4().hex}"},
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["tasks", "meetings"],
            "mode": "temporary",
            "reason": "Cobertura temporária com domínio canônico.",
            "startsAt": operation_start.isoformat(),
            "endsAt": (operation_start + timedelta(hours=2)).isoformat(),
        },
    )

    assert created.status_code == 201, created.text
    plan = created.json()
    transferred_task = await TaskService(db_session).get(
        task.id,
        auth_context.workspace_id,
    )
    transferred_meeting = await MeetingService(db_session).get_for_workspace(
        auth_context.workspace_id,
        meeting.id,
    )
    assert transferred_task is not None
    assert transferred_task.assigned_to == target.id
    assert transferred_task.version == 2
    assert {(item.user_id, item.role) for item in transferred_task.participants if item.active} == {
        (target.id, TaskParticipantRole.owner),
        (observer.id, TaskParticipantRole.observer),
    }
    assert transferred_meeting.responsible_user_id == target.id
    assert transferred_meeting.version == 2
    assert {
        (item.user_id, item.role) for item in transferred_meeting.participants if item.active
    } == {
        (target.id, MeetingParticipantRole.organizer),
        (observer.id, MeetingParticipantRole.attendee),
    }
    transfer_correlation_id = f"staff-reassignment:{plan['id']}:apply:tasks:{task.id}"
    transferred_task_event = await db_session.scalar(
        select(TaskEvent).where(
            TaskEvent.task_id == task.id,
            TaskEvent.correlation_id == transfer_correlation_id,
        )
    )
    assert transferred_task_event is not None
    assert transferred_task_event.actor_user_id == auth_context.user_id
    assert transferred_task_event.session_type == "system"
    assert "assignedTo" in transferred_task_event.changed_paths
    assert transferred_task_event.resource_version_before == 1
    assert transferred_task_event.resource_version_after == 2
    assert (
        await db_session.scalar(
                select(KlmAuditEvent.id).where(
                    KlmAuditEvent.entity_type == "meeting",
                    KlmAuditEvent.entity_id == meeting.id,
                    KlmAuditEvent.event_type == "meeting_updated",
                )
            )
        ) is not None
    pending_task_delivery = await db_session.scalar(
        select(TaskReminderOutbox)
        .where(
            TaskReminderOutbox.task_id == task.id,
            TaskReminderOutbox.status == TaskReminderDeliveryStatus.pending,
        )
        .order_by(TaskReminderOutbox.created_at.desc())
    )
    assert pending_task_delivery is not None
    assert pending_task_delivery.recipient_user_id == target.id
    current_meeting_intent = await db_session.scalar(
        select(MeetingNotificationOutbox).where(
            MeetingNotificationOutbox.meeting_id == meeting.id,
            MeetingNotificationOutbox.meeting_version == 2,
            MeetingNotificationOutbox.status == MeetingNotificationStatus.pending,
        )
    )
    assert current_meeting_intent is not None

    restored = await client.post(
        f"/api/v1/admin/staff-reassignments/{plan['id']}/restore",
        json={
            "expectedVersion": plan["version"],
            "reason": "Retorno do owner original confirmado.",
        },
    )

    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "restored"
    restored_task = await TaskService(db_session).get(
        task.id,
        auth_context.workspace_id,
    )
    restored_meeting = await MeetingService(db_session).get_for_workspace(
        auth_context.workspace_id,
        meeting.id,
    )
    assert restored_task is not None
    assert restored_task.assigned_to == source.id
    assert restored_task.version == 3
    assert restored_meeting.responsible_user_id == source.id
    assert restored_meeting.version == 3
    restore_correlation_id = f"staff-reassignment:{plan['id']}:restore:tasks:{task.id}"
    restored_task_event = await db_session.scalar(
        select(TaskEvent).where(
            TaskEvent.task_id == task.id,
            TaskEvent.correlation_id == restore_correlation_id,
        )
    )
    assert restored_task_event is not None
    assert restored_task_event.actor_user_id == auth_context.user_id
    assert restored_task_event.session_type == "system"
    assert "assignedTo" in restored_task_event.changed_paths
    assert restored_task_event.resource_version_before == 2
    assert restored_task_event.resource_version_after == 3
    latest_task_delivery = await db_session.scalar(
        select(TaskReminderOutbox)
        .where(
            TaskReminderOutbox.task_id == task.id,
            TaskReminderOutbox.status == TaskReminderDeliveryStatus.pending,
        )
        .order_by(TaskReminderOutbox.created_at.desc())
    )
    assert latest_task_delivery is not None
    assert latest_task_delivery.recipient_user_id == source.id
    assert (
        await db_session.scalar(
            select(MeetingNotificationOutbox.id).where(
                MeetingNotificationOutbox.meeting_id == meeting.id,
                MeetingNotificationOutbox.meeting_version == 3,
                MeetingNotificationOutbox.status == MeetingNotificationStatus.pending,
            )
        )
    ) is not None


async def test_platform_apply_is_physically_separated(
    platform_client,
    db_session,
    platform_auth_context,
):
    source = await _platform_employee(
        db_session,
        name="Suporte desligado",
        active=False,
    )
    target = await _platform_employee(
        db_session,
        name="Suporte substituto",
        active=True,
    )
    prospect = GrowthProspect(
        nome_escritorio="Conta em continuidade",
        owner_user_id=source.id,
        metadata_json={},
    )
    db_session.add(prospect)
    await db_session.flush()
    starts_at = datetime.now(UTC)
    headers = {"Idempotency-Key": f"platform-{uuid4().hex}"}
    payload = {
        "sourceUserId": str(source.id),
        "targetUserId": str(target.id),
        "scopes": ["growth_prospects"],
        "mode": "permanent",
        "reason": "Redistribuição definitiva após desligamento.",
        "startsAt": starts_at.isoformat(),
    }
    response = await platform_client.post(
        "/api/v1/platform/staff-reassignments",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["surface"] == "platform"
    assert body["workspaceId"] is None
    assert body["requestedByUserId"] == str(platform_auth_context.user_id)
    await db_session.refresh(prospect)
    assert prospect.owner_user_id == target.id
    assert len(prospect.metadata_json["continuityTransfers"]) == 1
    assert prospect.metadata_json["continuityTransfers"][0]["sourceUserId"] == str(source.id)

    platform_item = await db_session.scalar(
        select(PlatformStaffReassignmentItem).where(
            PlatformStaffReassignmentItem.plan_id == body["id"]
        )
    )
    platform_event = await db_session.scalar(
        select(PlatformStaffReassignmentEvent).where(
            PlatformStaffReassignmentEvent.plan_id == body["id"]
        )
    )
    assert platform_item is not None
    assert platform_event is not None
    assert (await db_session.scalar(select(WorkspaceStaffReassignmentItem))) is None
    assert (await db_session.scalar(select(WorkspaceStaffReassignmentEvent))) is None

    repeated = await platform_client.post(
        "/api/v1/platform/staff-reassignments",
        headers=headers,
        json=payload,
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == body["id"]


async def test_workspace_area_manager_only_reassigns_subordinate_roles(
    client,
    db_session,
    auth_context,
):
    actor_membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    actor_membership.role = WorkspaceRole.legal_manager
    auth_context.role = WorkspaceRole.legal_manager
    subordinate, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Atendente da área",
        status=MembershipStatus.active,
        is_active=True,
        role=WorkspaceRole.operator,
    )
    administrator, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Administrador da conta",
        status=MembershipStatus.active,
        is_active=True,
        role=WorkspaceRole.workspace_admin,
    )
    target, _ = await _workspace_employee(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Substituto autorizado",
        status=MembershipStatus.active,
        is_active=True,
    )
    await db_session.flush()

    allowed = await client.post(
        "/api/v1/admin/staff-reassignments/preview",
        json={
            "sourceUserId": str(subordinate.id),
            "targetUserId": str(target.id),
            "scopes": ["booking_links"],
        },
    )
    assert allowed.status_code == 200

    forbidden = await client.post(
        "/api/v1/admin/staff-reassignments/preview",
        json={
            "sourceUserId": str(administrator.id),
            "targetUserId": str(target.id),
            "scopes": ["booking_links"],
        },
    )
    assert forbidden.status_code == 403


async def test_blackbox_area_manager_cannot_access_another_area(
    platform_client,
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    actor.is_platform_admin = False
    actor.is_internal_staff = True
    actor.platform_access_active = True
    actor.platform_role_package = "technical_support"
    source = await _platform_employee(
        db_session,
        name="Funcionário da área comercial",
        active=False,
    )
    target = await _platform_employee(
        db_session,
        name="Substituto da área comercial",
        active=True,
    )
    await db_session.flush()

    forbidden_preview = await platform_client.post(
        "/api/v1/platform/staff-reassignments/preview",
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["growth_prospects"],
        },
    )
    assert forbidden_preview.status_code == 403

    forbidden_apply = await platform_client.post(
        "/api/v1/platform/staff-reassignments",
        headers={"Idempotency-Key": f"wrong-area-{uuid4().hex}"},
        json={
            "sourceUserId": str(source.id),
            "targetUserId": str(target.id),
            "scopes": ["growth_prospects"],
            "mode": "permanent",
            "reason": "Tentativa fora da área de responsabilidade.",
            "startsAt": datetime.now(UTC).isoformat(),
        },
    )
    assert forbidden_apply.status_code == 403


async def test_workspace_identity_cannot_use_blackbox_continuity(client):
    forbidden_platform = await client.get("/api/v1/platform/staff-reassignments")
    assert forbidden_platform.status_code == 403


async def test_platform_identity_cannot_use_workspace_continuity(platform_client):
    forbidden_workspace = await platform_client.get("/api/v1/admin/staff-reassignments")
    assert forbidden_workspace.status_code == 403
