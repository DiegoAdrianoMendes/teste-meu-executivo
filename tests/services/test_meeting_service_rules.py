from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.entitlement import EntitlementKey, OverrideMode, WorkspaceEntitlementOverride
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.models.meeting import MeetingNotificationOutbox
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)

pytestmark = pytest.mark.usefixtures("active_subscription")


def _meeting_headers() -> dict[str, str]:
    return {"Idempotency-Key": f"meeting-test-{uuid4().hex}"}


async def _enable_meeting_participants(
    db: AsyncSession,
    workspace_id,
    *,
    limit: int = 5,
) -> None:
    db.add(
        WorkspaceEntitlementOverride(
            workspace_id=workspace_id,
            entitlement=EntitlementKey.meeting_participants,
            mode=OverrideMode.grant,
            quantity=limit,
            reason="Compatibilidade local para testar participantes internos.",
        )
    )
    await db.flush()


async def _matter(db: AsyncSession, workspace_id, suffix: str = "local") -> Matter:
    client = Client(
        workspace_id=workspace_id,
        name=f"Cliente {suffix}",
        email=f"{suffix}-{uuid4().hex[:6]}@example.com",
    )
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type=f"Negativa {suffix}",
    )
    db.add(matter)
    await db.flush()
    return matter


async def _member(
    db: AsyncSession,
    workspace_id,
    suffix: str,
    *,
    membership_status: MembershipStatus = MembershipStatus.active,
    user_is_active: bool = True,
) -> User:
    user = User(
        name=f"Usuário {suffix}",
        email=f"usuario-{suffix}-{uuid4().hex[:6]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="UR",
        color="#111827",
        is_active=user_is_active,
        is_internal_staff=False,
    )
    db.add(user)
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=membership_status,
            is_default=True,
        )
    )
    await db.flush()
    return user


def _meeting_payload(matter_id, **overrides):
    payload = {
        "matterId": str(matter_id),
        "title": "Primeira reunião",
        "startsAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_first_case_meeting_must_be_online(client, db_session, auth_context):
    matter = await _matter(db_session, auth_context.workspace_id)

    rejected = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Escritório"),
    )
    bypass = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="meet no escritório"),
    )
    accepted = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="  GOOGLE-MEET  "),
    )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "MEE-003"
    assert bypass.status_code == 422
    assert bypass.json()["detail"]["code"] == "MEE-003"
    assert accepted.status_code == 201
    assert accepted.json()["location"] == "  GOOGLE-MEET  "


@pytest.mark.asyncio
async def test_valid_meeting_url_is_online_evidence(client, db_session, auth_context):
    matter = await _matter(db_session, auth_context.workspace_id, "url-valida")

    response = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Escritório",
            meetingUrl="https://meet.example.com/sala-123",
        ),
    )

    assert response.status_code == 201, response.text
    assert response.json()["meetingUrl"] == "https://meet.example.com/sala-123"


@pytest.mark.asyncio
async def test_later_case_meeting_may_be_physical(client, db_session, auth_context):
    matter = await _matter(db_session, auth_context.workspace_id, "posterior")
    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Online"),
    )
    second = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Reunião posterior",
            location="Escritório",
            startsAt=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        ),
    )

    assert first.status_code == second.status_code == 201


@pytest.mark.asyncio
async def test_create_ignores_cancelled_meeting_when_enforcing_first_online(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "create-cancelada")
    cancelled = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Online"),
    )
    assert cancelled.status_code == 201, cancelled.text
    cancellation = await client.patch(
        f"/api/v1/meetings/{cancelled.json()['id']}",
        json={"status": "cancelled", "expectedVersion": 1},
    )
    assert cancellation.status_code == 200, cancellation.text

    response = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Primeira reunião efetiva",
            location="Escritório",
            startsAt=(
                datetime.fromisoformat(cancelled.json()["startsAt"]) + timedelta(days=1)
            ).isoformat(),
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEE-003"


