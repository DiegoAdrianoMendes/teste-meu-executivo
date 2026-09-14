"""Sprint W1.11 + W1.8 (parte de status/erros — P1-6 da auditoria Meta).

Cobre:
- opt-out inbound automático (SAIR/PARE/PARAR/STOP/CANCELAR/REMOVER e as duas
  frases-chave), incluindo o caso de falso positivo documentado na auditoria
  ("não quero perder o prazo" não deve suprimir);
- idempotência da supressão criada por opt-out inbound;
- persistência do errors[] completo no status `failed` do webhook;
- 131050 no status `failed` cria supressão permanente; 131049 só marca
  cap-hit, sem supressão.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import WhatsAppSuppressionEntry
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.whatsapp.webhook_service import (
    WhatsAppWebhookService,
    _detect_inbound_opt_out,
)


class StubConnectionService:
    async def get_connection(self, connection_id, workspace_id):
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
    def __init__(self, calls: Counter | None = None) -> None:
        self.calls = calls if calls is not None else Counter()

    async def trigger_follow_up_flow_on_reply(self, **_kwargs):
        return None

    async def sync_delivery_status(self, *_args, **_kwargs) -> None:
        self.calls["campaign_status"] += 1


def build_service(db: AsyncSession, campaign_service=None) -> WhatsAppWebhookService:
    return WhatsAppWebhookService(
        db,
        connection_service=StubConnectionService(),
        message_service=StubMessageService(),
        automation_rule_service=StubAutomationRuleService(),
        campaign_service=campaign_service or StubCampaignService(),
    )


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    database_path = tmp_path / "whatsapp-opt-out.sqlite3"
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


async def _seed_workspace_and_connection(factory, suffix: str):
    async with factory() as db:
        workspace = Workspace(
            name=f"Opt-out {suffix}",
            slug=f"opt-out-{suffix}",
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


def _inbound_text_payload(*, wamid: str, phone: str, body: str) -> tuple[dict, list[dict]]:
    return (
        {
            "id": wamid,
            "from": phone,
            "timestamp": "1712345678",
            "type": "text",
            "text": {"body": body},
        },
        [{"wa_id": phone, "profile": {"name": "Cliente Opt-out"}}],
    )


# ---------------------------------------------------------------------------
# Detecção de gatilhos (unidade, sem banco)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "SAIR",
        "pare",
        "Por favor, PARAR com isso",
        "stop",
        "cancelar",
        "quero remover meu numero",
        "não quero mais",
        "Não Quero Mais receber mensagens",
        "nao quero receber",
    ],
)
def test_detect_inbound_opt_out_matches_known_variants(body: str) -> None:
    assert _detect_inbound_opt_out(body) is not None


@pytest.mark.parametrize(
    "body",
    [
        "não quero perder o prazo",
        "vou comparecer na audiência amanhã",
        "obrigado pela atenção",
        "",
    ],
)
def test_detect_inbound_opt_out_ignores_false_positives(body: str) -> None:
    assert _detect_inbound_opt_out(body) is None


# ---------------------------------------------------------------------------
# Fluxo completo: mensagem inbound de opt-out cria supressão e não bloqueia
# o atendimento normal.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_opt_out_creates_permanent_suppression_without_blocking_pipeline(
    session_factory,
):
    workspace_id, connection_id = await _seed_workspace_and_connection(
        session_factory, "trigger"
    )
    phone = "5511988887777"
    wamid = "wamid-opt-out-1"
    message_data, contacts_data = _inbound_text_payload(
        wamid=wamid, phone=phone, body="PARE de me mandar mensagem"
    )

    async with session_factory() as db:
        service = build_service(db)
        await service._handle_inbound_message(
            connection_id, workspace_id, message_data, contacts_data
        )

        message = await db.scalar(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        )
        assert message is not None
        assert message.direction == WaMessageDirection.inbound
        assert message.message_metadata["optOutDetected"] is True
        assert message.message_metadata["optOutTrigger"] == "pare"

        entry = (
            await db.execute(
                select(WhatsAppSuppressionEntry).where(
                    WhatsAppSuppressionEntry.workspace_id == workspace_id,
                    WhatsAppSuppressionEntry.phone == phone,
                )
            )
        ).scalars().first()
        assert entry is not None
        assert entry.is_active is True
        assert entry.reason == "inbound_opt_out"
        assert entry.details["wamid"] == wamid
        assert entry.details["triggerMatched"] == "pare"


@pytest.mark.asyncio
async def test_inbound_opt_out_false_positive_message_is_not_suppressed(session_factory):
    workspace_id, connection_id = await _seed_workspace_and_connection(
        session_factory, "false-positive"
    )
    phone = "5511988887778"
    wamid = "wamid-opt-out-2"
    message_data, contacts_data = _inbound_text_payload(
        wamid=wamid, phone=phone, body="não quero perder o prazo da audiência"
    )

    async with session_factory() as db:
        service = build_service(db)
        await service._handle_inbound_message(
            connection_id, workspace_id, message_data, contacts_data
        )

        message = await db.scalar(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        )
        assert message is not None
        assert not message.message_metadata.get("optOutDetected")

        entry = (
            await db.execute(
                select(WhatsAppSuppressionEntry).where(
                    WhatsAppSuppressionEntry.workspace_id == workspace_id,
                    WhatsAppSuppressionEntry.phone == phone,
                )
            )
        ).scalars().first()
        assert entry is None


@pytest.mark.asyncio
async def test_inbound_opt_out_suppression_upsert_is_idempotent_on_retry(session_factory):
    workspace_id, connection_id = await _seed_workspace_and_connection(
        session_factory, "idempotent"
    )
    phone = "5511988887779"
    wamid = "wamid-opt-out-3"
    message_data, contacts_data = _inbound_text_payload(
        wamid=wamid, phone=phone, body="SAIR"
    )

    async with session_factory() as db:
        service = build_service(db)
        # Simula reentrega do webhook: processa a mesma mensagem duas vezes.
        await service._handle_inbound_message(
            connection_id, workspace_id, message_data, contacts_data
        )
        await service._handle_inbound_message(
            connection_id, workspace_id, message_data, contacts_data
        )

        entries = (
            await db.execute(
                select(WhatsAppSuppressionEntry).where(
                    WhatsAppSuppressionEntry.workspace_id == workspace_id,
                    WhatsAppSuppressionEntry.phone == phone,
                )
            )
        ).scalars().all()
        assert len(entries) == 1
        assert entries[0].is_active is True


# ---------------------------------------------------------------------------
# Status `failed` do webhook: errors[] persistido + reação por código.
# ---------------------------------------------------------------------------


async def _seed_outbound_message(factory, *, suffix: str, wamid: str):
    async with factory() as db:
        workspace = Workspace(
            name=f"Status {suffix}",
            slug=f"status-{suffix}",
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
        await db.flush()
        phone = "5511977776666"
        contact = WhatsAppContact(
            workspace_id=workspace.id,
            whatsapp_connection_id=connection.id,
            phone=phone,
            name="Cliente Status",
        )
        db.add(contact)
        await db.flush()
        message = WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=connection.id,
            wamid=wamid,
            direction=WaMessageDirection.outbound,
            status=WaMessageStatus.sent,
            body="Mensagem de marketing",
            timestamp=datetime.now(UTC),
        )
        db.add(message)
        await db.commit()
        return SimpleNamespace(
            workspace_id=workspace.id,
            connection_id=connection.id,
            contact_id=contact.id,
            phone=phone,
        )


@pytest.mark.asyncio
async def test_status_failed_persists_full_errors_array(session_factory, monkeypatch):
    wamid = "wamid-status-generic-failure"
    seed = await _seed_outbound_message(session_factory, suffix="generic", wamid=wamid)
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    status_data = {
        "id": wamid,
        "status": "failed",
        "errors": [
            {"code": 470, "title": "Message expired", "message": "detail-a"},
        ],
    }

    async with session_factory() as db:
        service = build_service(db)
        await service._handle_status_update(
            seed.connection_id, status_data, workspace_id=seed.workspace_id
        )

        message = await db.scalar(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        )
        assert message.status == WaMessageStatus.failed
        assert message.message_metadata["lastStatusErrors"] == [
            {"code": 470, "title": "Message expired", "message": "detail-a"}
        ]


@pytest.mark.asyncio
async def test_status_failed_131050_creates_permanent_suppression(session_factory, monkeypatch):
    wamid = "wamid-status-131050"
    seed = await _seed_outbound_message(session_factory, suffix="opt-out-status", wamid=wamid)
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    status_data = {
        "id": wamid,
        "status": "failed",
        "errors": [
            {
                "code": 131050,
                "title": "Message failed to send because... user opted out",
                "message": "opt-out",
            }
        ],
    }

    async with session_factory() as db:
        service = build_service(db)
        await service._handle_status_update(
            seed.connection_id, status_data, workspace_id=seed.workspace_id
        )

        entry = (
            await db.execute(
                select(WhatsAppSuppressionEntry).where(
                    WhatsAppSuppressionEntry.workspace_id == seed.workspace_id,
                    WhatsAppSuppressionEntry.phone == seed.phone,
                )
            )
        ).scalars().first()
        assert entry is not None
        assert entry.is_active is True
        assert entry.reason == "meta_opt_out_status"
        assert entry.details["providerCode"] == 131050


@pytest.mark.asyncio
async def test_exact_131050_status_replay_does_not_repeat_audit_or_campaign_effect(
    session_factory,
    monkeypatch,
):
    wamid = "wamid-status-131050-replay"
    seed = await _seed_outbound_message(session_factory, suffix="status-replay", wamid=wamid)
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    status_data = {
        "id": wamid,
        "status": "failed",
        "timestamp": "1712345678",
        "errors": [
            {
                "code": 131050,
                "title": "Message failed because the user opted out",
                "message": "opt-out",
            }
        ],
    }
    campaign_calls = Counter()

    async with session_factory() as db:
        service = build_service(db, StubCampaignService(campaign_calls))

        # Equivalent to outer worker-state settlement failing after the domain
        # transaction committed: the same signed receipt is processed again.
        await service._handle_status_update(
            seed.connection_id,
            status_data,
            workspace_id=seed.workspace_id,
        )
        await service._handle_status_update(
            seed.connection_id,
            status_data,
            workspace_id=seed.workspace_id,
        )

        message = await db.scalar(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        )
        audit_events = (
            await db.scalars(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == seed.workspace_id,
                    KlmAuditEvent.event_type == "whatsapp_suppression_created",
                )
            )
        ).all()

        assert message is not None
        assert len(message.message_metadata["providerStatusReceiptKeys"]) == 1
        assert campaign_calls["campaign_status"] == 1
        assert len(audit_events) == 1


@pytest.mark.asyncio
async def test_status_failed_131049_marks_cap_hit_without_suppression(session_factory, monkeypatch):
    wamid = "wamid-status-131049"
    seed = await _seed_outbound_message(session_factory, suffix="cap-hit", wamid=wamid)
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    status_data = {
        "id": wamid,
        "status": "failed",
        "errors": [
            {"code": 131049, "title": "Marketing message limit", "message": "cap"}
        ],
    }

    async with session_factory() as db:
        service = build_service(db)
        await service._handle_status_update(
            seed.connection_id, status_data, workspace_id=seed.workspace_id
        )

        message = await db.scalar(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        )
        assert message.message_metadata["capHit"]["providerCode"] == 131049

        entry = (
            await db.execute(
                select(WhatsAppSuppressionEntry).where(
                    WhatsAppSuppressionEntry.workspace_id == seed.workspace_id,
                    WhatsAppSuppressionEntry.phone == seed.phone,
                )
            )
        ).scalars().first()
        assert entry is None
