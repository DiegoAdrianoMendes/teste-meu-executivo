from __future__ import annotations

import base64
import hashlib
import logging
from typing import List, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings

logger = logging.getLogger(__name__)


def _derive_fernet(material: str) -> Fernet:
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _load_key_materials() -> List[str]:
    """Return the configured key materials, first entry is the active (encrypt) key.

    Supports the new `WHATSAPP_SECRET_ENCRYPTION_KEYS` (comma-separated, versioned
    list) with the legacy single-key `WHATSAPP_SECRET_ENCRYPTION_KEY` (or SECRET_KEY)
    as a fallback so existing deployments keep working unchanged.
    """
    raw = getattr(settings, "WHATSAPP_SECRET_ENCRYPTION_KEYS", "") or ""
    materials = [item.strip() for item in raw.split(",") if item.strip()]
    if materials:
        return materials
    return [settings.WHATSAPP_SECRET_ENCRYPTION_KEY or settings.SECRET_KEY]


class WhatsAppSecretDecryptionError(Exception):
    """Raised when a stored secret cannot be decrypted with any configured key."""


class WhatsAppSecretCipher:
    def __init__(self) -> None:
        materials = _load_key_materials()
        self._fernets = [_derive_fernet(material) for material in materials]
        self._multi_fernet = MultiFernet(self._fernets)
        self._active_fernet = self._fernets[0]

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return self._active_fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        """Best-effort decrypt: returns None (and logs) if every configured key fails.

        Prefer `decrypt_strict` at call sites that must react to total decrypt
        failure (e.g. marking a connection as errored) instead of silently
        swallowing the failure.
        """
        if not value:
            return None
        try:
            return self._multi_fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            logger.error(
                "whatsapp_secret_decrypt_failed",
                extra={"key_count": len(self._fernets)},
            )
            return None

    def decrypt_strict(self, value: Optional[str]) -> Optional[str]:
        """Like `decrypt`, but raises `WhatsAppSecretDecryptionError` instead of
        returning None when a non-empty value fails to decrypt with every key.
        """
        if not value:
            return None
        try:
            return self._multi_fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            logger.error(
                "whatsapp_secret_decrypt_failed",
                extra={"key_count": len(self._fernets)},
            )
            raise WhatsAppSecretDecryptionError(
                "Não foi possível decifrar o segredo com nenhuma chave configurada."
            ) from exc

    def is_encrypted_with_active_key(self, value: Optional[str]) -> bool:
        if not value:
            return True
        try:
            self._active_fernet.decrypt(value.encode("utf-8"))
            return True
        except InvalidToken:
            return False

    def re_encrypt(self, value: Optional[str]) -> Optional[str]:
        """Re-cipher a stored value with the active key, tolerating values already
        encrypted with it. Raises `WhatsAppSecretDecryptionError` if the value
        cannot be decrypted with any configured key.
        """
        if not value:
            return value
        plain = self.decrypt_strict(value)
        if plain is None:
            return value
        return self.encrypt(plain)


SECRET_FIELDS = (
    "access_token_encrypted",
    "business_token_encrypted",
    "instance_token_encrypted",
    "registration_pin_encrypted",
    "app_secret_encrypted",
    "webhook_verify_token_encrypted",
)


async def re_encrypt_all(db, *, dry_run: bool = True, cipher: Optional[WhatsAppSecretCipher] = None):
    """Re-cipher all WhatsApp connection secrets with the currently active key.

    Returns a report dict: {"total": int, "rotated": int, "failed": list[str], "dry_run": bool}.
    With dry_run=True (the default), no changes are persisted — only the report is computed.
    """
    from sqlalchemy import select

    from app.models.whatsapp import WhatsAppConnection

    cipher = cipher or WhatsAppSecretCipher()
    result = await db.execute(select(WhatsAppConnection))
    connections = result.scalars().all()

    report = {"total": len(connections), "rotated": 0, "failed": [], "dry_run": dry_run}

    for connection in connections:
        needs_rotation = False
        new_values = {}
        for field in SECRET_FIELDS:
            current = getattr(connection, field, None)
            if not current:
                continue
            if cipher.is_encrypted_with_active_key(current):
                continue
            try:
                new_values[field] = cipher.re_encrypt(current)
                needs_rotation = True
            except WhatsAppSecretDecryptionError:
                report["failed"].append(f"{connection.id}:{field}")
        if needs_rotation:
            report["rotated"] += 1
            if not dry_run:
                for field, value in new_values.items():
                    setattr(connection, field, value)

    if not dry_run and report["rotated"]:
        await db.commit()

    return report
