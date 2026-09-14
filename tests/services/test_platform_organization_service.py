from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.platform_organization import (
    PlatformOperationalAssignmentWrite,
    PlatformWorkspaceAssignmentWrite,
)
from app.services.platform_organization_service import (
    PlatformOrganizationError,
    PlatformOrganizationService,
)


@pytest.mark.asyncio
async def test_staff_invitation_audit_never_persists_raw_email(
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    raw_email = f"private-{uuid4().hex[:8]}@example.com"

    invitation = await PlatformOrganizationService(db_session).create_staff_invitation(
        actor=actor,
        values={
            "email": raw_email,
            "name": "Pessoa de suporte",
            "role_package": "support",
            "expires_in_days": 7,
        },
        reason="Contratação aprovada pelo titular",
    )

    event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.resource_type == "staff_invitation",
            PlatformAuditEvent.resource_id == str(invitation.id),
        )
    )
    assert event is not None
    assert raw_email not in repr(event.after)
    assert event.after["emailHint"].endswith("@example.com")


@pytest.mark.asyncio
async def test_operational_assignment_and_review_require_real_source(
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    service = PlatformOrganizationService(db_session)

    with pytest.raises(PlatformOrganizationError) as assignment_error:
        await service.save_operational_assignment(
            actor=actor,
            values={
                "source_type": "billing",
                "source_id": uuid4(),
                "owner_user_id": actor.id,
                "team_id": None,
                "next_action": "Verificar cobrança",
                "next_action_at": None,
                "due_at": None,
                "state": "open",
                "expected_version": None,
            },
            reason="Atribuição operacional de teste",
        )
    assert assignment_error.value.code == "PLATFORM_OPERATIONAL_SOURCE_NOT_FOUND"

    with pytest.raises(PlatformOrganizationError) as review_error:
        await service.save_review(
            actor=actor,
            values={
                "source_type": "configuration",
                "source_id": uuid4(),
                "state": "in_review",
                "reviewer_user_id": None,
                "review_due_at": None,
                "expected_version": None,
            },
            reason="Revisão de configuração inexistente",
        )
    assert review_error.value.code == "PLATFORM_REVIEW_SOURCE_NOT_FOUND"


def test_assignment_contracts_reject_person_and_team_together():
    common = {"reason": "Atribuição ambígua deve ser recusada"}
    with pytest.raises(ValidationError):
        PlatformWorkspaceAssignmentWrite(
            assignment_type="support",
            assigned_user_id=uuid4(),
            assigned_team_id=uuid4(),
            **common,
        )
    with pytest.raises(ValidationError):
        PlatformOperationalAssignmentWrite(
            source_type="ticket",
            source_id=uuid4(),
            owner_user_id=uuid4(),
            team_id=uuid4(),
            state="open",
            **common,
        )


@pytest.mark.asyncio
async def test_milestone_rejects_customer_as_internal_owner(
    db_session,
    platform_auth_context,
):
    actor = await db_session.get(User, platform_auth_context.user_id)
    workspace = Workspace(
        name="Conta de onboarding",
        slug=f"onboarding-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.assisted,
        is_active=True,
        onboarding_completed=False,
        settings={},
    )
    customer = User(
        name="Pessoa cliente",
        email=f"customer-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="PC",
        color="#111111",
        is_active=True,
        is_internal_staff=False,
        platform_access_active=False,
    )
    db_session.add_all([workspace, customer])
    await db_session.flush()

    with pytest.raises(PlatformOrganizationError) as error:
        await PlatformOrganizationService(db_session).save_milestone(
            actor=actor,
            workspace_id=workspace.id,
            values={
                "key": "kickoff",
                "label": "Kickoff",
                "status": "pending",
                "owner_user_id": customer.id,
                "due_at": None,
                "expected_version": None,
            },
            reason="Responsável precisa integrar a equipe SaaS",
        )
    assert error.value.code == "PLATFORM_MILESTONE_OWNER_INVALID"
