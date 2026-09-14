from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base
from app.models.client import Client
from app.models.matter import Matter
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.klm_operations_service import KlmOperationsService
from app.services.whatsapp.webhook_service import (
    InboundPipelineLeaseLostError,
    WhatsAppWebhookService,
)
from app.workers.whatsapp_inbound_tasks import (
    _async_reconcile_workspace_media,
    _pending_media_message_ids,
)


@dataclass(frozen=True)
class SeededInbound:
    workspace_id: UUID
    connection_id: UUID
    contact_id: UUID
    message_id: UUID
    wamid: str
    phone: str


class StubConnectionService:
    async def get_connection(self, connection_id: UUID, workspace_id: UUID):
        return SimpleNamespace(
            id=connection_id,
            workspace_id=workspace_id,
            phone_number_id=None,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
        )


class StubAutomationRuleService:
    def __init__(self, calls: Counter, outcome: dict | None = None) -> None:
        self.calls = calls
        self.outcome = outcome

    async def apply_inbound_rules(self, *, contact, inbound_text):
        self.calls["automation"] += 1
        return self.outcome


class StubMessageService:
    def __init__(self, calls: Counter, behavior: str = "confirmed") -> None:
        self.calls = calls
        self.behavior = behavior

    async def send_text_direct(self, **_kwargs) -> bool:
        self.calls["send"] += 1
        if self.behavior == "timeout":
            raise TimeoutError("provider response was not observed")
        return self.behavior == "confirmed"


class StubCampaignService:
    def __init__(self, calls: Counter) -> None:
        self.calls = calls

    async def trigger_follow_up_flow_on_reply(self, **_kwargs):
        self.calls["campaign_flow"] += 1
        return None

    async def sync_delivery_status(self, *_args, **_kwargs) -> None:
        self.calls["campaign_status"] += 1


def build_service(
    db: AsyncSession,
    calls: Counter,
    *,
    automation_outcome: dict | None = None,
    send_behavior: str = "confirmed",
) -> WhatsAppWebhookService:
    return WhatsAppWebhookService(
        db,
        connection_service=StubConnectionService(),
        message_service=StubMessageService(calls, send_behavior),
        automation_rule_service=StubAutomationRuleService(calls, automation_outcome),
        campaign_service=StubCampaignService(calls),
    )


@pytest_asyncio.fixture
async def inbound_session_factory(tmp_path):
    database_path = tmp_path / "whatsapp-inbound-leases.sqlite3"
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


async def seed_inbound_message(
    factory,
    suffix: str,
    *,
    with_matter: bool,
) -> SeededInbound:
    async with factory() as db:
        workspace = Workspace(
            name=f"Inbound {suffix}",
            slug=f"inbound-{suffix}",
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

        matter_id = None
        if with_matter:
            client = Client(
                workspace_id=workspace.id,
                name=f"Cliente {suffix}",
                phone="5511999999999",
            )
            db.add(client)
            await db.flush()
            matter = Matter(
                workspace_id=workspace.id,
                client_id=client.id,
                demand_type="Teste de lease inbound",
            )
            db.add(matter)
            await db.flush()
            matter_id = matter.id

        phone = "5511999999999"
        contact = WhatsAppContact(
            workspace_id=workspace.id,
            whatsapp_connection_id=connection.id,
            phone=phone,
            name="Cliente Lease",
            matter_id=matter_id,
        )
        db.add(contact)
        await db.flush()

        wamid = f"wamid-{suffix}"
        message = WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=connection.id,
            wamid=wamid,
            direction=WaMessageDirection.inbound,
            status=WaMessageStatus.delivered,
            body="Preciso de atendimento",
            timestamp=datetime.now(UTC),
            inbound_pipeline={"state": "accepted", "attempt": 0},
        )
        db.add(message)
        await db.commit()
        return SeededInbound(
            workspace_id=workspace.id,
            connection_id=connection.id,
            contact_id=contact.id,
            message_id=message.id,
            wamid=wamid,
            phone=phone,
        )


def inbound_payload(seed: SeededInbound) -> tuple[dict, list[dict]]:
    return (
        {
            "id": seed.wamid,
            "from": seed.phone,
            "timestamp": "1712345678",
            "type": "text",
            "text": {"body": "Preciso de atendimento"},
        },
        [{"wa_id": seed.phone, "profile": {"name": "Cliente Lease"}}],
    )


