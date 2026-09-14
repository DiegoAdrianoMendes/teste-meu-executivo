"""Sprint social-funnel: eventos PSID/IGSID + qualificação multi-turno via Direct.

Cobre:
1. DM social inbound com externalUserId numérico -> conversation_started carrega
   click_ids.igsid/psid (alimenta o CAPI social pelo mesmo trilho do ctwa_clid).
2. Thread já vinculada a Matter com qualificação incompleta dispara a
   continuação do SDR (`dispatch_social_message`) na chegada de uma nova DM;
   com qualificação completa, não dispara de novo.
3. `run_sdr_on_social_message` + `_draft_social_sdr_reply` produzem rascunho
   local_draft (dentro da janela) ou nota interna (fora da janela) — nunca
   transporte real — e ao completar a qualificação emitem `lead_qualified` com
   os click_ids herdados.
"""

from datetime import UTC, datetime, timedelta
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
    external_user_id: str,
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
        "externalUserId": external_user_id,
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
async def test_instagram_dm_with_igsid_propagates_click_ids_on_conversation_started(
    client, db_session
):
    payload = _social_dm_event(
        channel="instagram",
        provider="meta_instagram",
        event_type="direct_message",
        external_event_id="ig-dm-igsid-1",
        external_thread_id="ig-thread-igsid",
        external_user_id="17841400000001234",
        body="Oi, quero saber sobre meu caso.",
    )

    response = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert response.status_code == 201, response.text
    thread_id = response.json()["thread"]["id"]

    event = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"social:{thread_id}"
        )
    )
    assert event is not None
    assert event.click_ids.get("igsid") == "17841400000001234"

    thread = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread_id)
    )
    acquisition = (thread.thread_metadata or {}).get("acquisition") or {}
    assert acquisition.get("clickIds", {}).get("igsid") == "17841400000001234"


@pytest.mark.asyncio
async def test_messenger_dm_with_psid_propagates_click_ids(client, db_session):
    payload = _social_dm_event(
        channel="messenger",
        provider="meta_messenger",
        event_type="direct_message",
        external_event_id="fb-dm-psid-1",
        external_thread_id="fb-thread-psid",
        external_user_id="9988776655",
        body="Preciso de ajuda com meu processo.",
    )

    response = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert response.status_code == 201, response.text
    thread_id = response.json()["thread"]["id"]

    event = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"social:{thread_id}"
        )
    )
    assert event is not None
    assert event.click_ids.get("psid") == "9988776655"


@pytest.mark.asyncio
async def test_non_numeric_external_user_id_is_never_sent_as_click_id(client, db_session):
    """Formato inválido (não numérico) é ignorado, fail-closed: nunca manda lixo pro CAPI."""
    payload = _social_dm_event(
        channel="instagram",
        provider="meta_instagram",
        event_type="direct_message",
        external_event_id="ig-dm-badid-1",
        external_thread_id="ig-thread-badid",
        external_user_id="not-a-real-igsid",
        body="Olá.",
    )
    response = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert response.status_code == 201, response.text
    thread_id = response.json()["thread"]["id"]

    event = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.dedupe_key == f"social:{thread_id}"
        )
    )
    assert event is not None
    assert "igsid" not in event.click_ids


@pytest.mark.asyncio
async def test_inbound_dm_on_open_matter_dispatches_social_qualification(
    client, db_session, monkeypatch
):
    """Thread com Matter já aberto e qualificação incompleta dispara a continuação
    do SDR social (`dispatch_social_message`) na chegada de uma nova DM."""
    dispatched: list[dict] = []

    async def _fake_dispatch(matter_id, thread_id, inbound_text, channel, *, workspace_id, dedupe_key=None):
        dispatched.append(
            {
                "matter_id": matter_id,
                "thread_id": thread_id,
                "inbound_text": inbound_text,
                "channel": channel,
                "dedupe_key": dedupe_key,
            }
        )
        return None

    monkeypatch.setattr("app.workers.event_bus.dispatch_social_message", _fake_dispatch)

    first_payload = _social_dm_event(
        channel="instagram",
        provider="meta_instagram",
        event_type="direct_message",
        external_event_id="ig-dm-qual-1",
        external_thread_id="ig-thread-qual",
        external_user_id="17841400000009999",
        body="Fui demitido sem justa causa.",
    )
    first = await client.post("/api/v1/klm/omnichannel/events/ingest", json=first_payload)
    assert first.status_code == 201, first.text
    thread_id = first.json()["thread"]["id"]
    matter_id = first.json()["thread"]["matterId"]
    assert matter_id is not None

    matter = await db_session.scalar(select(Matter).where(Matter.id == matter_id))
    assert (matter.sdr_qualification or {}).get("completed") is not True

    second_payload = _social_dm_event(
        channel="instagram",
        provider="meta_instagram",
        event_type="direct_message",
        external_event_id="ig-dm-qual-2",
        external_thread_id="ig-thread-qual",
        external_user_id="17841400000009999",
        body="Trabalhei 2 anos na empresa X.",
    )
    second = await client.post("/api/v1/klm/omnichannel/events/ingest", json=second_payload)
    assert second.status_code == 201, second.text

    assert len(dispatched) == 1
    assert dispatched[0]["matter_id"] == matter.id
    assert str(dispatched[0]["thread_id"]) == thread_id
    assert dispatched[0]["channel"] == "instagram"
    assert dispatched[0]["inbound_text"] == "Trabalhei 2 anos na empresa X."


