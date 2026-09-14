"""Encryption and response redaction for integration credentials."""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import is_strong_integration_secret_encryption_key, settings

SENSITIVE_SETTING_KEYS = frozenset(
    {
        "apiToken",
        "apiKey",
        "accessToken",
        "refreshToken",
        "clientSecret",
        "webhookSecret",
        "password",
        "developerToken",
    }
)


class IntegrationSecretCipher:
    def __init__(self) -> None:
        material = settings.INTEGRATION_SECRET_ENCRYPTION_KEY.strip()
        if not is_strong_integration_secret_encryption_key(
            material,
            jwt_secret=settings.SECRET_KEY,
        ):
            raise RuntimeError(
                "INTEGRATION_SECRET_ENCRYPTION_KEY ausente, fraca ou não dedicada."
            )
        digest = hashlib.sha256(material.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return None


WEBHOOK_SECRET_DIGEST_KEY = "webhookSecretDigest"


def webhook_secret_digest(token: str) -> str:
    """Digest determinístico do segredo do webhook, gravado em claro ao lado do
    cifrado para o resolver SECURITY DEFINER localizar o workspace sem ler o
    segredo (S7). Chaveado pela chave dedicada de integrações, com derivação
    própria: rotacionar a chave invalida o cifrado e o digest juntos, um só
    runbook. Calculado por chamada para a chave nunca ficar em cache."""
    material = settings.INTEGRATION_SECRET_ENCRYPTION_KEY.strip().encode("utf-8")
    key = hashlib.sha256(b"klm-integration:webhook-secret-digest:" + material).digest()
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def protect_integration_settings(value: dict[str, Any] | None) -> dict[str, Any]:
    cipher = IntegrationSecretCipher()
    payload = dict(value or {})
    # O digest só é aceito de quem trouxe o segredo (recalculado) ou de quem já
    # trazia o cifrado (callers internos reescrevendo o dict). Digest avulso
    # vindo do cliente é descartado: sem ele, o resolver não casa nada.
    digest_recebido = payload.pop(WEBHOOK_SECRET_DIGEST_KEY, None)
    protected: dict[str, Any] = {}
    for key, item in payload.items():
        if key in SENSITIVE_SETTING_KEYS and item:
            protected[f"{key}Encrypted"] = cipher.encrypt(str(item))
        else:
            protected[key] = item
    webhook_secret = payload.get("webhookSecret")
    if webhook_secret:
        protected[WEBHOOK_SECRET_DIGEST_KEY] = webhook_secret_digest(str(webhook_secret))
    elif digest_recebido and payload.get("webhookSecretEncrypted"):
        protected[WEBHOOK_SECRET_DIGEST_KEY] = digest_recebido
    return protected


def reveal_integration_secret(settings_payload: dict[str, Any] | None, key: str) -> str:
    payload = settings_payload or {}
    encrypted = payload.get(f"{key}Encrypted")
    if encrypted:
        return IntegrationSecretCipher().decrypt(str(encrypted)) or ""
    # Read-only compatibility for records created before encryption rollout.
    return str(payload.get(key) or "")


def redact_integration_settings(value: dict[str, Any] | None) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, item in (value or {}).items():
        # O digest também sai dobrado: exposto, viraria um oráculo de HMAC.
        plain_key = key.removesuffix("Encrypted").removesuffix("Digest")
        if plain_key in SENSITIVE_SETTING_KEYS:
            redacted[f"{plain_key}Configured"] = bool(item) or redacted.get(
                f"{plain_key}Configured", False
            )
        else:
            redacted[key] = item
    return redacted
