from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.rls import install_rls_runtime_guard
from app.db.tenant import TenantSession
from app.models.identity import (
    IdentityIdempotencyRecord,
    IdentityOutboxMessage,
    SignupVerification,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.user import RegisterRequest
from app.services.auth_rate_limit_service import opaque_auth_digest
from app.services.identity_lifecycle_service import IdentityLifecycleService

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")
APP_URL = os.getenv("KLM_TEST_POSTGRES_APP_URL")

if not MASTER_URL or not APP_URL:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL and "
        "KLM_TEST_POSTGRES_APP_URL; run scripts/verify_postgresql_qa.sh."
    )


@pytest.mark.asyncio
async def test_new_and_resumed_signup_run_under_forced_rls() -> None:
    assert MASTER_URL is not None and APP_URL is not None
    master_engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    app_engine = create_async_engine(APP_URL, pool_pre_ping=True)
    install_rls_runtime_guard(app_engine)
    app_sessions = async_sessionmaker(
        app_engine,
        class_=AsyncSession,
        sync_session_class=TenantSession,
        expire_on_commit=False,
    )
    master_sessions = async_sessionmaker(
        master_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    email = f"rls-signup-{uuid4().hex}@example.com"
    first_key = f"rls-new-{uuid4().hex}"
    resumed_key = f"rls-resumed-{uuid4().hex}"
    payload = RegisterRequest(
        name="RLS Signup Owner",
        email=email,
        workspace_name="RLS Signup Workspace",
        password="SenhaRLS#2026",
        plan="basic",
    )

    try:
        async with app_sessions() as session:
            first = await IdentityLifecycleService(session).request_signup(
                payload,
                idempotency_key=first_key,
            )
        async with app_sessions() as session:
            resumed = await IdentityLifecycleService(session).request_signup(
                payload,
                idempotency_key=resumed_key,
            )

        assert resumed.delivery_id != first.delivery_id
        async with master_sessions() as session:
            user_id = await session.scalar(select(User.id).where(User.email == email))
            assert user_id is not None
            verifications = list(
                (
                    await session.scalars(
                        select(SignupVerification).where(
                            SignupVerification.user_id == user_id
                        )
                    )
                ).all()
            )
            assert len(verifications) == 2
            assert len({row.workspace_id for row in verifications}) == 1
            assert sum(
                row.consumed_at is None and row.revoked_at is None
                for row in verifications
            ) == 1
    finally:
        try:
            async with master_sessions() as session:
                user_id = await session.scalar(select(User.id).where(User.email == email))
                workspace_ids = (
                    list(
                        (
                            await session.scalars(
                                select(SignupVerification.workspace_id).where(
                                    SignupVerification.user_id == user_id
                                )
                            )
                        ).all()
                    )
                    if user_id is not None
                    else []
                )
                await session.execute(
                    delete(IdentityOutboxMessage).where(
                        IdentityOutboxMessage.recipient_digest
                        == opaque_auth_digest("outbox-recipient", email)
                    )
                )
                await session.execute(
                    delete(IdentityIdempotencyRecord).where(
                        IdentityIdempotencyRecord.operation == "signup",
                        IdentityIdempotencyRecord.key_digest.in_(
                            (
                                opaque_auth_digest("idempotency:signup", first_key),
                                opaque_auth_digest("idempotency:signup", resumed_key),
                            )
                        ),
                    )
                )
                if user_id is not None:
                    await session.execute(delete(User).where(User.id == user_id))
                if workspace_ids:
                    await session.execute(
                        delete(Workspace).where(Workspace.id.in_(workspace_ids))
                    )
                await session.commit()
        finally:
            await app_engine.dispose()
            await master_engine.dispose()
