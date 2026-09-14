"""S35: a listagem de campanhas não carrega todos os destinatários."""

from __future__ import annotations

import pytest
from sqlalchemy import event, select

from app.models.whatsapp_campaign import CampaignRecipientStatus, WhatsAppCampaignRecipient
from tests.services.test_whatsapp_campaign_tier_quota import (
    AlwaysOkMetaClient,
    _campaign_scenario,
    _service,
)


@pytest.mark.asyncio
async def test_list_counters_match_get_and_recipients_are_not_loaded(db_session):
    phones = [f"5511{900000000 + i:09d}" for i in range(30)]
    workspace, connection, campaign, recipients, _jobs = await _campaign_scenario(
        db_session, phones=phones
    )
    for index, recipient in enumerate(recipients):
        recipient.status = [
            CampaignRecipientStatus.sent,
            CampaignRecipientStatus.delivered,
            CampaignRecipientStatus.read,
            CampaignRecipientStatus.failed,
            CampaignRecipientStatus.queued,
        ][index % 5]
    await db_session.commit()
    service = _service(db_session, connection, AlwaysOkMetaClient())

    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "whatsapp_campaign_recipients" in statement:
            statements.append(statement)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        listed = await service.list_campaigns(workspace.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    detailed = await service.get_campaign(workspace.id, campaign.id)

    assert len(statements) == 2, "agregação + janela, nunca a relação inteira"
    assert all("GROUP BY" in s or "row_number" in s.lower() for s in statements)
    (item,) = [c for c in listed if c.id == campaign.id]
    campos = ("sent_count", "delivered_count", "read_count", "failed_count", "total_recipients")
    for campo in campos:
        assert getattr(item, campo) == getattr(detailed, campo), campo
    assert item.sent_count == 18 and item.delivered_count == 12 and item.read_count == 6
    assert item.failed_count == 6
    assert len(item.sample_recipients) == 8
    assert len(detailed.sample_recipients) == 8
    assert {r.id for r in item.sample_recipients} == {r.id for r in detailed.sample_recipients}
    mais_recentes = (
        await db_session.scalars(
            select(WhatsAppCampaignRecipient.id)
            .where(WhatsAppCampaignRecipient.campaign_id == campaign.id)
            .order_by(
                WhatsAppCampaignRecipient.created_at.desc(), WhatsAppCampaignRecipient.id.desc()
            )
            .limit(8)
        )
    ).all()
    assert {r.id for r in item.sample_recipients} == set(mais_recentes)
