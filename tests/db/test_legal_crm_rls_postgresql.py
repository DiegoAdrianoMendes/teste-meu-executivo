from __future__ import annotations

import ast
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
from app.db.aws_database_roles import resolver_owner_role_name
from app.db.rls import (
    RESOLVER_OWNER_ROLE_MARKER,
    RLS_POLICY_SPECS,
    RLS_TENANT_TABLES,
    install_rls_runtime_guard,
    policy_spec_expression_matches,
)
from app.db.tenant import TenantSession, set_workspace_context
from app.models.approval import ApprovalQueueItem
from app.models.client import Client
from app.models.document import Document
from app.models.finance import FinanceEntry, FinanceReminder
from app.models.klm_operations import (
    LegalArtifactVersion,
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.matter import Matter
from app.models.oss_native import (
    OSSNativeCapabilityState,
    OSSNativeCommand,
    OSSNativeExecutionMode,
)
from app.models.storage_object import StorageObject
from app.models.user import AgentType, User, UserRole
from app.models.whatsapp import (
    WaMessageDirection,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_note import WhatsAppConversationNote
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus

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
class LegalCrmRows:
    workspace_a: UUID
    workspace_b: UUID
    ids: dict[str, tuple[UUID, UUID]]


PROTECTED_MODELS = {
    "approval_queue_items": ApprovalQueueItem,
    "clients": Client,
    "documents": Document,
    "finance_entries": FinanceEntry,
    "finance_reminders": FinanceReminder,
    "klm_omnichannel_messages": OmnichannelMessage,
    "klm_omnichannel_threads": OmnichannelThread,
    "matters": Matter,
    "whatsapp_connections": WhatsAppConnection,
    "whatsapp_contacts": WhatsAppContact,
    "whatsapp_conversation_notes": WhatsAppConversationNote,
    "whatsapp_messages": WhatsAppMessage,
}


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
async def legal_crm_rows(postgres_engines: PostgresEngines) -> AsyncIterator[LegalCrmRows]:
    token = uuid4().hex[:12]
    master_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with master_sessions() as session:
        workspace_a = _workspace(f"Legal RLS A {token}", f"legal-rls-a-{token}")
        workspace_b = _workspace(f"Legal RLS B {token}", f"legal-rls-b-{token}")
        session.add_all((workspace_a, workspace_b))
        await session.flush()

        client_a = Client(workspace_id=workspace_a.id, name=f"Cliente A {token}")
        client_b = Client(workspace_id=workspace_b.id, name=f"Cliente B {token}")
        session.add_all((client_a, client_b))
        await session.flush()

        matter_a = Matter(
            workspace_id=workspace_a.id,
            client_id=client_a.id,
            demand_type=f"Demanda A {token}",
        )
        matter_b = Matter(
            workspace_id=workspace_b.id,
            client_id=client_b.id,
            demand_type=f"Demanda B {token}",
        )
        session.add_all((matter_a, matter_b))
        await session.flush()

        document_a = Document(matter_id=matter_a.id, name=f"Documento A {token}")
        document_b = Document(matter_id=matter_b.id, name=f"Documento B {token}")
        approval_a = ApprovalQueueItem(
            matter_id=matter_a.id,
            approval_type="document",
            intent_key=f"rls:workspace-a:{token}",
            title=f"Aprovação A {token}",
        )
        approval_b = ApprovalQueueItem(
            matter_id=matter_b.id,
            approval_type="document",
            intent_key=f"rls:workspace-b:{token}",
            title=f"Aprovação B {token}",
        )
        finance_a = FinanceEntry(
            workspace_id=workspace_a.id,
            matter_id=matter_a.id,
            title=f"Financeiro A {token}",
            amount=100,
        )
        finance_b = FinanceEntry(
            workspace_id=workspace_b.id,
            matter_id=matter_b.id,
            title=f"Financeiro B {token}",
            amount=200,
        )
        session.add_all((document_a, document_b, approval_a, approval_b, finance_a, finance_b))
        await session.flush()

        reminder_a = FinanceReminder(
            workspace_id=workspace_a.id,
            finance_entry_id=finance_a.id,
            remind_at=datetime.now(UTC),
        )
        reminder_b = FinanceReminder(
            workspace_id=workspace_b.id,
            finance_entry_id=finance_b.id,
            remind_at=datetime.now(UTC),
        )
        connection_a = WhatsAppConnection(
            workspace_id=workspace_a.id,
            phone_number_id=f"phone-a-{token}",
        )
        connection_b = WhatsAppConnection(
            workspace_id=workspace_b.id,
            phone_number_id=f"phone-b-{token}",
        )
        session.add_all((reminder_a, reminder_b, connection_a, connection_b))
        await session.flush()

        contact_a = WhatsAppContact(
            workspace_id=workspace_a.id,
            whatsapp_connection_id=connection_a.id,
            matter_id=matter_a.id,
            phone=f"5511{token[:8]}01",
            name=f"Lead A {token}",
        )
        contact_b = WhatsAppContact(
            workspace_id=workspace_b.id,
            whatsapp_connection_id=connection_b.id,
            matter_id=matter_b.id,
            phone=f"5511{token[:8]}02",
            name=f"Lead B {token}",
        )
        thread_a = OmnichannelThread(
            workspace_id=workspace_a.id,
            matter_id=matter_a.id,
            client_id=client_a.id,
            subject=f"Thread A {token}",
        )
        thread_b = OmnichannelThread(
            workspace_id=workspace_b.id,
            matter_id=matter_b.id,
            client_id=client_b.id,
            subject=f"Thread B {token}",
        )
        session.add_all((contact_a, contact_b, thread_a, thread_b))
        await session.flush()

        whatsapp_message_a = WhatsAppMessage(
            contact_id=contact_a.id,
            whatsapp_connection_id=connection_a.id,
            direction=WaMessageDirection.inbound,
            body=f"Mensagem WhatsApp A {token}",
        )
        whatsapp_message_b = WhatsAppMessage(
            contact_id=contact_b.id,
            whatsapp_connection_id=connection_b.id,
            direction=WaMessageDirection.inbound,
            body=f"Mensagem WhatsApp B {token}",
        )
        note_a = WhatsAppConversationNote(
            workspace_id=workspace_a.id,
            contact_id=contact_a.id,
            matter_id=matter_a.id,
            body=f"Nota A {token}",
        )
        note_b = WhatsAppConversationNote(
            workspace_id=workspace_b.id,
            contact_id=contact_b.id,
            matter_id=matter_b.id,
            body=f"Nota B {token}",
        )
        omni_message_a = _omnichannel_message(
            workspace_a.id, thread_a.id, matter_a.id, f"omni-a-{token}"
        )
        omni_message_b = _omnichannel_message(
            workspace_b.id, thread_b.id, matter_b.id, f"omni-b-{token}"
        )
        session.add_all(
            (
                whatsapp_message_a,
                whatsapp_message_b,
                note_a,
                note_b,
                omni_message_a,
                omni_message_b,
            )
        )
        await session.commit()

    rows = LegalCrmRows(
        workspace_a=workspace_a.id,
        workspace_b=workspace_b.id,
        ids={
            "approval_queue_items": (approval_a.id, approval_b.id),
            "clients": (client_a.id, client_b.id),
            "documents": (document_a.id, document_b.id),
            "finance_entries": (finance_a.id, finance_b.id),
            "finance_reminders": (reminder_a.id, reminder_b.id),
            "klm_omnichannel_messages": (omni_message_a.id, omni_message_b.id),
            "klm_omnichannel_threads": (thread_a.id, thread_b.id),
            "matters": (matter_a.id, matter_b.id),
            "whatsapp_connections": (connection_a.id, connection_b.id),
            "whatsapp_contacts": (contact_a.id, contact_b.id),
            "whatsapp_conversation_notes": (note_a.id, note_b.id),
            "whatsapp_messages": (whatsapp_message_a.id, whatsapp_message_b.id),
        },
    )
    try:
        yield rows
    finally:
        async with master_sessions() as session:
            await session.execute(
                delete(Workspace).where(Workspace.id.in_((rows.workspace_a, rows.workspace_b)))
            )
            await session.commit()


def _workspace(name: str, slug: str) -> Workspace:
    return Workspace(
        name=name,
        slug=slug,
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


def _omnichannel_message(
    workspace_id: UUID,
    thread_id: UUID,
    matter_id: UUID,
    suffix: str,
) -> OmnichannelMessage:
    return OmnichannelMessage(
        workspace_id=workspace_id,
        thread_id=thread_id,
        matter_id=matter_id,
        channel=OmnichannelChannel.whatsapp,
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
        author_label="RLS PostgreSQL test",
        body=f"Omnichannel {suffix}",
        idempotency_key=suffix,
    )


@pytest.mark.asyncio
async def test_legal_crm_rls_catalog_is_complete(
    postgres_engines: PostgresEngines,
) -> None:
    async with postgres_engines.admin.connect() as connection:
        migrator_role = str(await connection.scalar(text("SELECT current_user")))
        resolver_owner = resolver_owner_role_name(migrator_role)
        revisions = set(
            (await connection.scalars(text("SELECT version_num FROM alembic_version"))).all()
        )
        assert revisions == _migration_heads()

        tables = (
            await connection.execute(
                text(
                    """
                    SELECT relation.relname,
                           relation.relrowsecurity,
                           relation.relforcerowsecurity
                    FROM pg_class relation
                    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relkind IN ('r', 'p')
                    """
                )
            )
        ).all()
        protected = {row[0]: (row[1], row[2]) for row in tables}
        assert set(RLS_TENANT_TABLES) <= set(protected)
        assert all(protected[table_name] == (True, True) for table_name in RLS_TENANT_TABLES)

        policies = (
            await connection.execute(
                text(
                    """
                    SELECT tablename, policyname, permissive, cmd, roles, qual, with_check
                    FROM pg_policies
                    WHERE schemaname = current_schema()
                    """
                )
            )
        ).all()
        protected_policies: dict[str, list[tuple[object, ...]]] = {}
        for row in policies:
            if row[0] in RLS_TENANT_TABLES:
                protected_policies.setdefault(row[0], []).append(tuple(row[1:]))

        assert set(protected_policies) == set(RLS_TENANT_TABLES)
        for table_name in RLS_TENANT_TABLES:
            actual = protected_policies[table_name]
            expected = RLS_POLICY_SPECS[table_name]
            assert len(actual) == len(expected)
            actual_by_name = {str(row[0]): row for row in actual}
            for policy_spec in expected:
                matching = actual_by_name[policy_spec.name]
                _, permissive, command, roles, actual_using, actual_check = matching
                assert permissive == policy_spec.permissive
                assert command == policy_spec.command
                expected_roles = {
                    resolver_owner if role == RESOLVER_OWNER_ROLE_MARKER else role
                    for role in policy_spec.roles
                }
                assert set(roles or ()) == expected_roles
                assert policy_spec_expression_matches(policy_spec.using, actual_using)
                assert policy_spec_expression_matches(
                    policy_spec.with_check,
                    actual_check,
                )


@pytest.mark.asyncio
async def test_database_rejects_cross_workspace_matter_and_storage_references(
    postgres_engines: PostgresEngines,
    legal_crm_rows: LegalCrmRows,
) -> None:
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with admin_sessions() as session:
        session.add(
            Matter(
                workspace_id=legal_crm_rows.workspace_a,
                client_id=legal_crm_rows.ids["clients"][1],
                demand_type="Cross-workspace client reference",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        object_id = uuid4()
        session.add(
            StorageObject(
                workspace_id=legal_crm_rows.workspace_a,
                object_id=object_id,
                version=1,
                matter_id=legal_crm_rows.ids["matters"][1],
                client_id=legal_crm_rows.ids["clients"][0],
                original_filename="cross-matter.pdf",
                declared_content_type="application/pdf",
                storage_key=f"tenant-integrity/{object_id}/cross-matter",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        object_id = uuid4()
        session.add(
            StorageObject(
                workspace_id=legal_crm_rows.workspace_a,
                object_id=object_id,
                version=1,
                matter_id=legal_crm_rows.ids["matters"][0],
                client_id=legal_crm_rows.ids["clients"][1],
                original_filename="cross-client.pdf",
                declared_content_type="application/pdf",
                storage_key=f"tenant-integrity/{object_id}/cross-client",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        session.add(
            OmnichannelThread(
                workspace_id=legal_crm_rows.workspace_a,
                matter_id=legal_crm_rows.ids["matters"][1],
                client_id=legal_crm_rows.ids["clients"][0],
                subject="Cross-workspace KLM matter reference",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        session.add(
            _omnichannel_message(
                legal_crm_rows.workspace_a,
                legal_crm_rows.ids["klm_omnichannel_threads"][1],
                legal_crm_rows.ids["matters"][0],
                f"cross-thread-{uuid4().hex}",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        foreign_state = OSSNativeCapabilityState(
            workspace_id=legal_crm_rows.workspace_b,
            provider=f"tenant-integrity-{uuid4().hex}",
            capability="cross-workspace-state",
            configuration={},
            config_hash="a" * 64,
            execution_mode=OSSNativeExecutionMode.disabled,
        )
        session.add(foreign_state)
        await session.flush()
        session.add(
            OSSNativeCommand(
                workspace_id=legal_crm_rows.workspace_a,
                state_id=foreign_state.id,
                provider=foreign_state.provider,
                capability=foreign_state.capability,
                command_type="adversarial",
                idempotency_key=uuid4().hex,
                payload_hash="b" * 64,
                command_payload={},
                execution_mode=OSSNativeExecutionMode.disabled,
                state_version=1,
                command_result={},
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_postgresql_evidence_is_append_only_and_native_approval_is_single_use(
    postgres_engines: PostgresEngines,
    legal_crm_rows: LegalCrmRows,
) -> None:
    async with postgres_engines.master.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        token = uuid4().hex
        try:
            user = User(
                name=f"Evidence QA {token}",
                initials="EQ",
                agent_type=AgentType.human,
                role=UserRole.lawyer,
            )
            session.add(user)
            await session.flush()

            artifact = LegalArtifactVersion(
                workspace_id=legal_crm_rows.workspace_a,
                matter_id=legal_crm_rows.ids["matters"][0],
                artifact_key=uuid4(),
                version=1,
                kind="qa_evidence",
                title="PostgreSQL immutable artifact",
                content={"body": "immutable"},
                source_snapshot={"source": "postgresql-qa"},
                created_by_user_id=user.id,
                evidence_sha256="",
            )
            session.add(artifact)
            await session.flush()

            evidence_ids = {
                "klm_audit_events": uuid4(),
                "oss_native_audit_events": uuid4(),
                "oss_function_unit_events": uuid4(),
                "klm_legal_artifact_versions": artifact.id,
            }
            await session.execute(
                text(
                    """
                    INSERT INTO klm_audit_events
                        (id, workspace_id, actor_type, actor_label, event_type,
                         entity_type, entity_id, summary)
                    VALUES
                        (:id, :workspace_id, 'system', 'PostgreSQL QA',
                         'append_only_qa', 'workspace', :workspace_id,
                         'Append-only PostgreSQL evidence')
                    """
                ),
                {
                    "id": evidence_ids["klm_audit_events"],
                    "workspace_id": legal_crm_rows.workspace_a,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO oss_native_audit_events
                        (id, workspace_id, provider, capability, action, summary, metadata)
                    VALUES
                        (:id, :workspace_id, 'postgresql_qa', 'append_only',
                         'command_succeeded', 'Append-only PostgreSQL evidence',
                         '{}'::jsonb)
                    """
                ),
                {
                    "id": evidence_ids["oss_native_audit_events"],
                    "workspace_id": legal_crm_rows.workspace_a,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO oss_function_unit_events
                        (id, workspace_id, unit_id, contract_id, provider, capability,
                         semantic_profile, operation, idempotency_key, resource_id,
                         payload, result, status)
                    VALUES
                        (:id, :workspace_id, :unit_id, 'qa-contract', 'postgresql_qa',
                         'append_only', 'qa', 'verify', :idempotency_key,
                         'qa-resource', '{}'::jsonb, '{}'::jsonb, 'succeeded')
                    """
                ),
                {
                    "id": evidence_ids["oss_function_unit_events"],
                    "workspace_id": legal_crm_rows.workspace_a,
                    "unit_id": f"qa-unit-{token}",
                    "idempotency_key": f"qa-event-{token}",
                },
            )

            trigger_rows = (
                await session.execute(
                    text(
                        """
                        SELECT relation.relname, trigger.tgname,
                               pg_get_triggerdef(trigger.oid)
                        FROM pg_trigger trigger
                        JOIN pg_class relation ON relation.oid = trigger.tgrelid
                        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = current_schema()
                          AND NOT trigger.tgisinternal
                          AND relation.relname::text = ANY(CAST(:table_names AS text[]))
                        """
                    ),
                    {"table_names": list(evidence_ids)},
                )
            ).all()
            trigger_defs = {(row[0], row[1]): row[2] for row in trigger_rows}
            for table_name in evidence_ids:
                assert (table_name, f"trg_{table_name}_immutable_row") in trigger_defs
                truncate_definition = trigger_defs[
                    (table_name, f"trg_{table_name}_immutable_truncate")
                ]
                assert "TRUNCATE" in truncate_definition

            for table_name, row_id in evidence_ids.items():
                with pytest.raises(DBAPIError, match="is immutable"):
                    async with session.begin_nested():
                        await session.execute(
                            text(f'UPDATE "{table_name}" SET id = id WHERE id = :row_id'),
                            {"row_id": row_id},
                        )
                with pytest.raises(DBAPIError, match="is immutable"):
                    async with session.begin_nested():
                        await session.execute(
                            text(f'DELETE FROM "{table_name}" WHERE id = :row_id'),
                            {"row_id": row_id},
                        )
                with pytest.raises(DBAPIError, match="is immutable"):
                    async with session.begin_nested():
                        await session.execute(text(f'TRUNCATE TABLE "{table_name}" CASCADE'))

            consumed_entity_id = uuid4()
            consumed_values = {
                "workspace_id": legal_crm_rows.workspace_a,
                "entity_type": "native_capability_approval",
                "entity_id": consumed_entity_id,
            }
            consumed_insert = text(
                """
                INSERT INTO klm_audit_events
                    (id, workspace_id, actor_type, actor_label, event_type,
                     entity_type, entity_id, summary)
                VALUES
                    (:id, :workspace_id, 'human', 'PostgreSQL QA',
                     'native_capability_human_approval_consumed',
                     :entity_type, :entity_id, 'Consumed exactly once')
                """
            )
            await session.execute(
                consumed_insert,
                {"id": uuid4(), **consumed_values},
            )
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        consumed_insert,
                        {"id": uuid4(), **consumed_values},
                    )

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        consumed_insert,
                        {
                            "id": uuid4(),
                            "workspace_id": legal_crm_rows.workspace_a,
                            "entity_type": "native_capability_approval",
                            "entity_id": None,
                        },
                    )
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


@pytest.mark.asyncio
async def test_legal_crm_rls_isolates_priority_domain_rows(
    postgres_engines: PostgresEngines,
    legal_crm_rows: LegalCrmRows,
) -> None:
    sessions = postgres_engines.app_sessions

    async with sessions() as session:
        for model in PROTECTED_MODELS.values():
            assert await session.scalar(select(func.count()).select_from(model)) == 0

    async with sessions() as session:
        await set_workspace_context(session, legal_crm_rows.workspace_a)
        for table_name, model in PROTECTED_MODELS.items():
            visible_ids = set((await session.scalars(select(model.id))).all())
            assert visible_ids == {legal_crm_rows.ids[table_name][0]}

        hidden_finance_update = await session.execute(
            update(FinanceEntry)
            .where(FinanceEntry.id == legal_crm_rows.ids["finance_entries"][1])
            .values(title="must remain hidden")
        )
        assert hidden_finance_update.rowcount == 0

        hidden_document_update = await session.execute(
            update(Document)
            .where(Document.id == legal_crm_rows.ids["documents"][1])
            .values(name="must remain hidden")
        )
        assert hidden_document_update.rowcount == 0

        session.add(Client(workspace_id=legal_crm_rows.workspace_b, name="cross-tenant direct"))
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        session.add(
            Document(
                matter_id=legal_crm_rows.ids["matters"][1],
                name="cross-tenant document",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        session.add(
            ApprovalQueueItem(
                matter_id=legal_crm_rows.ids["matters"][1],
                approval_type="document",
                intent_key="rls:cross-tenant",
                title="cross-tenant approval",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        session.add(
            WhatsAppMessage(
                contact_id=legal_crm_rows.ids["whatsapp_contacts"][1],
                whatsapp_connection_id=legal_crm_rows.ids["whatsapp_connections"][1],
                direction=WaMessageDirection.outbound,
                body="cross-tenant message",
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()

        own_document = Document(
            matter_id=legal_crm_rows.ids["matters"][0],
            name="own document",
        )
        own_message = WhatsAppMessage(
            contact_id=legal_crm_rows.ids["whatsapp_contacts"][0],
            whatsapp_connection_id=legal_crm_rows.ids["whatsapp_connections"][0],
            direction=WaMessageDirection.outbound,
            body="own message",
        )
        session.add_all((own_document, own_message))
        await session.commit()

        assert await session.get(Document, own_document.id) is not None
        assert await session.get(WhatsAppMessage, own_message.id) is not None

    async with sessions() as session:
        await set_workspace_context(session, legal_crm_rows.workspace_b)
        for table_name, model in PROTECTED_MODELS.items():
            visible_ids = set((await session.scalars(select(model.id))).all())
            assert visible_ids == {legal_crm_rows.ids[table_name][1]}


@pytest.mark.asyncio
async def test_provider_media_receipt_rejects_update_delete_and_truncate_in_postgresql(
    postgres_engines: PostgresEngines,
    legal_crm_rows: LegalCrmRows,
) -> None:
    table_name = "klm_omnichannel_provider_media_receipts"
    async with postgres_engines.master.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        native_connection_id = uuid4()
        receipt_id = uuid4()
        try:
            await session.execute(
                text(
                    """
                    INSERT INTO klm_omnichannel_channel_connections
                        (id, workspace_id, channel, provider, connection_key, display_name)
                    VALUES
                        (:id, :workspace_id, 'whatsapp', 'meta_cloud', :connection_key,
                         'PostgreSQL media receipt QA')
                    """
                ),
                {
                    "id": native_connection_id,
                    "workspace_id": legal_crm_rows.workspace_a,
                    "connection_key": f"whatsapp:qa:{native_connection_id}",
                },
            )
            await session.execute(
                text(
                    f"""
                    INSERT INTO {table_name}
                        (id, workspace_id, connection_id, provider, provider_media_id,
                         media_type, source_sha256, source_size_bytes,
                         source_content_type, provider_payload_sha256, provider_receipt,
                         uploaded_at)
                    VALUES
                        (:id, :workspace_id, :connection_id, 'meta_cloud', :media_id,
                         'image', :source_sha, 1024, 'image/jpeg', :payload_sha,
                         CAST(:provider_receipt AS jsonb), now())
                    """
                ),
                {
                    "id": receipt_id,
                    "workspace_id": legal_crm_rows.workspace_a,
                    "connection_id": native_connection_id,
                    "media_id": f"meta-{receipt_id}",
                    "source_sha": "a" * 64,
                    "payload_sha": "b" * 64,
                    "provider_receipt": f'{{"id":"meta-{receipt_id}"}}',
                },
            )
            trigger_rows = (
                await session.execute(
                    text(
                        """
                        SELECT trigger.tgname, pg_get_triggerdef(trigger.oid)
                        FROM pg_trigger trigger
                        JOIN pg_class relation ON relation.oid = trigger.tgrelid
                        WHERE relation.relname = :table_name
                          AND NOT trigger.tgisinternal
                        """
                    ),
                    {"table_name": table_name},
                )
            ).all()
            trigger_defs = {row[0]: row[1] for row in trigger_rows}
            assert f"trg_{table_name}_immutable" in trigger_defs
            immutable_definition = trigger_defs[f"trg_{table_name}_immutable"]
            assert "BEFORE" in immutable_definition
            assert "UPDATE" in immutable_definition
            assert "DELETE" in immutable_definition
            truncate_name = f"trg_{table_name}_immutable_truncate"
            assert "TRUNCATE" in trigger_defs[truncate_name]

            for mutation in (
                f"UPDATE {table_name} SET provider_media_id = provider_media_id ",
                f"DELETE FROM {table_name} ",
            ):
                with pytest.raises(DBAPIError, match="is append-only"):
                    async with session.begin_nested():
                        await session.execute(
                            text(f"{mutation}WHERE id = :receipt_id"),
                            {"receipt_id": receipt_id},
                        )
            with pytest.raises(DBAPIError, match="is append-only"):
                async with session.begin_nested():
                    await session.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


@pytest.mark.asyncio
async def test_provider_media_intent_and_receipt_are_isolated_for_app_role(
    postgres_engines: PostgresEngines,
    legal_crm_rows: LegalCrmRows,
) -> None:
    connection_a, connection_b = uuid4(), uuid4()
    receipt_a, receipt_b = uuid4(), uuid4()
    intent_a, intent_b = uuid4(), uuid4()
    admin_sessions = async_sessionmaker(
        postgres_engines.master,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def cleanup_fixture_rows() -> None:
        cleanup_errors: list[Exception] = []
        receipt_trigger = "trg_klm_omnichannel_provider_media_receipts_immutable"

        async with admin_sessions() as session:
            try:
                await session.execute(
                    text(
                        "DELETE FROM "
                        "klm_omnichannel_provider_media_upload_intents "
                        "WHERE id IN (:intent_a, :intent_b)"
                    ),
                    {"intent_a": intent_a, "intent_b": intent_b},
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                cleanup_errors.append(exc)

        async with admin_sessions() as session:
            trigger_disabled = False
            try:
                await session.execute(
                    text(
                        "ALTER TABLE klm_omnichannel_provider_media_receipts "
                        f"DISABLE TRIGGER {receipt_trigger}"
                    )
                )
                trigger_disabled = True
                await session.execute(
                    text(
                        "DELETE FROM klm_omnichannel_provider_media_receipts "
                        "WHERE id IN (:receipt_a, :receipt_b)"
                    ),
                    {"receipt_a": receipt_a, "receipt_b": receipt_b},
                )
            except Exception as exc:
                await session.rollback()
                cleanup_errors.append(exc)
            finally:
                if trigger_disabled:
                    try:
                        await session.execute(
                            text(
                                "ALTER TABLE "
                                "klm_omnichannel_provider_media_receipts "
                                f"ENABLE TRIGGER {receipt_trigger}"
                            )
                        )
                        await session.commit()
                    except Exception as exc:
                        await session.rollback()
                        cleanup_errors.append(exc)

        async with admin_sessions() as session:
            try:
                await session.execute(
                    text(
                        "DELETE FROM klm_omnichannel_channel_connections "
                        "WHERE id IN (:connection_a, :connection_b)"
                    ),
                    {"connection_a": connection_a, "connection_b": connection_b},
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                cleanup_errors.append(exc)

        if cleanup_errors:
            raise ExceptionGroup(
                "provider media RLS fixture cleanup failed",
                cleanup_errors,
            )

    test_failure: BaseException | None = None
    try:
        async with admin_sessions() as session:
            for workspace_id, connection_id, suffix in (
                (legal_crm_rows.workspace_a, connection_a, "a"),
                (legal_crm_rows.workspace_b, connection_b, "b"),
            ):
                await session.execute(
                    text(
                        """
                        INSERT INTO klm_omnichannel_channel_connections
                            (id, workspace_id, channel, provider, connection_key,
                             display_name)
                        VALUES
                            (:id, :workspace_id, 'whatsapp', 'meta_cloud',
                             :connection_key, :display_name)
                        """
                    ),
                    {
                        "id": connection_id,
                        "workspace_id": workspace_id,
                        "connection_key": (f"whatsapp:media-rls:{suffix}:{connection_id}"),
                        "display_name": f"Media RLS {suffix}",
                    },
                )
            for workspace_id, connection_id, receipt_id, intent_id, suffix in (
                (
                    legal_crm_rows.workspace_a,
                    connection_a,
                    receipt_a,
                    intent_a,
                    "a",
                ),
                (
                    legal_crm_rows.workspace_b,
                    connection_b,
                    receipt_b,
                    intent_b,
                    "b",
                ),
            ):
                await session.execute(
                    text(
                        """
                        INSERT INTO klm_omnichannel_provider_media_receipts
                            (id, workspace_id, connection_id, provider,
                             provider_media_id, media_type, source_sha256,
                             source_size_bytes, source_content_type,
                             provider_payload_sha256, provider_receipt, uploaded_at)
                        VALUES
                            (:id, :workspace_id, :connection_id, 'meta_cloud',
                             :provider_media_id, 'document', :source_sha, 1024,
                             'application/pdf', :payload_sha,
                             CAST(:provider_receipt AS jsonb), now())
                        """
                    ),
                    {
                        "id": receipt_id,
                        "workspace_id": workspace_id,
                        "connection_id": connection_id,
                        "provider_media_id": f"media-rls-{suffix}-{receipt_id}",
                        "source_sha": suffix * 64,
                        "payload_sha": ("c" if suffix == "a" else "d") * 64,
                        "provider_receipt": (f'{{"id":"media-rls-{suffix}-{receipt_id}"}}'),
                    },
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO klm_omnichannel_provider_media_upload_intents
                            (id, workspace_id, connection_id, provider,
                             idempotency_key_hash, payload_sha256, media_type,
                             source_sha256, source_size_bytes, source_content_type,
                             source_extension, source_filename_sha256, state,
                             provider_media_id, provider_receipt, receipt_id,
                             request_started_at, provider_accepted_at, completed_at)
                        VALUES
                            (:id, :workspace_id, :connection_id, 'meta_cloud',
                             :key_hash, :payload_sha, 'document', :source_sha, 1024,
                             'application/pdf', '.pdf', :filename_sha, 'completed',
                             :provider_media_id, CAST(:provider_receipt AS jsonb),
                             :receipt_id, now(), now(), now())
                        """
                    ),
                    {
                        "id": intent_id,
                        "workspace_id": workspace_id,
                        "connection_id": connection_id,
                        "key_hash": ("e" if suffix == "a" else "f") * 64,
                        "payload_sha": ("1" if suffix == "a" else "2") * 64,
                        "source_sha": suffix * 64,
                        "filename_sha": ("3" if suffix == "a" else "4") * 64,
                        "provider_media_id": f"media-rls-{suffix}-{receipt_id}",
                        "provider_receipt": (f'{{"id":"media-rls-{suffix}-{receipt_id}"}}'),
                        "receipt_id": receipt_id,
                    },
                )
            await session.commit()

        async with postgres_engines.app_sessions() as session:
            await set_workspace_context(session, legal_crm_rows.workspace_a)
            visible_receipts = set(
                (
                    await session.scalars(
                        text("SELECT id FROM klm_omnichannel_provider_media_receipts")
                    )
                ).all()
            )
            visible_intents = set(
                (
                    await session.scalars(
                        text("SELECT id FROM klm_omnichannel_provider_media_upload_intents")
                    )
                ).all()
            )
            assert receipt_a in visible_receipts
            assert receipt_b not in visible_receipts
            assert intent_a in visible_intents
            assert intent_b not in visible_intents

            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            """
                            INSERT INTO
                                klm_omnichannel_provider_media_upload_intents
                                (id, workspace_id, connection_id, provider,
                                 idempotency_key_hash, payload_sha256, media_type,
                                 source_sha256, source_size_bytes,
                                 source_content_type, source_extension,
                                 source_filename_sha256)
                            VALUES
                                (:id, :workspace_id, :connection_id, 'meta_cloud',
                                 :key_hash, :payload_sha, 'document', :source_sha,
                                 1024, 'application/pdf', '.pdf', :filename_sha)
                            """
                        ),
                        {
                            "id": uuid4(),
                            "workspace_id": legal_crm_rows.workspace_b,
                            "connection_id": connection_b,
                            "key_hash": "5" * 64,
                            "payload_sha": "6" * 64,
                            "source_sha": "7" * 64,
                            "filename_sha": "8" * 64,
                        },
                    )
    except BaseException as exc:
        test_failure = exc
        raise
    finally:
        try:
            await cleanup_fixture_rows()
        except Exception as cleanup_exc:
            if test_failure is None:
                raise
            test_failure.add_note(f"Provider media fixture cleanup also failed: {cleanup_exc!r}")


async def test_escopo_de_descoberta_abre_so_a_leitura_da_familia_e_nada_mais(
    postgres_engines: PostgresEngines,
) -> None:
    """S6: as varreduras periódicas liam tabelas de tenant sem contexto e, sob FORCE
    RLS fail-closed, recebiam zero linhas sem erro. Só o PostgreSQL prova isso.

    Fixa as três metades: sem escopo a leitura é cega; com o escopo da família a
    leitura enxerga; e o escopo NÃO permite gravar nem ler outra família."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.exc import DBAPIError

    from app.db.worker_discovery import set_worker_discovery_context
    from app.models.klm_operations import FollowUpStatus, FollowUpTask, OmnichannelChannel
    from app.workers.follow_up_tasks import _due_follow_up_workspace_ids

    token = uuid4().hex[:12]
    master_sessions = async_sessionmaker(
        postgres_engines.master, class_=AsyncSession, expire_on_commit=False
    )
    async with master_sessions() as session:
        workspace = _workspace(f"Descoberta {token}", f"descoberta-{token}")
        session.add(workspace)
        await session.flush()
        session.add(
            FollowUpTask(
                workspace_id=workspace.id,
                channel=OmnichannelChannel.whatsapp,
                status=FollowUpStatus.scheduled,
                due_at=datetime.now(UTC) - timedelta(minutes=5),
                reason="Retomar contato",
            )
        )
        await session.commit()
        workspace_id = workspace.id

    try:
        async with postgres_engines.app_sessions() as session:
            # 1. sem escopo: a descoberta antiga é cega (o defeito)
            cega = await session.scalars(
                select(FollowUpTask.workspace_id).where(FollowUpTask.workspace_id == workspace_id)
            )
            assert list(cega.all()) == []

        async with postgres_engines.app_sessions() as session:
            # 2. com o escopo da família: enxerga (o conserto)
            await set_worker_discovery_context(session, family="follow_up")
            visto = await session.scalars(
                select(FollowUpTask.workspace_id).where(FollowUpTask.workspace_id == workspace_id)
            )
            assert list(visto.all()) == [workspace_id]
            # 3. o escopo é só de leitura: gravar sem workspace continua recusado
            session.add(
                FollowUpTask(
                    workspace_id=workspace_id,
                    channel=OmnichannelChannel.whatsapp,
                    status=FollowUpStatus.scheduled,
                    due_at=datetime.now(UTC),
                )
            )
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()

        async with postgres_engines.app_sessions() as session:
            # 4. outra família não enxerga esta tabela
            await set_worker_discovery_context(session, family="lead_hydration")
            outra = await session.scalars(
                select(FollowUpTask.workspace_id).where(FollowUpTask.workspace_id == workspace_id)
            )
            assert list(outra.all()) == []

        # 5. a função real do worker, como o beat a chama
        ids, linhas = await _due_follow_up_workspace_ids(
            session_factory=postgres_engines.app_sessions
        )
        assert workspace_id in ids
        assert linhas >= 1
    finally:
        async with master_sessions() as session:
            await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await session.commit()


@pytest.mark.asyncio
async def test_s6c_descoberta_com_join_enxerga_e_task_por_workspace_grava_sob_force_rls(
    postgres_engines: PostgresEngines,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S6-C: a descoberta da cobrança é a única do S6 com JOIN de duas tabelas na
    mesma família (finance_charges + finance_entries); e as tasks por workspace
    de cobrança e recorrência gravam com o papel do app sob FORCE RLS.
    Só o PostgreSQL prova as duas coisas."""
    from datetime import UTC, date, datetime, timedelta

    import app.workers.finance_tasks as finance_tasks
    from app.models.client import Client
    from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
    from app.models.finance_charge import FinanceCharge, FinanceChargeStatus
    from app.models.finance_dunning import FinanceDunningLog
    from app.models.finance_recurrence import (
        FinanceRecurrence,
        FinanceRecurrenceCycle,
        FinanceRecurrenceKind,
        FinanceRecurrenceRun,
        FinanceRecurrenceStatus,
    )

    hoje = date(2026, 9, 4)
    vencimento = datetime.combine(hoje - timedelta(days=3), datetime.min.time(), tzinfo=UTC)
    token = uuid4().hex[:12]
    master_sessions = async_sessionmaker(
        postgres_engines.master, class_=AsyncSession, expire_on_commit=False
    )
    async with master_sessions() as session:
        workspace = _workspace(f"S6C {token}", f"s6c-{token}")
        session.add(workspace)
        await session.flush()
        client = Client(workspace_id=workspace.id, name="Cliente S6C", phone="+5561999990000")
        session.add(client)
        await session.flush()
        entry = FinanceEntry(
            workspace_id=workspace.id,
            title="Honorários vencidos",
            entry_type=FinanceEntryType.receivable,
            status=FinanceEntryStatus.overdue,
            amount=1000,
            due_date=vencimento,
            entry_metadata={"clientId": str(client.id)},
        )
        session.add(entry)
        await session.flush()
        session.add(
            FinanceCharge(
                workspace_id=workspace.id,
                entry_id=entry.id,
                status=FinanceChargeStatus.vencida,
                value=1000,
                due_date=vencimento,
            )
        )
        session.add(
            FinanceRecurrence(
                workspace_id=workspace.id,
                client_id=client.id,
                entry_title="Honorários mensais",
                entry_value=500,
                entry_type=FinanceEntryType.receivable,
                kind=FinanceRecurrenceKind.geracao_programada,
                cycle=FinanceRecurrenceCycle.mensal,
                next_run=hoje,
                status=FinanceRecurrenceStatus.ativa,
            )
        )
        await session.commit()
        workspace_id = workspace.id

    despachos: list[dict] = []
    monkeypatch.setattr(
        finance_tasks.dunning_workspace, "apply_async", lambda **kw: despachos.append(kw)
    )
    monkeypatch.setattr(
        finance_tasks.finance_recurrence_workspace,
        "apply_async",
        lambda **kw: despachos.append(kw),
    )
    try:
        async with postgres_engines.app_sessions() as session:
            # sem escopo, o JOIN da cobrança é cego (o defeito)
            cego = await session.scalars(
                select(FinanceCharge.workspace_id)
                .join(FinanceEntry, FinanceEntry.id == FinanceCharge.entry_id)
                .where(FinanceCharge.workspace_id == workspace_id)
            )
            assert list(cego.all()) == []

        # 1. os ticks reais, como o beat os chama: descobrem sob o escopo e despacham
        cobranca = await finance_tasks._async_dunning_tick(
            reference_date=hoje.isoformat(), session_factory=postgres_engines.app_sessions
        )
        recorrencia = await finance_tasks._async_finance_recurrence_tick(
            reference_date=hoje.isoformat(), session_factory=postgres_engines.app_sessions
        )
        assert cobranca["dispatched"] >= 1 and recorrencia["dispatched"] >= 1
        assert {
            d["kwargs"]["workspace_id"] for d in despachos
        } >= {str(workspace_id)}

        # 2. as tasks por workspace gravam com o papel do app sob FORCE RLS
        gravado = await finance_tasks._async_dunning_workspace(
            workspace_id,
            reference_date=hoje.isoformat(),
            session_factory=postgres_engines.app_sessions,
        )
        gerado = await finance_tasks._async_finance_recurrence_workspace(
            workspace_id,
            reference_date=hoje.isoformat(),
            session_factory=postgres_engines.app_sessions,
        )
        assert gravado["processed"] >= 1
        assert gerado["generated"] >= 1
        async with master_sessions() as session:
            degraus = await session.scalars(
                select(FinanceDunningLog).where(FinanceDunningLog.workspace_id == workspace_id)
            )
            assert len(list(degraus.all())) >= 1
            competencias = await session.scalars(
                select(FinanceRecurrenceRun).where(
                    FinanceRecurrenceRun.workspace_id == workspace_id
                )
            )
            assert len(list(competencias.all())) >= 1
    finally:
        async with master_sessions() as session:
            await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await session.commit()


@pytest.mark.asyncio
async def test_s30_tick_da_reforma_tributaria_escreve_sob_force_rls_com_contexto_por_workspace(
    postgres_engines: PostgresEngines,
) -> None:
    """S30: o tick listava workspaces (tabela sem RLS) e chamava o serviço na
    mesma sessão sem contexto: perfil tributário invisível e gravação recusada,
    falha diária. Com uma sessão por workspace e contexto, o papel do app grava."""
    from datetime import date
    from decimal import Decimal

    from app.models.finance_dimensional import FinanceTaxProfile
    from app.workers.finance_tasks import _async_tax_reform_alert_tick

    token = uuid4().hex[:12]
    master_sessions = async_sessionmaker(
        postgres_engines.master, class_=AsyncSession, expire_on_commit=False
    )
    async with master_sessions() as session:
        workspace = _workspace(f"S30 {token}", f"s30-{token}")
        session.add(workspace)
        await session.flush()
        session.add(
            FinanceTaxProfile(
                workspace_id=workspace.id,
                regime="lucro_presumido",
                tax_rate=Decimal("12.5"),
                effective_from=date(2026, 1, 1),
                sources=[{"type": "contador", "reference": "responsável configurado"}],
                reform_calendar={"confirmationRequired": True},
                settings={"humanValidationRequired": True},
                version=1,
            )
        )
        await session.commit()
        workspace_id = workspace.id

    try:
        async with postgres_engines.app_sessions() as session:
            # sem contexto o perfil do workspace é invisível (o defeito)
            cego = await session.scalar(
                select(FinanceTaxProfile).where(FinanceTaxProfile.workspace_id == workspace_id)
            )
            assert cego is None

        totals = await _async_tax_reform_alert_tick(
            reference_date="2026-07-05", session_factory=postgres_engines.app_sessions
        )
        assert totals["failed"] == 0, totals
        assert totals["called"] + totals["existing"] + totals["blocked"] >= 1, totals
    finally:
        # Sem apagar o workspace: o tick grava klm_audit_events, tabela imutável
        # (DELETE proibido por trigger), e o cascade do workspace esbarraria nela.
        # O banco do gate é efêmero; a linha semeada fica.
        pass

async def test_fan_out_do_monitoramento_nao_pode_ler_monitored_processes_sem_contexto(
    postgres_engines: PostgresEngines,
) -> None:
    """S44: o beat enumerava workspaces lendo `monitored_processes` direto.

    `monitored_processes` está em PROCESS_MONITORING_DIRECT_TENANT_TABLES com
    FORCE ROW LEVEL SECURITY e predicado fail-closed. Sem workspace no contexto,
    a consulta antiga devolvia zero linhas, sem erro: o tick despachava nada e
    se registrava como sucesso. Só o PostgreSQL prova isso — em SQLite não há
    policy e a mesma consulta devolveria os processos.

    Este teste fixa as duas metades: a consulta antiga é cega, e a nova
    (por `workspaces`, que não está sob RLS de tenant) enxerga.
    """
    from app.models.process_monitoring import MonitoredProcess, MonitoredProcessStatus
    from app.workers.process_monitoring_tasks import _active_workspace_ids

    token = uuid4().hex[:12]
    master_sessions = async_sessionmaker(
        postgres_engines.master, class_=AsyncSession, expire_on_commit=False
    )
    async with master_sessions() as session:
        workspace = _workspace(f"Monitoramento {token}", f"monitoramento-{token}")
        session.add(workspace)
        await session.flush()
        # created_by_user_id é NOT NULL: sem usuário, o Postgres recusa a semeadura
        # (o SQLite não tem essa coluna como obrigatória no mesmo grau, e foi por
        # isso que este teste nasceu vermelho no harness sem ninguém ver).
        autor = User(
            name=f"Monitoramento QA {token}",
            initials="MQ",
            agent_type=AgentType.human,
            role=UserRole.lawyer,
        )
        session.add(autor)
        await session.flush()
        session.add(
            MonitoredProcess(
                workspace_id=workspace.id,
                numero_cnj="0001234-37.2026.8.07.0001",
                status=MonitoredProcessStatus.ativo,
                created_by_user_id=autor.id,
            )
        )
        await session.commit()
        workspace_id = workspace.id
        autor_id = autor.id

    try:
        async with postgres_engines.app_sessions() as session:
            # A consulta antiga, sem contexto: cega pelo predicado fail-closed.
            antiga = await session.scalars(
                select(MonitoredProcess.workspace_id)
                .where(MonitoredProcess.status == MonitoredProcessStatus.ativo)
                .distinct()
            )
            assert list(antiga.all()) == []

            # A nova, por `workspaces`: enxerga, porque essa tabela não está
            # sob RLS de tenant, e é assim que os outros beats da casa fazem.
            assert workspace_id in await _active_workspace_ids(session)
    finally:
        async with master_sessions() as session:
            await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await session.execute(delete(User).where(User.id == autor_id))
            await session.commit()
