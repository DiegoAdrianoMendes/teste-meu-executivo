from __future__ import annotations

import ast
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401
from app.agents.stubs import klm_sdr
from app.db.identity_tenant import (
    resolve_signup_verification_workspace,
    resolve_workspace_invitation_workspace,
)
from app.db.platform_control import (
    _set_commercial_portability_rebind_context,
    _set_commercial_portability_restore_rebind_context,
    set_platform_control_context,
)
from app.db.platform_support import set_platform_support_context
from app.db.rls import (
    OSS_NATIVE_TENANT_TABLES,
    RESOLVER_OWNER_ROLE_MARKER,
    RLS_POLICY_SPECS,
    RLS_TENANT_TABLES,
    RLSRuntimeConfigurationError,
    expected_runtime_table_privileges,
    install_rls_runtime_guard,
    policy_spec_expression_matches,
)
from app.db.tenant import TenantSession, WorkspaceContextError, set_workspace_context
from app.db.webhook_tenant import (
    resolve_whatsapp_webhook_workspace,
    resolve_zapsign_webhook_workspace,
)
from app.models.client import Client
from app.models.commercial_catalog import (
    CommercialAdjustment,
    CommercialAdjustmentSource,
    CommercialAdjustmentType,
    CommercialBillingInterval,
    CommercialCatalogEvent,
    CommercialCommandReceipt,
    CommercialContractLineStatus,
    CommercialContractStatus,
    CommercialOffer,
    CommercialOfferFamily,
    CommercialOfferKind,
    CommercialOfferVersion,
    CommercialPortability,
    CommercialRecordStatus,
    EntitlementBonus,
    EntitlementBonusMode,
    EntitlementBonusSource,
    WorkspaceCommercialContract,
    WorkspaceContractLine,
)
from app.models.configuration import ConfigDocument, ConfigDocumentVersion, ConfigScope
from app.models.entitlement import (
    EntitlementKey,
    OverrideMode,
    WorkspaceEntitlementOverride,
    WorkspaceMonthlyUsage,
)
from app.models.identity import (
    IdentityIdempotencyRecord,
    IdentityOutboxMessage,
    IdentityOutboxPurpose,
    InvitationStatus,
    SignupVerification,
    WorkspaceInvitation,
)
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import KanbanColumnId, Matter, PracticeArea
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    IntegrationEventReceipt,
    OSSBridgeProvider,
)
from app.models.playbook import (
    PlaybookLifecycleState,
    PlaybookScopeBinding,
    PlaybookScopeDimension,
    PlaybookSimulationRun,
    PlaybookSimulationStatus,
    PlaybookVersionGovernance,
)
from app.models.public_api import WorkspacePublicApiToken
from app.models.support_ticket import (
    SupportTicket,
    SupportTicketPriority,
    SupportTicketQueue,
    SupportTicketSeverity,
    SupportTicketStatus,
    SupportTicketType,
)
from app.models.user import AgentType, User, UserRole
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WhatsAppConnection,
    WhatsAppContact,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.sdr import SDRHandoffTarget, SDRQualification
from app.schemas.user import RegisterRequest
from app.services.chatwoot_bridge_service import (
    ChatwootAccountBindingError,
    ChatwootBridgeService,
)
from app.services.identity_lifecycle_service import IdentityLifecycleService
from app.services.klm_local_seed_service import (
    LOCAL_COMMERCIAL_SEED_VERSION,
    LOCAL_PLATFORM_USER_EMAIL,
    LOCAL_WORKSPACE_SLUG,
    KlmLocalSeedService,
)
from app.services.public_api_service import PublicApiService
from app.services.whatsapp_service import WhatsAppService

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")
ADMIN_URL = os.getenv("KLM_TEST_POSTGRES_ADMIN_URL")
APP_URL = os.getenv("KLM_TEST_POSTGRES_APP_URL")

if not MASTER_URL or not ADMIN_URL or not APP_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL, "
        "KLM_TEST_POSTGRES_ADMIN_URL, and KLM_TEST_POSTGRES_APP_URL; "
        "run scripts/verify_postgresql_qa.sh."
    )


def _migration_heads() -> set[str]:
    revisions: set[str] = set()
    parent_revisions: set[str] = set()
    for path in Path("alembic/versions").glob("*.py"):
        values: dict[str, object] = {}
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            target = None
            value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            if (
                isinstance(target, ast.Name)
                and target.id in {"revision", "down_revision"}
                and value
            ):
                values[target.id] = ast.literal_eval(value)
        revision = values.get("revision")
        if isinstance(revision, str):
            revisions.add(revision)
        parents = values.get("down_revision")
        if isinstance(parents, str):
            parent_revisions.add(parents)
        elif isinstance(parents, tuple | list):
            parent_revisions.update(str(parent) for parent in parents if parent)
    return revisions - parent_revisions


@dataclass(frozen=True)
class PostgresEngines:
    master: AsyncEngine
    admin: AsyncEngine
    app: AsyncEngine
    app_sessions: async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class TenantRows:
    workspace_a: UUID
    workspace_b: UUID
    audit_a: UUID
    audit_b: UUID


@pytest_asyncio.fixture
async def postgres_engines() -> AsyncIterator[PostgresEngines]:
    assert MASTER_URL is not None and ADMIN_URL is not None and APP_URL is not None
    master_engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    admin_engine = create_async_engine(ADMIN_URL, pool_pre_ping=True)
    app_engine = create_async_engine(APP_URL, pool_pre_ping=True)
    install_rls_runtime_guard(app_engine)
    app_sessions = async_sessionmaker(
        app_engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
    )

    try:
        yield PostgresEngines(master_engine, admin_engine, app_engine, app_sessions)
    finally:
        await app_engine.dispose()
        await admin_engine.dispose()
        await master_engine.dispose()


