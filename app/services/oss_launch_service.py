"""Short-lived, local-only launch tickets for complete OSS product UIs."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.core.config import settings
from app.services.oss_capability_registry import oss_capability_registry
from app.services.oss_motor_routes import MOTOR_DESTINATIONS


class OSSLaunchError(ValueError):
    pass


_ROOT = Path(__file__).resolve().parents[2]
_NONCE_LEDGER_PATH = _ROOT / ".local" / "oss-launch-nonces.json"
_SERVICE_URLS = {
    "chatwoot": lambda: settings.KLM_CHATWOOT_URL,
    "rocketchat": lambda: settings.KLM_ROCKETCHAT_URL,
    "chattermate": lambda: settings.KLM_CHATTERMATE_URL,
    "bottlecrm": lambda: settings.KLM_BOTTLECRM_URL,
}

_CAPABILITY_DESTINATIONS = MOTOR_DESTINATIONS


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret() -> bytes:
    value = settings.KLM_OSS_LAUNCH_SECRET.strip()
    if len(value) < 32:
        raise OSSLaunchError("Segredo de abertura dos módulos locais não configurado.")
    return value.encode("utf-8")


def _configured_workspace_id() -> UUID:
    raw_value = settings.KLM_OSS_WORKSPACE_ID.strip()
    try:
        return UUID(raw_value)
    except (TypeError, ValueError) as error:
        raise OSSLaunchError("Workspace dos módulos locais não configurado.") from error


def _consume_nonce(nonce: str, expires_at: int, now: int) -> None:
    """Persist one-use tickets across workers and local runtime restarts."""
    path = _NONCE_LEDGER_PATH
    local_dir = path.parent
    if local_dir.exists() and local_dir.is_symlink():
        raise OSSLaunchError("Diretório do ledger de bilhetes locais é inválido.")
    local_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(local_dir, 0o700)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise OSSLaunchError("Ledger de bilhetes locais indisponível.") from error
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, 1_000_001)
        if len(raw) > 1_000_000:
            raise OSSLaunchError("Ledger de bilhetes locais excedeu o limite seguro.")
        try:
            stored = json.loads(raw.decode("ascii")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OSSLaunchError("Ledger de bilhetes locais está corrompido.") from error
        if not isinstance(stored, dict):
            raise OSSLaunchError("Ledger de bilhetes locais está corrompido.")
        active: dict[str, int] = {}
        for stored_nonce, stored_expiry in stored.items():
            if not isinstance(stored_nonce, str) or not isinstance(stored_expiry, int):
                raise OSSLaunchError("Ledger de bilhetes locais está corrompido.")
            if stored_expiry >= now:
                active[stored_nonce] = stored_expiry
        if nonce in active:
            raise OSSLaunchError("Bilhete local já utilizado.")
        active[nonce] = expires_at
        encoded = json.dumps(active, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(encoded) > 1_000_000:
            raise OSSLaunchError("Ledger de bilhetes locais excedeu o limite seguro.")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        written = 0
        while written < len(encoded):
            written += os.write(descriptor, encoded[written:])
        os.fsync(descriptor)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _local_url(service: str) -> str:
    factory = _SERVICE_URLS.get(service)
    if factory is None:
        raise OSSLaunchError("Módulo local desconhecido.")
    value = factory().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise OSSLaunchError("Abertura bloqueada para origem não local.")
    return value


def _launch_context(service: str, context: dict[str, Any] | None) -> dict[str, Any]:
    supplied = context or {}
    return_to = str(supplied.get("returnTo") or "/app")
    if not return_to.startswith("/app") or return_to.startswith("//") or "\\" in return_to:
        raise OSSLaunchError("Rota de retorno do KLM inválida.")
    capability_id = str(supplied.get("capabilityId") or "")
    expected_prefix = f"{service}."
    capability_key = capability_id.removeprefix(expected_prefix) if capability_id else ""
    if capability_id and not capability_id.startswith(expected_prefix):
        raise OSSLaunchError("Capacidade não pertence ao módulo solicitado.")
    destination = _CAPABILITY_DESTINATIONS.get(service, {}).get(capability_key)
    if capability_id:
        capability = next(
            (
                item
                for item in oss_capability_registry()["capabilities"]
                if item["id"] == capability_id
            ),
            None,
        )
        if capability is None:
            raise OSSLaunchError("Capacidade local desconhecida.")
        if (
            capability["disposition"].startswith("blocked_")
            or capability["disposition"] == "not_applicable"
            or capability["currentExposure"] == "none"
        ):
            raise OSSLaunchError("Capacidade bloqueada no runtime local.")
        if destination is None:
            raise OSSLaunchError("Capacidade sem destino específico no motor local.")
    if service == "chatwoot" and destination:
        destination = f"/app/accounts/{settings.KLM_CHATWOOT_ACCOUNT_ID}/{destination}"
    return {
        "returnTo": return_to,
        "capabilityId": capability_id or None,
        "matterId": str(supplied["matterId"]) if supplied.get("matterId") else None,
        "threadId": str(supplied["threadId"]) if supplied.get("threadId") else None,
        "flowId": str(supplied["flowId"]) if supplied.get("flowId") else None,
        "surface": str(supplied.get("surface") or "")[:100] or None,
        "destinationPath": destination,
    }


def issue_launch_ticket(
    service: str,
    workspace_id: UUID,
    ttl_seconds: int = 60,
    *,
    actor_user_id: UUID,
    membership_id: UUID,
    actor_role: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not settings.uses_local_infrastructure or not settings.KLM_OSS_LOCAL_BRIDGES_ENABLED:
        raise OSSLaunchError("Módulos locais não estão habilitados neste runtime.")
    if workspace_id != _configured_workspace_id():
        raise OSSLaunchError("Workspace não autorizado para estes módulos locais.")
    base_url = _local_url(service)
    now = int(time.time())
    launch_context = _launch_context(service, context)
    payload = {
        "scope": "oss-admin-session",
        "service": service,
        "workspaceId": str(workspace_id),
        "actorUserId": str(actor_user_id),
        "membershipId": str(membership_id),
        "actorRole": str(actor_role),
        "context": launch_context,
        "iat": now,
        "exp": now + max(15, min(ttl_seconds, 120)),
        "nonce": secrets.token_urlsafe(18),
    }
    encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64encode(hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    ticket = f"{encoded}.{signature}"
    return {
        "service": service,
        "launchUrl": f"{base_url}/__klm_session?ticket={ticket}",
        "expiresAt": datetime.fromtimestamp(payload["exp"], tz=UTC),
        "embeddable": service in {"chattermate", "bottlecrm"},
        "context": launch_context,
    }


def consume_launch_ticket(ticket: str, expected_service: str) -> dict[str, Any]:
    if len(ticket) > 4096 or ticket.count(".") != 1:
        raise OSSLaunchError("Bilhete local inválido.")
    encoded, supplied_signature = ticket.split(".", 1)
    expected_signature = _b64encode(
        hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise OSSLaunchError("Assinatura do bilhete local inválida.")
    try:
        payload = json.loads(_b64decode(encoded))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OSSLaunchError("Conteúdo do bilhete local inválido.") from error
    if not isinstance(payload, dict) or payload.get("service") != expected_service:
        raise OSSLaunchError("Bilhete emitido para outro módulo.")
    now = int(time.time())
    try:
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
        workspace_id = UUID(str(payload["workspaceId"]))
        actor_user_id = UUID(str(payload["actorUserId"]))
        membership_id = UUID(str(payload["membershipId"]))
    except (KeyError, TypeError, ValueError) as error:
        raise OSSLaunchError("Contrato do bilhete local inválido.") from error
    if payload.get("scope") != "oss-admin-session" or not str(payload.get("actorRole") or ""):
        raise OSSLaunchError("Escopo do bilhete local inválido.")
    if issued_at > now + 5 or expires_at < now or expires_at - issued_at > 120:
        raise OSSLaunchError("Bilhete local expirado.")
    if workspace_id != _configured_workspace_id():
        raise OSSLaunchError("Bilhete emitido para outro workspace.")
    context = payload.get("context")
    if not isinstance(context, dict):
        raise OSSLaunchError("Contexto do bilhete local inválido.")
    payload["context"] = _launch_context(expected_service, context)
    payload["actorUserId"] = str(actor_user_id)
    payload["membershipId"] = str(membership_id)
    nonce = str(payload.get("nonce") or "")
    if not nonce:
        raise OSSLaunchError("Bilhete local sem nonce.")
    _consume_nonce(nonce, expires_at, now)
    return payload
