from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

import app.services.meta_oauth_service as meta_oauth_service_module
from app.models.meta_channels import MetaChannelConnection, MetaChannelType, MetaConnectionStatus
from app.services.meta_oauth_service import (
    INSTAGRAM_OAUTH_SCOPES,
    PAGE_OAUTH_SCOPES,
    MetaOAuthConnectionNotFoundError,
    MetaOAuthService,
    MetaOAuthStateError,
    sign_oauth_state,
    verify_oauth_state,
)


async def _connection(db_session, workspace_id, **overrides) -> MetaChannelConnection:
    defaults = dict(
        workspace_id=workspace_id,
        channel_type=MetaChannelType.instagram,
        external_account_id=f"ig-{uuid4()}",
        account_name="Escritório MV",
        scopes=[],
        capability_matrix={},
        status=MetaConnectionStatus.pending,
        webhook_registered=False,
    )
    defaults.update(overrides)
    connection = MetaChannelConnection(**defaults)
    db_session.add(connection)
    await db_session.flush()
    return connection


class TestOAuthState:
    def test_state_roundtrip(self):
        workspace_id = uuid4()
        connection_id = uuid4()
        state, expires_at = sign_oauth_state(
            workspace_id=workspace_id,
            connection_id=connection_id,
            redirect_uri="https://app.example/callback",
        )
        payload = verify_oauth_state(state)
        assert payload.workspace_id == workspace_id
        assert payload.connection_id == connection_id
        assert payload.redirect_uri == "https://app.example/callback"
        assert payload.expires_at == expires_at

    def test_forged_signature_rejected(self):
        state, _ = sign_oauth_state(
            workspace_id=uuid4(),
            connection_id=uuid4(),
            redirect_uri="https://app.example/callback",
        )
        encoded, _signature = state.split(".", 1)
        forged = f"{encoded}.deadbeef" * 1
        with pytest.raises(MetaOAuthStateError):
            verify_oauth_state(forged)

    def test_tampered_payload_rejected(self):
        state, _ = sign_oauth_state(
            workspace_id=uuid4(),
            connection_id=uuid4(),
            redirect_uri="https://app.example/callback",
        )
        encoded, signature = state.split(".", 1)
        tampered = f"{encoded}x.{signature}"
        with pytest.raises(MetaOAuthStateError):
            verify_oauth_state(tampered)

    def test_expired_state_rejected(self):
        now = datetime.now(UTC) - timedelta(seconds=400)
        state, _ = sign_oauth_state(
            workspace_id=uuid4(),
            connection_id=uuid4(),
            redirect_uri="https://app.example/callback",
            now=now,
            ttl_seconds=300,
        )
        with pytest.raises(MetaOAuthStateError):
            verify_oauth_state(state)

    def test_malformed_state_rejected(self):
        with pytest.raises(MetaOAuthStateError):
            verify_oauth_state("not-a-valid-state")


@pytest.mark.asyncio
class TestBuildAuthorizationUrl:
    async def test_builds_url_with_expected_scopes(self, db_session, auth_context):
        connection = await _connection(db_session, auth_context.workspace_id)
        service = MetaOAuthService(db_session)
        result = await service.build_authorization_url(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            redirect_uri="https://app.example/ig/callback",
        )
        assert result["scopes"] == list(INSTAGRAM_OAUTH_SCOPES)
        assert "instagram_business_basic" in result["authorizationUrl"]
        assert "instagram_business_manage_messages" in result["authorizationUrl"]
        assert "instagram_business_manage_comments" in result["authorizationUrl"]
        assert result["state"]

    async def test_unknown_connection_raises(self, db_session, auth_context):
        service = MetaOAuthService(db_session)
        with pytest.raises(MetaOAuthConnectionNotFoundError):
            await service.build_authorization_url(
                workspace_id=auth_context.workspace_id,
                connection_id=uuid4(),
                redirect_uri="https://app.example/ig/callback",
            )


