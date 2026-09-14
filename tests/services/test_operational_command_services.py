"""Regression coverage for flow/API canonical mutation boundaries."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal, ContractBillingProposalStatus
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.services.client_command_service import ClientCommandService
from app.services.contract_command_service import ContractCommandService


@pytest.mark.asyncio
async def test_client_command_is_workspace_scoped_audited_and_idempotent(db_session, auth_context):
    client = Client(workspace_id=auth_context.workspace_id, name="Antes")
    db_session.add(client)
    await db_session.flush()

    service = ClientCommandService(db_session)
    updated = await service.update(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        changes={"name": "Depois"},
        actor_user_id=auth_context.user_id,
        idempotency_key="client-change-001",
    )
    assert updated.name == "Depois"
    replay = await service.update(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        changes={"name": "Depois"},
        actor_user_id=auth_context.user_id,
        idempotency_key="client-change-001",
    )
    assert replay.id == client.id
    events = list(
        (
            await db_session.scalars(
                select(KlmAuditEvent).where(KlmAuditEvent.entity_id == client.id)
            )
        ).all()
    )
    assert len([event for event in events if event.event_type == "client_updated"]) == 1
    with pytest.raises(HTTPException) as exc:
        await service.update(
            workspace_id=uuid4(),
            client_id=client.id,
            changes={"name": "Não pode"},
            actor_user_id=auth_context.user_id,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_contract_command_only_edits_drafts_and_writes_audit(db_session, auth_context):
    client = Client(workspace_id=auth_context.workspace_id, name="Cliente contrato")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Contrato",
    )
    db_session.add(matter)
    await db_session.flush()
    proposal = ContractBillingProposal(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        items=[],
        status=ContractBillingProposalStatus.draft,
        notes="antes",
    )
    db_session.add(proposal)
    await db_session.flush()
    service = ContractCommandService(db_session)
    updated = await service.update(
        workspace_id=auth_context.workspace_id,
        proposal_id=proposal.id,
        changes={"notes": "depois"},
        actor_user_id=auth_context.user_id,
        idempotency_key="proposal-change-001",
    )
    assert updated.notes == "depois"
    event = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.entity_id == proposal.id,
            KlmAuditEvent.event_type == "contract_billing_proposal_updated",
        )
    )
    assert event is not None
    proposal.status = ContractBillingProposalStatus.pending_approval
    await db_session.flush()
    with pytest.raises(HTTPException) as exc:
        await service.update(
            workspace_id=auth_context.workspace_id,
            proposal_id=proposal.id,
            changes={"notes": "não deve mudar"},
            actor_user_id=auth_context.user_id,
        )
    assert exc.value.status_code == 409
