"""
Testes do consumo de tier/quota de 24h pelo motor de campanhas (Sprint
W1.5 — revisão pós-auditoria: janela deslizante real + rampa).

Cobre:
- lote maior que a folga da janela envia só o que cabe e reagenda o
  restante sem consumir tentativa, com evento de auditoria
  "tier_quota_deferred";
- rampa de aquecimento aplica um teto adicional conservador por estágio;
- após envios bem-sucedidos, o cache informativo do health service é
  atualizado com os únicos novos do lote.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.whatsapp import WaConnectionStatus, WhatsAppConnection, WhatsAppConnectionTier
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
    WhatsAppCampaignTemplateSnapshot,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.whatsapp import campaign_service as campaign_service_module
from app.services.whatsapp.campaign_service import RAMP_UP_STAGE_CAPS, WhatsAppCampaignService
from app.services.whatsapp.connection_health_service import WhatsAppConnectionHealthService


def _workspace(name: str) -> Workspace:
    return Workspace(
        name=name,
        slug=f"tier-quota-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


async def _campaign_scenario(
    db_session: AsyncSession,
    *,
    phones: list[str],
):
    workspace = _workspace(f"Tier quota {uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        phone_number_id=f"phone-{uuid4().hex[:10]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title="Campanha tier quota",
        status=CampaignStatus.running,
    )
    db_session.add(campaign)
    await db_session.flush()
    db_session.add(
        WhatsAppCampaignTemplateSnapshot(
            campaign_id=campaign.id,
            template_name="tier_quota",
            language="pt_BR",
            status=MetaTemplateStatus.approved,
            header_parameters=[],
            body_parameters=[],
            components=[],
            raw_payload={},
        )
    )
    recipients: list[WhatsAppCampaignRecipient] = []
    jobs: list[WhatsAppCampaignSendJob] = []
    base = datetime.now(UTC) - timedelta(minutes=10)
    for index, phone in enumerate(phones):
        recipient = WhatsAppCampaignRecipient(
            campaign_id=campaign.id,
            workspace_id=workspace.id,
            phone=phone,
            status=CampaignRecipientStatus.queued,
            send_context={},
        )
        db_session.add(recipient)
        await db_session.flush()
        job = WhatsAppCampaignSendJob(
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            workspace_id=workspace.id,
            whatsapp_connection_id=connection.id,
            status=CampaignSendJobStatus.pending,
            scheduled_for=base + timedelta(seconds=index),
        )
        db_session.add(job)
        recipients.append(recipient)
        jobs.append(job)
    await db_session.commit()
    return workspace, connection, campaign, recipients, jobs


class FakeConnectionService:
    def __init__(self, connection: WhatsAppConnection) -> None:
        self.connection = connection

    async def get_connection(self, connection_id, workspace_id):
        return self.connection

    async def get_default_connection(self, workspace_id, *, connected_only=False):
        return self.connection

    def get_meta_access_token(self, connection):
        return "local-test-token"

    async def resolve_meta_access_token(self, connection):
        return self.get_meta_access_token(connection)


class AlwaysOkMetaClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_template_message(self, **kwargs):
        to = kwargs["to"]
        self.calls.append(to)
        return {"messages": [{"id": f"wamid-{len(self.calls)}"}]}


def _service(db_session: AsyncSession, connection: WhatsAppConnection, client) -> WhatsAppCampaignService:
    return WhatsAppCampaignService(
        db_session,
        connection_service=FakeConnectionService(connection),
        meta_client=client,
    )


def _allow_finite_commercial_quota(monkeypatch, service: WhatsAppCampaignService) -> None:
    """Isola a cota Meta testada aqui de uma cota comercial finita explícita."""

    async def finite_commercial_quota(**_kwargs) -> int:
        return 100_000

    monkeypatch.setattr(service, "_campaign_recipient_limit", finite_commercial_quota)


@pytest.mark.asyncio
async def test_batch_bigger_than_quota_folga_sends_partial_and_defers_rest(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phones = [f"55119888{i:05d}" for i in range(5)]
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=phones
    )

    health_service = WhatsAppConnectionHealthService(db_session)
    record = await health_service.get_or_create_tier(
        workspace_id=workspace.id, connection_id=connection.id
    )
    record.tier = WhatsAppConnectionTier.tier_250
    # Número já aquecido (estágio além do mapa de rampa): só o teto do
    # tier vale, isolando o cenário testado aqui (folga do tier).
    record.ramp_up_stage = 99
    await db_session.commit()

    # Já usou 247 da janela de 24h (fonte de query): sobram 3 de folga.
    await _seed_used_recipients(db_session, workspace=workspace, connection=connection, count=247)

    client = AlwaysOkMetaClient()
    service = _service(db_session, connection, client)
    _allow_finite_commercial_quota(monkeypatch, service)

    processed = await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    assert processed == 3
    assert len(client.calls) == 3

    for recipient in recipients:
        await db_session.refresh(recipient)
    for job in jobs:
        await db_session.refresh(job)

    sent = [r for r in recipients if r.status == CampaignRecipientStatus.sent]
    queued = [r for r in recipients if r.status == CampaignRecipientStatus.queued]
    assert len(sent) == 3
    assert len(queued) == 2

    deferred_jobs = [j for j in jobs if j.recipient_id in {r.id for r in queued}]
    for job in deferred_jobs:
        assert job.status == CampaignSendJobStatus.pending
        assert job.attempt_count == 0  # não consumiu tentativa
        assert job.scheduled_for is not None
        scheduled_for = job.scheduled_for
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.replace(tzinfo=UTC)
        assert scheduled_for > datetime.now(UTC)
        assert "tier_quota_deferred" in (job.last_error or "")

    events = (
        await db_session.execute(
            select(WhatsAppCampaignDeliveryEvent).where(
                WhatsAppCampaignDeliveryEvent.campaign_id == campaign.id
            )
        )
    ).scalars().all()
    audit_events = [e for e in events if e.payload.get("event") == "tier_quota_deferred"]
    assert len(audit_events) == 1
    assert audit_events[0].payload["deferredCount"] == 2


@pytest.mark.asyncio
async def test_ramp_up_stage_caps_batch_even_with_tier_folga_available(
    db_session: AsyncSession, monkeypatch
):
    """Rampa de aquecimento (estágio 0) limita a 50 destinatários mesmo com
    tier_10k (folga de milhares) disponível."""
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phones = [f"55119777{i:05d}" for i in range(3)]
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=phones
    )

    health_service = WhatsAppConnectionHealthService(db_session)
    record = await health_service.get_or_create_tier(
        workspace_id=workspace.id, connection_id=connection.id
    )
    record.tier = WhatsAppConnectionTier.tier_10k
    record.ramp_up_stage = 0
    await db_session.commit()

    # Já usou 49 do teto de rampa do estágio 0 (RAMP_UP_STAGE_CAPS[0] == 50):
    # só sobra 1 de folga de rampa, mesmo o tier tendo milhares livres.
    ramp_cap_stage_0 = RAMP_UP_STAGE_CAPS[0]
    await _seed_used_recipients(
        db_session, workspace=workspace, connection=connection, count=ramp_cap_stage_0 - 1
    )

    client = AlwaysOkMetaClient()
    service = _service(db_session, connection, client)
    _allow_finite_commercial_quota(monkeypatch, service)
    processed = await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    assert processed == 1
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_successful_batch_updates_health_service_cache_with_unique_new(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    phones = [f"55119666{i:05d}" for i in range(4)]
    workspace, connection, campaign, recipients, jobs = await _campaign_scenario(
        db_session, phones=phones
    )
    client = AlwaysOkMetaClient()
    service = _service(db_session, connection, client)
    _allow_finite_commercial_quota(monkeypatch, service)

    await service._process_ready_jobs(campaign.id, connection, workspace_id=workspace.id)

    health_service = WhatsAppConnectionHealthService(db_session)
    record = await health_service.get_or_create_tier(
        workspace_id=workspace.id, connection_id=connection.id
    )
    assert record.daily_unique_recipients == 4


async def _seed_used_recipients(db_session, *, workspace, connection, count: int) -> None:
    """Semeia `count` destinatários já 'sent' há 1h (dentro da janela de
    24h) numa campanha auxiliar, para simular uso prévio da quota."""
    if count <= 0:
        return
    aux_campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title="Campanha auxiliar (uso prévio)",
        status=CampaignStatus.completed,
    )
    db_session.add(aux_campaign)
    await db_session.flush()
    sent_at = datetime.now(UTC) - timedelta(hours=1)
    for index in range(count):
        db_session.add(
            WhatsAppCampaignRecipient(
                campaign_id=aux_campaign.id,
                workspace_id=workspace.id,
                phone=f"+55110{index:08d}",
                status=CampaignRecipientStatus.sent,
                sent_at=sent_at,
                send_context={},
            )
        )
    await db_session.commit()
