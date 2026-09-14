"""LegalJourneyService — máquina de estados da jornada publicação→pasta.

Os 19 estados nomeados no Plano Mestre §6 (`PublicationJourneyState`, ver
`app/models/process_monitoring.py`) descrevem o caminho de uma
`ProcessPublication` desde a captura bruta (DJEN/DataJud) até o
acompanhamento pós-protocolo. Este serviço é a ÚNICA porta de avanço dessa
jornada:

  * MONOTÔNICA — `VALID_TRANSITIONS` é um grafo explícito de arestas
    permitidas; não existe "pular etapa" nem "regredir arbitrário". O único
    ciclo aceito é `revisao_juridica -> correcoes_pendentes ->
    minuta_em_producao` (retrabalho após reprovação), porque é uma aresta
    nomeada no grafo, não uma regressão livre;
  * `protocolo_registrado` só é alcançável por ator HUMANO — nunca agente.
    Esta é a linha vermelha do módulo (Plano Mestre §6): nenhum agente
    protocola sozinho. Fica coberta em dois lugares redundantes de propósito
    (o conjunto `HUMAN_ONLY_STATES` já barra qualquer agente a partir de
    `revisao_juridica`, e há uma checagem extra dedicada só para
    `protocolo_registrado`, para que o motivo do bloqueio nunca fique
    ambíguo numa auditoria);
  * automação (ator `agent`, tipicamente A2/A3) pode avançar sozinha até
    `estrategia_preparada` inclusive; a partir de `revisao_juridica` todo
    avanço exige ator `human` (derivado do usuário autenticado, nunca do
    payload — mesma lição de `ExceptionCenterService`/`ApprovalService`);
  * cada avanço é auditado via `KlmAuditEvent` (append-only, mesmo padrão de
    `ProcessMonitoringService`/`ExceptionCenterService` — não nasce uma
    tabela de auditoria paralela);
  * `advance()` é IDEMPOTENTE: reenviar o mesmo `to_state` que a publicação
    já tem é um no-op silencioso (replay seguro para orquestração de
    agentes), nunca duplica o evento de auditoria;
  * nos pontos de exceção nomeados no plano (publicação sem vínculo
    confiável, prazo ambíguo, precedente não verificado, documento
    indispensável ausente) o serviço NUNCA lança uma exceção Python dura de
    domínio — ele abre um item na Central de Exceções
    (`app/services/exception_center_service.py`, Onda 2) e devolve a
    publicação SEM avançar, para que um humano decida. A jornada só trava
    para erros de programação (transição inválida, ator não autorizado) —
    nunca para uma decisão de negócio ambígua.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalPriority
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_exception import ExceptionKind, LegalException
from app.models.process_monitoring import (
    DeadlineStatus,
    MonitoredProcess,
    ProcessPublication,
    PublicationJourneyState,
)
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.exception_center_service import ExceptionCenterService

_S = PublicationJourneyState


class JourneyActorType(StrEnum):
    """Quem está pedindo o avanço — grava-se literal em `KlmAuditEvent.actor_type`."""

    agent = "agent"
    human = "human"


# ── Grafo de transições válidas (monotônico) ───────────────────────────────
# Único ciclo aceito: revisao_juridica -> correcoes_pendentes ->
# minuta_em_producao (retrabalho), porque é uma aresta nomeada — não uma
# regressão arbitrária. Todo o resto só anda para frente.
VALID_TRANSITIONS: dict[PublicationJourneyState, frozenset[PublicationJourneyState]] = {
    _S.capturada: frozenset({_S.deduplicada}),
    _S.deduplicada: frozenset({_S.vinculo_sugerido}),
    _S.vinculo_sugerido: frozenset({_S.vinculada}),
    _S.vinculada: frozenset({_S.triagem_concluida}),
    _S.triagem_concluida: frozenset({_S.prazo_sugerido}),
    _S.prazo_sugerido: frozenset({_S.prazo_confirmado}),
    _S.prazo_confirmado: frozenset({_S.trabalho_distribuido}),
    _S.trabalho_distribuido: frozenset({_S.documentos_solicitados}),
    _S.documentos_solicitados: frozenset({_S.estrategia_preparada}),
    _S.estrategia_preparada: frozenset({_S.minuta_em_producao}),
    _S.minuta_em_producao: frozenset({_S.revisao_juridica}),
    _S.revisao_juridica: frozenset({_S.aprovada, _S.correcoes_pendentes}),
    _S.correcoes_pendentes: frozenset({_S.minuta_em_producao}),
    _S.aprovada: frozenset({_S.pasta_em_preparacao}),
    _S.pasta_em_preparacao: frozenset({_S.pasta_validada}),
    _S.pasta_validada: frozenset({_S.aguardando_protocolo_humano}),
    _S.aguardando_protocolo_humano: frozenset({_S.protocolo_registrado}),
    _S.protocolo_registrado: frozenset({_S.acompanhamento_criado}),
    _S.acompanhamento_criado: frozenset(),
}

# A partir daqui (inclusive) todo avanço exige ator humano — automação de
# agente (A2/A3) para no máximo em `estrategia_preparada`.
HUMAN_ONLY_STATES: frozenset[PublicationJourneyState] = frozenset(
    {
        _S.revisao_juridica,
        _S.correcoes_pendentes,
        _S.aprovada,
        _S.pasta_em_preparacao,
        _S.pasta_validada,
        _S.aguardando_protocolo_humano,
        _S.protocolo_registrado,
        _S.acompanhamento_criado,
    }
)

_AGENT_LABEL_DEFAULT = "Automação (IA)"


class LegalJourneyError(RuntimeError):
    """Base para erros de domínio da jornada publicação→pasta."""


class LegalJourneyWorkspaceMismatchError(LegalJourneyError):
    pass


class LegalJourneyInvalidTransitionError(LegalJourneyError):
    def __init__(
        self, current: PublicationJourneyState, to_state: PublicationJourneyState
    ) -> None:
        self.current = current
        self.to_state = to_state
        super().__init__(
            f"Transição inválida: '{current.value}' -> '{to_state.value}' não é uma "
            "aresta permitida na jornada publicação→pasta."
        )


class LegalJourneyActorNotAllowedError(LegalJourneyError):
    def __init__(
        self,
        to_state: PublicationJourneyState,
        actor_type: JourneyActorType,
        *,
        is_protocol: bool = False,
    ) -> None:
        self.to_state = to_state
        self.actor_type = actor_type
        if is_protocol:
            message = (
                "Protocolo (`protocolo_registrado`) só pode ser registrado por ação "
                "HUMANA explícita — agentes nunca protocolam sozinhos."
            )
        else:
            message = (
                f"Avançar para '{to_state.value}' exige ator humano "
                f"(recebido: '{actor_type.value}') — a partir de 'revisao_juridica' "
                "a automação para e um humano assume."
            )
        super().__init__(message)


class LegalJourneyMissingActorError(LegalJourneyError):
    pass


@dataclass
class JourneyAdvanceResult:
    """Resultado de `advance()` — nunca lança para decisões de negócio
    ambíguas; `exception` sinaliza que a jornada abriu um item na Central de
    Exceções em vez de travar, e `publication.journey_state` NÃO mudou."""

    publication: ProcessPublication
    advanced: bool
    exception: Optional[LegalException] = None


class LegalJourneyService:
    def __init__(self, db: AsyncSession, workspace_id: UUID) -> None:
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id

    async def advance(
        self,
        publication: ProcessPublication,
        to_state: PublicationJourneyState,
        *,
        actor_type: JourneyActorType,
        actor_user_id: Optional[UUID] = None,
        actor_label: Optional[str] = None,
        checkpoint_context: Optional[dict] = None,
    ) -> JourneyAdvanceResult:
        if publication.workspace_id != self.workspace_id:
            raise LegalJourneyWorkspaceMismatchError(
                f"Publicação {publication.id} não pertence ao workspace {self.workspace_id}."
            )

        current = publication.journey_state

        # Idempotência: reenviar o mesmo alvo é um no-op — replay seguro
        # para orquestração de agentes (retry de rede, reprocessamento).
        if current == to_state:
            return JourneyAdvanceResult(publication=publication, advanced=False)

        allowed = VALID_TRANSITIONS.get(current, frozenset())
        if to_state not in allowed:
            raise LegalJourneyInvalidTransitionError(current, to_state)

        if to_state == _S.protocolo_registrado and actor_type != JourneyActorType.human:
            raise LegalJourneyActorNotAllowedError(to_state, actor_type, is_protocol=True)
        if to_state in HUMAN_ONLY_STATES and actor_type != JourneyActorType.human:
            raise LegalJourneyActorNotAllowedError(to_state, actor_type)

        if actor_type == JourneyActorType.human:
            if actor_user_id is None:
                raise LegalJourneyMissingActorError(
                    "Avanço por ator humano exige actor_user_id (derivado do usuário "
                    "autenticado, nunca do payload)."
                )
            resolved_label = await resolve_human_actor_label(self.db, actor_user_id)
        else:
            resolved_label = actor_label or _AGENT_LABEL_DEFAULT

        exception = await self._run_checkpoint_guard(
            publication, to_state, checkpoint_context or {}, actor_user_id
        )
        if exception is not None:
            return JourneyAdvanceResult(publication=publication, advanced=False, exception=exception)

        before_state = current.value
        now = datetime.now(UTC)
        publication.journey_state = to_state
        publication.journey_updated_at = now
        self.db.add(
            KlmAuditEvent(
                workspace_id=self.workspace_id,
                actor_user_id=actor_user_id if actor_type == JourneyActorType.human else None,
                actor_type=actor_type.value,
                actor_label=resolved_label,
                event_type="legal_journey_advanced",
                entity_type="process_publication",
                entity_id=publication.id,
                correlation_id=f"{before_state}->{to_state.value}",
                summary=(
                    f"Publicação {publication.id} ({publication.numero_cnj}) avançou de "
                    f"'{before_state}' para '{to_state.value}'."
                ),
                before_data={"journeyState": before_state},
                after_data={"journeyState": to_state.value},
            )
        )
        await self.db.flush()
        return JourneyAdvanceResult(publication=publication, advanced=True)

    # ── Pontos de exceção do plano — abrem item em vez de travar ──────────

    async def _run_checkpoint_guard(
        self,
        publication: ProcessPublication,
        to_state: PublicationJourneyState,
        ctx: dict,
        actor_user_id: Optional[UUID],
    ) -> Optional[LegalException]:
        exceptions = ExceptionCenterService(self.db, self.workspace_id)

        if to_state == _S.vinculada and publication.monitored_process_id is None:
            return await exceptions.open_exception(
                exception_kind=ExceptionKind.publicacao_sem_vinculo_confiavel,
                origin="legal_journey_service.advance",
                dedupe_key=f"{publication.id}:vinculada",
                title=f"Publicação sem processo vinculado — {publication.numero_cnj}",
                description=(
                    "A jornada não pode avançar para 'vinculada': nenhum "
                    "MonitoredProcess confiável está associado a esta publicação. "
                    "Confirme o vínculo manualmente."
                ),
                priority=(
                    ApprovalPriority.alta
                    if publication.urgencia == "critica"
                    else ApprovalPriority.media
                ),
                payload={
                    "numeroCnj": publication.numero_cnj,
                    "publicationId": str(publication.id),
                    "toState": to_state.value,
                },
                opened_by=actor_user_id,
            )

        if to_state == _S.prazo_sugerido and (
            publication.dias is None or publication.prazo_status is None
        ):
            return await self._open_prazo_ambiguo(
                publication, to_state, actor_user_id, exceptions,
                motivo="Nenhum prazo foi calculado para esta publicação.",
            )

        if to_state == _S.prazo_confirmado and publication.prazo_status != DeadlineStatus.confirmado:
            return await self._open_prazo_ambiguo(
                publication, to_state, actor_user_id, exceptions,
                motivo=(
                    "O prazo ainda não foi confirmado por um humano "
                    f"(status atual: '{publication.prazo_status.value if publication.prazo_status else 'ausente'}')."
                ),
            )

        if to_state == _S.estrategia_preparada and ctx.get("documento_indispensavel_ausente"):
            matter_id = await self._matter_id_for(publication)
            return await exceptions.open_exception(
                exception_kind=ExceptionKind.documento_indispensavel_ausente,
                origin="legal_journey_service.advance",
                dedupe_key=f"{publication.id}:estrategia_preparada",
                title=f"Documento indispensável ausente — {publication.numero_cnj}",
                description=(
                    "A jornada não pode avançar para 'estrategia_preparada': falta "
                    "documento indispensável solicitado ao cliente/caso."
                ),
                priority=ApprovalPriority.alta,
                matter_id=matter_id,
                payload={
                    "numeroCnj": publication.numero_cnj,
                    "publicationId": str(publication.id),
                    "toState": to_state.value,
                    "detalhe": ctx.get("documento_indispensavel_ausente_detalhe"),
                },
                opened_by=actor_user_id,
            )

        if to_state == _S.revisao_juridica and ctx.get("precedente_nao_verificado"):
            matter_id = await self._matter_id_for(publication)
            return await exceptions.open_exception(
                exception_kind=ExceptionKind.precedente_nao_verificado,
                origin="legal_journey_service.advance",
                dedupe_key=f"{publication.id}:revisao_juridica",
                title=f"Precedente não verificado — {publication.numero_cnj}",
                description=(
                    "A jornada não pode avançar para 'revisao_juridica': a minuta cita "
                    "precedente/tese que ainda não foi verificado em fonte primária."
                ),
                priority=ApprovalPriority.alta,
                matter_id=matter_id,
                payload={
                    "numeroCnj": publication.numero_cnj,
                    "publicationId": str(publication.id),
                    "toState": to_state.value,
                    "detalhe": ctx.get("precedente_nao_verificado_detalhe"),
                },
                opened_by=actor_user_id,
            )

        return None

    async def _open_prazo_ambiguo(
        self,
        publication: ProcessPublication,
        to_state: PublicationJourneyState,
        actor_user_id: Optional[UUID],
        exceptions: ExceptionCenterService,
        *,
        motivo: str,
    ) -> LegalException:
        matter_id = await self._matter_id_for(publication)
        return await exceptions.open_exception(
            exception_kind=ExceptionKind.prazo_ambiguo,
            origin="legal_journey_service.advance",
            dedupe_key=f"{publication.id}:{to_state.value}",
            title=f"Prazo ambíguo — {publication.numero_cnj}",
            description=f"A jornada não pode avançar para '{to_state.value}': {motivo}",
            priority=ApprovalPriority.alta,
            matter_id=matter_id,
            payload={
                "numeroCnj": publication.numero_cnj,
                "publicationId": str(publication.id),
                "toState": to_state.value,
            },
            opened_by=actor_user_id,
        )

    async def _matter_id_for(self, publication: ProcessPublication) -> Optional[UUID]:
        if publication.monitored_process_id is None:
            return None
        monitored_process = await self.db.scalar(
            select(MonitoredProcess).where(
                MonitoredProcess.id == publication.monitored_process_id,
                MonitoredProcess.workspace_id == self.workspace_id,
            )
        )
        return monitored_process.matter_id if monitored_process else None


__all__ = [
    "LegalJourneyService",
    "JourneyActorType",
    "JourneyAdvanceResult",
    "VALID_TRANSITIONS",
    "HUMAN_ONLY_STATES",
    "LegalJourneyError",
    "LegalJourneyWorkspaceMismatchError",
    "LegalJourneyInvalidTransitionError",
    "LegalJourneyActorNotAllowedError",
    "LegalJourneyMissingActorError",
]
