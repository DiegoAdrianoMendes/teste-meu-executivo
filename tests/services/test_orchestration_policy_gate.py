"""`rate_limits`/`quiet_hours` deixam de ser decorativos + dedup cross-regra de
`private_reply`. Ver docs/PLANO_META_MAXIMO_2026-07-23.md seção 4-I1 itens 3-4 e
docs/AUDITORIA_META_CANAIS_2026-07-23.md linha 43 (ALTOS de Instagram)."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models.omnichannel_v6 import OmnichannelActionIntent
from app.services.omnichannel_orchestration_service import OmnichannelOrchestrationService


pytestmark = pytest.mark.usefixtures("active_subscription")


def _connection_payload(connection_key: str) -> dict:
    return {
        "channel": "instagram",
        "provider": "meta_instagram",
        "connectionKey": connection_key,
        "displayName": "Instagram KLM",
        "transportMode": "official_api",
        "configuration": {"pageId": "page-1", "instagramAccountId": "ig-klm"},
        "credentialReference": f"vault://local/{connection_key}",
        "isDefault": False,
    }


def _public_comment(*, external_event_id: str, external_user_id: str = "ig-user-ana") -> dict:
    return {
        "provider": "meta_instagram",
        "connectionKey": "instagram:gate-orchestration",
        "channel": "instagram",
        "eventType": "post_comment",
        "externalEventId": external_event_id,
        "externalThreadId": f"ig-post-123:comment-thread-{external_user_id}",
        "externalUserId": external_user_id,
        "externalAccountId": "ig-account-klm",
        "displayName": "Ana Paula",
        "address": "@anapaula",
        "direction": "inbound",
        "authorType": "client",
        "authorLabel": "Ana Paula",
        "body": "Preciso deste remédio urgente",
        "visibility": "public",
        "publicUrl": f"https://www.instagram.com/p/fixture/comment/{external_event_id}",
        "actionability": "reply_required",
        "correlation": {"postId": "ig-post-123", "commentId": external_event_id},
        "metadata": {},
        "payload": {},
    }


def _rule_payload(*, slug: str, action_key: str, **overrides) -> dict:
    base = {
        "name": f"Regra {slug}",
        "slug": slug,
        "channel": "instagram",
        "triggerType": "keyword_comment",
        "status": "active",
        "coexistWithAi": True,
        "stopOnMatch": False,
        "triggerConfig": {"keywords": ["remédio"], "sourceIds": ["ig-post-123"]},
        "conditions": [{"field": "visibility", "operator": "equals", "value": "public"}],
        "actions": [
            {
                "key": action_key,
                "type": "private_reply",
                "body": "Olá! Vamos conversar por aqui.",
            }
        ],
    }
    base.update(overrides)
    return base


async def _ingest(client, *, external_event_id: str, external_user_id: str = "ig-user-ana") -> dict:
    event = await client.post(
        "/api/v1/klm/omnichannel/events/ingest",
        json=_public_comment(
            external_event_id=external_event_id, external_user_id=external_user_id
        ),
    )
    assert event.status_code == 201, event.text
    return event.json()


@pytest.mark.asyncio
async def test_private_reply_dedup_across_rules(client, db_session):
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection_payload("instagram:gate-orchestration"),
    )
    assert connection.status_code == 201, connection.text

    rule_a = await client.post(
        "/api/v1/klm/omnichannel/automation-rules",
        json=_rule_payload(slug="dedup-regra-a", action_key="privada-a", priority=10),
    )
    rule_b = await client.post(
        "/api/v1/klm/omnichannel/automation-rules",
        json=_rule_payload(slug="dedup-regra-b", action_key="privada-b", priority=20),
    )
    assert rule_a.status_code == 201, rule_a.text
    assert rule_b.status_code == 201, rule_b.text

    event_body = await _ingest(client, external_event_id="ig-comment-dedup-001")

    service = OmnichannelOrchestrationService(db_session)
    workspace_id = UUID(event_body["event"]["workspaceId"]) if "workspaceId" in event_body["event"] else None
    if workspace_id is None:
        # workspace_id não é exposto no payload do evento; usar o da conexão criada.
        workspace_id = UUID(connection.json()["workspaceId"])
    runs = await service.trigger_active_automations_for_event(
        workspace_id, UUID(event_body["event"]["id"])
    )
    await db_session.commit()

    assert len(runs) == 2
    private_reply_intents = await db_session.scalar(
        select(func.count()).select_from(OmnichannelActionIntent).where(
            OmnichannelActionIntent.action_type == "private_reply",
            OmnichannelActionIntent.target_external_id == "ig-comment-dedup-001",
        )
    )
    assert private_reply_intents == 1


@pytest.mark.asyncio
async def test_rule_rate_limit_per_identity_per_hour_blocks_after_threshold(
    client, db_session
):
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection_payload("instagram:gate-orchestration"),
    )
    assert connection.status_code == 201, connection.text
    workspace_id = UUID(connection.json()["workspaceId"])

    rule = await client.post(
        "/api/v1/klm/omnichannel/automation-rules",
        json=_rule_payload(
            slug="rate-limit-identity",
            action_key="privada-rate",
            rateLimits={"perIdentityPerHour": 1},
        ),
    )
    assert rule.status_code == 201, rule.text

    service = OmnichannelOrchestrationService(db_session)

    first_event = await _ingest(client, external_event_id="ig-comment-rate-001")
    first_runs = await service.trigger_active_automations_for_event(
        workspace_id, UUID(first_event["event"]["id"])
    )
    await db_session.commit()
    assert first_runs[0].result.get("executionMode") == "live_local"

    second_event = await _ingest(client, external_event_id="ig-comment-rate-002")
    second_runs = await service.trigger_active_automations_for_event(
        workspace_id, UUID(second_event["event"]["id"])
    )
    await db_session.commit()
    assert second_runs[0].result.get("executionMode") == "skipped_rate_limited"
    assert second_runs[0].result.get("rateLimitReason") == "rate_limit_per_identity_per_hour"


@pytest.mark.asyncio
async def test_rule_quiet_hours_skips_materialization(client, db_session):
    connection = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json=_connection_payload("instagram:gate-orchestration"),
    )
    assert connection.status_code == 201, connection.text
    workspace_id = UUID(connection.json()["workspaceId"])

    rule = await client.post(
        "/api/v1/klm/omnichannel/automation-rules",
        json=_rule_payload(
            slug="quiet-hours-full-day",
            action_key="privada-quiet",
            quietHours={"enabled": True, "startHour": 0, "endHour": 0},
        ),
    )
    assert rule.status_code == 201, rule.text
    # startHour == endHour não bloqueia (ver _rule_quiet_hours_blocked); usar
    # uma janela que cobre qualquer hora do relógio de 24h é impossível com
    # start==end, então cobrimos as 24h com duas regras opostas não é
    # necessário: validamos a lógica pura em test_meta_policy_gate-adjacent
    # cases dentro deste arquivo via horário amplo abaixo.
    event_body = await _ingest(client, external_event_id="ig-comment-quiet-001")

    service = OmnichannelOrchestrationService(db_session)
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    # Quiet hours agora são avaliadas no fuso do workspace (baixo 8 da
    # auditoria Meta 23/07/2026), default America/Sao_Paulo quando o
    # workspace não configurou `settings.timezone`. Usar a hora local, não a
    # UTC, para configurar a janela que deve bloquear.
    hour = datetime.now(ZoneInfo("America/Sao_Paulo")).hour
    start = hour
    end = (hour + 1) % 24
    # Reconfigurar a regra para cobrir a hora atual explicitamente.
    from app.models.omnichannel_v6 import OmnichannelAutomationRule

    rule_row = await db_session.get(
        OmnichannelAutomationRule, UUID(rule.json()["id"])
    )
    rule_row.quiet_hours = {"enabled": True, "startHour": start, "endHour": end}
    await db_session.flush()

    runs = await service.trigger_active_automations_for_event(
        workspace_id, UUID(event_body["event"]["id"])
    )
    await db_session.commit()
    assert runs[0].result.get("executionMode") == "skipped_quiet_hours"
    materialized = await db_session.scalar(
        select(func.count()).select_from(OmnichannelActionIntent).where(
            OmnichannelActionIntent.target_external_id == "ig-comment-quiet-001"
        )
    )
    assert materialized == 0