@pytest.mark.asyncio
async def test_update_ignores_cancelled_meeting_when_enforcing_first_online(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "update-cancelada")
    cancelled = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Online"),
    )
    assert cancelled.status_code == 201, cancelled.text
    cancellation = await client.patch(
        f"/api/v1/meetings/{cancelled.json()['id']}",
        json={"status": "cancelled", "expectedVersion": 1},
    )
    assert cancellation.status_code == 200, cancellation.text

    first_effective = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Primeira reunião efetiva",
            location="Google Meet",
            startsAt=(
                datetime.fromisoformat(cancelled.json()["startsAt"]) + timedelta(days=1)
            ).isoformat(),
        ),
    )
    assert first_effective.status_code == 201, first_effective.text

    response = await client.patch(
        f"/api/v1/meetings/{first_effective.json()['id']}",
        json={"location": "Escritório", "expectedVersion": 1},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEE-003"


@pytest.mark.asyncio
async def test_cancel_rejects_online_first_when_physical_meeting_would_become_first(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "cancel-primeira")
    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Online"),
    )
    second = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Reunião presencial posterior",
            location="Escritório",
            startsAt=(
                datetime.fromisoformat(first.json()["startsAt"]) + timedelta(days=1)
            ).isoformat(),
        ),
    )
    assert first.status_code == second.status_code == 201

    response = await client.patch(
        f"/api/v1/meetings/{first.json()['id']}",
        json={"status": "cancelled", "expectedVersion": 1},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEE-003"


@pytest.mark.asyncio
async def test_update_keeps_first_meeting_online_but_allows_later_physical_meeting(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "update-formato")
    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Online"),
    )
    second = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Reunião posterior",
            location="Escritório",
            startsAt=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        ),
    )
    assert first.status_code == second.status_code == 201

    rejected_first = await client.patch(
        f"/api/v1/meetings/{first.json()['id']}",
        json={"location": "meet no escritório", "expectedVersion": 1},
    )
    accepted_first = await client.patch(
        f"/api/v1/meetings/{first.json()['id']}",
        json={
            "location": "Escritório",
            "meetingUrl": "https://video.example.com/primeira-reuniao",
            "expectedVersion": 1,
        },
    )
    accepted_second = await client.patch(
        f"/api/v1/meetings/{second.json()['id']}",
        json={"location": "Sala 2", "expectedVersion": 1},
    )
    rejected_reorder = await client.patch(
        f"/api/v1/meetings/{first.json()['id']}",
        json={
            "startsAt": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
            "expectedVersion": 2,
        },
    )
    rejected_detach = await client.patch(
        f"/api/v1/meetings/{first.json()['id']}",
        json={"matterId": None, "expectedVersion": 2},
    )

    assert rejected_first.status_code == 422
    assert rejected_first.json()["detail"]["code"] == "MEE-003"
    assert accepted_first.status_code == 200, accepted_first.text
    assert accepted_second.status_code == 200, accepted_second.text
    assert accepted_second.json()["location"] == "Sala 2"
    assert rejected_reorder.status_code == 422
    assert rejected_reorder.json()["detail"]["code"] == "MEE-003"
    assert rejected_detach.status_code == 422
    assert rejected_detach.json()["detail"]["code"] == "MEE-003"


