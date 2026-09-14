"""Persistência, sanitização e diff da auditoria administrativa canônica."""
from __future__ import annotations

import hashlib
import json
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, cast, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_audit_event import PlatformAuditEvent
from app.schemas.platform_audit_event import PlatformAuditEventCreate

_SECRET_PARTS = {
    "password", "passwd", "secret", "api_key", "apikey", "private_key",
    "cookie", "authorization", "credential", "recovery_code", "mfa_code",
    "totp", "client_secret",
}
_TOKEN_METRIC_PARTS = {
    "quantity", "count", "usage", "limit", "budget", "cost", "price",
    "package", "quota", "consumed", "remaining",
}
_FIELD_LABELS = {
    "accessPackage": "Pacote de acesso",
    "activeSessions": "Sessões ativas",
    "amountCents": "Valor",
    "authEpoch": "Versão de autorização",
    "denials": "Permissões negadas",
    "description": "Descrição",
    "grants": "Permissões adicionais",
    "isActive": "Ativo",
    "isDefault": "Conta padrão",
    "onboardingCompleted": "Onboarding concluído",
    "planId": "Plano",
    "platformAccessActive": "Acesso ao Blackbox",
    "role": "Papel",
    "rolePackage": "Função da plataforma",
    "status": "Status",
    "trialEnd": "Fim do trial",
    "userActive": "Usuário ativo",
}
_MISSING = object()
_RESTORABLE_RESOURCE_TYPES = frozenset({
    "workspace",
    "staff",
    "user",
    "customer_membership",
    "plan",
    "subscription",
    "platform_configuration",
})
_NON_RESTORABLE_ACTION_PARTS = ("session", "transfer", "invitation", "invite")

_PEM_RE = re.compile(
    r"-----BEGIN [^-\r\n]+-----.*?-----END [^-\r\n]+-----",
    re.IGNORECASE | re.DOTALL,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_URL_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?P<userinfo>[^\s/@:]+:[^\s/@]+)@",
    re.IGNORECASE,
)
_NAMED_SECRET_RE = re.compile(
    r"(?P<key_quote>[\"']?)(?P<name>password|passwd|client[_-]?secret|"
    r"api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|authorization|cookie|recovery[_-]?code|mfa[_-]?code|totp)"
    r"(?P=key_quote)"
    r"(?P<separator>\s*[=:]\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\s,;\"']{4,})(?P=quote)",
    re.IGNORECASE,
)
_KNOWN_CREDENTIAL_RE = re.compile(
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\bsk-[A-Za-z0-9_-]{20,}\b"
)


def _is_secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    compact = normalized.replace("_", "")
    if any(
        part in normalized or part.replace("_", "") in compact
        for part in _SECRET_PARTS
    ):
        return True
    if "token" not in compact:
        return False
    # Métricas comerciais/operacionais de tokens não são credenciais. O valor
    # secreto continua protegido para accessToken, refreshToken, bearerToken,
    # token isolado e demais chaves sem semântica quantitativa.
    if any(metric in compact for metric in _TOKEN_METRIC_PARTS):
        return False
    if compact.startswith(("max", "min")):
        return False
    return True


def _secret_metadata(value: Any) -> dict[str, Any]:
    raw = "" if value is None else str(value)
    return {
        "configured": value not in (None, "", [], {}),
        "redacted": True,
        "fingerprint": (
            f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}" if raw else None
        ),
        "last4": raw[-4:] if len(raw) >= 4 else None,
    }


