"""Achado C3 (cross-tenant): `entry.id` do payload Meta nunca era validado contra
`external_account_id` da conexão local. Ver docs/AUDITORIA_META_CANAIS_2026-07-23.md
linha 24-25 e docs/PLANO_META_MAXIMO_2026-07-23.md seção 4-I1 item 1.

Estes testes exercitam `SocialEventAdapterService` isoladamente (sem sessão de
banco): a conexão é apenas um objeto ORM em memória, nunca persistido.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.services.social_event_adapter_service import SocialEventAdapterService


def _connection(*, external_account_id: str | None) -> OmnichannelChannelConnection:
    return OmnichannelChannelConnection(
        id=uuid4(),
        workspace_id=uuid4(),
        channel="instagram",
        provider="meta_instagram",
        connection_key="instagram:test",
        display_name="Instagram Teste",
        external_account_id=external_account_id,
        transport_mode="official_api",
        status="connected",
        capabilities={},
        routing_policy={},
        configuration={},
    )


def _payload(entry_id: str) -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": entry_id,
                "time": 1_700_000_000,
                "messaging": [
                    {
                        "sender": {"id": "ig-user-1", "username": "cliente"},
                        "recipient": {"id": entry_id},
                        "timestamp": 1_700_000_000,
                        "message": {"mid": "mid-1", "text": "Olá"},
                    }
                ],
            }
        ],
    }


def test_matching_entry_id_is_accepted() -> None:
    connection = _connection(external_account_id="ig-business-001")
    batch = SocialEventAdapterService.normalize(connection, _payload("ig-business-001"))
    assert batch.inspected_count == 1
    assert len(batch.events) == 1
    assert batch.ignored_count == 0


def test_diverging_entry_id_is_discarded_and_counted(caplog: pytest.LogCaptureFixture) -> None:
    connection = _connection(external_account_id="ig-business-001")
    with caplog.at_level("WARNING"):
        batch = SocialEventAdapterService.normalize(connection, _payload("ig-OTHER-TENANT"))
    assert len(batch.events) == 0
    assert batch.ignored_count == 1
    assert batch.inspected_count == 1
    assert any(
        "entry_id_mismatch_discarded" in record.message for record in caplog.records
    )
    # A entry divergente (potencial vazamento cross-tenant) não deve vazar o id
    # real em texto puro no log; só uma referência opaca (hash).
    assert not any("ig-OTHER-TENANT" in record.message for record in caplog.records)


def test_empty_external_account_id_accepts_with_unverified_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connection = _connection(external_account_id=None)
    with caplog.at_level("WARNING"):
        batch = SocialEventAdapterService.normalize(connection, _payload("ig-anything"))
    assert len(batch.events) == 1
    assert batch.ignored_count == 0
    assert any(
        "unverified_account_binding" in record.message for record in caplog.records
    )


def test_missing_entry_id_falls_back_to_bound_account_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connection = _connection(external_account_id="ig-business-001")
    payload = _payload("")
    payload["entry"][0]["id"] = None
    with caplog.at_level("WARNING"):
        batch = SocialEventAdapterService.normalize(connection, payload)
    assert len(batch.events) == 1
    assert not any(
        "entry_id_mismatch_discarded" in record.message
        or "unverified_account_binding" in record.message
        for record in caplog.records
    )