@pytest.mark.asyncio
async def test_delete_rejects_online_first_when_physical_meeting_would_become_first(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "delete-primeira")
    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Online"),
    )
    second = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Reunião presencial posterior",
            location="Escritório",
            startsAt=(
                datetime.fromisoformat(first.json()["startsAt"]) + timedelta(days=1)
            ).isoformat(),
        ),
    )
    assert first.status_code == second.status_code == 201

    response = await client.delete(
        f"/api/v1/meetings/{first.json()['id']}",
        params={"expectedVersion": first.json()["version"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEE-003"


@pytest.mark.asyncio
async def test_delete_ignores_cancelled_meeting_when_selecting_first_remaining(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "delete-cancelada")
    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Online"),
    )
    second = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Segunda reunião online",
            location="Google Meet",
            startsAt=(
                datetime.fromisoformat(first.json()["startsAt"]) + timedelta(days=1)
            ).isoformat(),
        ),
    )
    third = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Terceira reunião presencial",
            location="Escritório",
            startsAt=(
                datetime.fromisoformat(first.json()["startsAt"]) + timedelta(days=2)
            ).isoformat(),
        ),
    )
    assert first.status_code == second.status_code == third.status_code == 201

    cancellation = await client.patch(
        f"/api/v1/meetings/{first.json()['id']}",
        json={"status": "cancelled", "expectedVersion": 1},
    )
    assert cancellation.status_code == 200, cancellation.text

    response = await client.delete(
        f"/api/v1/meetings/{second.json()['id']}",
        params={"expectedVersion": second.json()["version"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MEE-003"


@pytest.mark.asyncio
async def test_delete_allows_valid_first_meeting_outcomes(client, db_session, auth_context):
    online_successor_matter = await _matter(
        db_session,
        auth_context.workspace_id,
        "delete-sucessora-online",
    )
    first_online = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(online_successor_matter.id, location="Online"),
    )
    second_online = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            online_successor_matter.id,
            title="Segunda reunião online",
            location="Google Meet",
            startsAt=(
                datetime.fromisoformat(first_online.json()["startsAt"]) + timedelta(days=1)
            ).isoformat(),
        ),
    )
    assert first_online.status_code == second_online.status_code == 201

    delete_with_online_successor = await client.delete(
        f"/api/v1/meetings/{first_online.json()['id']}",
        params={"expectedVersion": first_online.json()["version"]},
    )

    later_physical_matter = await _matter(
        db_session,
        auth_context.workspace_id,
        "delete-posterior",
    )
    first_kept = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(later_physical_matter.id, location="Online"),
    )
    later_physical = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            later_physical_matter.id,
            title="Reunião presencial posterior",
            location="Escritório",
            startsAt=(
                datetime.fromisoformat(first_kept.json()["startsAt"]) + timedelta(days=1)
            ).isoformat(),
        ),
    )
    assert first_kept.status_code == later_physical.status_code == 201

    delete_later_physical = await client.delete(
        f"/api/v1/meetings/{later_physical.json()['id']}",
        params={"expectedVersion": later_physical.json()["version"]},
    )
    delete_only_remaining = await client.delete(
        f"/api/v1/meetings/{first_kept.json()['id']}",
        params={"expectedVersion": first_kept.json()["version"]},
    )

    assert delete_with_online_successor.status_code == 204
    assert delete_later_physical.status_code == 204
    assert delete_only_remaining.status_code == 204


@pytest.mark.asyncio
async def test_meeting_interval_must_end_after_start_on_create_and_update(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "intervalo")
    starts_at = datetime.now(UTC) + timedelta(days=1)

    rejected_create = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            startsAt=starts_at.isoformat(),
            endsAt=(starts_at - timedelta(minutes=1)).isoformat(),
        ),
    )
    created = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            startsAt=starts_at.isoformat(),
            endsAt=(starts_at + timedelta(hours=1)).isoformat(),
        ),
    )
    assert created.status_code == 201, created.text
    rejected_update = await client.patch(
        f"/api/v1/meetings/{created.json()['id']}",
        json={"endsAt": starts_at.isoformat(), "expectedVersion": 1},
    )

    assert rejected_create.status_code == 422
    assert "posterior ao início" in rejected_create.text
    assert rejected_update.status_code == 422
    assert rejected_update.json()["detail"]["code"] == "MEE-005"


@pytest.mark.asyncio
async def test_responsible_user_must_be_active_member_of_meeting_workspace(
    client,
    db_session,
    auth_context,
):
    other_workspace = Workspace(
        name="Workspace do responsável externo",
        slug=f"responsavel-externo-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    outside_user = await _member(db_session, other_workspace.id, "externo")
    suspended_user = await _member(
        db_session,
        auth_context.workspace_id,
        "suspenso",
        membership_status=MembershipStatus.suspended,
    )
    inactive_user = await _member(
        db_session,
        auth_context.workspace_id,
        "inativo",
        user_is_active=False,
    )
    matter = await _matter(db_session, auth_context.workspace_id, "responsavel")

    rejected_cross_workspace = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            responsibleUserId=str(outside_user.id),
        ),
    )
    rejected_inactive = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            responsibleUserId=str(inactive_user.id),
        ),
    )
    created = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            responsibleUserId=str(auth_context.user_id),
        ),
    )
    assert created.status_code == 201, created.text
    rejected_cross_workspace_update = await client.patch(
        f"/api/v1/meetings/{created.json()['id']}",
        json={
            "responsibleUserId": str(outside_user.id),
            "expectedVersion": 1,
        },
    )
    rejected_inactive_update = await client.patch(
        f"/api/v1/meetings/{created.json()['id']}",
        json={
            "responsibleUserId": str(inactive_user.id),
            "expectedVersion": 1,
        },
    )
    rejected_suspended_update = await client.patch(
        f"/api/v1/meetings/{created.json()['id']}",
        json={
            "responsibleUserId": str(suspended_user.id),
            "expectedVersion": 1,
        },
    )

    rejected_responsibles = (
        rejected_cross_workspace,
        rejected_inactive,
        rejected_cross_workspace_update,
        rejected_inactive_update,
        rejected_suspended_update,
    )
    for response in rejected_responsibles:
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "MEE-006"


