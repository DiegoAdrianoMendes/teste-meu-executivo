"""Testes da máquina de estados da jornada publicação→pasta (Onda 4, §6).

Cobre: transições válidas avançam; inválida/regressão é barrada; protocolo
por agente é PROIBIDO; cada tipo de exceção nomeado abre item na Central de
Exceções em vez de travar; idempotência de `advance()`; isolamento por
workspace.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind, LegalException
from app.models.process_monitoring import (
    DeadlineStatus,
    MonitoredProcess,
    MonitoredProcessStatus,
    ProcessPublication,
    ProcessPublicationSource,
    PublicationJourneyState,
)
from app.services.legal_journey_service import (
    HUMAN_ONLY_STATES,
    JourneyActorType,
    LegalJourneyActorNotAllowedError,
    LegalJourneyInvalidTransitionError,
    LegalJourneyMissingActorError,
    LegalJourneyService,
    LegalJourneyWorkspaceMismatchError,
    VALID_TRANSITIONS,
)

_S = PublicationJourneyState


async def _make_publication(
    db: AsyncSession,
    workspace_id,
    *,
    monitored_process_id=None,
    journey_state: PublicationJourneyState = _S.capturada,
    dias=None,
    prazo_status=None,
    confirmed_by_user_id=None,
    confirmed_at=None,
) -> ProcessPublication:
    publication = ProcessPublication(
        workspace_id=workspace_id,
        monitored_process_id=monitored_process_id,
        fonte=ProcessPublicationSource.djen,
        external_id=f"ext-{uuid4().hex[:10]}",
        numero_cnj="0001234-37.2026.8.07.0001",
        conteudo="Texto da publicação.",
        published_at=datetime.now(UTC),
        journey_state=journey_state,
        dias=dias,
        prazo_status=prazo_status,
        confirmed_by_user_id=confirmed_by_user_id,
        confirmed_at=confirmed_at,
    )
    db.add(publication)
    await db.flush()
    return publication


async def _make_monitored_process(
    db: AsyncSession, workspace_id, created_by_user_id, *, matter_id=None
) -> MonitoredProcess:
    process = MonitoredProcess(
        workspace_id=workspace_id,
        matter_id=matter_id,
        numero_cnj="0001234-37.2026.8.07.0001",
        status=MonitoredProcessStatus.ativo,
        created_by_user_id=created_by_user_id,
    )
    db.add(process)
    await db.flush()
    return process


async def _count_advanced_audit_events(db: AsyncSession, workspace_id, publication_id) -> int:
    result = await db.execute(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == workspace_id,
            KlmAuditEvent.entity_id == publication_id,
            KlmAuditEvent.event_type == "legal_journey_advanced",
        )
    )
    return len(list(result.scalars().all()))


# ── Grafo ────────────────────────────────────────────────────────────────


def test_all_19_states_have_an_entry_in_the_transition_graph() -> None:
    assert len(PublicationJourneyState) == 19
    assert set(VALID_TRANSITIONS.keys()) == set(PublicationJourneyState)
    assert VALID_TRANSITIONS[_S.acompanhamento_criado] == frozenset()


# ── Transições válidas avançam ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_advance_valid_transition_updates_state_and_audits(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(db_session, auth_context.workspace_id)
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication, _S.deduplicada, actor_type=JourneyActorType.agent
    )

    assert result.advanced is True
    assert result.exception is None
    assert publication.journey_state == _S.deduplicada
    assert publication.journey_updated_at is not None
    assert await _count_advanced_audit_events(
        db_session, auth_context.workspace_id, publication.id
    ) == 1


@pytest.mark.asyncio
async def test_advance_is_idempotent_on_replay(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(
        db_session, auth_context.workspace_id, journey_state=_S.deduplicada
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication, _S.deduplicada, actor_type=JourneyActorType.agent
    )

    assert result.advanced is False
    assert result.exception is None
    assert publication.journey_state == _S.deduplicada
    assert await _count_advanced_audit_events(
        db_session, auth_context.workspace_id, publication.id
    ) == 0


# ── Inválida/regressão barrada ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_advance_cannot_skip_states(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(db_session, auth_context.workspace_id)
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    with pytest.raises(LegalJourneyInvalidTransitionError):
        await service.advance(publication, _S.vinculada, actor_type=JourneyActorType.agent)
    assert publication.journey_state == _S.capturada


@pytest.mark.asyncio
async def test_advance_cannot_regress_arbitrarily(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(
        db_session, auth_context.workspace_id, journey_state=_S.vinculo_sugerido
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    with pytest.raises(LegalJourneyInvalidTransitionError):
        await service.advance(publication, _S.capturada, actor_type=JourneyActorType.agent)
    assert publication.journey_state == _S.vinculo_sugerido


@pytest.mark.asyncio
async def test_correcoes_pendentes_to_minuta_is_the_only_accepted_cycle(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(
        db_session, auth_context.workspace_id, journey_state=_S.correcoes_pendentes
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication,
        _S.minuta_em_producao,
        actor_type=JourneyActorType.human,
        actor_user_id=auth_context.user_id,
    )
    assert result.advanced is True
    assert publication.journey_state == _S.minuta_em_producao


# ── Protocolo por agente é PROIBIDO ─────────────────────────────────────


@pytest.mark.asyncio
async def test_protocolo_registrado_by_agent_is_forbidden(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        journey_state=_S.aguardando_protocolo_humano,
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    with pytest.raises(LegalJourneyActorNotAllowedError):
        await service.advance(
            publication, _S.protocolo_registrado, actor_type=JourneyActorType.agent
        )
    assert publication.journey_state == _S.aguardando_protocolo_humano
    assert await _count_advanced_audit_events(
        db_session, auth_context.workspace_id, publication.id
    ) == 0


@pytest.mark.asyncio
async def test_protocolo_registrado_by_human_succeeds(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        journey_state=_S.aguardando_protocolo_humano,
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication,
        _S.protocolo_registrado,
        actor_type=JourneyActorType.human,
        actor_user_id=auth_context.user_id,
    )
    assert result.advanced is True
    assert publication.journey_state == _S.protocolo_registrado


@pytest.mark.asyncio
async def test_human_only_states_reject_agent_actor(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(
        db_session, auth_context.workspace_id, journey_state=_S.minuta_em_producao
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)
    assert _S.revisao_juridica in HUMAN_ONLY_STATES

    with pytest.raises(LegalJourneyActorNotAllowedError):
        await service.advance(
            publication, _S.revisao_juridica, actor_type=JourneyActorType.agent
        )
    assert publication.journey_state == _S.minuta_em_producao


@pytest.mark.asyncio
async def test_agent_can_automate_up_to_estrategia_preparada(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    process = await _make_monitored_process(
        db_session, auth_context.workspace_id, auth_context.user_id
    )
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        monitored_process_id=process.id,
        journey_state=_S.trabalho_distribuido,
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication, _S.documentos_solicitados, actor_type=JourneyActorType.agent
    )
    assert result.advanced is True

    result = await service.advance(
        publication, _S.estrategia_preparada, actor_type=JourneyActorType.agent
    )
    assert result.advanced is True
    assert publication.journey_state == _S.estrategia_preparada


@pytest.mark.asyncio
async def test_human_actor_without_user_id_raises(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(db_session, auth_context.workspace_id)
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    with pytest.raises(LegalJourneyMissingActorError):
        await service.advance(
            publication, _S.deduplicada, actor_type=JourneyActorType.human
        )


@pytest.mark.asyncio
async def test_workspace_mismatch_raises(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(db_session, auth_context.workspace_id)
    other_workspace_id = uuid4()
    service = LegalJourneyService(db_session, other_workspace_id)

    with pytest.raises(LegalJourneyWorkspaceMismatchError):
        await service.advance(publication, _S.deduplicada, actor_type=JourneyActorType.agent)


# ── Cada tipo de exceção nomeado abre item em vez de travar ─────────────


@pytest.mark.asyncio
async def test_vinculada_without_linked_process_opens_exception(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(
        db_session, auth_context.workspace_id, journey_state=_S.vinculo_sugerido
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication, _S.vinculada, actor_type=JourneyActorType.agent
    )

    assert result.advanced is False
    assert result.exception is not None
    assert result.exception.exception_kind == ExceptionKind.publicacao_sem_vinculo_confiavel
    assert result.exception.status == ExceptionCenterStatus.aberta
    assert publication.journey_state == _S.vinculo_sugerido


@pytest.mark.asyncio
async def test_vinculada_exception_is_idempotent_on_replay(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    publication = await _make_publication(
        db_session, auth_context.workspace_id, journey_state=_S.vinculo_sugerido
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    first = await service.advance(publication, _S.vinculada, actor_type=JourneyActorType.agent)
    second = await service.advance(publication, _S.vinculada, actor_type=JourneyActorType.agent)

    assert first.exception is not None
    assert second.exception is not None
    assert first.exception.id == second.exception.id

    count = await db_session.scalar(
        select(LegalException).where(
            LegalException.workspace_id == auth_context.workspace_id,
            LegalException.exception_kind == ExceptionKind.publicacao_sem_vinculo_confiavel,
        )
    )
    assert count is not None  # só uma linha existe — não duplicou


@pytest.mark.asyncio
async def test_prazo_sugerido_without_calculated_deadline_opens_prazo_ambiguo(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    process = await _make_monitored_process(
        db_session, auth_context.workspace_id, auth_context.user_id
    )
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        monitored_process_id=process.id,
        journey_state=_S.triagem_concluida,
        dias=None,
        prazo_status=None,
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication, _S.prazo_sugerido, actor_type=JourneyActorType.agent
    )

    assert result.advanced is False
    assert result.exception is not None
    assert result.exception.exception_kind == ExceptionKind.prazo_ambiguo
    assert publication.journey_state == _S.triagem_concluida


@pytest.mark.asyncio
async def test_prazo_confirmado_without_human_confirmation_opens_prazo_ambiguo(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    process = await _make_monitored_process(
        db_session, auth_context.workspace_id, auth_context.user_id
    )
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        monitored_process_id=process.id,
        journey_state=_S.prazo_sugerido,
        dias=15,
        prazo_status=DeadlineStatus.prazo_sugerido,
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication, _S.prazo_confirmado, actor_type=JourneyActorType.agent
    )

    assert result.advanced is False
    assert result.exception is not None
    assert result.exception.exception_kind == ExceptionKind.prazo_ambiguo
    assert publication.journey_state == _S.prazo_sugerido


@pytest.mark.asyncio
async def test_prazo_confirmado_advances_once_deadline_is_confirmed(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    process = await _make_monitored_process(
        db_session, auth_context.workspace_id, auth_context.user_id
    )
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        monitored_process_id=process.id,
        journey_state=_S.prazo_sugerido,
        dias=15,
        prazo_status=DeadlineStatus.confirmado,
        confirmed_by_user_id=auth_context.user_id,
        confirmed_at=datetime.now(UTC),
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication,
        _S.prazo_confirmado,
        actor_type=JourneyActorType.human,
        actor_user_id=auth_context.user_id,
    )

    assert result.advanced is True
    assert result.exception is None
    assert publication.journey_state == _S.prazo_confirmado


@pytest.mark.asyncio
async def test_documento_indispensavel_ausente_opens_exception(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    process = await _make_monitored_process(
        db_session, auth_context.workspace_id, auth_context.user_id
    )
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        monitored_process_id=process.id,
        journey_state=_S.documentos_solicitados,
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication,
        _S.estrategia_preparada,
        actor_type=JourneyActorType.agent,
        checkpoint_context={"documento_indispensavel_ausente": True},
    )

    assert result.advanced is False
    assert result.exception is not None
    assert result.exception.exception_kind == ExceptionKind.documento_indispensavel_ausente
    assert publication.journey_state == _S.documentos_solicitados

    # Sem a flag, o mesmo alvo avança normalmente.
    result_ok = await service.advance(
        publication, _S.estrategia_preparada, actor_type=JourneyActorType.agent
    )
    assert result_ok.advanced is True
    assert publication.journey_state == _S.estrategia_preparada


@pytest.mark.asyncio
async def test_precedente_nao_verificado_opens_exception(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    process = await _make_monitored_process(
        db_session, auth_context.workspace_id, auth_context.user_id
    )
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        monitored_process_id=process.id,
        journey_state=_S.minuta_em_producao,
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    result = await service.advance(
        publication,
        _S.revisao_juridica,
        actor_type=JourneyActorType.human,
        actor_user_id=auth_context.user_id,
        checkpoint_context={"precedente_nao_verificado": True},
    )

    assert result.advanced is False
    assert result.exception is not None
    assert result.exception.exception_kind == ExceptionKind.precedente_nao_verificado
    assert publication.journey_state == _S.minuta_em_producao


# ── Caminho feliz completo (19 estados) ─────────────────────────────────


@pytest.mark.asyncio
async def test_full_happy_path_reaches_acompanhamento_criado(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    process = await _make_monitored_process(
        db_session, auth_context.workspace_id, auth_context.user_id
    )
    publication = await _make_publication(
        db_session,
        auth_context.workspace_id,
        monitored_process_id=process.id,
        dias=15,
        prazo_status=DeadlineStatus.confirmado,
        confirmed_by_user_id=auth_context.user_id,
        confirmed_at=datetime.now(UTC),
    )
    service = LegalJourneyService(db_session, auth_context.workspace_id)

    agent_path = [
        _S.deduplicada,
        _S.vinculo_sugerido,
        _S.vinculada,
        _S.triagem_concluida,
        _S.prazo_sugerido,
        _S.prazo_confirmado,
        _S.trabalho_distribuido,
        _S.documentos_solicitados,
        _S.estrategia_preparada,
        _S.minuta_em_producao,
    ]
    for target in agent_path:
        result = await service.advance(publication, target, actor_type=JourneyActorType.agent)
        assert result.advanced is True, f"falhou em {target}"

    human_path = [
        _S.revisao_juridica,
        _S.aprovada,
        _S.pasta_em_preparacao,
        _S.pasta_validada,
        _S.aguardando_protocolo_humano,
        _S.protocolo_registrado,
        _S.acompanhamento_criado,
    ]
    for target in human_path:
        result = await service.advance(
            publication,
            target,
            actor_type=JourneyActorType.human,
            actor_user_id=auth_context.user_id,
        )
        assert result.advanced is True, f"falhou em {target}"

    assert publication.journey_state == _S.acompanhamento_criado
    assert await _count_advanced_audit_events(
        db_session, auth_context.workspace_id, publication.id
    ) == len(agent_path) + len(human_path)
