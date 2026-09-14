from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.db.append_only import ImmutableRecordError
from app.models.omnichannel_v6 import WorkspaceTeam
from app.models.staff_availability import (
    AvailabilityAuditAction,
    AvailabilityChangeType,
    AvailabilityExceptionKind,
    AvailabilityMode,
    AvailabilityRequestStatus,
    StaffAvailabilityAuditEvent,
    StaffAvailabilityChangeRequest,
    StaffAvailabilityException,
    StaffAvailabilityProfile,
    StaffRecurringAvailability,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.staff_availability import StaffAvailabilityChangeRequestCreate
from app.services.staff_availability_service import (
    AvailabilityAuthorizationError,
    AvailabilityConflictError,
    AvailabilityNotFoundError,
    StaffAvailabilityService,
)

MONDAY_INSTANT = datetime(2026, 7, 20, 13, 0, tzinfo=UTC)  # 10:00 in São Paulo


def _utc_value(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _member(
    db: AsyncSession,
    *,
    workspace_id,
    name: str,
    role: WorkspaceRole,
    permission_denials: list[dict] | None = None,
) -> tuple[User, WorkspaceMembership, AuthContext]:
    unique = uuid4().hex[:10]
    user = User(
        name=name,
        email=f"{unique}@availability.example",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="".join(part[0] for part in name.split())[:4].upper() or "AV",
        color="#123456",
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id=user.id,
        role=role,
        status=MembershipStatus.active,
        is_default=True,
        permission_denials=permission_denials or [],
    )
    db.add(membership)
    await db.flush()
    return (
        user,
        membership,
        AuthContext(
            user_id=user.id,
            workspace_id=workspace_id,
            membership_id=membership.id,
            role=role,
            is_internal_staff=False,
        ),
    )


def _schedule_request(
    user_id,
    *,
    starts_at: str = "09:00:00",
    ends_at: str = "18:00:00",
    expected_version: int | None = None,
) -> StaffAvailabilityChangeRequestCreate:
    return StaffAvailabilityChangeRequestCreate(
        target_user_id=user_id,
        expected_version=expected_version,
        reason="Configurar jornada semanal",
        proposed_change={
            "change_type": "replace_recurring_schedule",
            "timezone": "America/Sao_Paulo",
            "slots": [
                {
                    "weekday": 0,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "label": "Segunda-feira",
                }
            ],
        },
    )


def _complete_configuration_request(
    user_id,
    *,
    expected_version: int | None = None,
    mode: str = "away",
    schedule_start: str = "09:00",
    schedule_end: str = "18:00",
    message: str = "Estou ausente. A equipe dará continuidade.",
    activation: dict | None = None,
    redirect: dict | None = None,
    automatic_swap: bool = False,
    exceptions: list[dict] | None = None,
) -> StaffAvailabilityChangeRequestCreate:
    return StaffAvailabilityChangeRequestCreate(
        target_user_id=user_id,
        expected_version=expected_version,
        reason="Substituir configuração completa de disponibilidade",
        proposed_change={
            "changeType": "replace_configuration",
            "mode": mode,
            "timezone": "America/Sao_Paulo",
            "weeklyAvailability": [
                {
                    "weekday": weekday,
                    "enabled": weekday == 0,
                    "windows": (
                        [{"start": schedule_start, "end": schedule_end}] if weekday == 0 else []
                    ),
                }
                for weekday in range(7)
            ],
            "exceptions": exceptions or [],
            "absenceMessage": {"enabled": True, "text": message},
            "activation": activation
            or {
                "mode": "now",
                "startsAt": None,
                "endsAt": None,
            },
            "redirect": redirect or {"kind": "none", "targetId": None},
            "automaticSwap": automatic_swap,
        },
    )


async def _approved_schedule(
    service: StaffAvailabilityService,
    *,
    workspace_id,
    staff_user_id,
    requester: AuthContext,
    approver: AuthContext,
    key: str,
) -> StaffAvailabilityChangeRequest:
    request = await service.submit_change(
        workspace_id=workspace_id,
        actor=requester,
        payload=_schedule_request(staff_user_id),
        idempotency_key=key,
    )
    return await service.approve_request(
        workspace_id=workspace_id,
        request_id=request.id,
        actor=approver,
        note="Jornada conferida",
    )


@pytest.mark.asyncio
async def test_schedule_stays_pending_until_master_approval_and_is_audited(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador Disponível",
        role=WorkspaceRole.operator,
    )
    service = StaffAvailabilityService(db_session)

    request = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_schedule_request(user.id, expected_version=0),
        idempotency_key="schedule-first-001",
    )

    assert request.status is AvailabilityRequestStatus.pending
    assert (
        await db_session.scalar(
            select(StaffAvailabilityProfile).where(
                StaffAvailabilityProfile.workspace_id == auth_context.workspace_id,
                StaffAvailabilityProfile.staff_user_id == user.id,
            )
        )
        is None
    )
    assert await db_session.scalar(select(func.count(StaffAvailabilityAuditEvent.id))) == 1

    approved = await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=request.id,
        actor=auth_context,
        note="Aprovado pelo titular",
    )
    profile = await db_session.scalar(
        select(StaffAvailabilityProfile).where(
            StaffAvailabilityProfile.workspace_id == auth_context.workspace_id,
            StaffAvailabilityProfile.staff_user_id == user.id,
        )
    )
    slots = list(
        (
            await db_session.scalars(
                select(StaffRecurringAvailability).where(
                    StaffRecurringAvailability.profile_id == profile.id
                )
            )
        ).all()
    )

    assert approved.status is AvailabilityRequestStatus.approved
    assert approved.applied_version == 1
    assert approved.decision_policy["authorization"] == "account_master"
    assert profile is not None
    assert profile.version == 1
    assert profile.schedule_configured is True
    assert len(slots) == 1
    assert (
        await service.evaluate(
            workspace_id=auth_context.workspace_id,
            staff_user_id=user.id,
            at=MONDAY_INSTANT,
        )
    ).is_available is True
    outside = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        staff_user_id=user.id,
        at=datetime(2026, 7, 20, 23, 0, tzinfo=UTC),
    )
    assert outside.is_available is False
    assert outside.reason == "outside_recurring_schedule"
    actions = list(
        (
            await db_session.scalars(
                select(StaffAvailabilityAuditEvent.action).order_by(
                    StaffAvailabilityAuditEvent.occurred_at,
                    StaffAvailabilityAuditEvent.id,
                )
            )
        ).all()
    )
    assert set(actions) == {
        AvailabilityAuditAction.request_submitted,
        AvailabilityAuditAction.schedule_replaced,
        AvailabilityAuditAction.request_approved,
    }