@pytest.mark.asyncio
async def test_meeting_rejects_cross_workspace_matter_and_unsafe_url(
    client,
    db_session,
    auth_context,
):
    other_workspace = Workspace(
        name="Outro escritório",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    other_matter = await _matter(db_session, other_workspace.id, "externo")
    local_matter = await _matter(db_session, auth_context.workspace_id, "url")

    cross_tenant = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(other_matter.id, location="Online"),
    )
    unsafe_url = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            local_matter.id,
            location="Online",
            meetingUrl="javascript:alert(1)",
        ),
    )

    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["detail"]["code"] == "MEE-002"
    assert unsafe_url.status_code == 422
    assert unsafe_url.json()["detail"]["code"] == "MEE-004"


@pytest.mark.asyncio
async def test_meeting_with_matter_records_meeting_booked_acquisition_event(
    client, db_session, auth_context
):
    """Auditoria 2026-07-26, item 7: reunião real agendada tem que virar o
    marco de funil `meeting_booked` (antes só existia no seed local — nenhum
    ponto real emitia)."""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.klm_operations import AcquisitionEvent

    matter = await _matter(db_session, auth_context.workspace_id, "reuniao-real")

    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(matter.id, location="Google Meet"),
    )
    assert first.status_code == 201, first.text
    meeting_id = first.json()["id"]

    event = await db_session.scalar(
        sa_select(AcquisitionEvent).where(
            AcquisitionEvent.workspace_id == auth_context.workspace_id,
            AcquisitionEvent.dedupe_key == f"conversion:reuniao_agendada:{matter.id}:{meeting_id}",
        )
    )
    assert event is not None
    assert event.event_name == "meeting_booked"
    assert event.channel == "agenda"
    assert event.matter_id == matter.id

    # Uma segunda reunião real no mesmo caso é um marco novo (dedupe por
    # reunião, não por caso) — replay da MESMA reunião é que não duplica.
    second = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Google Meet",
            startsAt=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        ),
    )
    assert second.status_code == 201, second.text
    second_meeting_id = second.json()["id"]
    assert second_meeting_id != meeting_id

    events_count = await db_session.scalar(
        sa_select(func.count()).select_from(AcquisitionEvent).where(
            AcquisitionEvent.workspace_id == auth_context.workspace_id,
            AcquisitionEvent.event_name == "meeting_booked",
            AcquisitionEvent.matter_id == matter.id,
        )
    )
    assert events_count == 2


@pytest.mark.asyncio
async def test_meeting_without_matter_does_not_record_acquisition_event(
    client, db_session, auth_context
):
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.klm_operations import AcquisitionEvent

    payload = {
        "title": "Reunião interna sem caso",
        "startsAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "location": "Escritório",
    }
    response = await client.post("/api/v1/meetings", headers=_meeting_headers(), json=payload)
    assert response.status_code == 201, response.text

    events_count = await db_session.scalar(
        sa_select(func.count()).select_from(AcquisitionEvent).where(
            AcquisitionEvent.workspace_id == auth_context.workspace_id,
            AcquisitionEvent.event_name == "meeting_booked",
        )
    )
    assert events_count == 0


# ── Conflito de agenda: mesma pessoa não dupla-agenda (comercial + jurídico) ──


@pytest.mark.asyncio
async def test_create_rejects_overlapping_meeting_for_same_responsible_across_natures(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "conflito-comercial")
    other_matter = await _matter(db_session, auth_context.workspace_id, "conflito-juridico")
    starts_at = datetime.now(UTC) + timedelta(days=1)

    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            startsAt=starts_at.isoformat(),
            endsAt=(starts_at + timedelta(hours=1)).isoformat(),
            responsibleUserId=str(auth_context.user_id),
            natureza="comercial",
        ),
    )
    assert first.status_code == 201, first.text

    # Audiência (jurídico) sobreposta ao mesmo horário, mesmo responsável —
    # a natureza diferente NÃO isenta do conflito: é a MESMA pessoa.
    conflicting = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            other_matter.id,
            title="Audiência conflitante",
            location="Online",
            startsAt=(starts_at + timedelta(minutes=30)).isoformat(),
            endsAt=(starts_at + timedelta(minutes=90)).isoformat(),
            responsibleUserId=str(auth_context.user_id),
            natureza="juridico",
            eventType="audiencia",
        ),
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "MEE-007"

    non_overlapping = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            other_matter.id,
            title="Audiência sem conflito",
            location="Online",
            startsAt=(starts_at + timedelta(hours=3)).isoformat(),
            endsAt=(starts_at + timedelta(hours=4)).isoformat(),
            responsibleUserId=str(auth_context.user_id),
            natureza="juridico",
            eventType="audiencia",
        ),
    )
    assert non_overlapping.status_code == 201, non_overlapping.text


