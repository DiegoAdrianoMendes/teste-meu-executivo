"""Sprint F3-B: intake social do SDR (comment-to-DM, story reply/mention, DM espontânea).

Cobre G-C6/G-C8 da auditoria de 23/07/2026: comment-to-DM não alimentava o SDR e
story reply/mention não viravam lead quente. Reutiliza o padrão de
`tests/api/test_omnichannel_v6.py` (client HTTP + verificação direta no db_session).
"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.klm_operations import AcquisitionEvent, OmnichannelThread
from app.models.matter import Matter


pytestmark = pytest.mark.usefixtures("active_subscription")


def _social_dm_event(
    *,
    channel: str,
    provider: str,
    event_type: str,
    external_event_id: str,
    external_thread_id: str,
    body: str,
    correlation: dict | None = None,
) -> dict:
    return {
        "provider": provider,
        "connectionKey": f"{provider}:principal",
        "channel": channel,
        "eventType": event_type,
        "externalEventId": external_event_id,
        "externalThreadId": external_thread_id,
        "externalUserId": f"user:{channel}:{external_thread_id}",
        "externalAccountId": f"account:{channel}:principal",
        "displayName": "Fernanda Lima",
        "address": "@fernandalima",
        "direction": "inbound",
        "authorType": "client",
        "authorLabel": "Fernanda Lima",
        "body": body,
        "visibility": "client_private",
        "actionability": "reply_required",
        "correlation": correlation or {},
        "metadata": {"providerStatus": "received"},
    }


@pytest.mark.asyncio
async def test_dm_after_private_reply_creates_matter_with_comment_origin_idempotently(
    client, db_session
):
    """DM inbound numa thread correlacionada a um post/comentário cria Matter com
    lead_source do canal e demand_type 'Comentário em post'; replay não duplica."""
    payload = _social_dm_event(
        channel="instagram",
        provider="meta_instagram",
        event_type="direct_message",
        external_event_id="ig-dm-1",
        external_thread_id="ig-thread-fernanda",
        body="Oi, vi seu comentário e quero saber mais sobre o caso.",
        correlation={"postId": "post-123", "adId": "ad-456"},
    )

    first = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert first.status_code == 201, first.text
    matter_id = first.json()["thread"]["matterId"]
    assert matter_id is not None

    matter = await db_session.scalar(select(Matter).where(Matter.id == matter_id))
    assert matter is not None
    assert matter.lead_source == "instagram"
    assert matter.demand_type == "Comentário em post"

    events_count = await db_session.scalar(
        select(func.count()).select_from(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"social:{first.json()['thread']['id']}"
        )
    )
    assert events_count == 1

    # Segunda DM na mesma thread não cria um novo Matter nem duplica o evento de aquisição.
    second_payload = _social_dm_event(
        channel="instagram",
        provider="meta_instagram",
        event_type="direct_message",
        external_event_id="ig-dm-2",
        external_thread_id="ig-thread-fernanda",
        body="Só complementando a mensagem anterior.",
        correlation={"postId": "post-123", "adId": "ad-456"},
    )
    second = await client.post("/api/v1/klm/omnichannel/events/ingest", json=second_payload)
    assert second.status_code == 201, second.text
    assert second.json()["thread"]["matterId"] == matter_id

    matters_count = await db_session.scalar(select(func.count()).select_from(Matter))
    assert matters_count == 1
    events_count_after = await db_session.scalar(
        select(func.count()).select_from(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"social:{second.json()['thread']['id']}"
        )
    )
    assert events_count_after == 1


@pytest.mark.asyncio
async def test_story_reply_creates_hot_lead_with_high_priority(client, db_session):
    payload = _social_dm_event(
        channel="instagram",
        provider="meta_instagram",
        event_type="story_reply",
        external_event_id="ig-story-reply-1",
        external_thread_id="ig-thread-story-lead",
        body="Respondi seu story, preciso de ajuda urgente.",
        correlation={"storyId": "story-789"},
    )

    response = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert response.status_code == 201, response.text
    thread_id = response.json()["thread"]["id"]
    matter_id = response.json()["thread"]["matterId"]
    assert matter_id is not None

    thread = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread_id)
    )
    assert thread is not None
    assert thread.priority >= 90
    social_lead = (thread.thread_metadata or {}).get("socialLead")
    assert social_lead is not None
    assert social_lead["hot"] is True
    assert social_lead["origin"] == "story"

    matter = await db_session.scalar(select(Matter).where(Matter.id == matter_id))
    assert matter.demand_type == "Resposta de story"
    assert matter.lead_source == "instagram"


@pytest.mark.asyncio
async def test_spontaneous_dm_creates_lead_with_direct_origin(client, db_session):
    payload = _social_dm_event(
        channel="messenger",
        provider="meta_messenger",
        event_type="direct_message",
        external_event_id="mg-dm-spontaneous-1",
        external_thread_id=f"mg-thread-{uuid4()}",
        body="Oi, encontrei vocês e queria uma orientação.",
        correlation={},
    )

    response = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert response.status_code == 201, response.text
    matter_id = response.json()["thread"]["matterId"]
    assert matter_id is not None

    matter = await db_session.scalar(select(Matter).where(Matter.id == matter_id))
    assert matter.lead_source == "messenger"
    assert matter.demand_type == "Mensagem direta via messenger"

    thread_id = response.json()["thread"]["id"]
    thread = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread_id)
    )
    social_lead = (thread.thread_metadata or {}).get("socialLead")
    assert social_lead["origin"] == "direct"
    assert social_lead["hot"] is False


@pytest.mark.asyncio
async def test_public_comment_event_does_not_create_lead(client, db_session):
    """Comentário público (visibility=public, sem direction) não deve, sozinho,
    abrir caso — só a DM inbound subsequente cria o lead (G-C8)."""
    payload = {
        "provider": "meta_instagram",
        "connectionKey": "meta_instagram:principal",
        "channel": "instagram",
        "eventType": "post_comment",
        "externalEventId": "ig-comment-only-1",
        "externalThreadId": "ig-thread-comment-only",
        "externalUserId": "user:instagram:comment-only",
        "externalAccountId": "account:instagram:principal",
        "displayName": "Lead Comentarista",
        "address": "@leadcomentarista",
        "visibility": "public",
        "actionability": "informational",
        "correlation": {"postId": "post-999", "commentId": "comment-1"},
        "metadata": {},
    }
    response = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["thread"]["matterId"] is None

    matters_count = await db_session.scalar(select(func.count()).select_from(Matter))
    assert matters_count == 0


@pytest.mark.asyncio
async def test_deep_link_ref_is_persisted_on_thread_and_acquisition_event(
    client, db_session
):
    """Auditoria 2026-07-26, item 2: DM originada por m.me/ig.me?ref=<code>
    persiste o `ref` tanto no `thread_metadata.acquisition` quanto no
    `AcquisitionEvent.event_metadata` — fecha o loop de atribuição para
    conversas iniciadas por deep link (sem adId/postId)."""
    payload = _social_dm_event(
        channel="messenger",
        provider="meta_messenger",
        event_type="direct_message",
        external_event_id="mm-dm-ref-1",
        external_thread_id="mm-thread-ref",
        body="Oi, vim pelo link da bio",
        correlation={"ref": "campanha-instagram-bio"},
    )

    response = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert response.status_code == 201, response.text
    thread_id = response.json()["thread"]["id"]
    matter_id = response.json()["thread"]["matterId"]
    assert matter_id is not None

    thread = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread_id)
    )
    acquisition = (thread.thread_metadata or {}).get("acquisition") or {}
    assert acquisition.get("ref") == "campanha-instagram-bio"

    event = await db_session.scalar(
        select(AcquisitionEvent).where(AcquisitionEvent.dedupe_key == f"social:{thread_id}")
    )
    assert event is not None
    assert event.event_metadata.get("ref") == "campanha-instagram-bio"
    assert str(event.matter_id) == matter_id
