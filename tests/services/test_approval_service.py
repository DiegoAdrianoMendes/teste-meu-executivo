"""Focused transactional tests for the human approval workflow."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.post_commit import publish_committed_realtime_events
from app.models.agent_platform import AgentDeployment, DeploymentStatus
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.approval import (
    ApprovalDecision,
    ApprovalQueueItem,
    ApprovalStatus,
)
from app.models.client import Client
from app.models.klm_operations import LegalArtifactState, LegalArtifactVersion
from app.models.matter import (
    ClientClassification,
    KanbanColumnId,
    Matter,
    MicroStatus,
    PracticeArea,
    Priority,
    Urgency,
)
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.approval import ApprovalCreate, ApprovalResolveRequest
from app.services.approval_service import ApprovalService
from app.websockets.manager import ws_manager
from app.workers.agent_tasks import (
    AGENT_DISPATCH_LEASE_TIMEOUT,
    _recover_agent_runs_async,
)


async def _create_test_matter(db: AsyncSession) -> Matter:
    workspace = Workspace(
        name="Workspace Aprovação",
        slug=f"workspace-aprovacao-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()

    user = User(
        name="Aprovador Teste",
        email=f"aprovador-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="AT",
        color="#0f172a",
        is_active=True,
        is_internal_staff=False,
    )
    db.add(user)
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    await db.flush()

    client = Client(
        workspace_id=workspace.id,
        name="Cliente Teste",
        cpf="000.000.000-00",
    )
    db.add(client)
    await db.flush()

    matter = Matter(
        workspace_id=workspace.id,
        client_id=client.id,
        demand_type="Rescisão Indireta",
        practice_area=PracticeArea.trabalhista,
        column_id=KanbanColumnId.redacao_peca,
        micro_status=MicroStatus.minuta_em_elaboracao,
        priority=Priority.alta,
        urgency=Urgency.urgente,
        classification=ClientClassification.Premium,
        responsible_human_id=user.id,
    )
    db.add(matter)
    await db.flush()
    return matter


def _approval_data(
    matter: Matter,
    *,
    approval_type: str = "petition_review",
    title: str = "Revisão da Petição",
) -> ApprovalCreate:
    return ApprovalCreate(
        matter_id=matter.id,
        approval_type=approval_type,
        title=title,
        priority="alta",
        requested_by=matter.responsible_human_id,
    )


def test_resolve_schema_rejects_pending_decision() -> None:
    with pytest.raises(ValidationError):
        ApprovalResolveRequest(status="pending")


@pytest.mark.asyncio
async def test_service_requires_workspace_binding(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="workspace_id is required"):
        ApprovalService(db_session, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_service_is_fail_closed_to_bound_workspace(
    db_session: AsyncSession,
) -> None:
    own_matter = await _create_test_matter(db_session)
    other_matter = await _create_test_matter(db_session)
    own_service = ApprovalService(db_session, own_matter.workspace_id)
    other_service = ApprovalService(db_session, other_matter.workspace_id)

    own_item = await own_service.create(_approval_data(own_matter))
    other_item = await other_service.create(_approval_data(other_matter))

    assert [item.id for item in await own_service.get_pending()] == [own_item.id]
    assert [item.id for item in await own_service.get_all()] == [own_item.id]
    assert (await own_service.get_by_id(own_item.id)).id == own_item.id
    assert await own_service.get_by_id(other_item.id) is None

    with pytest.raises(RuntimeError, match="Approval disappeared"):
        await own_service._load_enriched(other_item.id)

    with pytest.raises(HTTPException) as create_error:
        await own_service.create(_approval_data(other_matter))
    assert create_error.value.status_code == 404

    with pytest.raises(HTTPException) as resolve_error:
        await own_service.resolve(
            other_item.id,
            ApprovalResolveRequest(status=ApprovalDecision.approved),
        )
    assert resolve_error.value.status_code == 404
    assert other_item.status == ApprovalStatus.pending


@pytest.mark.asyncio
async def test_create_approval_sets_pending_flag(db_session: AsyncSession) -> None:
    matter = await _create_test_matter(db_session)

    item = await ApprovalService(db_session, matter.workspace_id).create(
        _approval_data(matter)
    )

    assert item.id is not None
    assert item.status == ApprovalStatus.pending
    assert matter.needs_approval is True


@pytest.mark.asyncio
async def test_equivalent_duplicate_create_returns_existing_without_duplicate_timeline(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)
    payload = _approval_data(matter)

    first = await service.create(payload)
    second = await service.create(payload.model_copy(deep=True))

    assert second.id == first.id
    assert (
        await db_session.scalar(
            select(func.count(ApprovalQueueItem.id)).where(
                ApprovalQueueItem.matter_id == matter.id
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(TimelineEvent.id)).where(
                TimelineEvent.matter_id == matter.id,
                TimelineEvent.event_type == TimelineEventType.approval_requested,
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_divergent_duplicate_create_is_explicit_conflict_and_session_survives(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)
    await service.create(_approval_data(matter))

    with pytest.raises(HTTPException) as caught:
        await service.create(_approval_data(matter, title="Outra revisão"))

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "approval_pending_payload_conflict"
    assert (
        await db_session.scalar(
            select(func.count(ApprovalQueueItem.id)).where(
                ApprovalQueueItem.matter_id == matter.id
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_partial_unique_index_rejects_second_pending_row(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    await ApprovalService(db_session, matter.workspace_id).create(
        _approval_data(matter)
    )

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                ApprovalQueueItem(
                    matter_id=matter.id,
                    approval_type="petition_review",
                    intent_key="type:petition_review",
                    title="Duplicada",
                    priority="alta",
                    status=ApprovalStatus.pending,
                )
            )
            await db_session.flush()

    assert await db_session.scalar(select(func.count(ApprovalQueueItem.id))) == 1


@pytest.mark.asyncio
async def test_distinct_intents_of_same_type_can_coexist(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)

    first = await service.create(
        _approval_data(matter).model_copy(update={"intent_key": "petition:draft-v1"})
    )
    second = await service.create(
        _approval_data(matter, title="Revisão da petição v2").model_copy(
            update={"intent_key": "petition:draft-v2"}
        )
    )

    assert first.id != second.id
    assert (
        await db_session.scalar(
            select(func.count(ApprovalQueueItem.id)).where(
                ApprovalQueueItem.matter_id == matter.id,
                ApprovalQueueItem.approval_type == "petition_review",
                ApprovalQueueItem.status == ApprovalStatus.pending,
            )
        )
        == 2
    )


def test_approval_intent_key_is_trimmed_and_cannot_be_blank() -> None:
    matter_id = uuid4()
    payload = {
        "matter_id": matter_id,
        "approval_type": "petition_review",
        "title": "Revisão da Petição",
        "priority": "alta",
    }

    normalized = ApprovalCreate.model_validate(
        payload | {"intent_key": "  petition:draft-v1  "}
    )

    assert normalized.intent_key == "petition:draft-v1"
    with pytest.raises(ValueError, match="intent_key must not be blank"):
        ApprovalCreate.model_validate(payload | {"intent_key": "   "})


@pytest.mark.asyncio
async def test_approved_resolution_advances_when_no_pending_remains(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)
    item = await service.create(_approval_data(matter))

    resolved = await service.resolve(
        item.id,
        ApprovalResolveRequest(status=ApprovalDecision.approved),
    )

    assert resolved.status == ApprovalStatus.approved
    assert matter.needs_approval is False
    assert matter.micro_status == MicroStatus.checklist_final


@pytest.mark.asyncio
async def test_rejection_without_deployment_completes_without_dormant_agent_run(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)
    item = await service.create(_approval_data(matter))

    resolved = await service.resolve(
        item.id,
        ApprovalResolveRequest(
            status=ApprovalDecision.rejected,
            resolution_reason="Faltam argumentos sobre o nexo causal.",
            resolved_by=matter.responsible_human_id,
        ),
    )
    run = await db_session.scalar(
        select(AgentRun).where(
            AgentRun.workspace_id == matter.workspace_id,
            AgentRun.dedupe_key == f"approval:{item.id}:rejected",
        )
    )

    assert resolved.status == ApprovalStatus.rejected
    assert matter.micro_status == MicroStatus.ajustes_solicitados
    assert run is None


@pytest.mark.asyncio
async def test_reprocess_agent_run_uses_existing_equivalent_dedupe(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)
    item = await service.create(_approval_data(matter))
    reason = "Revisar os pedidos."
    existing = AgentRun(
        workspace_id=matter.workspace_id,
        matter_id=matter.id,
        triggered_by_user_id=matter.responsible_human_id,
        agent_name="klm_sdr",
        trigger_source="approval:changes_requested",
        thread_id=f"klm_sdr-{matter.id}",
        dedupe_key=f"approval:{item.id}:changes_requested",
        status=AgentRunStatus.queued,
        input_payload={
            "approval_id": str(item.id),
            "approval_status": "changes_requested",
            "approval_reason": reason,
            "reprocess_requested": True,
            "stage_key": "redacao_peca",
        },
        attempt_count=1,
    )
    db_session.add(existing)
    await db_session.flush()

    await service.resolve(
        item.id,
        ApprovalResolveRequest(
            status=ApprovalDecision.changes_requested,
            resolution_reason=reason,
            resolved_by=matter.responsible_human_id,
        ),
    )

    runs = list(
        (
            await db_session.scalars(
                select(AgentRun).where(
                    AgentRun.dedupe_key
                    == f"approval:{item.id}:changes_requested"
                )
            )
        ).all()
    )
    assert [run.id for run in runs] == [existing.id]
    assert existing.celery_task_id


@pytest.mark.asyncio
async def test_matter_flag_tracks_all_pending_approval_types(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)
    petition = await service.create(_approval_data(matter))
    contract = await service.create(
        _approval_data(
            matter,
            approval_type="contract_review",
            title="Revisão do contrato",
        )
    )

    await service.resolve(
        petition.id,
        ApprovalResolveRequest(status=ApprovalDecision.approved),
    )
    assert matter.needs_approval is True
    assert matter.micro_status == MicroStatus.revisao_interna

    await service.resolve(
        contract.id,
        ApprovalResolveRequest(status=ApprovalDecision.approved),
    )
    assert matter.needs_approval is False
    assert matter.micro_status == MicroStatus.checklist_final


@pytest.mark.asyncio
async def test_resolution_and_legal_artifact_state_change_together(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)
    item = await service.create(_approval_data(matter))
    artifact = LegalArtifactVersion(
        workspace_id=matter.workspace_id,
        matter_id=matter.id,
        artifact_key=uuid4(),
        version=1,
        kind="petition",
        title="Petição inicial",
        content={"body": "Minuta"},
        source_snapshot={"matterId": str(matter.id)},
        created_by_user_id=matter.responsible_human_id,
        evidence_sha256="",
    )
    db_session.add(artifact)
    await db_session.flush()
    artifact_state = LegalArtifactState(
        workspace_id=matter.workspace_id,
        artifact_version_id=artifact.id,
        matter_id=matter.id,
        status="review_pending",
        approval_item_id=item.id,
        approval_matter_id=matter.id,
    )
    db_session.add(artifact_state)
    await db_session.flush()

    await service.resolve(
        item.id,
        ApprovalResolveRequest(
            status=ApprovalDecision.changes_requested,
            resolution_reason="Ajustar fundamentos.",
            resolved_by=matter.responsible_human_id,
        ),
    )

    assert artifact_state.status == "changes_requested"
    assert item.status == ApprovalStatus.changes_requested


@pytest.mark.asyncio
async def test_second_resolution_is_conflict_without_duplicate_timeline(
    db_session: AsyncSession,
) -> None:
    matter = await _create_test_matter(db_session)
    service = ApprovalService(db_session, matter.workspace_id)
    item = await service.create(_approval_data(matter))
    await service.resolve(
        item.id,
        ApprovalResolveRequest(status=ApprovalDecision.approved),
    )

    with pytest.raises(HTTPException) as caught:
        await service.resolve(
            item.id,
            ApprovalResolveRequest(status=ApprovalDecision.rejected),
        )

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "approval_already_resolved"
    assert (
        await db_session.scalar(
            select(func.count(TimelineEvent.id)).where(
                TimelineEvent.matter_id == matter.id,
                TimelineEvent.event_type == TimelineEventType.approval_resolved,
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_broker_failure_leaves_agent_run_queued_and_periodically_recoverable(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    matter = await _create_test_matter(db_session)
    db_session.add(
        AgentDeployment(
            workspace_id=matter.workspace_id,
            agent_key="klm_sdr",
            version=1,
            status=DeploymentStatus.active,
            config={
                "definitionVersion": 1,
                "model": "test",
                "testedAt": "2026-08-03T00:00:00Z",
            },
            gates={"published": True},
            budget_policy={"monthlyTokenLimit": 10_000},
        )
    )
    await db_session.flush()
    monkeypatch.setattr(
        "app.services.agent_runtime_service.verify_deployment",
        AsyncMock(return_value={"testPassed": True}),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime_service.validate_deployment_ready",
        lambda config, gates: None,
    )
    service = ApprovalService(db_session, matter.workspace_id)
    item = await service.create(_approval_data(matter))
    await service.resolve(
        item.id,
        ApprovalResolveRequest(
            status=ApprovalDecision.rejected,
            resolution_reason="Reprocessar.",
            resolved_by=matter.responsible_human_id,
        ),
    )
    run = await db_session.scalar(
        select(AgentRun).where(AgentRun.dedupe_key == f"approval:{item.id}:rejected")
    )
    assert run is not None
    await db_session.commit()

    async def ignore_realtime(*args, **kwargs) -> None:
        return None

    def broker_down(*args, **kwargs) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(ws_manager, "broadcast_board_update", ignore_realtime)
    monkeypatch.setattr(
        "app.workers.agent_tasks.execute_agent_run.apply_async",
        broker_down,
    )
    await publish_committed_realtime_events(db_session)

    await db_session.refresh(run)
    assert run.status == AgentRunStatus.queued
    assert run.celery_task_id

    published: list[tuple[str, str, str]] = []
    recovery_sessions = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    recovery = await _recover_agent_runs_async(
        session_factory=recovery_sessions,
        now=datetime.now(UTC) + AGENT_DISPATCH_LEASE_TIMEOUT + timedelta(seconds=1),
        workspace_ids=[matter.workspace_id],
        publisher=lambda **payload: published.append(
            (
                str(payload["run_id"]),
                str(payload["workspace_id"]),
                payload["task_id"],
            )
        ),
    )

    assert recovery["published"] == 1
    assert published == [
        (str(run.id), str(matter.workspace_id), run.celery_task_id)
    ]