@pytest.mark.asyncio
async def test_four_eyes_and_manager_permission_policy_are_enforced(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador Quatro Olhos",
        role=WorkspaceRole.operator,
    )
    _, _, manager = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Gestora Jurídica",
        role=WorkspaceRole.legal_manager,
    )
    _, _, denied_manager = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Gestora Sem Aprovação",
        role=WorkspaceRole.legal_manager,
        permission_denials=[{"module": "aprovacoes", "action": "approve", "scope": "workspace"}],
    )
    service = StaffAvailabilityService(db_session)
    request = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_schedule_request(user.id),
        idempotency_key="four-eyes-001",
    )

    with pytest.raises(AvailabilityAuthorizationError, match="Four-eyes"):
        await service.approve_request(
            workspace_id=auth_context.workspace_id,
            request_id=request.id,
            actor=employee,
        )
    with pytest.raises(AvailabilityAuthorizationError, match="effective"):
        await service.approve_request(
            workspace_id=auth_context.workspace_id,
            request_id=request.id,
            actor=denied_manager,
        )

    approved = await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=request.id,
        actor=manager,
    )
    assert approved.status is AvailabilityRequestStatus.approved
    assert approved.decision_policy["authorization"] == "authorized_manager"
    assert approved.decision_policy["effectivePermission"] == "aprovacoes:approve"


@pytest.mark.asyncio
async def test_manager_changes_require_account_master_approval(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    manager_user, _, manager = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Gestor Alvo",
        role=WorkspaceRole.legal_manager,
    )
    _, _, another_manager = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Outro Gestor",
        role=WorkspaceRole.legal_manager,
    )
    service = StaffAvailabilityService(db_session)
    request = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=manager,
        payload=_schedule_request(manager_user.id),
        idempotency_key="manager-target-001",
    )

    with pytest.raises(AvailabilityAuthorizationError, match="account master"):
        await service.approve_request(
            workspace_id=auth_context.workspace_id,
            request_id=request.id,
            actor=another_manager,
        )
    approved = await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=request.id,
        actor=auth_context,
    )
    assert approved.status is AvailabilityRequestStatus.approved


