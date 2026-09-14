from __future__ import annotations

import pytest

from app.schemas.whatsapp import StartWhatsAppConversationRequest
from app.schemas.workspace import WorkspaceOwnerChannelUpdate
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.helpers import (
    normalize_meta_phone_number,
    normalize_phone_number,
    phone_number_variants,
)
from app.services.whatsapp.meta_cloud_client import MetaCloudClient


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(61) 99999-9999", "5561999999999"),
        ("+55 61 99999-9999", "5561999999999"),
        ("0055 61 99999-9999", "5561999999999"),
        ("5561999999999", "5561999999999"),
        ("+1 202-555-0123", "12025550123"),
    ],
)
def test_phone_is_normalized_to_meta_recipient_format(raw: str, expected: str) -> None:
    assert normalize_phone_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "99999999", "telefone-invalido", "+55 61 99999-9999 ext. 2"])
def test_invalid_or_ambiguous_phone_is_rejected(raw: str) -> None:
    assert normalize_phone_number(raw) is None


def test_meta_phone_requires_international_format() -> None:
    assert normalize_meta_phone_number("5561999999999") == "5561999999999"
    assert normalize_meta_phone_number("61999999999") is None


def test_brazilian_mobile_aliases_resolve_to_ninth_digit_canonical_form() -> None:
    assert normalize_phone_number("556182502136") == "5561982502136"
    assert phone_number_variants("5561982502136") == {
        "5561982502136",
        "556182502136",
    }


def test_foreign_and_brazilian_fixed_numbers_do_not_gain_mobile_aliases() -> None:
    assert phone_number_variants("15556658126") == {"15556658126"}
    assert phone_number_variants("556133334444") == {"556133334444"}
    assert phone_number_variants("5561933334444") == {"5561933334444"}


def test_start_conversation_schema_persists_canonical_phone() -> None:
    payload = StartWhatsAppConversationRequest(
        connection_id="f88f8bf9-0e01-4f18-90a4-c6e07b8d935a",
        meta_template_id="b9891019-b4a0-4c6d-bb48-034daaf2c821",
        phone="(61) 99999-9999",
    )
    assert payload.phone == "5561999999999"


def test_owner_alert_phone_uses_the_same_meta_contract() -> None:
    payload = WorkspaceOwnerChannelUpdate(owner_whatsapp_phone="(61) 99999-9999")
    assert payload.owner_whatsapp_phone == "5561999999999"


@pytest.mark.asyncio
async def test_meta_client_is_final_phone_validation_choke_point(monkeypatch) -> None:
    captured: dict = {}

    async def fake_request(*args, **kwargs):
        captured.update(kwargs["json"])
        return {"messages": [{"id": "wamid.test"}]}

    client = MetaCloudClient()
    monkeypatch.setattr(client, "_request", fake_request)
    await client.send_text_message(
        phone_number_id="phone-id",
        access_token="token",
        to="(61) 99999-9999",
        body="teste sem envio real",
    )
    assert captured["to"] == "5561999999999"

    with pytest.raises(WhatsAppProviderError) as captured_error:
        await client.send_text_message(
            phone_number_id="phone-id",
            access_token="token",
            to="99999999",
            body="não deve chegar ao request",
        )
    assert captured_error.value.code == "WA-PHONE-001"
