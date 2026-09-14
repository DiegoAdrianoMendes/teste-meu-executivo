"""
Módulo de segurança: geração/validação de JWT e hashing de senhas.
Usa bcrypt diretamente (sem passlib) para compatibilidade com bcrypt >= 4.0.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from time import time_ns
from typing import Any
from uuid import UUID

import bcrypt
import jwt
from jwt import InvalidTokenError

from app.core.config import settings

ALGORITHM = "HS256"
TOKEN_ISSUER = "klm-legal-hub"
TOKEN_VERSION = 2
AUTH_DUMMY_PASSWORD_HASH = (
    "$2b$12$kexpl63tUisHm2fu/9zqdeOAiMvo4z0ljCPgrHDXRaPoKt7v6/yPi"
)
SESSION_TOKEN_ID_PREFIX = "s1"
TOKEN_PURPOSES = frozenset(
    {
        "access",
        "refresh",
        "websocket",
        "local_storage",
        "booking",
        "admin_session",
        "support_access",
        "platform_websocket",
    }
)


def token_audience(purpose: str) -> str:
    if purpose not in TOKEN_PURPOSES:
        raise ValueError("Finalidade de token desconhecida.")
    return f"{TOKEN_ISSUER}:{purpose}"


def derive_token_signing_key(purpose: str) -> bytes:
    """Deriva uma subchave independente sem persistir novos segredos locais."""
    if purpose not in TOKEN_PURPOSES:
        raise ValueError("Finalidade de token desconhecida.")
    context = f"klm-jwt:v{TOKEN_VERSION}:{purpose}".encode("ascii")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), context, hashlib.sha256).digest()


def _token_key_id(purpose: str) -> str:
    return f"klm-{purpose}-v{TOKEN_VERSION}"


def _encode_token(payload: dict[str, Any], purpose: str) -> str:
    if payload.get("token_type") != purpose:
        raise ValueError("A finalidade do token não corresponde ao payload.")
    versioned_payload = {
        **payload,
        "iss": TOKEN_ISSUER,
        "aud": token_audience(purpose),
        "token_version": TOKEN_VERSION,
    }
    return jwt.encode(
        versioned_payload,
        derive_token_signing_key(purpose),
        algorithm=ALGORITHM,
        headers={"kid": _token_key_id(purpose), "typ": "JWT"},
    )


def create_session_token_id(
    subject: str | Any,
    *,
    issued_at_ns: int | None = None,
    nonce: str | None = None,
) -> str:
    """Cria um jti com metadados suficientes para revogação por usuário."""
    resolved_issued_at_ns = time_ns() if issued_at_ns is None else int(issued_at_ns)
    if resolved_issued_at_ns <= 0:
        raise ValueError("A época da sessão deve ser positiva.")
    encoded_subject = (
        base64.urlsafe_b64encode(str(subject).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    return ".".join(
        (
            SESSION_TOKEN_ID_PREFIX,
            encoded_subject,
            str(resolved_issued_at_ns),
            nonce or uuid.uuid4().hex,
        )
    )


def parse_session_token_id(token_id: str | None) -> tuple[str, int, str] | None:
    if not token_id:
        return None
    parts = token_id.split(".", 3)
    if len(parts) != 4 or parts[0] != SESSION_TOKEN_ID_PREFIX:
        return None
    try:
        padding = "=" * (-len(parts[1]) % 4)
        subject = base64.urlsafe_b64decode(f"{parts[1]}{padding}").decode("utf-8")
        issued_at_ns = int(parts[2])
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    nonce = parts[3]
    if not subject or issued_at_ns <= 0 or not nonce:
        return None
    return subject, issued_at_ns, nonce


def _normalize_session_token_id(payload: dict[str, Any]) -> None:
    """Adapta JWTs legados para o mesmo corte de revogação por usuário."""
    if payload.get("token_type", "access") not in {"access", "refresh", "websocket"}:
        return
    token_id = payload.get("jti")
    subject = payload.get("sub")
    issued_at = payload.get("iat")
    parsed = parse_session_token_id(token_id)
    if parsed:
        if subject and parsed[0] != str(subject):
            raise InvalidTokenError(
                "O subject do token não corresponde à família da sessão."
            )
        return
    if not token_id or not subject or issued_at is None:
        return
    try:
        if isinstance(issued_at, datetime):
            issued_at_ns = int(issued_at.timestamp() * 1_000_000_000)
        else:
            issued_at_ns = int(float(issued_at) * 1_000_000_000)
    except (TypeError, ValueError, OverflowError):
        return
    if issued_at_ns <= 0:
        raise InvalidTokenError("A época da sessão no token é inválida.")
    payload["jti"] = create_session_token_id(
        subject,
        issued_at_ns=issued_at_ns,
        nonce=str(token_id),
    )


def create_access_token(
    subject: object,
    expires_delta: timedelta | None = None,
    workspace_id: UUID | None = None,
    role: str | None = None,
    is_internal_staff: bool = False,
    membership_id: UUID | None = None,
    session_issued_at_ns: int | None = None,
    session_id: UUID | None = None,
    token_id: str | None = None,
    auth_epoch: int = 0,
) -> str:
    """Gera um JWT de acesso."""
    now = datetime.now(UTC)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {
        "exp": expire,
        "iat": now,
        "jti": token_id
        or create_session_token_id(subject, issued_at_ns=session_issued_at_ns),
        "token_type": "access",
        "sub": str(subject),
        "workspace_id": str(workspace_id) if workspace_id else None,
        "role": role,
        "is_internal_staff": is_internal_staff,
        "membership_id": str(membership_id) if membership_id else None,
        "session_id": str(session_id) if session_id else None,
        "auth_epoch": int(auth_epoch),
    }
    return _encode_token(to_encode, "access")


def _decode_versioned_token(token: str, purpose: str) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    if header.get("kid") != _token_key_id(purpose):
        raise InvalidTokenError("Identificador de chave incompatível com a finalidade.")
    payload = jwt.decode(
        token,
        derive_token_signing_key(purpose),
        algorithms=[ALGORITHM],
        audience=token_audience(purpose),
        issuer=TOKEN_ISSUER,
        options={
            "require": [
                "aud",
                "exp",
                "iat",
                "iss",
                "jti",
                "sub",
                "token_type",
            ]
        },
    )
    if payload.get("token_version") != TOKEN_VERSION:
        raise InvalidTokenError("Versão de token inválida.")
    if payload.get("token_type") != purpose:
        raise InvalidTokenError("Finalidade de token inválida.")
    return payload


def _decode_legacy_token(token: str) -> dict[str, Any]:
    """Aceita JWTs HS256 já emitidos, sem permitir downgrade de tokens v2."""
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[ALGORITHM],
        options={
            "require": ["exp"],
            "verify_aud": False,
            "verify_iss": False,
        },
    )


def verify_token(token: str) -> dict[str, Any] | None:
    """Valida tokens v2 por finalidade e mantém compatibilidade legada."""
    try:
        header = jwt.get_unverified_header(token)
        if not hmac.compare_digest(str(header.get("alg") or ""), ALGORITHM):
            raise InvalidTokenError("Algoritmo de assinatura inválido.")
        unverified = jwt.decode(token, options={"verify_signature": False})
        purpose = str(unverified.get("token_type") or "access")
        if purpose not in TOKEN_PURPOSES:
            raise InvalidTokenError("Finalidade de token desconhecida.")

        has_v2_marker = any(
            (
                unverified.get("token_version") is not None,
                unverified.get("iss") is not None,
                unverified.get("aud") is not None,
                header.get("kid") is not None,
            )
        )
        payload = (
            _decode_versioned_token(token, purpose)
            if has_v2_marker
            else _decode_legacy_token(token)
        )
        if purpose in {"access", "refresh", "websocket", "admin_session"}:
            auth_epoch = payload.get("auth_epoch")
            if auth_epoch is None and not has_v2_marker and purpose == "access":
                auth_epoch = 0
                payload["auth_epoch"] = auth_epoch
            if not isinstance(auth_epoch, int) or auth_epoch < 0:
                raise InvalidTokenError("Epoca de credencial ausente ou invalida.")
        if purpose == "websocket" and not payload.get("session_id"):
            raise InvalidTokenError("Ticket WebSocket sem familia de sessao.")
        _normalize_session_token_id(payload)
        return payload
    except (InvalidTokenError, TypeError, ValueError):
        return None


def create_refresh_token(
    subject: object,
    workspace_id: UUID | None = None,
    role: str | None = None,
    is_internal_staff: bool = False,
    membership_id: UUID | None = None,
    session_issued_at_ns: int | None = None,
    session_id: UUID | None = None,
    token_id: str | None = None,
    auth_epoch: int = 0,
) -> str:
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "exp": expire,
        "iat": now,
        "jti": token_id
        or create_session_token_id(subject, issued_at_ns=session_issued_at_ns),
        "token_type": "refresh",
        "sub": str(subject),
        "workspace_id": str(workspace_id) if workspace_id else None,
        "role": role,
        "is_internal_staff": is_internal_staff,
        "membership_id": str(membership_id) if membership_id else None,
        "session_id": str(session_id) if session_id else None,
        "auth_epoch": int(auth_epoch),
    }
    return _encode_token(to_encode, "refresh")


def create_websocket_ticket(
    subject: str | Any,
    *,
    workspace_id: UUID,
    membership_id: UUID,
    session_id: UUID,
    auth_epoch: int,
    session_issued_at_ns: int | None = None,
) -> str:
    """Gera uma credencial curta e restrita à abertura do canal em tempo real."""
    now = datetime.now(UTC)
    to_encode = {
        "exp": now + timedelta(seconds=settings.WEBSOCKET_TICKET_EXPIRE_SECONDS),
        "iat": now,
        "jti": create_session_token_id(subject, issued_at_ns=session_issued_at_ns),
        "token_type": "websocket",
        "sub": str(subject),
        "workspace_id": str(workspace_id),
        "membership_id": str(membership_id),
        "session_id": str(session_id),
        "auth_epoch": int(auth_epoch),
    }
    return _encode_token(to_encode, "websocket")


def create_platform_websocket_ticket(
    subject: str | Any,
    *,
    session_id: UUID,
    auth_epoch: int,
    session_issued_at_ns: int | None = None,
) -> str:
    """Gera credencial curta e exclusiva do canal interno de suporte."""
    now = datetime.now(UTC)
    to_encode = {
        "exp": now + timedelta(seconds=settings.WEBSOCKET_TICKET_EXPIRE_SECONDS),
        "iat": now,
        "jti": create_session_token_id(subject, issued_at_ns=session_issued_at_ns),
        "token_type": "platform_websocket",
        "sub": str(subject),
        "session_id": str(session_id),
        "auth_epoch": int(auth_epoch),
        "scope": "support:view",
    }
    return _encode_token(to_encode, "platform_websocket")


def create_local_storage_ticket(
    s3_key: str,
    *,
    operation: str,
    content_type: str | None = None,
    expires_in: int = 3600,
) -> str:
    """Assina uma operação de arquivo usada somente pelo armazenamento local."""
    now = datetime.now(UTC)
    payload = {
        "exp": now + timedelta(seconds=expires_in),
        "iat": now,
        "jti": str(uuid.uuid4()),
        "token_type": "local_storage",
        "sub": s3_key,
        "operation": operation,
        "s3_key": s3_key,
        "content_type": content_type,
    }
    return _encode_token(payload, "local_storage")


def create_booking_token(
    link_id: UUID,
    *,
    workspace_id: UUID,
    public_key: str,
    expires_at: datetime,
) -> str:
    """Assina um link público de agenda sem carregar dados do lead."""
    now = datetime.now(UTC)
    normalized_expiry = (
        expires_at.replace(tzinfo=UTC)
        if expires_at.tzinfo is None
        else expires_at.astimezone(UTC)
    )
    payload = {
        "exp": normalized_expiry,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "token_type": "booking",
        "sub": str(link_id),
        "link_id": str(link_id),
        "workspace_id": str(workspace_id),
        "public_key": public_key,
    }
    return _encode_token(payload, "booking")


def create_admin_session_token(
    subject: object,
    *,
    session_id: UUID,
    expires_at: datetime,
    mfa_method: str,
    auth_epoch: int,
) -> str:
    """Emite credencial curta, isolada dos tokens comuns da aplicação."""
    now = datetime.now(UTC)
    normalized_expiry = (
        expires_at.replace(tzinfo=UTC)
        if expires_at.tzinfo is None
        else expires_at.astimezone(UTC)
    )
    payload = {
        "exp": normalized_expiry,
        "iat": now,
        "jti": create_session_token_id(subject),
        "token_type": "admin_session",
        "sub": str(subject),
        "session_id": str(session_id),
        "mfa_method": mfa_method,
        "auth_epoch": int(auth_epoch),
    }
    return _encode_token(payload, "admin_session")


def create_support_access_token(
    subject: object,
    *,
    grant_id: UUID,
    workspace_id: UUID,
    scopes: list[str],
    expires_at: datetime,
    token_jti: str,
) -> str:
    """Emit a short credential restricted to one approved local support grant."""
    now = datetime.now(UTC)
    normalized_expiry = (
        expires_at.replace(tzinfo=UTC)
        if expires_at.tzinfo is None
        else expires_at.astimezone(UTC)
    )
    payload = {
        "exp": normalized_expiry,
        "iat": now,
        "jti": token_jti,
        "token_type": "support_access",
        "sub": str(subject),
        "grant_id": str(grant_id),
        "workspace_id": str(workspace_id),
        "scopes": sorted(set(scopes)),
    }
    return _encode_token(payload, "support_access")


def verify_booking_token(token: str) -> dict[str, Any] | None:
    """Valida e normaliza somente tokens da página pública de agenda."""
    payload = verify_token(token)
    if not payload or payload.get("token_type") != "booking":
        return None
    try:
        payload["link_id"] = UUID(str(payload["link_id"]))
        payload["workspace_id"] = UUID(str(payload["workspace_id"]))
    except (KeyError, TypeError, ValueError):
        return None
    public_key = str(payload.get("public_key") or "")
    if not public_key:
        return None
    return payload


def hash_password(password: str) -> str:
    """Gera hash bcrypt da senha."""
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha plain bate com o hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


async def hash_password_async(password: str) -> str:
    """Run CPU-bound bcrypt hashing outside the async event loop."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """Run CPU-bound bcrypt verification outside the async event loop."""
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)
