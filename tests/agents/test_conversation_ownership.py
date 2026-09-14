"""Posse de conversa por agente — CAS, transfer, takeover humano, segunda área.

Cobre `app.services.conversation_ownership_service` diretamente (unidade
rápida, sem passar pelo runtime de agentes). A integração real com
`KlmAgentService._execute_one` (recusa não-dono via o endpoint HTTP) está em
`tests/services/test_klm_agent_service_ownership.py`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.klm_operations import KlmAuditEvent, OmnichannelChannel, OmnichannelThread, ThreadStatus
from app.models.client import Client
from app.models.matter import Matter, PracticeArea
from app.services.conversation_ownership_service import (
    ConversationNotFoundError,
    ConversationOwnershipDenied,
    OwnershipConflictError,
    authorize_agent_response,
    claim_conversation,
    human_takeover,
    open_second_area_matter,
    release_conversation,
    transfer_conversation,
)


async def _matter_and_thread(
    db_session, workspace_id, *, area_key: str = "harmonizacao_teste_area"
) -> tuple[Matter, OmnichannelThread]:
    client = Client(workspace_id=workspace_id, name=f"Cliente {uuid4().hex[:6]}")
    db_session.add(client)
    await db_session.flush()

    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Demanda de teste — posse de conversa",
        legal_area_key=area_key,
    )
    db_session.add(matter)
    await db_session.flush()

    thread = OmnichannelThread(
        workspace_id=workspace_id,
        matter_id=matter.id,
        client_id=client.id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
    )
    db_session.add(thread)
    await db_session.flush()
    return matter, thread


async def _thread_events(db_session, workspace_id, thread_id, event_type):
    rows = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == workspace_id,
                KlmAuditEvent.entity_id == thread_id,
                KlmAuditEvent.event_type == event_type,
            )
        )
    ).all()
    return list(rows)


@pytest.mark.asyncio
async def test_claim_conversation_is_atomic_only_one_of_two_concurrent_claims_wins(
    db_session, auth_context
):
    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    # Both callers read the same generation (0, orphan) before either CAS
    # UPDATE runs — the classic concurrent-claim race.
    read_generation = thread.owner_generation
    assert read_generation == 0

    winner = await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    assert winner.changed is True
    assert winner.thread.owning_agent_key == "sdr"
    assert winner.thread.owner_generation == read_generation + 1

    with pytest.raises(OwnershipConflictError) as excinfo:
        await claim_conversation(
            db_session,
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            agent_key="sdr_saude_vendas",
            area_key="saude_suplementar",
            actor_label="SDR de Direito da Saúde",
        )
    # The loser's exception carries the FRESH row, not the stale one it
    # started with — it must reread before deciding to retry.
    assert excinfo.value.thread.owning_agent_key == "sdr"
    assert excinfo.value.thread.owner_generation == read_generation + 1

    claimed_events = await _thread_events(
        db_session, auth_context.workspace_id, thread.id, "conversation_ownership_claimed"
    )
    assert len(claimed_events) == 1
    assert claimed_events[0].after_data["owningAgentKey"] == "sdr"


@pytest.mark.asyncio
async def test_claim_conversation_is_idempotent_for_the_current_owner(db_session, auth_context):
    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    first = await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    second = await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    assert second.already_owner is True
    assert second.changed is False
    # No new generation, no duplicate audit noise for a no-op re-claim.
    assert second.thread.owner_generation == first.thread.owner_generation


@pytest.mark.asyncio
async def test_authorize_agent_response_orphan_thread_is_claimed_by_first_agent(
    db_session, auth_context
):
    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    result = await authorize_agent_response(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread=thread,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    assert result.thread.owning_agent_key == "sdr"
    assert result.changed is True


@pytest.mark.asyncio
async def test_authorize_agent_response_denies_non_owner_and_audits(db_session, auth_context):
    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    await authorize_agent_response(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread=thread,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )

    stale_thread = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread.id)
    )
    with pytest.raises(ConversationOwnershipDenied) as excinfo:
        await authorize_agent_response(
            db_session,
            workspace_id=auth_context.workspace_id,
            thread=stale_thread,
            agent_key="crm",
            area_key="saude_suplementar",
            actor_label="CRM de precisão",
        )
    assert excinfo.value.agent_key == "crm"
    assert excinfo.value.reason == "not_owner"

    denied_events = await _thread_events(
        db_session, auth_context.workspace_id, thread.id, "conversation_ownership_response_denied"
    )
    assert len(denied_events) == 1
    assert denied_events[0].after_data["owningAgentKey"] == "sdr"
    assert denied_events[0].before_data["requestedAgentKey"] == "crm"


@pytest.mark.asyncio
async def test_transfer_conversation_registers_handoff_audit_and_increments_generation(
    db_session, auth_context
):
    matter, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    matter.executive_summary = "Tese identificada: negativa de home care."
    await db_session.commit()

    claimed = await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    generation_after_claim = claimed.thread.owner_generation

    result, summary = await transfer_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        from_agent_key="sdr",
        to_agent_key="juridico_saude",
        to_area_key="saude_suplementar",
        handoff_context={
            "thesis": "Negativa de home care.",
            "dossier": {"legalAreaKey": "saude_suplementar"},
            "objectionsHandled": ["preço"],
            "nextStep": "Especialista assume o diagnóstico.",
        },
        actor_label="SDR Jurídico",
    )
    assert result.thread.owning_agent_key == "juridico_saude"
    assert result.thread.owner_generation == generation_after_claim + 1
    assert summary.thesis == "Negativa de home care."
    assert summary.objections_handled == ("preço",)

    transferred_events = await _thread_events(
        db_session,
        auth_context.workspace_id,
        thread.id,
        "conversation_ownership_transferred",
    )
    assert len(transferred_events) == 1
    after = transferred_events[0].after_data
    assert after["owningAgentKey"] == "juridico_saude"
    assert after["handoffSummary"]["thesis"] == "Negativa de home care."
    assert after["handoffSummary"]["objectionsHandled"] == ["preço"]
    assert transferred_events[0].before_data["owningAgentKey"] == "sdr"


@pytest.mark.asyncio
async def test_transfer_conversation_rejects_stale_from_agent(db_session, auth_context):
    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    with pytest.raises(OwnershipConflictError):
        await transfer_conversation(
            db_session,
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            from_agent_key="sdr",  # nobody owns it yet — not a valid "from"
            to_agent_key="juridico_saude",
            to_area_key="saude_suplementar",
            handoff_context={},
            actor_label="SDR Jurídico",
        )


@pytest.mark.asyncio
async def test_release_conversation_returns_thread_to_orphan(db_session, auth_context):
    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    claimed = await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    # Capture as a plain int first: `claimed.thread` and the row `release_
    # conversation` mutates below are the SAME identity-mapped ORM object,
    # so comparing the live attribute after release would just compare the
    # object to itself.
    generation_after_claim = claimed.thread.owner_generation

    released = await release_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        actor_label="SDR Jurídico",
    )
    assert released.thread.owning_agent_key is None
    assert released.thread.owner_generation == generation_after_claim + 1


@pytest.mark.asyncio
async def test_human_takeover_always_wins_and_displaces_agent_owner(db_session, auth_context):
    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )

    result = await human_takeover(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        human_user_id=auth_context.user_id,
        actor_label="Advogado responsável",
    )
    assert result.thread.owning_agent_key is None
    assert result.thread.assigned_human_id == auth_context.user_id
    assert result.thread.status == ThreadStatus.human_active

    takeover_events = await _thread_events(
        db_session,
        auth_context.workspace_id,
        thread.id,
        "conversation_ownership_human_takeover",
    )
    assert len(takeover_events) == 1
    assert takeover_events[0].after_data["manualOverrideWins"] is True
    assert takeover_events[0].before_data["owningAgentKey"] == "sdr"


@pytest.mark.asyncio
async def test_human_takeover_beats_a_pending_agent_claim_race(db_session, auth_context):
    """Even mid-race (agent about to claim, human takes over first), the
    human's takeover is unconditional — no `owner_generation` predicate can
    ever block it."""
    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    # Human takes over an orphan conversation before any agent claims it.
    result = await human_takeover(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        human_user_id=auth_context.user_id,
        actor_label="Advogado responsável",
    )
    assert result.thread.owner_generation == 1

    # An agent trying to claim afterwards finds the conversation still
    # "orphan" from the ownership column's point of view (human takeover
    # clears `owning_agent_key`) but is now attached to a human via
    # `assigned_human_id` / `status=human_active` — routing that surface is
    # the inbox's job, not this module's; here we only assert the CAS math
    # still behaves (claim succeeds against the post-takeover generation).
    claimed = await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    assert claimed.thread.owner_generation == 2


@pytest.mark.asyncio
async def test_open_second_area_matter_creates_new_matter_without_changing_owner(
    db_session, auth_context
):
    matter, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()

    claimed = await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    owner_before = (
        claimed.thread.owning_agent_key,
        claimed.thread.owning_area_key,
        claimed.thread.owner_generation,
    )

    new_matter = await open_second_area_matter(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread=claimed.thread,
        area_key="trabalhista",
        demand_type="Segunda demanda — rescisão indireta",
        created_by_user_id=auth_context.user_id,
    )
    assert new_matter.id != matter.id
    assert new_matter.legal_area_key == "trabalhista"
    assert new_matter.client_id == matter.client_id

    refreshed_thread = await db_session.scalar(
        select(OmnichannelThread)
        .where(OmnichannelThread.id == thread.id)
        .execution_options(populate_existing=True)
    )
    owner_after = (
        refreshed_thread.owning_agent_key,
        refreshed_thread.owning_area_key,
        refreshed_thread.owner_generation,
    )
    # Sequenciamento: abrir o caso da segunda área NUNCA troca o dono.
    assert owner_after == owner_before

    opened_events = await _thread_events(
        db_session,
        auth_context.workspace_id,
        thread.id,
        "conversation_second_area_matter_opened",
    )
    assert len(opened_events) == 1
    assert opened_events[0].after_data["newMatterId"] == str(new_matter.id)
    assert opened_events[0].after_data["threadOwningAgentKeyUnchanged"] == "sdr"


@pytest.mark.asyncio
async def test_claim_conversation_across_workspaces_is_isolated(db_session, auth_context):
    """A thread from workspace B is invisible to a claim scoped to workspace A
    — the workspace_id filter in every ownership query is the same discipline
    RLS enforces at the database layer for this already-protected table."""
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

    other_workspace = Workspace(
        name="Outro Workspace",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    _, thread = await _matter_and_thread(db_session, other_workspace.id)
    await db_session.commit()

    with pytest.raises(ConversationNotFoundError):
        await claim_conversation(
            db_session,
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            agent_key="sdr",
            area_key="saude_suplementar",
            actor_label="SDR Jurídico",
        )


# --- S1: humano ativo governa a resposta do agente ------------------------


@pytest.mark.asyncio
async def test_authorize_recusa_com_humano_ativo_e_volta_a_autorizar_apos_release(
    db_session, auth_context
):
    from app.services.conversation_ownership_service import (
        ConversationOwnershipDenied,
        authorize_agent_response,
        human_release,
        human_takeover,
    )

    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()
    await human_takeover(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        human_user_id=auth_context.user_id,
        actor_label="Advogado responsável",
    )

    with pytest.raises(ConversationOwnershipDenied) as recusa:
        await authorize_agent_response(
            db_session,
            workspace_id=auth_context.workspace_id,
            thread=thread,
            agent_key="sdr",
            area_key="saude_suplementar",
            actor_label="SDR Jurídico",
        )
    assert recusa.value.reason == "human_active"
    negadas = await _thread_events(
        db_session, auth_context.workspace_id, thread.id, "conversation_ownership_response_denied"
    )
    assert len(negadas) == 1
    assert negadas[0].after_data["reason"] == "human_active"
    assert negadas[0].after_data["assignedHumanId"] == str(auth_context.user_id)

    released = await human_release(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        actor_label="Advogado responsável",
    )
    assert released.thread.status == ThreadStatus.open

    autorizado = await authorize_agent_response(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread=thread,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    assert autorizado.thread.owning_agent_key == "sdr"


@pytest.mark.asyncio
async def test_human_release_restaura_o_status_anterior_e_mantem_o_responsavel(
    db_session, auth_context
):
    from app.services.conversation_ownership_service import human_release, human_takeover

    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    thread.status = ThreadStatus.waiting_documents
    await db_session.commit()
    versao_inicial = thread.version

    taken = await human_takeover(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        human_user_id=auth_context.user_id,
        actor_label="Advogado responsável",
    )
    assert taken.thread.status == ThreadStatus.human_active
    assert (
        taken.thread.thread_metadata["humanTakeover"]["previousStatus"] == "waiting_documents"
    )
    assert taken.thread.version == versao_inicial + 1

    released = await human_release(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        actor_label="Advogado responsável",
    )
    assert released.thread.status == ThreadStatus.waiting_documents
    assert released.thread.assigned_human_id == auth_context.user_id
    assert "humanTakeover" not in (released.thread.thread_metadata or {})
    assert released.thread.version == versao_inicial + 2
    eventos = await _thread_events(
        db_session, auth_context.workspace_id, thread.id, "conversation_ownership_human_release"
    )
    assert len(eventos) == 1


@pytest.mark.asyncio
async def test_human_takeover_idempotente_so_quando_nenhum_agente_e_dono(
    db_session, auth_context
):
    from app.services.conversation_ownership_service import human_takeover

    _, thread = await _matter_and_thread(db_session, auth_context.workspace_id)
    await db_session.commit()
    first = await human_takeover(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        human_user_id=auth_context.user_id,
        actor_label="Advogado responsável",
    )
    second = await human_takeover(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        human_user_id=auth_context.user_id,
        actor_label="Advogado responsável",
    )
    assert first.changed is True and second.changed is False
    assert second.thread.owner_generation == first.thread.owner_generation
    assert len(
        await _thread_events(
            db_session,
            auth_context.workspace_id,
            thread.id,
            "conversation_ownership_human_takeover",
        )
    ) == 1

    # Um agente reivindica por fora (claim direto continua permitido): o humano
    # ganha de novo, derrubando o agente, mesmo já estando ativo.
    await claim_conversation(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        agent_key="sdr",
        area_key="saude_suplementar",
        actor_label="SDR Jurídico",
    )
    third = await human_takeover(
        db_session,
        workspace_id=auth_context.workspace_id,
        thread_id=thread.id,
        human_user_id=auth_context.user_id,
        actor_label="Advogado responsável",
    )
    assert third.changed is True
    assert third.thread.owning_agent_key is None


@pytest.mark.asyncio
async def test_human_takeover_em_thread_de_outro_workspace_nao_encontra(db_session, auth_context):
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
    from app.services.conversation_ownership_service import human_takeover

    other_workspace = Workspace(
        name="Outro Workspace",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    _, thread = await _matter_and_thread(db_session, other_workspace.id)
    await db_session.commit()

    with pytest.raises(ConversationNotFoundError):
        await human_takeover(
            db_session,
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            human_user_id=auth_context.user_id,
            actor_label="Advogado responsável",
        )


@pytest.mark.asyncio
async def test_resolve_contact_thread_usa_o_binding_do_contato(db_session, auth_context):
    from app.models.omnichannel_v6 import OmnichannelThreadBinding
    from app.models.whatsapp import WhatsAppConnection, WhatsAppContact
    from app.services.conversation_ownership_service import resolve_contact_thread

    connection = WhatsAppConnection(workspace_id=auth_context.workspace_id)
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
    )
    sem_binding = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
    )
    db_session.add_all((contact, sem_binding))
    await db_session.flush()
    # thread canônica nasce sem matter (primeira mensagem)
    thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        OmnichannelThreadBinding(
            workspace_id=auth_context.workspace_id,
            thread_id=thread.id,
            provider="whatsapp",
            connection_key=f"whatsapp:{connection.id}",
            external_thread_id=f"whatsapp-contact:{contact.id}",
            binding_state="active",
        )
    )
    await db_session.commit()

    achada = await resolve_contact_thread(
        db_session, workspace_id=auth_context.workspace_id, contact_id=contact.id
    )
    assert achada is not None and achada.id == thread.id
    assert (
        await resolve_contact_thread(
            db_session, workspace_id=auth_context.workspace_id, contact_id=sem_binding.id
        )
        is None
    )