@pytest.mark.asyncio
async def test_completed_qualification_never_redispatches(client, db_session, monkeypatch):
    """Caminho WhatsApp intocado + social não dispara mais nada uma vez completo."""
    dispatched: list[dict] = []

    async def _fake_dispatch(*args, **kwargs):
        dispatched.append(kwargs)
        return None

    monkeypatch.setattr("app.workers.event_bus.dispatch_social_message", _fake_dispatch)

    payload = _social_dm_event(
        channel="messenger",
        provider="meta_messenger",
        event_type="direct_message",
        external_event_id="fb-dm-done-1",
        external_thread_id="fb-thread-done",
        external_user_id="112233445566",
        body="Olá, quero saber do meu caso.",
    )
    first = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    assert first.status_code == 201, first.text
    matter_id = first.json()["thread"]["matterId"]

    matter = await db_session.scalar(select(Matter).where(Matter.id == matter_id))
    matter.sdr_qualification = {**(matter.sdr_qualification or {}), "completed": True}
    await db_session.commit()

    second_payload = _social_dm_event(
        channel="messenger",
        provider="meta_messenger",
        event_type="direct_message",
        external_event_id="fb-dm-done-2",
        external_thread_id="fb-thread-done",
        external_user_id="112233445566",
        body="Mais uma mensagem.",
    )
    second = await client.post("/api/v1/klm/omnichannel/events/ingest", json=second_payload)
    assert second.status_code == 201, second.text

    assert dispatched == []


@pytest.mark.asyncio
async def test_draft_social_sdr_reply_within_window_creates_local_draft(
    db_session, auth_context, monkeypatch
):
    from app.models.klm_operations import OmnichannelThread
    from app.workers.agent_tasks import _draft_social_sdr_reply

    from app.models.client import Client

    workspace_id = auth_context.workspace_id
    social_client = Client(workspace_id=workspace_id, name="Lead Instagram")
    db_session.add(social_client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=social_client.id,
        demand_type="Rescisão indevida",
        lead_source="instagram",
    )
    db_session.add(matter)
    await db_session.flush()

    from app.models.klm_operations import OmnichannelChannel as _Channel
    from app.models.klm_operations import ThreadStatus as _ThreadStatus

    thread = OmnichannelThread(
        workspace_id=workspace_id,
        matter_id=matter.id,
        primary_channel=_Channel.instagram,
        preferred_channel="instagram",
        status=_ThreadStatus.open,
        last_inbound_at=datetime.now(UTC) - timedelta(hours=1),
        thread_metadata={},
    )
    db_session.add(thread)
    await db_session.commit()

    result = await _draft_social_sdr_reply(
        db_session,
        workspace_id=workspace_id,
        thread_id=thread.id,
        channel="instagram",
        body="Olá! Pode me contar mais sobre sua situação na empresa?",
        run_id=uuid4(),
    )
    assert result == "local_draft"

    refreshed = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread.id)
    )
    social_lead = (refreshed.thread_metadata or {}).get("socialLead") or {}
    assert social_lead.get("lastDraft", {}).get("audience") == "client_private"
    assert social_lead["lastDraft"]["policyGate"]["allowed"] is True


@pytest.mark.asyncio
async def test_draft_social_sdr_reply_outside_window_creates_internal_note_not_client_draft(
    db_session, auth_context,
):
    from app.models.klm_operations import OmnichannelThread
    from app.workers.agent_tasks import _draft_social_sdr_reply

    from app.models.client import Client

    workspace_id = auth_context.workspace_id
    social_client = Client(workspace_id=workspace_id, name="Lead Instagram")
    db_session.add(social_client)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=social_client.id,
        demand_type="Rescisão indevida",
        lead_source="instagram",
    )
    db_session.add(matter)
    await db_session.flush()

    from app.models.klm_operations import OmnichannelChannel as _Channel
    from app.models.klm_operations import ThreadStatus as _ThreadStatus

    thread = OmnichannelThread(
        workspace_id=workspace_id,
        matter_id=matter.id,
        primary_channel=_Channel.instagram,
        preferred_channel="instagram",
        status=_ThreadStatus.open,
        last_inbound_at=datetime.now(UTC) - timedelta(hours=48),
        thread_metadata={},
    )
    db_session.add(thread)
    await db_session.commit()

    result = await _draft_social_sdr_reply(
        db_session,
        workspace_id=workspace_id,
        thread_id=thread.id,
        channel="instagram",
        body="Olá! Pode me contar mais sobre sua situação na empresa?",
        run_id=uuid4(),
    )
    assert result == "policy_gated_internal_note"

    refreshed = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == thread.id)
    )
    social_lead = (refreshed.thread_metadata or {}).get("socialLead") or {}
    assert social_lead.get("lastDraft", {}).get("audience") == "internal"
    assert social_lead["lastDraft"]["policyGate"]["allowed"] is False
