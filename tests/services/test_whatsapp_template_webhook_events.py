"""W2.2 — eventos de status/categoria de template Meta no webhook do WABA.

Cobre:
- `message_template_status_update` (approved/rejected/paused) atualiza o
  WhatsAppMetaTemplate, grava KlmAuditEvent e notifica os aprovadores do
  workspace (workspace_owner/workspace_admin);
- `template_category_update` marca `previousCategory` no raw_payload e
  alerta o custo ~7x quando a nova categoria é marketing;
- replay do mesmo evento (mesma task Celery reprocessada) é idempotente:
  não duplica notificação/evento de auditoria.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.models.klm_operations import KlmAuditEvent
from app.models.notification import Notification
from app.models.user import AgentType, User, UserRole
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
    WhatsAppConnectionTiers,
)
from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.whatsapp.meta_webhook_operations_service import MetaWebhookOperationsService
from app.services.whatsapp.webhook_service import WhatsAppWebhookService


class _StubConnectionService:
    async def find_connection_by_phone_number_id(self, _phone_number_id):
        return None


def _build_service(db: AsyncSession) -> WhatsAppWebhookService:
    return WhatsAppWebhookService(
        db,
        connection_service=_StubConnectionService(),
        message_service=SimpleNamespace(),
        automation_rule_service=SimpleNamespace(),
        campaign_service=SimpleNamespace(),
    )


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    database_path = tmp_path / "whatsapp-template-webhook.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        poolclass=NullPool,
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed(factory):
    async with factory() as db:
        workspace = Workspace(
            name="Template Webhook WS",
            slug=f"template-webhook-{uuid4().hex[:8]}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.flush()

        owner = User(
            name="Owner Teste",
            email=f"owner-{uuid4().hex[:8]}@example.com",
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials="OT",
            color="#111827",
            is_active=True,
            is_internal_staff=False,
        )
        db.add(owner)
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=owner.id,
                role=WorkspaceRole.workspace_owner,
                status=MembershipStatus.active,
                is_default=True,
            )
        )

        connection = WhatsAppConnection(
            workspace_id=workspace.id,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
            is_default=True,
            phone_number_id="pn-template-1",
            waba_id="waba-template-1",
            status=WaConnectionStatus.connected,
        )
        db.add(connection)
        await db.flush()

        template = WhatsAppMetaTemplate(
            workspace_id=workspace.id,
            whatsapp_connection_id=connection.id,
            external_template_id="ext-tpl-1",
            name="lembrete_consulta",
            category="utility",
            language="pt_BR",
            status=MetaTemplateStatus.pending,
            components=[],
            raw_payload={"source": "meta_sync"},
        )
        db.add(template)
        await db.commit()
        return workspace.id, owner.id, template.id


def _status_payload(*, waba_id: str, event: str, template_id: str, reason: str | None = None) -> dict:
    value: dict = {
        "event": event,
        "message_template_id": template_id,
        "message_template_name": "lembrete_consulta",
        "message_template_language": "pt_BR",
    }
    if reason is not None:
        value["reason"] = reason
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
                "changes": [
                    {"field": "message_template_status_update", "value": value}
                ],
            }
        ],
    }


def _category_payload(*, waba_id: str, template_id: str, previous: str, new: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
                "changes": [
                    {
                        "field": "template_category_update",
                        "value": {
                            "message_template_id": template_id,
                            "message_template_name": "lembrete_consulta",
                            "message_template_language": "pt_BR",
                            "previous_category": previous,
                            "new_category": new,
                        },
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_status_update_rejected_updates_template_and_notifies(session_factory):
    workspace_id, owner_id, template_id = await _seed(session_factory)
    async with session_factory() as db:
        service = _build_service(db)
        payload = _status_payload(
            waba_id="waba-template-1",
            event="REJECTED",
            template_id="ext-tpl-1",
            reason="INVALID_FORMAT",
        )
        await service.process_webhook(payload)
        await db.commit()

    async with session_factory() as db:
        template = await db.get(WhatsAppMetaTemplate, template_id)
        assert template.status == MetaTemplateStatus.rejected
        assert template.raw_payload["rejected_reason"] == "INVALID_FORMAT"
        assert template.raw_payload["last_status_event"] == "REJECTED"

        audit_count = await db.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.event_type == "whatsapp_template_status_update"
            )
        )
        assert audit_count == 1

        notifications = (
            await db.scalars(select(Notification).where(Notification.user_id == owner_id))
        ).all()
        assert len(notifications) == 1
        assert "rejeitado" in notifications[0].message.lower()
        assert "formato inválido" in notifications[0].message.lower()


@pytest.mark.asyncio
async def test_status_update_replay_is_idempotent(session_factory):
    workspace_id, owner_id, template_id = await _seed(session_factory)
    payload = _status_payload(
        waba_id="waba-template-1",
        event="APPROVED",
        template_id="ext-tpl-1",
    )

    async with session_factory() as db:
        service = _build_service(db)
        await service.process_webhook(payload)
        await db.commit()

    # Reentrega da Meta / retry de Celery: mesmo payload processado de novo.
    async with session_factory() as db:
        service = _build_service(db)
        await service.process_webhook(payload)
        await db.commit()

    async with session_factory() as db:
        template = await db.get(WhatsAppMetaTemplate, template_id)
        assert template.status == MetaTemplateStatus.approved

        audit_count = await db.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.event_type == "whatsapp_template_status_update"
            )
        )
        assert audit_count == 1

        notifications = (
            await db.scalars(select(Notification).where(Notification.user_id == owner_id))
        ).all()
        assert len(notifications) == 1


@pytest.mark.asyncio
async def test_category_update_flags_marketing_cost_and_notifies(session_factory):
    workspace_id, owner_id, template_id = await _seed(session_factory)
    async with session_factory() as db:
        service = _build_service(db)
        payload = _category_payload(
            waba_id="waba-template-1",
            template_id="ext-tpl-1",
            previous="utility",
            new="marketing",
        )
        await service.process_webhook(payload)
        await db.commit()

    async with session_factory() as db:
        template = await db.get(WhatsAppMetaTemplate, template_id)
        assert template.category == "marketing"
        assert template.raw_payload["previousCategory"] == "utility"

        notifications = (
            await db.scalars(select(Notification).where(Notification.user_id == owner_id))
        ).all()
        assert len(notifications) == 1
        assert "7x" in notifications[0].message

        audit_count = await db.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.event_type == "whatsapp_template_category_update"
            )
        )
        assert audit_count == 1


@pytest.mark.asyncio
async def test_status_update_unknown_waba_is_ignored_not_crashed(session_factory):
    await _seed(session_factory)
    async with session_factory() as db:
        service = _build_service(db)
        payload = _status_payload(
            waba_id="waba-does-not-exist",
            event="APPROVED",
            template_id="ext-tpl-1",
        )
        await service.process_webhook(payload)
        await db.commit()

    async with session_factory() as db:
        audit_count = await db.scalar(select(func.count(KlmAuditEvent.id)))
        assert audit_count == 0


@pytest.mark.asyncio
async def test_quality_and_account_fields_are_projected_and_audited(session_factory):
    workspace_id, _, _ = await _seed(session_factory)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "waba-template-1",
            "changes": [
                {
                    "field": "phone_number_quality_update",
                    "value": {
                        "metadata": {"phone_number_id": "pn-template-1"},
                        "quality_rating": "YELLOW",
                    },
                },
                {
                    "field": "account_alerts",
                    "value": {"event": "POLICY_ALERT", "status": "OPEN"},
                },
            ],
        }],
    }
    async with session_factory() as db:
        await _build_service(db).process_webhook(payload)
        await db.commit()

    async with session_factory() as db:
        operations = await MetaWebhookOperationsService(db).list_events(
            workspace_id=workspace_id,
        )
        assert len(operations) == 2
        assert {event.event_type for event in operations} == {
            "whatsapp_meta_webhook_phone_number_quality_update",
            "whatsapp_meta_webhook_account_alerts",
        }
        tier = await db.scalar(
            select(WhatsAppConnectionTiers).where(
                WhatsAppConnectionTiers.workspace_id == workspace_id
            )
        )
        assert tier is not None
        assert tier.quality_rating == "amarelo"
        event_types = set(
            (
                await db.scalars(
                    select(KlmAuditEvent.event_type).where(
                        KlmAuditEvent.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        assert "whatsapp_meta_webhook_phone_number_quality_update" in event_types
        assert "whatsapp_meta_webhook_account_alerts" in event_types


@pytest.mark.asyncio
async def test_template_quality_signal_updates_local_operational_snapshot(session_factory):
    _, _, template_id = await _seed(session_factory)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "waba-template-1",
            "changes": [{
                "field": "message_template_quality_update",
                "value": {
                    "message_template_id": "ext-tpl-1",
                    "message_template_name": "lembrete_consulta",
                    "message_template_language": "pt_BR",
                    "quality_score": "RED",
                },
            }],
        }],
    }
    async with session_factory() as db:
        await _build_service(db).process_webhook(payload)
        await db.commit()

    async with session_factory() as db:
        template = await db.get(WhatsAppMetaTemplate, template_id)
        update = template.raw_payload["metaOperationalUpdates"][
            "message_template_quality_update"
        ]
        assert update["payload"]["quality_score"] == "RED"
        assert update["payloadHash"]
