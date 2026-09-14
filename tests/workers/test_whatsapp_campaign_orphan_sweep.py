"""Sweep de claims órfãos do worker de campanhas (médio 5 da auditoria Meta
23/07/2026): job preso em `processing` por crash do worker deve virar
`failed` com razão legível, e NUNCA ser reenviado automaticamente.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp import WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.workers.whatsapp_campaign_tasks import (
    ORPHAN_CLAIM_FAILURE_REASON,
    _sweep_orphan_campaign_claims_for_workspace,
)


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)


def _workspace() -> Workspace:
    return Workspace(
        name="Sweep WS",
        slug=f"sweep-ws-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


async def _processing_job(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    claimed_at: datetime | None,
    status=CampaignSendJobStatus.processing,
    last_error: str | None = None,
    recipient_status=CampaignRecipientStatus.processing,
):
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        phone_number_id=f"sweep-{uuid4().hex[:8]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title="Campanha sweep",
        status=CampaignStatus.running,
    )
    db_session.add(campaign)
    await db_session.flush()
    send_context = {}
    if claimed_at is not None:
        send_context = {"claim": {"claimedAt": claimed_at.isoformat()}}
    recipient = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=workspace.id,
        phone=f"5511{uuid4().int % 10**9:09d}",
        status=recipient_status,
        send_context=send_context,
    )
    db_session.add(recipient)
    await db_session.flush()
    job = WhatsAppCampaignSendJob(
        campaign_id=campaign.id,
        recipient_id=recipient.id,
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        status=status,
        last_error=last_error,
    )
    db_session.add(job)
    await db_session.flush()
    return job, recipient


@pytest.mark.asyncio
async def test_sweep_marks_only_expired_claims_as_failed(db_session: AsyncSession):
    workspace = _workspace()
    db_session.add(workspace)
    await db_session.flush()

    now = datetime.now(UTC)
    expired_job, expired_recipient = await _processing_job(
        db_session, workspace, claimed_at=now - timedelta(minutes=45)
    )
    fresh_job, fresh_recipient = await _processing_job(
        db_session, workspace, claimed_at=now - timedelta(minutes=5)
    )
    no_claim_job, no_claim_recipient = await _processing_job(
        db_session, workspace, claimed_at=None
    )
    with_error_job, _ = await _processing_job(
        db_session, workspace, claimed_at=now - timedelta(minutes=90), last_error="already flagged"
    )
    uncertain_job, _ = await _processing_job(
        db_session,
        workspace,
        claimed_at=now - timedelta(minutes=90),
        recipient_status=CampaignRecipientStatus.delivery_uncertain,
    )
    await db_session.commit()

    swept = await _sweep_orphan_campaign_claims_for_workspace(
        workspace.id, session_factory=lambda: _session_factory(db_session)(), now=now
    )
    assert swept == 1

    async with _session_factory(db_session)() as verify_db:
        refreshed_expired = await verify_db.get(WhatsAppCampaignSendJob, expired_job.id)
        assert refreshed_expired.status == CampaignSendJobStatus.failed
        assert refreshed_expired.last_error == ORPHAN_CLAIM_FAILURE_REASON

        refreshed_expired_recipient = await verify_db.get(
            WhatsAppCampaignRecipient, expired_recipient.id
        )
        assert refreshed_expired_recipient.status == CampaignRecipientStatus.failed

        refreshed_fresh = await verify_db.get(WhatsAppCampaignSendJob, fresh_job.id)
        assert refreshed_fresh.status == CampaignSendJobStatus.processing

        refreshed_no_claim = await verify_db.get(WhatsAppCampaignSendJob, no_claim_job.id)
        assert refreshed_no_claim.status == CampaignSendJobStatus.processing

        refreshed_with_error = await verify_db.get(WhatsAppCampaignSendJob, with_error_job.id)
        assert refreshed_with_error.status == CampaignSendJobStatus.processing

        refreshed_uncertain = await verify_db.get(WhatsAppCampaignSendJob, uncertain_job.id)
        assert refreshed_uncertain.status == CampaignSendJobStatus.processing

        audit = await verify_db.scalar(
            select(KlmAuditEvent).where(
                KlmAuditEvent.entity_id == expired_job.id,
                KlmAuditEvent.event_type == "whatsapp_campaign_job_claim_expired",
            )
        )
        assert audit is not None


@pytest.mark.asyncio
async def test_sweep_never_resends_it_only_fails_the_job(db_session: AsyncSession):
    """Garantia explícita do requisito: o sweep NUNCA reenvia — só encerra o
    job. Confirmamos que attempt_count não é mexido e o job não volta a
    `pending`/`scheduled`."""
    workspace = _workspace()
    db_session.add(workspace)
    await db_session.flush()
    now = datetime.now(UTC)
    job, _ = await _processing_job(db_session, workspace, claimed_at=now - timedelta(hours=2))
    job.attempt_count = 1
    await db_session.commit()

    await _sweep_orphan_campaign_claims_for_workspace(
        workspace.id, session_factory=lambda: _session_factory(db_session)(), now=now
    )

    async with _session_factory(db_session)() as verify_db:
        refreshed = await verify_db.get(WhatsAppCampaignSendJob, job.id)
        assert refreshed.status == CampaignSendJobStatus.failed
        assert refreshed.attempt_count == 1
        assert refreshed.status not in (
            CampaignSendJobStatus.pending,
            CampaignSendJobStatus.scheduled,
        )