def sanitize_audit_text(value: str) -> str:
    """Redact credentials embedded in free text, including legacy audit rows.

    Key-aware sanitization protects structured snapshots. Audit reasons and
    historic string fields need a second layer because operators may paste a
    connection URL, authorization header, PEM block or provider token into a
    free-text field.
    """
    sanitized = _PEM_RE.sub("[REDACTED_PEM]", value)
    sanitized = _BEARER_RE.sub("Bearer [REDACTED]", sanitized)
    sanitized = _JWT_RE.sub("[REDACTED_JWT]", sanitized)
    sanitized = _URL_CREDENTIAL_RE.sub(
        lambda match: f"{match.group('scheme')}[REDACTED]@",
        sanitized,
    )
    sanitized = _NAMED_SECRET_RE.sub(
        lambda match: (
            f"{match.group('key_quote')}{match.group('name')}"
            f"{match.group('key_quote')}{match.group('separator')}[REDACTED]"
        ),
        sanitized,
    )
    return _KNOWN_CREDENTIAL_RE.sub("[REDACTED_CREDENTIAL]", sanitized)


def sanitize_audit_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_secret_key(key):
        return _secret_metadata(value)
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_audit_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [sanitize_audit_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return [sanitize_audit_value(item) for item in sorted(value, key=str)]
    if isinstance(value, Enum):
        return sanitize_audit_value(value.value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value), "redacted": True}
    if isinstance(value, str):
        return sanitize_audit_text(value)
    if value is None or isinstance(value, int | float | bool):
        return value
    # A auditoria deve permanecer gravável sem serializar acidentalmente ORM,
    # clientes de provider ou outros objetos que possam carregar credenciais.
    return {"type": type(value).__name__, "unsupported": True}


def changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        # Metadados de segredo formam um campo semântico único. Não exponha
        # fingerprint/last4 como subcampos operacionais do diff.
        if before.get("redacted") is True or after.get("redacted") is True:
            return [prefix or "$"]
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(changed_paths(before.get(key), after.get(key), path))
        return paths
    return [prefix or "$"]


def _value_at_path(value: Any, path: str) -> Any:
    current = value
    if path == "$":
        return current
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def semantic_field_diff(before: Any, after: Any) -> list[dict[str, Any]]:
    """Return a stable, already-sanitized operator diff for the Audit Center."""
    result: list[dict[str, Any]] = []
    for path in changed_paths(before, after):
        previous = _value_at_path(before, path)
        current = _value_at_path(after, path)
        previous_value = None if previous is _MISSING else previous
        current_value = None if current is _MISSING else current
        sensitive = bool(
            isinstance(previous_value, dict) and previous_value.get("redacted") is True
            or isinstance(current_value, dict) and current_value.get("redacted") is True
        )
        leaf = path.rsplit(".", 1)[-1]
        result.append({
            "path": path,
            "label": _FIELD_LABELS.get(leaf, leaf.replace("_", " ")),
            "changeType": (
                "added" if previous is _MISSING
                else "removed" if current is _MISSING
                else "changed"
            ),
            "before": previous_value,
            "after": current_value,
            "sensitive": sensitive,
        })
    return result


def is_audit_event_restorable(event: PlatformAuditEvent) -> bool:
    action = str(event.action or "").casefold()
    return bool(
        event.resource_type in _RESTORABLE_RESOURCE_TYPES
        and event.result in {"success", "partial", "compensated"}
        and isinstance(event.before, dict)
        and bool(event.before)
        and not any(part in action for part in _NON_RESTORABLE_ACTION_PARTS)
    )


def _encode_cursor(occurred_at: datetime, event_id: Any) -> str:
    payload = json.dumps([occurred_at.isoformat(), str(event_id)], separators=(",", ":"))
    return urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    padded = cursor + "=" * (-len(cursor) % 4)
    raw = json.loads(urlsafe_b64decode(padded.encode()).decode())
    return datetime.fromisoformat(raw[0]), UUID(str(raw[1]))


