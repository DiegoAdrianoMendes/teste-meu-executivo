"""S25: id de mídia/lead do webhook nunca entra na URL da Graph sem ser numérico."""

from unittest.mock import Mock

import pytest

from app.services.transcription_service import TranscriptionService
from app.services.whatsapp.media_security import trusted_meta_object_id


@pytest.mark.parametrize("ruim", ["123/../me", "123?access_token=x", "me", "", " 12 34"])
def test_non_numeric_ids_are_rejected(ruim):
    with pytest.raises(ValueError):
        trusted_meta_object_id(ruim)


def test_numeric_id_passes_trimmed():
    assert trusted_meta_object_id(" 1037543291543636 ") == "1037543291543636"


@pytest.mark.asyncio
async def test_media_download_rejects_forged_id_before_any_http(monkeypatch):
    client = Mock(side_effect=AssertionError("HTTP não deve ser inicializado"))
    monkeypatch.setattr("app.services.transcription_service.httpx.AsyncClient", client)
    monkeypatch.setattr(
        "app.services.transcription_service.external_delivery_allowed", lambda: True
    )
    with pytest.raises(ValueError, match="numérico"):
        await TranscriptionService().download_whatsapp_media("123/../me?fields=x", "token")
    client.assert_not_called()