@pytest.mark.asyncio
async def test_idempotency_and_optimistic_version_prevent_lost_updates(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador Concorrente",
        role=WorkspaceRole.operator,
    )
    service = StaffAvailabilityService(db_session)
    first = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_schedule_request(user.id),
        idempotency_key="concurrency-001",
    )
    duplicate = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_schedule_request(user.id),
        idempotency_key="concurrency-001",
    )
    second = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_schedule_request(user.id, starts_at="10:00:00"),
        idempotency_key="concurrency-002",
    )

    assert duplicate.id == first.id
    with pytest.raises(AvailabilityConflictError, match="different proposal"):
        await service.submit_change(
            workspace_id=auth_context.workspace_id,
            actor=employee,
            payload=_schedule_request(user.id, starts_at="11:00:00"),
            idempotency_key="concurrency-001",
        )

    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=first.id,
        actor=auth_context,
    )
    retry_after_version_advanced = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_schedule_request(user.id),
        idempotency_key="concurrency-001",
    )
    assert retry_after_version_advanced.id == first.id
    with pytest.raises(AvailabilityConflictError, match="aggregate changed"):
        await service.approve_request(
            workspace_id=auth_context.workspace_id,
            request_id=second.id,
            actor=auth_context,
        )
    assert second.status is AvailabilityRequestStatus.pending


