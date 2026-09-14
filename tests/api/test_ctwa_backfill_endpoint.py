"""Endpoint de backfill CTWA histórico (T1.2 do Plano Meta Máximo).

Cobre RBAC (precisa de admin/manage) e o contraste dry-run × execute.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.klm_operations import AcquisitionEvent, OmnichannelThread
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.message_evidence_service import MessageEvidenceService

pytestmark = pytest.mark.usefixtures("active_subscription")

CTWA_REFERRAL = {
    "ctwa_clid": "ARAcLId_endpoint_0001",
    "source_url": "https://fb.me/anuncio-endpoint",
    "source_id": "111222333444555",
    "source_type": "ad",
    "headline": "Fale com um advogado agora",
    "body": "Atendimento imediato pelo WhatsApp",
}


async def _seed_referral_evidence(db_session, workspace_id, *, wamid: str):
    thread = OmnichannelThread(workspace_id=workspace_id)
    db_session.add(thread)
    await db_session.flush()
    message_data = {
        "id": wamid,
        "from": "5511988887777",
        "timestamp": "1700000000",
        "type": "text",
        "text": {"body": "Vi o anúncio de vocês"},
        "referral": dict(CTWA_REFERRAL),
    }
    await MessageEvidenceService(db_session).record_event(
        workspace_id=workspace_id,
        provider="meta_cloud",
        connection_key="whatsapp:conn-1",
        external_message_id=wamid,
        event_type="provider_receipt",
        thread_id=thread.id,
        body_snapshot="Vi o anúncio de vocês",
        content_type="text",
        event_payload=message_data,
    )
    await db_session.commit()
    return thread.id


@pytest.mark.asyncio
async def test_dry_run_reports_without_persisting(client, db_session, auth_context):
    await _seed_referral_evidence(db_session, auth_context.workspace_id, wamid="wamid.ep-dryrun-1")

    response = await client.post(
        "/api/v1/klm/tracking/backfill-ctwa",
        json={"dryRun": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dryRun"] is True
    assert body["scanned"] == 1
    assert body["withReferral"] == 1
    assert body["eventsCreated"] == 1

    total = await db_session.scalar(
        select(AcquisitionEvent).where(AcquisitionEvent.workspace_id == auth_context.workspace_id)
    )
    assert total is None


@pytest.mark.asyncio
async def test_execute_persists_acquisition_event(client, db_session, auth_context):
    await _seed_referral_evidence(db_session, auth_context.workspace_id, wamid="wamid.ep-execute-1")

    response = await client.post(
        "/api/v1/klm/tracking/backfill-ctwa",
        json={"dryRun": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dryRun"] is False
    assert body["eventsCreated"] == 1

    event = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.workspace_id == auth_context.workspace_id,
            AcquisitionEvent.dedupe_key == "ctwa:wamid.ep-execute-1",
        )
    )
    assert event is not None
    assert event.event_name == "conversation_started"

    # Reentrada não duplica.
    second = await client.post(
        "/api/v1/klm/tracking/backfill-ctwa",
        json={"dryRun": False},
    )
    assert second.status_code == 200, second.text
    assert second.json()["skippedExisting"] == 1
    assert second.json()["eventsCreated"] == 0


@pytest.mark.asyncio
async def test_operator_role_is_forbidden(client, db_session, auth_context):
    operator_membership = await db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == auth_context.workspace_id,
            WorkspaceMembership.user_id == auth_context.user_id,
        )
    )
    assert operator_membership is not None
    operator_membership.role = WorkspaceRole.operator
    await db_session.commit()

    response = await client.post(
        "/api/v1/klm/tracking/backfill-ctwa",
        json={"dryRun": True},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_workspace_scoping_ignores_other_workspace_evidence(client, db_session, auth_context):
    suffix = uuid4().hex[:8]
    other_workspace = Workspace(
        name="Outro Workspace",
        slug=f"outro-workspace-{suffix}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.flush()
    await _seed_referral_evidence(db_session, other_workspace.id, wamid="wamid.ep-other-ws-1")

    response = await client.post(
        "/api/v1/klm/tracking/backfill-ctwa",
        json={"dryRun": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scanned"] == 0
    assert body["withReferral"] == 0
