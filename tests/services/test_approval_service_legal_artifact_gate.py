"""
Gate de aprovação de artefato jurídico (Onda 6, §9/§3 item 20): aprovar um
`legal_artifact:*` com citação não verificada ou achado crítico de revisão
aberto é rejeitado ANTES da transição de status em ApprovalService.resolve.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalDecision, ApprovalStatus
from app.models.client import Client
from app.models.klm_operations import LegalArtifactState, LegalArtifactVersion
from app.models.legal_artifact_verification import (
    LegalCitationVerification,
    LegalReviewFinding,
)
from app.models.legal_demand import LegalDemand, LegalDemandNature, LegalDemandPhase
from app.models.matter import KanbanColumnId, Matter, MicroStatus, PracticeArea, Priority, Urgency
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.approval import ApprovalCreate, ApprovalResolveRequest
from app.services.approval_service import ApprovalService
from app.services.citation_verification_service import extract_citations


async def _create_test_matter(db: AsyncSession) -> Matter:
    workspace = Workspace(
        name="Workspace Gate Jurídico",
        slug=f"workspace-gate-juridico-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()

    user = User(
        name="Aprovador Gate",
        email=f"aprovador-gate-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="AG",
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

    client = Client(workspace_id=workspace.id, name="Cliente Gate", cpf="000.000.000-00")
    db.add(client)
    await db.flush()

    matter = Matter(
        workspace_id=workspace.id,
        client_id=client.id,
        demand_type="Petição inicial",
        practice_area=PracticeArea.civel,
        column_id=KanbanColumnId.redacao_peca,
        micro_status=MicroStatus.minuta_em_elaboracao,
        priority=Priority.alta,
        urgency=Urgency.urgente,
        responsible_human_id=user.id,
    )
    db.add(matter)
    await db.flush()
    return matter


async def _create_artifact_pending_approval(
    db: AsyncSession, matter: Matter, *, content: dict
) -> tuple:
    service = ApprovalService(db, matter.workspace_id)
    item = await service.create(
        ApprovalCreate(
            matter_id=matter.id,
            approval_type="legal_artifact:petition",
            title="Revisar minuta",
            priority="alta",
            requested_by=matter.responsible_human_id,
        )
    )
    artifact = LegalArtifactVersion(
        workspace_id=matter.workspace_id,
        matter_id=matter.id,
        artifact_key=uuid4(),
        version=1,
        kind="petition",
        title="Petição inicial",
        content=content,
        source_snapshot={"matterId": str(matter.id)},
        created_by_user_id=matter.responsible_human_id,
        evidence_sha256="",
    )
    db.add(artifact)
    await db.flush()
    artifact_state = LegalArtifactState(
        workspace_id=matter.workspace_id,
        artifact_version_id=artifact.id,
        matter_id=matter.id,
        status="review_pending",
        approval_item_id=item.id,
        approval_matter_id=matter.id,
    )
    db.add(artifact_state)
    await db.flush()
    return service, item, artifact


async def test_approval_blocked_when_citation_unverified(db_session: AsyncSession) -> None:
    matter = await _create_test_matter(db_session)
    content = {"sections": [{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}]}
    service, item, artifact = await _create_artifact_pending_approval(
        db_session, matter, content=content
    )

    with pytest.raises(HTTPException) as caught:
        await service.resolve(item.id, ApprovalResolveRequest(status=ApprovalDecision.approved))

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "legal_artifact_citation_unverified"

    await db_session.refresh(item)
    assert item.status == ApprovalStatus.pending


async def test_approval_allowed_when_citation_verified(db_session: AsyncSession) -> None:
    matter = await _create_test_matter(db_session)
    content = {"sections": [{"title": "Do direito", "body": "Nos termos da Súmula 7 do STJ."}]}
    service, item, artifact = await _create_artifact_pending_approval(
        db_session, matter, content=content
    )

    [candidate] = extract_citations(content)
    db_session.add(
        LegalCitationVerification(
            workspace_id=matter.workspace_id,
            legal_demand_id=await _make_demand_id(db_session, matter),
            artifact_version_id=artifact.id,
            citation_text=candidate.text,
            citation_kind=candidate.kind,
            status="verificada",
            source_reference="https://stj.jus.br/sumula/7",
            trail={},
            checked_by_user_id=matter.responsible_human_id,
        )
    )
    await db_session.flush()

    resolved = await service.resolve(
        item.id, ApprovalResolveRequest(status=ApprovalDecision.approved)
    )
    assert resolved.status == ApprovalStatus.approved


async def test_approval_blocked_by_open_critical_review_finding(db_session: AsyncSession) -> None:
    matter = await _create_test_matter(db_session)
    content = {"sections": [{"title": "Do pedido", "body": "Pedido sem citação alguma."}]}
    service, item, artifact = await _create_artifact_pending_approval(
        db_session, matter, content=content
    )

    db_session.add(
        LegalReviewFinding(
            workspace_id=matter.workspace_id,
            legal_demand_id=await _make_demand_id(db_session, matter),
            artifact_version_id=artifact.id,
            severity="critica",
            kind="placeholder",
            description="Marcador de preenchimento encontrado.",
            reference={},
        )
    )
    await db_session.flush()

    with pytest.raises(HTTPException) as caught:
        await service.resolve(item.id, ApprovalResolveRequest(status=ApprovalDecision.approved))

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "legal_artifact_review_finding_open"


async def _make_demand_id(db_session: AsyncSession, matter: Matter):
    demand = LegalDemand(
        workspace_id=matter.workspace_id,
        nature=LegalDemandNature.judicial,
        area="civel",
        client_id=matter.client_id,
        parties=[],
        phase=LegalDemandPhase.triagem,
        sla={},
        documents_required=[],
        matter_id=matter.id,
    )
    db_session.add(demand)
    await db_session.flush()
    return demand.id
