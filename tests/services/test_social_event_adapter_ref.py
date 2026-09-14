"""Auditoria 2026-07-26, item 2: referral.ref de deep links m.me/ig.me.

`SocialEventAdapterService._meta_message` extrai `referral.ref` (código do
deep link m.me/<page>?ref=<code> / ig.me/m/<user>?ref=<code>) para a
correlation do evento canônico — sem isto, conversas iniciadas por link não
tinham nenhuma evidência de origem para o funil de atribuição (item 9).
"""

from __future__ import annotations

from uuid import uuid4

from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.services.social_event_adapter_service import SocialEventAdapterService


def _connection() -> OmnichannelChannelConnection:
    return OmnichannelChannelConnection(
        id=uuid4(),
        workspace_id=uuid4(),
        channel="messenger",
        provider="meta_messenger",
        connection_key="messenger:test",
        display_name="Messenger Teste",
        external_account_id=None,
        transport_mode="official_api",
        status="connected",
        capabilities={},
        routing_policy={},
        configuration={},
    )


def test_referral_ref_is_extracted_into_correlation() -> None:
    connection = _connection()
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "page-1",
                "time": 1_700_000_000,
                "messaging": [
                    {
                        "sender": {"id": "psid-1", "username": "cliente"},
                        "recipient": {"id": "page-1"},
                        "timestamp": 1_700_000_000,
                        "message": {"mid": "mid-1", "text": "Oi, vim pelo link"},
                        "referral": {
                            "ref": "campanha-instagram-bio",
                            "source": "SHORTLINK",
                        },
                    }
                ],
            }
        ],
    }

    batch = SocialEventAdapterService.normalize(connection, payload)

    assert len(batch.events) == 1
    correlation = batch.events[0].payload.correlation
    assert correlation["ref"] == "campanha-instagram-bio"


def test_missing_referral_ref_is_simply_absent() -> None:
    connection = _connection()
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "page-1",
                "time": 1_700_000_000,
                "messaging": [
                    {
                        "sender": {"id": "psid-2", "username": "cliente2"},
                        "recipient": {"id": "page-1"},
                        "timestamp": 1_700_000_000,
                        "message": {"mid": "mid-2", "text": "Mensagem espontânea"},
                    }
                ],
            }
        ],
    }

    batch = SocialEventAdapterService.normalize(connection, payload)

    assert len(batch.events) == 1
    assert "ref" not in batch.events[0].payload.correlation