@pytest.mark.asyncio
async def test_complete_configuration_is_applied_and_replaced_in_one_approval(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador Configuração Atômica",
        role=WorkspaceRole.operator,
    )
    team = WorkspaceTeam(
        workspace_id=auth_context.workspace_id,
        name="Cobertura Atômica",
        slug=f"cobertura-atomica-{uuid4().hex[:8]}",
        description="Equipe de cobertura temporária",
        team_type="service",
        color="#123456",
        capacity=10,
        routing_strategy="round_robin",
        schedule={},
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    service = StaffAvailabilityService(db_session)
    first_payload = _complete_configuration_request(
        user.id,
        expected_version=0,
        activation={
            "mode": "scheduled",
            "startsAt": "2026-08-03T08:00:00",
            "endsAt": "2026-08-14T18:00:00",
        },
        redirect={"kind": "team", "targetId": str(team.id)},
        automatic_swap=True,
        exceptions=[
            {
                "id": "vacation-2026",
                "kind": "vacation",
                "name": "Férias",
                "startsOn": "2026-08-03",
                "endsOn": "2026-08-04",
                "mode": "unavailable",
                "windows": [],
            },
            {
                "id": "holiday-2026",
                "kind": "holiday",
                "name": "Feriado local",
                "startsOn": "2026-08-05",
                "endsOn": "2026-08-05",
                "mode": "unavailable",
                "windows": [],
            },
            {
                "id": "custom-2026",
                "kind": "exception",
                "name": "Plantão parcial",
                "startsOn": "2026-08-06",
                "endsOn": "2026-08-06",
                "mode": "custom_hours",
                "windows": [{"start": "10:00", "end": "12:00"}],
            },
        ],
    )

    first = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=first_payload,
        idempotency_key="complete-configuration-001",
    )

    assert first.change_type is AvailabilityChangeType.replace_configuration
    assert first.status is AvailabilityRequestStatus.pending
    assert first.base_version == 0
    assert (
        await db_session.scalar(
            select(StaffAvailabilityProfile.id).where(
                StaffAvailabilityProfile.workspace_id == auth_context.workspace_id,
                StaffAvailabilityProfile.staff_user_id == user.id,
            )
        )
        is None
    )
    assert (
        await db_session.scalar(
            select(func.count(StaffRecurringAvailability.id)).where(
                StaffRecurringAvailability.workspace_id == auth_context.workspace_id
            )
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count(StaffAvailabilityException.id)).where(
                StaffAvailabilityException.workspace_id == auth_context.workspace_id,
                StaffAvailabilityException.staff_user_id == user.id,
            )
        )
        == 0
    )

    approved_first = await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=first.id,
        actor=auth_context,
        note="Configuração completa conferida",
    )
    profile = await db_session.scalar(
        select(StaffAvailabilityProfile).where(
            StaffAvailabilityProfile.workspace_id == auth_context.workspace_id,
            StaffAvailabilityProfile.staff_user_id == user.id,
        )
    )
    slots = list(
        (
            await db_session.scalars(
                select(StaffRecurringAvailability).where(
                    StaffRecurringAvailability.profile_id == profile.id
                )
            )
        ).all()
    )
    active_exceptions = list(
        (
            await db_session.scalars(
                select(StaffAvailabilityException).where(
                    StaffAvailabilityException.workspace_id == auth_context.workspace_id,
                    StaffAvailabilityException.staff_user_id == user.id,
                    StaffAvailabilityException.deleted_at.is_(None),
                )
            )
        ).all()
    )

    assert approved_first.applied_version == 1
    assert profile is not None
    assert profile.version == 1
    assert profile.availability_mode is AvailabilityMode.away
    assert _utc_value(profile.availability_starts_at) == datetime(2026, 8, 3, 11, 0, tzinfo=UTC)
    assert _utc_value(profile.availability_ends_at) == datetime(2026, 8, 14, 21, 0, tzinfo=UTC)
    assert profile.redirect_team_id == team.id
    assert profile.auto_reassign_enabled is True
    assert len(slots) == 1
    assert slots[0].weekday == 0
    assert slots[0].starts_at.isoformat() == "09:00:00"
    assert slots[0].ends_at.isoformat() == "18:00:00"
    assert slots[0].source_request_id == first.id
    assert len(active_exceptions) == 4
    assert {item.kind for item in active_exceptions} == {
        AvailabilityExceptionKind.vacation,
        AvailabilityExceptionKind.holiday,
        AvailabilityExceptionKind.unavailable,
        AvailabilityExceptionKind.available_override,
    }
    assert {item.source_request_id for item in active_exceptions} == {first.id}
    effective = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        staff_user_id=user.id,
        at=datetime(2026, 8, 3, 14, 0, tzinfo=UTC),
    )
    assert effective.is_available is False
    assert effective.reason == "scheduled_away"
    assert effective.absence_message == "Estou ausente. A equipe dará continuidade."
    assert effective.redirect_team_id == team.id
    assert effective.auto_reassign_enabled is True

    replacement = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_complete_configuration_request(
            user.id,
            expected_version=1,
            mode="available",
            schedule_start="10:00",
            schedule_end="16:00",
            message="Disponibilidade regular restabelecida.",
        ),
        idempotency_key="complete-configuration-002",
    )

    assert replacement.status is AvailabilityRequestStatus.pending
    assert profile.version == 1
    assert profile.availability_mode is AvailabilityMode.away
    assert slots[0].starts_at.isoformat() == "09:00:00"
    assert all(item.deleted_at is None for item in active_exceptions)

    approved_replacement = await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=replacement.id,
        actor=auth_context,
    )
    replacement_slots = list(
        (
            await db_session.scalars(
                select(StaffRecurringAvailability).where(
                    StaffRecurringAvailability.profile_id == profile.id
                )
            )
        ).all()
    )
    remaining_exceptions = await db_session.scalar(
        select(func.count(StaffAvailabilityException.id)).where(
            StaffAvailabilityException.workspace_id == auth_context.workspace_id,
            StaffAvailabilityException.staff_user_id == user.id,
            StaffAvailabilityException.deleted_at.is_(None),
        )
    )
    replaced_audits = await db_session.scalar(
        select(func.count(StaffAvailabilityAuditEvent.id)).where(
            StaffAvailabilityAuditEvent.workspace_id == auth_context.workspace_id,
            StaffAvailabilityAuditEvent.action == AvailabilityAuditAction.configuration_replaced,
        )
    )

    assert approved_replacement.applied_version == 2
    assert profile.version == 2
    assert profile.availability_mode is AvailabilityMode.available
    assert profile.availability_starts_at is None
    assert profile.availability_ends_at is None
    assert profile.redirect_kind is None
    assert profile.auto_reassign_enabled is False
    assert len(replacement_slots) == 1
    assert replacement_slots[0].starts_at.isoformat() == "10:00:00"
    assert replacement_slots[0].ends_at.isoformat() == "16:00:00"
    assert replacement_slots[0].source_request_id == replacement.id
    assert remaining_exceptions == 0
    assert all(item.deleted_at is not None for item in active_exceptions)
    assert replaced_audits == 2


