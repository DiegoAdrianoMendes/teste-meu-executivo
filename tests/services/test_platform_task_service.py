from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.append_only import ImmutableRecordError
from app.models.growth_crm import GrowthProspect
from app.models.platform_incident import PlatformIncident
from app.models.platform_organization import PlatformTeam, PlatformTeamMembership
from app.models.platform_release import PlatformRelease
from app.models.platform_task import (
    PlatformTask,
    PlatformTaskEvent,
    PlatformTaskParticipant,
    PlatformTaskParticipantRole,
    PlatformTaskParticipantState,
    PlatformTaskReviewState,
    PlatformTaskStatus,
)
from app.models.platform_work_item import PlatformWorkItem
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.task import Task
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.platform_task import (
    PlatformTaskAssignmentsReplace,
    PlatformTaskCommand,
    PlatformTaskCommandRequest,
    PlatformTaskCommentCreate,
    PlatformTaskCreate,
    PlatformTaskParticipantStateUpdate,
    PlatformTaskParticipantWrite,
    PlatformTaskUpdate,
)
from app.services.platform_task_service import (
    PlatformTaskError,
    PlatformTaskMutationContext,
    PlatformTaskService,
    has_platform_task_permission,
    required_platform_task_command_permission,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (PlatformTaskCommand.start, "edit"),
        (PlatformTaskCommand.block, "edit"),
        (PlatformTaskCommand.submit_for_review, "edit"),
        (PlatformTaskCommand.approve, "review"),
        (PlatformTaskCommand.request_changes, "review"),
        (PlatformTaskCommand.reject, "review"),
        (PlatformTaskCommand.complete, "complete"),
        (PlatformTaskCommand.reopen, "complete"),
        (PlatformTaskCommand.cancel, "complete"),
    ],
)
def test_platform_task_commands_map_to_one_exact_permission(
    command: PlatformTaskCommand, expected: str
) -> None:
    assert required_platform_task_command_permission(command) == expected


