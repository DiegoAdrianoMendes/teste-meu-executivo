"""Validation for provider-issued WhatsApp media download URLs."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_META_OBJECT_ID = re.compile(r"^[0-9]{1,64}$")


_EXACT_META_MEDIA_HOSTS = frozenset(
    {
        "lookaside.facebook.com",
        "lookaside.fbsbx.com",
    }
)
_META_MEDIA_HOST_SUFFIXES = (
    ".facebook.com",
    ".fbcdn.net",
    ".fbsbx.com",
)


def trusted_meta_media_url(value: object) -> str:
    """Return a provider URL only when sending a Meta bearer token is safe."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("URL de mídia da Meta ausente ou inválida")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("URL de mídia da Meta contém caracteres inválidos")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL de mídia da Meta inválida") from error

    hostname = (parsed.hostname or "").casefold().rstrip(".")
    trusted_host = hostname in _EXACT_META_MEDIA_HOSTS or any(
        hostname.endswith(suffix) for suffix in _META_MEDIA_HOST_SUFFIXES
    )
    if (
        parsed.scheme != "https"
        or not trusted_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("URL de mídia fora dos hosts HTTPS autorizados da Meta")
    return value


def trusted_meta_object_id(value: object, *, kind: str = "mídia") -> str:
    """S25: ids de mídia/lead da Meta são numéricos. Um id vindo do webhook que
    não seja só dígitos (`../me?fields=...`, `123?access_token=`) viraria uma
    chamada arbitrária à Graph com o token da plataforma. Nunca interpolar sem
    passar por aqui."""
    candidate = str(value or "").strip()
    if not _META_OBJECT_ID.fullmatch(candidate):
        raise ValueError(f"Identificador de {kind} da Meta fora do formato numérico")
    return candidate
