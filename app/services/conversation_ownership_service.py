"""Posse de conversa por agente — harmonização multi-área no mesmo WhatsApp.

Vários agentes especialistas (SDR, especialistas de área, closer, humano)
podem operar sobre o mesmo número/canal sem colidir. Este módulo é a fonte
única de verdade de QUEM pode responder por uma `OmnichannelThread` agora —
`owning_agent_key` / `owning_area_key` / `owner_since` / `owner_generation`
em `app.models.klm_operations.OmnichannelThread`.

Não confundir com `OmnichannelThread.owner_agent_key` (sem o "ing"): esse é
o rótulo informativo, sincronizado automaticamente pelo roteamento de área em
`MatterService._sync_legal_routing`, e não participa de nenhuma corrida
concorrente. As colunas geridas aqui são a posse FORTE: um `UPDATE`
condicional (compare-and-swap) por `owner_generation`, no mesmo espírito dos
leases de pipeline já usados em `omnichannel_v6` (`lease_owner` /
`lease_expires_at` / `lease_generation`) e em
`OSSNativeOutboxService._claim_with_atomic_update`.

Operações:
  - `claim_conversation`  — reivindica uma conversa órfã (sem dono) para um
    agente/área; CAS por `owner_generation`, perdedor relê o estado atual.
  - `transfer_conversation` — handoff explícito de um dono agente para
    outro, sempre com `HandoffSummary` estruturado (nunca o lead repete a
    história) e evento de auditoria.
  - `release_conversation` — dono agente libera a conversa (volta a órfã),
    auditado.
  - `human_takeover` — um humano SEMPRE pode assumir; nenhuma corrida o
    barra, derruba o dono agente vigente incondicionalmente
    (`manual_override_wins`).
  - `authorize_agent_response` — ponto único de decisão consumido por
    `KlmAgentService._execute_one`: conversa órfã é reivindicada
    automaticamente pelo primeiro agente que chega (recepção); conversa já
    possuída só responde para o dono; qualquer outro agente é recusado de
    forma determinística e auditada (nunca duas respostas na mesma janela).
  - `open_second_area_matter` — quando surge uma segunda demanda de outra
    área no meio da conversa, abre um `Matter` novo naquela área SEM tocar a
    posse da conversa — o dono atual termina o funil corrente antes de
    qualquer transferência explícita.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import KlmAuditEvent, OmnichannelThread, ThreadStatus
from app.models.omnichannel_v6 import OmnichannelThreadBinding
from app.services.agent_governance_service import HandoffSummary, build_handoff_summary

CONVERSATION_OWNERSHIP_VERSION = "conversation-ownership-v1"


class ConversationOwnershipError(ValueError):
    """Base error for ownership operations that cannot be applied as requested.

    Subclasses `ValueError` — same convention as
    `AgentGovernanceError(ValueError)` — so it is caught by the generic
    `except (ValueError, LookupError)` branch already wired into every
    endpoint that calls `KlmAgentService.execute`/`_execute_one`, and then
    dispatched to a stable HTTP code by `_http_error` in
    `app/api/v1/endpoints/klm_operations.py`.
    """


class ConversationNotFoundError(ConversationOwnershipError):
    """The referenced thread does not exist in this workspace."""


class OwnershipConflictError(ConversationOwnershipError):
    """A CAS claim/transfer lost the race to a fresher `owner_generation`.

    `thread` carries the freshly re-read row so the caller can decide
    whether to retry against the current state instead of the stale one it
    started with.
    """

    def __init__(self, thread: OmnichannelThread, *, detail: str = "") -> None:
        self.thread = thread
        message = (
            f"Conversa {thread.id} já está na geração {thread.owner_generation} "
            f"(dono atual: {thread.owning_agent_key or 'nenhum'}); a operação "
            "perdeu a corrida e deve reler o estado atual antes de repetir."
        )
        super().__init__(f"{message} {detail}".strip())


class ConversationOwnershipDenied(ConversationOwnershipError):
    """Deterministic refusal: `agent_key` is not the owner of `thread`.

    Raised by `authorize_agent_response` so the caller never generates a
    second response for the same inbound window — the denial itself is
    already audited (`KlmAuditEvent`) before this is raised.
    """

    def __init__(self, *, thread: OmnichannelThread, agent_key: str, reason: str) -> None:
        self.thread = thread
        self.agent_key = agent_key
        self.reason = reason
        super().__init__(
            f"Agente '{agent_key}' não é dono da conversa {thread.id} "
            f"(dono atual: '{thread.owning_agent_key}'; motivo: {reason})."
        )


@dataclass(frozen=True)
class OwnershipResult:
    """Outcome of a claim/transfer/release/takeover call."""

    thread: OmnichannelThread
    changed: bool
    already_owner: bool
    reason: str


WHATSAPP_CONTACT_BINDING_PREFIX = "whatsapp-contact:"


async def resolve_contact_thread(
    db: AsyncSession, *, workspace_id: UUID, contact_id: UUID
) -> OmnichannelThread | None:
    """Thread canônica de um contato de WhatsApp, pelo binding
    `whatsapp-contact:{contact_id}` (o mais recente ativo). A thread nasce na
    primeira mensagem com `matter_id` nulo e um caso pode ter vários contatos:
    o binding é a única chave que aponta para a conversa certa (S1)."""
    thread_id = await db.scalar(
        select(OmnichannelThreadBinding.thread_id)
        .where(
            OmnichannelThreadBinding.workspace_id == workspace_id,
            OmnichannelThreadBinding.external_thread_id
            == f"{WHATSAPP_CONTACT_BINDING_PREFIX}{contact_id}",
            OmnichannelThreadBinding.binding_state == "active",
        )
        .order_by(OmnichannelThreadBinding.created_at.desc())
        .limit(1)
    )
    if thread_id is None:
        return None
    return await db.scalar(
        select(OmnichannelThread).where(
            OmnichannelThread.id == thread_id,
            OmnichannelThread.workspace_id == workspace_id,
        )
    )


def human_already_holds(thread: OmnichannelThread, human_user_id: UUID | None) -> bool:
    """Idempotência do takeover: só quando o humano já está ativo E nenhum agente
    é dono. Com agente dono, o humano SEMPRE ganha de novo (derruba o agente)."""
    return (
        human_user_id is not None
        and thread.status == ThreadStatus.human_active
        and thread.assigned_human_id == human_user_id
        and thread.owning_agent_key is None
    )


async def _load_thread(
    db: AsyncSession, *, workspace_id: UUID, thread_id: UUID, fresh: bool = False
) -> OmnichannelThread:
    statement = select(OmnichannelThread).where(
        OmnichannelThread.id == thread_id,
        OmnichannelThread.workspace_id == workspace_id,
    )
    if fresh:
        statement = statement.execution_options(populate_existing=True)
    thread = await db.scalar(statement)
    if thread is None:
        raise ConversationNotFoundError(
            f"Conversa {thread_id} não encontrada no workspace {workspace_id}."
        )
    return thread


def _audit(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread_id: UUID,
    actor_type: str,
    actor_label: str,
    event_type: str,
    summary: str,
    before_data: dict[str, Any],
    after_data: dict[str, Any],
) -> None:
    db.add(
        KlmAuditEvent(
            workspace_id=workspace_id,
            actor_type=actor_type,
            actor_label=actor_label,
            event_type=event_type,
            entity_type="omnichannel_thread",
            entity_id=thread_id,
            summary=summary,
            correlation_id=str(thread_id),
            before_data=before_data,
            after_data=after_data,
        )
    )


async def claim_conversation(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread_id: UUID,
    agent_key: str,
    area_key: Optional[str],
    actor_label: str,
    reason: str = "claim",
) -> OwnershipResult:
    """Atomically claim an orphan conversation for `agent_key`/`area_key`.

    CAS by `owner_generation`: two concurrent callers that both read the same
    generation race the same `UPDATE ... WHERE owner_generation == <read>`;
    only the first commit's row satisfies the predicate on the database side,
    the loser gets zero rows back (`OwnershipConflictError`, carrying the
    fresh row) and must reread before deciding whether to retry.

    Idempotent when `agent_key` already owns the thread (no-op, no new
    generation, no audit noise). Raises `OwnershipConflictError` immediately
    — without attempting the UPDATE — when a *different* agent already owns
    it; that is not this function's job (see `transfer_conversation` for an
    explicit handoff and `human_takeover` for the manual-override path).
    """
    if not agent_key:
        raise ValueError("agent_key é obrigatório para claim_conversation.")

    thread = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id)

    if thread.owning_agent_key == agent_key:
        return OwnershipResult(
            thread=thread, changed=False, already_owner=True, reason="already_owner"
        )
    if thread.owning_agent_key is not None:
        raise OwnershipConflictError(thread, detail="conversa já possuída por outro agente.")

    expected_generation = thread.owner_generation
    now = datetime.now(timezone.utc)
    statement = (
        update(OmnichannelThread)
        .where(
            OmnichannelThread.id == thread_id,
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.owner_generation == expected_generation,
            OmnichannelThread.owning_agent_key.is_(None),
        )
        .values(
            owning_agent_key=agent_key,
            owning_area_key=area_key,
            owner_since=now,
            owner_generation=expected_generation + 1,
        )
        .execution_options(synchronize_session=False)
        .returning(OmnichannelThread.id)
    )
    claimed_ids = list((await db.scalars(statement)).all())
    if not claimed_ids:
        fresh = await _load_thread(
            db, workspace_id=workspace_id, thread_id=thread_id, fresh=True
        )
        raise OwnershipConflictError(fresh, detail="claim perdeu a corrida (CAS).")

    await db.flush()
    refreshed = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id, fresh=True)
    _audit(
        db,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_type="agent",
        actor_label=actor_label,
        event_type="conversation_ownership_claimed",
        summary=(
            f"{actor_label} assumiu a conversa órfã "
            f"({area_key or 'área não definida'})."
        ),
        before_data={"owningAgentKey": None, "ownerGeneration": expected_generation},
        after_data={
            "owningAgentKey": agent_key,
            "owningAreaKey": area_key,
            "ownerGeneration": refreshed.owner_generation,
            "reason": reason,
        },
    )
    return OwnershipResult(thread=refreshed, changed=True, already_owner=False, reason=reason)


async def transfer_conversation(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread_id: UUID,
    from_agent_key: str,
    to_agent_key: str,
    to_area_key: Optional[str],
    handoff_context: Mapping[str, Any],
    actor_label: str,
) -> tuple[OwnershipResult, HandoffSummary]:
    """Explicit handoff from the current owner agent to another agent/area.

    Only succeeds while `from_agent_key` still is the owner at the read
    generation (CAS) — a stale caller loses to whoever moved the thread
    first and gets `OwnershipConflictError` with the fresh row. Always
    attaches a structured `HandoffSummary` (`build_handoff_summary`) to the
    audit event's `after_data`, so the lead's story never needs to be told
    twice to the agent that picks the conversation up (voz única).
    """
    thread = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id)
    if thread.owning_agent_key != from_agent_key:
        raise OwnershipConflictError(
            thread, detail=f"'{from_agent_key}' não é o dono atual para transferir."
        )

    summary = build_handoff_summary(handoff_context)
    expected_generation = thread.owner_generation
    now = datetime.now(timezone.utc)
    statement = (
        update(OmnichannelThread)
        .where(
            OmnichannelThread.id == thread_id,
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.owner_generation == expected_generation,
            OmnichannelThread.owning_agent_key == from_agent_key,
        )
        .values(
            owning_agent_key=to_agent_key,
            owning_area_key=to_area_key,
            owner_since=now,
            owner_generation=expected_generation + 1,
        )
        .execution_options(synchronize_session=False)
        .returning(OmnichannelThread.id)
    )
    transferred_ids = list((await db.scalars(statement)).all())
    if not transferred_ids:
        fresh = await _load_thread(
            db, workspace_id=workspace_id, thread_id=thread_id, fresh=True
        )
        raise OwnershipConflictError(fresh, detail="transfer perdeu a corrida (CAS).")

    await db.flush()
    refreshed = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id, fresh=True)
    _audit(
        db,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_type="agent",
        actor_label=actor_label,
        event_type="conversation_ownership_transferred",
        summary=(
            f"Handoff de '{from_agent_key}' para '{to_agent_key}' "
            f"({to_area_key or 'área não definida'})."
        ),
        before_data={
            "owningAgentKey": from_agent_key,
            "owningAreaKey": thread.owning_area_key,
            "ownerGeneration": expected_generation,
        },
        after_data={
            "owningAgentKey": to_agent_key,
            "owningAreaKey": to_area_key,
            "ownerGeneration": refreshed.owner_generation,
            "handoffSummary": summary.as_payload(),
        },
    )
    return (
        OwnershipResult(thread=refreshed, changed=True, already_owner=False, reason="transfer"),
        summary,
    )


async def release_conversation(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread_id: UUID,
    agent_key: str,
    actor_label: str,
    reason: str = "release",
) -> OwnershipResult:
    """The current owner agent releases the conversation back to orphan.

    Same CAS discipline as `claim_conversation`/`transfer_conversation`: only
    succeeds while `agent_key` still owns the thread at the read generation.
    """
    thread = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id)
    if thread.owning_agent_key != agent_key:
        raise OwnershipConflictError(
            thread, detail=f"'{agent_key}' não é o dono atual para liberar."
        )

    expected_generation = thread.owner_generation
    now = datetime.now(timezone.utc)
    statement = (
        update(OmnichannelThread)
        .where(
            OmnichannelThread.id == thread_id,
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.owner_generation == expected_generation,
            OmnichannelThread.owning_agent_key == agent_key,
        )
        .values(
            owning_agent_key=None,
            owning_area_key=None,
            owner_since=now,
            owner_generation=expected_generation + 1,
        )
        .execution_options(synchronize_session=False)
        .returning(OmnichannelThread.id)
    )
    released_ids = list((await db.scalars(statement)).all())
    if not released_ids:
        fresh = await _load_thread(
            db, workspace_id=workspace_id, thread_id=thread_id, fresh=True
        )
        raise OwnershipConflictError(fresh, detail="release perdeu a corrida (CAS).")

    await db.flush()
    refreshed = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id, fresh=True)
    _audit(
        db,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_type="agent",
        actor_label=actor_label,
        event_type="conversation_ownership_released",
        summary=f"{actor_label} liberou a conversa (volta a órfã).",
        before_data={
            "owningAgentKey": agent_key,
            "ownerGeneration": expected_generation,
        },
        after_data={"owningAgentKey": None, "ownerGeneration": refreshed.owner_generation, "reason": reason},
    )
    return OwnershipResult(thread=refreshed, changed=True, already_owner=False, reason=reason)


async def human_takeover(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread_id: UUID,
    human_user_id: Optional[UUID],
    actor_label: str,
    actor_type: str = "human",
    reason: str = "manual_override",
) -> OwnershipResult:
    """A human ALWAYS wins: no CAS race, unconditional, derruba o dono agente.

    Deliberately has no `owner_generation` predicate — a human takeover must
    never be blocked by a concurrent agent claim/transfer. It also sets
    `assigned_human_id` (the thread's pre-existing human-assignment column)
    and, when the thread was mid-funnel, `status = human_active`, so the
    inbox surfaces the takeover the same way any other human assignment
    does.
    """
    thread = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id)
    if human_already_holds(thread, human_user_id):
        return OwnershipResult(
            thread=thread, changed=False, already_owner=True, reason="already_human"
        )
    previous_owner = thread.owning_agent_key
    previous_area = thread.owning_area_key
    previous_generation = thread.owner_generation
    now = datetime.now(timezone.utc)

    values: dict[str, Any] = {
        "owning_agent_key": None,
        "owning_area_key": None,
        "owner_since": now,
        "owner_generation": OmnichannelThread.owner_generation + 1,
        "version": OmnichannelThread.version + 1,
    }
    if human_user_id is not None:
        values["assigned_human_id"] = human_user_id
    if thread.status.value not in ("closed", "blocked"):
        values["status"] = ThreadStatus.human_active
        if thread.status != ThreadStatus.human_active:
            # Guarda o estado anterior para `human_release` devolver a thread ao
            # ponto em que estava (coleta de documentos, por exemplo).
            metadata = dict(thread.thread_metadata or {})
            metadata["humanTakeover"] = {
                "previousStatus": thread.status.value,
                "at": now.isoformat(),
                "reason": reason,
            }
            values["thread_metadata"] = metadata

    statement = (
        update(OmnichannelThread)
        .where(
            OmnichannelThread.id == thread_id,
            OmnichannelThread.workspace_id == workspace_id,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
        .returning(OmnichannelThread.id)
    )
    taken_ids = list((await db.scalars(statement)).all())
    if not taken_ids:
        # Only reachable if the thread vanished between the read above and
        # this UPDATE (e.g. hard-deleted concurrently) — surface plainly.
        raise ConversationNotFoundError(
            f"Conversa {thread_id} não encontrada no workspace {workspace_id} ao assumir."
        )

    await db.flush()
    refreshed = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id, fresh=True)
    _audit(
        db,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_type=actor_type,
        actor_label=actor_label,
        event_type="conversation_ownership_human_takeover",
        summary=(
            f"{actor_label} assumiu manualmente a conversa "
            f"(dono agente anterior: {previous_owner or 'nenhum'})."
        ),
        before_data={
            "owningAgentKey": previous_owner,
            "owningAreaKey": previous_area,
            "ownerGeneration": previous_generation,
        },
        after_data={
            "owningAgentKey": None,
            "ownerGeneration": refreshed.owner_generation,
            "assignedHumanId": str(human_user_id) if human_user_id else None,
            "manualOverrideWins": True,
            "reason": reason,
        },
    )
    return OwnershipResult(thread=refreshed, changed=True, already_owner=False, reason=reason)


async def human_release(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread_id: UUID,
    actor_label: str,
    actor_type: str = "human",
    reason: str = "handoff_ai",
) -> OwnershipResult:
    """O humano devolve a conversa à automação: `human_active` volta ao status
    anterior ao takeover (fallback `open`). Mantém `assigned_human_id` (é o
    responsável do inbox; `KlmOperationsService.update_thread` zera ao ir para
    `open`, semântica própria da UI). Não mexe em posse de agente (já zerada
    pelo takeover). Sem CAS: decisão humana não perde corrida."""
    thread = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id)
    if thread.status != ThreadStatus.human_active:
        return OwnershipResult(
            thread=thread, changed=False, already_owner=False, reason="not_human_active"
        )
    metadata = dict(thread.thread_metadata or {})
    takeover = metadata.pop("humanTakeover", None) or {}
    previous = takeover.get("previousStatus") if isinstance(takeover, dict) else None
    try:
        restored = ThreadStatus(previous) if previous else ThreadStatus.open
    except ValueError:
        restored = ThreadStatus.open
    if restored in (ThreadStatus.human_active, ThreadStatus.closed, ThreadStatus.blocked):
        restored = ThreadStatus.open
    statement = (
        update(OmnichannelThread)
        .where(
            OmnichannelThread.id == thread_id,
            OmnichannelThread.workspace_id == workspace_id,
        )
        .values(
            status=restored,
            thread_metadata=metadata,
            version=OmnichannelThread.version + 1,
        )
        .execution_options(synchronize_session=False)
        .returning(OmnichannelThread.id)
    )
    released = list((await db.scalars(statement)).all())
    if not released:
        raise ConversationNotFoundError(
            f"Conversa {thread_id} não encontrada no workspace {workspace_id} ao liberar."
        )
    await db.flush()
    refreshed = await _load_thread(db, workspace_id=workspace_id, thread_id=thread_id, fresh=True)
    _audit(
        db,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actor_type=actor_type,
        actor_label=actor_label,
        event_type="conversation_ownership_human_release",
        summary=f"{actor_label} devolveu a conversa à automação ({restored.value}).",
        before_data={"status": ThreadStatus.human_active.value},
        after_data={
            "status": restored.value,
            "assignedHumanId": str(thread.assigned_human_id) if thread.assigned_human_id else None,
            "reason": reason,
        },
    )
    return OwnershipResult(thread=refreshed, changed=True, already_owner=False, reason=reason)


async def authorize_agent_response(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread: OmnichannelThread,
    agent_key: str,
    area_key: Optional[str],
    actor_label: str,
) -> OwnershipResult:
    """Single enforcement point consumed by `KlmAgentService._execute_one`.

    - Orphan thread (`owning_agent_key IS NULL`): `agent_key` claims it
      atomically (recepção pattern — first responder to an orphan
      conversation becomes its owner) and is allowed to respond.
    - Already owned by `agent_key`: allowed, no-op.
    - Owned by a different agent: raises `ConversationOwnershipDenied` — the
      denial is audited (`conversation_ownership_response_denied`) before
      the exception is raised, so callers never need to log it themselves
      and, critically, never generate a second response for the same
      inbound window.
    """
    # Releitura fresca: o objeto recebido pode ser anterior a um takeover humano.
    thread = await _load_thread(db, workspace_id=workspace_id, thread_id=thread.id, fresh=True)
    if thread.status == ThreadStatus.human_active:
        # S1: com humano ativo a IA não responde, mesmo em thread órfã. Recusa
        # determinística e auditada, nunca uma segunda resposta.
        _audit(
            db,
            workspace_id=workspace_id,
            thread_id=thread.id,
            actor_type="system",
            actor_label="Posse de conversa",
            event_type="conversation_ownership_response_denied",
            summary=f"Agente '{agent_key}' recusado: conversa em atendimento humano.",
            before_data={"requestedAgentKey": agent_key},
            after_data={
                "owningAgentKey": thread.owning_agent_key,
                "ownerGeneration": thread.owner_generation,
                "assignedHumanId": (
                    str(thread.assigned_human_id) if thread.assigned_human_id else None
                ),
                "reason": "human_active",
            },
        )
        raise ConversationOwnershipDenied(thread=thread, agent_key=agent_key, reason="human_active")

    if thread.owning_agent_key == agent_key:
        return OwnershipResult(
            thread=thread, changed=False, already_owner=True, reason="already_owner"
        )

    if thread.owning_agent_key is None:
        try:
            return await claim_conversation(
                db,
                workspace_id=workspace_id,
                thread_id=thread.id,
                agent_key=agent_key,
                area_key=area_key,
                actor_label=actor_label,
                reason="orphan_auto_claim",
            )
        except OwnershipConflictError as conflict:
            # Someone else won the race between our read and the CAS UPDATE.
            if conflict.thread.owning_agent_key == agent_key:
                return OwnershipResult(
                    thread=conflict.thread,
                    changed=False,
                    already_owner=True,
                    reason="already_owner",
                )
            _audit(
                db,
                workspace_id=workspace_id,
                thread_id=thread.id,
                actor_type="system",
                actor_label="Posse de conversa",
                event_type="conversation_ownership_response_denied",
                summary=(
                    f"Agente '{agent_key}' recusado: perdeu a corrida de claim "
                    f"para '{conflict.thread.owning_agent_key}'."
                ),
                before_data={"requestedAgentKey": agent_key},
                after_data={
                    "owningAgentKey": conflict.thread.owning_agent_key,
                    "ownerGeneration": conflict.thread.owner_generation,
                    "reason": "lost_claim_race",
                },
            )
            raise ConversationOwnershipDenied(
                thread=conflict.thread, agent_key=agent_key, reason="lost_claim_race"
            ) from conflict

    _audit(
        db,
        workspace_id=workspace_id,
        thread_id=thread.id,
        actor_type="system",
        actor_label="Posse de conversa",
        event_type="conversation_ownership_response_denied",
        summary=(
            f"Agente '{agent_key}' recusado: conversa pertence a "
            f"'{thread.owning_agent_key}'."
        ),
        before_data={"requestedAgentKey": agent_key},
        after_data={
            "owningAgentKey": thread.owning_agent_key,
            "ownerGeneration": thread.owner_generation,
            "reason": "not_owner",
        },
    )
    raise ConversationOwnershipDenied(thread=thread, agent_key=agent_key, reason="not_owner")


async def open_second_area_matter(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread: OmnichannelThread,
    area_key: str,
    demand_type: str,
    created_by_user_id: Optional[UUID] = None,
) -> Any:
    """Register a second area's demand as its own case (`Matter`).

    Import of `MatterService`/`MatterCreate` is local to dodge an import
    cycle (`matter_service` does not import this module, but several other
    services import both, and the omnichannel/matter model modules already
    sit close together in the dependency graph).

    Sequenciamento do handoff: this ONLY opens the CRM-side `Matter` in the
    new area — it never touches `thread.owning_*`. The current owner agent
    finishes the funnel it is already running; a separate, explicit
    `transfer_conversation` call is what would ever move ownership. This
    function asserts that invariant against a race by re-reading the thread
    after the matter is created and comparing the ownership triple.
    """
    if thread.client_id is None:
        raise ConversationOwnershipError(
            "Conversa sem client_id não pode abrir um segundo caso de área."
        )

    from app.schemas.matter import MatterCreate
    from app.services.matter_service import MatterService

    before_owner = (
        thread.owning_agent_key,
        thread.owning_area_key,
        thread.owner_generation,
    )

    matter = await MatterService(db).create(
        MatterCreate(
            client_id=thread.client_id,
            demand_type=demand_type,
            legal_area_key=area_key,
        ),
        workspace_id=workspace_id,
        created_by=created_by_user_id,
    )

    after_thread = await _load_thread(
        db, workspace_id=workspace_id, thread_id=thread.id, fresh=True
    )
    after_owner = (
        after_thread.owning_agent_key,
        after_thread.owning_area_key,
        after_thread.owner_generation,
    )
    if after_owner != before_owner:  # pragma: no cover - defensive invariant
        raise ConversationOwnershipError(
            "Abrir o caso da segunda área alterou a posse da conversa — "
            "isso nunca deveria acontecer."
        )

    _audit(
        db,
        workspace_id=workspace_id,
        thread_id=thread.id,
        actor_type="system",
        actor_label="Harmonização multi-área",
        event_type="conversation_second_area_matter_opened",
        summary=(
            f"Segunda demanda identificada na área '{area_key}'; caso novo "
            f"{matter.id} aberto sem trocar o dono da conversa "
            f"('{thread.owning_agent_key or 'nenhum'}')."
        ),
        before_data={"threadOwningAgentKey": thread.owning_agent_key},
        after_data={
            "newMatterId": str(matter.id),
            "areaKey": area_key,
            "threadOwningAgentKeyUnchanged": after_thread.owning_agent_key,
        },
    )
    return matter
