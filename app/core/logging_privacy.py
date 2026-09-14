"""Privacy-safe primitives for structured logging."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from typing import Final

from app.core.config import settings

_DEFAULT_SECRETS: Final = frozenset({"", "change-me", "change-me-in-production"})
_SAFE_KIND: Final = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_SAFE_ERROR_TYPE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")
_PROCESS_FALLBACK_KEY: Final = secrets.token_bytes(32)
_SENSITIVE_URL_PARAMETER: Final = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|fb_exchange_token|client_secret|"
    r"appsecret_proof|code)=)[^&\s]+"
)


def _reference_key() -> bytes:
    secret = settings.SECRET_KEY.strip()
    if secret in _DEFAULT_SECRETS:
        return _PROCESS_FALLBACK_KEY
    return hmac.new(
        secret.encode("utf-8", errors="replace"),
        b"klm-logging-reference-key-v1",
        hashlib.sha256,
    ).digest()


def opaque_ref(value: object | None, *, kind: str) -> str | None:
    """Return a keyed, non-reversible reference without exposing the source value."""
    if value is None:
        return None

    safe_kind = kind if _SAFE_KIND.fullmatch(kind) else "value"
    if isinstance(value, bytes):
        raw_value = value
    else:
        try:
            raw_value = str(value).encode("utf-8", errors="replace")
        except Exception:
            raw_value = type(value).__name__.encode("ascii", errors="replace")

    digest = hmac.new(
        _reference_key(),
        safe_kind.encode("ascii") + b"\0" + raw_value,
        hashlib.sha256,
    ).hexdigest()[:20]
    return f"{safe_kind}:{digest}"


def safe_error_type(error: BaseException) -> str:
    """Return only a conservative exception class name, never its message or traceback."""
    name = type(error).__name__
    if not _SAFE_ERROR_TYPE.fullmatch(name):
        return "UnknownError"
    return name


class RedactSensitiveUrlParameters(logging.Filter):
    """Remove credenciais de URLs antes que clientes HTTP as escrevam no log."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SENSITIVE_URL_PARAMETER.sub(r"\1[redacted]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True