@pytest.mark.asyncio
async def test_complete_configuration_submission_is_idempotent_and_version_locked(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador Configuração Idempotente",
        role=WorkspaceRole.operator,
    )
    service = StaffAvailabilityService(db_session)
    first_payload = _complete_configuration_request(user.id, expected_version=0)

    first = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=first_payload,
        idempotency_key="complete-idempotency-001",
    )
    duplicate = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=first_payload,
        idempotency_key="complete-idempotency-001",
    )
    stale = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_complete_configuration_request(
            user.id,
            expected_version=0,
            schedule_start="10:00",
        ),
        idempotency_key="complete-idempotency-002",
    )

    assert duplicate.id == first.id
    with pytest.raises(AvailabilityConflictError, match="different proposal"):
        await service.submit_change(
            workspace_id=auth_context.workspace_id,
            actor=employee,
            payload=_complete_configuration_request(
                user.id,
                expected_version=0,
                message="Conteúdo divergente para a mesma chave.",
            ),
            idempotency_key="complete-idempotency-001",
        )

    approved = await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=first.id,
        actor=auth_context,
    )
    retry_after_approval = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=first_payload,
        idempotency_key="complete-idempotency-001",
    )

    assert approved.applied_version == 1
    assert retry_after_approval.id == first.id
    assert retry_after_approval.status is AvailabilityRequestStatus.approved
    with pytest.raises(AvailabilityConflictError, match="aggregate changed"):
        await service.approve_request(
            workspace_id=auth_context.workspace_id,
            request_id=stale.id,
            actor=auth_context,
        )
    assert stale.status is AvailabilityRequestStatus.pending
    assert (
        await db_session.scalar(
            select(func.count(StaffAvailabilityChangeRequest.id)).where(
                StaffAvailabilityChangeRequest.workspace_id == auth_context.workspace_id,
                StaffAvailabilityChangeRequest.requested_by_user_id == employee.user_id,
                StaffAvailabilityChangeRequest.idempotency_key == "complete-idempotency-001",
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(StaffAvailabilityAuditEvent.id)).where(
                StaffAvailabilityAuditEvent.request_id == first.id,
                StaffAvailabilityAuditEvent.action
                == AvailabilityAuditAction.configuration_replaced,
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_complete_configuration_rolls_back_as_one_transaction(
    db_session: AsyncSession,
    auth_context: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador Rollback Atômico",
        role=WorkspaceRole.operator,
    )
    service = StaffAvailabilityService(db_session)
    baseline_request = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_complete_configuration_request(
            user.id,
            expected_version=0,
            exceptions=[
                {
                    "id": "baseline-exception",
                    "kind": "exception",
                    "name": "Configuração anterior",
                    "startsOn": "2026-08-10",
                    "endsOn": "2026-08-10",
                    "mode": "unavailable",
                    "windows": [],
                }
            ],
        ),
        idempotency_key="complete-rollback-baseline",
    )
    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=baseline_request.id,
        actor=auth_context,
    )
    user_id = user.id
    baseline_request_id = baseline_request.id
    await db_session.commit()

    replacement = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_complete_configuration_request(
            user.id,
            expected_version=1,
            mode="available",
            schedule_start="11:00",
            schedule_end="17:00",
            message="Nova configuração que deverá falhar.",
            exceptions=[],
        ),
        idempotency_key="complete-rollback-replacement",
    )
    replacement_id = replacement.id
    await db_session.commit()

    async def fail_effective_audit(*_args, **_kwargs) -> None:
        raise RuntimeError("forced atomic failure")

    monkeypatch.setattr(service, "_audit_effective_change", fail_effective_audit)
    with pytest.raises(RuntimeError, match="forced atomic failure"):
        await service.approve_request(
            workspace_id=auth_context.workspace_id,
            request_id=replacement_id,
            actor=auth_context,
        )
    await db_session.rollback()
    db_session.expire_all()

    profile = await db_session.scalar(
        select(StaffAvailabilityProfile).where(
            StaffAvailabilityProfile.workspace_id == auth_context.workspace_id,
            StaffAvailabilityProfile.staff_user_id == user_id,
        )
    )
    slots = list(
        (
            await db_session.scalars(
                select(StaffRecurringAvailability).where(
                    StaffRecurringAvailability.profile_id == profile.id
                )
            )
        ).all()
    )
    active_exceptions = list(
        (
            await db_session.scalars(
                select(StaffAvailabilityException).where(
                    StaffAvailabilityException.workspace_id == auth_context.workspace_id,
                    StaffAvailabilityException.staff_user_id == user_id,
                    StaffAvailabilityException.deleted_at.is_(None),
                )
            )
        ).all()
    )
    persisted_request = await db_session.get(
        StaffAvailabilityChangeRequest,
        replacement_id,
    )
    replacement_audits = await db_session.scalar(
        select(func.count(StaffAvailabilityAuditEvent.id)).where(
            StaffAvailabilityAuditEvent.action == AvailabilityAuditAction.configuration_replaced,
        )
    )

    assert profile is not None
    assert profile.version == 1
    assert profile.availability_mode is AvailabilityMode.away
    assert profile.absence_message == "Estou ausente. A equipe dará continuidade."
    assert len(slots) == 1
    assert slots[0].starts_at.isoformat() == "09:00:00"
    assert slots[0].ends_at.isoformat() == "18:00:00"
    assert len(active_exceptions) == 1
    assert active_exceptions[0].title == "Configuração anterior"
    assert active_exceptions[0].source_request_id == baseline_request_id
    assert persisted_request is not None
    assert persisted_request.status is AvailabilityRequestStatus.pending
    assert persisted_request.applied_version is None
    assert replacement_audits == 1


