from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.omnichannel_v6 import (
    OmnichannelMessageEvidenceEvent,
    OmnichannelProviderWebhookEnvelope,
    OmnichannelProviderWebhookProcessingState,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.message_evidence_service import MessageEvidenceService

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")
DISPOSABLE_QA = os.getenv("KLM_TEST_POSTGRES_QA_DISPOSABLE") == "1"

if not MASTER_URL or not DISPOSABLE_QA:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL and an explicit "
        "KLM_TEST_POSTGRES_QA_DISPOSABLE=1 gate; "
        "run scripts/verify_postgresql_qa.sh."
    )


async def _cleanup(sessions, envelope_id: UUID) -> None:
    async with sessions.begin() as db:
        await db.execute(text("SET LOCAL lock_timeout = '10s'"))
        await db.execute(
            text(
                "ALTER TABLE klm_omnichannel_provider_webhook_envelopes "
                "DISABLE TRIGGER "
                "trg_klm_omnichannel_provider_webhook_envelopes_immutable"
            )
        )
        await db.execute(
            delete(OmnichannelProviderWebhookProcessingState).where(
                OmnichannelProviderWebhookProcessingState.envelope_id
                == envelope_id
            )
        )
        await db.execute(
            delete(OmnichannelProviderWebhookEnvelope).where(
                OmnichannelProviderWebhookEnvelope.id == envelope_id
            )
        )
        await db.execute(
            text(
                "ALTER TABLE klm_omnichannel_provider_webhook_envelopes "
                "ENABLE TRIGGER "
                "trg_klm_omnichannel_provider_webhook_envelopes_immutable"
            )
        )