@pytest.mark.asyncio
async def test_inbound_meta_context_is_forwarded_to_canonical_reply_resolution(
    inbound_session_factory,
    monkeypatch,
):
    seed = await seed_inbound_message(
        inbound_session_factory,
        "provider-reply-context",
        with_matter=False,
    )
    captured: dict = {}
    reference_id = uuid4()
    target_message_id = uuid4()

    async def fake_resolve_provider_reply_context(_service, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id=reference_id,
            target_message_id=target_message_id,
        )

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: False,
    )
    monkeypatch.setattr(
        KlmOperationsService,
        "resolve_provider_reply_context",
        fake_resolve_provider_reply_context,
    )
    monkeypatch.setattr(WhatsAppWebhookService, "_maybe_escalate_to_human", no_op)
    message_data, contacts_data = inbound_payload(seed)
    message_data["context"] = {"id": "wamid.provider.original.context"}

    async with inbound_session_factory() as db:
        await build_service(db, Counter())._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            message_data,
            contacts_data,
        )
        message = await db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        assert message.message_metadata["providerContext"] == message_data["context"]
        assert message.inbound_pipeline["contextCompleted"] is True
        assert message.inbound_pipeline["contextResolutionState"] == (
            "resolved_same_thread"
        )
        assert message.inbound_pipeline["contextReferenceId"] == str(reference_id)
        assert message.inbound_pipeline["contextTargetMessageId"] == str(
            target_message_id
        )

    assert captured["workspace_id"] == seed.workspace_id
    assert captured["provider_message_id"] == "wamid.provider.original.context"
    assert captured["correlation_id"] == (
        f"{seed.wamid}:wamid.provider.original.context"
    )
    assert captured["source_message_id"] is not None
    assert captured["thread_id"] is not None


@pytest.mark.asyncio
async def test_inbound_meta_media_id_is_never_promoted_or_delivered_as_storage(
    inbound_session_factory,
    monkeypatch,
):
    seed = await seed_inbound_message(
        inbound_session_factory,
        "media-storage-binding",
        with_matter=True,
    )
    async def forbidden_upload(**_kwargs):
        raise AssertionError("webhook must not call provider/storage reconciliation inline")

    async def forbidden_stage(*_args, **_kwargs):
        raise AssertionError("pending local media must stop the inbound pipeline")

    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.sdr_intake_service.upload_media_to_s3",
        forbidden_upload,
    )
    monkeypatch.setattr(
        "app.services.knowledge_service.KnowledgeService.index_whatsapp_message",
        forbidden_stage,
    )
    monkeypatch.setattr(
        "app.services.sdr_intake_service.handle_inbound_lead",
        forbidden_stage,
    )
    monkeypatch.setattr(
        WhatsAppWebhookService,
        "_maybe_escalate_to_human",
        forbidden_stage,
    )

    async with inbound_session_factory() as db:
        message = await db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        message.media_url = "meta-media-id"
        message.media_type = "document"
        message.inbound_pipeline = {
            "state": "accepted",
            "attempt": 0,
            "sourceMediaId": "meta-media-id",
        }
        await db.commit()

        service = build_service(db, Counter())
        await service._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            {
                "id": seed.wamid,
                "from": seed.phone,
                "timestamp": "1712345678",
                "type": "document",
                "document": {"id": "meta-media-id", "caption": "Laudo"},
            },
            [{"wa_id": seed.phone, "profile": {"name": "Cliente Lease"}}],
        )

        await db.refresh(message)
        assert message.media_url is None
        assert message.inbound_pipeline["sourceMediaId"] == "meta-media-id"
        assert message.inbound_pipeline["mediaStorage"] == {
            "state": "retry_required",
            "storageKey": None,
            "objectId": None,
            "version": None,
            "reason": "unversioned_media_reference",
            "retryable": True,
            "reconciliationRequired": True,
        }
        assert message.inbound_pipeline["state"] == "reconciliation_required"
        assert message.inbound_pipeline["reconciliationKind"] == "media_storage"
        assert message.inbound_pipeline["mediaRetry"]["state"] == "scheduled"
        assert message.inbound_pipeline["mediaRetry"]["attempt"] == 0
        assert "completedAt" not in message.inbound_pipeline
        assert message.inbound_lease_generation == 1

        await service._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            {
                "id": seed.wamid,
                "from": seed.phone,
                "timestamp": "1712345678",
                "type": "document",
                "document": {"id": "meta-media-id", "caption": "Laudo"},
            },
            [{"wa_id": seed.phone, "profile": {"name": "Cliente Lease"}}],
        )
        await db.refresh(message)
        assert message.inbound_pipeline["state"] == "reconciliation_required"
        assert message.inbound_lease_generation == 2


