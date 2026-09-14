from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.platform_support import (
    PlatformSupportContextError,
    set_platform_support_context,
)
from app.db.rls import (
    platform_support_policy_predicate,
    policy_expression_matches,
    workspace_policy_predicate,
)


@pytest.mark.asyncio
async def test_platform_support_context_is_narrow_and_validated_on_sqlite() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, class_=AsyncSession)
    try:
        async with sessions() as session:
            await set_platform_support_context(
                session,
                actor_user_id=uuid4(),
                action="view",
            )
            assert not session.in_transaction()

            with pytest.raises(PlatformSupportContextError):
                await set_platform_support_context(
                    session,
                    actor_user_id=uuid4(),
                    action="customers:manage",
                )
            with pytest.raises(PlatformSupportContextError):
                await set_platform_support_context(
                    session,
                    actor_user_id="not-a-uuid",
                    action="view",
                )
    finally:
        await engine.dispose()


def test_only_support_policies_accept_the_platform_support_context() -> None:
    support_only_predicate = platform_support_policy_predicate()
    predicate = platform_support_policy_predicate(allow_staff_reassignment=True)

    assert workspace_policy_predicate() in predicate
    assert "app.current_platform_scope" in predicate
    assert "app.current_platform_actor_id" in predicate
    assert "app.current_platform_action" in predicate
    assert "'support:view'" in predicate
    assert "'customers:manage'" not in predicate
    assert "staff_reassignment" in predicate
    assert not policy_expression_matches("support_tickets", support_only_predicate)
    assert policy_expression_matches("support_tickets", predicate)
    assert not policy_expression_matches("clients", predicate)
