"""S2: pausar (ou parar) a campanha durante o lote não perde a corrida para o worker."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

import app.services.whatsapp.campaign_service as campaign_service_module
from app.models.whatsapp_campaign import (
    CampaignSendJobStatus,
    CampaignStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
)
from tests.services.test_whatsapp_campaign_tier_quota import (
    AlwaysOkMetaClient,
    _allow_finite_commercial_quota,
    _campaign_scenario,
    _service,
)


class StatusFlippingMetaClient(AlwaysOkMetaClient):
    """No primeiro envio grava o status por fora (mesma sessão: em SQLite o pool
    é estático e uma segunda sessão compartilharia a transação), reproduzindo a
    pausa concorrente: banco muda, objeto em memória do worker continua
    `running`."""

    def __init__(self, db, campaign_id, status: CampaignStatus) -> None:
        super().__init__()
        self.db = db
        self.campaign_id = campaign_id
        self.status = status
        self.flipped = False

    async def send_template_message(self, **kwargs):
        response = await super().send_template_message(**kwargs)
        if not self.flipped:
            self.flipped = True
            await self.db.execute(
                update(WhatsAppCampaign)
                .where(WhatsAppCampaign.id == self.campaign_id)
                .values(status=self.status, paused_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()
        return response


async def _job_statuses(db_session, campaign_id):
    rows = await db_session.execute(
        select(WhatsAppCampaignSendJob.status, WhatsAppCampaignRecipient.send_context)
        .join(
            WhatsAppCampaignRecipient,
            WhatsAppCampaignRecipient.id == WhatsAppCampaignSendJob.recipient_id,
        )
        .where(WhatsAppCampaignSendJob.campaign_id == campaign_id)
        .order_by(WhatsAppCampaignSendJob.scheduled_for)
    )
    return list(rows.all())


async def _campaign_status(db_session, campaign_id) -> CampaignStatus:
    return await db_session.scalar(
        select(WhatsAppCampaign.status).where(WhatsAppCampaign.id == campaign_id)
    )


@pytest.mark.asyncio
async def test_pausa_durante_o_lote_para_o_envio_devolve_jobs_e_nunca_completa(
    db_session, monkeypatch
):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    workspace, connection, campaign, _recipients, _jobs = await _campaign_scenario(
        db_session, phones=["5511999990001", "5511999990002", "5511999990003"]
    )
    client = StatusFlippingMetaClient(db_session, campaign.id, CampaignStatus.paused)
    service = _service(db_session, connection, client)
    _allow_finite_commercial_quota(monkeypatch, service)

    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    assert client.calls == ["5511999990001"], "só o primeiro sai; a pausa é vista antes do segundo"
    statuses = await _job_statuses(db_session, campaign.id)
    assert [status for status, _ in statuses] == [
        CampaignSendJobStatus.sent,
        CampaignSendJobStatus.pending,
        CampaignSendJobStatus.pending,
    ]
    assert all("claim" not in (ctx or {}) for _, ctx in statuses[1:]), "claim limpo ao devolver"
    assert await _campaign_status(db_session, campaign.id) == CampaignStatus.paused

    # Retomar volta a enviar os dois devolvidos e aí sim completa.
    await service.resume_campaign(workspace.id, campaign.id)
    assert client.calls == ["5511999990001", "5511999990002", "5511999990003"]
    assert await _campaign_status(db_session, campaign.id) == CampaignStatus.completed


@pytest.mark.asyncio
async def test_parada_durante_o_lote_cancela_o_restante_sem_orfaos(db_session, monkeypatch):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    workspace, connection, campaign, _recipients, _jobs = await _campaign_scenario(
        db_session, phones=["5511999990011", "5511999990012"]
    )
    client = StatusFlippingMetaClient(db_session, campaign.id, CampaignStatus.stopped)
    service = _service(db_session, connection, client)
    _allow_finite_commercial_quota(monkeypatch, service)

    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    assert client.calls == ["5511999990011"]
    statuses = await _job_statuses(db_session, campaign.id)
    assert [status for status, _ in statuses] == [
        CampaignSendJobStatus.sent,
        CampaignSendJobStatus.cancelled,
    ]
    assert await _campaign_status(db_session, campaign.id) == CampaignStatus.stopped