@pytest.mark.asyncio
async def test_conflict_check_ignores_cancelled_meetings(client, db_session, auth_context):
    matter = await _matter(db_session, auth_context.workspace_id, "conflito-cancelada")
    starts_at = datetime.now(UTC) + timedelta(days=1)

    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            startsAt=starts_at.isoformat(),
            endsAt=(starts_at + timedelta(hours=1)).isoformat(),
            responsibleUserId=str(auth_context.user_id),
        ),
    )
    assert first.status_code == 201, first.text
    cancellation = await client.patch(
        f"/api/v1/meetings/{first.json()['id']}",
        json={"status": "cancelled", "expectedVersion": 1},
    )
    assert cancellation.status_code == 200, cancellation.text

    second = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            title="Reunião no lugar da cancelada",
            location="Online",
            startsAt=(starts_at + timedelta(minutes=15)).isoformat(),
            endsAt=(starts_at + timedelta(minutes=75)).isoformat(),
            responsibleUserId=str(auth_context.user_id),
        ),
    )
    assert second.status_code == 201, second.text


@pytest.mark.asyncio
async def test_update_rejects_move_into_conflict_with_another_meeting(
    client,
    db_session,
    auth_context,
):
    matter = await _matter(db_session, auth_context.workspace_id, "update-conflito")
    other_matter = await _matter(db_session, auth_context.workspace_id, "update-conflito-2")
    starts_at = datetime.now(UTC) + timedelta(days=1)

    first = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            startsAt=starts_at.isoformat(),
            endsAt=(starts_at + timedelta(hours=1)).isoformat(),
            responsibleUserId=str(auth_context.user_id),
        ),
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            other_matter.id,
            title="Sustentação oral",
            location="Online",
            startsAt=(starts_at + timedelta(hours=5)).isoformat(),
            endsAt=(starts_at + timedelta(hours=6)).isoformat(),
            responsibleUserId=str(auth_context.user_id),
            natureza="juridico",
            eventType="sustentacao",
        ),
    )
    assert second.status_code == 201, second.text

    moved_into_conflict = await client.patch(
        f"/api/v1/meetings/{second.json()['id']}",
        json={
            "startsAt": (starts_at + timedelta(minutes=30)).isoformat(),
            "endsAt": (starts_at + timedelta(minutes=90)).isoformat(),
            "expectedVersion": 1,
        },
    )
    assert moved_into_conflict.status_code == 409
    assert moved_into_conflict.json()["detail"]["code"] == "MEE-007"


