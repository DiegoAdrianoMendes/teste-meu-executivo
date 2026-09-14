"""Ponto de resolução de credencial por chamada para o gateway.

Precedência de chave por provider, avaliada a cada chamada:

1. credencial BYOK ativa do workspace (``workspace_model_credentials``);
2. credencial da plataforma gravada pelo Blackbox (``platform_model_credentials``);
3. variável de ambiente (singleton do gateway), quando este resolver devolve None.

Um adapter equivalente ao da plataforma é construído com a chave encontrada e
cacheado por (workspace, provider) ou por provider. Cada entrada guarda um
``stamp`` (sha256 do ciphertext + is_active) e o instante da última conferência:
passado ``CREDENTIAL_RECHECK_SECONDS`` a linha é relida com um SELECT barato e
o adapter só é reconstruído (decifrando de novo) quando o stamp muda. Assim a
troca de chave feita em outro processo (API com vários workers, worker Celery)
vale em até 15 s, e a invalidação em processo continua imediata.

Fail-closed: falha de leitura ou de decifragem NUNCA cai silenciosamente para
a camada seguinte. A rota daquele provider falha com ``AIConfigurationError``
(o gateway registra ``preflight_blocked`` e segue para o próximo candidato).

O lock é por event loop: o gateway é singleton por processo e cada task Celery
cria e fecha o próprio loop; um ``asyncio.Lock`` preso a outro loop levantaria
``RuntimeError`` fora da hierarquia ``AIRuntimeError``.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.ai.errors import AIConfigurationError
from app.ai.models import ProviderName
from app.ai.providers.base import ModelProviderAdapter

# ProviderName do gateway → provider das tabelas de credencial.
# Somente adapters baseados em API key participam da resolução;
# "vertex" (service account) fica fora até existir client compatível.
PROVIDER_CREDENTIAL_KEYS: dict[ProviderName, str] = {
    ProviderName.OPENAI: "openai",
    ProviderName.ANTHROPIC: "anthropic",
    ProviderName.GEMINI_API: "gemini",
    ProviderName.MARITACA: "maritaca",
}

AdapterFactory = Callable[[str], ModelProviderAdapter]

# Janela máxima para uma troca de chave feita em outro processo valer aqui.
CREDENTIAL_RECHECK_SECONDS = 15.0

# Registro fraco dos resolvers vivos para invalidação vinda dos serviços,
# sem acoplar os serviços à fábrica do gateway.
_ACTIVE_RESOLVERS: weakref.WeakSet[WorkspaceCredentialResolver] = weakref.WeakSet()


def invalidate_workspace_credential_cache(
    workspace_id: str, provider_key: str | None = None
) -> None:
    """Invalida o cache de todos os resolvers ativos para o workspace.

    ``provider_key`` usa o vocabulário da tabela ("openai", "anthropic", ...);
    ``None`` limpa todos os providers do workspace.
    """
    for resolver in list(_ACTIVE_RESOLVERS):
        resolver.invalidate(workspace_id, provider_key)


def invalidate_platform_credential_cache(provider_key: str | None = None) -> None:
    """Invalida, nos resolvers ativos deste processo, a credencial da plataforma.

    ``None`` limpa todos os providers. Outros processos convergem em até
    ``CREDENTIAL_RECHECK_SECONDS`` pelo recheck do stamp.
    """
    for resolver in list(_ACTIVE_RESOLVERS):
        resolver.invalidate_platform(provider_key)


def credential_stamp(api_key_encrypted: str, is_active: bool) -> tuple[str, bool]:
    """Identidade da credencial armazenada, sem expor o ciphertext.

    O Fernet gera IV aleatório por cifragem, então o ciphertext muda em toda
    gravação (inclusive ao regravar a mesma chave). ``updated_at`` não serve:
    tem resolução de segundo no SQLite e é o início da transação no PostgreSQL.
    """
    digest = hashlib.sha256((api_key_encrypted or "").encode("utf-8")).hexdigest()
    return (digest, bool(is_active))


def _normalized_workspace_id(workspace_id: str) -> str | None:
    try:
        return str(UUID(str(workspace_id)))
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass(frozen=True, slots=True)
class _CredentialRow:
    stamp: tuple[str, bool]
    api_key_encrypted: str


@dataclass(slots=True)
class _CacheEntry:
    # None = sem credencial ativa nesta camada (seguir para a próxima).
    stamp: tuple[str, bool] | None
    adapter: ModelProviderAdapter | None
    checked_at: float


class WorkspaceCredentialResolver:
    """Resolve adapter por chamada: workspace → plataforma (Blackbox) → env."""

    def __init__(
        self,
        *,
        adapter_factories: dict[ProviderName, AdapterFactory],
        session_factory: Callable | None = None,
        recheck_seconds: float = CREDENTIAL_RECHECK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter_factories = adapter_factories
        self._session_factory = session_factory
        self._recheck_seconds = float(recheck_seconds)
        self._clock = clock
        # (workspace_id, provider_key) -> entrada do workspace.
        self._workspace_cache: dict[tuple[str, str], _CacheEntry] = {}
        # provider_key -> entrada da plataforma.
        self._platform_cache: dict[str, _CacheEntry] = {}
        self._locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )
        self._cipher = None
        _ACTIVE_RESOLVERS.add(self)

    # ── invalidação ─────────────────────────────────────────────────────

    def invalidate(self, workspace_id: str, provider_key: str | None = None) -> None:
        normalized = _normalized_workspace_id(workspace_id)
        if normalized is None:
            return
        if provider_key is None:
            stale = [key for key in self._workspace_cache if key[0] == normalized]
        else:
            stale = [(normalized, provider_key)]
        for key in stale:
            self._workspace_cache.pop(key, None)

    def invalidate_platform(self, provider_key: str | None = None) -> None:
        if provider_key is None:
            self._platform_cache.clear()
            return
        self._platform_cache.pop(provider_key, None)

    def clear(self) -> None:
        self._workspace_cache.clear()
        self._platform_cache.clear()

    # ── infraestrutura ──────────────────────────────────────────────────

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    def _session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import model_usage_session_factory

        return model_usage_session_factory()()

    def _get_cipher(self):
        if self._cipher is None:
            from app.services.workspace_model_credential_service import (
                ModelCredentialCipher,
            )

            self._cipher = ModelCredentialCipher()
        return self._cipher

    def _fresh(self, entry: _CacheEntry | None, now: float) -> bool:
        return entry is not None and (now - entry.checked_at) < self._recheck_seconds

    # ── resolução ───────────────────────────────────────────────────────

    async def resolve_adapter(
        self, workspace_id: str, provider: ProviderName
    ) -> ModelProviderAdapter | None:
        """Devolve o adapter do workspace ou da plataforma, ou None (usar a env)."""
        provider_key = PROVIDER_CREDENTIAL_KEYS.get(provider)
        factory = self._adapter_factories.get(provider)
        if provider_key is None or factory is None:
            return None

        normalized = _normalized_workspace_id(workspace_id)
        workspace_key = (normalized, provider_key) if normalized is not None else None

        cached = self._cached_adapter(workspace_key, provider_key, self._clock())
        if cached is not _MISS:
            return cached

        async with self._lock():
            now = self._clock()
            cached = self._cached_adapter(workspace_key, provider_key, now)
            if cached is not _MISS:
                return cached
            return await self._refresh(workspace_key, provider_key, factory, now)

    def _cached_adapter(
        self,
        workspace_key: tuple[str, str] | None,
        provider_key: str,
        now: float,
    ):
        """Resposta sem tocar o banco quando as camadas necessárias estão frescas."""
        workspace_entry = (
            self._workspace_cache.get(workspace_key) if workspace_key is not None else None
        )
        workspace_fresh = self._fresh(workspace_entry, now)
        if workspace_fresh and workspace_entry.adapter is not None:
            return workspace_entry.adapter
        if workspace_key is not None and not workspace_fresh:
            return _MISS
        platform_entry = self._platform_cache.get(provider_key)
        if self._fresh(platform_entry, now):
            return platform_entry.adapter
        return _MISS

    async def _refresh(
        self,
        workspace_key: tuple[str, str] | None,
        provider_key: str,
        factory: AdapterFactory,
        now: float,
    ) -> ModelProviderAdapter | None:
        """Relê o que estiver vencido em UMA sessão; reconstrói só quando o stamp muda."""
        from app.services.workspace_model_credential_service import (
            ModelCredentialDecryptionError,
        )

        try:
            async with self._session() as db:
                if workspace_key is not None:
                    workspace_entry = self._workspace_cache.get(workspace_key)
                    if not self._fresh(workspace_entry, now):
                        row = await self._load_workspace_row(db, workspace_key[0], provider_key)
                        workspace_entry = self._rebuild(workspace_entry, row, factory, now)
                        self._workspace_cache[workspace_key] = workspace_entry
                    if workspace_entry.adapter is not None:
                        return workspace_entry.adapter

                platform_entry = self._platform_cache.get(provider_key)
                if not self._fresh(platform_entry, now):
                    row = await self._load_platform_row(db, provider_key)
                    platform_entry = self._rebuild(platform_entry, row, factory, now)
                    self._platform_cache[provider_key] = platform_entry
                return platform_entry.adapter
        except ModelCredentialDecryptionError as exc:
            raise AIConfigurationError(
                f"Credencial do provider {provider_key} não pôde ser decifrada; "
                "rota bloqueada (fail-closed, sem fallback silencioso)."
            ) from exc
        except AIConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001 — fail-closed com erro visível
            raise AIConfigurationError(
                f"Falha ao resolver credencial do provider {provider_key}; "
                "rota bloqueada (fail-closed, sem fallback silencioso)."
            ) from exc

    def _rebuild(
        self,
        entry: _CacheEntry | None,
        row: _CredentialRow | None,
        factory: AdapterFactory,
        now: float,
    ) -> _CacheEntry:
        if row is None:
            return _CacheEntry(stamp=None, adapter=None, checked_at=now)
        if entry is not None and entry.adapter is not None and entry.stamp == row.stamp:
            # Mesma credencial: mantém o adapter (sem decifrar de novo).
            return _CacheEntry(stamp=row.stamp, adapter=entry.adapter, checked_at=now)
        api_key = self._get_cipher().decrypt_strict(row.api_key_encrypted)
        if not api_key:
            return _CacheEntry(stamp=None, adapter=None, checked_at=now)
        return _CacheEntry(stamp=row.stamp, adapter=factory(api_key), checked_at=now)

    async def _load_workspace_row(
        self, db, workspace_id: str, provider_key: str
    ) -> _CredentialRow | None:
        from app.db.tenant import set_workspace_context
        from app.services.workspace_model_credential_service import (
            WorkspaceModelCredentialService,
        )

        await set_workspace_context(db, workspace_id)
        credential = await WorkspaceModelCredentialService(db).get_credential(
            UUID(workspace_id), provider_key
        )
        if credential is None or not credential.is_active:
            return None
        return _CredentialRow(
            stamp=credential_stamp(credential.api_key_encrypted, True),
            api_key_encrypted=credential.api_key_encrypted,
        )

    async def _load_platform_row(self, db, provider_key: str) -> _CredentialRow | None:
        from app.services.platform_model_credential_service import (
            PlatformModelCredentialService,
        )

        credential = await PlatformModelCredentialService(db).get_credential(provider_key)
        if credential is None or not credential.is_active:
            return None
        return _CredentialRow(
            stamp=credential_stamp(credential.api_key_encrypted, True),
            api_key_encrypted=credential.api_key_encrypted,
        )


# Sentinela interna: "nada fresco em cache, é preciso reler".
_MISS = object()