@pytest_asyncio.fixture
async def tenant_rows(postgres_engines: PostgresEngines) -> AsyncIterator[TenantRows]:
    token = uuid4().hex[:12]
    master_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with master_sessions() as session:
        workspace_a = Workspace(
            name=f"RLS A {token}",
            slug=f"rls-a-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        workspace_b = Workspace(
            name=f"RLS B {token}",
            slug=f"rls-b-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        session.add_all((workspace_a, workspace_b))
        await session.flush()

        audit_a = _audit_event(workspace_a.id, f"seed-a-{token}")
        audit_b = _audit_event(workspace_b.id, f"seed-b-{token}")
        session.add_all(
            (
                audit_a,
                audit_b,
                _receipt(workspace_a.id, f"seed-a-{token}"),
                _receipt(workspace_b.id, f"seed-b-{token}"),
            )
        )
        await session.commit()

    rows = TenantRows(workspace_a.id, workspace_b.id, audit_a.id, audit_b.id)
    try:
        yield rows
    finally:
        async with master_sessions() as session:
            await session.execute(
                text(
                    "ALTER TABLE klm_audit_events DISABLE TRIGGER "
                    "trg_klm_audit_events_immutable_row"
                )
            )
            try:
                await session.execute(
                    delete(Workspace).where(
                        Workspace.id.in_((rows.workspace_a, rows.workspace_b))
                    )
                )
            finally:
                await session.execute(
                    text(
                        "ALTER TABLE klm_audit_events ENABLE TRIGGER "
                        "trg_klm_audit_events_immutable_row"
                    )
                )
            await session.commit()


def _audit_event(workspace_id: UUID, suffix: str) -> KlmAuditEvent:
    return KlmAuditEvent(
        workspace_id=workspace_id,
        actor_type="system",
        actor_label="RLS integration test",
        event_type=f"rls.{suffix}",
        entity_type="workspace",
        summary=f"RLS integration event {suffix}",
    )


def _receipt(workspace_id: UUID, suffix: str) -> IntegrationEventReceipt:
    return IntegrationEventReceipt(
        workspace_id=workspace_id,
        provider=OSSBridgeProvider.bottlecrm,
        external_event_id=f"rls-{suffix}",
        payload_hash="a" * 64,
        payload={"source": "rls-integration"},
    )


@pytest.mark.asyncio
async def test_runtime_role_and_rls_catalog_are_enforced(
    postgres_engines: PostgresEngines,
) -> None:
    async with postgres_engines.app.connect() as connection:
        role = (
            await connection.execute(
                text(
                    """
                    SELECT current_user,
                           rolsuper,
                           rolbypassrls,
                           rolcreaterole,
                           rolcreatedb,
                           rolreplication,
                           has_schema_privilege(current_user, 'public', 'USAGE'),
                           has_schema_privilege(current_user, 'public', 'CREATE')
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
            )
        ).one()
        assert role[1:] == (False, False, False, False, False, True, False)
        resolver_acl = (
            await connection.execute(
                text(
                    """
                    SELECT has_function_privilege(
                               current_user,
                               'public.klm_resolve_zapsign_webhook_workspace(uuid,text)',
                               'EXECUTE'
                           ),
                           has_function_privilege(
                               current_user,
                               'public.klm_resolve_whatsapp_webhook_workspace(text)',
                               'EXECUTE'
                           ),
                           has_function_privilege(
                               current_user,
                               'public.klm_resolve_whatsapp_template_webhook_workspace(text)',
                               'EXECUTE'
                           ),
                           has_function_privilege(
                               current_user,
                               'public.klm_resolve_asaas_webhook_workspace(text)',
                               'EXECUTE'
                           ),
                           has_function_privilege(
                               current_user,
                               'public.klm_resolve_signup_verification_workspace(text,uuid)',
                               'EXECUTE'
                           ),
                           has_function_privilege(
                               current_user,
                               'public.klm_resolve_workspace_invitation_workspace(uuid,text,uuid)',
                               'EXECUTE'
                           )
                    """
                )
            )
        ).one()
        assert resolver_acl == (True, True, True, True, True, True)
        resolver_owners = set(
            (
                await connection.execute(
                    text(
                        """
                        SELECT DISTINCT pg_get_userbyid(procedure.proowner)
                        FROM pg_proc AS procedure
                        WHERE procedure.oid IN (
                            to_regprocedure(
                                'public.klm_resolve_zapsign_webhook_workspace(uuid,text)'
                            ),
                            to_regprocedure(
                                'public.klm_resolve_whatsapp_webhook_workspace(text)'
                            ),
                            to_regprocedure(
                                'public.klm_resolve_whatsapp_template_webhook_workspace(text)'
                            ),
                            to_regprocedure(
                                'public.klm_resolve_asaas_webhook_workspace(text)'
                            ),
                            to_regprocedure(
                                'public.klm_resolve_signup_verification_workspace(text,uuid)'
                            ),
                            to_regprocedure(
                                'public.klm_resolve_workspace_invitation_workspace(uuid,text,uuid)'
                            )
                        )
                        """
                    )
                )
            ).scalars()
        )
        assert len(resolver_owners) == 1
        resolver_owner = resolver_owners.pop()
        assert resolver_owner != role[0]
        owner_contract = (
            await connection.execute(
                text(
                    """
                    SELECT rolcanlogin,
                           rolsuper,
                           rolcreatedb,
                           rolcreaterole,
                           rolinherit,
                           rolreplication,
                           rolbypassrls,
                           pg_has_role(current_user, oid, 'MEMBER'),
                           has_schema_privilege(rolname, 'public', 'USAGE'),
                           has_schema_privilege(rolname, 'public', 'CREATE')
                    FROM pg_roles
                    WHERE rolname = :resolver_owner
                    """
                ),
                {"resolver_owner": resolver_owner},
            )
        ).one()
        assert owner_contract == (
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        )

        privilege_rows = (
            await connection.execute(
                text(
                    """
                    SELECT relation.relname,
                           has_table_privilege(current_user, relation.oid, 'SELECT'),
                           has_table_privilege(current_user, relation.oid, 'INSERT'),
                           has_table_privilege(current_user, relation.oid, 'UPDATE'),
                           has_table_privilege(current_user, relation.oid, 'DELETE'),
                           EXISTS (
                               SELECT 1
                               FROM pg_trigger immutable_trigger
                               WHERE immutable_trigger.tgrelid = relation.oid
                                 AND NOT immutable_trigger.tgisinternal
                                 AND immutable_trigger.tgname LIKE '%immutable%'
                           )
                    FROM pg_class relation
                    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relkind IN ('r', 'p')
                      AND relation.relname = ANY(:table_names)
                    ORDER BY relation.relname
                    """
                ),
                {"table_names": list(RLS_TENANT_TABLES)},
            )
        ).all()
        assert {row[0] for row in privilege_rows} == set(RLS_TENANT_TABLES)
        for table_name, can_select, can_insert, can_update, can_delete, immutable in (
            privilege_rows
        ):
            actual = {
                privilege
                for privilege, granted in (
                    ("SELECT", can_select),
                    ("INSERT", can_insert),
                    ("UPDATE", can_update),
                    ("DELETE", can_delete),
                )
                if granted
            }
            assert actual == expected_runtime_table_privileges(
                table_name,
                immutable=immutable,
            )

        official_delivery_acl = (
            await connection.execute(
                text(
                    """
                    SELECT has_table_privilege(
                               current_user,
                               'platform_official_whatsapp_deliveries',
                               'SELECT'
                           ),
                           has_table_privilege(
                               current_user,
                               'platform_official_whatsapp_deliveries',
                               'INSERT'
                           ),
                           has_table_privilege(
                               current_user,
                               'platform_official_whatsapp_deliveries',
                               'UPDATE'
                           ),
                           has_table_privilege(
                               current_user,
                               'platform_official_whatsapp_deliveries',
                               'DELETE'
                           ),
                           has_table_privilege(
                               current_user,
                               'platform_official_whatsapp_deliveries',
                               'TRUNCATE'
                           )
                    """
                )
            )
        ).one()
        assert official_delivery_acl == (True, True, True, False, False)

    async with postgres_engines.admin.connect() as connection:
        revisions = set(
            (await connection.scalars(text("SELECT version_num FROM alembic_version"))).all()
        )
        assert revisions == _migration_heads()

        table_rows = (
            await connection.execute(
                text(
                    """
                    SELECT relation.relname,
                           relation.relrowsecurity,
                           relation.relforcerowsecurity
                    FROM pg_class relation
                    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relkind IN ('r', 'p')
                    """
                )
            )
        ).all()
        protected = {row[0]: (row[1], row[2]) for row in table_rows}
        assert set(OSS_NATIVE_TENANT_TABLES) <= set(RLS_TENANT_TABLES)
        assert set(RLS_TENANT_TABLES) <= set(protected)
        assert all(protected[table] == (True, True) for table in RLS_TENANT_TABLES)

        policies = (
            await connection.execute(
                text(
                    """
                    SELECT tablename, policyname, permissive, cmd, roles, qual, with_check
                    FROM pg_policies
                    WHERE schemaname = 'public'
                    """
                )
            )
        ).all()
        protected_policies = [row for row in policies if row[0] in RLS_TENANT_TABLES]
        assert len(protected_policies) == sum(
            len(specs) for specs in RLS_POLICY_SPECS.values()
        )
        policy_by_table: dict[str, list[tuple[object, ...]]] = {}
        for row in protected_policies:
            policy_by_table.setdefault(row[0], []).append(tuple(row[1:]))
        assert set(policy_by_table) == set(RLS_TENANT_TABLES)
        for table_name, table_policies in policy_by_table.items():
            expected_specs = RLS_POLICY_SPECS[table_name]
            assert len(table_policies) == len(expected_specs)
            actual_by_name = {str(row[0]): row for row in table_policies}
            for expected in expected_specs:
                policy = actual_by_name[expected.name]
                policy_name, permissive, command, roles, using_clause, check_clause = policy
                assert policy_name == expected.name
                assert permissive == expected.permissive
                assert command == expected.command
                expected_roles = {
                    resolver_owner if item == RESOLVER_OWNER_ROLE_MARKER else item
                    for item in expected.roles
                }
                assert set(roles) == expected_roles
                assert policy_spec_expression_matches(expected.using, using_clause)
                assert policy_spec_expression_matches(expected.with_check, check_clause)


@pytest.mark.asyncio
async def test_official_whatsapp_delivery_contract_is_database_guarded(
    postgres_engines: PostgresEngines,
) -> None:
    connection_id = uuid4()
    delivery_id = uuid4()
    created_connection = False

    async with postgres_engines.admin.begin() as connection:
        existing_connection_id = await connection.scalar(
            text(
                "SELECT id FROM platform_official_whatsapp_connections "
                "WHERE role = 'outbound' LIMIT 1"
            )
        )
        if existing_connection_id is None:
            created_connection = True
            await connection.execute(
                text(
                    """
                    INSERT INTO platform_official_whatsapp_connections (
                        id, role, connection_type, is_active, status,
                        webhook_registered
                    ) VALUES (
                        :id, 'outbound', 'OFFICIAL_MANUAL', true,
                        'disconnected', false
                    )
                    """
                ),
                {"id": connection_id},
            )
        else:
            connection_id = UUID(str(existing_connection_id))
        await connection.execute(
            text(
                """
                INSERT INTO platform_official_whatsapp_deliveries (
                    id, connection_id, role, source_type,
                    idempotency_key_hash, payload_digest,
                    payload_ciphertext, body_length
                ) VALUES (
                    :id, :connection_id, 'outbound', 'postgres_qa',
                    :idempotency_key_hash, :payload_digest,
                    'ciphertext', 12
                )
                """
            ),
            {
                "id": delivery_id,
                "connection_id": connection_id,
                "idempotency_key_hash": uuid4().hex * 2,
                "payload_digest": uuid4().hex * 2,
            },
        )

    try:
        async with postgres_engines.admin.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE platform_official_whatsapp_deliveries "
                    "SET state = 'provider_call_started', attempt_count = 1 "
                    "WHERE id = :id"
                ),
                {"id": delivery_id},
            )

        with pytest.raises(DBAPIError, match="payload ciphertext is immutable"):
            async with postgres_engines.admin.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE platform_official_whatsapp_deliveries "
                        "SET payload_ciphertext = 'ciphertext-forged' WHERE id = :id"
                    ),
                    {"id": delivery_id},
                )

        async with postgres_engines.admin.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE platform_official_whatsapp_deliveries "
                    "SET state = 'failed', payload_ciphertext = NULL, "
                    "failed_at = now() WHERE id = :id"
                ),
                {"id": delivery_id},
            )

        with pytest.raises(DBAPIError, match="request contract is immutable"):
            async with postgres_engines.admin.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE platform_official_whatsapp_deliveries "
                        "SET source_type = 'forged' WHERE id = :id"
                    ),
                    {"id": delivery_id},
                )

        with pytest.raises(DBAPIError, match="cannot be delete"):
            async with postgres_engines.admin.begin() as connection:
                await connection.execute(
                    text(
                        "DELETE FROM platform_official_whatsapp_deliveries "
                        "WHERE id = :id"
                    ),
                    {"id": delivery_id},
                )

        with pytest.raises(DBAPIError, match="cannot be truncate"):
            async with postgres_engines.admin.begin() as connection:
                await connection.execute(
                    text("TRUNCATE TABLE platform_official_whatsapp_deliveries")
                )
    finally:
        async with postgres_engines.admin.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE platform_official_whatsapp_deliveries "
                    "DISABLE TRIGGER USER"
                )
            )
            try:
                await connection.execute(
                    text(
                        "DELETE FROM platform_official_whatsapp_deliveries "
                        "WHERE id = :id"
                    ),
                    {"id": delivery_id},
                )
            finally:
                await connection.execute(
                    text(
                        "ALTER TABLE platform_official_whatsapp_deliveries "
                        "ENABLE TRIGGER USER"
                    )
                )
            if created_connection:
                await connection.execute(
                    text(
                        "DELETE FROM platform_official_whatsapp_connections "
                        "WHERE id = :id"
                    ),
                    {"id": connection_id},
                )


@pytest.mark.asyncio
async def test_superuser_engine_is_rejected_by_runtime_guard(
    postgres_engines: PostgresEngines,
) -> None:
    assert ADMIN_URL is not None
    unsafe_engine = create_async_engine(ADMIN_URL)
    install_rls_runtime_guard(unsafe_engine)
    try:
        with pytest.raises(RLSRuntimeConfigurationError):
            async with unsafe_engine.connect():
                pass
    finally:
        await unsafe_engine.dispose()


@pytest.mark.asyncio
async def test_runtime_guard_rejects_an_additional_permissive_policy(
    postgres_engines: PostgresEngines,
) -> None:
    assert APP_URL is not None
    async with postgres_engines.admin.begin() as connection:
        await connection.execute(
            text(
                """
                DROP POLICY IF EXISTS klm_rls_test_extra_policy
                ON klm_audit_events
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE POLICY klm_rls_test_extra_policy ON klm_audit_events
                FOR SELECT TO PUBLIC USING (true)
                """
            )
        )

    guarded_engine = create_async_engine(APP_URL)
    install_rls_runtime_guard(guarded_engine)
    try:
        with pytest.raises(RLSRuntimeConfigurationError):
            async with guarded_engine.connect():
                pass
    finally:
        await guarded_engine.dispose()
        async with postgres_engines.admin.begin() as connection:
            await connection.execute(
                text(
                    """
                    DROP POLICY IF EXISTS klm_rls_test_extra_policy
                    ON klm_audit_events
                    """
                )
            )


@pytest.mark.asyncio
async def test_postgresql_rls_isolates_tenants_and_reapplies_context(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
) -> None:
    sessions = postgres_engines.app_sessions

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KlmAuditEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(IntegrationEventReceipt)) == 0
        setting = await session.scalar(
            text("SELECT NULLIF(current_setting('app.current_workspace_id', true), '')")
        )
        assert setting is None

        session.add(_audit_event(tenant_rows.workspace_a, "missing-context"))
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

    async with sessions() as session:
        await set_workspace_context(session, tenant_rows.workspace_a)
        visible_ids = set((await session.scalars(select(KlmAuditEvent.id))).all())
        assert tenant_rows.audit_a in visible_ids
        assert tenant_rows.audit_b not in visible_ids

        with pytest.raises(DBAPIError):
            await session.execute(
                update(KlmAuditEvent)
                .where(KlmAuditEvent.id == tenant_rows.audit_b)
                .values(summary="must remain hidden")
            )
        await session.rollback()

        session.add(_audit_event(tenant_rows.workspace_b, "cross-tenant-klm"))
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        assert await session.scalar(select(func.count()).select_from(KlmAuditEvent)) == 1

        session.add(_receipt(tenant_rows.workspace_b, "cross-tenant-oss"))
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        own_event = _audit_event(tenant_rows.workspace_a, "allowed")
        own_receipt = _receipt(tenant_rows.workspace_a, "allowed")
        session.add_all((own_event, own_receipt))
        await session.commit()

        assert await session.scalar(select(func.count()).select_from(KlmAuditEvent)) == 2
        assert await session.scalar(select(func.count()).select_from(IntegrationEventReceipt)) == 2
        with pytest.raises(WorkspaceContextError):
            await set_workspace_context(session, tenant_rows.workspace_b)

    # set_config(..., true) must not leak through the pooled physical connection.
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KlmAuditEvent)) == 0

    async with sessions() as session:
        await set_workspace_context(session, tenant_rows.workspace_b)
        visible_ids = set((await session.scalars(select(KlmAuditEvent.id))).all())
        assert visible_ids == {tenant_rows.audit_b}


@pytest.mark.asyncio
async def test_commercial_rls_allows_tenant_manifest_reads_but_denies_tenant_writes(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
) -> None:
    """Commercial RLS is read-through for tenants and write-only for Blackbox."""

    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    token = uuid4().hex[:12]
    now = datetime.now(UTC)
    actor = User(
        id=uuid4(),
        name=f"Commercial RLS Actor {token}",
        email=f"commercial-rls-{token}@example.test",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="CR",
        color="#164e63",
        is_active=True,
        email_verified_at=now,
        is_internal_staff=True,
        is_platform_admin=True,
        platform_role_package="platform_admin",
        platform_access_active=True,
    )
    sdr_actor = User(
        id=uuid4(),
        name=f"Commercial RLS SDR {token}",
        email=f"commercial-rls-sdr-{token}@example.test",
        agent_type=AgentType.human,
        role=UserRole.intern,
        initials="SD",
        color="#1d4ed8",
        is_active=True,
        email_verified_at=now,
        is_internal_staff=True,
        platform_role_package="sales_sdr",
        platform_access_active=True,
    )
    public_offer = CommercialOffer(
        sku=f"rls_public_{token}",
        family=CommercialOfferFamily.crm,
        kind=CommercialOfferKind.base_plan,
        created_by_user_id=actor.id,
    )
    private_offer = CommercialOffer(
        sku=f"rls_private_{token}",
        family=CommercialOfferFamily.legal,
        kind=CommercialOfferKind.solution,
        created_by_user_id=actor.id,
    )

    seeded_offer_ids: list[UUID] = []
    seeded_version_ids: list[UUID] = []
    seeded_contract_ids: list[UUID] = []
    seeded_line_ids: list[UUID] = []
    seeded_adjustment_ids: list[UUID] = []
    seeded_bonus_ids: list[UUID] = []
    seeded_event_ids: list[UUID] = []
    seeded_receipt_ids: list[UUID] = []
    root_created_offer_id: UUID | None = None

    try:
        async with admin_sessions() as session:
            session.add_all((actor, sdr_actor))
            await session.flush()
            session.add_all((public_offer, private_offer))
            await session.flush()
            public_version = CommercialOfferVersion(
                offer_id=public_offer.id,
                version=1,
                name="Public RLS offer",
                is_public=True,
                is_active=True,
                available_intervals=[CommercialBillingInterval.monthly.value],
                currency="BRL",
                price_monthly_cents=26_900,
                cta="Test",
                entitlement_policy={},
                snapshot_hash="a" * 64,
                publication_reason="PostgreSQL RLS integration fixture.",
                published_by_user_id=actor.id,
            )
            private_version = CommercialOfferVersion(
                offer_id=private_offer.id,
                version=1,
                name="Private contracted RLS offer",
                is_public=False,
                is_active=False,
                is_quote_only=True,
                available_intervals=[CommercialBillingInterval.monthly.value],
                currency="BRL",
                price_monthly_cents=89_900,
                cta="Test",
                entitlement_policy={},
                snapshot_hash="b" * 64,
                publication_reason="PostgreSQL RLS integration fixture.",
                published_by_user_id=actor.id,
            )
            session.add_all((public_version, private_version))
            await session.flush()

            contract_a = WorkspaceCommercialContract(
                workspace_id=tenant_rows.workspace_a,
                status=CommercialContractStatus.active,
                currency="BRL",
                version=1,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
            )
            contract_b = WorkspaceCommercialContract(
                workspace_id=tenant_rows.workspace_b,
                status=CommercialContractStatus.active,
                currency="BRL",
                version=1,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
            )
            session.add_all((contract_a, contract_b))
            await session.flush()

            line_a = WorkspaceContractLine(
                contract_id=contract_a.id,
                offer_id=private_offer.id,
                policy_version_id=private_version.id,
                offer_family=CommercialOfferFamily.legal,
                quantity=1,
                list_price_cents=89_900,
                contracted_price_cents=89_900,
                billing_interval=CommercialBillingInterval.monthly,
                entitlement_overrides={},
                effective_from=now,
                status=CommercialContractLineStatus.active,
                version=1,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
            )
            line_b = WorkspaceContractLine(
                contract_id=contract_b.id,
                offer_id=public_offer.id,
                policy_version_id=public_version.id,
                offer_family=CommercialOfferFamily.crm,
                quantity=1,
                list_price_cents=26_900,
                contracted_price_cents=26_900,
                billing_interval=CommercialBillingInterval.monthly,
                entitlement_overrides={},
                effective_from=now,
                status=CommercialContractLineStatus.active,
                version=1,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
            )
            session.add_all((line_a, line_b))
            await session.flush()

            adjustment_a = CommercialAdjustment(
                contract_id=contract_a.id,
                contract_line_id=line_a.id,
                type=CommercialAdjustmentType.fixed_discount,
                value=1_000,
                starts_at=now,
                stack_group="rls",
                stackable=False,
                portability=CommercialPortability.preserve,
                reason="PostgreSQL RLS fixture.",
                source=CommercialAdjustmentSource.manual,
                status=CommercialRecordStatus.active,
                version=1,
                created_by_user_id=actor.id,
            )
            adjustment_b = CommercialAdjustment(
                contract_id=contract_b.id,
                contract_line_id=line_b.id,
                type=CommercialAdjustmentType.fixed_discount,
                value=2_000,
                starts_at=now,
                stack_group="rls",
                stackable=False,
                portability=CommercialPortability.expire_on_offer_change,
                reason="PostgreSQL RLS fixture.",
                source=CommercialAdjustmentSource.manual,
                status=CommercialRecordStatus.active,
                version=1,
                created_by_user_id=actor.id,
            )
            bonus_a = EntitlementBonus(
                workspace_id=tenant_rows.workspace_a,
                entitlement=EntitlementKey.users,
                mode=EntitlementBonusMode.quantity_delta,
                quantity=1,
                starts_at=now,
                renews_each_period=False,
                portability=CommercialPortability.expire_on_offer_change,
                reason="PostgreSQL RLS fixture.",
                source=EntitlementBonusSource.manual,
                status=CommercialRecordStatus.active,
                version=1,
                created_by_user_id=actor.id,
            )
            bonus_b = EntitlementBonus(
                workspace_id=tenant_rows.workspace_b,
                entitlement=EntitlementKey.users,
                mode=EntitlementBonusMode.quantity_delta,
                quantity=1,
                starts_at=now,
                renews_each_period=False,
                portability=CommercialPortability.preserve,
                reason="PostgreSQL RLS fixture.",
                source=EntitlementBonusSource.manual,
                status=CommercialRecordStatus.active,
                version=1,
                created_by_user_id=actor.id,
            )
            override_a = WorkspaceEntitlementOverride(
                workspace_id=tenant_rows.workspace_a,
                entitlement=EntitlementKey.tokens,
                mode=OverrideMode.limit,
                quantity=111_111,
                reason="PostgreSQL RLS workspace A override fixture.",
                created_by_user_id=actor.id,
            )
            override_b = WorkspaceEntitlementOverride(
                workspace_id=tenant_rows.workspace_b,
                entitlement=EntitlementKey.tokens,
                mode=OverrideMode.limit,
                quantity=222_222,
                reason="PostgreSQL RLS workspace B override fixture.",
                created_by_user_id=actor.id,
            )
            month_start = now.date().replace(day=1)
            usage_a = WorkspaceMonthlyUsage(
                workspace_id=tenant_rows.workspace_a,
                entitlement=EntitlementKey.tokens,
                period_start=month_start,
                consumed_quantity=11,
                reserved_quantity=1,
                limit_quantity=111_111,
                hard_limit=True,
                limit_source="rls_test",
            )
            usage_b = WorkspaceMonthlyUsage(
                workspace_id=tenant_rows.workspace_b,
                entitlement=EntitlementKey.tokens,
                period_start=month_start,
                consumed_quantity=22,
                reserved_quantity=2,
                limit_quantity=222_222,
                hard_limit=True,
                limit_source="rls_test",
            )
            event = CommercialCatalogEvent(
                event_type="rls_fixture",
                actor_user_id=actor.id,
                workspace_id=tenant_rows.workspace_a,
                resource_type="workspace_contract",
                resource_id=contract_a.id,
                before=None,
                after={"version": 1},
                reason="PostgreSQL RLS fixture.",
                correlation_id=f"rls-event-{token}",
            )
            event_b = CommercialCatalogEvent(
                event_type="rls_fixture",
                actor_user_id=actor.id,
                workspace_id=tenant_rows.workspace_b,
                resource_type="workspace_contract",
                resource_id=contract_b.id,
                before=None,
                after={"version": 1},
                reason="PostgreSQL RLS cross-workspace fixture.",
                correlation_id=f"rls-event-b-{token}",
            )
            receipt = CommercialCommandReceipt(
                operation="rls_fixture",
                scope_key=token,
                idempotency_key=f"rls-receipt-{token}",
                request_hash="c" * 64,
                response_payload={"ok": True},
                actor_user_id=actor.id,
            )
            session.add_all(
                (
                    adjustment_a,
                    adjustment_b,
                    bonus_a,
                    bonus_b,
                    override_a,
                    override_b,
                    usage_a,
                    usage_b,
                    event,
                    event_b,
                    receipt,
                )
            )
            await session.commit()

            seeded_offer_ids = [public_offer.id, private_offer.id]
            seeded_version_ids = [public_version.id, private_version.id]
            seeded_contract_ids = [contract_a.id, contract_b.id]
            seeded_line_ids = [line_a.id, line_b.id]
            seeded_adjustment_ids = [adjustment_a.id, adjustment_b.id]
            seeded_bonus_ids = [bonus_a.id, bonus_b.id]
            seeded_event_ids = [event.id, event_b.id]
            seeded_receipt_ids = [receipt.id]

        async with postgres_engines.app_sessions() as session:
            await set_workspace_context(session, tenant_rows.workspace_a)
            assert set((await session.scalars(select(WorkspaceCommercialContract.id))).all()) == {
                contract_a.id
            }
            assert set((await session.scalars(select(WorkspaceContractLine.id))).all()) == {
                line_a.id
            }
            assert set((await session.scalars(select(CommercialAdjustment.id))).all()) == {
                adjustment_a.id
            }
            assert set((await session.scalars(select(EntitlementBonus.id))).all()) == {
                bonus_a.id
            }
            assert set((await session.scalars(select(CommercialOffer.id))).all()) == {
                public_offer.id,
                private_offer.id,
            }
            assert set((await session.scalars(select(CommercialOfferVersion.id))).all()) == {
                public_version.id,
                private_version.id,
            }
            # This is the same join needed by resolve_effective_manifest; a
            # private offer becomes visible only because this workspace has
            # the matching contracted line.
            own_lines = (
                await session.execute(
                    select(WorkspaceContractLine.id, CommercialOffer.sku)
                    .join(CommercialOffer, CommercialOffer.id == WorkspaceContractLine.offer_id)
                    .where(WorkspaceContractLine.contract_id == contract_a.id)
                )
            ).all()
            assert own_lines == [(line_a.id, private_offer.sku)]
            assert (
                await session.scalar(select(func.count()).select_from(CommercialCatalogEvent))
                == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(CommercialCommandReceipt))
                == 0
            )

            tenant_update = await session.execute(
                update(WorkspaceCommercialContract)
                .where(WorkspaceCommercialContract.id == contract_a.id)
                .values(version=99)
            )
            assert tenant_update.rowcount == 0
            with pytest.raises(DBAPIError):
                await session.execute(
                    delete(CommercialAdjustment).where(
                        CommercialAdjustment.id == adjustment_a.id
                    )
                )
            await session.rollback()

            await set_workspace_context(session, tenant_rows.workspace_a)
            session.add(
                EntitlementBonus(
                    workspace_id=tenant_rows.workspace_a,
                    entitlement=EntitlementKey.users,
                    mode=EntitlementBonusMode.quantity_delta,
                    quantity=99,
                    starts_at=now,
                    renews_each_period=False,
                    portability=CommercialPortability.preserve,
                    reason="Tenant must not insert commercial state.",
                    source=EntitlementBonusSource.manual,
                    status=CommercialRecordStatus.active,
                    version=1,
                    created_by_user_id=actor.id,
                )
            )
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await set_workspace_context(session, tenant_rows.workspace_b)
            assert set((await session.scalars(select(CommercialOffer.id))).all()) == {
                public_offer.id
            }
            assert set((await session.scalars(select(CommercialOfferVersion.id))).all()) == {
                public_version.id
            }
            assert set((await session.scalars(select(WorkspaceCommercialContract.id))).all()) == {
                contract_b.id
            }

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="plans",
                action="view",
            )
            assert set((await session.scalars(select(CommercialOffer.id))).all()) >= {
                public_offer.id,
                private_offer.id,
            }
            assert set(
                (await session.scalars(select(CommercialCatalogEvent.id))).all()
            ) >= set(seeded_event_ids)
            assert (
                await session.scalar(
                    select(func.count()).select_from(CommercialCommandReceipt)
                )
                == 0
            )
            session.add(
                CommercialCatalogEvent(
                    event_type="rls_view_forbidden",
                    actor_user_id=actor.id,
                    workspace_id=None,
                    resource_type="commercial_offer",
                    resource_id=public_offer.id,
                    before=None,
                    after={"visible": True},
                    reason="Read capability must not write the ledger.",
                    correlation_id=f"rls-view-forbidden-{token}",
                )
            )
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            try:
                denied_delete = await session.execute(
                    delete(CommercialAdjustment).where(
                        CommercialAdjustment.id == adjustment_a.id
                    )
                )
                assert denied_delete.rowcount == 0
                await session.commit()
            except DBAPIError:
                await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="plans",
                action="preview",
            )
            assert set(
                (await session.scalars(select(CommercialCatalogEvent.id))).all()
            ) >= set(seeded_event_ids)
            assert (
                await session.scalar(
                    select(func.count()).select_from(CommercialCommandReceipt)
                )
                == 0
            )
            session.add(
                CommercialOffer(
                    sku=f"rls_preview_forbidden_{token}",
                    family=CommercialOfferFamily.crm,
                    kind=CommercialOfferKind.base_plan,
                    created_by_user_id=actor.id,
                )
            )
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()

        for plan_action in ("draft", "publish", "restore"):
            async with postgres_engines.app_sessions() as session:
                await set_platform_control_context(
                    session,
                    actor_user_id=actor.id,
                    module="plans",
                    action=plan_action,
                )
                assert set(
                    (await session.scalars(select(CommercialCatalogEvent.id))).all()
                ) >= set(seeded_event_ids)
                assert (
                    await session.scalar(
                        select(func.count()).select_from(CommercialCommandReceipt)
                    )
                    >= 1
                )
                action_event = CommercialCatalogEvent(
                    event_type=f"rls_{plan_action}",
                    actor_user_id=actor.id,
                    workspace_id=None,
                    resource_type="commercial_offer",
                    resource_id=public_offer.id,
                    before=None,
                    after={"action": plan_action},
                    reason=f"Exercise plans:{plan_action} PostgreSQL policy.",
                    correlation_id=f"rls-{plan_action}-{token}",
                )
                action_receipt = CommercialCommandReceipt(
                    operation=f"rls_{plan_action}",
                    scope_key=token,
                    idempotency_key=f"rls-{plan_action}-{token}",
                    request_hash=(plan_action[0] * 64),
                    response_payload={"ok": True, "action": plan_action},
                    actor_user_id=actor.id,
                )
                session.add_all((action_event, action_receipt))
                await session.commit()
                seeded_event_ids.append(action_event.id)
                seeded_receipt_ids.append(action_receipt.id)

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="plans",
                action="manage",
            )
            assert set((await session.scalars(select(CommercialOffer.id))).all()) >= {
                public_offer.id,
                private_offer.id,
            }
            assert set((await session.scalars(select(CommercialOfferVersion.id))).all()) >= {
                public_version.id,
                private_version.id,
            }

            # A platform capability does not bypass generic tenant RLS.  The
            # single workspace must be bound explicitly before the manifest
            # can read overrides and monthly consumption.
            assert (
                await session.scalar(
                    select(func.count()).select_from(WorkspaceEntitlementOverride)
                )
                == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(WorkspaceMonthlyUsage))
                == 0
            )
            await set_workspace_context(session, tenant_rows.workspace_a)
            assert set(
                (await session.scalars(select(WorkspaceEntitlementOverride.workspace_id))).all()
            ) == {tenant_rows.workspace_a}
            assert set(
                (await session.scalars(select(WorkspaceMonthlyUsage.workspace_id))).all()
            ) == {tenant_rows.workspace_a}
            with pytest.raises(WorkspaceContextError):
                await set_workspace_context(session, tenant_rows.workspace_b)

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=sdr_actor.id,
                module="sales",
                action="view",
            )
            assert await session.scalar(select(func.count()).select_from(CommercialOffer)) == 0
            assert (
                await session.scalar(select(func.count()).select_from(CommercialOfferVersion))
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(CommercialCatalogEvent)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(CommercialCommandReceipt)
                )
                == 0
            )

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="commercial_contracts",
                action="manage",
            )
            contract_update = await session.execute(
                update(WorkspaceCommercialContract)
                .where(WorkspaceCommercialContract.id == contract_a.id)
                .values(version=2)
            )
            assert contract_update.rowcount == 1
            await session.commit()

        # The same canonical aggregate is legitimately created or revised by
        # three independently authorized workflows.  Their command services
        # revalidate the originating RBAC capability before setting this
        # database context; PostgreSQL must then permit the aggregate, receipt
        # and immutable event in one transaction.  Discount and bonus records
        # remain outside those broad workflow capabilities.
        for workflow_index, workflow_module in enumerate(
            ("sales", "customers", "finance"),
            start=1,
        ):
            async with postgres_engines.app_sessions() as session:
                await set_platform_control_context(
                    session,
                    actor_user_id=actor.id,
                    module=workflow_module,
                    action="manage",
                )
                assert set((await session.scalars(select(CommercialOffer.id))).all()) >= {
                    public_offer.id,
                    private_offer.id,
                }
                assert set(
                    (await session.scalars(select(CommercialOfferVersion.id))).all()
                ) >= {
                    public_version.id,
                    private_version.id,
                }
                contract_update = await session.execute(
                    update(WorkspaceCommercialContract)
                    .where(WorkspaceCommercialContract.id == contract_a.id)
                    .values(version=10 + workflow_index)
                )
                line_update = await session.execute(
                    update(WorkspaceContractLine)
                    .where(WorkspaceContractLine.id == line_a.id)
                    .values(version=10 + workflow_index)
                )
                assert contract_update.rowcount == 1
                assert line_update.rowcount == 1

                workflow_event = CommercialCatalogEvent(
                    event_type=f"rls_{workflow_module}_manage",
                    actor_user_id=actor.id,
                    workspace_id=tenant_rows.workspace_a,
                    resource_type="workspace_contract",
                    resource_id=contract_a.id,
                    before={"version": 9 + workflow_index},
                    after={"version": 10 + workflow_index},
                    reason=f"Exercise {workflow_module}:manage commercial RLS.",
                    correlation_id=f"rls-{workflow_module}-manage-{token}",
                )
                workflow_receipt = CommercialCommandReceipt(
                    operation=f"rls_{workflow_module}_manage",
                    scope_key=token,
                    idempotency_key=f"rls-{workflow_module}-manage-{token}",
                    request_hash=(str(workflow_index) * 64),
                    response_payload={"ok": True, "module": workflow_module},
                    actor_user_id=actor.id,
                )
                session.add_all((workflow_event, workflow_receipt))
                await session.commit()
                seeded_event_ids.append(workflow_event.id)
                seeded_receipt_ids.append(workflow_receipt.id)

                # Workflow capabilities may resolve published/contracted
                # offers, but they cannot mutate the catalog itself.  Either
                # a zero-row RLS denial or a database denial is acceptable;
                # neither path may persist an offer/version edit.
                try:
                    denied_offer = await session.execute(
                        update(CommercialOffer)
                        .where(CommercialOffer.id == public_offer.id)
                        .values(legacy_tier=f"forbidden-{workflow_module}")
                    )
                    denied_version = await session.execute(
                        update(CommercialOfferVersion)
                        .where(CommercialOfferVersion.id == public_version.id)
                        .values(name=f"Forbidden {workflow_module} edit")
                    )
                    assert denied_offer.rowcount == 0
                    assert denied_version.rowcount == 0
                    await session.commit()
                except DBAPIError:
                    await session.rollback()

            # A workflow can revise the aggregate but cannot directly mutate
            # an existing discount or bonus.  Keep each denial in its own
            # transaction so one PostgreSQL RLS error cannot mask the other.
            for model, resource_id, values in (
                (
                    CommercialAdjustment,
                    adjustment_a.id,
                    {"value": 8_001, "version": 81},
                ),
                (
                    EntitlementBonus,
                    bonus_a.id,
                    {"quantity": 81, "version": 81},
                ),
            ):
                async with postgres_engines.app_sessions() as session:
                    await set_platform_control_context(
                        session,
                        actor_user_id=actor.id,
                        module=workflow_module,
                        action="manage",
                    )
                    try:
                        denied = await session.execute(
                            update(model).where(model.id == resource_id).values(**values)
                        )
                        assert denied.rowcount == 0
                        await session.commit()
                    except DBAPIError:
                        await session.rollback()

        # Create a second line in the same contract so the internal adjustment
        # path can prove a legitimate FK-bound rebind without weakening the
        # guard to another contract or arbitrary economic fields.
        async with admin_sessions() as session:
            rebind_line_a = WorkspaceContractLine(
                contract_id=contract_a.id,
                offer_id=public_offer.id,
                policy_version_id=public_version.id,
                offer_family=CommercialOfferFamily.crm,
                quantity=1,
                list_price_cents=26_900,
                contracted_price_cents=26_900,
                billing_interval=CommercialBillingInterval.monthly,
                entitlement_overrides={},
                effective_from=now,
                status=CommercialContractLineStatus.active,
                version=1,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
            )
            rebind_line_b = WorkspaceContractLine(
                contract_id=contract_b.id,
                offer_id=public_offer.id,
                policy_version_id=public_version.id,
                offer_family=CommercialOfferFamily.crm,
                quantity=1,
                list_price_cents=26_900,
                contracted_price_cents=26_900,
                billing_interval=CommercialBillingInterval.monthly,
                entitlement_overrides={},
                effective_from=now,
                status=CommercialContractLineStatus.cancelled,
                version=1,
                created_by_user_id=actor.id,
                updated_by_user_id=actor.id,
            )
            session.add_all((rebind_line_a, rebind_line_b))
            await session.commit()
            seeded_line_ids.extend((rebind_line_a.id, rebind_line_b.id))

        # Ordinary revision cannot rebind an expire-on-change adjustment.
        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(CommercialAdjustment)
                    .where(CommercialAdjustment.id == adjustment_b.id)
                    .values(contract_line_id=rebind_line_b.id, version=2)
                )
            await session.rollback()

        # Explicit restoration has a separate, adjustment-only context that
        # may preserve any declared portability by changing only its line.
        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_restore_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            restored_expiring_adjustment = await session.execute(
                update(CommercialAdjustment)
                .where(CommercialAdjustment.id == adjustment_b.id)
                .values(contract_line_id=rebind_line_b.id, version=2)
            )
            assert restored_expiring_adjustment.rowcount == 1
            await session.commit()

        # Contract revision may only rebind/revoke portability through this
        # bounded, non-public database context installed by the command bus.
        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            portability_update = await session.execute(
                update(CommercialAdjustment)
                .where(CommercialAdjustment.id == adjustment_a.id)
                .values(contract_line_id=rebind_line_a.id, version=2)
            )
            adjustment_revoke = await session.execute(
                update(CommercialAdjustment)
                .where(CommercialAdjustment.id == adjustment_b.id)
                .values(status=CommercialRecordStatus.revoked, version=3)
            )
            bonus_portability_update = await session.execute(
                update(EntitlementBonus)
                .where(EntitlementBonus.id == bonus_a.id)
                .values(status=CommercialRecordStatus.revoked, version=2)
            )
            assert portability_update.rowcount == 1
            assert adjustment_revoke.rowcount == 1
            assert bonus_portability_update.rowcount == 1
            await session.commit()

        # Exercise the same expire-on-offer-change transition from scheduled
        # state. The master session deliberately resets only these fixtures;
        # the reserved RLS context must still perform the second revocation.
        async with admin_sessions() as session:
            scheduled_adjustment = await session.get(
                CommercialAdjustment,
                adjustment_b.id,
            )
            scheduled_bonus = await session.get(EntitlementBonus, bonus_a.id)
            assert scheduled_adjustment is not None
            assert scheduled_bonus is not None
            scheduled_adjustment.status = CommercialRecordStatus.scheduled
            scheduled_adjustment.version = 4
            scheduled_bonus.status = CommercialRecordStatus.scheduled
            scheduled_bonus.version = 3
            await session.commit()

        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(CommercialAdjustment)
                    .where(CommercialAdjustment.id == adjustment_b.id)
                    .values(contract_line_id=line_b.id, version=5)
                )
            await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_restore_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            scheduled_restore_rebind = await session.execute(
                update(CommercialAdjustment)
                .where(CommercialAdjustment.id == adjustment_b.id)
                .values(contract_line_id=line_b.id, version=5)
            )
            assert scheduled_restore_rebind.rowcount == 1
            await session.commit()

        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            scheduled_adjustment_revoke = await session.execute(
                update(CommercialAdjustment)
                .where(CommercialAdjustment.id == adjustment_b.id)
                .values(status=CommercialRecordStatus.revoked, version=6)
            )
            scheduled_bonus_revoke = await session.execute(
                update(EntitlementBonus)
                .where(EntitlementBonus.id == bonus_a.id)
                .values(status=CommercialRecordStatus.revoked, version=4)
            )
            assert scheduled_adjustment_revoke.rowcount == 1
            assert scheduled_bonus_revoke.rowcount == 1
            await session.commit()

        # Return the eligible expire-on-change bonus to an active fixture so
        # every frozen-column denial below reaches the guard's field checks.
        async with admin_sessions() as session:
            guarded_bonus = await session.get(EntitlementBonus, bonus_a.id)
            assert guarded_bonus is not None
            guarded_bonus.status = CommercialRecordStatus.active
            guarded_bonus.version = 5
            await session.commit()

        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            adjustment_state = await session.get(
                CommercialAdjustment,
                adjustment_a.id,
            )
            bonus_state = await session.get(EntitlementBonus, bonus_a.id)
            assert adjustment_state is not None
            assert bonus_state is not None
            adjustment_updated_at = adjustment_state.updated_at
            adjustment_created_at = adjustment_state.created_at
            bonus_updated_at = bonus_state.updated_at
            bonus_created_at = bonus_state.created_at

        adjustment_forbidden_updates = (
            ("id", {"id": uuid4()}),
            (
                "contract",
                {
                    "contract_id": contract_b.id,
                    "contract_line_id": line_b.id,
                },
            ),
            ("type", {"type": CommercialAdjustmentType.percentage_discount}),
            ("value", {"value": 8_001}),
            ("starts_at", {"starts_at": now - timedelta(days=1)}),
            ("ends_at", {"ends_at": now + timedelta(days=1)}),
            ("remaining_cycles", {"remaining_cycles": 2}),
            ("stack_group", {"stack_group": "forbidden-change"}),
            ("stackable", {"stackable": True}),
            ("portability", {"portability": CommercialPortability.manual_review}),
            ("reason", {"reason": "Forbidden internal reason mutation."}),
            ("source", {"source": CommercialAdjustmentSource.benefit}),
            ("created_by_user_id", {"created_by_user_id": sdr_actor.id}),
            (
                "created_at",
                {"created_at": adjustment_created_at - timedelta(days=1)},
            ),
        )
        bonus_forbidden_updates = (
            ("id", {"id": uuid4()}),
            ("workspace_id", {"workspace_id": tenant_rows.workspace_b}),
            ("entitlement", {"entitlement": EntitlementKey.agents}),
            (
                "mode",
                {"mode": EntitlementBonusMode.feature_grant, "quantity": None},
            ),
            ("quantity", {"quantity": 81}),
            ("starts_at", {"starts_at": now - timedelta(days=1)}),
            ("ends_at", {"ends_at": now + timedelta(days=1)}),
            ("renews_each_period", {"renews_each_period": True}),
            ("portability", {"portability": CommercialPortability.manual_review}),
            ("reason", {"reason": "Forbidden internal bonus reason mutation."}),
            ("source", {"source": EntitlementBonusSource.campaign}),
            ("created_by_user_id", {"created_by_user_id": sdr_actor.id}),
            ("created_at", {"created_at": bonus_created_at - timedelta(days=1)}),
        )

        # Restore rebind is broader only in accepted portability values, not
        # in writable columns or status transitions.
        for restore_forbidden in (
            {"value": 8_002},
            {"status": CommercialRecordStatus.revoked},
        ):
            async with postgres_engines.app_sessions() as session:
                await _set_commercial_portability_restore_rebind_context(
                    session,
                    actor_user_id=actor.id,
                    is_platform_root=False,
                )
                with pytest.raises(DBAPIError):
                    await session.execute(
                        update(CommercialAdjustment)
                        .where(CommercialAdjustment.id == adjustment_a.id)
                        .values(
                            contract_line_id=line_a.id,
                            version=3,
                            **restore_forbidden,
                        )
                    )
                await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_restore_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            try:
                denied_restore_bonus = await session.execute(
                    update(EntitlementBonus)
                    .where(EntitlementBonus.id == bonus_b.id)
                    .values(status=CommercialRecordStatus.revoked, version=2)
                )
                assert denied_restore_bonus.rowcount == 0
                await session.commit()
            except DBAPIError:
                await session.rollback()

        guarded_negative_updates = [
            # Preserve permits a same-status line rebind, never revocation.
            (
                "adjustment_preserve_revoke",
                CommercialAdjustment,
                adjustment_a.id,
                {"status": CommercialRecordStatus.revoked, "version": 3},
            ),
            *(
                (
                    f"adjustment_frozen_{field_name}",
                    CommercialAdjustment,
                    adjustment_a.id,
                    {
                        "contract_line_id": line_a.id,
                        "version": 3,
                        **forbidden,
                    },
                )
                for field_name, forbidden in adjustment_forbidden_updates
            ),
            (
                "adjustment_version_jump",
                CommercialAdjustment,
                adjustment_a.id,
                {"contract_line_id": line_a.id, "version": 4},
            ),
            (
                "adjustment_non_advancing_timestamp",
                CommercialAdjustment,
                adjustment_a.id,
                {
                    "contract_line_id": line_a.id,
                    "version": 3,
                    "updated_at": adjustment_updated_at,
                },
            ),
            (
                "adjustment_forbidden_expiry",
                CommercialAdjustment,
                adjustment_a.id,
                {"status": CommercialRecordStatus.expired, "version": 3},
            ),
            *(
                (
                    f"bonus_frozen_{field_name}",
                    EntitlementBonus,
                    bonus_a.id,
                    {
                        "status": CommercialRecordStatus.revoked,
                        "version": 6,
                        **forbidden,
                    },
                )
                for field_name, forbidden in bonus_forbidden_updates
            ),
            (
                "bonus_version_jump",
                EntitlementBonus,
                bonus_a.id,
                {"status": CommercialRecordStatus.revoked, "version": 7},
            ),
            (
                "bonus_non_advancing_timestamp",
                EntitlementBonus,
                bonus_a.id,
                {
                    "status": CommercialRecordStatus.revoked,
                    "version": 6,
                    "updated_at": bonus_updated_at,
                },
            ),
            (
                "bonus_forbidden_expiry",
                EntitlementBonus,
                bonus_a.id,
                {"status": CommercialRecordStatus.expired, "version": 6},
            ),
            # A preserve bonus must not enter the automatic revoke path.
            (
                "bonus_preserve_revoke",
                EntitlementBonus,
                bonus_b.id,
                {"status": CommercialRecordStatus.revoked, "version": 2},
            ),
        ]

        # Every economic, identity, window and attribution field is frozen in
        # the internal scope.  One failed statement must not mask the next.
        for label, model, resource_id, values in guarded_negative_updates:
            async with postgres_engines.app_sessions() as session:
                await _set_commercial_portability_rebind_context(
                    session,
                    actor_user_id=actor.id,
                    is_platform_root=False,
                )
                try:
                    await session.execute(
                        update(model).where(model.id == resource_id).values(**values)
                    )
                except DBAPIError:
                    await session.rollback()
                else:
                    await session.rollback()
                    pytest.fail(
                        "Guarded commercial update unexpectedly succeeded: "
                        f"{label}"
                    )

        # The internal portability context is UPDATE-only: it cannot create
        # a new commercial adjustment or capacity bonus.
        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            session.add(
                CommercialAdjustment(
                    contract_id=contract_a.id,
                    contract_line_id=line_a.id,
                    type=CommercialAdjustmentType.fixed_discount,
                    value=123,
                    starts_at=now,
                    stack_group="forbidden-portability",
                    stackable=False,
                    portability=CommercialPortability.preserve,
                    reason="This insert must be rejected by RLS.",
                    source=CommercialAdjustmentSource.manual,
                    status=CommercialRecordStatus.active,
                    version=1,
                    created_by_user_id=actor.id,
                )
            )
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await _set_commercial_portability_rebind_context(
                session,
                actor_user_id=actor.id,
                is_platform_root=False,
            )
            session.add(
                EntitlementBonus(
                    workspace_id=tenant_rows.workspace_a,
                    entitlement=EntitlementKey.users,
                    mode=EntitlementBonusMode.quantity_delta,
                    quantity=1,
                    starts_at=now,
                    renews_each_period=False,
                    portability=CommercialPortability.preserve,
                    reason="This insert must be rejected by RLS.",
                    source=EntitlementBonusSource.manual,
                    status=CommercialRecordStatus.active,
                    version=1,
                    created_by_user_id=actor.id,
                )
            )
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="commercial_contracts",
                action="manage",
            )
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(CommercialOffer)
                    .where(CommercialOffer.id == public_offer.id)
                    .values(legacy_tier="forbidden")
                )
            await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="commercial_contracts",
                action="manage",
            )
            with pytest.raises(DBAPIError):
                await session.execute(
                    delete(EntitlementBonus).where(EntitlementBonus.id == bonus_a.id)
                )
            await session.rollback()

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="commercial_adjustments",
                action="view",
            )
            assert await session.get(CommercialAdjustment, adjustment_a.id) is not None
            denied = await session.execute(
                update(CommercialAdjustment)
                .where(CommercialAdjustment.id == adjustment_a.id)
                .values(value=9_999)
            )
            assert denied.rowcount == 0

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="commercial_adjustments",
                action="manage",
            )
            allowed = await session.execute(
                update(CommercialAdjustment)
                .where(CommercialAdjustment.id == adjustment_a.id)
                .values(value=1_750, version=3)
            )
            assert allowed.rowcount == 1
            assert (
                await session.scalar(select(func.count()).select_from(CommercialCatalogEvent))
                >= 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(CommercialCommandReceipt))
                >= 1
            )
            await session.commit()

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="entitlement_bonuses",
                action="manage",
            )
            allowed = await session.execute(
                update(EntitlementBonus)
                .where(EntitlementBonus.id == bonus_b.id)
                .values(quantity=3, version=2)
            )
            assert allowed.rowcount == 1
            await session.commit()

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="platform_root",
                action="manage",
                is_platform_root=True,
            )
            assert set((await session.scalars(select(CommercialOffer.id))).all()) >= {
                public_offer.id,
                private_offer.id,
            }
            assert set((await session.scalars(select(CommercialOfferVersion.id))).all()) >= {
                public_version.id,
                private_version.id,
            }
            assert set(
                (await session.scalars(select(CommercialCatalogEvent.id))).all()
            ) >= set(seeded_event_ids)
            assert set(
                (await session.scalars(select(CommercialCommandReceipt.id))).all()
            ) >= set(seeded_receipt_ids)
            root_offer = CommercialOffer(
                sku=f"rls_root_{token}",
                family=CommercialOfferFamily.crm,
                kind=CommercialOfferKind.base_plan,
                created_by_user_id=actor.id,
            )
            root_adjustment_update = await session.execute(
                update(CommercialAdjustment)
                .where(CommercialAdjustment.id == adjustment_a.id)
                .values(value=1_800, version=4)
            )
            root_bonus_update = await session.execute(
                update(EntitlementBonus)
                .where(EntitlementBonus.id == bonus_b.id)
                .values(quantity=4, version=3)
            )
            assert root_adjustment_update.rowcount == 1
            assert root_bonus_update.rowcount == 1
            session.add(root_offer)
            await session.commit()
            root_created_offer_id = root_offer.id

        async with postgres_engines.app_sessions() as session:
            await set_platform_control_context(
                session,
                actor_user_id=actor.id,
                module="platform_root",
                action="manage",
                is_platform_root=True,
            )
            session.add(
                WorkspaceContractLine(
                    contract_id=contract_b.id,
                    offer_id=public_offer.id,
                    policy_version_id=public_version.id,
                    # The offer is CRM; this forged Legal family must be
                    # rejected by fk_contract_line_offer_family.
                    offer_family=CommercialOfferFamily.legal,
                    quantity=1,
                    list_price_cents=26_900,
                    contracted_price_cents=26_900,
                    billing_interval=CommercialBillingInterval.monthly,
                    entitlement_overrides={},
                    effective_from=now,
                    status=CommercialContractLineStatus.active,
                    version=1,
                    created_by_user_id=actor.id,
                    updated_by_user_id=actor.id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
    finally:
        async with admin_sessions() as session:
            for table_name in (
                "commercial_catalog_events",
                "commercial_command_receipts",
                "commercial_offer_versions",
                "commercial_offers",
            ):
                await session.execute(text(f'ALTER TABLE "{table_name}" DISABLE TRIGGER USER'))
            try:
                if seeded_event_ids:
                    await session.execute(
                        delete(CommercialCatalogEvent).where(
                            CommercialCatalogEvent.id.in_(seeded_event_ids)
                        )
                    )
                if seeded_receipt_ids:
                    await session.execute(
                        delete(CommercialCommandReceipt).where(
                            CommercialCommandReceipt.id.in_(seeded_receipt_ids)
                        )
                    )
                if seeded_adjustment_ids:
                    await session.execute(
                        delete(CommercialAdjustment).where(
                            CommercialAdjustment.id.in_(seeded_adjustment_ids)
                        )
                    )
                if seeded_bonus_ids:
                    await session.execute(
                        delete(EntitlementBonus).where(EntitlementBonus.id.in_(seeded_bonus_ids))
                    )
                if seeded_line_ids:
                    await session.execute(
                        delete(WorkspaceContractLine).where(
                            WorkspaceContractLine.id.in_(seeded_line_ids)
                        )
                    )
                if seeded_contract_ids:
                    await session.execute(
                        delete(WorkspaceCommercialContract).where(
                            WorkspaceCommercialContract.id.in_(seeded_contract_ids)
                        )
                    )
                if seeded_version_ids:
                    await session.execute(
                        delete(CommercialOfferVersion).where(
                            CommercialOfferVersion.id.in_(seeded_version_ids)
                        )
                    )
                offer_ids = seeded_offer_ids + (
                    [root_created_offer_id] if root_created_offer_id is not None else []
                )
                if offer_ids:
                    await session.execute(
                        delete(CommercialOffer).where(CommercialOffer.id.in_(offer_ids))
                    )
                await session.execute(
                    delete(User).where(User.id.in_((actor.id, sdr_actor.id)))
                )
                await session.commit()
            finally:
                for table_name in (
                    "commercial_catalog_events",
                    "commercial_command_receipts",
                    "commercial_offer_versions",
                    "commercial_offers",
                ):
                    await session.execute(text(f'ALTER TABLE "{table_name}" ENABLE TRIGGER USER'))
                await session.commit()


@pytest.mark.asyncio
async def test_platform_support_context_reads_all_tickets_without_bypassing_other_rls(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
) -> None:
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    token = uuid4().hex[:12]
    now = datetime.now(UTC)
    reporter = User(
        id=uuid4(),
        name=f"Support RLS Reporter {token}",
        email=f"support-rls-{token}@example.test",
        agent_type=AgentType.human,
        role=UserRole.lawyer,
        initials="SR",
        color="#164e63",
        is_active=True,
        email_verified_at=now,
    )

    def ticket(workspace_id: UUID, suffix: str) -> SupportTicket:
        return SupportTicket(
            public_number=f"SAC-RLS-{token}-{suffix}",
            workspace_id=workspace_id,
            reporter_user_id=reporter.id,
            ticket_type=SupportTicketType.problem,
            priority=SupportTicketPriority.normal,
            severity=SupportTicketSeverity.medium,
            queue=SupportTicketQueue.support,
            status=SupportTicketStatus.new,
            subject=f"Support RLS ticket {suffix}",
            description="Ticket used to prove governed platform support RLS.",
            technical_context={},
            idempotency_key=f"support-rls-{token}-{suffix}",
            first_response_due_at=now + timedelta(hours=4),
            resolution_due_at=now + timedelta(days=3),
        )

    ticket_a = ticket(tenant_rows.workspace_a, "a")
    ticket_b = ticket(tenant_rows.workspace_b, "b")
    async with admin_sessions() as session:
        session.add(reporter)
        await session.flush()
        session.add_all((ticket_a, ticket_b))
        await session.commit()

    ticket_ids = (ticket_a.id, ticket_b.id)
    query = select(func.count()).select_from(SupportTicket).where(
        SupportTicket.id.in_(ticket_ids)
    )
    try:
        async with postgres_engines.app_sessions() as session:
            assert await session.scalar(query) == 0

        async with postgres_engines.app_sessions() as session:
            await session.execute(
                text(
                    "SELECT "
                    "set_config('app.current_platform_scope', 'support', true), "
                    "set_config('app.current_platform_actor_id', :actor, true), "
                    "set_config('app.current_platform_action', 'customers:manage', true)"
                )
                .bindparams(actor=str(reporter.id))
            )
            assert await session.scalar(query) == 0

        async with postgres_engines.app_sessions() as session:
            await set_workspace_context(session, tenant_rows.workspace_a)
            assert await session.scalar(query) == 1

        async with postgres_engines.app_sessions() as session:
            await set_platform_support_context(
                session,
                actor_user_id=reporter.id,
                action="view",
            )
            assert await session.scalar(query) == 2
            assert (
                await session.scalar(
                    select(func.count()).select_from(KlmAuditEvent)
                )
                == 0
            )

        async with postgres_engines.app_sessions() as session:
            assert await session.scalar(query) == 0
    finally:
        async with admin_sessions() as session:
            await session.execute(
                delete(SupportTicket).where(SupportTicket.id.in_(ticket_ids))
            )
            await session.execute(delete(User).where(User.id == reporter.id))
            await session.commit()


@pytest.mark.asyncio
async def test_playbook_tables_are_isolated_and_local_only_under_postgresql_rls(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
) -> None:
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    seeded: dict[UUID, tuple[ConfigDocument, ConfigDocumentVersion]] = {}
    async with admin_sessions() as session:
        for suffix, workspace_id in (
            ("a", tenant_rows.workspace_a),
            ("b", tenant_rows.workspace_b),
        ):
            document = ConfigDocument(
                workspace_id=workspace_id,
                namespace=f"sales.playbook.rls{suffix}{uuid4().hex}",
                scope=ConfigScope.workspace,
                scope_key=f"workspace:sales.playbook.rls{suffix}{uuid4().hex}",
                schema_version="1.0",
                current_version=1,
                etag=suffix * 64,
                payload={"rls": suffix},
            )
            session.add(document)
            await session.flush()
            version = ConfigDocumentVersion(
                document_id=document.id,
                version=1,
                schema_version="1.0",
                payload={"rls": suffix},
                payload_hash=suffix * 64,
                reason="RLS seed",
            )
            session.add(version)
            await session.flush()
            session.add_all(
                (
                    PlaybookVersionGovernance(
                        workspace_id=workspace_id,
                        document_id=document.id,
                        document_version_id=version.id,
                        version=1,
                        sequence=1,
                        state=PlaybookLifecycleState.draft,
                        reason="RLS seed",
                    ),
                    PlaybookScopeBinding(
                        workspace_id=workspace_id,
                        document_id=document.id,
                        document_version_id=version.id,
                        version=1,
                        dimension=PlaybookScopeDimension.area,
                        value="saude",
                    ),
                    PlaybookSimulationRun(
                        workspace_id=workspace_id,
                        document_id=document.id,
                        document_version_id=version.id,
                        version=1,
                        content_digest=suffix * 64,
                        status=PlaybookSimulationStatus.passed,
                        scenario="RLS",
                        customer_message="local",
                        context={},
                        response="local",
                        next_action="none",
                        policy_trace=[],
                        sources=[],
                        autonomy_mode="human_only",
                        human_approval_required=True,
                        external_actions_executed=0,
                        prompt_tokens=1,
                        completion_tokens=1,
                        estimated_cost_brl=0,
                    ),
                )
            )
            seeded[workspace_id] = (document, version)
        await session.commit()

    sessions = postgres_engines.app_sessions
    async with sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(PlaybookVersionGovernance))
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(PlaybookScopeBinding)) == 0
        assert await session.scalar(select(func.count()).select_from(PlaybookSimulationRun)) == 0

    async with sessions() as session:
        await set_workspace_context(session, tenant_rows.workspace_a)
        assert (
            await session.scalar(select(func.count()).select_from(PlaybookVersionGovernance))
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(PlaybookScopeBinding)) == 1
        assert await session.scalar(select(func.count()).select_from(PlaybookSimulationRun)) == 1
        foreign_document, foreign_version = seeded[tenant_rows.workspace_b]
        session.add(
            PlaybookScopeBinding(
                workspace_id=tenant_rows.workspace_b,
                document_id=foreign_document.id,
                document_version_id=foreign_version.id,
                version=2,
                dimension=PlaybookScopeDimension.channel,
                value="whatsapp",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        assert await session.scalar(select(func.count()).select_from(PlaybookSimulationRun)) == 1
        own_document, own_version = seeded[tenant_rows.workspace_a]
        session.add(
            PlaybookSimulationRun(
                workspace_id=tenant_rows.workspace_a,
                document_id=own_document.id,
                document_version_id=own_version.id,
                version=1,
                content_digest="a" * 64,
                status=PlaybookSimulationStatus.passed,
                scenario="constraint",
                customer_message="local",
                context={},
                response="local",
                next_action="none",
                policy_trace=[],
                sources=[],
                autonomy_mode="human_only",
                human_approval_required=True,
                external_actions_executed=1,
                prompt_tokens=1,
                completion_tokens=1,
                estimated_cost_brl=0,
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_security_definer_webhook_resolvers_only_bootstrap_bound_tenant(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
) -> None:
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    token = f"zapsign-{uuid4().hex}"
    public_api_raw_token = f"klm_rls_{uuid4().hex}"
    phone_number_id = f"pn-{uuid4().hex}"
    duplicate_phone_number_id = f"pn-{uuid4().hex}"
    waba_id = f"waba-{uuid4().hex}"
    async with admin_sessions() as session:
        client = Client(workspace_id=tenant_rows.workspace_a, name="Webhook RLS Client")
        session.add(client)
        await session.flush()
        matter = Matter(
            workspace_id=tenant_rows.workspace_a,
            client_id=client.id,
            practice_area=PracticeArea.saude_suplementar,
            demand_type="Webhook resolver",
            column_id=KanbanColumnId.novo_lead,
            zapsign_doc_token=token,
        )
        connection = WhatsAppConnection(
            workspace_id=tenant_rows.workspace_a,
            connection_type=ConnectionType.OFFICIAL_MANUAL,
            is_default=True,
            phone_number_id=phone_number_id,
            waba_id=waba_id,
            status=WaConnectionStatus.connected,
        )
        creator = User(
            name="Public API RLS Owner",
            email=f"public-api-rls-{uuid4().hex}@example.test",
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials="PA",
            color="#111827",
            is_active=True,
        )
        session.add_all((matter, connection, creator))
        await session.flush()
        membership = WorkspaceMembership(
            workspace_id=tenant_rows.workspace_a,
            user_id=creator.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
        )
        public_api_token = WorkspacePublicApiToken(
            workspace_id=tenant_rows.workspace_a,
            created_by_id=creator.id,
            label="PostgreSQL RLS token",
            token_prefix=public_api_raw_token[:18],
            token_hash=PublicApiService(session)._hash_token(public_api_raw_token),
            scopes=["contacts:read"],
            rate_limit_per_minute=120,
            is_active=True,
        )
        contact = WhatsAppContact(
            workspace_id=tenant_rows.workspace_a,
            whatsapp_connection_id=connection.id,
            phone="5511999990000",
            name="Contato RLS",
        )
        session.add_all((membership, public_api_token, contact))
        await session.commit()
        matter_id = matter.id
        creator_id = creator.id

    try:
        async with postgres_engines.app_sessions() as session:
            assert await session.get(Matter, matter_id) is None
            assert (
                await resolve_zapsign_webhook_workspace(
                    session,
                    matter_id=matter_id,
                    document_token="wrong-token",
                )
                is None
            )
            assert (
                await resolve_zapsign_webhook_workspace(
                    session,
                    matter_id=matter_id,
                    document_token=token,
                )
                == tenant_rows.workspace_a
            )
            assert (
                await resolve_whatsapp_webhook_workspace(
                    session,
                    phone_number_id=phone_number_id,
                )
                == tenant_rows.workspace_a
            )
            resolved_template_workspace = await session.scalar(
                text(
                    "SELECT public."
                    "klm_resolve_whatsapp_template_webhook_workspace(:waba_id)"
                ),
                {"waba_id": waba_id},
            )
            assert UUID(str(resolved_template_workspace)) == tenant_rows.workspace_a

            await set_workspace_context(session, tenant_rows.workspace_a)
            assert await session.get(Matter, matter_id) is not None

        async with admin_sessions() as session:
            session.add(
                WhatsAppConnection(
                    workspace_id=tenant_rows.workspace_b,
                    connection_type=ConnectionType.OFFICIAL_MANUAL,
                    is_default=True,
                    phone_number_id=duplicate_phone_number_id,
                    waba_id=waba_id,
                    status=WaConnectionStatus.connected,
                )
            )
            await session.commit()

        async with postgres_engines.app_sessions() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT public."
                        "klm_resolve_whatsapp_template_webhook_workspace(:waba_id)"
                    ),
                    {"waba_id": waba_id},
                )
                is None
            )

        async with postgres_engines.app_sessions() as session:
            public_api = PublicApiService(session)
            auth = await public_api.authenticate(
                raw_token=public_api_raw_token,
                required_scope="contacts:read",
            )
            assert auth.token.workspace_id == tenant_rows.workspace_a
            contacts = await public_api.list_contacts(tenant_rows.workspace_a)
            assert [item.phone for item in contacts] == ["5511999990000"]

        async with postgres_engines.app_sessions() as session:
            await WhatsAppService(session).process_webhook(
                {
                    "object": "whatsapp_business_account",
                    "entry": [
                        {
                            "changes": [
                                {
                                    "field": "messages",
                                    "value": {
                                        "metadata": {"phone_number_id": phone_number_id},
                                        "messages": [],
                                        "statuses": [],
                                    },
                                }
                            ]
                        }
                    ],
                }
            )
            assert await session.get(WhatsAppConnection, connection.id) is not None
    finally:
        async with admin_sessions() as session:
            await session.execute(
                delete(WorkspacePublicApiToken).where(
                    WorkspacePublicApiToken.workspace_id == tenant_rows.workspace_a
                )
            )
            await session.execute(
                delete(WhatsAppContact).where(
                    WhatsAppContact.workspace_id == tenant_rows.workspace_a
                )
            )
            await session.execute(delete(Matter).where(Matter.id == matter_id))
            await session.execute(
                delete(WhatsAppConnection).where(
                    WhatsAppConnection.waba_id == waba_id
                )
            )
            await session.execute(
                delete(WorkspaceMembership).where(WorkspaceMembership.user_id == creator_id)
            )
            await session.execute(delete(User).where(User.id == creator_id))
            await session.commit()


@pytest.mark.asyncio
async def test_identity_resolvers_only_return_a_credential_bound_workspace(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
) -> None:
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=30)
    signup_token_digest = uuid4().hex * 2
    invitation_token_digest = uuid4().hex * 2
    signup_delivery_id = uuid4()
    invitation_delivery_id = uuid4()
    verification_id = uuid4()
    invitation_id = uuid4()

    async with admin_sessions() as session:
        identity_user = User(
            name="Identity RLS User",
            email=f"identity-rls-{uuid4().hex}@example.test",
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials="IR",
            color="#111827",
            is_active=False,
        )
        session.add(identity_user)
        await session.flush()
        membership = WorkspaceMembership(
            workspace_id=tenant_rows.workspace_a,
            user_id=identity_user.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.invited,
            is_default=True,
        )
        session.add(membership)
        await session.flush()
        verification = SignupVerification(
            id=verification_id,
            user_id=identity_user.id,
            workspace_id=tenant_rows.workspace_a,
            token_digest=signup_token_digest,
            code_digest="a" * 64,
            plan_tier="basic",
            expires_at=expires_at,
        )
        invitation = WorkspaceInvitation(
            id=invitation_id,
            workspace_id=tenant_rows.workspace_a,
            membership_id=membership.id,
            user_id=identity_user.id,
            invited_by_user_id=identity_user.id,
            invited_email=str(identity_user.email),
            invited_name=identity_user.name,
            token_digest=invitation_token_digest,
            code_digest="b" * 64,
            status=InvitationStatus.pending,
            expires_at=expires_at,
            last_sent_at=now,
            send_count=1,
        )
        deliveries = (
            IdentityOutboxMessage(
                id=signup_delivery_id,
                purpose=IdentityOutboxPurpose.signup_verification,
                recipient_digest="c" * 64,
                recipient_hint="i***@example.test",
                payload_ciphertext="encrypted-signup",
                related_type="signup_verification",
                related_id=verification_id,
                expires_at=expires_at,
            ),
            IdentityOutboxMessage(
                id=invitation_delivery_id,
                purpose=IdentityOutboxPurpose.workspace_invitation,
                recipient_digest="d" * 64,
                recipient_hint="i***@example.test",
                payload_ciphertext="encrypted-invitation",
                related_type="workspace_invitation",
                related_id=invitation_id,
                expires_at=expires_at,
            ),
        )
        session.add_all((verification, invitation, *deliveries))
        await session.commit()
        identity_user_id = identity_user.id

    try:
        async with postgres_engines.app_sessions() as session:
            assert await session.scalar(
                select(func.count()).select_from(SignupVerification)
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceInvitation)
            ) == 0

            assert await resolve_signup_verification_workspace(
                session,
                token_digest="wrong-signup-token",
                delivery_id=None,
            ) is None
            assert await resolve_signup_verification_workspace(
                session,
                token_digest=signup_token_digest,
                delivery_id=None,
            ) == tenant_rows.workspace_a
            assert await resolve_signup_verification_workspace(
                session,
                token_digest=None,
                delivery_id=signup_delivery_id,
            ) == tenant_rows.workspace_a
            assert await resolve_signup_verification_workspace(
                session,
                token_digest=signup_token_digest,
                delivery_id=signup_delivery_id,
            ) is None

            assert await resolve_workspace_invitation_workspace(
                session,
                invitation_id=invitation_id,
                token_digest="wrong-invitation-token",
                delivery_id=None,
            ) is None
            assert await resolve_workspace_invitation_workspace(
                session,
                invitation_id=invitation_id,
                token_digest=invitation_token_digest,
                delivery_id=None,
            ) == tenant_rows.workspace_a
            assert await resolve_workspace_invitation_workspace(
                session,
                invitation_id=invitation_id,
                token_digest=None,
                delivery_id=invitation_delivery_id,
            ) == tenant_rows.workspace_a
            assert await resolve_workspace_invitation_workspace(
                session,
                invitation_id=uuid4(),
                token_digest=invitation_token_digest,
                delivery_id=None,
            ) is None

            await set_workspace_context(session, tenant_rows.workspace_a)
            assert await session.scalar(
                select(func.count()).select_from(SignupVerification)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceInvitation)
            ) == 1
    finally:
        async with admin_sessions() as session:
            await session.execute(
                delete(IdentityOutboxMessage).where(
                    IdentityOutboxMessage.id.in_(
                        (signup_delivery_id, invitation_delivery_id)
                    )
                )
            )
            await session.execute(delete(User).where(User.id == identity_user_id))
            await session.commit()


@pytest.mark.asyncio
async def test_runtime_role_can_create_and_resume_signup_under_forced_rls(
    postgres_engines: PostgresEngines,
) -> None:
    token = uuid4().hex[:12]
    email = f"signup-rls-{token}@example.com"
    first_key = f"signup-new-{uuid4().hex}"
    second_key = f"signup-resume-{uuid4().hex}"
    payload = RegisterRequest.model_validate(
        {
            "name": "Signup RLS Owner",
            "email": email,
            "workspaceName": f"Signup RLS {token}",
            "password": "SenhaSignup#2026",
            "plan": "basic",
        }
    )
    first_delivery_id: UUID | None = None
    second_delivery_id: UUID | None = None
    workspace_id: UUID | None = None
    user_id: UUID | None = None
    request_ids: list[UUID] = []
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        async with postgres_engines.app_sessions() as session:
            first = await IdentityLifecycleService(session).request_signup(
                payload,
                idempotency_key=first_key,
            )
            first_delivery_id = first.delivery_id
            request_ids.append(first.request_id)
            verification = await session.scalar(
                select(SignupVerification).where(
                    SignupVerification.last_delivery_id == first.delivery_id
                )
            )
            assert verification is not None
            workspace_id = verification.workspace_id
            user_id = verification.user_id

        async with postgres_engines.app_sessions() as session:
            second = await IdentityLifecycleService(session).request_signup(
                payload,
                idempotency_key=second_key,
            )
            second_delivery_id = second.delivery_id
            request_ids.append(second.request_id)
            assert second.delivery_id != first_delivery_id

            active_verification = await session.scalar(
                select(SignupVerification).where(
                    SignupVerification.user_id == user_id,
                    SignupVerification.revoked_at.is_(None),
                )
            )
            assert active_verification is not None
            assert active_verification.workspace_id == workspace_id
            assert active_verification.last_delivery_id == second.delivery_id
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceMembership).where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == user_id,
                )
            ) == 1

        async with admin_sessions() as session:
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        User.__table__.insert().values(
                            id=uuid4(),
                            name="Noncanonical PG",
                            email=f"Upper-{token}@example.test",
                            agent_type=AgentType.human,
                            role=UserRole.admin,
                            initials="NP",
                            color="#111827",
                            is_active=True,
                            auth_epoch=0,
                            is_internal_staff=False,
                            is_platform_admin=False,
                        )
                    )
    finally:
        async with admin_sessions() as session:
            delivery_ids = [
                delivery_id
                for delivery_id in (first_delivery_id, second_delivery_id)
                if delivery_id is not None
            ]
            if delivery_ids:
                await session.execute(
                    delete(IdentityOutboxMessage).where(
                        IdentityOutboxMessage.id.in_(delivery_ids)
                    )
                )
            if request_ids:
                await session.execute(
                    delete(IdentityIdempotencyRecord).where(
                        IdentityIdempotencyRecord.id.in_(request_ids)
                    )
                )
            if user_id is not None:
                await session.execute(delete(User).where(User.id == user_id))
            if workspace_id is not None:
                await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await session.commit()


@pytest.mark.asyncio
async def test_chatwoot_account_binding_is_scoped_by_postgresql_rls(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
) -> None:
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with admin_sessions() as session:
        session.add(
            ExternalEntityLink(
                workspace_id=tenant_rows.workspace_a,
                provider=OSSBridgeProvider.chatwoot,
                klm_entity_type="workspace",
                klm_entity_id=tenant_rows.workspace_a,
                external_account_id="chatwoot-account-a",
                external_entity_type="account",
                external_entity_id="chatwoot-account-a",
                link_metadata={"binding": "postgresql-test"},
                sync_state=ExternalEntitySyncState.synced,
            )
        )
        await session.commit()
        session.add(
            ExternalEntityLink(
                workspace_id=tenant_rows.workspace_b,
                provider=OSSBridgeProvider.chatwoot,
                klm_entity_type="workspace",
                klm_entity_id=tenant_rows.workspace_b,
                external_account_id="chatwoot-account-a",
                external_entity_type="account",
                external_entity_id="chatwoot-account-a",
                link_metadata={"binding": "must-fail"},
                sync_state=ExternalEntitySyncState.synced,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, tenant_rows.workspace_b)
        with pytest.raises(ChatwootAccountBindingError):
            await ChatwootBridgeService(session)._require_account_binding(
                tenant_rows.workspace_b,
                "chatwoot-account-a",
            )

    async with postgres_engines.app_sessions() as session:
        await set_workspace_context(session, tenant_rows.workspace_a)
        await ChatwootBridgeService(session)._require_account_binding(
            tenant_rows.workspace_a,
            "chatwoot-account-a",
        )


@pytest.mark.asyncio
async def test_sdr_handoff_adopts_workspace_before_accessing_the_matter(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
    monkeypatch,
) -> None:
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with admin_sessions() as session:
        client = Client(workspace_id=tenant_rows.workspace_a, name="Cliente RLS SDR")
        session.add(client)
        await session.flush()
        matter = Matter(
            workspace_id=tenant_rows.workspace_a,
            client_id=client.id,
            practice_area=PracticeArea.trabalhista,
            demand_type="Handoff RLS",
        )
        session.add(matter)
        await session.commit()
        matter_id = matter.id

    dispatched = AsyncMock()
    monkeypatch.setattr(klm_sdr, "AsyncSessionLocal", postgres_engines.app_sessions)
    monkeypatch.setattr("app.workers.event_bus.dispatch_analyst", dispatched)
    qualification = SDRQualification(
        handoff_target=SDRHandoffTarget.closer_juridico,
        handoff_notes="Encaminhar com contexto isolado.",
    )

    await klm_sdr._trigger_handoff(
        matter_id,
        qualification,
        workspace_id=tenant_rows.workspace_b,
    )
    dispatched.assert_not_awaited()

    async with admin_sessions() as session:
        hidden_attempt = await session.get(Matter, matter_id)
        assert hidden_attempt is not None
        assert hidden_attempt.column_id == KanbanColumnId.entrada

    await klm_sdr._trigger_handoff(
        matter_id,
        qualification,
        workspace_id=tenant_rows.workspace_a,
    )
    dispatched.assert_awaited_once()
    assert dispatched.await_args.kwargs["workspace_id"] == tenant_rows.workspace_a

    async with admin_sessions() as session:
        updated = await session.get(Matter, matter_id)
        assert updated is not None
        assert updated.column_id == KanbanColumnId.proposta_fechamento


@pytest.mark.asyncio
async def test_local_seed_uses_attributable_root_and_clears_control_plane_context(
    postgres_engines: PostgresEngines,
    monkeypatch,
) -> None:
    async with postgres_engines.app_sessions() as session:
        tenant_user, workspace = await KlmLocalSeedService(session).ensure()
        platform_owner = await session.scalar(
            select(User).where(User.email == LOCAL_PLATFORM_USER_EMAIL)
        )
        assert platform_owner is not None
        assert platform_owner.platform_role_package == "platform_owner"
        assert platform_owner.id != tenant_user.id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WorkspaceMembership)
                .where(WorkspaceMembership.user_id == platform_owner.id)
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT current_setting('app.current_platform_scope', true)")
            )
            == ""
        )
        workspace_id = workspace.id
        platform_owner_id = platform_owner.id
        await session.commit()

    async with postgres_engines.app_sessions() as session:
        await set_platform_control_context(
            session,
            actor_user_id=platform_owner_id,
            module="commercial_contracts",
            action="manage",
            is_platform_root=True,
        )
        contract = await session.scalar(
            select(WorkspaceCommercialContract).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )
        assert contract is not None
        active_skus = set(
            (
                await session.scalars(
                    select(CommercialOffer.sku)
                    .join(
                        WorkspaceContractLine,
                        WorkspaceContractLine.offer_id == CommercialOffer.id,
                    )
                    .where(
                        WorkspaceContractLine.contract_id == contract.id,
                        WorkspaceContractLine.status
                        == CommercialContractLineStatus.active,
                    )
                )
            ).all()
        )
        assert active_skus == {"crm_evolution", "legal_360"}
        catalog_receipt = await session.scalar(
            select(CommercialCommandReceipt).where(
                CommercialCommandReceipt.operation == "bootstrap_catalog",
                CommercialCommandReceipt.idempotency_key
                == f"klm-local-commercial-catalog:{LOCAL_COMMERCIAL_SEED_VERSION}",
            )
        )
        assert catalog_receipt is not None
        assert catalog_receipt.actor_user_id == platform_owner_id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CommercialCatalogEvent)
                .where(CommercialCatalogEvent.actor_user_id == platform_owner_id)
            )
            > 0
        )

    async with postgres_engines.app_sessions() as session:
        soft_deleted_owner = await session.scalar(
            select(User).where(User.id == platform_owner_id)
        )
        assert soft_deleted_owner is not None
        previous_auth_epoch = soft_deleted_owner.auth_epoch
        soft_deleted_owner.deleted_at = datetime.now(UTC)
        soft_deleted_owner.is_active = False
        await session.flush()

        second_user, second_workspace = await KlmLocalSeedService(session).ensure()
        second_owner = await KlmLocalSeedService(session).ensure_platform_identity()
        assert second_user.id == tenant_user.id
        assert second_workspace.id == workspace_id
        assert second_owner.id == platform_owner_id
        assert second_owner.deleted_at is None
        assert second_owner.is_active is True
        assert second_owner.auth_epoch == previous_auth_epoch + 1
        assert second_workspace.slug == LOCAL_WORKSPACE_SLUG
        assert (
            await session.scalar(
                text("SELECT current_setting('app.current_platform_scope', true)")
            )
            == ""
        )
        await session.commit()

    async with postgres_engines.app_sessions() as session:
        workspace = await session.scalar(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        assert workspace is not None
        workspace.settings = {
            key: value
            for key, value in (workspace.settings or {}).items()
            if key != "klmCommercialSeedVersion"
        }
        await session.flush()
        monkeypatch.setattr(
            KlmLocalSeedService,
            "_ensure_local_commercial_contract",
            AsyncMock(side_effect=RuntimeError("falha comercial controlada")),
        )
        with pytest.raises(RuntimeError, match="falha comercial controlada"):
            await KlmLocalSeedService(session).ensure()
        assert (
            await session.scalar(
                text("SELECT current_setting('app.current_platform_scope', true)")
            )
            == ""
        )


@pytest.mark.asyncio
async def test_asaas_webhook_resolver_endpoint_and_task_only_bootstrap_bound_tenant(
    postgres_engines: PostgresEngines,
    tenant_rows: TenantRows,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S7: os três elos do webhook do Asaas sob FORCE RLS. Sem escopo a
    varredura de integration_connections é cega; o resolver devolve o
    workspace só para o digest certo e NULL em ambiguidade; o endpoint grava
    o recibo com contexto e enfileira com workspace_id; a task lê o recibo
    com contexto. Só o PostgreSQL prova isso."""
    from httpx import ASGITransport, AsyncClient

    from app.core.config import settings
    from app.core.deps import get_db
    from app.main import app
    from app.models.asaas_webhook_receipt import AsaasWebhookReceipt, AsaasWebhookReceiptStatus
    from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
    from app.services.integration_secrets import (
        protect_integration_settings,
        webhook_secret_digest,
    )
    from app.workers.finance_tasks import _async_process_asaas_webhook_receipt

    monkeypatch.setattr(
        settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", "rls-harness-integration-key-" + "x" * 24
    )
    token = f"asaas-{uuid4().hex}"
    duplicated = f"asaas-dup-{uuid4().hex}"
    admin_sessions = async_sessionmaker(
        postgres_engines.master, class_=AsyncSession, expire_on_commit=False
    )
    async with admin_sessions() as session:
        session.add_all(
            (
                IntegrationConnection(
                    workspace_id=tenant_rows.workspace_a,
                    provider=IntegrationProvider.asaas,
                    status=IntegrationStatus.connected,
                    label="Asaas A",
                    settings=protect_integration_settings({"webhookSecret": token}),
                ),
                IntegrationConnection(
                    workspace_id=tenant_rows.workspace_a,
                    provider=IntegrationProvider.asaas,
                    status=IntegrationStatus.connected,
                    label="Asaas A dup",
                    settings=protect_integration_settings({"webhookSecret": duplicated}),
                ),
                IntegrationConnection(
                    workspace_id=tenant_rows.workspace_b,
                    provider=IntegrationProvider.asaas,
                    status=IntegrationStatus.connected,
                    label="Asaas B dup",
                    settings=protect_integration_settings({"webhookSecret": duplicated}),
                ),
            )
        )
        await session.commit()

    queued: list[dict] = []
    monkeypatch.setattr(
        "app.workers.finance_tasks.process_asaas_webhook_receipt.apply_async",
        lambda *args, **kwargs: queued.append(kwargs),
    )

    async def _override_get_db():
        async with postgres_engines.app_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with postgres_engines.app_sessions() as session:
            # elo 1, o defeito: sem contexto a varredura é cega
            blind = await session.scalars(
                select(IntegrationConnection.id).where(
                    IntegrationConnection.provider == IntegrationProvider.asaas
                )
            )
            assert list(blind.all()) == []
            resolver = text("SELECT public.klm_resolve_asaas_webhook_workspace(:digest)")
            assert UUID(
                str(await session.scalar(resolver, {"digest": webhook_secret_digest(token)}))
            ) == tenant_rows.workspace_a
            assert await session.scalar(resolver, {"digest": webhook_secret_digest("x")}) is None
            # mesmo digest em A e B: NULL, nunca um tenant arbitrário
            assert (
                await session.scalar(resolver, {"digest": webhook_secret_digest(duplicated)})
                is None
            )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://harness") as client:
            body = {"id": f"evt_{uuid4().hex}", "event": "PAYMENT_HARNESS", "payment": {}}
            wrong = await client.post(
                "/api/v1/webhooks/asaas", json=body, headers={"asaas-access-token": "errado"}
            )
            assert wrong.status_code == 401
            ambiguous = await client.post(
                "/api/v1/webhooks/asaas", json=body, headers={"asaas-access-token": duplicated}
            )
            assert ambiguous.status_code == 401
            accepted = await client.post(
                "/api/v1/webhooks/asaas", json=body, headers={"asaas-access-token": token}
            )
            assert accepted.status_code == 200, accepted.text
        (enqueued,) = queued
        assert enqueued["kwargs"]["workspace_id"] == str(tenant_rows.workspace_a)
        receipt_id = UUID(enqueued["kwargs"]["receipt_id"])

        # elo 3: a task lê o recibo com o contexto do workspace
        await _async_process_asaas_webhook_receipt(
            receipt_id,
            workspace_id=tenant_rows.workspace_a,
            session_factory=postgres_engines.app_sessions,
        )
        async with admin_sessions() as session:
            receipt = await session.get(AsaasWebhookReceipt, receipt_id)
            assert receipt is not None
            assert receipt.workspace_id == tenant_rows.workspace_a
            assert receipt.status == AsaasWebhookReceiptStatus.processado
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with admin_sessions() as session:
            await session.execute(
                delete(AsaasWebhookReceipt).where(
                    AsaasWebhookReceipt.workspace_id.in_(
                        (tenant_rows.workspace_a, tenant_rows.workspace_b)
                    )
                )
            )
            await session.execute(
                delete(IntegrationConnection).where(
                    IntegrationConnection.provider == IntegrationProvider.asaas,
                    IntegrationConnection.workspace_id.in_(
                        (tenant_rows.workspace_a, tenant_rows.workspace_b)
                    ),
                )
            )
            await session.commit()
