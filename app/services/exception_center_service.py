"""Central de Exceções (Plano Mestre §6/§3).

Generaliza a fila de aprovações (`app/services/approval_service.py`) para
TODOS os tipos de exceção humana nomeados no plano — não é um sistema
paralelo, é a mesma disciplina de human-in-the-loop aplicada a eventos que
hoje só logam ou falham silenciosamente (ver produtores em
`app/services/process_monitoring_service.py::ingest_publication` e
`app/workers/finance_tasks.py::_dispatch_dunning_log`).

Regras replicadas de `ApprovalService` (não duplicadas por acidente —
são a MESMA lição):
  * `open_exception` é idempotente por
    `(workspace_id, exception_kind, origin, dedupe_key)` enquanto
    `status='aberta'` — reingestão do mesmo evento pelo mesmo produtor
    nunca duplica (replay retorna a exceção existente);
  * `resolve`/`escalate` fazem CAS a partir de `aberta` dentro de uma
    transação (compare-and-swap via UPDATE ... WHERE status = 'aberta'),
    igual a `ApprovalService.resolve`;
  * o resolvedor/escalador é SEMPRE o parâmetro `actor_user_id` explícito
    (derivado do usuário autenticado pelo endpoint) — NUNCA um campo do
    payload do cliente (lição do Risco 2, ver
    app/api/v1/endpoints/approvals.py).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.advisory import acquire_transaction_advisory_lock
from app.models.approval import ApprovalPriority
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_exception import (
    ExceptionCenterStatus,
    ExceptionKind,
    LegalException,
)
from app.models.matter import Matter
from app.models.task import TaskPriority
from app.schemas.task import TaskCreate
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.task_service import TaskService

# Título humano por tipo de exceção (Plano Mestre §6/§3) — usado só para
# derivar o título da task materializada em open_exception(); a exceção em
# si guarda seu próprio `title` livre (produtor decide).
_TASK_TITLE_BY_EXCEPTION_KIND: dict[ExceptionKind, str] = {
    ExceptionKind.publicacao_sem_vinculo_confiavel: "Vincular publicação a um processo",
    ExceptionKind.homonimo: "Confirmar homônimo",
    ExceptionKind.prazo_ambiguo: "Esclarecer prazo ambíguo",
    ExceptionKind.conflito_calendario: "Resolver conflito de agenda",
    ExceptionKind.alteracao_tese: "Validar alteração de tese",
    ExceptionKind.precedente_nao_verificado: "Verificar precedente citado",
    ExceptionKind.documento_indispensavel_ausente: "Providenciar documento indispensável",
    ExceptionKind.procuracao_inconsistente: "Corrigir procuração inconsistente",
    ExceptionKind.custas_nao_comprovadas: "Comprovar custas processuais",
    ExceptionKind.versao_modificada_apos_aprovacao: "Revisar versão modificada após aprovação",
    ExceptionKind.acao_externa_nao_autorizada: "Autorizar ação externa pendente",
    ExceptionKind.conflito_sincronizacao_documental: (
        "Resolver conflito de sincronização documental"
    ),
}


class ExceptionCenterError(RuntimeError):
    """Base para erros de domínio da Central de Exceções."""


class ExceptionCenterNotFoundError(ExceptionCenterError):
    pass


class ExceptionCenterAlreadyResolvedError(ExceptionCenterError):
    def __init__(self, item: LegalException) -> None:
        self.item = item
        super().__init__(f"Exceção {item.id} já possui uma decisão terminal ({item.status}).")


class ExceptionCenterMatterMismatchError(ExceptionCenterError):
    """Levantado quando `matter_id` informado não pertence ao workspace."""


class ExceptionCenterService:
    def __init__(self, db: AsyncSession, workspace_id: UUID):
        if workspace_id is None:
            raise ValueError("workspace_id is required")
        self.db = db
        self.workspace_id = workspace_id

    # ── Abertura idempotente (produtores) ──────────────────────────────────

    async def open_exception(
        self,
        *,
        exception_kind: ExceptionKind,
        origin: str,
        dedupe_key: str,
        title: str,
        description: Optional[str] = None,
        priority: ApprovalPriority = ApprovalPriority.media,
        matter_id: Optional[UUID] = None,
        payload: Optional[dict] = None,
        opened_by: Optional[UUID] = None,
    ) -> LegalException:
        """Abre uma exceção ou retorna a já aberta para a mesma
        `(exception_kind, origin, dedupe_key)` — replay idempotente, o
        mesmo desenho de `ApprovalService.create`."""
        if matter_id is not None:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == matter_id,
                    Matter.workspace_id == self.workspace_id,
                )
            )
            if matter is None:
                raise ExceptionCenterMatterMismatchError(
                    f"Caso {matter_id} não encontrado neste workspace."
                )

        # Serializa aberturas concorrentes da MESMA intenção — o índice
        # parcial único é quem garante a correção mesmo sem isto (ver
        # fallback de IntegrityError abaixo), mas o lock evita retries.
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="exception-intent",
            value=f"{self.workspace_id}:{exception_kind.value}:{origin}:{dedupe_key}",
        )

        existing = await self._get_open_for_intent(
            exception_kind=exception_kind, origin=origin, dedupe_key=dedupe_key
        )
        if existing is not None:
            return existing

        item = LegalException(
            workspace_id=self.workspace_id,
            matter_id=matter_id,
            opened_by=opened_by,
            exception_kind=exception_kind,
            origin=origin,
            dedupe_key=dedupe_key,
            title=title,
            description=description,
            payload=payload,
            priority=priority,
            status=ExceptionCenterStatus.aberta,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(item)
                await self.db.flush()
        except IntegrityError:
            existing = await self._get_open_for_intent(
                exception_kind=exception_kind, origin=origin, dedupe_key=dedupe_key
            )
            if existing is None:
                raise
            return existing

        self.db.add(
            KlmAuditEvent(
                workspace_id=self.workspace_id,
                actor_user_id=None,
                actor_type="system",
                actor_label="Central de Exceções",
                event_type="legal_exception_opened",
                entity_type="legal_exception",
                entity_id=item.id,
                correlation_id=f"{origin}:{dedupe_key}",
                summary=f"Exceção aberta ({exception_kind.value}): {title}",
                before_data=None,
                after_data={
                    "exceptionKind": exception_kind.value,
                    "origin": origin,
                    "dedupeKey": dedupe_key,
                    "matterId": str(matter_id) if matter_id else None,
                },
            )
        )
        # Materialização como tarefa humana (decisão do dono): só na
        # abertura NOVA (nunca no replay acima, que já retornou antes de
        # chegar aqui) — é isso que dá idempotência de graça ("reabrir a
        # mesma exceção não cria segunda task"). Só quando há matter_id
        # (ver docstring da migração d7e9f1a3c5b6 para o motivo do limite).
        if matter_id is not None:
            await self._materialize_task_for_exception(item, matter)
        await self.db.flush()
        return await self._load(item.id)

    # ── Materialização como tarefa (decisão do dono) ────────────────────────

    async def _materialize_task_for_exception(
        self, item: LegalException, matter: Matter
    ) -> None:
        """Cria a Task correspondente a uma LegalException recém-aberta,
        no mesmo `tasks` do Kanban/lista de tarefas do caso — não uma fila
        paralela. Idempotente por construção: só é chamada no ramo de
        INSERT novo de `open_exception` (replay retorna antes de chegar
        aqui); o índice único `ux_tasks_source_exception_id` é a segunda
        linha de defesa contra duplicata caso este método seja chamado
        duas vezes por engano."""
        kind_title = _TASK_TITLE_BY_EXCEPTION_KIND.get(
            item.exception_kind, item.exception_kind.value.replace("_", " ").capitalize()
        )
        title = f"Exceção: {kind_title}"[:500]

        description_lines = [item.description or item.title]
        description_lines.append(f"Ver caso: /app/demandas/{matter.id}")
        description_lines.append(
            f"Central de Exceções: exceção {item.id} "
            f"({item.exception_kind.value}, origem {item.origin})"
        )
        description = "\n\n".join(description_lines)[:10000]

        task = await TaskService(self.db).create_automatic(
            matter.id,
            TaskCreate(
                title=title,
                description=description,
                priority=TaskPriority(item.priority.value),
                is_ai_generated=True,
                idempotency_key=f"legal_exception:{item.id}",
            ),
            self.workspace_id,
            source="exception_center.materialize",
            actor_user_id=item.opened_by,
            preferred_owner_id=matter.responsible_human_id,
            source_exception_id=item.id,
            correlation_id=f"{item.origin}:{item.dedupe_key}",
        )

        item.linked_task_id = task.id

    # ── Leitura ──────────────────────────────────────────────────────────

    async def list_exceptions(
        self,
        *,
        exception_kind: Optional[ExceptionKind] = None,
        status_filter: Optional[ExceptionCenterStatus] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[LegalException]:
        statement = (
            select(LegalException)
            .options(selectinload(LegalException.matter))
            .where(LegalException.workspace_id == self.workspace_id)
        )
        if exception_kind is not None:
            statement = statement.where(LegalException.exception_kind == exception_kind)
        if status_filter is not None:
            statement = statement.where(LegalException.status == status_filter)
        statement = (
            statement.order_by(LegalException.created_at.desc()).offset(skip).limit(limit)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, exception_id: UUID) -> Optional[LegalException]:
        return await self.db.scalar(
            select(LegalException)
            .options(selectinload(LegalException.matter))
            .where(
                LegalException.id == exception_id,
                LegalException.workspace_id == self.workspace_id,
            )
        )

    # ── Resolução / escalonamento (CAS a partir de 'aberta') ────────────────

    async def resolve(
        self,
        exception_id: UUID,
        *,
        resolved_by: UUID,
        resolution_reason: Optional[str] = None,
    ) -> LegalException:
        """Resolve — SEMPRE atribuída a `resolved_by` (derivado do
        usuário autenticado pelo endpoint, nunca do payload)."""
        return await self._transition(
            exception_id,
            target_status=ExceptionCenterStatus.resolvida,
            actor_user_id=resolved_by,
            resolution_reason=resolution_reason,
        )

    async def escalate(
        self,
        exception_id: UUID,
        *,
        escalated_by: UUID,
        escalated_to: Optional[UUID] = None,
        resolution_reason: Optional[str] = None,
    ) -> LegalException:
        """Escala — auditada com o autor real da escalação; `escalated_to`
        é só o destinatário sugerido, nunca substitui o autor auditado."""
        return await self._transition(
            exception_id,
            target_status=ExceptionCenterStatus.escalada,
            actor_user_id=escalated_by,
            resolution_reason=resolution_reason,
            escalated_to=escalated_to,
        )

    async def _transition(
        self,
        exception_id: UUID,
        *,
        target_status: ExceptionCenterStatus,
        actor_user_id: UUID,
        resolution_reason: Optional[str],
        escalated_to: Optional[UUID] = None,
    ) -> LegalException:
        if actor_user_id is None:
            raise ValueError("resolved_by/escalated_by é obrigatório e vem do autenticado.")
        # Falha cedo se o usuário não existir/estiver inativo — mesma
        # disciplina de auditoria de app/services/audit_actor_service.py.
        await resolve_human_actor_label(self.db, actor_user_id)

        item = await self.db.scalar(
            select(LegalException)
            .where(
                LegalException.id == exception_id,
                LegalException.workspace_id == self.workspace_id,
            )
            .with_for_update()
        )
        if item is None:
            raise ExceptionCenterNotFoundError(f"Exceção {exception_id} não encontrada.")
        if item.status != ExceptionCenterStatus.aberta:
            raise ExceptionCenterAlreadyResolvedError(item)

        decided_at = datetime.now(UTC)
        values: dict = {
            "status": target_status,
            "resolution_reason": resolution_reason,
            "updated_at": decided_at,
        }
        if target_status == ExceptionCenterStatus.resolvida:
            values["resolved_by"] = actor_user_id
            values["resolved_at"] = decided_at
        else:
            values["escalated_to"] = escalated_to
            values["escalated_at"] = decided_at

        cas_result = await self.db.execute(
            update(LegalException)
            .where(
                LegalException.id == item.id,
                LegalException.workspace_id == self.workspace_id,
                LegalException.status == ExceptionCenterStatus.aberta,
            )
            .values(**values)
            .returning(LegalException.id)
            .execution_options(synchronize_session=False)
        )
        if cas_result.scalar_one_or_none() is None:
            await self.db.refresh(item)
            raise ExceptionCenterAlreadyResolvedError(item)
        await self.db.refresh(item)

        self.db.add(
            KlmAuditEvent(
                workspace_id=self.workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=await resolve_human_actor_label(self.db, actor_user_id),
                event_type=f"legal_exception_{target_status.value}",
                entity_type="legal_exception",
                entity_id=item.id,
                correlation_id=f"{item.origin}:{item.dedupe_key}",
                summary=(
                    f"Exceção {target_status.value} ({item.exception_kind.value}): "
                    f"{item.title[:80]}"
                ),
                before_data={"status": "aberta"},
                after_data={
                    "status": target_status.value,
                    "reason": resolution_reason,
                    "escalatedTo": str(escalated_to) if escalated_to else None,
                },
            )
        )
        # Resolver a exceção conclui a task materializada (direção
        # principal da referência cruzada, decisão do dono) — idempotente
        # via TaskService.mark_done (não alterna, só avança para 'done').
        # Escalar NÃO conclui a task: escalação não é decisão terminal de
        # trabalho, só de responsável — a task segue aberta até alguém
        # resolver.
        if target_status == ExceptionCenterStatus.resolvida and item.linked_task_id is not None:
            await TaskService(self.db).mark_done(
                item.linked_task_id,
                workspace_id=self.workspace_id,
                actor_user_id=actor_user_id,
            )
        await self.db.flush()
        return await self._load(item.id)

    # ── Internos ─────────────────────────────────────────────────────────

    async def _get_open_for_intent(
        self,
        *,
        exception_kind: ExceptionKind,
        origin: str,
        dedupe_key: str,
    ) -> Optional[LegalException]:
        return await self.db.scalar(
            select(LegalException)
            .where(
                LegalException.workspace_id == self.workspace_id,
                LegalException.exception_kind == exception_kind,
                LegalException.origin == origin,
                LegalException.dedupe_key == dedupe_key,
                LegalException.status == ExceptionCenterStatus.aberta,
            )
            .with_for_update()
        )

    async def _load(self, exception_id: UUID) -> LegalException:
        item = await self.db.scalar(
            select(LegalException)
            .options(selectinload(LegalException.matter))
            .where(
                LegalException.id == exception_id,
                LegalException.workspace_id == self.workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        if item is None:
            raise RuntimeError("LegalException disappeared inside its transaction")
        return item

    @staticmethod
    def not_found_http() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exceção não encontrada.",
        )

    @staticmethod
    def already_resolved_http(item: LegalException) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "exception_already_resolved",
                "message": "A exceção já possui uma decisão terminal.",
                "exceptionId": str(item.id),
                "currentStatus": getattr(item.status, "value", str(item.status)),
            },
        )
