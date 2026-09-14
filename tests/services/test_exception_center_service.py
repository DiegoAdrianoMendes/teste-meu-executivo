"""Testes da Central de Exceções (Plano Mestre §6/§3).

Cobre a disciplina reaproveitada de ApprovalService: idempotência por
origem+chave, CAS a partir de 'aberta', resolvedor/escalador sempre
derivado de um usuário autenticado real (nunca aceito de fora) e
isolamento por workspace (RLS).
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.approval import ApprovalPriority
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind
from app.models.matter import (
    ClientClassification,
    KanbanColumnId,
    Matter,
    MicroStatus,
    PracticeArea,
    Priority,
    Urgency,
)
from app.models.client import Client
from app.models.task import Task, TaskStatus
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.exception_center_service import (
    ExceptionCenterAlreadyResolvedError,
    ExceptionCenterMatterMismatchError,
    ExceptionCenterNotFoundError,
    ExceptionCenterService,
)
from app.services.task_service import TaskService


async def _create_matter(db: AsyncSession, workspace_id) -> Matter:
    client = Client(workspace_id=workspace_id, name="Cliente Teste", cpf="000.000.000-00")
    db.add(client)
    await db.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type="Rescisão Indireta",
        practice_area=PracticeArea.trabalhista,
        column_id=KanbanColumnId.redacao_peca,
        micro_status=MicroStatus.minuta_em_elaboracao,
        priority=Priority.alta,
        urgency=Urgency.urgente,
        classification=ClientClassification.Premium,
    )
    db.add(matter)
    await db.flush()
    return matter


@pytest.mark.asyncio
async def test_open_exception_is_idempotent_by_kind_origin_and_dedupe_key(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    service = ExceptionCenterService(db_session, auth_context.workspace_id)

    kwargs = dict(
        exception_kind=ExceptionKind.homonimo,
        origin="test.producer",
        dedupe_key="lead-123",
        title="Possível homônimo detectado",
    )
    first = await service.open_exception(**kwargs)
    await db_session.flush()
    second = await service.open_exception(**kwargs)

    assert first.id == second.id
    items = await service.list_exceptions()
    assert len(items) == 1


@pytest.mark.asyncio
async def test_open_exception_rejects_matter_from_other_workspace(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    other_workspace = Workspace(
        name="Outro Escritório",
        slug=f"outro-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    foreign_matter = await _create_matter(db_session, other_workspace.id)

    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    with pytest.raises(ExceptionCenterMatterMismatchError):
        await service.open_exception(
            exception_kind=ExceptionKind.prazo_ambiguo,
            origin="test.producer",
            dedupe_key="foreign-matter",
            title="Prazo ambíguo",
            matter_id=foreign_matter.id,
        )


@pytest.mark.asyncio
async def test_resolve_sets_authenticated_actor_and_terminal_state(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.custas_nao_comprovadas,
        origin="test.producer",
        dedupe_key="custas-1",
        title="Custas não comprovadas",
    )

    resolved = await service.resolve(
        item.id, resolved_by=auth_context.user_id, resolution_reason="Comprovante anexado."
    )

    assert resolved.status == ExceptionCenterStatus.resolvida
    assert resolved.resolved_by == auth_context.user_id
    assert resolved.resolution_reason == "Comprovante anexado."
    assert resolved.resolved_at is not None


@pytest.mark.asyncio
async def test_resolve_is_cas_and_rejects_double_resolution(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.documento_indispensavel_ausente,
        origin="test.producer",
        dedupe_key="doc-1",
        title="Procuração ausente",
    )
    await service.resolve(item.id, resolved_by=auth_context.user_id)

    with pytest.raises(ExceptionCenterAlreadyResolvedError):
        await service.resolve(item.id, resolved_by=auth_context.user_id)


@pytest.mark.asyncio
async def test_resolve_unknown_exception_raises_not_found(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    with pytest.raises(ExceptionCenterNotFoundError):
        await service.resolve(uuid4(), resolved_by=auth_context.user_id)


@pytest.mark.asyncio
async def test_escalate_sets_escalated_fields_and_leaves_resolved_by_empty(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    escalation_target = User(
        name="Sócio Sênior",
        email=f"socio-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="SS",
        color="#0f172a",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(escalation_target)
    await db_session.flush()

    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.alteracao_tese,
        origin="test.producer",
        dedupe_key="tese-1",
        title="Alteração de tese detectada",
        priority=ApprovalPriority.alta,
    )

    escalated = await service.escalate(
        item.id,
        escalated_by=auth_context.user_id,
        escalated_to=escalation_target.id,
        resolution_reason="Precisa de decisão do sócio.",
    )

    assert escalated.status == ExceptionCenterStatus.escalada
    assert escalated.escalated_to == escalation_target.id
    assert escalated.escalated_at is not None
    assert escalated.resolved_by is None


@pytest.mark.asyncio
async def test_list_exceptions_filters_by_kind_and_status(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    open_item = await service.open_exception(
        exception_kind=ExceptionKind.conflito_calendario,
        origin="test.producer",
        dedupe_key="cal-1",
        title="Conflito de agenda",
    )
    other_kind = await service.open_exception(
        exception_kind=ExceptionKind.precedente_nao_verificado,
        origin="test.producer",
        dedupe_key="prec-1",
        title="Precedente não verificado",
    )
    await service.resolve(other_kind.id, resolved_by=auth_context.user_id)

    only_open = await service.list_exceptions(status_filter=ExceptionCenterStatus.aberta)
    assert {item.id for item in only_open} == {open_item.id}

    only_calendar = await service.list_exceptions(
        exception_kind=ExceptionKind.conflito_calendario
    )
    assert {item.id for item in only_calendar} == {open_item.id}


@pytest.mark.asyncio
async def test_exceptions_are_isolated_by_workspace(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    other_workspace = Workspace(
        name="Outro Escritório 2",
        slug=f"outro2-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()

    service_a = ExceptionCenterService(db_session, auth_context.workspace_id)
    await service_a.open_exception(
        exception_kind=ExceptionKind.versao_modificada_apos_aprovacao,
        origin="test.producer",
        dedupe_key="versao-1",
        title="Versão modificada após aprovação",
    )

    service_b = ExceptionCenterService(db_session, other_workspace.id)
    items_b = await service_b.list_exceptions()
    assert items_b == []


# ── Materialização como tarefa humana (decisão do dono) ─────────────────────


@pytest.mark.asyncio
async def test_open_exception_with_matter_materializes_a_task(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _create_matter(db_session, auth_context.workspace_id)
    service = ExceptionCenterService(db_session, auth_context.workspace_id)

    item = await service.open_exception(
        exception_kind=ExceptionKind.documento_indispensavel_ausente,
        origin="test.producer",
        dedupe_key="materializa-1",
        title="Procuração ausente no processo",
        matter_id=matter.id,
    )

    assert item.linked_task_id is not None
    task = await db_session.get(Task, item.linked_task_id)
    assert task is not None
    assert task.matter_id == matter.id
    assert task.source_exception_id == item.id
    assert task.status == TaskStatus.pending
    assert task.is_ai_generated is True
    assert "Exceção:" in task.title
    assert str(matter.id) in task.description
    assert str(item.id) in task.description


@pytest.mark.asyncio
async def test_reopening_the_same_exception_intent_never_creates_a_second_task(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _create_matter(db_session, auth_context.workspace_id)
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    kwargs = dict(
        exception_kind=ExceptionKind.prazo_ambiguo,
        origin="test.producer",
        dedupe_key="dedupe-task-1",
        title="Prazo ambíguo",
        matter_id=matter.id,
    )

    first = await service.open_exception(**kwargs)
    await db_session.flush()
    second = await service.open_exception(**kwargs)

    assert first.id == second.id
    assert first.linked_task_id == second.linked_task_id

    tasks = await db_session.execute(
        select(Task).where(Task.source_exception_id == first.id)
    )
    assert len(tasks.scalars().all()) == 1


@pytest.mark.asyncio
async def test_exception_without_matter_does_not_materialize_a_task(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """`publicacao_sem_vinculo_confiavel` nasce sem matter_id de propósito
    (é literalmente a exceção que existe porque não há vínculo confiável).
    Continua 100% visível na Central de Exceções; só não vira Task porque
    `tasks.matter_id` é obrigatório (ver docstring da migração
    d7e9f1a3c5b6)."""
    service = ExceptionCenterService(db_session, auth_context.workspace_id)

    item = await service.open_exception(
        exception_kind=ExceptionKind.publicacao_sem_vinculo_confiavel,
        origin="process_monitoring.ingest_publication",
        dedupe_key="djen:sem-matter-1",
        title="Publicação sem processo vinculado",
    )

    assert item.matter_id is None
    assert item.linked_task_id is None
    items = await service.list_exceptions()
    assert item.id in {row.id for row in items}


@pytest.mark.asyncio
async def test_resolving_exception_concludes_its_materialized_task(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _create_matter(db_session, auth_context.workspace_id)
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.custas_nao_comprovadas,
        origin="test.producer",
        dedupe_key="resolve-task-1",
        title="Custas não comprovadas",
        matter_id=matter.id,
    )
    task_id = item.linked_task_id
    assert task_id is not None

    await service.resolve(item.id, resolved_by=auth_context.user_id)

    task = await db_session.get(Task, task_id)
    assert task.status == TaskStatus.done


@pytest.mark.asyncio
async def test_escalating_exception_does_not_conclude_its_task(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    matter = await _create_matter(db_session, auth_context.workspace_id)
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.conflito_calendario,
        origin="test.producer",
        dedupe_key="escalate-task-1",
        title="Conflito de agenda",
        matter_id=matter.id,
    )
    task_id = item.linked_task_id
    assert task_id is not None

    await service.escalate(item.id, escalated_by=auth_context.user_id)

    task = await db_session.get(Task, task_id)
    assert task.status == TaskStatus.pending


@pytest.mark.asyncio
async def test_completing_the_task_resolves_its_source_exception(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """A outra ponta da referência cruzada: concluir a task materializada
    (via lista de tarefas, não via Central de Exceções) resolve a exceção
    de origem — implementado só no toggle interativo (destoggling nunca
    reabre, é documentado, não implementado)."""
    matter = await _create_matter(db_session, auth_context.workspace_id)
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.homonimo,
        origin="test.producer",
        dedupe_key="task-completes-exception-1",
        title="Possível homônimo",
        matter_id=matter.id,
    )
    task_id = item.linked_task_id
    assert task_id is not None

    tasks = TaskService(db_session)
    await tasks.toggle_status(
        task_id, workspace_id=auth_context.workspace_id, toggled_by=auth_context.user_id
    )

    resolved = await service.get_by_id(item.id)
    assert resolved.status == ExceptionCenterStatus.resolvida
    assert resolved.resolved_by == auth_context.user_id


@pytest.mark.asyncio
async def test_completing_task_of_already_resolved_exception_does_not_raise(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """Best-effort: se a exceção já foi resolvida por outra via (ex.: pela
    própria Central de Exceções), marcar a task como concluída (que já
    estará 'done' pela propagação direta) não deve levantar erro."""
    matter = await _create_matter(db_session, auth_context.workspace_id)
    service = ExceptionCenterService(db_session, auth_context.workspace_id)
    item = await service.open_exception(
        exception_kind=ExceptionKind.acao_externa_nao_autorizada,
        origin="test.producer",
        dedupe_key="task-already-resolved-1",
        title="Ação externa pendente de autorização",
        matter_id=matter.id,
    )
    task_id = item.linked_task_id
    await service.resolve(item.id, resolved_by=auth_context.user_id)

    task = await db_session.get(Task, task_id)
    assert task.status == TaskStatus.done

    tasks = TaskService(db_session)
    # Task já está 'done' — toggle alterna para 'pending' primeiro (não é
    # o cenário de conclusão), então volta a 'done' numa segunda chamada,
    # que é o cenário real desta asserção: propagar para uma exceção já
    # terminal não pode explodir.
    await tasks.toggle_status(
        task_id, workspace_id=auth_context.workspace_id, toggled_by=auth_context.user_id
    )
    result = await tasks.toggle_status(
        task_id, workspace_id=auth_context.workspace_id, toggled_by=auth_context.user_id
    )
    assert result.status == TaskStatus.done