@pytest.mark.asyncio
async def test_production_marks_unversioned_inbound_media_recoverable_for_retry(
    inbound_session_factory,
    monkeypatch,
):
    seed = await seed_inbound_message(
        inbound_session_factory,
        "media-production-unavailable",
        with_matter=True,
    )

    async def forbidden_upload(**_kwargs):
        raise AssertionError("initial handler must persist retry before downloading media")

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.sdr_intake_service.upload_media_to_s3",
        forbidden_upload,
    )
    monkeypatch.setattr(
        "app.services.knowledge_service.KnowledgeService.index_whatsapp_message",
        no_op,
    )
    monkeypatch.setattr(
        "app.services.sdr_intake_service.handle_inbound_lead",
        no_op,
    )
    monkeypatch.setattr(WhatsAppWebhookService, "_maybe_escalate_to_human", no_op)

    async with inbound_session_factory() as db:
        message = await db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        message.media_url = "meta-media-id"
        message.media_type = "document"
        message.inbound_pipeline = {
            "state": "accepted",
            "attempt": 0,
            "sourceMediaId": "meta-media-id",
        }
        await db.commit()

        await build_service(db, Counter())._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            {
                "id": seed.wamid,
                "from": seed.phone,
                "timestamp": "1712345678",
                "type": "document",
                "document": {"id": "meta-media-id", "caption": "Laudo"},
            },
            [{"wa_id": seed.phone, "profile": {"name": "Cliente Lease"}}],
        )
        await db.refresh(message)
        assert message.media_url is None
        assert message.inbound_pipeline["state"] == "reconciliation_required"
        assert message.inbound_pipeline["mediaStorage"] == {
            "state": "retry_required",
            "storageKey": None,
            "objectId": None,
            "version": None,
            "reason": "storage_object_missing",
            "retryable": True,
            "reconciliationRequired": True,
        }
        assert message.inbound_pipeline["mediaRetry"]["state"] == "scheduled"