@pytest.mark.asyncio
async def test_vacation_message_redirects_automatically_to_available_user(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    source_user, _, source = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Titular em Férias",
        role=WorkspaceRole.operator,
    )
    backup_user, _, backup = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Substituta Disponível",
        role=WorkspaceRole.operator,
    )
    service = StaffAvailabilityService(db_session)
    await _approved_schedule(
        service,
        workspace_id=auth_context.workspace_id,
        staff_user_id=source_user.id,
        requester=source,
        approver=auth_context,
        key="vacation-source-schedule",
    )
    await _approved_schedule(
        service,
        workspace_id=auth_context.workspace_id,
        staff_user_id=backup_user.id,
        requester=backup,
        approver=auth_context,
        key="vacation-backup-schedule",
    )
    vacation = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=source,
        idempotency_key="vacation-create-001",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=source_user.id,
            reason="Férias aprovadas pelo RH",
            proposed_change={
                "change_type": "create_exception",
                "scope": "staff",
                "kind": "vacation",
                "title": "Férias de julho",
                "starts_at": "2026-07-20T00:00:00Z",
                "ends_at": "2026-07-25T00:00:00Z",
                "all_day": True,
                "absence_message": "Estou de férias; a equipe dará continuidade.",
                "redirect": {"kind": "user", "user_id": str(backup_user.id)},
                "auto_reassign": True,
            },
        ),
    )
    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=vacation.id,
        actor=auth_context,
    )

    effective = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        staff_user_id=source_user.id,
        at=MONDAY_INSTANT,
    )
    resolution = await service.resolve_responsibility(
        workspace_id=auth_context.workspace_id,
        responsible_user_id=source_user.id,
        at=MONDAY_INSTANT,
    )

    assert effective.is_available is False
    assert effective.active_exception_kind is AvailabilityExceptionKind.vacation
    assert effective.absence_message == "Estou de férias; a equipe dará continuidade."
    assert effective.auto_reassign_enabled is True
    assert resolution.changed is True
    assert resolution.effective_user_id == backup_user.id
    assert resolution.reason == "redirected_to_available_user"
    assert resolution.path == [source_user.id, backup_user.id]


@pytest.mark.asyncio
async def test_scheduled_absence_routing_can_redirect_to_workspace_team(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador Fora do Expediente",
        role=WorkspaceRole.operator,
    )
    team = WorkspaceTeam(
        workspace_id=auth_context.workspace_id,
        name="Plantão Jurídico",
        slug=f"plantao-{uuid4().hex[:8]}",
        description="Equipe temporária",
        team_type="service",
        color="#123456",
        capacity=10,
        routing_strategy="round_robin",
        schedule={},
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    service = StaffAvailabilityService(db_session)
    await _approved_schedule(
        service,
        workspace_id=auth_context.workspace_id,
        staff_user_id=user.id,
        requester=employee,
        approver=auth_context,
        key="team-route-schedule",
    )
    routing = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        idempotency_key="team-route-window-001",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=user.id,
            reason="Ativar plantão após expediente",
            proposed_change={
                "change_type": "set_absence_routing",
                "absence_message": "O plantão continuará o atendimento.",
                "absence_message_starts_at": "2026-07-20T21:00:00Z",
                "absence_message_ends_at": "2026-07-21T02:00:00Z",
                "redirect": {"kind": "team", "team_id": str(team.id)},
                "redirect_starts_at": "2026-07-20T21:00:00Z",
                "redirect_ends_at": "2026-07-21T02:00:00Z",
                "auto_reassign_enabled": True,
            },
        ),
    )
    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=routing.id,
        actor=auth_context,
    )
    after_hours = datetime(2026, 7, 20, 22, 30, tzinfo=UTC)

    resolution = await service.resolve_responsibility(
        workspace_id=auth_context.workspace_id,
        responsible_user_id=user.id,
        at=after_hours,
    )
    assert resolution.changed is True
    assert resolution.effective_team_id == team.id
    assert resolution.effective_user_id is None
    assert resolution.reason == "redirected_to_team"


