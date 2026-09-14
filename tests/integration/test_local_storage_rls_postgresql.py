from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.rls import install_rls_runtime_guard
from app.db.tenant import (
    TenantSession,
    WorkspaceContextError,
    set_workspace_context,
)
from app.models.billing import (
    BillingInterval,
    Plan,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.local_object_storage_service import (
    LocalObjectStorageService,
    LocalObjectTicketError,
)
from app.services.private_document_storage_service import PrivateDocumentStorageService
from app.services.s3_service import S3Service

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")
APP_URL = os.getenv("KLM_TEST_POSTGRES_APP_URL")

if not MASTER_URL or not APP_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL and "
        "KLM_TEST_POSTGRES_APP_URL; run scripts/verify_postgresql_qa.sh."
    )


def _workspace(token: str, suffix: str) -> Workspace:
    return Workspace(
        name=f"Storage RLS {suffix} {token}",
        slug=f"storage-rls-{suffix}-{token}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )


def _storage_object(workspace_id, token: str, suffix: str) -> StorageObject:
    version_id = uuid4()
    object_id = uuid4()
    return StorageObject(
        id=version_id,
        workspace_id=workspace_id,
        object_id=object_id,
        version=1,
        original_filename=f"{suffix}.pdf",
        declared_content_type="application/pdf",
        storage_key=f"tenants/{workspace_id.hex}/objects/{object_id.hex}/{token}/payload",
        quarantine_key=f"quarantine/{workspace_id.hex}/{version_id.hex}/payload",
        state=StorageObjectState.pending,
        av_status=StorageAVStatus.pending,
    )


class _PrivateAuthorizationS3:
    def verify_private_bucket(self) -> None:
        return None

    def generate_presigned_private_upload_url(self, key: str, **_kwargs) -> str:
        return f"https://private.invalid/{key}"


@pytest.mark.asyncio
async def test_ticket_validation_installs_postgresql_rls_only_for_bound_workspace(
    monkeypatch,
    tmp_path,
) -> None:
    assert MASTER_URL is not None and APP_URL is not None
    monkeypatch.setattr(S3Service, "LOCAL_ROOT", tmp_path / "objects")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    token = uuid4().hex[:12]
    master_engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    app_engine = create_async_engine(APP_URL, pool_pre_ping=True)
    install_rls_runtime_guard(app_engine)
    master_sessions = async_sessionmaker(
        master_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    app_sessions = async_sessionmaker(
        app_engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
    )

    async with master_sessions() as db:
        workspace_a = _workspace(token, "a")
        workspace_b = _workspace(token, "b")
        db.add_all((workspace_a, workspace_b))
        await db.flush()
        record_a = _storage_object(workspace_a.id, token, "a")
        record_b = _storage_object(workspace_b.id, token, "b")
        db.add_all((record_a, record_b))
        await db.commit()
        workspace_a_id = workspace_a.id
        workspace_b_id = workspace_b.id
        record_a_id = record_a.id

    ticket = S3Service().issue_local_object_ticket(
        operation="object_upload",
        workspace_id=workspace_a_id,
        object_id=record_a.object_id,
        version_id=record_a.id,
        version=1,
        s3_key=record_a.storage_key,
        content_type=record_a.declared_content_type,
        expires_in=60,
        ticket_epoch=record_a.ticket_epoch,
    )

    try:
        async with app_sessions() as db:
            with pytest.raises(LocalObjectTicketError):
                await LocalObjectStorageService(db).validate_upload_ticket(
                    f"{ticket.token}tampered"
                )
            setting = await db.scalar(
                text(
                    "SELECT NULLIF(current_setting('app.current_workspace_id', true), '')"
                )
            )
            assert setting is None
            assert await db.scalar(select(func.count()).select_from(StorageObject)) == 0

        async with app_sessions() as db:
            payload = await LocalObjectStorageService(db).validate_upload_ticket(
                ticket.token
            )
            assert payload["workspace_id"] == str(workspace_a_id)
            visible_ids = set((await db.scalars(select(StorageObject.id))).all())
            assert visible_ids == {record_a_id}
    finally:
        async with master_sessions() as db:
            await db.execute(
                delete(Workspace).where(
                    Workspace.id.in_((workspace_a_id, workspace_b_id))
                )
            )
            await db.commit()
        await app_engine.dispose()
        await master_engine.dispose()


@pytest.mark.asyncio
async def test_runtime_role_cannot_duplicate_hidden_storage_object_version() -> None:
    assert MASTER_URL is not None and APP_URL is not None
    token = uuid4().hex[:12]
    shared_object_id = uuid4()
    master_engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    app_engine = create_async_engine(APP_URL, pool_pre_ping=True)
    master_sessions = async_sessionmaker(
        master_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    app_sessions = async_sessionmaker(
        app_engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
    )

    async with master_sessions() as db:
        workspace_a = _workspace(token, "global-a")
        workspace_b = _workspace(token, "global-b")
        db.add_all((workspace_a, workspace_b))
        await db.flush()
        first = _storage_object(workspace_a.id, token, "global-a")
        first.object_id = shared_object_id
        db.add(first)
        await db.commit()
        workspace_a_id = workspace_a.id
        workspace_b_id = workspace_b.id

    try:
        async with app_sessions() as db:
            role_state = (
                await db.execute(
                    text(
                        """
                        SELECT role.rolsuper, role.rolbypassrls
                        FROM pg_roles AS role
                        WHERE role.rolname = current_user
                        """
                    )
                )
            ).one()
            assert tuple(role_state) == (False, False)
            rls_state = (
                await db.execute(
                    text(
                        """
                        SELECT relation.relrowsecurity, relation.relforcerowsecurity
                        FROM pg_class AS relation
                        WHERE relation.oid = 'storage_objects'::regclass
                        """
                    )
                )
            ).one()
            assert tuple(rls_state) == (True, True)
            await set_workspace_context(db, workspace_b_id)
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(StorageObject)
                    .where(StorageObject.object_id == shared_object_id)
                )
                == 0
            )
            duplicate = _storage_object(workspace_b_id, token, "global-b")
            duplicate.object_id = shared_object_id
            db.add(duplicate)
            with pytest.raises(DBAPIError):
                await db.flush()
            await db.rollback()
    finally:
        async with master_sessions() as db:
            await db.execute(
                delete(Workspace).where(
                    Workspace.id.in_((workspace_a_id, workspace_b_id))
                )
            )
            await db.commit()
        await app_engine.dispose()
        await master_engine.dispose()


@pytest.mark.asyncio
async def test_private_authorization_uses_isolated_tenant_transaction(
    monkeypatch,
) -> None:
    """The durable S3 intent must not commit caller work or cross RLS scope."""

    assert MASTER_URL is not None and APP_URL is not None
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "production")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", True)
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", "alias/meu-executivo-documents")

    token = uuid4().hex[:12]
    master_engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    app_engine = create_async_engine(APP_URL, pool_pre_ping=True)
    install_rls_runtime_guard(app_engine)
    master_sessions = async_sessionmaker(
        master_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    app_sessions = async_sessionmaker(
        app_engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with master_sessions() as db:
        workspace_a = _workspace(token, "boundary-a")
        workspace_b = _workspace(token, "boundary-b")
        db.add_all((workspace_a, workspace_b))
        await db.flush()
        plan = Plan(
            name=f"Storage boundary {token}",
            tier=PlanTier.enterprise,
            price_monthly_cents=0,
            price_annually_cents=0,
            max_users=10,
            max_matters=None,
            max_tokens_per_month=1_000,
            max_ai_agents=1,
            features={},
            is_active=True,
            is_public=False,
            sort_order=1,
        )
        db.add(plan)
        await db.flush()
        db.add(
            Subscription(
                workspace_id=workspace_a.id,
                plan_id=plan.id,
                status=SubscriptionStatus.active,
                interval=BillingInterval.monthly,
                amount_cents=0,
                currency="BRL",
            )
        )
        await db.commit()
        workspace_a_id = workspace_a.id
        workspace_b_id = workspace_b.id

    try:
        async with app_sessions() as caller:
            await set_workspace_context(caller, workspace_a_id)
            # This is the same lock used by legal-suite and stock enforcement.
            # It must serialize another quota writer while remaining compatible
            # with the FK key-share lock of the dedicated storage transaction.
            assert (
                await EntitlementEnforcementService(caller).lock_workspace(
                    workspace_a_id
                )
                is True
            )
            caller_only = _storage_object(workspace_a_id, token, "caller-pending")
            caller.add(caller_only)
            await caller.flush()

            service = PrivateDocumentStorageService(
                caller,
                s3=_PrivateAuthorizationS3(),
            )
            authorization = await asyncio.wait_for(
                service.create_upload_authorization(
                    workspace_id=workspace_a_id,
                    actor_user_id=None,
                    filename="contrato.pdf",
                    content_type="application/pdf",
                    byte_size=128,
                    checksum_sha256="a" * 64,
                    retention_until=None,
                    expires_in=300,
                    idempotency_key=f"private-storage-boundary-{token}",
                ),
                timeout=5,
            )
            authorized_version_id = authorization.storage_object.id

            async with app_sessions() as observer:
                await set_workspace_context(observer, workspace_a_id)
                visible_ids = set((await observer.scalars(select(StorageObject.id))).all())
                assert authorized_version_id in visible_ids
                assert caller_only.id not in visible_ids

            await caller.rollback()

            # The caller remains bound to A after rollback; an isolated
            # authorization session cannot be used as a cross-workspace escape.
            with pytest.raises(WorkspaceContextError):
                await service.create_upload_authorization(
                    workspace_id=workspace_b_id,
                    actor_user_id=None,
                    filename="outro.pdf",
                    content_type="application/pdf",
                    byte_size=128,
                    checksum_sha256="b" * 64,
                    retention_until=None,
                    expires_in=300,
                    idempotency_key=f"private-storage-cross-tenant-{token}",
                )

        async with app_sessions() as observer:
            await set_workspace_context(observer, workspace_a_id)
            visible_ids = set((await observer.scalars(select(StorageObject.id))).all())
            assert authorized_version_id in visible_ids
            assert caller_only.id not in visible_ids
    finally:
        async with master_sessions() as db:
            await db.execute(
                text(
                    "ALTER TABLE klm_audit_events DISABLE TRIGGER "
                    "trg_klm_audit_events_immutable_row"
                )
            )
            try:
                await db.execute(
                    delete(Workspace).where(
                        Workspace.id.in_((workspace_a_id, workspace_b_id))
                    )
                )
            finally:
                await db.execute(
                    text(
                        "ALTER TABLE klm_audit_events ENABLE TRIGGER "
                        "trg_klm_audit_events_immutable_row"
                    )
                )
            await db.commit()
        await app_engine.dispose()
        await master_engine.dispose()