class PlatformAuditEventService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(self, payload: PlatformAuditEventCreate) -> PlatformAuditEvent:
        before = sanitize_audit_value(payload.before)
        after = sanitize_audit_value(payload.after)
        serialized = payload.model_dump(
            exclude={
                "before",
                "after",
                "reason",
                "workspace_label_snapshot",
                "resource_label_snapshot",
            }
        )
        event = PlatformAuditEvent(
            **serialized,
            before=before,
            after=after,
            reason=sanitize_audit_text(payload.reason),
            workspace_label_snapshot=(
                sanitize_audit_text(payload.workspace_label_snapshot)
                if payload.workspace_label_snapshot is not None
                else None
            ),
            resource_label_snapshot=(
                sanitize_audit_text(payload.resource_label_snapshot)
                if payload.resource_label_snapshot is not None
                else None
            ),
            changed_paths=changed_paths(before, after),
            # Python gera precisão subsegundo estável também no SQLite. O
            # server_default de segundos fazia todos os eventos do mesmo lote
            # colidirem no cursor e a comparação textual repetir a página.
            occurred_at=datetime.now(UTC),
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def list_events(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        module: str | None = None,
        result: str | None = None,
        workspace_id: Any | None = None,
        actor_id: Any | None = None,
        actor_role: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        correlation_id: str | None = None,
        operation_id: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        restorable: bool | None = None,
        search: str | None = None,
    ) -> tuple[list[PlatformAuditEvent], str | None]:
        query = select(PlatformAuditEvent)
        if module:
            query = query.where(PlatformAuditEvent.module == module)
        if result:
            query = query.where(PlatformAuditEvent.result == result)
        if workspace_id:
            query = query.where(PlatformAuditEvent.workspace_id == workspace_id)
        if actor_id:
            query = query.where(PlatformAuditEvent.actor_id == actor_id)
        if actor_role:
            query = query.where(PlatformAuditEvent.actor_role_snapshot == actor_role)
        if action:
            query = query.where(PlatformAuditEvent.action == action)
        if resource_type:
            query = query.where(PlatformAuditEvent.resource_type == resource_type)
        if correlation_id:
            query = query.where(PlatformAuditEvent.correlation_id == correlation_id)
        if operation_id:
            query = query.where(PlatformAuditEvent.operation_id == operation_id)
        if occurred_from:
            query = query.where(PlatformAuditEvent.occurred_at >= occurred_from)
        if occurred_to:
            query = query.where(PlatformAuditEvent.occurred_at <= occurred_to)
        if restorable is not None:
            serialized_before = cast(PlatformAuditEvent.before, String)
            restorable_condition = and_(
                PlatformAuditEvent.resource_type.in_(_RESTORABLE_RESOURCE_TYPES),
                PlatformAuditEvent.result.in_(["success", "partial", "compensated"]),
                serialized_before.not_in(["null", "{}"]),
                *(
                    not_(func.lower(PlatformAuditEvent.action).contains(part))
                    for part in _NON_RESTORABLE_ACTION_PARTS
                ),
            )
            query = query.where(restorable_condition if restorable else not_(restorable_condition))
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.where(
                or_(
                    PlatformAuditEvent.action.ilike(term),
                    PlatformAuditEvent.resource_type.ilike(term),
                    PlatformAuditEvent.resource_id.ilike(term),
                    PlatformAuditEvent.reason.ilike(term),
                    PlatformAuditEvent.correlation_id.ilike(term),
                    cast(PlatformAuditEvent.actor_id, String).ilike(term),
                )
            )
        if cursor:
            _, event_id = _decode_cursor(cursor)
            cursor_event = await self.db.get(PlatformAuditEvent, event_id)
            if cursor_event is None:
                raise ValueError("Cursor de auditoria inválido ou expirado.")
            occurred_at = cursor_event.occurred_at
            query = query.where(
                or_(
                    PlatformAuditEvent.occurred_at < occurred_at,
                    and_(
                        PlatformAuditEvent.occurred_at == occurred_at,
                        PlatformAuditEvent.id < event_id,
                    ),
                )
            )
        rows = list(
            (
                await self.db.scalars(
                    query.order_by(
                        PlatformAuditEvent.occurred_at.desc(),
                        PlatformAuditEvent.id.desc(),
                    ).limit(limit + 1)
                )
            ).all()
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = (
            _encode_cursor(items[-1].occurred_at, items[-1].id)
            if has_more and items
            else None
        )
        return items, next_cursor
