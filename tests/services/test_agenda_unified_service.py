"""Agenda unificada (GET /agenda) — Sprint B, comercial + jurídico numa
única lista, com filtro por natureza e conflito de agenda entre naturezas.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.billing import BillingInterval, Plan, PlanTier, Subscription, SubscriptionStatus
from app.models.client import Client
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.matter import Matter, PracticeArea
from app.models.meeting import Meeting, MeetingEventType, MeetingNature, MeetingStatus
from app.models.process_monitoring import (
    DeadlineCountingType,
    DeadlineStatus,
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublication,
    ProcessPublicationSource,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.agenda_service import AgendaService

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


async def _enable_process_monitoring(db_session, workspace_id) -> None:
    plan = Plan(
        name=f"Plano agenda {uuid4().hex[:8]}",
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
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    db_session.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.legal_suite,
                enabled=True,
                limit_quantity=None,
                hard_limit=True,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.monitored_processes,
                enabled=True,
                limit_quantity=10,
                hard_limit=True,
            ),
        ]
    )
    await db_session.flush()


async def _make_client(db_session, workspace_id) -> Client:
    client = Client(workspace_id=workspace_id, name="Cliente Agenda Unificada")
    db_session.add(client)
    await db_session.flush()
    return client


async def _make_matter(db_session, workspace_id, client_id) -> Matter:
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client_id,
        practice_area=PracticeArea.civel,
        demand_type="Caso agenda unificada",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


async def _make_user(db_session) -> User:
    user = User(
        name="Responsável Agenda",
        email=f"agenda-{uuid4().hex[:10]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="RA",
        color="#223344",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_unified_agenda_merges_comercial_and_juridico_with_filter(
    db_session, auth_context
):
    await _enable_process_monitoring(db_session, auth_context.workspace_id)
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    user = await _make_user(db_session)

    comercial_meeting = Meeting(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        responsible_user_id=user.id,
        title="Reunião inicial com o cliente",
        starts_at=NOW + timedelta(days=1),
        status=MeetingStatus.scheduled,
        natureza=MeetingNature.comercial,
        event_type=MeetingEventType.reuniao,
    )
    juridico_meeting = Meeting(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        responsible_user_id=user.id,
        title="Audiência de instrução",
        starts_at=NOW + timedelta(days=2),
        status=MeetingStatus.scheduled,
        natureza=MeetingNature.juridico,
        event_type=MeetingEventType.audiencia,
    )
    monitored = MonitoredProcess(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        numero_cnj=f"000{uuid4().hex[:1]}234-37.2026.8.07.0001",
        status=MonitoredProcessStatus.ativo,
        responsible_user_id=user.id,
        created_by_user_id=user.id,
    )
    db_session.add_all([comercial_meeting, juridico_meeting, monitored])
    await db_session.flush()

    publication = ProcessPublication(
        workspace_id=auth_context.workspace_id,
        monitored_process_id=monitored.id,
        fonte=ProcessPublicationSource.djen,
        external_id=f"ext-{uuid4().hex[:10]}",
        numero_cnj=monitored.numero_cnj,
        conteudo="Intimação confirmada",
        published_at=NOW,
        dias=15,
        tipo_contagem=DeadlineCountingType.dias_uteis,
        data_fatal=(NOW + timedelta(days=3)).date(),
        prazo_status=DeadlineStatus.confirmado,
        memoria_calculo=[{"evento": "contagem protegida"}],
        confirmed_by_user_id=user.id,
        confirmed_at=NOW,
    )
    db_session.add(publication)
    # Publicação de outro workspace nunca deve vazar para a agenda deste.
    other_workspace = Workspace(
        name="Outro escritório — agenda",
        slug=f"outro-agenda-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    unrelated_publication = ProcessPublication(
        workspace_id=other_workspace.id,
        fonte=ProcessPublicationSource.djen,
        external_id=f"ext-{uuid4().hex[:10]}",
        numero_cnj="0009999-00.2026.8.07.0001",
        conteudo="Intimação de outro workspace",
        published_at=NOW,
        dias=10,
        tipo_contagem=DeadlineCountingType.dias_uteis,
        data_fatal=(NOW + timedelta(days=3)).date(),
        prazo_status=DeadlineStatus.confirmado,
        confirmed_by_user_id=user.id,
        confirmed_at=NOW,
    )
    db_session.add(unrelated_publication)
    await db_session.commit()

    service = AgendaService(db_session)

    everything = await service.unified_agenda(
        auth_context.workspace_id,
        include_tasks=True,
        include_process_deadlines=True,
        can_view_sensitive=True,
        start=NOW,
        end=NOW + timedelta(days=10),
    )
    assert [event.starts_at for event in everything] == sorted(
        event.starts_at for event in everything
    )
    sources = {(event.source, event.event_type) for event in everything}
    assert sources == {
        ("meeting", MeetingEventType.reuniao),
        ("meeting", MeetingEventType.audiencia),
        ("process_publication", MeetingEventType.prazo_fatal),
    }
    prazo_event = next(event for event in everything if event.source == "process_publication")
    assert prazo_event.natureza == MeetingNature.juridico
    assert prazo_event.is_inviolable is True
    assert prazo_event.matter_id == matter.id
    assert prazo_event.dias == 15
    assert prazo_event.status == DeadlineStatus.confirmado.value
    assert prazo_event.memoria_calculo == [{"evento": "contagem protegida"}]

    masked = await service.unified_agenda(
        auth_context.workspace_id,
        include_tasks=True,
        include_process_deadlines=True,
        can_view_sensitive=False,
        start=NOW,
        end=NOW + timedelta(days=10),
    )
    masked_prazo = next(event for event in masked if event.source == "process_publication")
    assert masked_prazo.title == "Prazo fatal · Dados processuais protegidos"
    assert masked_prazo.memoria_calculo is None
    assert monitored.numero_cnj not in masked_prazo.title

    only_comercial = await service.unified_agenda(
        auth_context.workspace_id,
        include_tasks=True,
        include_process_deadlines=True,
        can_view_sensitive=True,
        natureza=MeetingNature.comercial,
        start=NOW,
        end=NOW + timedelta(days=10),
    )
    assert len(only_comercial) == 1
    assert only_comercial[0].source == "meeting"
    assert only_comercial[0].natureza == MeetingNature.comercial

    only_juridico = await service.unified_agenda(
        auth_context.workspace_id,
        include_tasks=True,
        include_process_deadlines=True,
        can_view_sensitive=True,
        natureza=MeetingNature.juridico,
        start=NOW,
        end=NOW + timedelta(days=10),
    )
    assert {event.source for event in only_juridico} == {"meeting", "process_publication"}
    assert all(event.natureza == MeetingNature.juridico for event in only_juridico)


@pytest.mark.asyncio
async def test_unified_agenda_ignores_cancelled_meetings_and_unconfirmed_deadlines(
    db_session, auth_context
):
    client = await _make_client(db_session, auth_context.workspace_id)
    matter = await _make_matter(db_session, auth_context.workspace_id, client.id)
    user = await _make_user(db_session)

    cancelled = Meeting(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        title="Reunião cancelada",
        starts_at=NOW + timedelta(days=1),
        status=MeetingStatus.cancelled,
        natureza=MeetingNature.comercial,
        event_type=MeetingEventType.reuniao,
    )
    db_session.add(cancelled)
    # Prazo apenas SUGERIDO (não confirmado) nunca aparece na agenda —
    # a agenda só mostra o prazo fatal já confirmado por um humano.
    db_session.add(
        ProcessPublication(
            workspace_id=auth_context.workspace_id,
            fonte=ProcessPublicationSource.djen,
            external_id=f"ext-{uuid4().hex[:10]}",
            numero_cnj="0001234-56.2026.8.07.0001",
            conteudo="Intimação sugerida",
            published_at=NOW,
            dias=15,
            tipo_contagem=DeadlineCountingType.dias_uteis,
            data_fatal=(NOW + timedelta(days=3)).date(),
            prazo_status=DeadlineStatus.prazo_sugerido,
        )
    )
    await db_session.commit()

    service = AgendaService(db_session)
    events = await service.unified_agenda(
        auth_context.workspace_id,
        include_tasks=True,
        include_process_deadlines=False,
        can_view_sensitive=True,
        start=NOW,
        end=NOW + timedelta(days=10),
    )
    assert events == []
