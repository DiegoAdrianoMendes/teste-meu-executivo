"""
Configuração global dos testes.
Usa banco de dados SQLite em memória para isolamento total (sem PostgreSQL).

Os modelos usam FlexibleJSON/FlexibleUUID (app/db/base.py) que detectam
o dialeto automaticamente — SQLite nos testes, PostgreSQL em produção.
"""

# ruff: noqa: E402
import asyncio
import unittest.mock as _mock
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Substituir lifespan ANTES de importar app.main ──────────────────────────
# Evita que o startup tente conectar ao Redis/PostgreSQL externo nos testes.
# O lifespan original faz: start_redis_listener + seed_templates (ambos externos).
import app.websockets.manager as _ws_mod

_ws_mod.ws_manager.start_redis_listener = _mock.AsyncMock(return_value=None)
_ws_mod.ws_manager.stop_redis_listener = _mock.AsyncMock(return_value=None)

import app.models  # noqa: F401 — garante que todos os modelos sejam registrados na Base
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
)
from app.core.security import create_admin_session_token, verify_token
from app.db.base import Base
from app.main import app as fastapi_app  # instância FastAPI, não o módulo
from app.models.auth_security import AdministrativeSession, MfaVerificationMethod
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole

# Engine de teste em memória (SQLite)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _build_oss_adapter_reference_app() -> FastAPI:
    """Mount preserved migration adapters outside the KLM product boundary."""

    # O catálogo OSS percorre e valida um inventário grande do repositório.
    # Importe-o somente nas jornadas que realmente exercitam os adapters; isso
    # mantém os testes comuns independentes desse custo e de arquivos frios.
    from app.api.v1.endpoints.oss_adapters import (
        router as oss_adapter_reference_router,
    )

    reference_app = FastAPI()
    reference_app.include_router(oss_adapter_reference_router, prefix="/api/v1")
    return reference_app


@pytest_asyncio.fixture(scope="function", autouse=True)
async def shutdown_default_executor():
    """Finaliza workers criados por asyncio.to_thread antes de fechar o loop do teste."""
    yield
    await asyncio.get_running_loop().shutdown_default_executor()


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def dispose_test_engine():
    """Encerra a conexão SQLite global e sua thread de worker ao fim da suíte."""
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_db():
    """Cria e destrói as tabelas para cada teste (isolamento total)."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session():
    """Fornece uma sessão de banco de dados para testes."""
    async with TestSessionLocal() as session:
        yield session


async def _request_scoped_test_db(db_session: AsyncSession):
    """Mirror get_db's commit/rollback boundary while reusing the test session."""
    await db_session.commit()
    try:
        yield db_session
        await db_session.commit()
    except Exception:
        retained_instances = list(db_session.identity_map.values())
        await db_session.rollback()
        # Production closes the request session here. Tests intentionally keep
        # one session so they can assert persisted state between HTTP calls.
        for instance in retained_instances:
            if not inspect(instance).persistent:
                continue
            try:
                await db_session.refresh(instance)
            except Exception:
                continue
        raise


