from datetime import UTC, datetime

import pytest

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelThread,
    ThreadStatus,
)
from app.schemas.klm_operations import OmnichannelMessageCreate
from app.schemas.messaging_cadence import MessagingCadenceSettingsUpdate
from app.services.klm_operations_service import KlmOperationsService
from app.services.messaging_cadence_service import MessagingCadenceService
pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_cadence_plan_is_deterministic_proportional_and_respects_quiet_hours(
    db_session,
    auth_context,
):
    service = MessagingCadenceService(db_session)
    await service.update_settings(
        auth_context.workspace_id,
        MessagingCadenceSettingsUpdate(
            pre_delay_min_ms=1000,
            pre_delay_max_ms=3000,
            characters_per_second=5,
            typing_min_ms=1000,
            typing_max_ms=30_000,
            jitter_percent=0,
            quiet_hours_enabled=True,
            quiet_hours_start=20,
            quiet_hours_end=8,
        ),
        actor_user_id=auth_context.user_id,
        actor_label="Admin",
    )

    noon = datetime(2030, 7, 18, 15, 0, tzinfo=UTC)  # 12h em São Paulo
    short = await service.plan(
        auth_context.workspace_id,
        text="Olá, tudo bem?",
        idempotency_key="same-message",
        now=noon,
        count_recent_messages=False,
    )
    replay = await service.plan(
        auth_context.workspace_id,
        text="Olá, tudo bem?",
        idempotency_key="same-message",
        now=noon,
        count_recent_messages=False,
    )
    long = await service.plan(
        auth_context.workspace_id,
        text="x" * 100,
        idempotency_key="long-message",
        now=noon,
        count_recent_messages=False,
    )
    assert short == replay
    assert long.typing_duration_ms > short.typing_duration_ms
    assert short.state == "ready_after_typing"
    assert short.not_before > short.typing_starts_at >= noon

    quiet = await service.plan(
        auth_context.workspace_id,
        text="Mensagem noturna",
        idempotency_key="quiet-message",
        now=datetime(2030, 7, 19, 1, 0, tzinfo=UTC),  # 22h em São Paulo
        count_recent_messages=False,
    )
    assert quiet.state == "deferred_quiet_hours"
    assert quiet.typing_starts_at.hour == 11  # 08h America/Sao_Paulo em UTC


@pytest.mark.asyncio
async def test_agent_outbound_stores_plan_and_stops_after_block_or_silence(
    db_session,
    auth_context,
):
    cadence = MessagingCadenceService(db_session)
    await cadence.update_settings(
        auth_context.workspace_id,
        MessagingCadenceSettingsUpdate(
            quiet_hours_enabled=False,
            max_consecutive_without_reply=2,
        ),
        actor_user_id=auth_context.user_id,
        actor_label="Admin",
    )
    thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
        score=80,
        unread_count=0,
        thread_metadata={},
    )
    db_session.add(thread)
    await db_session.flush()
    operations = KlmOperationsService(db_session)

    async def create(index: int):
        return await operations.create_message(
            auth_context.workspace_id,
            thread.id,
            auth_context.user_id,
            OmnichannelMessageCreate(
                channel=OmnichannelChannel.whatsapp,
                direction=MessageDirection.outbound,
                author_type=MessageAuthorType.agent,
                author_label="SDR Saúde",
                body=f"Mensagem automatizada {index}",
                idempotency_key=f"cadence-test-{index}",
            ),
        )

    first, deduplicated = await create(1)
    assert deduplicated is False
    plan = first.message_metadata["deliveryCadence"]
    assert plan["showTypingIndicator"] is True
    assert plan["notBefore"] > plan["plannedAt"]
    assert first.message_metadata["externalDispatcherMustHonorNotBefore"] is True
    await create(2)
    with pytest.raises(ValueError, match="limite sem resposta"):
        await create(3)

    thread.status = ThreadStatus.blocked
    await db_session.flush()
    with pytest.raises(ValueError, match="contato bloqueou"):
        await operations.create_message(
            auth_context.workspace_id,
            thread.id,
            auth_context.user_id,
            OmnichannelMessageCreate(
                channel=OmnichannelChannel.whatsapp,
                direction=MessageDirection.outbound,
                author_type=MessageAuthorType.agent,
                author_label="SDR Saúde",
                body="Não deve sair",
                idempotency_key="cadence-blocked",
            ),
        )


@pytest.mark.asyncio
async def test_cadence_settings_and_preview_are_available_through_api(client):
    current = await client.get("/api/v1/klm/messaging-cadence/settings")
    assert current.status_code == 200
    assert current.json()["officialApiOnly"] is True

    updated = await client.put(
        "/api/v1/klm/messaging-cadence/settings",
        json={**current.json(), "charactersPerSecond": 4.0, "maxMessagesPerMinute": 5},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["charactersPerSecond"] == 4.0

    preview = await client.post(
        "/api/v1/klm/messaging-cadence/preview",
        json={
            "text": "Mensagem de teste para o cliente.",
            "idempotencyKey": "preview-api",
            "now": "2030-07-18T15:00:00Z",
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["typingDurationMs"] >= 1200
    assert preview.json()["officialApiOnly"] is True
