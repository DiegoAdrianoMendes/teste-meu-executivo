from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.billing import BillingInterval, Plan, PlanTier, Subscription, SubscriptionStatus
from app.models.meeting import (
    Meeting,
    MeetingNotificationOutbox,
    MeetingNotificationStatus,
    MeetingStatus,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.services.operational_alert_service import OperationalAlertService


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_onboarding_persists_notification_recipients(client):
    response = await client.patch(
        "/api/v1/workspaces/onboarding",
        json={
            "notificationRecipients": [
                {
                    "id": "ops-1",
                    "name": "Secretaria Comercial",
                    "email": "secretaria@example.com",
                    "phone": "+5511999990000",
                    "role": "custom",
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    roles = {item["role"] for item in payload["notificationRecipients"]}
    assert "workspace_owner" in roles
    assert any(item["email"] == "secretaria@example.com" for item in payload["notificationRecipients"])


@pytest.mark.asyncio
async def test_plan_summary_exposes_operational_recipient_limits(client, db_session, auth_context):
    plan = Plan(
        name="Plano Basic",
        tier=PlanTier.starter,
        price_monthly_cents=29700,
        price_annually_cents=285120,
        max_users=3,
        max_matters=None,
        max_tokens_per_month=500_000,
        max_ai_agents=4,
        features={
            "maxConnectedWhatsapps": 1,
            "automationLimit": 6,
            "maxOperationalRecipients": 2,
        },
        is_active=True,
        is_public=True,
        sort_order=1,
    )
    db_session.add(plan)
    await db_session.flush()

    db_session.add(
        Subscription(
            workspace_id=auth_context.workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            current_period_start=datetime.now(timezone.utc),
            current_period_end=datetime.now(timezone.utc),
            amount_cents=29700,
            currency="BRL",
        )
    )

    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    settings = dict(workspace.settings or {})
    settings["notifications"] = {
        "whatsappEnabled": True,
        "emailEnabled": True,
        "dailyDigestHour": 7,
        "recipients": [
            {"id": "r1", "name": "Ops 1", "email": "ops1@example.com", "role": "custom"},
        ],
        "rules": [],
    }
    workspace.settings = settings
    await db_session.flush()

    response = await client.get("/api/v1/workspaces/plan-summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["maxOperationalRecipients"] == 2
    assert payload["operationalRecipientsUsed"] == 1
    assert payload["operationalRecipientsRemaining"] == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_create_meeting_enqueues_alert_without_precommit_delivery(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    sent_emails: list[str] = []
    sent_whatsapps: list[str] = []

    async def fake_send_email(**kwargs):
        sent_emails.append(kwargs["to_email"])
        return True

    async def fake_send_whatsapp(self, to: str, body: str, **_kwargs):
        sent_whatsapps.append(to)
        return True

    user = await db_session.get(User, auth_context.user_id)
    assert user is not None
    user.phone = "+5511999991111"

    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    settings = dict(workspace.settings or {})
    settings["notifications"] = {
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
                "key": "meetingScheduled",
                "label": "Reunião agendada",
                "description": "Alertar quando uma reunião for criada.",
                "enabled": True,
                "emailEnabled": True,
                "whatsappEnabled": True,
            }
        ],
    }
    workspace.settings = settings
    await db_session.flush()

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

    response = await client.post(
        "/api/v1/meetings",
        headers={"Idempotency-Key": f"meeting-notification-{uuid4().hex}"},
        json={
            "title": "Reunião de fechamento",
            "startsAt": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
            "location": "Google Meet",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["title"] == "Reunião de fechamento"
    assert sent_emails == []
    assert sent_whatsapps == []
    outbox = await db_session.scalar(
        select(MeetingNotificationOutbox).where(
            MeetingNotificationOutbox.workspace_id == auth_context.workspace_id,
            MeetingNotificationOutbox.meeting_id == payload["id"],
        )
    )
    assert outbox is not None
    assert outbox.status == MeetingNotificationStatus.pending
    assert outbox.attempts == 0


@pytest.mark.asyncio
async def test_daily_digest_sends_email_and_whatsapp_with_agenda(client, db_session, auth_context, monkeypatch):
    sent_emails: list[str] = []
    sent_whatsapps: list[str] = []

    async def fake_send_email(**kwargs):
        sent_emails.append(kwargs["to_email"])
        assert kwargs["agenda"]
        return True

    async def fake_send_whatsapp(self, to: str, body: str, **_kwargs):
        sent_whatsapps.append(to)
        assert "Pendências principais" in body
        assert "Reuniões de hoje" in body
        return True

    user = await db_session.get(User, auth_context.user_id)
    assert user is not None
    user.phone = "+5511999993333"

    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    settings = dict(workspace.settings or {})
    settings["notifications"] = {
        "whatsappEnabled": True,
        "emailEnabled": True,
        "dailyDigestHour": 7,
        "recipients": [
            {
                "id": "ops-2",
                "name": "Coordenadora",
                "email": "coord@example.com",
                "phone": "+5511999994444",
                "role": "custom",
            }
        ],
        "rules": [
            {
                "key": "dailyDigest",
                "label": "Digest diário",
                "description": "Resumo do início do dia.",
                "enabled": True,
                "emailEnabled": True,
                "whatsappEnabled": True,
            }
        ],
    }
    workspace.settings = settings
    await db_session.flush()

    db_session.add(
        Meeting(
            workspace_id=workspace.id,
            title="Call com lead prioritário",
            starts_at=datetime(2026, 4, 6, 13, 0, tzinfo=timezone.utc),
            status=MeetingStatus.scheduled,
        )
    )
    await db_session.commit()

    monkeypatch.setattr(
        "app.services.operational_alert_service.send_daily_digest_email",
        fake_send_email,
    )
    monkeypatch.setattr(
        "app.services.platform_whatsapp_alert_service.PlatformWhatsAppAlertService.send_text",
        fake_send_whatsapp,
    )

    sent = await OperationalAlertService(db_session).send_daily_digest_for_workspace(
        auth_context.workspace_id,
        now=datetime(2026, 4, 6, 10, 0, tzinfo=timezone.utc),
    )

    assert sent is True
    assert "coord@example.com" in sent_emails
    assert "+5511999993333" in sent_whatsapps
