from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import FollowUpStatus, FollowUpTask, OmnichannelChannel
from app.models.user import User
from app.models.workspace import Workspace
from app.services.operational_alert_service import OperationalAlertService


def _notifications_settings() -> dict:
    return {
        "whatsappEnabled": True,
        "emailEnabled": True,
        "dailyDigestHour": 7,
        "recipients": [
            {
                "id": "ops-1",
                "name": "Secretaria",
                "email": "secretaria@example.com",
                "phone": "+5511999992222",
                "role": "custom",
            }
        ],
        "rules": [
            {
                "key": "followUpDue",
                "enabled": True,
                "emailEnabled": True,
                "whatsappEnabled": True,
            }
        ],
    }


async def _seed_workspace_and_follow_up(
    db_session: AsyncSession,
    auth_context,
    *,
    due_at: datetime,
) -> FollowUpTask:
    user = await db_session.get(User, auth_context.user_id)
    assert user is not None
    user.phone = "+5511999991111"

    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    workspace.settings = {**(workspace.settings or {}), "notifications": _notifications_settings()}
    await db_session.flush()

    follow_up = FollowUpTask(
        workspace_id=auth_context.workspace_id,
        channel=OmnichannelChannel.whatsapp,
        status=FollowUpStatus.scheduled,
        due_at=due_at,
        reason="Cliente pediu retorno em 2 dias",
    )
    db_session.add(follow_up)
    await db_session.commit()
    await db_session.refresh(follow_up)
    return follow_up


@pytest.mark.asyncio
async def test_send_due_follow_up_reminders_dispatches_once_and_second_tick_is_noop(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    """Vencido -> enfileira 1x; rodar o tick de novo não duplica o envio."""
    sent_emails: list[str] = []
    sent_whatsapps: list[str] = []

    async def fake_send_email(**kwargs):
        sent_emails.append(kwargs["to_email"])
        return True

    async def fake_send_whatsapp(self, to: str, body: str, **_kwargs):
        sent_whatsapps.append(to)
        return True

    monkeypatch.setattr(
        "app.services.operational_alert_service.send_operational_alert_email",
        fake_send_email,
    )
    monkeypatch.setattr(
        "app.services.platform_whatsapp_alert_service.PlatformWhatsAppAlertService.send_text",
        fake_send_whatsapp,
    )
    monkeypatch.setattr(
        "app.services.operational_alert_service.external_delivery_allowed",
        lambda: True,
    )

    now = datetime.now(timezone.utc)
    follow_up = await _seed_workspace_and_follow_up(
        db_session, auth_context, due_at=now - timedelta(minutes=10)
    )

    service = OperationalAlertService(db_session)

    first_run = await service.send_due_follow_up_reminders(now=now)
    assert first_run == 1
    assert "secretaria@example.com" in sent_emails
    assert "+5511999991111" in sent_whatsapps
    emails_after_first_run = list(sent_emails)
    whatsapps_after_first_run = list(sent_whatsapps)

    await db_session.refresh(follow_up)
    assert follow_up.status == FollowUpStatus.due
    assert follow_up.last_notified_at is not None

    second_run = await service.send_due_follow_up_reminders(now=now + timedelta(minutes=1))
    assert second_run == 0
    assert sent_emails == emails_after_first_run
    assert sent_whatsapps == whatsapps_after_first_run


@pytest.mark.asyncio
async def test_send_due_follow_up_reminders_gate_closed_leaves_follow_up_pending(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    """Gate fechado: nenhum envio real acontece e o follow-up fica pendente
    (elegível) para o próximo tick, em vez de ser marcado como notificado."""

    def forbidden_send_email(**kwargs):
        raise AssertionError("gate fechado não deveria disparar e-mail")

    def forbidden_send_whatsapp(self, to: str, body: str, **_kwargs):
        raise AssertionError("gate fechado não deveria disparar WhatsApp")

    monkeypatch.setattr(
        "app.services.operational_alert_service.send_operational_alert_email",
        forbidden_send_email,
    )
    monkeypatch.setattr(
        "app.services.platform_whatsapp_alert_service.PlatformWhatsAppAlertService.send_text",
        forbidden_send_whatsapp,
    )
    monkeypatch.setattr(
        "app.services.operational_alert_service.external_delivery_allowed",
        lambda: False,
    )

    now = datetime.now(timezone.utc)
    follow_up = await _seed_workspace_and_follow_up(
        db_session, auth_context, due_at=now - timedelta(minutes=10)
    )

    service = OperationalAlertService(db_session)
    sent = await service.send_due_follow_up_reminders(now=now)

    assert sent == 0
    await db_session.refresh(follow_up)
    assert follow_up.status == FollowUpStatus.scheduled
    assert follow_up.last_notified_at is None
