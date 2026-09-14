"""End-to-end contracts for the native canonical omnichannel core."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.client import Client
from app.models.klm_operations import OmnichannelMessage, OmnichannelThread
from app.models.matter import Matter
from app.models.omnichannel_v6 import (
    LeadCaptureReceipt,
    OmnichannelCallCostLedgerEntry,
    OmnichannelCallEvent,
    OmnichannelCallSession,
    OmnichannelChannelConnection,
    OmnichannelChannelEvent,
    OmnichannelEndpoint,
    OmnichannelIdentity,
    OmnichannelIdentityMutationEvent,
    OmnichannelIdentityReviewCase,
    OmnichannelMessageCostLedgerEntry,
    OmnichannelMessageDeliveryEvent,
    OmnichannelScheduledMessage,
    OmnichannelThreadBinding,
    WorkspaceTeam,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.omnichannel_v6 import (
    CallSessionTransition,
    InternalCallSessionTransition,
    InternalMessageDeliveryReceiptIngest,
    MessageDeliveryReceiptIngest,
)
from app.services.omnichannel_v6_service import (
    OmnichannelConflictError,
    OmnichannelV6Service,
)


pytestmark = pytest.mark.usefixtures("active_subscription")


def _channel_event(
    *,
    channel: str,
    provider: str,
    external_event_id: str,
    external_thread_id: str,
    body: str,
    profile_avatar_url: str | None = None,
) -> dict:
    return {
        "provider": provider,
        "connectionKey": f"{provider}:principal",
        "channel": channel,
        "eventType": "message.received",
        "externalEventId": external_event_id,
        "externalThreadId": external_thread_id,
        "externalUserId": f"user:{channel}:ana",
        "externalAccountId": f"account:{channel}:principal",
        "displayName": "Ana Paula",
        "profileAvatarUrl": profile_avatar_url,
        "address": "@anapaula" if channel == "instagram" else "+5511999999999",
        "canonicalPhone": "+5511999999999",
        "direction": "inbound",
        "authorType": "client",
        "authorLabel": "Ana Paula",
        "body": body,
        "visibility": "client_private",
        "actionability": "reply_required",
        "metadata": {"accessToken": "never-persist-me", "providerStatus": "received"},
    }


@pytest.mark.asyncio
async def test_cross_channel_events_resolve_to_one_identity_and_thread(client, db_session):
    whatsapp = _channel_event(
        channel="whatsapp",
        provider="meta_cloud",
        external_event_id="wamid.ana.1",
        external_thread_id="wa-thread-ana",
        body="Meu plano negou o medicamento.",
        profile_avatar_url="https://cdn.example.test/whatsapp-ana.jpg",
    )
    instagram = _channel_event(
        channel="instagram",
        provider="meta_instagram",
        external_event_id="ig-message-ana-1",
        external_thread_id="ig-thread-ana",
        body="Também falei com vocês pelo WhatsApp.",
        profile_avatar_url="https://cdn.example.test/instagram-ana.jpg",
    )

    first = await client.post("/api/v1/klm/omnichannel/events/ingest", json=whatsapp)
    second = await client.post("/api/v1/klm/omnichannel/events/ingest", json=instagram)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["identity"]["id"] == second.json()["identity"]["id"]
    assert first.json()["thread"]["id"] == second.json()["thread"]["id"]
    assert first.json()["binding"]["id"] != second.json()["binding"]["id"]
    assert first.json()["event"]["eventMetadata"]["accessToken"] == "[REDACTED]"
    assert first.json()["event"]["eventMetadata"]["channel"] == "whatsapp"
    assert second.json()["thread"]["threadMetadata"]["profileAvatar"] == {
        "url": "https://cdn.example.test/whatsapp-ana.jpg",
        "sourceChannel": "whatsapp",
        "provider": "meta_cloud",
        "observedAt": first.json()["thread"]["threadMetadata"]["profileAvatar"]["observedAt"],
    }

    assert await db_session.scalar(select(func.count()).select_from(OmnichannelIdentity)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelThread)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelEndpoint)) == 2
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelThreadBinding)) == 2
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelMessage)) == 2


@pytest.mark.asyncio
async def test_bound_whatsapp_thread_accepts_brazilian_ninth_digit_alias(client, db_session):
    first = _channel_event(
        channel="whatsapp",
        provider="meta_cloud",
        external_event_id="wamid.br-alias.1",
        external_thread_id="wa-thread-br-alias",
        body="Primeira mensagem.",
    )
    first.update(
        {
            "externalUserId": "556182502136",
            "address": "556182502136",
            "canonicalPhone": "556182502136",
        }
    )
    second = {
        **first,
        "externalEventId": "wamid.br-alias.2",
        "externalUserId": "5561982502136",
        "address": "5561982502136",
        "canonicalPhone": "5561982502136",
        "body": "Segunda mensagem.",
    }

    original = await client.post("/api/v1/klm/omnichannel/events/ingest", json=first)
    aliased = await client.post("/api/v1/klm/omnichannel/events/ingest", json=second)

    assert original.status_code == 201, original.text
    assert aliased.status_code == 201, aliased.text
    assert aliased.json()["identity"]["id"] == original.json()["identity"]["id"]
    assert aliased.json()["thread"]["id"] == original.json()["thread"]["id"]
    assert aliased.json()["endpoint"]["id"] == original.json()["endpoint"]["id"]
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelIdentity)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelThread)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelMessage)) == 2


@pytest.mark.asyncio
async def test_channel_event_replay_is_idempotent_and_payload_conflict_is_rejected(
    client, db_session
):
    payload = _channel_event(
        channel="whatsapp",
        provider="meta_cloud",
        external_event_id="wamid.idempotent.1",
        external_thread_id="wa-thread-idempotent",
        body="Preciso de ajuda.",
    )
    first = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    replay = await client.post("/api/v1/klm/omnichannel/events/ingest", json=payload)
    changed = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json={**payload, "body": "Conteúdo divergente."},
    )

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert replay.json()["event"]["id"] == first.json()["event"]["id"]
    assert changed.status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelChannelEvent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelMessage)) == 1


@pytest.mark.asyncio
async def test_team_membership_and_versioned_transfer(client, auth_context):
    team_response = await client.post(
        "/api/v1/klm/omnichannel/teams",
        json={
            "name": "Saúde urgente",
            "slug": "saude-urgente",
            "teamType": "legal",
            "color": "#1A9948",
            "routingStrategy": "least_loaded",
        },
    )
    assert team_response.status_code == 201, team_response.text
    team = team_response.json()
    member_response = await client.post(
        f"/api/v1/klm/omnichannel/teams/{team['id']}/members",
        json={"userId": str(auth_context.user_id), "memberRole": "lead"},
    )
    assert member_response.status_code == 201, member_response.text

    thread_response = await client.post(
        "/api/v1/klm/omnichannel/threads",
        json={"primaryChannel": "whatsapp", "subject": "Caso para transferência"},
    )
    assert thread_response.status_code == 201, thread_response.text
    thread = thread_response.json()
    transfer_payload = {
        "expectedVersion": thread["version"],
        "idempotencyKey": f"transfer-{uuid4()}",
        "toTeamId": team["id"],
        "toUserId": str(auth_context.user_id),
        "queue": "legal_review",
        "reason": "Triagem jurídica necessária.",
    }
    transferred = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/transfer",
        json=transfer_payload,
    )
    replay = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/transfer",
        json=transfer_payload,
    )
    stale = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread['id']}/transfer",
        json={
            **transfer_payload,
            "idempotencyKey": f"transfer-{uuid4()}",
        },
    )

    assert transferred.status_code == 200, transferred.text
    assert transferred.json()["thread"]["assignedTeamId"] == team["id"]
    assert transferred.json()["thread"]["assignedHumanId"] == str(auth_context.user_id)
    assert transferred.json()["thread"]["queue"] == "legal_review"
    assert replay.status_code == 200
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert stale.status_code == 409
    assert stale.headers["X-KLM-Current-Version"] == str(thread["version"] + 1)

    teams = await client.get("/api/v1/klm/omnichannel/teams")
    assert teams.status_code == 200
    current = next(item for item in teams.json() if item["id"] == team["id"])
    assert current["memberCount"] == 1
    assert current["availableCount"] == 1
    assert current["openThreadCount"] == 1


@pytest.mark.asyncio
async def test_lead_capture_creates_one_health_case_and_replays_without_duplicates(
    client, db_session
):
    payload = {
        "provider": "meta_ads",
        "connectionKey": "meta-leads:principal",
        "externalLeadId": "meta-lead-001",
        "externalFormId": "form-negativa-saude",
        "channel": "whatsapp",
        "name": "Bruna Santos",
        "phone": "+5511988887777",
        "demandType": "Negativa de medicamento oncológico",
        "healthSubtheme": "medicamento_alto_custo",
        "healthObjectName": "Pembrolizumabe",
        "healthInsurer": "Plano Exemplo",
        "healthDenialReason": "Fora do rol",
        "medicalUrgencyHours": 24,
        "source": "Meta Lead Ads",
        "campaign": "Saúde - Pesquisa e remarketing",
        "adSet": "Oncologia",
        "ad": "Negativa urgente",
        "clickIds": {"fbclid": "fixture-click-id"},
        "fields": {"consentimento": True},
        "proactiveMessageTemplate": "triagem_saude_v1",
    }
    first = await client.post("/api/v1/klm/omnichannel/lead-captures/ingest", json=payload)
    replay = await client.post("/api/v1/klm/omnichannel/lead-captures/ingest", json=payload)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert replay.json()["thread"]["id"] == first.json()["thread"]["id"]
    assert await db_session.scalar(select(func.count()).select_from(Client)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 1
    assert await db_session.scalar(select(func.count()).select_from(LeadCaptureReceipt)) == 1

    scheduled = await db_session.scalar(select(OmnichannelScheduledMessage))
    assert scheduled is not None
    assert scheduled.state == "awaiting_connection"
    assert scheduled.connection_key == "unconfigured:whatsapp"

    matter = await db_session.scalar(select(Matter))
    assert matter is not None
    assert matter.health_object_name == "Pembrolizumabe"
    assert matter.medical_urgency_hours == 24

    captures = await client.get("/api/v1/klm/omnichannel/lead-captures")
    assert captures.status_code == 200, captures.text
    assert captures.json()["total"] == 1
    assert captures.json()["countsByProvider"] == {"meta_ads": 1}
    assert captures.json()["items"][0]["thread"]["id"] == first.json()["thread"]["id"]
    assert captures.json()["items"][0]["acquisition"]["campaign"] == payload["campaign"]


@pytest.mark.asyncio
async def test_same_verified_person_can_have_two_isolated_cases_and_review_queue(
    client, db_session
):
    base = {
        "provider": "meta_ads",
        "connectionKey": "meta-leads:multi-case",
        "externalFormId": "form-saude",
        "channel": "whatsapp",
        "name": "Pessoa com dois casos",
        "phone": "+5511966665511",
        "source": "Meta Lead Ads",
    }
    first = await client.post(
        "/api/v1/klm/omnichannel/lead-captures/ingest",
        json={
            **base,
            "externalLeadId": "multi-case-lead-1",
            "demandType": "Negativa de medicamento",
            "healthObjectName": "Medicamento A",
            "campaign": "Campanha A",
        },
    )
    second = await client.post(
        "/api/v1/klm/omnichannel/lead-captures/ingest",
        json={
            **base,
            "externalLeadId": "multi-case-lead-2",
            "demandType": "Negativa de cirurgia",
            "healthObjectName": "Cirurgia B",
            "campaign": "Campanha B",
        },
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["identity"]["id"] == second.json()["identity"]["id"]
    assert first.json()["clientId"] == second.json()["clientId"]
    assert first.json()["matterId"] != second.json()["matterId"]
    assert first.json()["thread"]["id"] != second.json()["thread"]["id"]
    assert first.json()["thread"]["matterId"] == first.json()["matterId"]
    assert second.json()["thread"]["matterId"] == second.json()["matterId"]
    assert first.json()["receipt"]["mappingVersion"] == "v7"
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelIdentity)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Client)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 2
    assert await db_session.scalar(select(func.count()).select_from(OmnichannelThread)) == 2
    assert (
        await db_session.scalar(
            select(func.count()).select_from(OmnichannelIdentityReviewCase)
        )
        == 1
    )

    reviews = await client.get("/api/v1/klm/omnichannel/identity-reviews")
    assert reviews.status_code == 200, reviews.text
    assert len(reviews.json()) == 1
    assert reviews.json()[0]["identityId"] == first.json()["identity"]["id"]
    assert reviews.json()[0]["matterId"] == second.json()["matterId"]


@pytest.mark.asyncio
async def test_explicit_case_links_a_new_social_channel_to_the_existing_case(client):
    capture = await client.post(
        "/api/v1/klm/omnichannel/lead-captures/ingest",
        json={
            "provider": "site",
            "connectionKey": "lovable:case-link",
            "externalLeadId": "case-link-lead-1",
            "channel": "webchat",
            "name": "Lead omnichannel",
            "phone": "+5511955554411",
            "demandType": "Negativa de tratamento",
        },
    )
    assert capture.status_code == 201, capture.text
    event = _channel_event(
        channel="instagram",
        provider="meta_instagram",
        external_event_id="ig-explicit-case-1",
        external_thread_id="ig-explicit-case-thread-1",
        body="Continuei pelo Instagram.",
    )
    event.update(
        {
            "canonicalPhone": "+5511955554411",
            "matterId": capture.json()["matterId"],
        }
    )
    linked = await client.post("/api/v1/klm/omnichannel/events/ingest", json=event)

    assert linked.status_code == 201, linked.text
    assert linked.json()["identity"]["id"] == capture.json()["identity"]["id"]
    assert linked.json()["thread"]["id"] == capture.json()["thread"]["id"]
    assert linked.json()["thread"]["matterId"] == capture.json()["matterId"]


@pytest.mark.asyncio
async def test_identity_merge_is_versioned_audited_and_idempotent(
    client, db_session, auth_context
):
    capture = await client.post(
        "/api/v1/klm/omnichannel/lead-captures/ingest",
        json={
            "provider": "meta_ads",
            "connectionKey": "meta-leads:identity-merge",
            "externalLeadId": "identity-merge-lead-1",
            "channel": "whatsapp",
            "name": "Pessoa principal",
            "phone": "+5511944443311",
            "demandType": "Negativa de medicamento",
        },
    )
    assert capture.status_code == 201, capture.text
    client_id = UUID(capture.json()["clientId"])
    source_matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client_id,
        demand_type="Negativa de home care",
    )
    source_identity = OmnichannelIdentity(
        workspace_id=auth_context.workspace_id,
        client_id=client_id,
        display_name="Pessoa duplicada",
        identity_state="provisional",
        source="manual_review",
        identity_metadata={},
    )
    db_session.add_all([source_matter, source_identity])
    await db_session.flush()
    source_thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        identity_id=source_identity.id,
        client_id=client_id,
        matter_id=source_matter.id,
        subject="Caso separado antes da união",
    )
    db_session.add(source_thread)
    await db_session.flush()

    payload = {
        "sourceIdentityId": str(source_identity.id),
        "expectedSourceVersion": 1,
        "expectedTargetVersion": 1,
        "idempotencyKey": f"identity-merge-{uuid4()}",
        "reason": "Revisão humana confirmou a mesma pessoa.",
    }
    endpoint = f"/api/v1/klm/omnichannel/identities/{capture.json()['identity']['id']}/merge"
    merged = await client.post(endpoint, json=payload)
    replay = await client.post(endpoint, json=payload)

    assert merged.status_code == 200, merged.text
    assert replay.status_code == 200, replay.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert merged.json()["source"]["identityState"] == "merged"
    assert merged.json()["target"]["mergeVersion"] == 2
    assert merged.json()["movedThreadIds"] == [str(source_thread.id)]
    await db_session.refresh(source_thread)
    assert str(source_thread.identity_id) == capture.json()["identity"]["id"]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(OmnichannelIdentityMutationEvent)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_identity_split_moves_selected_thread_and_its_bound_endpoint(
    client, db_session, auth_context
):
    capture = await client.post(
        "/api/v1/klm/omnichannel/lead-captures/ingest",
        json={
            "provider": "meta_ads",
            "connectionKey": "meta-leads:identity-split",
            "externalLeadId": "identity-split-lead-1",
            "channel": "whatsapp",
            "name": "Pessoa para revisão",
            "phone": "+5511933332211",
            "demandType": "Negativa de medicamento",
        },
    )
    assert capture.status_code == 201, capture.text
    second_matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=UUID(capture.json()["clientId"]),
        demand_type="Negativa de cirurgia",
    )
    db_session.add(second_matter)
    await db_session.flush()
    social_event = {
        "provider": "meta_instagram",
        "connectionKey": "instagram:identity-split",
        "channel": "instagram",
        "eventType": "message.received",
        "externalEventId": "ig-identity-split-1",
        "externalThreadId": "ig-identity-split-thread-1",
        "externalUserId": "ig-user-split-1",
        "displayName": "Perfil social separado",
        "address": "@perfil_separado",
        "clientId": capture.json()["clientId"],
        "matterId": str(second_matter.id),
        "direction": "inbound",
        "authorType": "client",
        "authorLabel": "Perfil social separado",
        "body": "Esta conta foi vinculada por engano.",
    }
    social = await client.post("/api/v1/klm/omnichannel/events/ingest", json=social_event)
    assert social.status_code == 201, social.text
    assert social.json()["identity"]["id"] == capture.json()["identity"]["id"]
    assert social.json()["thread"]["id"] != capture.json()["thread"]["id"]

    payload = {
        "threadIds": [social.json()["thread"]["id"]],
        "displayName": "Perfil social corrigido",
        "expectedSourceVersion": 1,
        "idempotencyKey": f"identity-split-{uuid4()}",
        "reason": "Revisão humana confirmou associação incorreta do perfil.",
    }
    endpoint = (
        f"/api/v1/klm/omnichannel/identities/{capture.json()['identity']['id']}/split"
    )
    split = await client.post(endpoint, json=payload)
    replay = await client.post(endpoint, json=payload)

    assert split.status_code == 200, split.text
    assert replay.status_code == 200, replay.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert split.json()["source"]["id"] == capture.json()["identity"]["id"]
    assert split.json()["target"]["id"] != split.json()["source"]["id"]
    assert split.json()["movedThreadIds"] == [social.json()["thread"]["id"]]
    assert split.json()["movedEndpointIds"] == [social.json()["endpoint"]["id"]]
    moved_thread = await db_session.get(
        OmnichannelThread,
        social.json()["thread"]["id"],
    )
    assert moved_thread is not None
    assert str(moved_thread.identity_id) == split.json()["target"]["id"]


@pytest.mark.asyncio
async def test_lead_capture_routes_proactive_message_through_default_official_number(
    client, db_session
):
    connection_response = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "whatsapp",
            "provider": "meta_cloud",
            "connectionKey": "whatsapp:comercial",
            "displayName": "WhatsApp comercial",
            "externalAccountId": "phone-number-id-local",
            "transportMode": "local_fixture",
            "isDefault": True,
        },
    )
    assert connection_response.status_code == 201, connection_response.text

    capture = await client.post(
        "/api/v1/klm/omnichannel/lead-captures/ingest",
        json={
            "provider": "tiktok_ads",
            "connectionKey": "tiktok-ads:principal",
            "externalLeadId": "tiktok-lead-route-001",
            "externalFormId": "tiktok-form-saude",
            "channel": "tiktok",
            "name": "Larissa Andrade",
            "phone": "+5511977776655",
            "source": "TikTok Instant Form",
            "campaign": "Negativa de tratamento",
            "proactiveMessageTemplate": "triagem_saude_v1",
        },
    )
    assert capture.status_code == 201, capture.text
    receipt = capture.json()["receipt"]
    assert receipt["result"]["proactiveRoute"]["connectionKey"] == "whatsapp:comercial"
    assert receipt["result"]["proactiveRoute"]["state"] == "scheduled"

    scheduled = await db_session.scalar(
        select(OmnichannelScheduledMessage).where(
            OmnichannelScheduledMessage.id == receipt["result"]["scheduledMessageId"]
        )
    )
    assert scheduled is not None
    assert scheduled.endpoint_id is not None
    assert scheduled.connection_key == "whatsapp:comercial"
    assert scheduled.state == "scheduled"
    connection = await db_session.scalar(select(OmnichannelChannelConnection))
    endpoint = await db_session.scalar(
        select(OmnichannelEndpoint).where(OmnichannelEndpoint.id == scheduled.endpoint_id)
    )
    assert connection is not None and endpoint is not None
    assert endpoint.connection_id == connection.id
    assert str(endpoint.identity_id) == capture.json()["identity"]["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "channel", "raw_payload", "expected_name"),
    [
        (
            "meta_ads",
            "facebook",
            {
                "id": "meta-raw-001",
                "form_id": "meta-form-001",
                "campaign_name": "Meta saúde",
                "field_data": [
                    {"name": "full_name", "values": ["Marina Lopes"]},
                    {"name": "phone_number", "values": ["+5511911112222"]},
                    {"name": "medicamento", "values": ["Nivolumabe"]},
                ],
                "access_token": "never-store",
            },
            "Marina Lopes",
        ),
        (
            "tiktok_ads",
            "tiktok",
            {
                "data": {
                    "lead_id": "tiktok-raw-001",
                    "form_id": "tiktok-form-001",
                    "campaign_name": "TikTok saúde",
                    "field_data": [
                        {"name": "full_name", "values": ["Caio Nunes"]},
                        {"name": "phone_number", "values": ["+5511922223333"]},
                    ],
                }
            },
            "Caio Nunes",
        ),
        (
            "lovable",
            "webchat",
            {
                "submission_id": "lovable-raw-001",
                "full_name": "Sofia Reis",
                "phone_number": "+5511933334444",
                "campaign": "Landing saúde",
                "client_secret": "never-store",
            },
            "Sofia Reis",
        ),
    ],
)
async def test_raw_provider_payloads_use_the_same_native_capture_boundary(
    client, provider, channel, raw_payload, expected_name
):
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": channel,
            "provider": provider,
            "connectionKey": f"{provider}:raw-test",
            "displayName": f"{provider} local",
            "transportMode": "local_fixture",
            "isDefault": True,
            "configuration": {"mappingVersion": "v6"},
        },
    )
    assert connection.status_code == 201, connection.text
    endpoint = (
        f"/api/v1/klm/omnichannel/connections/{connection.json()['id']}/lead-captures/ingest-raw"
    )
    first = await client.post(endpoint, json={"rawPayload": raw_payload})
    replay = await client.post(endpoint, json={"rawPayload": raw_payload})

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert first.headers["X-KLM-Normalizer"] == "native-v6"
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert first.json()["identity"]["displayName"] == expected_name
    assert first.json()["thread"]["id"] == replay.json()["thread"]["id"]
    serialized = str(first.json()["receipt"]["result"])
    assert "never-store" not in serialized


@pytest.mark.asyncio
async def test_schedule_call_and_timeline_remain_local(client):
    event = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_channel_event(
            channel="whatsapp",
            provider="meta_cloud",
            external_event_id="wamid.timeline.1",
            external_thread_id="wa-thread-timeline",
            body="Quero falar por áudio amanhã.",
        ),
    )
    assert event.status_code == 201, event.text
    thread_id = event.json()["thread"]["id"]
    endpoint_id = event.json()["endpoint"]["id"]

    scheduled_at = datetime.now(UTC) + timedelta(hours=2)
    scheduled = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/scheduled-messages",
        json={
            "endpointId": endpoint_id,
            "connectionKey": "meta_cloud:principal",
            "channel": "whatsapp",
            "body": "Lembrete da nossa conversa.",
            "scheduledAt": scheduled_at.isoformat(),
            "idempotencyKey": f"schedule-{uuid4()}",
        },
    )
    assert scheduled.status_code == 201, scheduled.text
    assert scheduled.json()["providerReceipt"]["externalActionsExecuted"] == 0
    assert scheduled.json()["authorType"] == "human"
    assert scheduled.json()["createdByUserId"] is not None
    pause_key = f"schedule-pause-{uuid4()}"
    paused = await client.patch(
        f"/api/v1/klm/omnichannel/scheduled-messages/{scheduled.json()['id']}",
        json={
            "action": "pause",
            "reason": "Aguardar confirmação.",
            "idempotencyKey": pause_key,
        },
    )
    paused_replay = await client.patch(
        f"/api/v1/klm/omnichannel/scheduled-messages/{scheduled.json()['id']}",
        json={
            "action": "pause",
            "reason": "Aguardar confirmação.",
            "idempotencyKey": pause_key,
        },
    )
    resumed = await client.patch(
        f"/api/v1/klm/omnichannel/scheduled-messages/{scheduled.json()['id']}",
        json={"action": "resume", "idempotencyKey": f"schedule-resume-{uuid4()}"},
    )
    assert paused.status_code == 200
    assert paused_replay.status_code == 200
    assert paused.json()["state"] == "paused"
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "scheduled"
    schedule_events = await client.get(
        f"/api/v1/klm/omnichannel/scheduled-messages/{scheduled.json()['id']}/events"
    )
    assert schedule_events.status_code == 200, schedule_events.text
    assert [item["eventType"] for item in schedule_events.json()] == [
        "created",
        "pause",
        "resume",
    ]

    call = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/calls",
        json={
            "endpointId": endpoint_id,
            "connectionKey": "meta_cloud:principal",
            "channel": "whatsapp",
            "direction": "outbound",
            "permissionState": "requested",
            "idempotencyKey": f"call-{uuid4()}",
        },
    )
    assert call.status_code == 201, call.text
    approved = await client.patch(
        f"/api/v1/klm/omnichannel/calls/{call.json()['id']}",
        json={
            "idempotencyKey": f"call-approved-{uuid4()}",
            "state": "approved_local",
            "permissionState": "granted",
        },
    )
    blocked_ring = await client.patch(
        f"/api/v1/klm/omnichannel/calls/{call.json()['id']}",
        json={
            "idempotencyKey": f"call-ringing-{uuid4()}",
            "state": "ringing",
            "providerCallId": "must-not-run",
        },
    )
    assert approved.status_code == 200
    assert blocked_ring.status_code == 409
    evidence = await client.get(
        f"/api/v1/klm/omnichannel/calls/{call.json()['id']}/events"
    )
    assert evidence.status_code == 200, evidence.text
    assert [item["event"]["nextState"] for item in evidence.json()] == [
        "requested",
        "approved_local",
    ]

    timeline = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/timeline")
    assert timeline.status_code == 200, timeline.text
    kinds = {item["kind"] for item in timeline.json()["items"]}
    assert {"message", "channel_event", "scheduled_message", "call"}.issubset(kinds)
    assert any(
        item["kind"] == "channel_event" and item["channel"] == "whatsapp"
        for item in timeline.json()["items"]
    )


@pytest.mark.asyncio
async def test_call_provider_cost_is_internal_idempotent_and_hidden_from_tenant(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(
        "app.services.omnichannel_v6_service.external_delivery_allowed", lambda: True
    )
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "whatsapp",
            "provider": "meta_cloud",
            "connectionKey": "meta_cloud:principal",
            "displayName": "WhatsApp de teste",
            "transportMode": "cloud_api",
            "isDefault": True,
        },
    )
    assert connection.status_code == 201, connection.text
    event = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_channel_event(
            channel="whatsapp",
            provider="meta_cloud",
            external_event_id="wamid.call-cost.1",
            external_thread_id="wa-thread-call-cost",
            body="Pode me ligar?",
        ),
    )
    assert event.status_code == 201, event.text
    thread_id = event.json()["thread"]["id"]
    call = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/calls",
        json={
            "endpointId": event.json()["endpoint"]["id"],
            "connectionKey": "meta_cloud:principal",
            "channel": "whatsapp",
            "direction": "outbound",
            "permissionState": "requested",
            "idempotencyKey": f"call-{uuid4()}",
        },
    )
    assert call.status_code == 201, call.text
    call_id = call.json()["id"]
    transitions = (
        {
            "idempotencyKey": "call-step-approved",
            "state": "approved_local",
            "permissionState": "granted",
        },
        {
            "idempotencyKey": "call-step-ringing",
            "state": "ringing",
            "providerCallId": "provider-call-1",
        },
        {"idempotencyKey": "call-step-active", "state": "active"},
    )
    for payload in transitions:
        response = await client.patch(f"/api/v1/klm/omnichannel/calls/{call_id}", json=payload)
        assert response.status_code == 200, response.text

    completed_payload = {
        "idempotencyKey": "call-step-completed",
        "state": "completed",
        "providerCallId": "provider-call-1",
        "durationSeconds": 187,
        "summary": "Cliente confirmou os documentos pendentes.",
        "costAmount": "1.250000",
        "costCurrency": "BRL",
        "pricingCategory": "whatsapp_audio",
        "providerInvoiceReference": "invoice-line-42",
        "externalEventId": "provider-call-event-42",
    }
    internal_payload = InternalCallSessionTransition.model_validate(completed_payload)
    service = OmnichannelV6Service(db_session)
    completed_call = await service.transition_call(
        UUID(event.json()["thread"]["workspaceId"]),
        UUID(call_id),
        internal_payload,
    )
    replay = await service.transition_call(
        UUID(event.json()["thread"]["workspaceId"]),
        UUID(call_id),
        internal_payload,
    )
    with pytest.raises(OmnichannelConflictError):
        await service.transition_call(
            UUID(event.json()["thread"]["workspaceId"]),
            UUID(call_id),
            InternalCallSessionTransition.model_validate(
                {**completed_payload, "summary": "Conteúdo divergente."}
            ),
        )
    assert replay.id == completed_call.id

    public_contract = CallSessionTransition.model_validate(completed_payload)
    assert "costAmount" not in public_contract.model_dump(mode="json", by_alias=True)
    assert "costAmount" not in CallSessionTransition.model_json_schema(by_alias=True)["properties"]
    calls = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/calls")
    assert calls.status_code == 200, calls.text
    tenant_call = next(item for item in calls.json() if item["id"] == call_id)
    assert "costAmount" not in tenant_call
    assert "costCurrency" not in tenant_call
    assert "actualCostsByCurrency" not in tenant_call["callMetadata"]

    evidence = await client.get(f"/api/v1/klm/omnichannel/calls/{call_id}/events")
    assert evidence.status_code == 200, evidence.text
    assert [item["event"]["nextState"] for item in evidence.json()] == [
        "requested",
        "approved_local",
        "ringing",
        "active",
        "completed",
    ]
    assert all("costEntries" not in item for item in evidence.json())
    assert (await db_session.scalar(select(func.count()).select_from(OmnichannelCallEvent))) == 5
    assert (
        await db_session.scalar(select(func.count()).select_from(OmnichannelCallCostLedgerEntry))
    ) == 1
    stored_call = await db_session.scalar(
        select(OmnichannelCallSession).where(OmnichannelCallSession.id == UUID(call_id))
    )
    assert stored_call is not None
    assert str(stored_call.cost_amount) == "1.250000"
    assert stored_call.cost_currency == "BRL"


@pytest.mark.asyncio
async def test_hybrid_whatsapp_routes_share_thread_and_preserve_selected_transport(
    client, db_session
):
    connection_specs = [
        ("whatsapp-cloud-test", "Cloud API", "cloud_api"),
        ("whatsapp-coexistence-test", "Coexistência", "coexistence"),
    ]
    connections = {}
    for connection_key, display_name, transport_mode in connection_specs:
        response = await client.post(
            "/api/v1/klm/omnichannel/connections",
            json={
                "channel": "whatsapp",
                "provider": "meta_cloud",
                "connectionKey": connection_key,
                "displayName": display_name,
                "transportMode": transport_mode,
                "isDefault": transport_mode == "cloud_api",
            },
        )
        assert response.status_code == 201, response.text
        connections[transport_mode] = response.json()

    cloud_payload = _channel_event(
        channel="whatsapp",
        provider="meta_cloud",
        external_event_id="wamid.hybrid.cloud.1",
        external_thread_id="wa-hybrid-cloud",
        body="Comecei pela API oficial.",
    )
    cloud_payload["connectionKey"] = "whatsapp-cloud-test"
    coexistence_payload = _channel_event(
        channel="whatsapp",
        provider="meta_cloud",
        external_event_id="wamid.hybrid.coexistence.1",
        external_thread_id="wa-hybrid-coexistence",
        body="Continuei pelo aplicativo conectado.",
    )
    coexistence_payload["connectionKey"] = "whatsapp-coexistence-test"

    cloud = await client.post("/api/v1/klm/omnichannel/events/ingest", json=cloud_payload)
    coexistence = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=coexistence_payload,
    )
    assert cloud.status_code == 201, cloud.text
    assert coexistence.status_code == 201, coexistence.text
    assert cloud.json()["thread"]["id"] == coexistence.json()["thread"]["id"]

    thread_id = cloud.json()["thread"]["id"]
    context = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/context")
    assert context.status_code == 200, context.text
    routes = {item["transportMode"]: item for item in context.json()["channels"]}
    assert {"cloud_api", "coexistence"}.issubset(routes)
    assert routes["coexistence"]["endpointId"]
    assert routes["coexistence"]["bindingId"]

    selected = routes["coexistence"]
    outbound_payload = {
        "endpointId": selected["endpointId"],
        "bindingId": selected["bindingId"],
        "channel": "whatsapp",
        "direction": "outbound",
        "authorType": "human",
        "authorLabel": "Operação local",
        "body": "Resposta preparada na rota de coexistência.",
        "transport": "coexistence",
        "costAmount": "9.990000",
        "costCurrency": "BRL",
        "idempotencyKey": "hybrid-coexistence-outbound-1",
        "messageMetadata": {
            "source": "spa_human_composer",
            "connectionId": connections["coexistence"]["id"],
            "connectionKey": "whatsapp-coexistence-test",
            "estimatedCostBrl": "9.990000",
        },
    }
    message = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json=outbound_payload,
    )
    assert message.status_code == 201, message.text
    assert message.json()["endpointId"] == selected["endpointId"]
    assert message.json()["bindingId"] == selected["bindingId"]
    assert message.json()["transport"] == "coexistence"
    assert message.json()["deliveryStatus"] == "local_draft"
    assert "costAmount" not in message.json()
    assert "costCurrency" not in message.json()
    assert "estimatedCostBrl" not in message.json()["messageMetadata"]
    stored_message = await db_session.scalar(
        select(OmnichannelMessage).where(OmnichannelMessage.id == UUID(message.json()["id"]))
    )
    assert stored_message is not None
    assert stored_message.cost_amount is None
    assert stored_message.cost_currency is None
    assert "estimatedCostBrl" not in stored_message.message_metadata

    replay = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json=outbound_payload,
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"

    refreshed_context = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/context")
    assert refreshed_context.status_code == 200, refreshed_context.text
    cost_state = refreshed_context.json()["thread"]["costState"]
    assert cost_state["messageCount"] == 3
    assert cost_state["inboundCount"] == 2
    assert cost_state["outboundCount"] == 1
    assert cost_state["byTransport"] == {"cloud_api": 1, "coexistence": 2}
    assert cost_state["byConnection"] == {
        "whatsapp-cloud-test": 1,
        "whatsapp-coexistence-test": 2,
    }
    assert "externalCost" not in cost_state
    assert "estimatedExternalCost" not in cost_state
    assert "estimatedCostsByCurrency" not in cost_state
    assert "actualCostsByCurrency" not in cost_state
    assert "costsByCurrency" not in cost_state
    assert "currency" not in cost_state

    wrong_transport = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json={
            "endpointId": selected["endpointId"],
            "bindingId": selected["bindingId"],
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Operação local",
            "body": "Rota inconsistente.",
            "transport": "cloud_api",
            "idempotencyKey": "hybrid-wrong-transport-1",
        },
    )
    assert wrong_transport.status_code == 409
    assert "Transporte" in wrong_transport.json()["detail"]


@pytest.mark.asyncio
async def test_rich_whatsapp_messages_are_normalized_persisted_and_idempotent(client):
    connection_key = f"rich-message-{uuid4().hex[:10]}"
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "whatsapp",
            "provider": "meta_cloud",
            "connectionKey": connection_key,
            "displayName": "WhatsApp conteúdo rico",
            "transportMode": "cloud_api",
        },
    )
    assert connection.status_code == 201, connection.text

    inbound = _channel_event(
        channel="whatsapp",
        provider="meta_cloud",
        external_event_id=f"rich-inbound-{uuid4()}",
        external_thread_id=f"rich-thread-{uuid4()}",
        body="Quero receber as opções.",
    )
    inbound["connectionKey"] = connection_key
    event = await client.post("/api/v1/klm/omnichannel/events/ingest", json=inbound)
    assert event.status_code == 201, event.text
    thread_id = event.json()["thread"]["id"]
    context = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/context")
    route = next(
        item for item in context.json()["channels"] if item["connectionKey"] == connection_key
    )
    route_fields = {
        "endpointId": route["endpointId"],
        "bindingId": route["bindingId"],
        "channel": "whatsapp",
        "direction": "outbound",
        "authorType": "human",
        "authorLabel": "Operação local",
        "transport": "cloud_api",
    }
    route_metadata = {
        "source": "spa_rich_composer",
        "connectionId": connection.json()["id"],
        "connectionKey": connection_key,
    }
    buttons_payload = {
        **route_fields,
        "body": "Como deseja continuar?",
        "contentType": "interactive_buttons",
        "idempotencyKey": "rich-buttons-idempotent-1",
        "messageMetadata": {
            **route_metadata,
            "structuredContent": {
                "kind": "reply_buttons",
                "body": "Como deseja continuar?",
                "buttons": [
                    {"id": "agendar", "title": "Agendar"},
                    {"id": "advogado", "title": "Falar com advogado"},
                ],
            },
        },
    }

    buttons = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json=buttons_payload,
    )
    replay = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json=buttons_payload,
    )

    assert buttons.status_code == replay.status_code == 201, buttons.text
    assert replay.headers["X-KLM-Deduplicated"] == "true"
    assert buttons.json()["contentType"] == "interactive_buttons"
    assert buttons.json()["deliveryStatus"] == "local_draft"
    assert buttons.json()["messageMetadata"]["structuredContent"] == {
        "kind": "reply_buttons",
        "body": "Como deseja continuar?",
        "header": None,
        "footer": None,
        "buttons": [
            {"id": "agendar", "title": "Agendar"},
            {"id": "advogado", "title": "Falar com advogado"},
        ],
    }

    media = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json={
            **route_fields,
            "body": "Segue a negativa vinculada ao caso.",
            "contentType": "media",
            "idempotencyKey": "rich-private-media-1",
            "messageMetadata": {
                **route_metadata,
                "attachments": [
                    {
                        "documentId": "document-local-123",
                        "filename": "negativa.pdf",
                        "mediaType": "document",
                    }
                ],
                "structuredContent": {
                    "kind": "media",
                    "type": "document",
                    "documentId": "document-local-123",
                    "filename": "negativa.pdf",
                    "caption": "Segue a negativa vinculada ao caso.",
                },
            },
        },
    )

    assert media.status_code == 201, media.text
    assert media.json()["deliveryStatus"] == "local_draft"
    assert media.json()["messageMetadata"]["structuredContent"]["localReference"] == {
        "documentId": "document-local-123"
    }
    assert media.json()["messageMetadata"]["structuredContent"]["requiresProviderUpload"] is True


@pytest.mark.asyncio
async def test_rich_message_contract_rejects_invalid_or_disabled_actions(client):
    connection_key = f"rich-disabled-{uuid4().hex[:10]}"
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "whatsapp",
            "provider": "meta_cloud",
            "connectionKey": connection_key,
            "displayName": "WhatsApp Flow desativado",
            "transportMode": "cloud_api",
            "capabilities": {"flows": False},
        },
    )
    assert connection.status_code == 201, connection.text
    inbound = _channel_event(
        channel="whatsapp",
        provider="meta_cloud",
        external_event_id=f"rich-disabled-inbound-{uuid4()}",
        external_thread_id=f"rich-disabled-thread-{uuid4()}",
        body="Quero continuar.",
    )
    inbound["connectionKey"] = connection_key
    event = await client.post("/api/v1/klm/omnichannel/events/ingest", json=inbound)
    assert event.status_code == 201, event.text
    thread_id = event.json()["thread"]["id"]
    context = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/context")
    route = next(
        item for item in context.json()["channels"] if item["connectionKey"] == connection_key
    )
    base = {
        "endpointId": route["endpointId"],
        "bindingId": route["bindingId"],
        "channel": "whatsapp",
        "authorType": "human",
        "authorLabel": "Operação local",
        "transport": "cloud_api",
    }
    metadata = {
        "connectionId": connection.json()["id"],
        "connectionKey": connection_key,
    }

    too_many_buttons = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json={
            **base,
            "direction": "outbound",
            "body": "Escolha uma opção.",
            "contentType": "interactive_buttons",
            "idempotencyKey": "rich-invalid-buttons-1",
            "messageMetadata": {
                **metadata,
                "structuredContent": {
                    "kind": "reply_buttons",
                    "body": "Escolha uma opção.",
                    "buttons": [
                        {"id": str(index), "title": f"Opção {index}"}
                        for index in range(4)
                    ],
                },
            },
        },
    )
    internal_rich = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json={
            **base,
            "direction": "internal",
            "body": "Nota privada.",
            "contentType": "interactive_buttons",
            "idempotencyKey": "rich-invalid-internal-1",
            "messageMetadata": {
                **metadata,
                "structuredContent": {
                    "kind": "reply_buttons",
                    "body": "Nota privada.",
                    "buttons": [{"id": "ok", "title": "OK"}],
                },
            },
        },
    )
    disabled_flow = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json={
            **base,
            "direction": "outbound",
            "body": "Preencha os dados.",
            "contentType": "whatsapp_flow",
            "idempotencyKey": "rich-disabled-flow-1",
            "messageMetadata": {
                **metadata,
                "structuredContent": {
                    "kind": "flow",
                    "body": "Preencha os dados.",
                    "flowId": "flow-intake",
                    "flowToken": "token-local",
                    "flowCta": "Preencher",
                    "action": "navigate",
                    "actionPayload": {"screen": "INTAKE"},
                    "mode": "published",
                },
            },
        },
    )

    assert too_many_buttons.status_code == 422, too_many_buttons.text
    assert internal_rich.status_code == 422, internal_rich.text
    assert disabled_flow.status_code == 409, disabled_flow.text
    assert "desativada" in disabled_flow.json()["detail"]


@pytest.mark.asyncio
async def test_delivery_receipts_are_monotonic_idempotent_and_cost_evidence_is_once(
    client, db_session
):
    connection_key = f"delivery-{uuid4().hex[:10]}"
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "whatsapp",
            "provider": "meta_cloud",
            "connectionKey": connection_key,
            "displayName": "WhatsApp recibos",
            "transportMode": "cloud_api",
        },
    )
    assert connection.status_code == 201, connection.text

    inbound = _channel_event(
        channel="whatsapp",
        provider="meta_cloud",
        external_event_id=f"inbound-{uuid4()}",
        external_thread_id=f"delivery-thread-{uuid4()}",
        body="Quero entender meus direitos.",
    )
    inbound["connectionKey"] = connection_key
    event = await client.post("/api/v1/klm/omnichannel/events/ingest", json=inbound)
    assert event.status_code == 201, event.text
    thread_id = event.json()["thread"]["id"]
    context = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/context")
    assert context.status_code == 200, context.text
    route = next(
        item
        for item in context.json()["channels"]
        if item["connectionKey"] == connection_key
    )

    outbound = await client.post(
        f"/api/v1/klm/omnichannel/threads/{thread_id}/messages",
        json={
            "endpointId": route["endpointId"],
            "bindingId": route["bindingId"],
            "channel": "whatsapp",
            "direction": "outbound",
            "authorType": "human",
            "authorLabel": "Advogado",
            "body": "Vou analisar sua negativa agora.",
            "transport": "cloud_api",
            "idempotencyKey": f"outbound-{uuid4()}",
            "messageMetadata": {
                "connectionId": connection.json()["id"],
                "connectionKey": connection_key,
                "provider": "meta_cloud",
            },
        },
    )
    assert outbound.status_code == 201, outbound.text
    message_id = outbound.json()["id"]
    assert outbound.json()["deliveryStatus"] == "local_draft"
    assert outbound.json()["connectionId"] == connection.json()["id"]

    sent_receipt = {
        "messageId": message_id,
        "provider": "meta_cloud",
        "connectionKey": connection_key,
        "externalEventId": f"receipt-sent-{uuid4()}",
        "externalMessageId": f"wamid.outbound.{uuid4()}",
        "status": "sent",
        "providerStatus": "accepted",
        "costAmount": "0.123456",
        "costCurrency": "brl",
        "pricingCategory": "utility",
        "payload": {"accessToken": "must-not-persist"},
    }
    internal_receipt = InternalMessageDeliveryReceiptIngest.model_validate(sent_receipt)
    service = OmnichannelV6Service(db_session)
    sent_message, sent_event, cost_entry, deduplicated = await service.ingest_delivery_receipt(
        UUID(event.json()["thread"]["workspaceId"]),
        internal_receipt,
    )
    (
        replay_message,
        replay_event,
        replay_cost,
        replay_deduplicated,
    ) = await service.ingest_delivery_receipt(
        UUID(event.json()["thread"]["workspaceId"]),
        internal_receipt,
    )
    with pytest.raises(OmnichannelConflictError):
        await service.ingest_delivery_receipt(
            UUID(event.json()["thread"]["workspaceId"]),
            InternalMessageDeliveryReceiptIngest.model_validate(
                {**sent_receipt, "providerStatus": "changed"}
            ),
        )
    assert sent_message.delivery_status.value == "sent"
    assert sent_event.applied is True
    assert sent_event.event_payload["accessToken"] == "[REDACTED]"
    assert cost_entry is not None
    assert str(cost_entry.amount) == "0.123456"
    assert deduplicated is False
    assert replay_message.id == sent_message.id
    assert replay_event.id == sent_event.id
    assert replay_cost is not None and replay_cost.id == cost_entry.id
    assert replay_deduplicated is True

    public_contract = MessageDeliveryReceiptIngest.model_validate(sent_receipt)
    assert "costAmount" not in public_contract.model_dump(mode="json", by_alias=True)
    assert (
        "costAmount"
        not in MessageDeliveryReceiptIngest.model_json_schema(by_alias=True)["properties"]
    )

    delivered_receipt = {
        **sent_receipt,
        "externalEventId": f"receipt-delivered-{uuid4()}",
        "status": "delivered",
        "providerStatus": "delivered",
    }
    delivered = await client.post(
        "/api/v1/klm/omnichannel/message-delivery-events/ingest",
        json=delivered_receipt,
    )
    stale = await client.post(
        "/api/v1/klm/omnichannel/message-delivery-events/ingest",
        json={
            **delivered_receipt,
            "externalEventId": f"receipt-stale-{uuid4()}",
            "status": "sent",
            "providerStatus": "sent-late",
        },
    )
    assert delivered.status_code == 201, delivered.text
    assert delivered.json()["message"]["deliveryStatus"] == "delivered"
    assert "costEntry" not in delivered.json()
    assert "costAmount" not in delivered.json()["message"]
    assert "costCurrency" not in delivered.json()["message"]
    assert "actualCostsByCurrency" not in delivered.json()["message"]["messageMetadata"]
    assert stale.status_code == 201, stale.text
    assert stale.json()["message"]["deliveryStatus"] == "delivered"
    assert stale.json()["event"]["applied"] is False
    assert stale.json()["event"]["ignoredReason"] == "stale_status"

    trail = await client.get(f"/api/v1/klm/omnichannel/messages/{message_id}/delivery-events")
    assert trail.status_code == 200, trail.text
    assert [item["projectedStatusAfter"] for item in trail.json()] == [
        "sent",
        "delivered",
        "delivered",
    ]
    assert (
        await db_session.scalar(select(func.count()).select_from(OmnichannelMessageDeliveryEvent))
        == 3
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(OmnichannelMessageCostLedgerEntry))
        == 1
    )

    refreshed = await client.get(f"/api/v1/klm/omnichannel/threads/{thread_id}/context")
    assert refreshed.status_code == 200
    tenant_cost_state = refreshed.json()["thread"]["costState"]
    assert tenant_cost_state["messageCount"] == 2
    assert "externalCost" not in tenant_cost_state
    assert "actualCostsByCurrency" not in tenant_cost_state
    assert "costsByCurrency" not in tenant_cost_state
    assert "currency" not in tenant_cost_state

    stored_message = await db_session.scalar(
        select(OmnichannelMessage).where(OmnichannelMessage.id == UUID(message_id))
    )
    stored_thread = await db_session.scalar(
        select(OmnichannelThread).where(OmnichannelThread.id == UUID(thread_id))
    )
    assert stored_message is not None
    assert str(stored_message.cost_amount) == "0.123456"
    assert stored_message.cost_currency == "BRL"
    assert stored_message.message_metadata["actualCostsByCurrency"] == {"BRL": "0.123456"}
    assert stored_thread is not None
    assert stored_thread.cost_state["externalCost"] == "0.123456"


@pytest.mark.asyncio
async def test_team_records_are_scoped_to_current_workspace(client, db_session, auth_context):
    foreign_workspace = Workspace(
        name="Workspace externo",
        slug=f"workspace-externo-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_team = WorkspaceTeam(
        workspace_id=foreign_workspace.id,
        name="Equipe externa",
        slug="equipe-externa",
        team_type="service",
        color="#111827",
        capacity=0,
        routing_strategy="manual",
        schedule={},
        is_active=True,
    )
    db_session.add(foreign_team)
    await db_session.flush()

    own_team = await client.post(
        "/api/v1/klm/omnichannel/teams",
        json={"name": "Equipe própria", "slug": "equipe-propria"},
    )
    assert own_team.status_code == 201, own_team.text
    rows = await client.get("/api/v1/klm/omnichannel/teams")
    assert rows.status_code == 200
    assert {item["workspaceId"] for item in rows.json()} == {str(auth_context.workspace_id)}