async def _staff(
    db_session,
    name: str,
    *,
    grants: list[dict] | None = None,
    role_package: str = "support",
) -> User:
    user = User(
        name=name,
        email=f"{name.casefold().replace(' ', '-')}-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="".join(part[0] for part in name.split())[:4].upper(),
        color="#172554",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=False,
        platform_role_package=role_package,
        platform_access_active=True,
        platform_permission_grants=grants or [],
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _context(actor: User, label: str) -> PlatformTaskMutationContext:
    return PlatformTaskMutationContext.for_actor(
        actor, correlation_id=f"platform-task-test:{label}:{uuid4()}"
    )


def _workspace(label: str) -> Workspace:
    token = uuid4().hex[:12]
    return Workspace(
        name=f"{label} {token}",
        slug=f"platform-task-{label.casefold().replace(' ', '-')}-{token}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


def _ticket(*, workspace_id, reporter_user_id, label: str) -> SupportTicket:
    now = datetime.now(UTC)
    token = uuid4().hex[:12]
    return SupportTicket(
        public_number=f"SUP-{token.upper()}",
        workspace_id=workspace_id,
        reporter_user_id=reporter_user_id,
        ticket_type=SupportTicketType.problem,
        priority=SupportTicketPriority.normal,
        severity=SupportTicketSeverity.medium,
        queue=SupportTicketQueue.support,
        status=SupportTicketStatus.new,
        subject=f"Ticket {label}",
        description="Ticket usado para validar vínculo governado.",
        technical_context={},
        idempotency_key=f"platform-task-ticket-{token}",
        first_response_due_at=now + timedelta(hours=4),
        resolution_due_at=now + timedelta(days=1),
    )


@pytest.mark.parametrize("payload", [{"title": None}, {"priority": None}])
def test_platform_task_update_rejects_explicit_null_for_required_columns(payload) -> None:
    with pytest.raises(ValidationError):
        PlatformTaskUpdate(
            expected_version=1,
            reason="Campo obrigatório não pode ser apagado.",
            **payload,
        )


@pytest.mark.asyncio
async def test_raw_platform_task_grant_does_not_authorize_an_unknown_package(db_session) -> None:
    actor = await _staff(
        db_session,
        "Pacote inexistente",
        role_package="package_not_registered",
        grants=[{"module": "platform_tasks", "action": "view"}],
    )

    assert has_platform_task_permission(actor, "view") is False


@pytest.mark.asyncio
async def test_collaborative_task_review_flow_is_versioned_append_only_and_flush_only(
    db_session,
    platform_auth_context,
    monkeypatch,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    first = await _staff(db_session, "Executor Um")
    second = await _staff(db_session, "Executor Dois")
    observer = await _staff(db_session, "Observador")
    reviewer = await _staff(
        db_session,
        "Revisor",
        grants=[{"module": "platform_tasks", "action": "review"}],
    )
    commit = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit)
    now = datetime.now(UTC)
    service = PlatformTaskService(db_session)

    task = await service.create(
        data=PlatformTaskCreate(
            title="Preparar revisão operacional",
            description="Não copiar Task tenant; api_key=github_pat_12345678901234567890",
            owner_user_id=actor.id,
            owner_due_at=now + timedelta(hours=4),
            reviewer_user_id=reviewer.id,
            review_due_at=now + timedelta(hours=8),
            due_at=now + timedelta(days=1),
            participants=[
                PlatformTaskParticipantWrite(
                    user_id=first.id,
                    role=PlatformTaskParticipantRole.assignee,
                    personal_due_at=now + timedelta(hours=3),
                    required_for_completion=True,
                ),
                PlatformTaskParticipantWrite(
                    user_id=second.id,
                    role=PlatformTaskParticipantRole.assignee,
                    personal_due_at=now + timedelta(hours=5),
                    required_for_completion=True,
                ),
                PlatformTaskParticipantWrite(
                    user_id=observer.id,
                    role=PlatformTaskParticipantRole.observer,
                    required_for_completion=False,
                ),
            ],
            reason="Coordenação interna necessária para o lançamento.",
        ),
        context=_context(actor, "create"),
    )

    assert task.version == 1
    assert task.owner_user_id == actor.id
    assert task.reviewer_user_id == reviewer.id
    assert len(task.participants) == 3
    assert commit.await_count == 0
    created_event = await db_session.scalar(
        select(PlatformTaskEvent).where(
            PlatformTaskEvent.task_id == task.id,
            PlatformTaskEvent.event_type == "created",
        )
    )
    assert created_event is not None
    assert created_event.actor_label_snapshot == actor.name
    assert created_event.actor_role_snapshot == actor.platform_role_package
    assert "github_pat_" not in repr(created_event.after_data)
    assert "REDACTED" in repr(created_event.after_data)

    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.start,
        data=PlatformTaskCommandRequest(expected_version=1),
        context=_context(actor, "start"),
    )
    with pytest.raises(PlatformTaskError) as incomplete:
        await service.execute_command(
            task_id=task.id,
            command=PlatformTaskCommand.submit_for_review,
            data=PlatformTaskCommandRequest(expected_version=2),
            context=_context(actor, "early-review"),
        )
    assert incomplete.value.code == "PLATFORM-TASK-ASSIGNEES-INCOMPLETE"
    assert incomplete.value.status_code == 409

    for expected_version, user in ((2, first), (3, second)):
        task = await service.set_participant_state(
            task_id=task.id,
            user_id=user.id,
            data=PlatformTaskParticipantStateUpdate(
                expected_version=expected_version,
                state=PlatformTaskParticipantState.completed,
                reason="Executor confirmou a conclusão da sua parte.",
            ),
            context=_context(actor, f"complete-{user.id}"),
        )
    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.submit_for_review,
        data=PlatformTaskCommandRequest(expected_version=4),
        context=_context(actor, "submit"),
    )
    assert task.version == 5
    assert task.review_state == PlatformTaskReviewState.pending
    assert task.review_rounds[-1].task_version_reviewed == 5

    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.approve,
        data=PlatformTaskCommandRequest(expected_version=5, comment="Entrega aprovada."),
        context=_context(reviewer, "approve"),
    )
    assert task.status == PlatformTaskStatus.done
    assert task.review_state == PlatformTaskReviewState.approved
    assert task.version == 6
    assert commit.await_count == 0
    assert int(await db_session.scalar(select(func.count(PlatformTaskEvent.id))) or 0) == 6
    assert int(await db_session.scalar(select(func.count(Task.id))) or 0) == 0
    assert int(await db_session.scalar(select(func.count(PlatformWorkItem.id))) or 0) == 0


