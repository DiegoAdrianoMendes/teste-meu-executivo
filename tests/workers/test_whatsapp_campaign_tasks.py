from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.tenant import get_workspace_context
from app.models.klm_operations import OmnichannelMessage, OmnichannelThread
from app.models.whatsapp import (
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
    WhatsAppCampaignTemplateSnapshot,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.omnichannel_legacy_bridge_service import OmnichannelLegacyBridgeService
from app.services.whatsapp import campaign_service as campaign_service_module
from app.services.whatsapp.campaign_service import WhatsAppCampaignService
from app.workers import whatsapp_campaign_tasks


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def _workspace(name: str, *, is_active: bool = True, deleted: bool = False) -> Workspace:
    return Workspace(
        name=name,
        slug=f"campaign-worker-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=is_active,
        deleted_at=datetime.now(UTC) if deleted else None,
        settings={},
    )


async def _due_campaign(db_session: AsyncSession, workspace: Workspace, *, suffix: str):
    connection = WhatsAppConnection(
        workspace_id=workspace.id,
        phone_number_id=f"campaign-worker-{suffix}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    campaign = WhatsAppCampaign(
        workspace_id=workspace.id,
        whatsapp_connection_id=connection.id,
        title=f"Campanha {suffix}",
        status=CampaignStatus.running,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db_session.add(campaign)
    await db_session.flush()
    recipient = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=workspace.id,
        phone=f"55119{abs(hash(suffix)) % 100000000:08d}",
        status=CampaignRecipientStatus.queued,
        send_context={},
    )
    db_session.add(recipient)
    await db_session.flush()
    db_session.add(
        WhatsAppCampaignSendJob(
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            workspace_id=workspace.id,
            whatsapp_connection_id=connection.id,
            status=CampaignSendJobStatus.pending,
            scheduled_for=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    return campaign


@pytest.mark.asyncio
async def test_campaign_dispatcher_enqueues_only_active_workspace_ids(
    db_session: AsyncSession,
    monkeypatch,
):
    active_a = _workspace("Campaign A")
    active_b = _workspace("Campaign B")
    inactive = _workspace("Campaign inactive", is_active=False)
    deleted = _workspace("Campaign deleted", deleted=True)
    idle = _workspace("Campaign idle")
    db_session.add_all((active_a, active_b, inactive, deleted, idle))
    await db_session.flush()
    await _due_campaign(db_session, active_a, suffix="active-a")
    await _due_campaign(db_session, active_b, suffix="active-b")
    await _due_campaign(db_session, inactive, suffix="inactive")
    await _due_campaign(db_session, deleted, suffix="deleted")
    await db_session.commit()

    messages: list[tuple[tuple, dict]] = []

    def record_message(*args, **options):
        messages.append((args, options))

    monkeypatch.setattr(whatsapp_campaign_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        whatsapp_campaign_tasks.process_due_campaigns,
        "apply_async",
        record_message,
    )

    count = await whatsapp_campaign_tasks._async_dispatch_due_campaigns(
        session_factory=_session_factory(db_session)
    )

    assert count == 2
    assert {(args, options["kwargs"]["workspace_id"]) for args, options in messages} == {
        ((), str(active_a.id)),
        ((), str(active_b.id)),
    }
    assert all(set(options) == {"kwargs"} for _, options in messages)


@pytest.mark.asyncio
async def test_campaign_worker_binds_one_session_to_each_workspace(
    db_session: AsyncSession,
    monkeypatch,
):
    workspace_a = uuid4()
    workspace_b = uuid4()
    observations: list[tuple[AsyncSession, object]] = []

    class RecordingCampaignService:
        def __init__(self, db: AsyncSession) -> None:
            self.db = db

        async def process_due_campaigns(self, *, workspace_id=None) -> int:
            assert workspace_id == get_workspace_context(self.db)
            observations.append((self.db, get_workspace_context(self.db)))
            return 1

    class RecordingPublicApiService:
        def __init__(self, db: AsyncSession) -> None:
            assert get_workspace_context(db) is not None
            self.campaign_service = RecordingCampaignService(db)

    monkeypatch.setattr(whatsapp_campaign_tasks, "external_delivery_allowed", lambda: True)
    session_factory = _session_factory(db_session)

    assert (
        await whatsapp_campaign_tasks._async_process_due_campaigns(
            workspace_a,
            session_factory=session_factory,
            service_factory=RecordingPublicApiService,
        )
        == 1
    )
    assert (
        await whatsapp_campaign_tasks._async_process_due_campaigns(
            workspace_b,
            session_factory=session_factory,
            service_factory=RecordingPublicApiService,
        )
        == 1
    )

    assert [context for _, context in observations] == [workspace_a, workspace_b]
    assert observations[0][0] is not observations[1][0]


@pytest.mark.asyncio
async def test_campaign_zero_work_does_not_enqueue_idle_tenants(
    db_session: AsyncSession,
    monkeypatch,
):
    db_session.add_all(_workspace(f"Campaign idle {index}") for index in range(12))
    await db_session.commit()

    def forbidden_enqueue(*args, **kwargs):
        raise AssertionError("idle campaign tenant was enqueued")

    monkeypatch.setattr(whatsapp_campaign_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        whatsapp_campaign_tasks.process_due_campaigns,
        "apply_async",
        forbidden_enqueue,
    )

    assert (
        await whatsapp_campaign_tasks._async_dispatch_due_campaigns(
            session_factory=_session_factory(db_session)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_campaign_fanout_respects_workspace_batch_cap(
    db_session: AsyncSession,
    monkeypatch,
):
    workspaces = [_workspace(f"Campaign cap {index}") for index in range(5)]
    db_session.add_all(workspaces)
    await db_session.flush()
    for index, workspace in enumerate(workspaces):
        await _due_campaign(db_session, workspace, suffix=f"cap-{index}")
    await db_session.commit()
    enqueued: list[str] = []
    monkeypatch.setattr(whatsapp_campaign_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        whatsapp_campaign_tasks.process_due_campaigns,
        "apply_async",
        lambda **options: enqueued.append(options["kwargs"]["workspace_id"]),
    )

    count = await whatsapp_campaign_tasks._async_dispatch_due_campaigns(
        session_factory=_session_factory(db_session),
        batch_size=2,
    )

    assert count == 2
    assert len(set(enqueued)) == 2


@pytest.mark.asyncio
async def test_campaign_processing_is_bounded_tenant_scoped_and_idempotent(
    db_session: AsyncSession,
    monkeypatch,
):
    workspace_a = _workspace("Campaign processing A")
    workspace_b = _workspace("Campaign processing B")
    db_session.add_all((workspace_a, workspace_b))
    await db_session.flush()
    campaign_a = await _due_campaign(db_session, workspace_a, suffix="processing-a")
    campaign_b = await _due_campaign(db_session, workspace_b, suffix="processing-b")
    for index in range(2):
        recipient = WhatsAppCampaignRecipient(
            campaign_id=campaign_a.id,
            workspace_id=workspace_a.id,
            phone=f"55119888888{index:02d}",
            status=CampaignRecipientStatus.queued,
            send_context={},
        )
        db_session.add(recipient)
        await db_session.flush()
        db_session.add(
            WhatsAppCampaignSendJob(
                campaign_id=campaign_a.id,
                recipient_id=recipient.id,
                workspace_id=workspace_a.id,
                whatsapp_connection_id=campaign_a.whatsapp_connection_id,
                status=CampaignSendJobStatus.pending,
                scheduled_for=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
    db_session.add_all(
        (
            WhatsAppCampaignTemplateSnapshot(
                campaign_id=campaign_a.id,
                template_name="campanha_a",
                language="pt_BR",
                status=MetaTemplateStatus.approved,
                header_parameters=[],
                body_parameters=[],
                components=[],
                raw_payload={},
            ),
            WhatsAppCampaignTemplateSnapshot(
                campaign_id=campaign_b.id,
                template_name="campanha_b",
                language="pt_BR",
                status=MetaTemplateStatus.approved,
                header_parameters=[],
                body_parameters=[],
                components=[],
                raw_payload={},
            ),
        )
    )
    await db_session.commit()
    connections = {
        campaign_a.whatsapp_connection_id: await db_session.get(
            WhatsAppConnection,
            campaign_a.whatsapp_connection_id,
        ),
        campaign_b.whatsapp_connection_id: await db_session.get(
            WhatsAppConnection,
            campaign_b.whatsapp_connection_id,
        ),
    }
    deliveries: list[str] = []

    class FakeConnectionService:
        async def get_connection(self, connection_id, workspace_id):
            connection = connections[connection_id]
            assert connection is not None
            assert connection.workspace_id == workspace_id
            return connection

        async def get_default_connection(self, workspace_id, *, connected_only=False):
            return next(
                connection
                for connection in connections.values()
                if connection is not None and connection.workspace_id == workspace_id
            )

        def get_meta_access_token(self, connection):
            return "local-test-token"

        async def resolve_meta_access_token(self, connection):
            return self.get_meta_access_token(connection)

    class FakeMetaClient:
        async def send_template_message(self, **kwargs):
            deliveries.append(kwargs["to"])
            return {"messages": [{"id": f"wamid-{len(deliveries)}"}]}

    monkeypatch.setattr(campaign_service_module, "external_delivery_allowed", lambda: True)
    service = WhatsAppCampaignService(
        db_session,
        connection_service=FakeConnectionService(),
        meta_client=FakeMetaClient(),
    )

    async def finite_commercial_quota(**_kwargs):
        return 100_000

    monkeypatch.setattr(
        service,
        "_campaign_recipient_limit",
        finite_commercial_quota,
    )

    first = await service.process_due_campaigns(
        workspace_id=workspace_a.id,
        campaign_batch_size=1,
        job_batch_size=1,
    )
    second = await service.process_due_campaigns(
        workspace_id=workspace_a.id,
        campaign_batch_size=1,
        job_batch_size=1,
    )
    third = await service.process_due_campaigns(
        workspace_id=workspace_a.id,
        campaign_batch_size=1,
        job_batch_size=1,
    )
    replay = await service.process_due_campaigns(
        workspace_id=workspace_a.id,
        campaign_batch_size=1,
        job_batch_size=1,
    )
    b_jobs_before = list(
        (
            await db_session.scalars(
                select(WhatsAppCampaignSendJob).where(
                    WhatsAppCampaignSendJob.workspace_id == workspace_b.id
                )
            )
        ).all()
    )

    assert first == 1
    assert second == 1
    assert third == 1
    assert replay == 0
    assert len(deliveries) == 3
    assert len(set(deliveries)) == 3
    assert all(job.status == CampaignSendJobStatus.pending for job in b_jobs_before)
    legacy_messages = list(
        (
            await db_session.scalars(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.message_metadata["campaignId"].as_string() == str(campaign_a.id)
                )
            )
        ).all()
    )
    canonical_messages = list(
        (
            await db_session.scalars(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace_a.id,
                )
            )
        ).all()
    )
    canonical_threads = list(
        (
            await db_session.scalars(
                select(OmnichannelThread).where(
                    OmnichannelThread.workspace_id == workspace_a.id,
                )
            )
        ).all()
    )
    assert len(legacy_messages) == 3
    assert len(canonical_messages) == 3
    assert all(message.message_metadata["broadcast"] is True for message in canonical_messages)
    assert len(canonical_threads) == 3
    assert all(
        thread.thread_metadata["broadcast"]["awaitingReply"] is True for thread in canonical_threads
    )
    first_outbound = legacy_messages[0]
    inbound = WhatsAppMessage(
        contact_id=first_outbound.contact_id,
        whatsapp_connection_id=first_outbound.whatsapp_connection_id,
        wamid=f"reply-{first_outbound.wamid}",
        direction=WaMessageDirection.inbound,
        status=WaMessageStatus.delivered,
        body="Quero continuar, pode me explicar?",
        timestamp=datetime.now(UTC) + timedelta(seconds=1),
    )
    db_session.add(inbound)
    await db_session.flush()
    first_contact = await db_session.get(WhatsAppContact, first_outbound.contact_id)
    first_connection = connections[first_outbound.whatsapp_connection_id]
    assert first_contact is not None
    assert first_connection is not None
    await OmnichannelLegacyBridgeService(db_session).ingest_whatsapp_message(
        workspace_id=workspace_a.id,
        connection=first_connection,
        contact=first_contact,
        message=inbound,
    )
    await db_session.flush()
    replied_thread = await db_session.get(
        OmnichannelThread,
        UUID(first_outbound.message_metadata["canonical"]["threadId"]),
    )
    assert replied_thread is not None
    assert replied_thread.thread_metadata["broadcast"]["awaitingReply"] is False
    assert replied_thread.thread_metadata["broadcast"]["lastReplyMessageId"]

    assert await service.process_due_campaigns(workspace_id=workspace_b.id) == 1
    assert len(deliveries) == 4


@pytest.mark.asyncio
async def test_campaign_worker_rejects_invalid_workspace_before_database_access():
    def forbidden_session_factory():
        raise AssertionError("invalid workspace reached the database")

    with pytest.raises(ValueError, match="workspace_id"):
        await whatsapp_campaign_tasks._async_process_due_campaigns(
            "not-a-uuid",
            session_factory=forbidden_session_factory,
        )


@pytest.mark.asyncio
async def test_campaign_worker_rechecks_external_gate_before_database_access(monkeypatch):
    def forbidden_session_factory():
        raise AssertionError("blocked delivery reached the database")

    def forbidden_service_factory(_db):
        raise AssertionError("blocked delivery constructed the external service")

    monkeypatch.setattr(whatsapp_campaign_tasks, "external_delivery_allowed", lambda: False)

    assert (
        await whatsapp_campaign_tasks._async_process_due_campaigns(
            uuid4(),
            session_factory=forbidden_session_factory,
            service_factory=forbidden_service_factory,
        )
        == 0
    )


@pytest.mark.asyncio
async def test_campaign_dispatcher_does_not_enqueue_when_external_gate_is_closed(monkeypatch):
    def forbidden_session_factory():
        raise AssertionError("blocked dispatcher reached the database")

    def forbidden_apply_async(*args, **kwargs):
        raise AssertionError("blocked dispatcher enqueued a campaign")

    monkeypatch.setattr(whatsapp_campaign_tasks, "external_delivery_allowed", lambda: False)
    monkeypatch.setattr(
        whatsapp_campaign_tasks.process_due_campaigns,
        "apply_async",
        forbidden_apply_async,
    )

    assert (
        await whatsapp_campaign_tasks._async_dispatch_due_campaigns(
            session_factory=forbidden_session_factory
        )
        == 0
    )
