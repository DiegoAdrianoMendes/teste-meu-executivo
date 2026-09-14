from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import MetaCloudClient


def test_group_message_builder_uses_the_official_recipient_contract() -> None:
    payload = MetaCloudClient.build_group_message_payload(
        group_id="GROUP_ID",
        message={"type": "text", "text": {"body": "Olá, grupo."}},
    )

    assert payload == {
        "messaging_product": "whatsapp",
        "recipient_type": "group",
        "to": "GROUP_ID",
        "type": "text",
        "text": {"preview_url": False, "body": "Olá, grupo."},
    }


@pytest.mark.asyncio
async def test_send_group_message_contract_uses_messages_edge_without_live_network(
    monkeypatch,
) -> None:
    client = MetaCloudClient()
    request = AsyncMock(return_value={"messages": [{"id": "wamid.group.fixture"}]})
    monkeypatch.setattr(client, "_request", request)

    result = await client.send_group_message_payload(
        phone_number_id="PHONE_NUMBER_ID",
        access_token="fixture-token",
        group_id="GROUP_ID",
        message={"type": "text", "text": {"body": "Contrato oficial"}},
    )

    assert result == {"messages": [{"id": "wamid.group.fixture"}]}
    request.assert_awaited_once_with(
        "POST",
        "/PHONE_NUMBER_ID/messages",
        access_token="fixture-token",
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "group",
            "to": "GROUP_ID",
            "type": "text",
            "text": {"preview_url": False, "body": "Contrato oficial"},
        },
    )


@pytest.mark.asyncio
async def test_official_group_creation_contract_is_pure_and_reused_by_client(
    monkeypatch,
) -> None:
    expected = {
        "messaging_product": "whatsapp",
        "subject": "Plantão jurídico",
        "description": "Convites somente pelo link oficial.",
        "join_approval_mode": "approval_required",
    }
    assert (
        MetaCloudClient.build_group_creation_payload(
            subject=" Plantão jurídico ",
            description=" Convites somente pelo link oficial. ",
            join_approval_mode="approval_required",
        )
        == expected
    )

    client = MetaCloudClient()
    request = AsyncMock(return_value={"request_id": "fixture-request"})
    monkeypatch.setattr(client, "_request", request)
    await client.create_group(
        phone_number_id="PHONE_NUMBER_ID",
        access_token="fixture-token",
        subject="Plantão jurídico",
        description="Convites somente pelo link oficial.",
        join_approval_mode="approval_required",
    )
    request.assert_awaited_once_with(
        "POST",
        "/PHONE_NUMBER_ID/groups",
        access_token="fixture-token",
        json=expected,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {"type": "interactive", "interactive": {"type": "button"}},
        {"type": "product", "product": {"retailer_id": "sku"}},
        {"type": "order", "order": {"catalog_id": "catalog"}},
        {
            "type": "template",
            "template_category": "authentication",
            "template_format": "text",
            "template": {
                "name": "auth_code",
                "language": {"code": "pt_BR"},
            },
        },
        {
            "type": "text",
            "text": {"body": "temporária", "disappearing": True},
        },
        {
            "type": "image",
            "image": {"link": "https://example.test/image.jpg", "view_once": True},
        },
        {
            "type": "text",
            "text": {"body": "resposta contextual"},
            "context": {"message_id": "wamid.parent"},
        },
    ],
)
async def test_unsupported_group_message_features_fail_before_request(
    monkeypatch,
    message,
) -> None:
    client = MetaCloudClient()
    request = AsyncMock()
    monkeypatch.setattr(client, "_request", request)

    with pytest.raises(WhatsAppProviderError) as captured:
        await client.send_group_message_payload(
            phone_number_id="PHONE_NUMBER_ID",
            access_token="fixture-token",
            group_id="GROUP_ID",
            message=message,
        )

    assert captured.value.code == "WA-GROUP-MESSAGE-UNSUPPORTED"
    request.assert_not_awaited()
