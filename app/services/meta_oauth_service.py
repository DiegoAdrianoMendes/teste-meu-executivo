"""OAuth Instagram Login / Facebook Login for Business (Página): URL de
autorização, callback e refresh de tokens.

Escopo (F3-I2, docs/PLANO_META_MAXIMO_2026-07-23.md):
- Instagram Login: `instagram_business_basic`, `instagram_business_manage_messages`,
  `instagram_business_manage_comments`.
- Facebook Login for Business (Página, sprint OAuth de Página): `pages_messaging`,
  `pages_manage_metadata`, `pages_read_engagement`.
- `state` anti-CSRF assinado com HMAC de subchave derivada de
  `settings.SECRET_KEY`, TTL curto — MESMO esquema para os dois canais.
- A troca code→token real e o refresh só executam quando
  `external_delivery_allowed()` estiver aberto (gate fail-closed do KLM Hub).
  Com o gate fechado, o fluxo é honesto: grava a intenção (`oauth_prepared`),
  audita e explica — nunca finge uma conexão que não existe.
- Tokens nunca aparecem em claro em logs, respostas ou auditoria.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.models.klm_operations import KlmAuditEvent
from app.models.meta_channels import (
    MetaChannelConnection,
    MetaChannelType,
    MetaConnectionStatus,
)

INSTAGRAM_OAUTH_SCOPES: tuple[str, ...] = (
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
)

PAGE_OAUTH_SCOPES: tuple[str, ...] = (
    "pages_messaging",
    "pages_manage_metadata",
    "pages_read_engagement",
)

AUTHORIZATION_BASE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_EXCHANGE_URL = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
INSTAGRAM_REFRESH_TOKEN_URL = "https://graph.instagram.com/refresh_access_token"

# Facebook Login for Business — usado para conectar a Página (Messenger).
PAGE_AUTHORIZATION_BASE_URL = (
    f"https://www.facebook.com/{settings.META_GRAPH_API_VERSION}/dialog/oauth"
)
PAGE_TOKEN_EXCHANGE_URL = (
    f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/oauth/access_token"
)
PAGE_GRAPH_BASE_URL = f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}"

_CHANNEL_SCOPES: dict[MetaChannelType, tuple[str, ...]] = {
    MetaChannelType.instagram: INSTAGRAM_OAUTH_SCOPES,
    MetaChannelType.messenger: PAGE_OAUTH_SCOPES,
}

_CHANNEL_AUTHORIZATION_BASE_URL: dict[MetaChannelType, str] = {
    MetaChannelType.instagram: AUTHORIZATION_BASE_URL,
    MetaChannelType.messenger: PAGE_AUTHORIZATION_BASE_URL,
}

STATE_TTL_SECONDS = 300
REFRESH_WINDOW_DAYS = 7
OAUTH_METADATA_KEY = "oauth"
OAUTH_HTTP_TIMEOUT_SECONDS = 20.0


class MetaOAuthStateError(ValueError):
    """State ausente, malformado, com assinatura inválida, expirado ou de outra conexão."""


class MetaOAuthConnectionNotFoundError(ValueError):
    """Conexão informada não existe (ou não pertence ao workspace)."""


def _state_signing_key() -> bytes:
    # Subchave derivada: nunca reutiliza o SECRET_KEY bruto para assinar state OAuth.
    material = f"{settings.SECRET_KEY}|meta_oauth_state".encode()
    return hashlib.sha256(material).digest()


def _b64encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True)
class MetaOAuthStatePayload:
    workspace_id: UUID
    connection_id: UUID
    redirect_uri: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    channel_type: MetaChannelType


def sign_oauth_state(
    *,
    workspace_id: UUID,
    connection_id: UUID,
    redirect_uri: str,
    channel_type: MetaChannelType = MetaChannelType.instagram,
    now: datetime | None = None,
    ttl_seconds: int = STATE_TTL_SECONDS,
) -> tuple[str, datetime]:
    reference = now or datetime.now(UTC)
    expires_at = reference + timedelta(seconds=ttl_seconds)
    body = {
        "wid": str(workspace_id),
        "cid": str(connection_id),
        "ru": redirect_uri,
        "ct": channel_type.value,
        "nonce": secrets.token_urlsafe(16),
        "iat": reference.timestamp(),
        "exp": expires_at.timestamp(),
    }
    encoded = _b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_state_signing_key(), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}", expires_at


def verify_oauth_state(state: str, *, now: datetime | None = None) -> MetaOAuthStatePayload:
    reference = now or datetime.now(UTC)
    try:
        encoded, signature = state.split(".", 1)
    except ValueError as error:
        raise MetaOAuthStateError("Formato de state inválido.") from error

    expected_signature = hmac.new(
        _state_signing_key(), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise MetaOAuthStateError("Assinatura do state não confere (possível CSRF).")

    try:
        body = json.loads(_b64decode(encoded))
    except (ValueError, UnicodeDecodeError) as error:
        raise MetaOAuthStateError("Payload do state corrompido.") from error

    try:
        expires_at = datetime.fromtimestamp(float(body["exp"]), tz=UTC)
        issued_at = datetime.fromtimestamp(float(body["iat"]), tz=UTC)
        # Compatibilidade: states emitidos antes deste sprint não trazem "ct".
        channel_raw = body.get("ct", MetaChannelType.instagram.value)
        payload = MetaOAuthStatePayload(
            workspace_id=UUID(body["wid"]),
            connection_id=UUID(body["cid"]),
            redirect_uri=str(body["ru"]),
            nonce=str(body["nonce"]),
            issued_at=issued_at,
            expires_at=expires_at,
            channel_type=MetaChannelType(channel_raw),
        )
    except (KeyError, ValueError) as error:
        raise MetaOAuthStateError("Campos obrigatórios ausentes no state.") from error

    if reference > payload.expires_at:
        raise MetaOAuthStateError("State expirado; reinicie o fluxo de conexão.")
    return payload


def _token_metadata(connection: MetaChannelConnection) -> dict[str, Any]:
    matrix = connection.capability_matrix or {}
    meta = matrix.get(OAUTH_METADATA_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def _write_token_metadata(connection: MetaChannelConnection, meta: dict[str, Any]) -> None:
    matrix = dict(connection.capability_matrix or {})
    matrix[OAUTH_METADATA_KEY] = meta
    connection.capability_matrix = matrix


class MetaOAuthService:
    """OAuth Instagram Login / Facebook Login for Business (Página) para
    conexões `meta_channel_connections`."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build_authorization_url(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        redirect_uri: str,
        channel_type: MetaChannelType = MetaChannelType.instagram,
    ) -> dict[str, Any]:
        connection = await self._get_connection(workspace_id, connection_id, channel_type)
        scopes = _CHANNEL_SCOPES[channel_type]
        base_url = _CHANNEL_AUTHORIZATION_BASE_URL[channel_type]
        state, expires_at = sign_oauth_state(
            workspace_id=workspace_id,
            connection_id=connection_id,
            redirect_uri=redirect_uri,
            channel_type=channel_type,
        )
        params = {
            "client_id": settings.META_APP_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(scopes),
            "state": state,
        }
        query = "&".join(
            f"{key}={_url_quote(value)}" for key, value in params.items() if value != ""
        )
        authorization_url = f"{base_url}?{query}"

        meta = _token_metadata(connection)
        meta.update(
            {
                "oauthStatus": "authorize_url_issued",
                "scopes": list(scopes),
                "stateIssuedAt": datetime.now(UTC).isoformat(),
                "stateExpiresAt": expires_at.isoformat(),
            }
        )
        _write_token_metadata(connection, meta)
        await self.db.flush()
        await self._audit(
            workspace_id=workspace_id,
            connection=connection,
            event_type="meta_oauth.authorize_url_issued",
            summary=f"URL de autorização {_channel_label(channel_type)} gerada.",
            extra={
                "redirectUri": redirect_uri,
                "scopes": list(scopes),
                "channelType": channel_type.value,
            },
        )
        return {
            "authorizationUrl": authorization_url,
            "state": state,
            "expiresAt": expires_at,
            "scopes": list(scopes),
        }

    async def handle_callback(
        self,
        *,
        workspace_id: UUID,
        code: str,
        state: str,
        channel_type: MetaChannelType = MetaChannelType.instagram,
    ) -> dict[str, Any]:
        try:
            payload = verify_oauth_state(state)
        except MetaOAuthStateError as error:
            logger.warning("meta_oauth.state_rejected", error_type=safe_error_type(error))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "META-OAUTH-001", "message": str(error)},
            ) from error

        if payload.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "META-OAUTH-002",
                    "message": "State pertence a outro workspace; callback rejeitado.",
                },
            )

        if payload.channel_type != channel_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "META-OAUTH-005",
                    "message": "State pertence a outro canal Meta; callback rejeitado.",
                },
            )

        connection = await self._get_connection(workspace_id, payload.connection_id, channel_type)

        if not external_delivery_allowed():
            meta = _token_metadata(connection)
            meta.update(
                {
                    "oauthStatus": "oauth_prepared",
                    "preparedAt": datetime.now(UTC).isoformat(),
                    "codeReceived": bool(code),
                }
            )
            _write_token_metadata(connection, meta)
            connection.status = MetaConnectionStatus.pending
            connection.last_error = (
                "Callback OAuth recebido e validado (state e CSRF conferidos); a troca "
                "code→token real fica bloqueada até as ações externas do Meu Executivo serem "
                "aprovadas (external_delivery_allowed). Nenhuma chamada de rede foi feita."
            )
            await self.db.flush()
            await self._audit(
                workspace_id=workspace_id,
                connection=connection,
                event_type="meta_oauth.prepared",
                summary=(
                    f"Callback OAuth {_channel_label(channel_type)} recebido; "
                    "troca de token gated (não executada)."
                ),
                extra={
                    "gate": "external_delivery_allowed=false",
                    "channelType": channel_type.value,
                },
            )
            return {
                "status": "oauth_prepared",
                "connectionId": connection.id,
                "message": (
                    "Autorização validada. A troca do código pelo token só é feita quando "
                    "as ações externas estiverem aprovadas para este ambiente."
                ),
            }

        # Com o gate aberto, troca o código pelo token real e cifra antes de persistir.
        return await self._exchange_and_store(
            workspace_id=workspace_id,
            connection=connection,
            code=code,
            redirect_uri=payload.redirect_uri,
        )

    async def _exchange_and_store(
        self,
        *,
        workspace_id: UUID,
        connection: MetaChannelConnection,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        from app.services.integration_secrets import IntegrationSecretCipher

        try:
            cipher = IntegrationSecretCipher()
        except RuntimeError as error:
            logger.error("meta_oauth.cipher_unavailable", error_type=safe_error_type(error))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "META-OAUTH-003",
                    "message": "Cifra de credenciais de integração não configurada.",
                },
            ) from error

        try:
            app_id, app_secret = self._require_provider_credentials()
            async with httpx.AsyncClient(timeout=OAUTH_HTTP_TIMEOUT_SECONDS) as client:
                if connection.channel_type == MetaChannelType.instagram:
                    short_lived = await self._request_json(
                        client,
                        "POST",
                        TOKEN_EXCHANGE_URL,
                        data={
                            "client_id": app_id,
                            "client_secret": app_secret,
                            "grant_type": "authorization_code",
                            "redirect_uri": redirect_uri,
                            "code": code,
                        },
                    )
                    short_token = self._require_access_token(short_lived)
                    long_lived = await self._request_json(
                        client,
                        "GET",
                        INSTAGRAM_LONG_LIVED_TOKEN_URL,
                        params={
                            "grant_type": "ig_exchange_token",
                            "client_secret": app_secret,
                            "access_token": short_token,
                        },
                    )
                else:
                    short_lived = await self._request_json(
                        client,
                        "GET",
                        PAGE_TOKEN_EXCHANGE_URL,
                        params={
                            "client_id": app_id,
                            "client_secret": app_secret,
                            "redirect_uri": redirect_uri,
                            "code": code,
                        },
                    )
                    short_token = self._require_access_token(short_lived)
                    long_lived = await self._request_json(
                        client,
                        "GET",
                        PAGE_TOKEN_EXCHANGE_URL,
                        params={
                            "grant_type": "fb_exchange_token",
                            "client_id": app_id,
                            "client_secret": app_secret,
                            "fb_exchange_token": short_token,
                        },
                    )
                    long_user_token = self._require_access_token(long_lived)
                    page_payload = await self._resolve_page_token(
                        client,
                        connection=connection,
                        user_access_token=long_user_token,
                    )
                    long_lived = {
                        **page_payload,
                        "expires_in": long_lived.get("expires_in"),
                    }

            access_token = self._require_access_token(long_lived)
            now = datetime.now(UTC)
            expires_at = now + timedelta(seconds=self._expires_in_seconds(long_lived))
            connection.access_token_encrypted = cipher.encrypt(access_token)
            if connection.channel_type == MetaChannelType.messenger:
                connection.refresh_token_encrypted = cipher.encrypt(long_user_token)
            connection.scopes = list(_CHANNEL_SCOPES[connection.channel_type])
            connection.status = MetaConnectionStatus.connected
            connection.connected_at = now
            connection.disconnected_at = None
            connection.last_error = None
            meta = _token_metadata(connection)
            meta.update(
                {
                    "oauthStatus": "connected",
                    "scopes": list(connection.scopes),
                    "expiresAt": expires_at.isoformat(),
                    "tokenStoredAt": now.isoformat(),
                    "tokenKind": "long_lived",
                }
            )
            external_user_id = long_lived.get("user_id") or short_lived.get("user_id")
            if external_user_id:
                meta["externalUserId"] = str(external_user_id)
            _write_token_metadata(connection, meta)
            await self.db.flush()
            await self._audit(
                workspace_id=workspace_id,
                connection=connection,
                event_type="meta_oauth.connected",
                summary=(
                    f"Conexão {_channel_label(connection.channel_type)} autorizada "
                    "com token de longa duração."
                ),
                extra={
                    "channelType": connection.channel_type.value,
                    "expiresAt": expires_at.isoformat(),
                    "scopes": list(connection.scopes),
                },
            )
            return {
                "status": "connected",
                "connectionId": connection.id,
                "expiresAt": expires_at,
                "scopes": list(connection.scopes),
                "message": "Conexão Meta autorizada e credencial armazenada de forma cifrada.",
            }
        except HTTPException as error:
            connection.status = MetaConnectionStatus.error
            connection.last_error = "A Meta não concluiu a autorização OAuth. Reinicie a conexão."
            meta = _token_metadata(connection)
            meta.update(
                {
                    "oauthStatus": "exchange_failed",
                    "exchangeFailedAt": datetime.now(UTC).isoformat(),
                }
            )
            _write_token_metadata(connection, meta)
            await self.db.flush()
            await self._audit(
                workspace_id=workspace_id,
                connection=connection,
                event_type="meta_oauth.exchange_failed",
                summary="Troca OAuth Meta falhou sem persistir credencial em claro.",
                extra={
                    "channelType": connection.channel_type.value,
                    "errorCode": "META-OAUTH-006",
                },
            )
            raise error

    async def refresh_due_tokens(
        self,
        *,
        channel_type: MetaChannelType = MetaChannelType.instagram,
        window: timedelta = timedelta(days=REFRESH_WINDOW_DAYS),
        now: datetime | None = None,
    ) -> dict[str, int]:
        reference = now or datetime.now(UTC)
        result = await self.db.execute(
            select(MetaChannelConnection).where(
                MetaChannelConnection.channel_type == channel_type,
                MetaChannelConnection.access_token_encrypted.is_not(None),
            )
        )
        candidates = result.scalars().all()
        due = 0
        skipped = 0
        prepared = 0
        refreshed = 0
        failed = 0
        for connection in candidates:
            meta = _token_metadata(connection)
            expires_at_raw = meta.get("expiresAt")
            if not expires_at_raw:
                skipped += 1
                continue
            try:
                expires_at = datetime.fromisoformat(str(expires_at_raw))
            except ValueError:
                skipped += 1
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at - reference > window:
                skipped += 1
                continue

            due += 1
            if not external_delivery_allowed():
                meta["oauthStatus"] = "refresh_prepared"
                meta["refreshPreparedAt"] = reference.isoformat()
                _write_token_metadata(connection, meta)
                await self._audit(
                    workspace_id=connection.workspace_id,
                    connection=connection,
                    event_type="meta_oauth.refresh_prepared",
                    summary=(
                        f"Refresh de token {_channel_label(channel_type)} identificado "
                        "e gated (não executado)."
                    ),
                    extra={"expiresAt": expires_at.isoformat(), "channelType": channel_type.value},
                )
                prepared += 1
                continue

            try:
                await self._refresh_connection_token(connection=connection, reference=reference)
                refreshed += 1
            except HTTPException:
                failed += 1

        await self.db.flush()
        return {
            "due": due,
            "prepared": prepared,
            "refreshed": refreshed,
            "failed": failed,
            "skipped": skipped,
        }

    async def _refresh_connection_token(
        self,
        *,
        connection: MetaChannelConnection,
        reference: datetime,
    ) -> None:
        from app.services.integration_secrets import IntegrationSecretCipher

        try:
            cipher = IntegrationSecretCipher()
        except RuntimeError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "META-OAUTH-003",
                    "message": "Cifra de credenciais de integração não configurada.",
                },
            ) from error
        current_token = cipher.decrypt(connection.access_token_encrypted)
        if connection.channel_type == MetaChannelType.messenger:
            current_token = cipher.decrypt(connection.refresh_token_encrypted)
        if not current_token:
            await self._mark_refresh_failed(connection, reference, "META-OAUTH-007")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "META-OAUTH-007",
                    "message": (
                        "Credencial Meta armazenada não pôde ser recuperada; reconecte o canal."
                    ),
                },
            )

        try:
            app_id, app_secret = self._require_provider_credentials()
            async with httpx.AsyncClient(timeout=OAUTH_HTTP_TIMEOUT_SECONDS) as client:
                if connection.channel_type == MetaChannelType.instagram:
                    payload = await self._request_json(
                        client,
                        "GET",
                        INSTAGRAM_REFRESH_TOKEN_URL,
                        params={
                            "grant_type": "ig_refresh_token",
                            "access_token": current_token,
                        },
                    )
                else:
                    long_user = await self._request_json(
                        client,
                        "GET",
                        PAGE_TOKEN_EXCHANGE_URL,
                        params={
                            "grant_type": "fb_exchange_token",
                            "client_id": app_id,
                            "client_secret": app_secret,
                            "fb_exchange_token": current_token,
                        },
                    )
                    long_user_token = self._require_access_token(long_user)
                    payload = await self._resolve_page_token(
                        client,
                        connection=connection,
                        user_access_token=long_user_token,
                    )
                    payload["expires_in"] = long_user.get("expires_in")
            refreshed_token = self._require_access_token(payload)
            expires_at = reference + timedelta(seconds=self._expires_in_seconds(payload))
            connection.access_token_encrypted = cipher.encrypt(refreshed_token)
            if connection.channel_type == MetaChannelType.messenger:
                connection.refresh_token_encrypted = cipher.encrypt(long_user_token)
            connection.status = MetaConnectionStatus.connected
            connection.last_error = None
            meta = _token_metadata(connection)
            meta.update(
                {
                    "oauthStatus": "refreshed",
                    "refreshedAt": reference.isoformat(),
                    "expiresAt": expires_at.isoformat(),
                    "tokenKind": "long_lived",
                }
            )
            _write_token_metadata(connection, meta)
            await self.db.flush()
            await self._audit(
                workspace_id=connection.workspace_id,
                connection=connection,
                event_type="meta_oauth.refreshed",
                summary=f"Token {_channel_label(connection.channel_type)} renovado.",
                extra={
                    "channelType": connection.channel_type.value,
                    "expiresAt": expires_at.isoformat(),
                },
            )
        except HTTPException:
            await self._mark_refresh_failed(connection, reference, "META-OAUTH-006")
            raise

    async def _mark_refresh_failed(
        self,
        connection: MetaChannelConnection,
        reference: datetime,
        error_code: str,
    ) -> None:
        connection.status = MetaConnectionStatus.error
        connection.last_error = "A renovação da credencial Meta falhou; reconecte o canal."
        meta = _token_metadata(connection)
        meta.update(
            {
                "oauthStatus": "refresh_failed",
                "refreshFailedAt": reference.isoformat(),
            }
        )
        _write_token_metadata(connection, meta)
        await self.db.flush()
        await self._audit(
            workspace_id=connection.workspace_id,
            connection=connection,
            event_type="meta_oauth.refresh_failed",
            summary="Renovação OAuth Meta falhou sem expor a credencial.",
            extra={
                "channelType": connection.channel_type.value,
                "errorCode": error_code,
            },
        )

    @staticmethod
    def _require_provider_credentials() -> tuple[str, str]:
        app_id = settings.META_APP_ID.strip()
        app_secret = (settings.META_APP_SECRET or settings.WHATSAPP_APP_SECRET).strip()
        if not app_id or not app_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "META-OAUTH-008",
                    "message": "App ID ou App Secret da Meta não está configurado.",
                },
            )
        return app_id, app_secret

    @staticmethod
    async def _resolve_page_token(
        client: httpx.AsyncClient,
        *,
        connection: MetaChannelConnection,
        user_access_token: str,
    ) -> dict[str, Any]:
        if connection.page_id:
            page = await MetaOAuthService._request_json(
                client,
                "GET",
                f"{PAGE_GRAPH_BASE_URL}/{connection.page_id}",
                params={
                    "fields": "id,name,access_token",
                    "access_token": user_access_token,
                },
            )
        else:
            accounts = await MetaOAuthService._request_json(
                client,
                "GET",
                f"{PAGE_GRAPH_BASE_URL}/me/accounts",
                params={
                    "fields": "id,name,access_token",
                    "limit": 100,
                    "access_token": user_access_token,
                },
            )
            available = accounts.get("data")
            pages = available if isinstance(available, list) else []
            if len(pages) != 1 or not isinstance(pages[0], dict):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "META-OAUTH-009",
                        "message": (
                            "Informe a Página do Facebook antes de autorizar. "
                            "A conta Meta não retornou uma única Página elegível."
                        ),
                    },
                )
            page = pages[0]

        page_token = MetaOAuthService._require_access_token(page)
        page_id = str(page.get("id") or connection.page_id or "").strip()
        if not page_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "META-OAUTH-006",
                    "message": "A Meta não identificou a Página autorizada.",
                },
            )
        connection.page_id = page_id
        connection.external_account_id = page_id
        if page.get("name"):
            connection.account_name = str(page["name"])
        return {"access_token": page_token, "page_id": page_id}

    @staticmethod
    async def _request_json(
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.warning(
                "meta_oauth.provider_request_failed",
                error_type=safe_error_type(error),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "META-OAUTH-006",
                    "message": "A Meta não concluiu a autorização. Reinicie a conexão.",
                },
            ) from error
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "META-OAUTH-006",
                    "message": "A Meta devolveu uma resposta inválida para a autorização.",
                },
            )
        return payload

    @staticmethod
    def _require_access_token(payload: dict[str, Any]) -> str:
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "META-OAUTH-006",
                    "message": "A Meta não devolveu a credencial esperada.",
                },
            )
        return access_token

    @staticmethod
    def _expires_in_seconds(payload: dict[str, Any]) -> int:
        try:
            expires_in = int(payload.get("expires_in") or 60 * 24 * 60 * 60)
        except (TypeError, ValueError):
            expires_in = 60 * 24 * 60 * 60
        return max(60, expires_in)

    def connection_token_status(self, connection: MetaChannelConnection) -> dict[str, Any]:
        meta = _token_metadata(connection)
        has_token = bool(connection.access_token_encrypted)
        expires_at_raw = meta.get("expiresAt")
        expires_in_days: int | None = None
        if expires_at_raw:
            try:
                expires_at = datetime.fromisoformat(str(expires_at_raw))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                expires_in_days = max(0, (expires_at - datetime.now(UTC)).days)
            except ValueError:
                expires_in_days = None

        if has_token and expires_in_days is not None:
            state = "expiring" if expires_in_days <= REFRESH_WINDOW_DAYS else "configured"
        elif has_token:
            state = "configured"
        elif meta.get("oauthStatus") in {
            "oauth_prepared",
            "authorize_url_issued",
            "refresh_prepared",
        }:
            state = "oauth_prepared"
        else:
            state = "not_connected"

        return {
            "connectionId": connection.id,
            "state": state,
            "expiresInDays": expires_in_days,
            "oauthStatus": meta.get("oauthStatus"),
            "scopes": meta.get("scopes", []),
        }

    async def _get_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        channel_type: MetaChannelType = MetaChannelType.instagram,
    ) -> MetaChannelConnection:
        connection = await self.db.scalar(
            select(MetaChannelConnection).where(
                MetaChannelConnection.workspace_id == workspace_id,
                MetaChannelConnection.id == connection_id,
                MetaChannelConnection.channel_type == channel_type,
            )
        )
        if connection is None:
            raise MetaOAuthConnectionNotFoundError(str(connection_id))
        return connection

    async def _audit(
        self,
        *,
        workspace_id: UUID,
        connection: MetaChannelConnection,
        event_type: str,
        summary: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_type="system",
                actor_label="meta_oauth_service",
                event_type=event_type,
                entity_type="meta_channel_connection",
                entity_id=connection.id,
                summary=summary,
                before_data=None,
                after_data=extra or {},
            )
        )
        await self.db.flush()


def _url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


def _channel_label(channel_type: MetaChannelType) -> str:
    return (
        "Instagram Login"
        if channel_type == MetaChannelType.instagram
        else "Facebook Login for Business (Página)"
    )


__all__ = [
    "INSTAGRAM_OAUTH_SCOPES",
    "PAGE_OAUTH_SCOPES",
    "PAGE_AUTHORIZATION_BASE_URL",
    "PAGE_TOKEN_EXCHANGE_URL",
    "PAGE_GRAPH_BASE_URL",
    "INSTAGRAM_LONG_LIVED_TOKEN_URL",
    "INSTAGRAM_REFRESH_TOKEN_URL",
    "MetaOAuthConnectionNotFoundError",
    "MetaOAuthService",
    "MetaOAuthStateError",
    "REFRESH_WINDOW_DAYS",
    "external_delivery_allowed",
    "sign_oauth_state",
    "verify_oauth_state",
]
