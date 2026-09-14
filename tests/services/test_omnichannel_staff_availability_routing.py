from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.klm_operations import (
    KlmAuditEvent,
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import (
    OmnichannelChannelConnection,
    OmnichannelRoutingEvent,
    OmnichannelScheduledMessage,
    OmnichannelScheduledMessageEvent,
    WorkspaceTeam,
)
from app.models.staff_availability import AvailabilityRequestStatus
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.omnichannel_v6 import CanonicalChannelEventIngest, ThreadTransferRequest
from app.schemas.staff_availability import StaffAvailabilityChangeRequestCreate
from app.services.omnichannel_v6_service import (
    OmnichannelConflictError,
    OmnichannelV6Service,
)
from app.services.staff_availability_service import StaffAvailabilityService


async def _member(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
    role: WorkspaceRole = WorkspaceRole.operator,
) -> tuple[User, AuthContext]:
    token = uuid4().hex[:10]
    user = User(
        name=name,
        email=f"{token}@routing.example",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="".join(part[0] for part in name.split())[:4].upper() or "RT",
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
    )
    db.add(membership)
    await db.flush()
    return user, AuthContext(
        user_id=user.id,
        workspace_id=workspace_id,
        membership_id=membership.id,
        role=role,
        is_internal_staff=False,
    )


async def _approve_away_redirect(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    target_user: User,
    requester: AuthContext,
    approver: AuthContext,
    redirect_kind: str,
    redirect_id: UUID,
    absence_message: str = "Estou ausente; a equipe dará continuidade.",
):
    service = StaffAvailabilityService(db)
    request = await service.submit_change(
        workspace_id=workspace_id,
        actor=requester,
        idempotency_key=f"availability-route:{uuid4()}",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=target_user.id,
            expected_version=0,
            reason="Ativar substituição aprovada durante ausência",
            proposed_change={
                "changeType": "replace_configuration",
                "mode": "away",
                "timezone": "America/Sao_Paulo",
                "weeklyAvailability": [
                    {
                        "weekday": weekday,
                        "enabled": weekday == 0,
                        "windows": ([{"start": "09:00", "end": "18:00"}] if weekday == 0 else []),
                    }
                    for weekday in range(7)
                ],
                "exceptions": [],
                "absenceMessage": {"enabled": True, "text": absence_message},
                "activation": {"mode": "now", "startsAt": None, "endsAt": None},
                "redirect": {"kind": redirect_kind, "targetId": str(redirect_id)},
                "automaticSwap": True,
            },
        ),
    )
    await service.approve_request(
        workspace_id=workspace_id,
        request_id=request.id,
        actor=approver,
        note="Substituição conferida",
    )
    return request


async def _plain_thread(db: AsyncSession, workspace_id: UUID) -> OmnichannelThread:
    thread = OmnichannelThread(
        workspace_id=workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        subject=f"Roteamento {uuid4().hex[:8]}",
        queue="new",
        thread_metadata={},
    )
    db.add(thread)
    await db.flush()
    return thread


async def _thread_with_recent_whatsapp_inbound(
    db: AsyncSession,
    *,
    workspace_id: UUID,
) -> OmnichannelThread:
    token = uuid4().hex[:10]
    connection = OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel=OmnichannelChannel.whatsapp.value,
        provider="meta_cloud",
        connection_key=f"meta-cloud:{token}",
        display_name="WhatsApp oficial de teste",
        transport_mode="official_api",
        status="active",
        is_default=True,
        capabilities={"outboundIdempotency": False},
        routing_policy={"serviceWindowHours": 24},
        configuration={},
    )
    db.add(connection)
    await db.flush()
    service = OmnichannelV6Service(db)
    resolution, _event, message, deduplicated = await service.ingest_event(
        workspace_id,
        CanonicalChannelEventIngest(
            provider="meta_cloud",
            connection_key=connection.connection_key,
            channel=OmnichannelChannel.whatsapp,
            event_type="message.received",
            external_event_id=f"wamid.{token}",
            external_thread_id=f"thread.{token}",
            external_user_id=f"5511988{token[:6]}",
            external_account_id=f"account.{token}",
            display_name="Cliente da conversa",
            address="+5511988887777",
            canonical_phone="+5511988887777",
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Cliente da conversa",
            body="Preciso de atendimento humano.",
            actionability="reply_required",
            occurred_at=datetime.now(UTC),
            metadata={"providerStatus": "received"},
        ),
    )
    assert deduplicated is False
    assert message is not None
    assert message.connection_id == connection.id
    return resolution.thread