@pytest.mark.asyncio
class TestHandleCallbackGateClosed:
    async def test_gate_closed_is_honest_and_writes_no_token(
        self, db_session, auth_context, monkeypatch
    ):
        monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
        connection = await _connection(db_session, auth_context.workspace_id)
        service = MetaOAuthService(db_session)
        state, _ = sign_oauth_state(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            redirect_uri="https://app.example/ig/callback",
        )
        result = await service.handle_callback(
            workspace_id=auth_context.workspace_id, code="fake-code", state=state
        )
        assert result["status"] == "oauth_prepared"
        assert connection.access_token_encrypted is None
        assert connection.capability_matrix["oauth"]["oauthStatus"] == "oauth_prepared"
        # nunca grava o código/estado bruto como token
        assert "fake-code" not in str(connection.capability_matrix)

    async def test_state_from_other_workspace_rejected(self, db_session, auth_context, monkeypatch):
        monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
        connection = await _connection(db_session, auth_context.workspace_id)
        service = MetaOAuthService(db_session)
        state, _ = sign_oauth_state(
            workspace_id=uuid4(),
            connection_id=connection.id,
            redirect_uri="https://app.example/ig/callback",
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await service.handle_callback(
                workspace_id=auth_context.workspace_id, code="fake-code", state=state
            )

    async def test_expired_state_rejected_with_400(self, db_session, auth_context, monkeypatch):
        monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
        connection = await _connection(db_session, auth_context.workspace_id)
        service = MetaOAuthService(db_session)
        state, _ = sign_oauth_state(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            redirect_uri="https://app.example/ig/callback",
            now=datetime.now(UTC) - timedelta(seconds=600),
            ttl_seconds=300,
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as raised:
            await service.handle_callback(
                workspace_id=auth_context.workspace_id, code="fake-code", state=state
            )
        assert raised.value.status_code == 400


@pytest.mark.asyncio
class TestRefreshDueTokens:
    async def test_refresh_selects_only_expiring_connections(
        self, db_session, auth_context, monkeypatch
    ):
        monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
        soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        far = (datetime.now(UTC) + timedelta(days=45)).isoformat()

        expiring = await _connection(
            db_session,
            auth_context.workspace_id,
            access_token_encrypted="enc-token-1",
            capability_matrix={"oauth": {"expiresAt": soon}},
        )
        healthy = await _connection(
            db_session,
            auth_context.workspace_id,
            access_token_encrypted="enc-token-2",
            capability_matrix={"oauth": {"expiresAt": far}},
        )
        no_token = await _connection(db_session, auth_context.workspace_id)

        service = MetaOAuthService(db_session)
        outcome = await service.refresh_due_tokens()

        assert outcome["due"] == 1
        assert outcome["prepared"] == 1
        assert outcome["skipped"] >= 1
        assert expiring.capability_matrix["oauth"]["oauthStatus"] == "refresh_prepared"
        assert "oauthStatus" not in healthy.capability_matrix.get("oauth", {})
        assert no_token.access_token_encrypted is None

    async def test_refresh_never_persists_token_in_clear(
        self, db_session, auth_context, monkeypatch
    ):
        monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
        soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        connection = await _connection(
            db_session,
            auth_context.workspace_id,
            access_token_encrypted="enc-token-1",
            capability_matrix={"oauth": {"expiresAt": soon}},
        )
        service = MetaOAuthService(db_session)
        await service.refresh_due_tokens()
        assert connection.access_token_encrypted == "enc-token-1"
        assert "IGAA" not in str(connection.capability_matrix)


class TestConnectionTokenStatus:
    def test_not_connected(self):
        connection = MetaChannelConnection(
            workspace_id=uuid4(),
            channel_type=MetaChannelType.instagram,
            capability_matrix={},
        )
        service = MetaOAuthService.__new__(MetaOAuthService)
        status_payload = service.connection_token_status(connection)
        assert status_payload["state"] == "not_connected"
        assert status_payload["oauthStatus"] is None

    def test_configured_when_token_present_and_far_from_expiry(self):
        far = (datetime.now(UTC) + timedelta(days=45)).isoformat()
        connection = MetaChannelConnection(
            workspace_id=uuid4(),
            channel_type=MetaChannelType.instagram,
            access_token_encrypted="enc-token",
            capability_matrix={"oauth": {"expiresAt": far}},
        )
        service = MetaOAuthService.__new__(MetaOAuthService)
        status_payload = service.connection_token_status(connection)
        assert status_payload["state"] == "configured"
        assert status_payload["expiresInDays"] and status_payload["expiresInDays"] > 30

    def test_expiring_soon(self):
        soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        connection = MetaChannelConnection(
            workspace_id=uuid4(),
            channel_type=MetaChannelType.instagram,
            access_token_encrypted="enc-token",
            capability_matrix={"oauth": {"expiresAt": soon}},
        )
        service = MetaOAuthService.__new__(MetaOAuthService)
        status_payload = service.connection_token_status(connection)
        assert status_payload["state"] == "expiring"

    def test_oauth_prepared_without_token(self):
        connection = MetaChannelConnection(
            workspace_id=uuid4(),
            channel_type=MetaChannelType.instagram,
            capability_matrix={"oauth": {"oauthStatus": "oauth_prepared"}},
        )
        service = MetaOAuthService.__new__(MetaOAuthService)
        status_payload = service.connection_token_status(connection)
        assert status_payload["state"] == "oauth_prepared"

    def test_never_leaks_raw_token_field(self):
        connection = MetaChannelConnection(
            workspace_id=uuid4(),
            channel_type=MetaChannelType.instagram,
            access_token_encrypted="super-secret-token",
            capability_matrix={},
        )
        service = MetaOAuthService.__new__(MetaOAuthService)
        status_payload = service.connection_token_status(connection)
        assert "super-secret-token" not in str(status_payload)


async def _page_connection(db_session, workspace_id, **overrides) -> MetaChannelConnection:
    defaults = dict(
        workspace_id=workspace_id,
        channel_type=MetaChannelType.messenger,
        external_account_id=f"page-{uuid4()}",
        account_name="Página Escritório MV",
        scopes=[],
        capability_matrix={},
        status=MetaConnectionStatus.pending,
        webhook_registered=False,
    )
    defaults.update(overrides)
    connection = MetaChannelConnection(**defaults)
    db_session.add(connection)
    await db_session.flush()
    return connection


@pytest.mark.asyncio
class TestBuildAuthorizationUrlPage:
    async def test_builds_facebook_login_for_business_url_with_page_scopes(
        self, db_session, auth_context
    ):
        connection = await _page_connection(db_session, auth_context.workspace_id)
        service = MetaOAuthService(db_session)
        result = await service.build_authorization_url(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            redirect_uri="https://app.example/page/callback",
            channel_type=MetaChannelType.messenger,
        )
        assert result["scopes"] == list(PAGE_OAUTH_SCOPES)
        assert "pages_messaging" in result["authorizationUrl"]
        assert "pages_manage_metadata" in result["authorizationUrl"]
        assert "pages_read_engagement" in result["authorizationUrl"]
        assert result["authorizationUrl"].startswith("https://www.facebook.com/")
        assert result["state"]

    async def test_unknown_page_connection_raises(self, db_session, auth_context):
        service = MetaOAuthService(db_session)
        with pytest.raises(MetaOAuthConnectionNotFoundError):
            await service.build_authorization_url(
                workspace_id=auth_context.workspace_id,
                connection_id=uuid4(),
                redirect_uri="https://app.example/page/callback",
                channel_type=MetaChannelType.messenger,
            )

    async def test_instagram_connection_id_not_visible_under_messenger_channel(
        self, db_session, auth_context
    ):
        ig_connection = await _connection(db_session, auth_context.workspace_id)
        service = MetaOAuthService(db_session)
        with pytest.raises(MetaOAuthConnectionNotFoundError):
            await service.build_authorization_url(
                workspace_id=auth_context.workspace_id,
                connection_id=ig_connection.id,
                redirect_uri="https://app.example/page/callback",
                channel_type=MetaChannelType.messenger,
            )


@pytest.mark.asyncio
class TestHandleCallbackGateClosedPage:
    async def test_gate_closed_is_honest_and_writes_no_token(
        self, db_session, auth_context, monkeypatch
    ):
        monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
        connection = await _page_connection(db_session, auth_context.workspace_id)
        service = MetaOAuthService(db_session)
        state, _ = sign_oauth_state(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            redirect_uri="https://app.example/page/callback",
            channel_type=MetaChannelType.messenger,
        )
        result = await service.handle_callback(
            workspace_id=auth_context.workspace_id,
            code="fake-code",
            state=state,
            channel_type=MetaChannelType.messenger,
        )
        assert result["status"] == "oauth_prepared"
        assert connection.access_token_encrypted is None
        assert connection.capability_matrix["oauth"]["oauthStatus"] == "oauth_prepared"
        assert "fake-code" not in str(connection.capability_matrix)

    async def test_state_from_instagram_channel_rejected_on_messenger_callback(
        self, db_session, auth_context, monkeypatch
    ):
        monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
        connection = await _page_connection(db_session, auth_context.workspace_id)
        service = MetaOAuthService(db_session)
        state, _ = sign_oauth_state(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            redirect_uri="https://app.example/page/callback",
            channel_type=MetaChannelType.instagram,
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as raised:
            await service.handle_callback(
                workspace_id=auth_context.workspace_id,
                code="fake-code",
                state=state,
                channel_type=MetaChannelType.messenger,
            )
        assert raised.value.status_code == 400


@pytest.mark.asyncio
class TestRefreshDueTokensPage:
    async def test_refresh_scoped_to_messenger_channel_only(
        self, db_session, auth_context, monkeypatch
    ):
        monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: False)
        soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()

        page_expiring = await _page_connection(
            db_session,
            auth_context.workspace_id,
            access_token_encrypted="enc-page-1",
            capability_matrix={"oauth": {"expiresAt": soon}},
        )
        ig_expiring = await _connection(
            db_session,
            auth_context.workspace_id,
            access_token_encrypted="enc-ig-1",
            capability_matrix={"oauth": {"expiresAt": soon}},
        )

        service = MetaOAuthService(db_session)
        outcome = await service.refresh_due_tokens(channel_type=MetaChannelType.messenger)

        assert outcome["due"] == 1
        assert outcome["prepared"] == 1
        assert page_expiring.capability_matrix["oauth"]["oauthStatus"] == "refresh_prepared"
        # A conexão Instagram não é tocada pelo refresh de Página.
        assert "oauthStatus" not in ig_expiring.capability_matrix.get("oauth", {}) or (
            ig_expiring.capability_matrix["oauth"].get("oauthStatus") != "refresh_prepared"
        )


class TestConnectionTokenStatusPage:
    def test_not_connected(self):
        connection = MetaChannelConnection(
            workspace_id=uuid4(),
            channel_type=MetaChannelType.messenger,
            capability_matrix={},
        )
        service = MetaOAuthService.__new__(MetaOAuthService)
        status_payload = service.connection_token_status(connection)
        assert status_payload["state"] == "not_connected"

    def test_never_leaks_raw_token_field(self):
        connection = MetaChannelConnection(
            workspace_id=uuid4(),
            channel_type=MetaChannelType.messenger,
            access_token_encrypted="super-secret-page-token",
            capability_matrix={},
        )
        service = MetaOAuthService.__new__(MetaOAuthService)
        status_payload = service.connection_token_status(connection)
        assert "super-secret-page-token" not in str(status_payload)


def _meta_http_factory(monkeypatch, handler):
    original_client = httpx.AsyncClient
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        meta_oauth_service_module.httpx,
        "AsyncClient",
        lambda **_kwargs: original_client(transport=httpx.MockTransport(recording_handler)),
    )
    return requests


def _configure_meta_oauth(monkeypatch) -> None:
    monkeypatch.setattr(meta_oauth_service_module, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(meta_oauth_service_module.settings, "META_APP_ID", "meta-app-id")
    monkeypatch.setattr(meta_oauth_service_module.settings, "META_APP_SECRET", "meta-app-secret")
    monkeypatch.setattr(
        meta_oauth_service_module.settings,
        "INTEGRATION_SECRET_ENCRYPTION_KEY",
        "meta-oauth-tests-dedicated-encryption-key-2026",
    )


@pytest.mark.asyncio
class TestMetaOAuthRealExchange:
    async def test_instagram_exchanges_and_encrypts_long_lived_token(
        self, db_session, auth_context, monkeypatch
    ):
        _configure_meta_oauth(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.instagram.com":
                return httpx.Response(200, json={"access_token": "short-ig", "user_id": "42"})
            return httpx.Response(
                200,
                json={"access_token": "long-ig-secret", "expires_in": 5_184_000},
            )

        requests = _meta_http_factory(monkeypatch, handler)
        connection = await _connection(db_session, auth_context.workspace_id)
        state, _ = sign_oauth_state(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            redirect_uri="https://app.example/ig/callback",
        )

        result = await MetaOAuthService(db_session).handle_callback(
            workspace_id=auth_context.workspace_id,
            code="authorization-code",
            state=state,
        )

        from app.services.integration_secrets import IntegrationSecretCipher

        assert result["status"] == "connected"
        assert connection.status is MetaConnectionStatus.connected
        assert connection.access_token_encrypted != "long-ig-secret"
        assert (
            IntegrationSecretCipher().decrypt(connection.access_token_encrypted) == "long-ig-secret"
        )
        assert connection.capability_matrix["oauth"]["tokenKind"] == "long_lived"
        assert "long-ig-secret" not in str(connection.capability_matrix)
        assert [request.url.host for request in requests] == [
            "api.instagram.com",
            "graph.instagram.com",
        ]

    async def test_messenger_uses_graph_exchange_and_never_returns_token(
        self, db_session, auth_context, monkeypatch
    ):
        _configure_meta_oauth(monkeypatch)
        call_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json={"access_token": "short-user"})
            if call_count == 2:
                return httpx.Response(
                    200,
                    json={"access_token": "long-user-secret", "expires_in": 5_184_000},
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "page-123",
                            "name": "Página Escritório MV",
                            "access_token": "long-page-secret",
                        }
                    ]
                },
            )

        requests = _meta_http_factory(monkeypatch, handler)
        connection = await _page_connection(db_session, auth_context.workspace_id)
        state, _ = sign_oauth_state(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            redirect_uri="https://app.example/page/callback",
            channel_type=MetaChannelType.messenger,
        )

        result = await MetaOAuthService(db_session).handle_callback(
            workspace_id=auth_context.workspace_id,
            code="authorization-code",
            state=state,
            channel_type=MetaChannelType.messenger,
        )

        assert result["status"] == "connected"
        assert "access_token" not in result
        assert len(requests) == 3
        assert all(request.url.host == "graph.facebook.com" for request in requests)
        assert connection.page_id == "page-123"
        assert connection.refresh_token_encrypted is not None

    async def test_refresh_replaces_expiring_token_and_tracks_expiry(
        self, db_session, auth_context, monkeypatch
    ):
        _configure_meta_oauth(monkeypatch)
        from app.services.integration_secrets import IntegrationSecretCipher

        cipher = IntegrationSecretCipher()
        connection = await _connection(
            db_session,
            auth_context.workspace_id,
            access_token_encrypted=cipher.encrypt("old-long-token"),
            capability_matrix={
                "oauth": {"expiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
            },
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/refresh_access_token"
            return httpx.Response(
                200,
                json={"access_token": "renewed-long-token", "expires_in": 5_184_000},
            )

        _meta_http_factory(monkeypatch, handler)
        result = await MetaOAuthService(db_session).refresh_due_tokens()

        assert result["refreshed"] == 1
        assert result["failed"] == 0
        assert cipher.decrypt(connection.access_token_encrypted) == "renewed-long-token"
        assert connection.capability_matrix["oauth"]["oauthStatus"] == "refreshed"
        assert "renewed-long-token" not in str(connection.capability_matrix)

    async def test_messenger_refreshes_user_token_then_derives_page_token(
        self, db_session, auth_context, monkeypatch
    ):
        _configure_meta_oauth(monkeypatch)
        from app.services.integration_secrets import IntegrationSecretCipher

        cipher = IntegrationSecretCipher()
        connection = await _page_connection(
            db_session,
            auth_context.workspace_id,
            page_id="page-123",
            access_token_encrypted=cipher.encrypt("old-page-token"),
            refresh_token_encrypted=cipher.encrypt("old-user-token"),
            capability_matrix={
                "oauth": {"expiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
            },
        )
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                assert request.url.path.endswith("/oauth/access_token")
                return httpx.Response(
                    200,
                    json={"access_token": "renewed-user-token", "expires_in": 5_184_000},
                )
            assert request.url.path.endswith("/page-123")
            return httpx.Response(
                200,
                json={
                    "id": "page-123",
                    "name": "Página Escritório MV",
                    "access_token": "renewed-page-token",
                },
            )

        _meta_http_factory(monkeypatch, handler)
        result = await MetaOAuthService(db_session).refresh_due_tokens(
            channel_type=MetaChannelType.messenger
        )

        assert result["refreshed"] == 1
        assert cipher.decrypt(connection.access_token_encrypted) == "renewed-page-token"
        assert cipher.decrypt(connection.refresh_token_encrypted) == "renewed-user-token"
