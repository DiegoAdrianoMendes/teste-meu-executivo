from __future__ import annotations

import pytest

from app.services.whatsapp.media_security import trusted_meta_media_url


@pytest.mark.parametrize(
    "url",
    [
        "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=abc",
        "https://lookaside.facebook.com/whatsapp_business/attachments/?mid=abc",
        "https://scontent-gru2-1.xx.fbcdn.net/media/file.ogg?token=opaque",
    ],
)
def test_trusted_meta_media_url_accepts_https_meta_hosts(url: str) -> None:
    assert trusted_meta_media_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://lookaside.fbsbx.com/media",
        "https://lookaside.fbsbx.com.evil.example/media",
        "https://127.0.0.1/latest/meta-data",
        "https://user:password@lookaside.fbsbx.com/media",
        "https://lookaside.fbsbx.com:444/media",
        "https://lookaside.fbsbx.com/media#secret",
        " https://lookaside.fbsbx.com/media",
        "https://evil.example/media",
        "",
        None,
    ],
)
def test_trusted_meta_media_url_rejects_bearer_exfiltration_targets(url: object) -> None:
    with pytest.raises(ValueError):
        trusted_meta_media_url(url)