@pytest.mark.asyncio
async def test_rule_transfer_applies_approved_user_redirect_with_audit_and_idempotency(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    source, source_actor = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Titular ausente",
    )
    backup, _ = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Substituta disponível",
    )
    await _approve_away_redirect(
        db_session,
        workspace_id=auth_context.workspace_id,
        target_user=source,
        requester=source_actor,
        approver=auth_context,
        redirect_kind="user",
        redirect_id=backup.id,
    )
    thread = await _plain_thread(db_session, auth_context.workspace_id)
    service = OmnichannelV6Service(db_session)
    payload = ThreadTransferRequest(
        expected_version=thread.version,
        idempotency_key=f"rule-transfer:{uuid4()}",
        to_user_id=source.id,
        source="rule",
        reason="Distribuição automática da fila.",
    )

    transferred, event, deduplicated = await service.transfer_thread(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        payload,
    )
    replayed, replay_event, replay_deduplicated = await service.transfer_thread(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        payload,
    )

    assert deduplicated is False
    assert transferred.assigned_human_id == backup.id
    assert transferred.status is ThreadStatus.human_active
    assert event.action == "automatic_swap"
    assert event.to_user_id == backup.id
    assert replay_deduplicated is True
    assert replayed.id == thread.id
    assert replay_event.id == event.id
    assert (
        await db_session.scalar(
            select(func.count(OmnichannelRoutingEvent.id)).where(
                OmnichannelRoutingEvent.workspace_id == auth_context.workspace_id,
                OmnichannelRoutingEvent.idempotency_key == payload.idempotency_key,
            )
        )
        == 1
    )

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "omnichannel_thread_transferred",
            KlmAuditEvent.correlation_id == payload.idempotency_key,
        )
    )
    routing = audit.after_data["availabilityRouting"]
    assert routing["applied"] is True
    assert routing["manualOverride"] is False
    assert routing["requestedUserId"] == str(source.id)
    assert routing["effectiveUserId"] == str(backup.id)
    assert routing["path"] == [str(source.id), str(backup.id)]
    assert routing["approvalState"]["approvedRequestId"]

    with pytest.raises(OmnichannelConflictError, match="idempotência"):
        await service.transfer_thread(
            auth_context.workspace_id,
            thread.id,
            auth_context.user_id,
            payload.model_copy(update={"to_user_id": backup.id}),
        )


@pytest.mark.asyncio
async def test_rule_transfer_routes_to_approved_team_and_waiting_human_queue(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    source, source_actor = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Titular encaminhada ao plantão",
    )
    team = WorkspaceTeam(
        workspace_id=auth_context.workspace_id,
        name="Plantão jurídico",
        slug=f"plantao-{uuid4().hex[:8]}",
        team_type="legal",
        color="#123456",
        routing_strategy="least_loaded",
        schedule={},
        is_active=True,
    )
    db_session.add(team)
    await db_session.flush()
    await _approve_away_redirect(
        db_session,
        workspace_id=auth_context.workspace_id,
        target_user=source,
        requester=source_actor,
        approver=auth_context,
        redirect_kind="team",
        redirect_id=team.id,
    )
    thread = await _plain_thread(db_session, auth_context.workspace_id)

    transferred, event, _ = await OmnichannelV6Service(db_session).transfer_thread(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        ThreadTransferRequest(
            expected_version=thread.version,
            idempotency_key=f"team-route:{uuid4()}",
            to_user_id=source.id,
            source="system",
        ),
    )

    assert transferred.assigned_human_id is None
    assert transferred.assigned_team_id == team.id
    assert transferred.status is ThreadStatus.waiting_human
    assert event.action == "automatic_swap"
    assert event.to_team_id == team.id