@pytest.mark.asyncio
async def test_task_roles_restrict_execution_review_and_observer_to_their_responsibilities(
    db_session,
    platform_auth_context,
) -> None:
    root = await db_session.get(User, platform_auth_context.user_id)
    reviewer = await _staff(
        db_session,
        "Revisor segregado",
        grants=[
            {"module": "platform_tasks", "action": "edit"},
            {"module": "platform_tasks", "action": "review"},
        ],
    )
    assignee = await _staff(db_session, "Executor autorizado")
    observer = await _staff(db_session, "Observador leitura")
    service = PlatformTaskService(db_session)
    task = await service.create(
        data=PlatformTaskCreate(
            title="Papéis operacionais segregados",
            owner_user_id=root.id,
            reviewer_user_id=reviewer.id,
            review_required=True,
            participants=[
                PlatformTaskParticipantWrite(
                    user_id=assignee.id,
                    role=PlatformTaskParticipantRole.assignee,
                    required_for_completion=False,
                ),
                PlatformTaskParticipantWrite(
                    user_id=observer.id,
                    role=PlatformTaskParticipantRole.observer,
                    required_for_completion=False,
                ),
            ],
            reason="Comprovar segregação entre entrega e decisão.",
        ),
        context=_context(root, "role-create"),
    )

    for actor in (reviewer, observer):
        with pytest.raises(PlatformTaskError) as denied:
            await service.execute_command(
                task_id=task.id,
                command=PlatformTaskCommand.start,
                data=PlatformTaskCommandRequest(expected_version=task.version),
                context=_context(actor, f"role-start-denied-{actor.id}"),
            )
        assert denied.value.code == "PLATFORM-TASK-EXECUTOR-ONLY"
        assert denied.value.status_code == 403

    with pytest.raises(PlatformTaskError) as observer_comment:
        await service.add_comment(
            task_id=task.id,
            data=PlatformTaskCommentCreate(
                expected_version=task.version,
                comment="Observador não pode comentar.",
            ),
            context=_context(observer, "observer-comment"),
        )
    assert observer_comment.value.code == "PLATFORM-TASK-COMMENT-DENIED"

    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.start,
        data=PlatformTaskCommandRequest(expected_version=task.version),
        context=_context(assignee, "assignee-start"),
    )

    with pytest.raises(PlatformTaskError) as assignee_cancel:
        await service.execute_command(
            task_id=task.id,
            command=PlatformTaskCommand.cancel,
            data=PlatformTaskCommandRequest(
                expected_version=task.version,
                reason="Executor tentou cancelar a tarefa do responsável.",
            ),
            context=_context(assignee, "assignee-cancel"),
        )
    assert assignee_cancel.value.code == "PLATFORM-TASK-OWNER-MANAGER-ONLY"

    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.submit_for_review,
        data=PlatformTaskCommandRequest(expected_version=task.version),
        context=_context(assignee, "assignee-submit"),
    )
    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.approve,
        data=PlatformTaskCommandRequest(
            expected_version=task.version,
            comment="Reviewer aprovou a entrega de outro funcionário.",
        ),
        context=_context(reviewer, "reviewer-approve"),
    )
    assert task.status == PlatformTaskStatus.done