@pytest_asyncio.fixture
async def auth_context(db_session: AsyncSession) -> AuthContext:
    """Cria um workspace e um usuário autenticado para testes tenant-aware."""
    workspace = Workspace(
        name="Workspace Teste",
        slug="workspace-teste",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()

    user = User(
        name="Usuário Teste",
        email="teste@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="UT",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()

    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.workspace_owner,
        status=MembershipStatus.active,
        is_default=True,
    )
    db_session.add(membership)
    await db_session.flush()

    return AuthContext(
        user_id=user.id,
        workspace_id=workspace.id,
        membership_id=membership.id,
        role=WorkspaceRole.workspace_owner,
        is_internal_staff=False,
    )


@pytest_asyncio.fixture
async def active_subscription(
    db_session: AsyncSession,
    auth_context: AuthContext,
) -> Subscription:
    """Provision the commercial precondition for tenant feature tests.

    Tests that exercise missing, suspended, or provider-controlled subscription
    states must keep seeding those states explicitly instead of using this fixture.
    """
    now = datetime.now(UTC)
    plan = Plan(
        name=f"Active test plan {uuid4().hex[:8]}",
        tier=PlanTier.starter,
        price_monthly_cents=26_900,
        price_annually_cents=0,
        features={"maxCampaignRecipientsPerMonth": 100_000},
        is_active=True,
        is_public=False,
    )
    db_session.add(plan)
    await db_session.flush()
    subscription = Subscription(
        workspace_id=auth_context.workspace_id,
        plan_id=plan.id,
        status=SubscriptionStatus.active,
        interval=BillingInterval.monthly,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        amount_cents=26_900,
        currency="BRL",
    )
    db_session.add(subscription)
    db_session.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=entitlement,
                enabled=True,
                limit_quantity=limit,
                hard_limit=True,
            )
            for entitlement, limit in (
                (EntitlementKey.users, 100),
                (EntitlementKey.whatsapp_numbers, 100),
                (EntitlementKey.agents, 100),
                (EntitlementKey.automations, 1_000),
                (EntitlementKey.ai_executions, 10_000),
                (EntitlementKey.ai_conversations, 10_000),
                (EntitlementKey.campaign_recipients, 100_000),
                (EntitlementKey.contacts, 100_000),
                (EntitlementKey.audio_minutes, 1_000),
                (EntitlementKey.files, 100_000),
                (EntitlementKey.storage_bytes, 10 * 1024 * 1024 * 1024),
                (EntitlementKey.active_tasks, 10_000),
                (EntitlementKey.legal_suite, None),
                (EntitlementKey.legal_areas, 100),
                (EntitlementKey.task_participants, 100),
                (EntitlementKey.task_reviews, 1),
                (EntitlementKey.review_rounds, 100),
                (EntitlementKey.task_reminder_rules, 100),
                (EntitlementKey.meeting_participants, 100),
                (EntitlementKey.digital_signature, None),
                (EntitlementKey.email, None),
            )
        ]
    )
    await db_session.flush()
    return subscription


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, auth_context: AuthContext):
    """Fornece um cliente HTTP de teste com banco de dados injetado."""

    async def override_get_db():
        async for session in _request_scoped_test_db(db_session):
            yield session

    async def override_get_current_auth_context():
        return auth_context

    async def override_get_current_user_id():
        return auth_context.user_id

    async def override_get_current_workspace_id():
        return auth_context.workspace_id

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_auth_context] = override_get_current_auth_context
    fastapi_app.dependency_overrides[get_current_user_id] = override_get_current_user_id
    fastapi_app.dependency_overrides[get_current_workspace_id] = override_get_current_workspace_id
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def unauthenticated_client(db_session: AsyncSession):
    async def override_get_db():
        async for session in _request_scoped_test_db(db_session):
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def oss_adapter_client(db_session: AsyncSession, auth_context: AuthContext):
    """Exercise preserved adapter code without exposing it from the KLM app."""

    reference_app = _build_oss_adapter_reference_app()

    async def override_get_db():
        yield db_session

    async def override_get_current_auth_context():
        return auth_context

    async def override_get_current_user_id():
        return auth_context.user_id

    async def override_get_current_workspace_id():
        return auth_context.workspace_id

    reference_app.dependency_overrides[get_db] = override_get_db
    reference_app.dependency_overrides[get_current_auth_context] = override_get_current_auth_context
    reference_app.dependency_overrides[get_current_user_id] = override_get_current_user_id
    reference_app.dependency_overrides[get_current_workspace_id] = override_get_current_workspace_id
    async with AsyncClient(
        transport=ASGITransport(app=reference_app), base_url="http://test"
    ) as adapter_client:
        yield adapter_client


@pytest_asyncio.fixture
async def oss_unauthenticated_adapter_client(db_session: AsyncSession):
    reference_app = _build_oss_adapter_reference_app()

    async def override_get_db():
        yield db_session

    reference_app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=reference_app), base_url="http://test"
    ) as adapter_client:
        yield adapter_client


@pytest_asyncio.fixture
async def platform_auth_context(db_session: AsyncSession) -> AuthContext:
    user = User(
        name="Platform Admin",
        email="platform@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="PA",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_role_package="platform_owner",
        platform_access_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()

    return AuthContext(
        user_id=user.id,
        workspace_id=None,
        membership_id=None,
        role=None,
        is_internal_staff=True,
        surface="platform",
    )


@pytest_asyncio.fixture
async def platform_client(db_session: AsyncSession, platform_auth_context: AuthContext):
    async def override_get_db():
        async for session in _request_scoped_test_db(db_session):
            yield session

    async def override_get_current_auth_context():
        return platform_auth_context

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_auth_context] = override_get_current_auth_context
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def local_outbox_headers(db_session: AsyncSession) -> dict[str, str]:
    """Mint the privileged local MFA surface without exposing it publicly."""
    user = User(
        name="Outbox Platform Admin",
        email=f"outbox-admin-{id(db_session)}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="OA",
        color="#0e3d5c",
        is_active=True,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_role_package="platform_owner",
        platform_access_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    session_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    token = create_admin_session_token(
        user.id,
        session_id=session_id,
        expires_at=expires_at,
        mfa_method=MfaVerificationMethod.challenge.value,
        auth_epoch=user.auth_epoch,
    )
    payload = verify_token(token)
    assert payload is not None
    db_session.add(
        AdministrativeSession(
            id=session_id,
            user_id=user.id,
            token_jti=str(payload["jti"]),
            mfa_method=MfaVerificationMethod.challenge,
            expires_at=expires_at,
            last_verified_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    return {"X-Administrative-Session": token}