@pytest.mark.asyncio
async def test_automatic_transfer_fails_closed_on_redirect_cycle_without_mutation(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    first, first_actor = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Primeira responsável",
    )
    second, second_actor = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Segunda responsável",
    )
    await _approve_away_redirect(
        db_session,
        workspace_id=auth_context.workspace_id,
        target_user=first,
        requester=first_actor,
        approver=auth_context,
        redirect_kind="user",
        redirect_id=second.id,
    )
    await _approve_away_redirect(
        db_session,
        workspace_id=auth_context.workspace_id,
        target_user=second,
        requester=second_actor,
        approver=auth_context,
        redirect_kind="user",
        redirect_id=first.id,
    )
    thread = await _plain_thread(db_session, auth_context.workspace_id)
    original_version = thread.version

    with pytest.raises(OmnichannelConflictError, match="redirect_cycle"):
        await OmnichannelV6Service(db_session).transfer_thread(
            auth_context.workspace_id,
            thread.id,
            auth_context.user_id,
            ThreadTransferRequest(
                expected_version=original_version,
                idempotency_key=f"cycle-route:{uuid4()}",
                to_user_id=first.id,
                source="agent",
            ),
        )

    assert thread.version == original_version
    assert thread.assigned_human_id is None
    assert await db_session.scalar(select(func.count(OmnichannelRoutingEvent.id))) == 0


@pytest.mark.asyncio
async def test_human_transfer_is_audited_manual_override_and_never_auto_replies(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    source, source_actor = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Titular escolhida manualmente",
    )
    backup, _ = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Substituta não aplicada",
    )
    await _approve_away_redirect(
        db_session,
        workspace_id=auth_context.workspace_id,
        target_user=source,
        requester=source_actor,
        approver=auth_context,
        redirect_kind="user",
        redirect_id=backup.id,
    )
    thread = await _thread_with_recent_whatsapp_inbound(
        db_session,
        workspace_id=auth_context.workspace_id,
    )
    payload = ThreadTransferRequest(
        expected_version=thread.version,
        idempotency_key=f"manual-override:{uuid4()}",
        to_user_id=source.id,
        source="human",
        reason="Gestor manteve a titular conscientemente.",
    )

    transferred, event, _ = await OmnichannelV6Service(db_session).transfer_thread(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        payload,
    )

    assert transferred.assigned_human_id == source.id
    assert event.action == "transfer"
    assert await db_session.scalar(select(func.count(OmnichannelScheduledMessage.id))) == 0
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.event_type == "omnichannel_thread_transferred",
            KlmAuditEvent.correlation_id == payload.idempotency_key,
        )
    )
    routing = audit.after_data["availabilityRouting"]
    assert routing["manualOverride"] is True
    assert routing["applied"] is False
    assert routing["reason"] == "manual_override"


@pytest.mark.asyncio
async def test_absence_response_is_human_approved_scheduled_and_deduped_by_state(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    source, source_actor = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Titular com resposta de ausência",
    )
    backup, _ = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Substituta da resposta",
    )
    message = "Estou ausente hoje; a equipe continuará o atendimento."
    approved_request = await _approve_away_redirect(
        db_session,
        workspace_id=auth_context.workspace_id,
        target_user=source,
        requester=source_actor,
        approver=auth_context,
        redirect_kind="user",
        redirect_id=backup.id,
        absence_message=message,
    )
    thread = await _thread_with_recent_whatsapp_inbound(
        db_session,
        workspace_id=auth_context.workspace_id,
    )
    service = OmnichannelV6Service(db_session)

    first_payload = ThreadTransferRequest(
        expected_version=thread.version,
        idempotency_key=f"absence-first:{uuid4()}",
        to_user_id=source.id,
        source="rule",
    )
    await service.transfer_thread(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        first_payload,
    )
    scheduled = await db_session.scalar(
        select(OmnichannelScheduledMessage).where(
            OmnichannelScheduledMessage.workspace_id == auth_context.workspace_id,
            OmnichannelScheduledMessage.thread_id == thread.id,
        )
    )
    assert scheduled is not None
    assert scheduled.author_type == "human"
    assert scheduled.created_by_user_id == source.id
    assert scheduled.body == message
    assert scheduled.state == "scheduled"
    assert scheduled.idempotency_key.startswith("staff-absence:")
    approval_state = scheduled.provider_receipt["staffAvailability"]["approvalState"]
    assert approval_state["approvedRequestId"] == str(approved_request.id)

    await service.transfer_thread(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        ThreadTransferRequest(
            expected_version=thread.version,
            idempotency_key=f"absence-manual-return:{uuid4()}",
            to_user_id=source.id,
            source="human",
        ),
    )
    await service.transfer_thread(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        ThreadTransferRequest(
            expected_version=thread.version,
            idempotency_key=f"absence-second:{uuid4()}",
            to_user_id=source.id,
            source="system",
        ),
    )

    assert (
        await db_session.scalar(
            select(func.count(OmnichannelScheduledMessage.id)).where(
                OmnichannelScheduledMessage.workspace_id == auth_context.workspace_id,
                OmnichannelScheduledMessage.thread_id == thread.id,
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(OmnichannelScheduledMessageEvent.id)).where(
                OmnichannelScheduledMessageEvent.workspace_id == auth_context.workspace_id,
                OmnichannelScheduledMessageEvent.scheduled_message_id == scheduled.id,
                OmnichannelScheduledMessageEvent.event_type == "created",
            )
        )
        == 1
    )
    absence_audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.event_type == "omnichannel_staff_absence_response_scheduled",
            KlmAuditEvent.entity_id == scheduled.id,
        )
    )
    assert absence_audit.actor_type == "system"
    assert absence_audit.after_data["authoredByUserId"] == str(source.id)
    assert approved_request.status is AvailabilityRequestStatus.approved


