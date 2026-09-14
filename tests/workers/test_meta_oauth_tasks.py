from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.meta_oauth_service as meta_oauth_service_module
from app.models.meta_channels import MetaChannelConnection, MetaChannelType, MetaConnectionStatus
from app.workers import meta_oauth_tasks


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)


async def _connection(db_session, workspace_id, **overrides) -> MetaChannelConnection:
    defaults = dict(
        workspace_id=workspace_id,
        channel_type=MetaChannelType.instagram,
        account_name="Perfil Oficial",
        status=MetaConnectionStatus.pending,
        scopes=[],
        capability_matrix={},
        webhook_registered=False,
    )
    defaults.update(overrides)
    connection = MetaChannelConnection(**defaults)
    db_session.add(connection)
    await db_session.commit()
    await db_session.refresh(connection)
    return connection


@pytest.mark.asyncio
async def test_due_workspace_ids_only_include_instagram_with_tokens(db_session, auth_context):
    await _connection(db_session, auth_context.workspace_id, access_token_encrypted="enc-1")
    await _connection(db_session, auth_context.workspace_id)  # sem token: não entra

    ids = await meta_oauth_tasks._due_workspace_ids(session_factory=_session_factory(db_session))
    assert auth_context.workspace_id in ids


@pytest.mark.asyncio
async def test_refresh_due_tokens_async_is_idempotent_and_gated(db_session, auth_context, monkeypatch):
    monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
    soon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    connection = await _connection(
        db_session,
        auth_context.workspace_id,
        access_token_encrypted="enc-1",
        capability_matrix={"oauth": {"expiresAt": soon}},
    )

    factory = _session_factory(db_session)
    first = await meta_oauth_tasks.refresh_due_tokens_async(session_factory=factory)
    assert first["due"] == 1
    assert first["prepared"] == 1

    # Rodar de novo é idempotente: mesmo resultado, nenhum token exposto.
    second = await meta_oauth_tasks.refresh_due_tokens_async(session_factory=factory)
    assert second["due"] == 1
    assert second["prepared"] == 1

    await db_session.refresh(connection)
    assert connection.access_token_encrypted == "enc-1"
    assert connection.capability_matrix["oauth"]["oauthStatus"] == "refresh_prepared"


async def _page_connection(db_session, workspace_id, **overrides) -> MetaChannelConnection:
    defaults = dict(
        workspace_id=workspace_id,
        channel_type=MetaChannelType.messenger,
        account_name="Página Oficial",
        status=MetaConnectionStatus.pending,
        scopes=[],
        capability_matrix={},
        webhook_registered=False,
    )
    defaults.update(overrides)
    connection = MetaChannelConnection(**defaults)
    db_session.add(connection)
    await db_session.commit()
    await db_session.refresh(connection)
    return connection


@pytest.mark.asyncio
async def test_due_workspace_ids_scoped_to_messenger_channel(db_session, auth_context):
    await _page_connection(db_session, auth_context.workspace_id, access_token_encrypted="enc-page-1")
    await _connection(db_session, auth_context.workspace_id, access_token_encrypted="enc-ig-1")  # não deve entrar

    factory = _session_factory(db_session)
    ig_ids = await meta_oauth_tasks._due_workspace_ids(
        channel_type=MetaChannelType.instagram, session_factory=factory
    )
    page_ids = await meta_oauth_tasks._due_workspace_ids(
        channel_type=MetaChannelType.messenger, session_factory=factory
    )
    assert auth_context.workspace_id in ig_ids
    assert auth_context.workspace_id in page_ids


@pytest.mark.asyncio
async def test_refresh_due_tokens_async_covers_both_channels_by_default(db_session, auth_context, monkeypatch):
    monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
    soon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    ig_connection = await _connection(
        db_session,
        auth_context.workspace_id,
        access_token_encrypted="enc-ig-1",
        capability_matrix={"oauth": {"expiresAt": soon}},
    )
    page_connection = await _page_connection(
        db_session,
        auth_context.workspace_id,
        access_token_encrypted="enc-page-1",
        capability_matrix={"oauth": {"expiresAt": soon}},
    )

    factory = _session_factory(db_session)
    outcome = await meta_oauth_tasks.refresh_due_tokens_async(session_factory=factory)

    assert outcome["due"] == 2
    assert outcome["prepared"] == 2

    await db_session.refresh(ig_connection)
    await db_session.refresh(page_connection)
    assert ig_connection.capability_matrix["oauth"]["oauthStatus"] == "refresh_prepared"
    assert page_connection.capability_matrix["oauth"]["oauthStatus"] == "refresh_prepared"


@pytest.mark.asyncio
async def test_refresh_workspace_tokens_messenger_only_leaves_instagram_untouched(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
    soon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    ig_connection = await _connection(
        db_session,
        auth_context.workspace_id,
        access_token_encrypted="enc-ig-1",
        capability_matrix={"oauth": {"expiresAt": soon}},
    )
    page_connection = await _page_connection(
        db_session,
        auth_context.workspace_id,
        access_token_encrypted="enc-page-1",
        capability_matrix={"oauth": {"expiresAt": soon}},
    )

    factory = _session_factory(db_session)
    outcome = await meta_oauth_tasks.refresh_workspace_tokens(
        auth_context.workspace_id, channel_type=MetaChannelType.messenger, session_factory=factory
    )
    assert outcome["due"] == 1
    assert outcome["prepared"] == 1

    await db_session.refresh(ig_connection)
    await db_session.refresh(page_connection)
    assert "oauthStatus" not in ig_connection.capability_matrix.get("oauth", {})
    assert page_connection.capability_matrix["oauth"]["oauthStatus"] == "refresh_prepared"


@pytest.mark.asyncio
async def test_refresh_due_tokens_async_preserves_refresh_and_failure_totals(monkeypatch):
    outcomes = iter(
        [
            {"workspaces": 1, "due": 2, "prepared": 0, "refreshed": 2, "failed": 0, "skipped": 1},
            {"workspaces": 1, "due": 2, "prepared": 0, "refreshed": 1, "failed": 1, "skipped": 0},
        ]
    )

    async def fake_refresh(*_args, **_kwargs):
        return next(outcomes)

    monkeypatch.setattr(meta_oauth_tasks, "_refresh_channel_due_tokens_async", fake_refresh)
    result = await meta_oauth_tasks.refresh_due_tokens_async()

    assert result == {
        "workspaces": 2,
        "due": 4,
        "prepared": 0,
        "refreshed": 3,
        "failed": 1,
        "skipped": 1,
    }
