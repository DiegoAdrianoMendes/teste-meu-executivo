"""Serviço de credenciais BYOK de LLM por workspace.

Segue o molde BYOA (whatsapp_connections.app_secret_encrypted): a chave é
cifrada com Fernet/MultiFernet derivado de INTEGRATION_SECRET_ENCRYPTION_KEY
(lista separada por vírgula para rotação de chave de cifra; a primeira entrada
é a ativa). A chave de API é write-only: respostas e auditoria carregam apenas
o hint (últimos 4 caracteres), nunca o valor em claro.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import List, Optional
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import is_strong_integration_secret_encryption_key, settings
from app.models.klm_operations import KlmAuditEvent
from app.models.workspace_model_credential import (
    MODEL_CREDENTIAL_PROVIDERS,
    WorkspaceModelCredential,
)

logger = logging.getLogger(__name__)

_HINT_LENGTH = 4


class ModelCredentialError(Exception):
    """Base para falhas de credencial BYOK."""


class UnknownModelCredentialProviderError(ModelCredentialError):
    """Provider fora do catálogo aceito."""


class InvalidModelCredentialError(ModelCredentialError):
    """Chave de API ausente ou inválida na escrita."""


class ModelCredentialNotFoundError(ModelCredentialError):
    """Nenhuma credencial armazenada para (workspace, provider)."""


class ModelCredentialDecryptionError(ModelCredentialError):
    """A credencial armazenada não decifra com nenhuma chave configurada."""


def _derive_fernet(material: str) -> Fernet:
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _load_key_materials() -> List[str]:
    """Materiais de cifra: primeira entrada é a ativa (cifragem).

    ``INTEGRATION_SECRET_ENCRYPTION_KEY`` aceita lista separada por vírgula
    para rotação; toda entrada precisa ser forte e dedicada (nunca reutilizar
    o SECRET_KEY do JWT).
    """
    raw = settings.INTEGRATION_SECRET_ENCRYPTION_KEY or ""
    materials = [item.strip() for item in raw.split(",") if item.strip()]
    if not materials:
        raise RuntimeError(
            "INTEGRATION_SECRET_ENCRYPTION_KEY ausente para credenciais BYOK de LLM."
        )
    for material in materials:
        if not is_strong_integration_secret_encryption_key(
            material,
            jwt_secret=settings.SECRET_KEY,
        ):
            raise RuntimeError(
                "INTEGRATION_SECRET_ENCRYPTION_KEY fraca ou não dedicada para BYOK de LLM."
            )
    return materials


class ModelCredentialCipher:
    """Fernet/MultiFernet no molde BYOA, com rotação de chave de cifra."""

    def __init__(self) -> None:
        materials = _load_key_materials()
        fernets = [_derive_fernet(material) for material in materials]
        self._multi_fernet = MultiFernet(fernets)
        self._active_fernet = fernets[0]

    def encrypt(self, value: str) -> str:
        return self._active_fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt_strict(self, value: Optional[str]) -> Optional[str]:
        """Fail-closed: levanta em vez de devolver valor inventado/None."""
        if not value:
            return None
        try:
            return self._multi_fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            logger.error("workspace_model_credential_decrypt_failed")
            raise ModelCredentialDecryptionError(
                "Não foi possível decifrar a credencial com nenhuma chave configurada."
            ) from exc


def redact_api_key(api_key_hint: str) -> str:
    """Máscara canônica: só os últimos 4 caracteres aparecem."""
    return f"****{api_key_hint}" if api_key_hint else "****"


def credential_to_redacted_dict(credential: WorkspaceModelCredential) -> dict:
    """Representação segura para respostas de API — nunca contém a chave."""
    return {
        "provider": credential.provider,
        "configured": True,
        "isActive": bool(credential.is_active),
        "apiKeyMasked": redact_api_key(credential.api_key_hint),
        "updatedAt": (
            credential.updated_at.isoformat() if credential.updated_at else None
        ),
    }


def _require_known_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if normalized not in MODEL_CREDENTIAL_PROVIDERS:
        raise UnknownModelCredentialProviderError(
            "Provider de modelo desconhecido: "
            f"{provider!r}. Aceitos: {', '.join(MODEL_CREDENTIAL_PROVIDERS)}."
        )
    return normalized


def _invalidate_gateway_cache(workspace_id: UUID, provider: str) -> None:
    """Invalida o cache workspace+provider do ponto de resolução do gateway."""
    from app.ai.workspace_credentials import invalidate_workspace_credential_cache

    invalidate_workspace_credential_cache(str(workspace_id), provider)


class WorkspaceModelCredentialService:
    def __init__(self, db: AsyncSession, *, cipher: ModelCredentialCipher | None = None):
        self.db = db
        self._cipher = cipher

    @property
    def cipher(self) -> ModelCredentialCipher:
        if self._cipher is None:
            self._cipher = ModelCredentialCipher()
        return self._cipher

    async def get_credential(
        self, workspace_id: UUID, provider: str
    ) -> WorkspaceModelCredential | None:
        normalized = _require_known_provider(provider)
        return await self.db.scalar(
            select(WorkspaceModelCredential).where(
                WorkspaceModelCredential.workspace_id == workspace_id,
                WorkspaceModelCredential.provider == normalized,
            )
        )

    async def set_credential(
        self,
        *,
        workspace_id: UUID,
        provider: str,
        api_key: str,
        actor_user_id: UUID | None,
        actor_label: str = "workspace_admin",
    ) -> WorkspaceModelCredential:
        """Cria ou substitui (rotaciona) a credencial do provider no workspace."""
        normalized = _require_known_provider(provider)
        cleaned = (api_key or "").strip()
        if len(cleaned) < 8:
            raise InvalidModelCredentialError(
                "Chave de API muito curta para ser armazenada (mínimo 8 caracteres)."
            )

        credential = await self.get_credential(workspace_id, normalized)
        created = credential is None
        if credential is None:
            credential = WorkspaceModelCredential(
                workspace_id=workspace_id,
                provider=normalized,
            )
            self.db.add(credential)

        credential.api_key_encrypted = self.cipher.encrypt(cleaned)
        credential.api_key_hint = cleaned[-_HINT_LENGTH:]
        credential.is_active = True
        await self.db.flush()
        # Recarrega colunas server-side (updated_at) para leitura segura em
        # contexto async, sem lazy refresh implícito depois do commit.
        await self.db.refresh(credential)

        await self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            event_type=(
                "workspace_model_credential_created"
                if created
                else "workspace_model_credential_rotated"
            ),
            credential=credential,
        )
        _invalidate_gateway_cache(workspace_id, normalized)
        return credential

    async def rotate_credential(
        self,
        *,
        workspace_id: UUID,
        provider: str,
        api_key: str,
        actor_user_id: UUID | None,
        actor_label: str = "workspace_admin",
    ) -> WorkspaceModelCredential:
        """Rotação explícita: exige credencial já existente."""
        normalized = _require_known_provider(provider)
        existing = await self.get_credential(workspace_id, normalized)
        if existing is None:
            raise ModelCredentialNotFoundError(
                f"Nenhuma credencial armazenada para o provider {normalized}."
            )
        return await self.set_credential(
            workspace_id=workspace_id,
            provider=normalized,
            api_key=api_key,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
        )

    async def deactivate_credential(
        self,
        *,
        workspace_id: UUID,
        provider: str,
        actor_user_id: UUID | None,
        actor_label: str = "workspace_admin",
    ) -> WorkspaceModelCredential:
        normalized = _require_known_provider(provider)
        credential = await self.get_credential(workspace_id, normalized)
        if credential is None:
            raise ModelCredentialNotFoundError(
                f"Nenhuma credencial armazenada para o provider {normalized}."
            )
        credential.is_active = False
        await self.db.flush()
        await self.db.refresh(credential)

        await self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            event_type="workspace_model_credential_deactivated",
            credential=credential,
        )
        _invalidate_gateway_cache(workspace_id, normalized)
        return credential

    async def get_decrypted(
        self, workspace_id: UUID, provider: str
    ) -> str | None:
        """Uso interno (gateway): chave em claro da credencial ATIVA, ou None.

        Nunca expor o retorno em resposta de API nem em log. Falha de
        decifragem levanta ``ModelCredentialDecryptionError`` (fail-closed).
        """
        credential = await self.get_credential(workspace_id, provider)
        if credential is None or not credential.is_active:
            return None
        return self.cipher.decrypt_strict(credential.api_key_encrypted)

    async def _record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        actor_label: str,
        event_type: str,
        credential: WorkspaceModelCredential,
    ) -> None:
        """Auditoria KlmAuditEvent sem jamais registrar a chave em claro."""
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type=event_type,
                entity_type="workspace_model_credential",
                entity_id=credential.id,
                summary=(
                    f"Credencial de modelo ({credential.provider}) "
                    f"{redact_api_key(credential.api_key_hint)} — {event_type}."
                ),
                after_data={
                    "provider": credential.provider,
                    "apiKeyMasked": redact_api_key(credential.api_key_hint),
                    "isActive": bool(credential.is_active),
                },
            )
        )
        await self.db.flush()