@pytest.mark.parametrize(
    "reason",
    [
        "unavailable_without_active_redirect",
        "invalid_redirect_target",
        "redirect_cycle",
        "redirect_hop_limit",
    ],
)
def test_terminal_unavailable_resolution_never_names_an_effective_human(reason: str) -> None:
    workspace_id = uuid4()
    requested_user_id = uuid4()
    resolution = StaffAvailabilityService._resolution(
        workspace_id,
        requested_user_id,
        MONDAY_INSTANT,
        [requested_user_id],
        reason=reason,
    )
    assert resolution.effective_user_id is None
    assert resolution.effective_team_id is None
    assert resolution.reason == reason


@pytest.mark.asyncio
async def test_staff_override_takes_precedence_over_workspace_holiday(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador de Plantão",
        role=WorkspaceRole.operator,
    )
    _, _, admin = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Administradora do Feriado",
        role=WorkspaceRole.workspace_admin,
    )
    service = StaffAvailabilityService(db_session)
    holiday = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=admin,
        idempotency_key="workspace-holiday-001",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=None,
            reason="Cadastrar feriado do workspace",
            proposed_change={
                "change_type": "create_exception",
                "scope": "workspace",
                "kind": "holiday",
                "title": "Feriado local",
                "starts_at": "2026-07-20T00:00:00Z",
                "ends_at": "2026-07-21T00:00:00Z",
                "all_day": True,
                "absence_message": "Escritório fechado no feriado.",
            },
        ),
    )
    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=holiday.id,
        actor=auth_context,
    )
    unavailable = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        staff_user_id=user.id,
        at=MONDAY_INSTANT,
    )
    assert unavailable.is_available is False
    assert unavailable.active_exception_kind is AvailabilityExceptionKind.holiday

    override = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        idempotency_key="staff-override-001",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=user.id,
            reason="Plantão previamente autorizado",
            proposed_change={
                "change_type": "create_exception",
                "scope": "staff",
                "kind": "available_override",
                "title": "Plantão no feriado",
                "starts_at": "2026-07-20T12:00:00Z",
                "ends_at": "2026-07-20T18:00:00Z",
            },
        ),
    )
    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=override.id,
        actor=auth_context,
    )
    available = await service.evaluate(
        workspace_id=auth_context.workspace_id,
        staff_user_id=user.id,
        at=MONDAY_INSTANT,
    )
    assert available.is_available is True
    assert available.active_exception_kind is AvailabilityExceptionKind.available_override
    assert available.reason == "available_override"


@pytest.mark.asyncio
async def test_exception_update_and_delete_are_pending_versioned_and_soft_deleted(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador com Exceção",
        role=WorkspaceRole.operator,
    )
    service = StaffAvailabilityService(db_session)
    created_request = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        idempotency_key="exception-lifecycle-create",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=user.id,
            reason="Criar compromisso indisponível",
            proposed_change={
                "change_type": "create_exception",
                "scope": "staff",
                "kind": "unavailable",
                "title": "Compromisso externo",
                "starts_at": "2026-07-20T12:00:00Z",
                "ends_at": "2026-07-20T15:00:00Z",
                "absence_message": "Retorno em breve.",
            },
        ),
    )
    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=created_request.id,
        actor=auth_context,
    )
    exception = await db_session.scalar(
        select(StaffAvailabilityException).where(
            StaffAvailabilityException.source_request_id == created_request.id
        )
    )
    profile = await db_session.scalar(
        select(StaffAvailabilityProfile).where(
            StaffAvailabilityProfile.staff_user_id == user.id,
            StaffAvailabilityProfile.workspace_id == auth_context.workspace_id,
        )
    )
    assert exception is not None
    assert profile.version == 1

    update_request = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        idempotency_key="exception-lifecycle-update",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=user.id,
            expected_version=profile.version,
            reason="Ampliar compromisso",
            proposed_change={
                "change_type": "update_exception",
                "exception_id": str(exception.id),
                "expected_exception_version": 1,
                "kind": "unavailable",
                "title": "Compromisso externo ampliado",
                "starts_at": "2026-07-20T12:00:00Z",
                "ends_at": "2026-07-20T16:00:00Z",
                "absence_message": "Retorno após as 13h.",
            },
        ),
    )
    assert exception.title == "Compromisso externo"
    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=update_request.id,
        actor=auth_context,
    )
    assert exception.title == "Compromisso externo ampliado"
    assert exception.version == 2
    assert profile.version == 2

    delete_request = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        idempotency_key="exception-lifecycle-delete",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=user.id,
            expected_version=profile.version,
            reason="Remover compromisso cancelado",
            proposed_change={
                "change_type": "delete_exception",
                "exception_id": str(exception.id),
                "expected_exception_version": 2,
            },
        ),
    )
    assert exception.deleted_at is None
    await service.approve_request(
        workspace_id=auth_context.workspace_id,
        request_id=delete_request.id,
        actor=auth_context,
    )
    assert exception.deleted_at is not None
    assert exception.version == 3
    assert profile.version == 3