@pytest.mark.asyncio
async def test_team_manager_can_cancel_and_reopen_a_managed_members_task(
    db_session,
    platform_auth_context,
) -> None:
    root = await db_session.get(User, platform_auth_context.user_id)
    manager = await _staff(
        db_session,
        "Gestor de equipe",
        role_package="commercial_manager",
    )
    owner = await _staff(db_session, "Pessoa gerenciada")
    now = datetime.now(UTC)
    team = PlatformTeam(
        name="Equipe gerenciada",
        slug=f"managed-team-{uuid4().hex[:12]}",
        manager_user_id=manager.id,
        active=True,
        version=1,
    )
    db_session.add(team)
    await db_session.flush()
    db_session.add(
        PlatformTeamMembership(
            team_id=team.id,
            user_id=owner.id,
            is_primary=True,
            active=True,
            starts_at=now - timedelta(minutes=1),
            version=1,
        )
    )
    await db_session.flush()

    service = PlatformTaskService(db_session)
    task = await service.create(
        data=PlatformTaskCreate(
            title="Tarefa sob gestão da equipe",
            owner_user_id=owner.id,
            reason="Comprovar autoridade operacional do gestor vinculado.",
        ),
        context=_context(root, "manager-create"),
    )
    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.cancel,
        data=PlatformTaskCommandRequest(
            expected_version=task.version,
            reason="Gestor cancelou tarefa sob sua responsabilidade.",
        ),
        context=_context(manager, "manager-cancel"),
    )
    assert task.status == PlatformTaskStatus.cancelled

    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.reopen,
        data=PlatformTaskCommandRequest(
            expected_version=task.version,
            reason="Gestor reabriu tarefa após revisão operacional.",
        ),
        context=_context(manager, "manager-reopen"),
    )
    assert task.status == PlatformTaskStatus.pending


@pytest.mark.asyncio
async def test_managing_only_observer_or_reviewer_does_not_grant_task_mutation_authority(
    db_session,
    platform_auth_context,
) -> None:
    root = await db_session.get(User, platform_auth_context.user_id)
    manager = await _staff(
        db_session,
        "Gestor sem owner gerenciado",
        role_package="commercial_manager",
    )
    owner = await _staff(db_session, "Owner fora da equipe")
    reviewer = await _staff(db_session, "Reviewer da equipe")
    observer = await _staff(db_session, "Observador da equipe")
    now = datetime.now(UTC)
    team = PlatformTeam(
        name="Equipe sem autoridade sobre owner",
        slug=f"observer-only-team-{uuid4().hex[:12]}",
        manager_user_id=manager.id,
        active=True,
        version=1,
    )
    db_session.add(team)
    await db_session.flush()
    db_session.add_all(
        [
            PlatformTeamMembership(
                team_id=team.id,
                user_id=user_id,
                is_primary=index == 0,
                active=True,
                starts_at=now - timedelta(minutes=1),
                version=1,
            )
            for index, user_id in enumerate((reviewer.id, observer.id))
        ]
    )
    await db_session.flush()
    service = PlatformTaskService(db_session)
    task = await service.create(
        data=PlatformTaskCreate(
            title="Tarefa de owner fora da equipe",
            owner_user_id=owner.id,
            reviewer_user_id=reviewer.id,
            participants=[
                PlatformTaskParticipantWrite(
                    user_id=observer.id,
                    role=PlatformTaskParticipantRole.observer,
                    required_for_completion=False,
                )
            ],
            reason="Comprovar que participante gerenciado não transfere autoridade.",
        ),
        context=_context(root, "observer-manager-create"),
    )

    with pytest.raises(PlatformTaskError) as edit_denied:
        await service.update(
            task_id=task.id,
            data=PlatformTaskUpdate(
                expected_version=task.version,
                title="Alteração indevida pelo gestor",
                reason="Gestor tentou editar tarefa de owner fora da equipe.",
            ),
            context=_context(manager, "observer-manager-edit"),
        )
    assert edit_denied.value.code == "PLATFORM-TASK-OWNER-MANAGER-ONLY"

    with pytest.raises(PlatformTaskError) as assign_denied:
        await service.replace_assignments(
            task_id=task.id,
            data=PlatformTaskAssignmentsReplace(
                expected_version=task.version,
                owner_user_id=owner.id,
                participants=[],
                reason="Gestor tentou redistribuir tarefa de owner externo.",
            ),
            context=_context(manager, "observer-manager-assign"),
        )
    assert assign_denied.value.code == "PLATFORM-TASK-OWNER-MANAGER-ONLY"

    with pytest.raises(PlatformTaskError) as cancel_denied:
        await service.execute_command(
            task_id=task.id,
            command=PlatformTaskCommand.cancel,
            data=PlatformTaskCommandRequest(
                expected_version=task.version,
                reason="Gestor tentou cancelar tarefa de owner fora da equipe.",
            ),
            context=_context(manager, "observer-manager-cancel"),
        )
    assert cancel_denied.value.code == "PLATFORM-TASK-OWNER-MANAGER-ONLY"
    assert PlatformTaskCommand.cancel not in await service.allowed_commands(
        actor=manager,
        task=task,
    )