@pytest.mark.asyncio
async def test_pending_availability_and_foreign_membership_never_affect_runtime_assignment(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> None:
    source, source_actor = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Titular com proposta pendente",
    )
    backup, _ = await _member(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Destino ainda não aprovado",
    )
    pending = await StaffAvailabilityService(db_session).submit_change(
        workspace_id=auth_context.workspace_id,
        actor=source_actor,
        idempotency_key=f"pending-route:{uuid4()}",
        payload=StaffAvailabilityChangeRequestCreate(
            target_user_id=source.id,
            expected_version=0,
            reason="Proposta ainda não aprovada",
            proposed_change={
                "changeType": "replace_configuration",
                "mode": "away",
                "timezone": "America/Sao_Paulo",
                "weeklyAvailability": [
                    {
                        "weekday": weekday,
                        "enabled": weekday == 0,
                        "windows": ([{"start": "09:00", "end": "18:00"}] if weekday == 0 else []),
                    }
                    for weekday in range(7)
                ],
                "exceptions": [],
                "absenceMessage": {"enabled": True, "text": "Mensagem pendente."},
                "activation": {"mode": "now"},
                "redirect": {"kind": "user", "targetId": str(backup.id)},
                "automaticSwap": True,
            },
        ),
    )
    thread = await _plain_thread(db_session, auth_context.workspace_id)
    service = OmnichannelV6Service(db_session)
    transferred, event, _ = await service.transfer_thread(
        auth_context.workspace_id,
        thread.id,
        auth_context.user_id,
        ThreadTransferRequest(
            expected_version=thread.version,
            idempotency_key=f"pending-runtime:{uuid4()}",
            to_user_id=source.id,
            source="rule",
        ),
    )
    assert pending.status is AvailabilityRequestStatus.pending
    assert transferred.assigned_human_id == source.id
    assert event.action == "transfer"
    assert await db_session.scalar(select(func.count(OmnichannelScheduledMessage.id))) == 0

    foreign_workspace = Workspace(
        name="Workspace estrangeiro",
        slug=f"foreign-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_user, _ = await _member(
        db_session,
        workspace_id=foreign_workspace.id,
        name="Usuário de outro tenant",
    )
    second_thread = await _plain_thread(db_session, auth_context.workspace_id)
    with pytest.raises(LookupError, match="workspace"):
        await service.transfer_thread(
            auth_context.workspace_id,
            second_thread.id,
            auth_context.user_id,
            ThreadTransferRequest(
                expected_version=second_thread.version,
                idempotency_key=f"foreign-runtime:{uuid4()}",
                to_user_id=foreign_user.id,
                source="system",
            ),
        )
    assert second_thread.assigned_human_id is None