@pytest.mark.asyncio
async def test_durable_media_retry_uses_meta_id_only_for_fetch_and_resumes_pipeline(
    inbound_session_factory,
    monkeypatch,
):
    seed = await seed_inbound_message(
        inbound_session_factory,
        "media-durable-retry",
        with_matter=True,
    )
    calls = Counter()
    source_media_id = "meta-media-id-durable"
    object_id = uuid4()
    storage_key = (
        f"tenants/{seed.workspace_id.hex}/objects/{object_id.hex}/1/payload"
    )

    async def no_op_rag(*_args, **_kwargs):
        calls["rag"] += 1

    async def no_op_sdr(**_kwargs):
        calls["sdr"] += 1

    async def no_op_escalation(self, contact, inbound_text):
        calls["escalation"] += 1

    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.whatsapp_inbound_tasks.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.knowledge_service.KnowledgeService.index_whatsapp_message",
        no_op_rag,
    )
    monkeypatch.setattr(
        "app.services.sdr_intake_service.handle_inbound_lead",
        no_op_sdr,
    )
    monkeypatch.setattr(
        WhatsAppWebhookService,
        "_maybe_escalate_to_human",
        no_op_escalation,
    )

    async with inbound_session_factory() as db:
        message = await db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        message.media_type = "document"
        message.inbound_pipeline = {
            "state": "accepted",
            "attempt": 0,
            "sourceMediaId": source_media_id,
            "mediaStorage": {
                "state": "retry_required",
                "storageKey": None,
                "objectId": None,
                "version": None,
                "reason": "versioned_storage_not_reconciled",
                "retryable": True,
                "reconciliationRequired": True,
            },
        }
        await db.commit()
        await build_service(db, calls)._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            {
                "id": seed.wamid,
                "from": seed.phone,
                "timestamp": "1712345678",
                "type": "document",
                "document": {"id": source_media_id, "caption": "Laudo"},
            },
            [{"wa_id": seed.phone, "profile": {"name": "Cliente Lease"}}],
        )
        await db.refresh(message)
        pipeline = dict(message.inbound_pipeline)
        retry = dict(pipeline["mediaRetry"])
        retry["nextAttemptAt"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
        pipeline["mediaRetry"] = retry
        message.inbound_pipeline = pipeline
        db.add(
            StorageObject(
                workspace_id=seed.workspace_id,
                object_id=object_id,
                version=1,
                original_filename="whatsapp-document.pdf",
                declared_content_type="application/pdf",
                detected_content_type="application/pdf",
                byte_size=128,
                checksum_sha256="a" * 64,
                storage_key=storage_key,
                state=StorageObjectState.available,
                av_status=StorageAVStatus.simulated_unverified,
            )
        )
        await db.commit()

    async with inbound_session_factory() as db:
        assert await _pending_media_message_ids(
            db,
            workspace_id=seed.workspace_id,
            now=datetime.now(UTC),
        ) == [
            seed.message_id
        ]

    async def fake_uploader(**kwargs):
        calls["uploader"] += 1
        assert kwargs["media_id"] == source_media_id
        stored_message = await kwargs["db"].get(WhatsAppMessage, seed.message_id)
        assert stored_message is not None
        assert stored_message.media_url is None
        return storage_key

    completed = await _async_reconcile_workspace_media(
        seed.workspace_id,
        session_factory=inbound_session_factory,
        service_factory=lambda db: build_service(db, calls),
        uploader=fake_uploader,
    )

    assert completed == 1
    async with inbound_session_factory() as db:
        message = await db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        assert message.media_url == storage_key
        assert message.inbound_pipeline["state"] == "completed"
        assert message.inbound_pipeline["mediaStorage"]["state"] == "available"
        assert message.inbound_pipeline["mediaRetry"]["state"] == "resolved"
    assert calls == {
        "uploader": 1,
        "campaign_flow": 1,
        "rag": 1,
        "sdr": 1,
        "automation": 1,
        "escalation": 1,
    }


@pytest.mark.asyncio
async def test_pending_media_sql_limit_is_applied_after_tenant_binding_on_sqlite(
    inbound_session_factory,
):
    target = await seed_inbound_message(
        inbound_session_factory,
        "tenant-limit-target",
        with_matter=False,
    )
    foreign = await seed_inbound_message(
        inbound_session_factory,
        "tenant-limit-foreign",
        with_matter=False,
    )
    due_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    async with inbound_session_factory() as db:
        target_message = await db.get(WhatsAppMessage, target.message_id)
        assert target_message is not None
        target_message.media_type = "document"
        target_message.timestamp = datetime.now(UTC)
        target_message.inbound_pipeline = {
            "state": "reconciliation_required",
            "mediaStorage": {
                "state": "retry_required",
                "reconciliationRequired": True,
            },
            "mediaRetry": {"nextAttemptAt": due_at},
        }
        for index in range(101):
            db.add(
                WhatsAppMessage(
                    contact_id=foreign.contact_id,
                    whatsapp_connection_id=foreign.connection_id,
                    wamid=f"wamid-foreign-limit-{index}",
                    direction=WaMessageDirection.inbound,
                    status=WaMessageStatus.delivered,
                    body="[document]",
                    media_type="document",
                    timestamp=datetime.now(UTC) - timedelta(hours=1, seconds=index),
                    inbound_pipeline={
                        "state": "reconciliation_required",
                        "mediaStorage": {
                            "state": "retry_required",
                            "reconciliationRequired": True,
                        },
                        "mediaRetry": {"nextAttemptAt": due_at},
                    },
                )
            )
        await db.commit()

    async with inbound_session_factory() as db:
        pending = await _pending_media_message_ids(
            db,
            workspace_id=target.workspace_id,
            now=datetime.now(UTC),
        )

    assert pending == [target.message_id]


@pytest.mark.asyncio
async def test_concurrent_real_sessions_execute_a_single_inbound_pipeline(
    inbound_session_factory,
    monkeypatch,
):
    seed = await seed_inbound_message(
        inbound_session_factory,
        "concurrent",
        with_matter=True,
    )
    calls = Counter()
    rag_started = asyncio.Event()
    release_rag = asyncio.Event()

    async def blocking_rag(*_args, **_kwargs):
        calls["rag"] += 1
        rag_started.set()
        await release_rag.wait()

    async def fake_sdr(**_kwargs):
        calls["sdr"] += 1

    async def fake_escalation(self, contact, inbound_text):
        calls["escalation"] += 1

    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.knowledge_service.KnowledgeService.index_whatsapp_message",
        blocking_rag,
    )
    monkeypatch.setattr(
        "app.services.sdr_intake_service.handle_inbound_lead",
        fake_sdr,
    )
    monkeypatch.setattr(
        WhatsAppWebhookService,
        "_maybe_escalate_to_human",
        fake_escalation,
    )
    message_data, contacts_data = inbound_payload(seed)

    async with inbound_session_factory() as first_db, inbound_session_factory() as second_db:
        first_service = build_service(first_db, calls)
        second_service = build_service(second_db, calls)
        first_worker = asyncio.create_task(
            first_service._handle_inbound_message(
                seed.connection_id,
                seed.workspace_id,
                message_data,
                contacts_data,
            )
        )
        await asyncio.wait_for(rag_started.wait(), timeout=5)
        try:
            await asyncio.wait_for(
                second_service._handle_inbound_message(
                    seed.connection_id,
                    seed.workspace_id,
                    message_data,
                    contacts_data,
                ),
                timeout=5,
            )
        finally:
            release_rag.set()
        await asyncio.wait_for(first_worker, timeout=5)

        await second_service._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            message_data,
            contacts_data,
        )

    async with inbound_session_factory() as verification_db:
        message = await verification_db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        assert message.inbound_pipeline["state"] == "completed"
        assert message.inbound_pipeline["attempt"] == 1
        assert message.inbound_lease_generation == 1
    assert calls == {
        "campaign_flow": 1,
        "rag": 1,
        "sdr": 1,
        "automation": 1,
        "escalation": 1,
    }