@pytest.mark.asyncio
async def test_workspace_isolation_blocks_foreign_targets_and_request_reads(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    source_user, _, source = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Origem Isolada",
        role=WorkspaceRole.operator,
    )
    other_workspace = Workspace(
        name="Outro Workspace",
        slug=f"outro-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    foreign_user, _, foreign_owner = await _member(
        db_session,
        workspace_id=other_workspace.id,
        name="Usuário Estrangeiro",
        role=WorkspaceRole.workspace_owner,
    )
    service = StaffAvailabilityService(db_session)

    with pytest.raises(AvailabilityNotFoundError, match="this workspace"):
        await service.submit_change(
            workspace_id=auth_context.workspace_id,
            actor=source,
            idempotency_key="foreign-redirect-001",
            payload=StaffAvailabilityChangeRequestCreate(
                target_user_id=source_user.id,
                reason="Tentar destino externo",
                proposed_change={
                    "change_type": "create_exception",
                    "scope": "staff",
                    "kind": "vacation",
                    "title": "Férias",
                    "starts_at": "2026-07-20T00:00:00Z",
                    "ends_at": "2026-07-21T00:00:00Z",
                    "redirect": {
                        "kind": "user",
                        "user_id": str(foreign_user.id),
                    },
                    "auto_reassign": True,
                },
            ),
        )

    own_request = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=source,
        payload=_schedule_request(source_user.id),
        idempotency_key="isolation-request-001",
    )
    with pytest.raises(AvailabilityNotFoundError, match="request not found"):
        await service.get_request(
            workspace_id=other_workspace.id,
            request_id=own_request.id,
            actor=foreign_owner,
        )


@pytest.mark.asyncio
async def test_rejection_cancellation_and_audit_immutability(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    user, _, employee = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Operador Auditável",
        role=WorkspaceRole.operator,
    )
    service = StaffAvailabilityService(db_session)
    rejected = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_schedule_request(user.id),
        idempotency_key="reject-request-001",
    )
    await service.reject_request(
        workspace_id=auth_context.workspace_id,
        request_id=rejected.id,
        actor=auth_context,
        note="Escala incompatível",
    )
    cancelled = await service.submit_change(
        workspace_id=auth_context.workspace_id,
        actor=employee,
        payload=_schedule_request(user.id),
        idempotency_key="cancel-request-001",
    )
    await service.cancel_request(
        workspace_id=auth_context.workspace_id,
        request_id=cancelled.id,
        actor=employee,
        note="Solicitação duplicada",
    )

    assert rejected.status is AvailabilityRequestStatus.rejected
    assert cancelled.status is AvailabilityRequestStatus.cancelled
    assert (
        await db_session.scalar(
            select(StaffAvailabilityProfile.id).where(
                StaffAvailabilityProfile.staff_user_id == user.id
            )
        )
        is None
    )
    event = await db_session.scalar(
        select(StaffAvailabilityAuditEvent).where(
            StaffAvailabilityAuditEvent.action == AvailabilityAuditAction.request_rejected
        )
    )
    event.summary = "Attempted tampering"
    with pytest.raises(ImmutableRecordError):
        await db_session.flush()
    await db_session.rollback()
