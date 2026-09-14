"""Sprint F2.1 — marcos do funil viram AcquisitionEvent idempotente.

Cobre: referral CTWA no webhook WhatsApp, contrato assinado (ZapSign),
honorário pago (finance) e qualificação do SDR (lead_source + lead_qualified).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.endpoints import webhooks as webhooks_module
from app.core.config import settings
from app.db.base import Base
from app.models.client import Client
from app.models.honorarium_estimate import HonorariumEstimate
from app.models.klm_operations import AcquisitionEvent, OmnichannelThread
from app.models.matter import Matter
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.finance import FinanceEntryCreate, FinanceEntryUpdate
from app.services.finance_service import FinanceService
from app.services.sdr_intake_service import handle_inbound_lead
from app.services.whatsapp.webhook_service import WhatsAppWebhookService

pytestmark = pytest.mark.usefixtures("active_subscription")

TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"

CTWA_REFERRAL = {
    "ctwa_clid": "ARAcLId1234567890abcDEF",
    "source_url": "https://fb.me/anuncio-klm",
    "source_id": "12030405060708090",
    "source_type": "ad",
    "headline": "Fale com um advogado agora",
    "body": "Atendimento imediato pelo WhatsApp",
}


# ────────────────────────────────────────────────────────────────────────────
# Harness do pipeline inbound (padrão de tests/services/test_whatsapp_inbound_pipeline.py)
# ────────────────────────────────────────────────────────────────────────────


class StubConnectionService:
    async def get_connection(self, connection_id: UUID, workspace_id: UUID):
        return SimpleNamespace(
            id=connection_id,
            workspace_id=workspace_id,
            phone_number_id=None,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
        )


class StubAutomationRuleService:
    async def apply_inbound_rules(self, *, contact, inbound_text):
        return None


class StubMessageService:
    async def send_text_direct(self, **_kwargs) -> bool:
        return True


class StubCampaignService:
    async def trigger_follow_up_flow_on_reply(self, **_kwargs):
        return None

    async def sync_delivery_status(self, *_args, **_kwargs) -> None:
        return None


def build_webhook_service(db: AsyncSession) -> WhatsAppWebhookService:
    return WhatsAppWebhookService(
        db,
        connection_service=StubConnectionService(),
        message_service=StubMessageService(),
        automation_rule_service=StubAutomationRuleService(),
        campaign_service=StubCampaignService(),
    )


@pytest_asyncio.fixture
async def inbound_session_factory(tmp_path):
    database_path = tmp_path / "tracking-funnel-events.sqlite3"
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


async def seed_whatsapp_workspace(factory, suffix: str):
    async with factory() as db:
        workspace = Workspace(
            name=f"Tracking {suffix}",
            slug=f"tracking-{suffix}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.flush()
        connection = WhatsAppConnection(
            workspace_id=workspace.id,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
            is_default=True,
            phone_number_id=f"pn-{suffix}",
            status=WaConnectionStatus.connected,
        )
        db.add(connection)
        await db.commit()
        return workspace.id, connection.id


def ctwa_message_payload(wamid: str, *, with_referral: bool) -> tuple[dict, list[dict]]:
    message = {
        "id": wamid,
        "from": "5511988887777",
        "timestamp": "1712345678",
        "type": "text",
        "text": {"body": "Vi o anúncio de vocês"},
    }
    if with_referral:
        message["referral"] = dict(CTWA_REFERRAL)
    return message, [{"wa_id": "5511988887777", "profile": {"name": "Lead CTWA"}}]


@pytest.fixture
def _offline_inbound_pipeline(monkeypatch):
    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: False,
    )
    monkeypatch.setattr(WhatsAppWebhookService, "_maybe_escalate_to_human", no_op)


@pytest.mark.asyncio
async def test_ctwa_referral_emits_conversation_started_and_is_idempotent(
    inbound_session_factory,
    _offline_inbound_pipeline,
):
    workspace_id, connection_id = await seed_whatsapp_workspace(
        inbound_session_factory, "ctwa"
    )
    wamid = "wamid.ctwa-referral-1"
    message_data, contacts_data = ctwa_message_payload(wamid, with_referral=True)

    async with inbound_session_factory() as db:
        await build_webhook_service(db)._handle_inbound_message(
            connection_id, workspace_id, message_data, contacts_data
        )

    async with inbound_session_factory() as db:
        events = (
            await db.scalars(
                select(AcquisitionEvent).where(
                    AcquisitionEvent.workspace_id == workspace_id
                )
            )
        ).all()
        assert len(events) == 1
        event = events[0]
        assert event.event_name == "conversation_started"
        assert event.channel == "whatsapp"
        assert event.dedupe_key == f"ctwa:{wamid}"
        assert event.click_ids == {"ctwa_clid": CTWA_REFERRAL["ctwa_clid"]}
        assert event.event_metadata["adId"] == CTWA_REFERRAL["source_id"]
        assert event.event_metadata["sourceUrl"] == CTWA_REFERRAL["source_url"]
        assert event.event_metadata["sourceType"] == CTWA_REFERRAL["source_type"]

        # Referral persistido no metadata da mensagem e no thread canônico.
        message = await db.scalar(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        )
        assert message is not None
        assert message.message_metadata["referral"] == CTWA_REFERRAL
        assert message.inbound_pipeline["acquisitionCompleted"] is True
        thread_id = UUID(str(event.event_metadata["threadId"]))
        thread = await db.get(OmnichannelThread, thread_id)
        assert thread is not None
        acquisition = thread.thread_metadata["acquisition"]
        assert acquisition["clickIds"] == {"ctwa_clid": CTWA_REFERRAL["ctwa_clid"]}
        assert acquisition["ad"] == CTWA_REFERRAL["source_id"]

    # Reentrega idêntica do webhook não duplica o evento.
    async with inbound_session_factory() as db:
        await build_webhook_service(db)._handle_inbound_message(
            connection_id, workspace_id, message_data, contacts_data
        )
    async with inbound_session_factory() as db:
        total = await db.scalar(
            select(func.count())
            .select_from(AcquisitionEvent)
            .where(AcquisitionEvent.workspace_id == workspace_id)
        )
        assert total == 1


@pytest.mark.asyncio
async def test_inbound_without_referral_records_no_acquisition_event(
    inbound_session_factory,
    _offline_inbound_pipeline,
):
    workspace_id, connection_id = await seed_whatsapp_workspace(
        inbound_session_factory, "plain"
    )
    message_data, contacts_data = ctwa_message_payload(
        "wamid.plain-message-1", with_referral=False
    )

    async with inbound_session_factory() as db:
        await build_webhook_service(db)._handle_inbound_message(
            connection_id, workspace_id, message_data, contacts_data
        )

    async with inbound_session_factory() as db:
        total = await db.scalar(
            select(func.count())
            .select_from(AcquisitionEvent)
            .where(AcquisitionEvent.workspace_id == workspace_id)
        )
        assert total == 0
        message = await db.scalar(
            select(WhatsAppMessage).where(
                WhatsAppMessage.wamid == "wamid.plain-message-1"
            )
        )
        assert message is not None
        assert "referral" not in (message.message_metadata or {})
        assert "acquisitionCompleted" not in (message.inbound_pipeline or {})


# ────────────────────────────────────────────────────────────────────────────
# ZapSign doc_signed → contract_created
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _zapsign_environment(monkeypatch):
    monkeypatch.setattr(
        settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        TEST_INTEGRATION_SECRET_KEY,
    )
    monkeypatch.setattr(webhooks_module, "external_delivery_allowed", lambda: True)


async def _matter_with_zapsign_token(client, db_session, token: str) -> Matter:
    from app.models.finance import (
        IntegrationConnection,
        IntegrationProvider,
        IntegrationStatus,
    )
    from app.services.integration_secrets import protect_integration_settings

    created_client = await client.post(
        "/api/v1/clients",
        json={
            "name": "Cliente ZapSign Tracking",
            "email": f"zapsign-tracking-{uuid4().hex[:8]}@example.com",
        },
    )
    assert created_client.status_code == 201, created_client.text
    created_matter = await client.post(
        "/api/v1/matters",
        json={
            "clientId": created_client.json()["id"],
            "practiceArea": "saude_suplementar",
            "demandType": "Contrato com tracking",
            "columnId": "contrato",
            "microStatus": "contrato_enviado",
        },
    )
    assert created_matter.status_code == 201, created_matter.text
    matter = await db_session.get(Matter, UUID(created_matter.json()["id"]))
    assert matter is not None
    matter.zapsign_doc_token = token
    matter.zapsign_status = "pending"
    db_session.add(
        IntegrationConnection(
            workspace_id=matter.workspace_id,
            provider=IntegrationProvider.zapsign,
            status=IntegrationStatus.connected,
            label="ZapSign",
            settings=protect_integration_settings({"webhookSecret": "webhook-secret"}),
        )
    )
    await db_session.flush()
    return matter


def _zapsign_payload(matter: Matter, token: str) -> dict:
    return {
        "event_type": "doc_signed",
        "token": token,
        "status": "signed",
        "external_id": str(matter.id),
        "name": "Contrato de honorários",
        "signed_file": "https://files.example.test/signed.pdf",
    }


@pytest.mark.asyncio
async def test_zapsign_doc_signed_records_contract_created_once(
    client, db_session, _zapsign_environment
):
    token = "doc-tracking-contract"
    matter = await _matter_with_zapsign_token(client, db_session, token)
    db_session.add(
        HonorariumEstimate(
            matter_id=matter.id,
            contract_type="fixo",
            fixed_fee=5000.0,
            total_estimated_value=7500.0,
        )
    )
    await db_session.flush()

    first = await client.post(
        "/api/v1/webhooks/zapsign",
        json=_zapsign_payload(matter, token),
        headers={"Authorization": "Bearer webhook-secret"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ok"

    second = await client.post(
        "/api/v1/webhooks/zapsign",
        json=_zapsign_payload(matter, token),
        headers={"Authorization": "Bearer webhook-secret"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["deduplicated"] is True

    events = (
        await db_session.scalars(
            select(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == matter.workspace_id,
                AcquisitionEvent.event_name == "contract_created",
            )
        )
    ).all()
    assert len(events) == 1
    event = events[0]
    assert event.dedupe_key == f"zapsign:signed:{matter.id}:{token}"
    assert event.matter_id == matter.id
    assert event.client_id == matter.client_id
    assert event.event_id == token
    assert float(event.value) == 7500.0


@pytest.mark.asyncio
async def test_zapsign_doc_signed_without_estimate_omits_value(
    client, db_session, _zapsign_environment
):
    token = "doc-tracking-no-estimate"
    matter = await _matter_with_zapsign_token(client, db_session, token)

    response = await client.post(
        "/api/v1/webhooks/zapsign",
        json=_zapsign_payload(matter, token),
        headers={"Authorization": "Bearer webhook-secret"},
    )
    assert response.status_code == 200, response.text

    event = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"zapsign:signed:{matter.id}:{token}"
        )
    )
    assert event is not None
    assert event.value is None


# ────────────────────────────────────────────────────────────────────────────
# Finance → payment_confirmed
# ────────────────────────────────────────────────────────────────────────────


async def _seed_matter(db_session, auth_context) -> Matter:
    client_row = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Financeiro",
        phone="5511977776666",
    )
    db_session.add(client_row)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_row.id,
        demand_type="Honorários com tracking",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


@pytest.mark.asyncio
async def test_finance_entry_created_as_paid_records_payment_confirmed(
    db_session, auth_context
):
    matter = await _seed_matter(db_session, auth_context)
    service = FinanceService(db_session)
    entry = await service.create_entry(
        auth_context.workspace_id,
        FinanceEntryCreate(
            matter_id=matter.id,
            title="Honorários iniciais",
            amount=1234.56,
            status="paid",
        ),
    )

    event = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"finance:{entry.id}"
        )
    )
    assert event is not None
    assert event.event_name == "payment_confirmed"
    assert float(event.value) == 1234.56
    assert event.currency == "BRL"
    assert event.matter_id == matter.id
    assert event.client_id == matter.client_id
    assert event.event_metadata["paymentId"] == str(entry.id)


@pytest.mark.asyncio
async def test_finance_entry_update_to_paid_records_payment_confirmed_once(
    db_session, auth_context
):
    matter = await _seed_matter(db_session, auth_context)
    service = FinanceService(db_session)
    entry = await service.create_entry(
        auth_context.workspace_id,
        FinanceEntryCreate(
            matter_id=matter.id,
            title="Parcela de honorários",
            amount=980.0,
        ),
    )
    no_event_yet = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"finance:{entry.id}"
        )
    )
    assert no_event_yet is None

    await service.update_entry(
        auth_context.workspace_id, entry.id, FinanceEntryUpdate(status="paid")
    )
    await service.update_entry(
        auth_context.workspace_id,
        entry.id,
        FinanceEntryUpdate(description="Comprovante anexado"),
    )

    total = await db_session.scalar(
        select(func.count())
        .select_from(AcquisitionEvent)
        .where(AcquisitionEvent.dedupe_key == f"finance:{entry.id}")
    )
    assert total == 1


@pytest.mark.asyncio
async def test_finance_entry_without_matter_does_not_record_event(
    db_session, auth_context
):
    service = FinanceService(db_session)
    entry = await service.create_entry(
        auth_context.workspace_id,
        FinanceEntryCreate(title="Recebível avulso", amount=100.0, status="paid"),
    )
    event = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"finance:{entry.id}"
        )
    )
    assert event is None


# ────────────────────────────────────────────────────────────────────────────
# SDR → lead_source + lead_qualified
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _quiet_sdr_side_effects(monkeypatch):
    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.workers.event_bus.dispatch_new_lead", no_op)
    monkeypatch.setattr("app.workers.event_bus.dispatch_whatsapp_message", no_op)
    monkeypatch.setattr(
        "app.services.knowledge_service.KnowledgeService.index_whatsapp_message",
        no_op,
    )
    monkeypatch.setattr(
        "app.services.conversation_insight_service.ConversationInsightService"
        ".generate_snapshot",
        no_op,
    )


async def _seed_whatsapp_contact(db_session, auth_context) -> WhatsAppContact:
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id=f"pn-sdr-{uuid4().hex[:8]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511966665555",
        name="Lead SDR",
    )
    db_session.add(contact)
    await db_session.flush()
    return contact


@pytest.mark.asyncio
async def test_sdr_new_lead_matter_gets_whatsapp_lead_source(
    db_session, auth_context, _quiet_sdr_side_effects
):
    contact = await _seed_whatsapp_contact(db_session, auth_context)

    await handle_inbound_lead(
        db=db_session,
        contact_id=contact.id,
        phone=contact.phone,
        contact_name=contact.name,
        message_body="Olá, preciso de ajuda jurídica",
        wamid="wamid.sdr-lead-source-1",
    )

    await db_session.refresh(contact)
    assert contact.matter_id is not None
    matter = await db_session.get(Matter, contact.matter_id)
    assert matter is not None
    assert matter.lead_source == "whatsapp"
    # Sem qualificação completa não há lead_qualified.
    event = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"sdr-qualified:{matter.id}"
        )
    )
    assert event is None


@pytest.mark.asyncio
async def test_sdr_completed_qualification_emits_lead_qualified_with_thread_click_ids(
    db_session, auth_context, _quiet_sdr_side_effects
):
    contact = await _seed_whatsapp_contact(db_session, auth_context)
    await handle_inbound_lead(
        db=db_session,
        contact_id=contact.id,
        phone=contact.phone,
        contact_name=contact.name,
        message_body="Primeiro contato",
        wamid="wamid.sdr-qualified-1",
    )
    await db_session.refresh(contact)
    matter = await db_session.get(Matter, contact.matter_id)
    assert matter is not None

    # Worker do SDR concluiu a qualificação; thread canônico tem o click id CTWA.
    matter.sdr_qualification = {"completed": True, "qualification_score": 8}
    thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        client_id=matter.client_id,
        thread_metadata={
            "acquisition": {
                "provider": "meta",
                "clickIds": {"ctwa_clid": CTWA_REFERRAL["ctwa_clid"]},
            }
        },
    )
    db_session.add(thread)
    await db_session.flush()

    await handle_inbound_lead(
        db=db_session,
        contact_id=contact.id,
        phone=contact.phone,
        contact_name=contact.name,
        message_body="Mensagem após qualificação",
        wamid="wamid.sdr-qualified-2",
    )

    events = (
        await db_session.scalars(
            select(AcquisitionEvent).where(
                AcquisitionEvent.dedupe_key == f"sdr-qualified:{matter.id}"
            )
        )
    ).all()
    assert len(events) == 1
    event = events[0]
    assert event.event_name == "lead_qualified"
    assert event.channel == "whatsapp"
    assert event.matter_id == matter.id
    assert event.client_id == matter.client_id
    assert event.click_ids == {"ctwa_clid": CTWA_REFERRAL["ctwa_clid"]}
    assert event.event_metadata["threadId"] == str(thread.id)

    # Reentrada não duplica o marco.
    await handle_inbound_lead(
        db=db_session,
        contact_id=contact.id,
        phone=contact.phone,
        contact_name=contact.name,
        message_body="Mais uma mensagem",
        wamid="wamid.sdr-qualified-3",
    )
    total = await db_session.scalar(
        select(func.count())
        .select_from(AcquisitionEvent)
        .where(AcquisitionEvent.dedupe_key == f"sdr-qualified:{matter.id}")
    )
    assert total == 1