@pytest.mark.asyncio
async def test_platform_root_cannot_decide_own_review_submission(
    db_session,
    platform_auth_context,
) -> None:
    root = await db_session.get(User, platform_auth_context.user_id)
    owner = await _staff(db_session, "Owner root")
    nominal_reviewer = await _staff(db_session, "Reviewer nominal root")
    service = PlatformTaskService(db_session)
    task = await service.create(
        data=PlatformTaskCreate(
            title="Exceção explícita de revisão root",
            owner_user_id=owner.id,
            reviewer_user_id=nominal_reviewer.id,
            review_required=True,
            reason="Comprovar a exceção exclusiva do Super Admin.",
        ),
        context=_context(root, "root-self-review-create"),
    )
    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.start,
        data=PlatformTaskCommandRequest(expected_version=task.version),
        context=_context(root, "root-self-review-start"),
    )
    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.submit_for_review,
        data=PlatformTaskCommandRequest(expected_version=task.version),
        context=_context(root, "root-self-review-submit"),
    )
    with pytest.raises(PlatformTaskError) as denied:
        await service.execute_command(
            task_id=task.id,
            command=PlatformTaskCommand.approve,
            data=PlatformTaskCommandRequest(
                expected_version=task.version,
                reason="Super Admin tentou decidir a própria submissão.",
                comment="A segregação de revisão também vale para root.",
            ),
            context=_context(root, "root-self-review-approve"),
        )

    assert denied.value.code == "PLATFORM-TASK-SELF-REVIEW-DENIED"
    assert denied.value.status_code == 403
    persisted = await service.get(actor=root, task_id=task.id)
    assert persisted.review_state == PlatformTaskReviewState.pending
    assert persisted.status == PlatformTaskStatus.in_progress