@pytest.mark.asyncio
async def test_concurrent_exact_delivery_and_claim_have_one_envelope_and_one_winner() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with engine.connect() as connection:
        database_name = await connection.scalar(text("SELECT current_database()"))
    assert str(database_name).startswith("klm_legal_hub_qa_")

    token = uuid4().hex
    raw_payload = (
        '{"object":"whatsapp_business_account","entry":[],"race":"'
        + token
        + '"}'
    ).encode()
    signature = f"sha256={token}"
    capture_start = asyncio.Event()

    async def capture() -> tuple[UUID, bool]:
        async with sessions() as db:
            await capture_start.wait()
            context = await MessageEvidenceService(db).capture_provider_envelope(
                provider="meta_whatsapp",
                raw_payload=raw_payload,
                signature_header=signature,
                signature_scheme="x-hub-signature-256",
                signature_verified=True,
                request_metadata={},
            )
            await db.commit()
            return context.id, context.deduplicated

    envelope_id: UUID | None = None
    try:
        capture_tasks = (
            asyncio.create_task(capture()),
            asyncio.create_task(capture()),
        )
        capture_start.set()
        captures = await asyncio.gather(*capture_tasks)
        assert len({item[0] for item in captures}) == 1
        assert sorted(item[1] for item in captures) == [False, True]
        envelope_id = captures[0][0]

        async with sessions() as db:
            assert await db.scalar(
                select(func.count())
                .select_from(OmnichannelProviderWebhookEnvelope)
                .where(OmnichannelProviderWebhookEnvelope.id == envelope_id)
            ) == 1
            assert await db.scalar(
                select(func.count())
                .select_from(OmnichannelProviderWebhookProcessingState)
                .where(
                    OmnichannelProviderWebhookProcessingState.envelope_id
                    == envelope_id
                )
            ) == 1

        claim_start = asyncio.Event()

        async def claim() -> bool:
            async with sessions() as db:
                await claim_start.wait()
                result = await MessageEvidenceService(db).claim_provider_webhook(
                    envelope_id
                )
                await db.commit()
                return result is not None

        claim_tasks = (
            asyncio.create_task(claim()),
            asyncio.create_task(claim()),
        )
        claim_start.set()
        claims = await asyncio.gather(*claim_tasks)
        assert sorted(claims) == [False, True]
        async with sessions() as db:
            state = await db.get(
                OmnichannelProviderWebhookProcessingState,
                envelope_id,
            )
            assert state is not None
            assert state.status == "processing"
            assert state.attempts == 1
            assert state.lease_token is not None
    finally:
        if envelope_id is not None:
            await _cleanup(sessions, envelope_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_evidence_idempotency_and_chain_locks_serialize_cross_session_races() -> None:
    """One explicit key cannot succeed for two chains; one chain keeps revisions."""

    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    token = uuid4().hex
    async with sessions() as db:
        workspace = Workspace(
            name=f"Evidence race {token}",
            slug=f"evidence-race-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.commit()
        workspace_id = workspace.id

    cross_chain_start = asyncio.Event()
    shared_key = f"evidence-race:{token}"

    async def record_cross_chain(external_message_id: str):
        async with sessions() as db:
            await cross_chain_start.wait()
            try:
                event, replay = await MessageEvidenceService(db).record_event(
                    workspace_id=workspace_id,
                    provider="meta_whatsapp",
                    connection_key="whatsapp:test",
                    external_message_id=external_message_id,
                    event_type="provider_receipt",
                    event_payload={"status": "delivered"},
                    idempotency_key=shared_key,
                )
                await db.commit()
                return ("success", event.id, replay)
            except ValueError as error:
                await db.rollback()
                return ("conflict", str(error), False)

    same_chain_start = asyncio.Event()

    async def record_same_chain(sequence: int):
        async with sessions() as db:
            await same_chain_start.wait()
            event, replay = await MessageEvidenceService(db).record_event(
                workspace_id=workspace_id,
                provider="meta_whatsapp",
                connection_key="whatsapp:test",
                external_message_id=f"same-chain-{token}",
                event_type="provider_receipt",
                event_payload={"sequence": sequence},
                idempotency_key=f"same-chain:{token}:{sequence}",
            )
            await db.commit()
            return event.revision, replay

    try:
        cross_chain_tasks = (
            asyncio.create_task(record_cross_chain(f"chain-a-{token}")),
            asyncio.create_task(record_cross_chain(f"chain-b-{token}")),
        )
        cross_chain_start.set()
        cross_chain_results = await asyncio.gather(*cross_chain_tasks)
        assert sorted(result[0] for result in cross_chain_results) == [
            "conflict",
            "success",
        ]
        conflict = next(
            result for result in cross_chain_results if result[0] == "conflict"
        )
        assert "já foi usada para outro conteúdo" in conflict[1]

        same_chain_tasks = (
            asyncio.create_task(record_same_chain(1)),
            asyncio.create_task(record_same_chain(2)),
        )
        same_chain_start.set()
        same_chain_results = await asyncio.gather(*same_chain_tasks)
        assert sorted(revision for revision, _replay in same_chain_results) == [1, 2]
        assert all(replay is False for _revision, replay in same_chain_results)

        async with sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(OmnichannelMessageEvidenceEvent)
                    .where(
                        OmnichannelMessageEvidenceEvent.workspace_id == workspace_id
                    )
                )
                == 3
            )
    finally:
        async with sessions.begin() as db:
            await db.execute(
                text(
                    "ALTER TABLE klm_omnichannel_message_evidence_events "
                    "DISABLE TRIGGER "
                    "trg_klm_omnichannel_message_evidence_events_immutable"
                )
            )
            await db.execute(
                delete(OmnichannelMessageEvidenceEvent).where(
                    OmnichannelMessageEvidenceEvent.workspace_id == workspace_id
                )
            )
            await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await db.execute(
                text(
                    "ALTER TABLE klm_omnichannel_message_evidence_events "
                    "ENABLE TRIGGER "
                    "trg_klm_omnichannel_message_evidence_events_immutable"
                )
            )
        await engine.dispose()
