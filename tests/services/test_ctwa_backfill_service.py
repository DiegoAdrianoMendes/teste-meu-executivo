"""Backfill CTWA histórico (T1.2 do Plano Meta Máximo).

Cobre: evidência com `referral` gera AcquisitionEvent (com thread_metadata
first-touch), evidência sem `referral` não gera nada, rodar duas vezes não
duplica, primeiro touch cronológico é preservado, evidência sem thread
resolvido conta como `unresolved`, e o modo dry-run não persiste nada.
"""
from __future__ import annotations

from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.models.klm_operations import AcquisitionEvent, OmnichannelThread
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.message_evidence_service import MessageEvidenceService
from app.services.whatsapp.ctwa_backfill_service import CtwaBackfillService

CTWA_REFERRAL = {
    "ctwa_clid": "ARAcLId_backfill_0001",
    "source_url": "https://fb.me/anuncio-antigo",
    "source_id": "998877665544332",
    "source_type": "ad",
    "headline": "Fale com um advogado agora",
    "body": "Atendimento imediato pelo WhatsApp",
}


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    database_path = tmp_path / "ctwa-backfill.sqlite3"
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


async def _seed_workspace(db: AsyncSession, suffix: str) -> UUID:
    workspace = Workspace(
        name=f"Backfill {suffix}",
        slug=f"backfill-{suffix}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db.add(workspace)
    await db.flush()
    return workspace.id


async def _seed_thread(db: AsyncSession, workspace_id: UUID) -> UUID:
    thread = OmnichannelThread(workspace_id=workspace_id)
    db.add(thread)
    await db.flush()
    return thread.id


async def _record_inbound_evidence(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    wamid: str,
    thread_id: UUID | None,
    with_referral: bool,
    provider: str = "meta_cloud",
):
    message_data = {
        "id": wamid,
        "from": "5511988887777",
        "timestamp": "1700000000",
        "type": "text",
        "text": {"body": "Vi o anúncio de vocês"},
    }
    if with_referral:
        message_data["referral"] = dict(CTWA_REFERRAL)
    await MessageEvidenceService(db).record_event(
        workspace_id=workspace_id,
        provider=provider,
        connection_key="whatsapp:conn-1",
        external_message_id=wamid,
        event_type="provider_receipt",
        observation_state="observed",
        thread_id=thread_id,
        body_snapshot="Vi o anúncio de vocês",
        content_type="text",
        event_payload=message_data,
    )
    await db.commit()


@pytest.mark.asyncio
async def test_evidence_with_referral_creates_acquisition_event(session_factory):
    async with session_factory() as db:
        workspace_id = await _seed_workspace(db, "with-referral")
        thread_id = await _seed_thread(db, workspace_id)
        await _record_inbound_evidence(
            db,
            workspace_id=workspace_id,
            wamid="wamid.backfill-1",
            thread_id=thread_id,
            with_referral=True,
        )

    async with session_factory() as db:
        report = await CtwaBackfillService(db).run(workspace_id=workspace_id, dry_run=False)

    assert report.scanned == 1
    assert report.with_referral == 1
    assert report.events_created == 1
    assert report.skipped_existing == 0
    assert report.unresolved == 0

    async with session_factory() as db:
        event = await db.scalar(
            select(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == workspace_id,
                AcquisitionEvent.dedupe_key == "ctwa:wamid.backfill-1",
            )
        )
        assert event is not None
        assert event.event_name == "conversation_started"
        assert event.click_ids == {"ctwa_clid": CTWA_REFERRAL["ctwa_clid"]}

        thread = await db.get(OmnichannelThread, thread_id)
        assert thread is not None
        acquisition = thread.thread_metadata["acquisition"]
        assert acquisition["clickIds"] == {"ctwa_clid": CTWA_REFERRAL["ctwa_clid"]}
        assert acquisition["ad"] == CTWA_REFERRAL["source_id"]


@pytest.mark.asyncio
async def test_evidence_without_referral_creates_nothing(session_factory):
    async with session_factory() as db:
        workspace_id = await _seed_workspace(db, "no-referral")
        thread_id = await _seed_thread(db, workspace_id)
        await _record_inbound_evidence(
            db,
            workspace_id=workspace_id,
            wamid="wamid.backfill-plain-1",
            thread_id=thread_id,
            with_referral=False,
        )

    async with session_factory() as db:
        report = await CtwaBackfillService(db).run(workspace_id=workspace_id, dry_run=False)

    assert report.scanned == 1
    assert report.with_referral == 0
    assert report.events_created == 0

    async with session_factory() as db:
        total = await db.scalar(
            select(func.count()).select_from(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == workspace_id
            )
        )
        assert total == 0


@pytest.mark.asyncio
async def test_running_twice_does_not_duplicate(session_factory):
    async with session_factory() as db:
        workspace_id = await _seed_workspace(db, "idempotent")
        thread_id = await _seed_thread(db, workspace_id)
        await _record_inbound_evidence(
            db,
            workspace_id=workspace_id,
            wamid="wamid.backfill-idem-1",
            thread_id=thread_id,
            with_referral=True,
        )

    async with session_factory() as db:
        first = await CtwaBackfillService(db).run(workspace_id=workspace_id, dry_run=False)
    assert first.events_created == 1
    assert first.skipped_existing == 0

    async with session_factory() as db:
        second = await CtwaBackfillService(db).run(workspace_id=workspace_id, dry_run=False)
    assert second.events_created == 0
    assert second.skipped_existing == 1

    async with session_factory() as db:
        total = await db.scalar(
            select(func.count()).select_from(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == workspace_id
            )
        )
        assert total == 1


@pytest.mark.asyncio
async def test_first_touch_is_preserved_across_chronological_order(session_factory):
    async with session_factory() as db:
        workspace_id = await _seed_workspace(db, "first-touch")
        thread_id = await _seed_thread(db, workspace_id)
        # Primeira mensagem histórica do thread (o "primeiro touch" real).
        await _record_inbound_evidence(
            db,
            workspace_id=workspace_id,
            wamid="wamid.backfill-first-touch-1",
            thread_id=thread_id,
            with_referral=True,
        )
        # Segunda mensagem histórica do mesmo thread, outro anúncio.
        second_referral = dict(CTWA_REFERRAL)
        second_referral["source_id"] = "second-ad-id"
        message_data = {
            "id": "wamid.backfill-first-touch-2",
            "from": "5511988887777",
            "timestamp": "1700000100",
            "type": "text",
            "text": {"body": "Outra mensagem"},
            "referral": second_referral,
        }
        await MessageEvidenceService(db).record_event(
            workspace_id=workspace_id,
            provider="meta_cloud",
            connection_key="whatsapp:conn-1",
            external_message_id="wamid.backfill-first-touch-2",
            event_type="provider_receipt",
            thread_id=thread_id,
            body_snapshot="Outra mensagem",
            content_type="text",
            event_payload=message_data,
        )
        await db.commit()

    async with session_factory() as db:
        report = await CtwaBackfillService(db).run(workspace_id=workspace_id, dry_run=False)
    assert report.events_created == 2

    async with session_factory() as db:
        thread = await db.get(OmnichannelThread, thread_id)
        assert thread is not None
        # setdefault na captura ao vivo garante que o primeiro processado
        # (ordem de id == ordem cronológica de chegada) vence.
        assert thread.thread_metadata["acquisition"]["ad"] == CTWA_REFERRAL["source_id"]
        touches = thread.thread_metadata["acquisitionTouches"]
        assert {touch["wamid"] for touch in touches} == {
            "wamid.backfill-first-touch-1",
            "wamid.backfill-first-touch-2",
        }


@pytest.mark.asyncio
async def test_evidence_without_thread_counts_as_unresolved(session_factory):
    async with session_factory() as db:
        workspace_id = await _seed_workspace(db, "unresolved")
        await _record_inbound_evidence(
            db,
            workspace_id=workspace_id,
            wamid="wamid.backfill-unresolved-1",
            thread_id=None,
            with_referral=True,
        )

    async with session_factory() as db:
        report = await CtwaBackfillService(db).run(workspace_id=workspace_id, dry_run=False)

    assert report.with_referral == 1
    assert report.events_created == 0
    assert report.unresolved == 1

    async with session_factory() as db:
        total = await db.scalar(
            select(func.count()).select_from(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == workspace_id
            )
        )
        assert total == 0


@pytest.mark.asyncio
async def test_dry_run_reports_but_does_not_persist(session_factory):
    async with session_factory() as db:
        workspace_id = await _seed_workspace(db, "dry-run")
        thread_id = await _seed_thread(db, workspace_id)
        await _record_inbound_evidence(
            db,
            workspace_id=workspace_id,
            wamid="wamid.backfill-dryrun-1",
            thread_id=thread_id,
            with_referral=True,
        )

    async with session_factory() as db:
        report = await CtwaBackfillService(db).run(workspace_id=workspace_id, dry_run=True)

    assert report.dry_run is True
    assert report.events_created == 1
    assert report.sample_dedupe_keys == ["ctwa:wamid.backfill-dryrun-1"]

    async with session_factory() as db:
        total = await db.scalar(
            select(func.count()).select_from(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == workspace_id
            )
        )
        assert total == 0
        thread = await db.get(OmnichannelThread, thread_id)
        assert thread is not None
        assert "acquisition" not in (thread.thread_metadata or {})