@pytest.mark.asyncio
async def test_platform_admin_cannot_override_the_assigned_reviewer(
    db_session,
    platform_auth_context,
) -> None:
    root = await db_session.get(User, platform_auth_context.user_id)
    owner = await _staff(db_session, "Owner review segregada")
    reviewer = await _staff(
        db_session,
        "Reviewer atribuido",
        grants=[{"module": "platform_tasks", "action": "review"}],
    )
    platform_admin = await _staff(
        db_session,
        "Administrador operacional",
        grants=[{"module": "platform_tasks", "action": "review"}],
    )
    platform_admin.platform_role_package = "platform_admin"
    platform_admin.is_platform_admin = True
    await db_session.flush()

    service = PlatformTaskService(db_session)
    task = await service.create(
        data=PlatformTaskCreate(
            title="Revisão exclusiva do responsável atribuído",
            owner_user_id=owner.id,
            reviewer_user_id=reviewer.id,
            review_required=True,
            reason="Comprovar segregação para o administrador operacional.",
        ),
        context=_context(root, "platform-admin-review-create"),
    )
    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.start,
        data=PlatformTaskCommandRequest(expected_version=task.version),
        context=_context(owner, "platform-admin-review-start"),
    )
    task = await service.execute_command(
        task_id=task.id,
        command=PlatformTaskCommand.submit_for_review,
        data=PlatformTaskCommandRequest(expected_version=task.version),
        context=_context(owner, "platform-admin-review-submit"),
    )

    with pytest.raises(PlatformTaskError) as denied:
        await service.execute_command(
            task_id=task.id,
            command=PlatformTaskCommand.approve,
            data=PlatformTaskCommandRequest(
                expected_version=task.version,
                reason="Administrador tentou substituir o reviewer atribuído.",
            ),
            context=_context(platform_admin, "platform-admin-review-denied"),
        )

    assert denied.value.code == "PLATFORM-TASK-REVIEWER-ONLY"
    assert denied.value.status_code == 403
    persisted = await service.get(actor=root, task_id=task.id)
    assert persisted.review_state == PlatformTaskReviewState.pending
    assert persisted.status == PlatformTaskStatus.in_progress
    assert persisted.version == task.version


