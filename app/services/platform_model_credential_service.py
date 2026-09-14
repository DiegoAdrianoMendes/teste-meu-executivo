"""Serviço das chaves de provedores de IA da plataforma, geridas pelo Blackbox.

Segunda camada da precedência de chave por provider no gateway:
BYOK do workspace -> credencial da plataforma (esta tabela) -> variável de
ambiente. Reutiliza o molde criptográfico do BYOK (``ModelCredentialCipher``,
Fernet/MultiFernet derivado de INTEGRATION_SECRET_ENCRYPTION_KEY). A chave é
write-only: respostas, auditoria e logs carregam apenas os últimos 4 caracteres.

A auditoria fica na trilha canônica da plataforma (``platform_audit_events``),
gravada pelo endpoint dentro da mesma transação; este serviço não grava
``KlmAuditEvent`` porque esse evento exige workspace.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.catalog import parse_json_string_set
from app.ai.errors import AIRuntimeError
from app.ai.security import require_allowed_base_url
from app.core.config import is_strong_integration_secret_encryption_key, settings
from app.models.platform_model_credential import PlatformModelCredential
from app.models.workspace_model_credential import MODEL_CREDENTIAL_PROVIDERS
from app.services.workspace_model_credential_service import (
    InvalidModelCredentialError,
    ModelCredentialCipher,
    ModelCredentialError,
    ModelCredentialNotFoundError,
    UnknownModelCredentialProviderError,
    redact_api_key,
)

_HINT_LENGTH = 4

# Providers cuja credencial é uma chave de API com adapter próprio no gateway.
# "vertex" usa service account (VERTEX_PROJECT_ID/VERTEX_LOCATION) e fica fora.
KEY_MANAGED_PROVIDERS = ("anthropic", "openai", "gemini", "maritaca")

_ENV_KEY_ATTRIBUTES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "maritaca": "MARITACA_API_KEY",
}

_VERTEX_REASON = (
    "O Vertex AI usa service account (VERTEX_PROJECT_ID e VERTEX_LOCATION), "
    "não chave de API."
)
_MARITACA_UNREGISTERED_REASON = (
    "A base URL da Maritaca não está em KLM_AI_ALLOWED_BASE_URLS_JSON; o gateway "
    "não registra o adapter e nenhuma chave deste provider é usada."
)


class UnmanagedModelCredentialProviderError(ModelCredentialError):
    """Provider sem adapter por chave de API (vertex)."""


class ModelCredentialAlreadyInactiveError(ModelCredentialError):
    """Desativar uma chave que já está desativada não muda nada: não se audita."""


def cipher_configured() -> bool:
    """INTEGRATION_SECRET_ENCRYPTION_KEY presente e forte, sem instanciar a cifra."""
    raw = settings.INTEGRATION_SECRET_ENCRYPTION_KEY or ""
    materials = [item.strip() for item in raw.split(",") if item.strip()]
    if not materials:
        return False
    return all(
        is_strong_integration_secret_encryption_key(material, jwt_secret=settings.SECRET_KEY)
        for material in materials
    )


def env_key_configured(provider: str) -> bool:
    """A variável de ambiente do provider está preenchida neste processo (sem expor)."""
    attribute = _ENV_KEY_ATTRIBUTES.get(provider)
    if attribute is None:
        return False
    return bool(str(getattr(settings, attribute, "") or "").strip())


def adapter_registered(provider: str) -> tuple[bool, str | None]:
    """Se o gateway registra adapter por chave para o provider, e o motivo quando não.

    Espelha ``app/ai/factory.py``: openai, anthropic e gemini sempre entram;
    maritaca só quando a base URL passa pela allowlist. Não constrói o gateway.
    """
    if provider == "vertex":
        return False, _VERTEX_REASON
    if provider != "maritaca":
        return True, None
    try:
        allowed = parse_json_string_set(
            settings.KLM_AI_ALLOWED_BASE_URLS_JSON,
            "KLM_AI_ALLOWED_BASE_URLS_JSON",
        )
        require_allowed_base_url(settings.KLM_AI_MARITACA_BASE_URL, allowed)
    except AIRuntimeError:
        return False, _MARITACA_UNREGISTERED_REASON
    return True, None


def _normalize_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if normalized not in MODEL_CREDENTIAL_PROVIDERS:
        raise UnknownModelCredentialProviderError(
            "Provider de modelo desconhecido: "
            f"{provider!r}. Aceitos: {', '.join(MODEL_CREDENTIAL_PROVIDERS)}."
        )
    return normalized


def credential_status_dict(
    provider: str, credential: PlatformModelCredential | None
) -> dict:
    """Representação segura para respostas de API. Nunca contém a chave."""
    key_managed = provider in KEY_MANAGED_PROVIDERS
    registered, reason = adapter_registered(provider)
    env_configured = env_key_configured(provider)
    active = bool(credential.is_active) if credential is not None else False

    if not key_managed or not registered:
        source = "none"
    elif active:
        source = "blackbox"
        reason = "Chave gravada pelo Blackbox; vale para todo workspace sem chave própria."
        if not cipher_configured():
            reason = (
                "Chave gravada, mas a cifra de segredos não está configurada neste processo: "
                "a rota falha fechada até corrigir INTEGRATION_SECRET_ENCRYPTION_KEY."
            )
    elif env_configured:
        source = "environment"
        reason = (
            "Sem chave ativa no Blackbox; o gateway usa a variável de ambiente do processo."
        )
    else:
        source = "none"
        reason = "Nenhuma chave gravada no Blackbox nem na variável de ambiente."

    return {
        "provider": provider,
        "configured": credential is not None,
        "isActive": active,
        "apiKeyMasked": redact_api_key(credential.api_key_hint) if credential else None,
        "keyLast4": credential.api_key_hint if credential else None,
        "updatedAt": (
            credential.updated_at.isoformat()
            if credential is not None and credential.updated_at
            else None
        ),
        "updatedByUserId": (
            str(credential.updated_by_user_id)
            if credential is not None and credential.updated_by_user_id
            else None
        ),
        "keyManaged": key_managed,
        "envConfigured": env_configured,
        "adapterRegistered": registered,
        "effectiveSource": source,
        "reason": reason,
    }


def _invalidate_gateway_cache(provider: str) -> None:
    """Invalida o cache do provider nos resolvers deste processo."""
    from app.ai.workspace_credentials import invalidate_platform_credential_cache

    invalidate_platform_credential_cache(provider)


class PlatformModelCredentialService:
    def __init__(self, db: AsyncSession, *, cipher: ModelCredentialCipher | None = None):
        self.db = db
        self._cipher = cipher

    @property
    def cipher(self) -> ModelCredentialCipher:
        if self._cipher is None:
            self._cipher = ModelCredentialCipher()
        return self._cipher

    def gate_status(self) -> dict:
        return {
            "modelApisEnabled": bool(settings.KLM_MODEL_APIS_ENABLED),
            "onlineApproved": bool(settings.KLM_ONLINE_APPROVED),
            "cipherConfigured": cipher_configured(),
        }

    async def get_credential(self, provider: str) -> PlatformModelCredential | None:
        normalized = _normalize_provider(provider)
        return await self.db.scalar(
            select(PlatformModelCredential).where(
                PlatformModelCredential.provider == normalized
            )
        )

    async def list_credentials(self) -> list[PlatformModelCredential]:
        result = await self.db.execute(
            select(PlatformModelCredential).order_by(PlatformModelCredential.provider)
        )
        return list(result.scalars().all())

    async def status_entries(self) -> list[dict]:
        """Uma entrada por provider do catálogo, na ordem do catálogo."""
        by_provider = {item.provider: item for item in await self.list_credentials()}
        return [
            credential_status_dict(provider, by_provider.get(provider))
            for provider in MODEL_CREDENTIAL_PROVIDERS
        ]

    async def set_credential(
        self,
        *,
        provider: str,
        api_key: str,
        actor_user_id: UUID | None,
    ) -> PlatformModelCredential:
        """Cria ou substitui (rotaciona) a chave do provider na plataforma."""
        normalized = _normalize_provider(provider)
        if normalized not in KEY_MANAGED_PROVIDERS:
            raise UnmanagedModelCredentialProviderError(_VERTEX_REASON)
        cleaned = (api_key or "").strip()
        if len(cleaned) < 8:
            raise InvalidModelCredentialError(
                "Chave de API muito curta para ser armazenada (mínimo 8 caracteres)."
            )

        credential = await self.get_credential(normalized)
        if credential is None:
            credential = PlatformModelCredential(provider=normalized)
            self.db.add(credential)

        credential.api_key_encrypted = self.cipher.encrypt(cleaned)
        credential.api_key_hint = cleaned[-_HINT_LENGTH:]
        credential.is_active = True
        credential.updated_by_user_id = actor_user_id
        await self.db.flush()
        # Recarrega colunas server-side (updated_at) sem lazy refresh após o commit.
        await self.db.refresh(credential)
        _invalidate_gateway_cache(normalized)
        return credential

    async def deactivate_credential(
        self,
        *,
        provider: str,
        actor_user_id: UUID | None,
    ) -> PlatformModelCredential:
        normalized = _normalize_provider(provider)
        credential = await self.get_credential(normalized)
        if credential is None:
            raise ModelCredentialNotFoundError(
                f"Nenhuma chave da plataforma armazenada para o provider {normalized}."
            )
        if not credential.is_active:
            raise ModelCredentialAlreadyInactiveError(
                f"A chave do provider {normalized} já está desativada."
            )
        credential.is_active = False
        credential.updated_by_user_id = actor_user_id
        await self.db.flush()
        await self.db.refresh(credential)
        _invalidate_gateway_cache(normalized)
        return credential

    async def get_decrypted(self, provider: str) -> str | None:
        """Uso interno (gateway): chave em claro da credencial ATIVA, ou None.

        Nunca expor o retorno em resposta de API nem em log. Falha de
        decifragem levanta ``ModelCredentialDecryptionError`` (fail-closed).
        """
        credential = await self.get_credential(provider)
        if credential is None or not credential.is_active:
            return None
        return self.cipher.decrypt_strict(credential.api_key_encrypted)