@pytest.mark.asyncio
async def test_meeting_supports_internal_participants_and_optimistic_replacement(
    client,
    db_session,
    auth_context,
):
    await _enable_meeting_participants(db_session, auth_context.workspace_id, limit=3)
    attendee = await _member(db_session, auth_context.workspace_id, "participante")
    observer = await _member(db_session, auth_context.workspace_id, "observador")
    matter = await _matter(db_session, auth_context.workspace_id, "participantes")
    starts_at = datetime.now(UTC) + timedelta(days=4)

    created = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            matter.id,
            location="Online",
            startsAt=starts_at.isoformat(),
            endsAt=(starts_at + timedelta(hours=1)).isoformat(),
            participants=[
                {"userId": str(auth_context.user_id), "role": "organizer"},
                {"userId": str(attendee.id), "role": "attendee"},
                {"userId": str(observer.id), "role": "observer"},
            ],
        ),
    )

    assert created.status_code == 201, created.text
    assert created.json()["responsibleUserId"] == str(auth_context.user_id)
    assert len([item for item in created.json()["participants"] if item["active"]]) == 3
    assert created.json()["version"] == 1

    replaced = await client.put(
        f"/api/v1/meetings/{created.json()['id']}/participants",
        json={
            "expectedVersion": 1,
            "participants": [
                {"userId": str(auth_context.user_id), "role": "organizer"},
                {"userId": str(attendee.id), "role": "observer"},
            ],
        },
    )

    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["version"] == 2
    active = [item for item in replaced.json()["participants"] if item["active"]]
    assert {(item["userId"], item["role"]) for item in active} == {
        (str(auth_context.user_id), "organizer"),
        (str(attendee.id), "observer"),
    }
    participant_audit = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.entity_type == "meeting",
                KlmAuditEvent.entity_id == created.json()["id"],
                KlmAuditEvent.event_type == "meeting_participants_changed",
            )
        )
    ).one()
    assert len(participant_audit.before_data["participants"]) == 3
    assert len(participant_audit.after_data["participants"]) == 2
    assert participant_audit.before_data["version"] == 1
    assert participant_audit.after_data["version"] == 2

    no_op = await client.put(
        f"/api/v1/meetings/{created.json()['id']}/participants",
        json={
            "expectedVersion": 2,
            "participants": [
                {"userId": str(attendee.id), "role": "observer"},
                {"userId": str(auth_context.user_id), "role": "organizer"},
            ],
        },
    )
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["version"] == 2
    assert await db_session.scalar(
        select(func.count(MeetingNotificationOutbox.id)).where(
            MeetingNotificationOutbox.meeting_id == created.json()["id"]
        )
    ) == 2
    assert len(
        (
            await db_session.scalars(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == auth_context.workspace_id,
                    KlmAuditEvent.entity_type == "meeting",
                    KlmAuditEvent.entity_id == created.json()["id"],
                    KlmAuditEvent.event_type == "meeting_participants_changed",
                )
            )
        ).all()
    ) == 1

    stale = await client.put(
        f"/api/v1/meetings/{created.json()['id']}/participants",
        json={
            "expectedVersion": 1,
            "participants": [
                {"userId": str(auth_context.user_id), "role": "organizer"},
            ],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "MEE-009"


@pytest.mark.asyncio
async def test_attendee_conflict_requires_explicit_reason_but_observer_does_not_block(
    client,
    db_session,
    auth_context,
):
    await _enable_meeting_participants(db_session, auth_context.workspace_id, limit=3)
    attendee = await _member(db_session, auth_context.workspace_id, "conflito-participante")
    organizer = await _member(db_session, auth_context.workspace_id, "novo-organizador")
    first_matter = await _matter(db_session, auth_context.workspace_id, "agenda-participante-1")
    second_matter = await _matter(db_session, auth_context.workspace_id, "agenda-participante-2")
    starts_at = datetime.now(UTC) + timedelta(days=5)

    existing = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=_meeting_payload(
            first_matter.id,
            location="Online",
            startsAt=starts_at.isoformat(),
            endsAt=(starts_at + timedelta(hours=1)).isoformat(),
            responsibleUserId=str(attendee.id),
        ),
    )
    assert existing.status_code == 201, existing.text

    payload = _meeting_payload(
        second_matter.id,
        title="Compromisso com conflito confirmado",
        location="Online",
        startsAt=(starts_at + timedelta(minutes=15)).isoformat(),
        endsAt=(starts_at + timedelta(minutes=45)).isoformat(),
        participants=[
            {"userId": str(organizer.id), "role": "organizer"},
            {"userId": str(attendee.id), "role": "attendee"},
        ],
    )
    warned = await client.post("/api/v1/meetings", headers=_meeting_headers(), json=payload)
    assert warned.status_code == 409
    assert warned.json()["detail"]["code"] == "MEE-008"

    payload["confirmParticipantConflicts"] = True
    payload["conflictReason"] = "curto"
    invalid_reason = await client.post(
        "/api/v1/meetings",
        headers=_meeting_headers(),
        json=payload,
    )
    assert invalid_reason.status_code == 422
    assert invalid_reason.json()["detail"]["code"] == "MEE-019"

    payload["conflictReason"] = "Cliente confirmou a sobreposição de agenda."
    confirmed = await client.post("/api/v1/meetings", headers=_meeting_headers(), json=payload)
    assert confirmed.status_code == 201, confirmed.text
    audit_event = await db_session.scalar(
        select(KlmAuditEvent)
        .where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.entity_type == "meeting",
            KlmAuditEvent.entity_id == UUID(confirmed.json()["id"]),
            KlmAuditEvent.event_type == "meeting_created",
        )
        .order_by(KlmAuditEvent.occurred_at.desc())
    )
    assert audit_event is not None
    assert audit_event.after_data["participantConflictConfirmation"] == {
        "confirmed": True,
        "reason": payload["conflictReason"],
    }
