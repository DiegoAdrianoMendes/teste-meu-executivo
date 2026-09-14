"""Resolução BYOK no gateway: workspace → fallback plataforma, cache e invalidação."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401 — registra todos os modelos na Base
from app.ai.base import AIMessage
from app.ai.cache import NullResponseCache
from app.ai.catalog import PriceCatalog, RouteCatalog
from app.ai.circuit import CircuitBreaker
from app.ai.errors import AIConfigurationError, AIRuntimeError, ProviderNotConfiguredError
from app.ai.gateway import ModelGateway
from app.ai.limits import InMemoryDailyBudgetLedger
from app.ai.models import ModelRequest, ProviderName, ProviderResponse, TaskClass
from app.ai.policy import ModelAPIGate, RequestPolicy
from app.ai.telemetry import InMemoryTelemetrySink
from app.ai.workspace_credentials import (
    WorkspaceCredentialResolver,
    _CredentialRow,
    invalidate_platform_credential_cache,
    invalidate_workspace_credential_cache,
)
from app.core.config import settings
from app.db.base import Base
from app.models.platform_model_credential import PlatformModelCredential
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_model_credential import WorkspaceModelCredential
from app.services.platform_model_credential_service import (
    PlatformModelCredentialService,
)
from app.services.workspace_model_credential_service import (
    WorkspaceModelCredentialService,
)
from tests.ai.test_gateway import FakeProvider, RecordingUsageAccountant

TEST_CIPHER_KEY = "test-only-resolver-cipher-key-2Vg7hK4sQ9xD1mW6pZ3c"
WORKSPACE_KEY = "chave-do-workspace-0123456789-fim-wxyz"


@pytest.fixture(autouse=True)
def _configure_cipher_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_CIPHER_KEY)


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    database_path = tmp_path / "byok-resolver.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        poolclass=NullPool,
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_workspace(factory) -> UUID:
    async with factory() as db:
        workspace = Workspace(
            name="Workspace BYOK",
            slug="workspace-byok",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.commit()
        return workspace.id


async def _store_credential(factory, workspace_id: UUID, api_key: str) -> None:
    async with factory() as db:
        await WorkspaceModelCredentialService(db).set_credential(
            workspace_id=workspace_id,
            provider="openai",
            api_key=api_key,
            actor_user_id=None,
        )
        await db.commit()


class KeyedFakeProvider(FakeProvider):
    """Adapter fake que registra a API key com que foi construído."""

    def __init__(self, name: ProviderName, api_key: str, response=None) -> None:
        super().__init__(name, response=response)
        self.api_key = api_key


def _counting_session_factory(factory):
    calls = {"count": 0}

    def make_session():
        calls["count"] += 1
        return factory()

    return make_session, calls


def _resolver(factory, *, provider_calls: list[str] | None = None):
    recorded = provider_calls if provider_calls is not None else []

    def adapter_factory(key: str):
        recorded.append(key)
        return KeyedFakeProvider(
            ProviderName.OPENAI,
            key,
            response=ProviderResponse("byok", "test-cheap", 5, 5),
        )

    make_session, calls = _counting_session_factory(factory)
    resolver = WorkspaceCredentialResolver(
        adapter_factories={ProviderName.OPENAI: adapter_factory},
        session_factory=make_session,
    )
    return resolver, calls, recorded


@pytest.mark.asyncio
async def test_without_stored_credential_falls_back_to_platform(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    resolver, calls, _ = _resolver(session_factory)

    adapter = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert adapter is None  # o gateway mantém o singleton da plataforma
    assert calls["count"] == 1

    # Ausência de override também é cacheada — sem novo hit no banco.
    assert await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI) is None
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_active_credential_builds_workspace_adapter_with_cache(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_credential(session_factory, workspace_id, WORKSPACE_KEY)
    resolver, calls, _ = _resolver(session_factory)

    adapter = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert isinstance(adapter, KeyedFakeProvider)
    assert adapter.api_key == WORKSPACE_KEY
    assert calls["count"] == 1

    again = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert again is adapter  # cache por workspace+provider
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_update_invalidates_cache_and_new_key_takes_effect(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_credential(session_factory, workspace_id, WORKSPACE_KEY)
    resolver, calls, _ = _resolver(session_factory)

    first = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert first.api_key == WORKSPACE_KEY

    # Rotação via serviço dispara a invalidação global dos resolvers ativos.
    await _store_credential(
        session_factory, workspace_id, "chave-rotacionada-9876-final-nova"
    )
    rotated = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert rotated is not first
    assert rotated.api_key == "chave-rotacionada-9876-final-nova"
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_deactivation_returns_to_platform_fallback(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_credential(session_factory, workspace_id, WORKSPACE_KEY)
    resolver, _, _ = _resolver(session_factory)

    assert (
        await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    ) is not None

    async with session_factory() as db:
        await WorkspaceModelCredentialService(db).deactivate_credential(
            workspace_id=workspace_id,
            provider="openai",
            actor_user_id=None,
        )
        await db.commit()

    assert (
        await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    ) is None


@pytest.mark.asyncio
async def test_unmapped_provider_and_invalid_workspace_use_platform(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    resolver, calls, _ = _resolver(session_factory)

    # Provider sem factory/mapeamento BYOK: plataforma, sem tocar o banco.
    assert (
        await resolver.resolve_adapter(str(workspace_id), ProviderName.VERTEX_GEMINI)
    ) is None
    assert calls["count"] == 0
    # workspace_id que não é UUID não tem credencial BYOK possível, mas a
    # camada da plataforma (Blackbox) ainda é consultada: uma leitura, sem chave.
    assert await resolver.resolve_adapter("workspace-a", ProviderName.OPENAI) is None
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_undecryptable_credential_fails_closed(session_factory, monkeypatch):
    workspace_id = await _seed_workspace(session_factory)
    await _store_credential(session_factory, workspace_id, WORKSPACE_KEY)

    # Corrompe o valor cifrado: decifragem impossível não pode virar fallback.
    async with session_factory() as db:
        await db.execute(
            update(WorkspaceModelCredential)
            .where(WorkspaceModelCredential.workspace_id == workspace_id)
            .values(api_key_encrypted="corrompido-nao-fernet")
        )
        await db.commit()

    resolver, _, _ = _resolver(session_factory)
    with pytest.raises(AIConfigurationError):
        await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)


@pytest.mark.asyncio
async def test_manual_invalidation_helper_reaches_active_resolvers(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_credential(session_factory, workspace_id, WORKSPACE_KEY)
    resolver, calls, _ = _resolver(session_factory)

    await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    invalidate_workspace_credential_cache(str(workspace_id), "openai")
    await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert calls["count"] == 2


def _gateway(providers, resolver):
    route_catalog = RouteCatalog.from_json(
        '{"version":"routes-test","classes":{"simple":['
        '{"provider":"openai","model":"test-cheap"}]},"agents":{}}'
    )
    price_catalog = PriceCatalog.from_json(
        '{"version":"prices-test","currency":"USD","models":{'
        '"openai:test-cheap":{"input_per_million_usd":"1",'
        '"output_per_million_usd":"2"}}}'
    )
    return ModelGateway(
        gate=ModelAPIGate(True, True),
        route_catalog=route_catalog,
        price_catalog=price_catalog,
        providers=providers,
        model_allowlist=frozenset({"openai:test-cheap"}),
        request_policy=RequestPolicy(
            max_input_tokens=10_000,
            max_output_tokens=2_000,
            max_cost_per_call_usd=Decimal("1"),
            default_workspace_daily_budget_usd=Decimal("5"),
            workspace_daily_budgets_usd={},
            agent_call_caps_usd={},
        ),
        budget_ledger=InMemoryDailyBudgetLedger(),
        circuit_breaker=CircuitBreaker(failure_threshold=2, cooldown_seconds=60),
        telemetry=InMemoryTelemetrySink(),
        response_cache=NullResponseCache(),
        default_timeout_seconds=1,
        max_retries=0,
        retry_base_seconds=0,
        response_cache_enabled=False,
        response_cache_ttl_seconds=60,
        usage_accountant=RecordingUsageAccountant(),
        workspace_credential_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_gateway_uses_workspace_adapter_and_platform_fallback(session_factory):
    workspace_with_key = await _seed_workspace(session_factory)
    await _store_credential(session_factory, workspace_with_key, WORKSPACE_KEY)

    async with session_factory() as db:
        other = Workspace(
            name="Workspace Plataforma",
            slug="workspace-plataforma",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(other)
        await db.commit()
        workspace_without_key = other.id

    platform_provider = FakeProvider(
        ProviderName.OPENAI,
        response=ProviderResponse("plataforma", "test-cheap", 5, 5),
    )
    resolver, _, _ = _resolver(session_factory)
    gateway = _gateway({ProviderName.OPENAI: platform_provider}, resolver)

    def make_request(workspace_id: UUID) -> ModelRequest:
        return ModelRequest(
            messages=[AIMessage("user", "Classifique o atendimento.")],
            workspace_id=str(workspace_id),
            agent_name="sdr",
            task_class=TaskClass.SIMPLE,
            max_output_tokens=100,
        )

    byok_result = await gateway.generate(make_request(workspace_with_key))
    assert byok_result.text == "byok"
    assert platform_provider.calls == 0  # a chamada saiu pelo adapter BYOK

    platform_result = await gateway.generate(make_request(workspace_without_key))
    assert platform_result.text == "plataforma"
    assert platform_provider.calls == 1  # fallback para o env da plataforma


# ── Camada da plataforma (chave gravada pelo Blackbox) ────────────────────

PLATFORM_KEY = "chave-da-plataforma-0123456789-fim-plat"
PLATFORM_ROTATED_KEY = "chave-da-plataforma-rotacionada-5555-fim-rota"


async def _store_platform_credential(factory, api_key: str, *, provider: str = "openai") -> None:
    async with factory() as db:
        await PlatformModelCredentialService(db).set_credential(
            provider=provider,
            api_key=api_key,
            actor_user_id=None,
        )
        await db.commit()


class _Clock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _clocked_resolver(factory, clock: _Clock):
    recorded: list[str] = []

    def adapter_factory(key: str):
        recorded.append(key)
        return KeyedFakeProvider(
            ProviderName.OPENAI,
            key,
            response=ProviderResponse("byok", "test-cheap", 5, 5),
        )

    make_session, calls = _counting_session_factory(factory)
    resolver = WorkspaceCredentialResolver(
        adapter_factories={ProviderName.OPENAI: adapter_factory},
        session_factory=make_session,
        clock=clock,
    )
    return resolver, calls, recorded


@pytest.mark.asyncio
async def test_platform_credential_used_when_workspace_has_none(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    resolver, calls, _ = _resolver(session_factory)

    adapter = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert isinstance(adapter, KeyedFakeProvider)
    assert adapter.api_key == PLATFORM_KEY
    assert calls["count"] == 1  # workspace e plataforma lidos na mesma sessão

    again = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert again is adapter
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_workspace_credential_wins_over_platform(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    await _store_credential(session_factory, workspace_id, WORKSPACE_KEY)
    resolver, _, _ = _resolver(session_factory)

    adapter = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert adapter.api_key == WORKSPACE_KEY


@pytest.mark.asyncio
async def test_platform_credential_used_for_invalid_workspace_id(session_factory):
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    resolver, calls, _ = _resolver(session_factory)

    adapter = await resolver.resolve_adapter("workspace-a", ProviderName.OPENAI)
    assert adapter.api_key == PLATFORM_KEY
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_inactive_platform_credential_falls_back_to_env(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    resolver, _, _ = _resolver(session_factory)
    assert await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI) is not None

    async with session_factory() as db:
        await PlatformModelCredentialService(db).deactivate_credential(
            provider="openai", actor_user_id=None
        )
        await db.commit()

    # A desativação invalida os resolvers deste processo.
    assert await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI) is None


@pytest.mark.asyncio
async def test_platform_rotation_from_another_process_lands_after_recheck_window(
    session_factory,
):
    """Troca feita em outro processo (sem invalidação local) vale após a janela."""
    workspace_id = await _seed_workspace(session_factory)
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    clock = _Clock()
    resolver, calls, _ = _clocked_resolver(session_factory, clock)

    first = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert first.api_key == PLATFORM_KEY
    assert calls["count"] == 1

    # Simula outro processo: escreve direto na tabela, sem invalidar este resolver.
    from app.services.workspace_model_credential_service import ModelCredentialCipher

    async with session_factory() as db:
        await db.execute(
            update(PlatformModelCredential)
            .where(PlatformModelCredential.provider == "openai")
            .values(
                api_key_encrypted=ModelCredentialCipher().encrypt(PLATFORM_ROTATED_KEY),
                api_key_hint=PLATFORM_ROTATED_KEY[-4:],
            )
        )
        await db.commit()

    clock.advance(5)
    still_cached = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert still_cached is first
    assert calls["count"] == 1

    clock.advance(11)  # 16 s desde a última conferência
    rotated = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert rotated is not first
    assert rotated.api_key == PLATFORM_ROTATED_KEY
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_workspace_rotation_from_another_process_lands_after_recheck_window(
    session_factory,
):
    """Correção do V34: rotação BYOK feita na API chega ao worker sem reinício."""
    workspace_id = await _seed_workspace(session_factory)
    await _store_credential(session_factory, workspace_id, WORKSPACE_KEY)
    clock = _Clock()
    resolver, calls, _ = _clocked_resolver(session_factory, clock)

    first = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert first.api_key == WORKSPACE_KEY

    from app.services.workspace_model_credential_service import ModelCredentialCipher

    async with session_factory() as db:
        await db.execute(
            update(WorkspaceModelCredential)
            .where(WorkspaceModelCredential.workspace_id == workspace_id)
            .values(
                api_key_encrypted=ModelCredentialCipher().encrypt(
                    "chave-rotacionada-9876-final-nova"
                ),
                api_key_hint="nova",
            )
        )
        await db.commit()

    clock.advance(16)
    rotated = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert rotated is not first
    assert rotated.api_key == "chave-rotacionada-9876-final-nova"
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_recheck_without_change_keeps_adapter_and_skips_decrypt(
    session_factory, monkeypatch
):
    workspace_id = await _seed_workspace(session_factory)
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    clock = _Clock()
    resolver, calls, recorded = _clocked_resolver(session_factory, clock)

    first = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert first.api_key == PLATFORM_KEY
    assert recorded == [PLATFORM_KEY]

    from app.services import workspace_model_credential_service as cipher_module

    decrypt_calls = {"count": 0}
    original = cipher_module.ModelCredentialCipher.decrypt_strict

    def counting_decrypt(self, value):
        decrypt_calls["count"] += 1
        return original(self, value)

    monkeypatch.setattr(cipher_module.ModelCredentialCipher, "decrypt_strict", counting_decrypt)

    clock.advance(16)
    again = await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert again is first  # mesmo stamp: adapter preservado
    assert decrypt_calls["count"] == 0
    assert recorded == [PLATFORM_KEY]
    assert calls["count"] == 2  # a releitura barata aconteceu


@pytest.mark.asyncio
async def test_platform_undecryptable_credential_fails_closed(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    async with session_factory() as db:
        await db.execute(
            update(PlatformModelCredential)
            .where(PlatformModelCredential.provider == "openai")
            .values(api_key_encrypted="corrompido-nao-fernet")
        )
        await db.commit()

    resolver, _, _ = _resolver(session_factory)
    with pytest.raises(AIConfigurationError):
        await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)


@pytest.mark.asyncio
async def test_database_failure_on_recheck_fails_closed(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    clock = _Clock()
    resolver, _, _ = _clocked_resolver(session_factory, clock)
    assert await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI) is not None

    class _BrokenSession:
        async def __aenter__(self):
            raise ConnectionError("banco indisponível")

        async def __aexit__(self, *_args):
            return False

    resolver._session_factory = lambda: _BrokenSession()
    clock.advance(16)
    with pytest.raises(AIConfigurationError):
        await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)


@pytest.mark.asyncio
async def test_platform_invalidation_helper_reaches_active_resolvers(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _store_platform_credential(session_factory, PLATFORM_KEY)
    resolver, calls, _ = _resolver(session_factory)

    await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    invalidate_platform_credential_cache("openai")
    await resolver.resolve_adapter(str(workspace_id), ProviderName.OPENAI)
    assert calls["count"] == 2


def test_lock_is_bound_per_event_loop():
    """Celery fecha um loop por task: o lock não pode ficar preso ao primeiro."""

    class _FakeCipher:
        def decrypt_strict(self, value):
            return "chave-do-stub-0123456789"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _StubResolver(WorkspaceCredentialResolver):
        async def _load_workspace_row(self, db, workspace_id, provider_key):
            await asyncio.sleep(0.005)
            return None

        async def _load_platform_row(self, db, provider_key):
            await asyncio.sleep(0.005)
            return _CredentialRow(stamp=("stamp", True), api_key_encrypted="x")

        def _get_cipher(self):
            return _FakeCipher()

    resolver = _StubResolver(
        adapter_factories={
            ProviderName.OPENAI: lambda key: KeyedFakeProvider(ProviderName.OPENAI, key)
        },
        session_factory=lambda: _Session(),
        recheck_seconds=0.0,  # sempre vencido: toda chamada passa pelo lock
    )

    async def contended_pair():
        results = await asyncio.gather(
            resolver.resolve_adapter("workspace-a", ProviderName.OPENAI),
            resolver.resolve_adapter("workspace-a", ProviderName.OPENAI),
        )
        assert all(isinstance(item, KeyedFakeProvider) for item in results)

    asyncio.run(contended_pair())
    asyncio.run(contended_pair())  # segundo loop: não pode levantar RuntimeError


class _UnconfiguredProvider(FakeProvider):
    """Singleton da plataforma com env vazia."""

    def require_configured(self) -> None:
        raise ProviderNotConfiguredError("Credencial não configurada para o provider openai.")


@pytest.mark.asyncio
async def test_gateway_uses_platform_key_when_env_is_empty(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    resolver, _, _ = _resolver(session_factory)
    gateway = _gateway({ProviderName.OPENAI: _UnconfiguredProvider(ProviderName.OPENAI)}, resolver)
    request = ModelRequest(
        messages=[AIMessage("user", "Classifique o atendimento.")],
        workspace_id=str(workspace_id),
        agent_name="sdr",
        task_class=TaskClass.SIMPLE,
        max_output_tokens=100,
    )

    # Sem chave em nenhuma camada: a rota falha de forma visível, não silenciosa.
    with pytest.raises(AIRuntimeError):
        await gateway.generate(request)

    await _store_platform_credential(session_factory, PLATFORM_KEY)
    result = await gateway.generate(request)
    assert result.text == "byok"  # adapter construído com a chave do Blackbox