@pytest.mark.asyncio
async def test_expired_lease_is_recovered_and_stale_checkpoint_is_fenced(
    inbound_session_factory,
):
    seed = await seed_inbound_message(
        inbound_session_factory,
        "fencing",
        with_matter=False,
    )
    calls = Counter()
    async with inbound_session_factory() as first_db, inbound_session_factory() as second_db:
        first_service = build_service(first_db, calls)
        second_service = build_service(second_db, calls)
        first_lease = await first_service._acquire_inbound_pipeline_lease(seed.message_id)
        assert first_lease is not None
        first_message = await first_db.get(WhatsAppMessage, seed.message_id)
        assert first_message is not None
        await first_service._checkpoint_inbound_pipeline(
            first_message,
            first_lease,
            "processing",
            owner="first",
        )

        assert await second_service._acquire_inbound_pipeline_lease(seed.message_id) is None
        await second_db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == seed.message_id,
                WhatsAppMessage.inbound_lease_token == first_lease.token,
            )
            .values(
                inbound_lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await second_db.commit()

        current_lease = await second_service._acquire_inbound_pipeline_lease(seed.message_id)
        assert current_lease is not None
        assert current_lease.generation == first_lease.generation + 1
        current_message = await second_db.scalar(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.id == seed.message_id)
            .execution_options(populate_existing=True)
        )
        assert current_message is not None

        with pytest.raises(InboundPipelineLeaseLostError):
            await first_service._checkpoint_inbound_pipeline(
                first_message,
                first_lease,
                "completed",
                owner="stale",
            )
        await second_service._checkpoint_inbound_pipeline(
            current_message,
            current_lease,
            "processing",
            owner="current",
        )
        assert await first_service._acquire_inbound_pipeline_lease(seed.message_id) is None

    async with inbound_session_factory() as verification_db:
        message = await verification_db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        assert message.inbound_pipeline["owner"] == "current"
        assert message.inbound_pipeline["state"] == "processing"
        assert message.inbound_lease_token == current_lease.token
        assert message.inbound_lease_generation == current_lease.generation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("send_behavior", "expected_error_type"),
    [
        ("timeout", "TimeoutError"),
        ("unconfirmed", "UnconfirmedDirectSend"),
    ],
)
async def test_uncertain_automation_send_requires_reconciliation_without_replay(
    inbound_session_factory,
    monkeypatch,
    send_behavior,
    expected_error_type,
):
    seed = await seed_inbound_message(
        inbound_session_factory,
        f"send-{send_behavior}",
        with_matter=False,
    )
    calls = Counter()

    async def fake_sdr(**_kwargs):
        calls["sdr"] += 1

    async def forbidden_escalation(self, contact, inbound_text):
        raise AssertionError("uncertain automation delivery must stop the pipeline")

    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.sdr_intake_service.handle_inbound_lead",
        fake_sdr,
    )
    monkeypatch.setattr(
        WhatsAppWebhookService,
        "_maybe_escalate_to_human",
        forbidden_escalation,
    )
    message_data, contacts_data = inbound_payload(seed)

    async with inbound_session_factory() as db:
        service = build_service(
            db,
            calls,
            automation_outcome={"replyText": "Resposta automática", "label": "Regra"},
            send_behavior=send_behavior,
        )
        await service._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            message_data,
            contacts_data,
        )
        await service._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            message_data,
            contacts_data,
        )

    async with inbound_session_factory() as verification_db:
        message = await verification_db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        pipeline = message.inbound_pipeline
        assert pipeline["state"] == "reconciliation_required"
        assert pipeline["reconciliationRequired"] is True
        assert pipeline["uncertainStage"] == "automation_send"
        assert pipeline["automationDeliveryOutcome"] == "unknown"
        assert pipeline["automationSendLastErrorType"] == expected_error_type
        assert pipeline["automationCompleted"] is False
        assert "completedAt" not in pipeline
        assert message.inbound_lease_generation == 1
    assert calls == {
        "campaign_flow": 1,
        "sdr": 1,
        "automation": 1,
        "send": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing_stage", "retry_state", "expected_rag_calls", "expected_sdr_calls"),
    [
        ("rag", "rag_retryable", 2, 1),
        ("sdr", "sdr_retryable", 1, 2),
    ],
)
async def test_rag_and_sdr_failures_retry_only_unfinished_stages(
    inbound_session_factory,
    monkeypatch,
    failing_stage,
    retry_state,
    expected_rag_calls,
    expected_sdr_calls,
):
    seed = await seed_inbound_message(
        inbound_session_factory,
        f"retry-{failing_stage}",
        with_matter=True,
    )
    calls = Counter()

    async def fake_rag(*_args, **_kwargs):
        calls["rag"] += 1
        if failing_stage == "rag" and calls["rag"] == 1:
            raise RuntimeError("injected RAG failure")

    async def fake_sdr(**_kwargs):
        calls["sdr"] += 1
        if failing_stage == "sdr" and calls["sdr"] == 1:
            raise RuntimeError("injected SDR failure")

    async def fake_escalation(self, contact, inbound_text):
        calls["escalation"] += 1

    monkeypatch.setattr(
        "app.services.whatsapp.webhook_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.knowledge_service.KnowledgeService.index_whatsapp_message",
        fake_rag,
    )
    monkeypatch.setattr(
        "app.services.sdr_intake_service.handle_inbound_lead",
        fake_sdr,
    )
    monkeypatch.setattr(
        WhatsAppWebhookService,
        "_maybe_escalate_to_human",
        fake_escalation,
    )
    message_data, contacts_data = inbound_payload(seed)

    async with inbound_session_factory() as db:
        service = build_service(db, calls)
        with pytest.raises(RuntimeError, match=f"injected {failing_stage.upper()} failure"):
            await service._handle_inbound_message(
                seed.connection_id,
                seed.workspace_id,
                message_data,
                contacts_data,
            )
        failed_message = await db.scalar(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.id == seed.message_id)
            .execution_options(populate_existing=True)
        )
        assert failed_message is not None
        assert failed_message.inbound_pipeline["state"] == retry_state

        await service._handle_inbound_message(
            seed.connection_id,
            seed.workspace_id,
            message_data,
            contacts_data,
        )

    async with inbound_session_factory() as verification_db:
        message = await verification_db.get(WhatsAppMessage, seed.message_id)
        assert message is not None
        assert message.inbound_pipeline["state"] == "completed"
        assert message.inbound_pipeline["attempt"] == 2
        assert message.inbound_lease_generation == 2
    assert calls == {
        "campaign_flow": 1,
        "rag": expected_rag_calls,
        "sdr": expected_sdr_calls,
        "automation": 1,
        "escalation": 1,
    }