@pytest.mark.asyncio
async def test_platform_task_rejects_stale_versions_customer_participants_and_cross_roles(
    db_session,
    platform_auth_context,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    customer = User(
        name="Cliente",
        email=f"cliente-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="CL",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        platform_access_active=False,
    )
    db_session.add(customer)
    await db_session.flush()
    service = PlatformTaskService(db_session)

    with pytest.raises(PlatformTaskError) as invalid_staff:
        await service.create(
            data=PlatformTaskCreate(
                title="Tarefa inválida",
                owner_user_id=actor.id,
                participants=[
                    PlatformTaskParticipantWrite(
                        user_id=customer.id,
                        role=PlatformTaskParticipantRole.assignee,
                    )
                ],
                reason="Participante precisa ser recusado pelo domínio.",
            ),
            context=_context(actor, "invalid-customer"),
        )
    assert invalid_staff.value.code == "PLATFORM-TASK-STAFF-INVALID"

    with pytest.raises(ValidationError):
        PlatformTaskCreate(
            title="Papéis incompatíveis",
            owner_user_id=actor.id,
            reviewer_user_id=customer.id,
            participants=[
                PlatformTaskParticipantWrite(
                    user_id=customer.id,
                    role=PlatformTaskParticipantRole.assignee,
                )
            ],
            reason="Reviewer não pode ser também executor.",
        )

    task = await service.create(
        data=PlatformTaskCreate(
            title="Controle otimista",
            owner_user_id=actor.id,
            reason="Comprovar rejeição de versão obsoleta.",
        ),
        context=_context(actor, "optimistic-create"),
    )
    with pytest.raises(PlatformTaskError) as stale:
        await service.execute_command(
            task_id=task.id,
            command=PlatformTaskCommand.start,
            data=PlatformTaskCommandRequest(expected_version=99),
            context=_context(actor, "stale"),
        )
    assert stale.value.code == "PLATFORM-TASK-VERSION-CONFLICT"
    assert stale.value.status_code == 409
    persisted = await db_session.get(PlatformTask, task.id)
    assert persisted.status == PlatformTaskStatus.pending
    assert persisted.version == 1


@pytest.mark.asyncio
async def test_lead_link_derives_workspace_and_rejects_ambiguous_or_cross_workspace_links(
    db_session,
    platform_auth_context,
) -> None:
    root = await db_session.get(User, platform_auth_context.user_id)
    workspace_a = _workspace("Conta A")
    workspace_b = _workspace("Conta B")
    db_session.add_all((workspace_a, workspace_b))
    await db_session.flush()
    converted = GrowthProspect(
        nome_escritorio="Lead convertido",
        valor_estimado_cents=0,
        workspace_id=workspace_a.id,
        metadata_json={},
    )
    unconverted = GrowthProspect(
        nome_escritorio="Lead ainda aberto",
        valor_estimado_cents=0,
        metadata_json={},
    )
    db_session.add_all((converted, unconverted))
    await db_session.flush()
    service = PlatformTaskService(db_session)

    derived = await service.create(
        data=PlatformTaskCreate(
            title="Acompanhar lead convertido",
            owner_user_id=root.id,
            linked_lead_id=converted.id,
            reason="Vincular a conta canônica derivada da conversão.",
        ),
        context=_context(root, "lead-derived-workspace"),
    )
    assert derived.workspace_id == workspace_a.id
    assert derived.linked_lead_id == converted.id

    with pytest.raises(PlatformTaskError) as cross_workspace:
        await service.create(
            data=PlatformTaskCreate(
                title="Vínculo cruzado inválido",
                owner_user_id=root.id,
                workspace_id=workspace_b.id,
                linked_lead_id=converted.id,
                reason="Comprovar bloqueio entre contas diferentes.",
            ),
            context=_context(root, "lead-cross-workspace"),
        )
    assert cross_workspace.value.code == "PLATFORM-TASK-LINK-WORKSPACE-MISMATCH"

    with pytest.raises(PlatformTaskError) as ambiguous:
        await service.create(
            data=PlatformTaskCreate(
                title="Lead não convertido com conta",
                owner_user_id=root.id,
                workspace_id=workspace_a.id,
                linked_lead_id=unconverted.id,
                reason="Comprovar que lead aberto não assume conta alheia.",
            ),
            context=_context(root, "lead-unconverted-workspace"),
        )
    assert ambiguous.value.code == "PLATFORM-TASK-LINK-WORKSPACE-MISMATCH"


@pytest.mark.asyncio
async def test_ticket_link_requires_independent_support_view_permission(
    db_session,
    platform_auth_context,
) -> None:
    root = await db_session.get(User, platform_auth_context.user_id)
    actor = await _staff(
        db_session,
        "SDR sem suporte",
        role_package="sales_sdr",
    )
    workspace = _workspace("Conta com ticket")
    db_session.add(workspace)
    await db_session.flush()
    ticket = _ticket(
        workspace_id=workspace.id,
        reporter_user_id=root.id,
        label="permissão independente",
    )
    db_session.add(ticket)
    await db_session.flush()

    with pytest.raises(PlatformTaskError) as denied:
        await PlatformTaskService(db_session).create(
            data=PlatformTaskCreate(
                title="Tentativa de vincular ticket",
                owner_user_id=actor.id,
                linked_ticket_id=ticket.id,
                reason="Comprovar gate separado de leitura do suporte.",
            ),
            context=_context(actor, "ticket-support-denied"),
        )
    assert denied.value.code == "PLATFORM-TASK-TICKET-PERMISSION-DENIED"
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_relation_ids_require_the_same_domain_permissions_as_the_picker(
    db_session,
    platform_auth_context,
) -> None:
    root = await db_session.get(User, platform_auth_context.user_id)
    workspace = _workspace("Conta protegida")
    lead = GrowthProspect(
        nome_escritorio="Lead protegido",
        valor_estimado_cents=0,
        metadata_json={},
    )
    incident = PlatformIncident(
        title="Incidente protegido",
        summary="Somente metadados operacionais.",
        severity="SEV2",
        status="investigating",
        components=[],
        updates=[],
        created_by_user_id=root.id,
    )
    release = PlatformRelease(
        version=f"v-{uuid4().hex[:8]}",
        environment="staging",
        status="planned",
        source="test",
        created_by_user_id=root.id,
        revision=1,
    )
    db_session.add_all((workspace, lead, incident, release))
    await db_session.flush()
    no_customers = await _staff(
        db_session,
        "Marketing sem clientes",
        role_package="marketing_content",
    )
    no_sales_or_releases = await _staff(
        db_session,
        "Suporte sem vendas e releases",
        role_package="support",
    )
    no_incidents = await _staff(
        db_session,
        "SDR sem incidentes",
        role_package="sales_sdr",
    )
    service = PlatformTaskService(db_session)

    cases = (
        (
            no_customers,
            {"workspace_id": workspace.id},
            "PLATFORM-TASK-WORKSPACE-PERMISSION-DENIED",
        ),
        (
            no_sales_or_releases,
            {"linked_lead_id": lead.id},
            "PLATFORM-TASK-LEAD-PERMISSION-DENIED",
        ),
        (
            no_incidents,
            {"linked_incident_id": incident.id},
            "PLATFORM-TASK-INCIDENT-PERMISSION-DENIED",
        ),
        (
            no_sales_or_releases,
            {"linked_release_id": release.id},
            "PLATFORM-TASK-RELEASE-PERMISSION-DENIED",
        ),
    )
    for index, (actor, relation, expected_code) in enumerate(cases):
        with pytest.raises(PlatformTaskError) as denied:
            await service.create(
                data=PlatformTaskCreate(
                    title=f"Vínculo direto proibido {index}",
                    owner_user_id=actor.id,
                    reason="Comprovar que UUID direto não contorna o RBAC.",
                    **relation,
                ),
                context=_context(actor, f"direct-link-denied-{index}"),
            )
        assert denied.value.code == expected_code
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_assignment_replacement_is_logical_and_history_cannot_be_rewritten(
    db_session,
    platform_auth_context,
) -> None:
    actor = await db_session.get(User, platform_auth_context.user_id)
    previous = await _staff(db_session, "Executor Anterior")
    replacement = await _staff(db_session, "Executor Substituto")
    reviewer = await _staff(db_session, "Reviewer Novo")
    service = PlatformTaskService(db_session)
    task = await service.create(
        data=PlatformTaskCreate(
            title="Redistribuir atividade",
            owner_user_id=actor.id,
            participants=[
                PlatformTaskParticipantWrite(
                    user_id=previous.id,
                    role=PlatformTaskParticipantRole.assignee,
                )
            ],
            reason="Distribuição inicial para a equipe interna.",
        ),
        context=_context(actor, "replace-create"),
    )
    previous_participant_id = task.participants[0].id

    task = await service.replace_assignments(
        task_id=task.id,
        data=PlatformTaskAssignmentsReplace(
            expected_version=1,
            owner_user_id=actor.id,
            reviewer_user_id=reviewer.id,
            participants=[
                PlatformTaskParticipantWrite(
                    user_id=replacement.id,
                    role=PlatformTaskParticipantRole.assignee,
                )
            ],
            reason="Redistribuição aprovada por mudança de disponibilidade.",
        ),
        context=_context(actor, "replace"),
    )
    assert task.version == 2
    assert [item.user_id for item in task.participants] == [replacement.id]
    previous_row = await db_session.get(PlatformTaskParticipant, previous_participant_id)
    assert previous_row is not None
    assert previous_row.active is False
    assert previous_row.state == PlatformTaskParticipantState.removed
    assert previous_row.label_snapshot == "Executor Anterior"

    events = list(
        (
            await db_session.scalars(
                select(PlatformTaskEvent)
                .where(PlatformTaskEvent.task_id == task.id)
                .order_by(PlatformTaskEvent.occurred_at, PlatformTaskEvent.id)
            )
        ).all()
    )
    assert len(events) == 2
    assert events[-1].changed_paths
    events[-1].reason = "Tentativa de reescrever evidência"
    with pytest.raises(ImmutableRecordError):
        await db_session.flush()
