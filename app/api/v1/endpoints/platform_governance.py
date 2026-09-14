"""Control plane versionado para tecnologia, segurança e governança do Blackbox."""
from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import Field
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_audit import (
    record_canonical_platform_mutation,
    snapshot_platform_config_document,
    snapshot_platform_incident,
    snapshot_platform_user_session,
)
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.models.auth_security import AdministrativeSession, AuthSecurityEvent, MfaCredential
from app.models.configuration import PlatformConfigDocument, PlatformConfigDocumentVersion
from app.models.error_log import ErrorLog, ErrorSeverity, ErrorStatus
from app.models.identity import UserSession
from app.models.platform_incident import PlatformIncident
from app.models.platform_official_whatsapp import PlatformOfficialWhatsAppConnection
from app.models.platform_release import PlatformRelease
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.models.user import User
from app.models.whatsapp import WaConnectionStatus
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.configuration import (
    ConfigVersionPageRead,
    ConfigVersionRead,
    PlatformConfigurationDocumentRead,
    PlatformConfigurationDocumentWrite,
    PlatformFeatureFlagUpdate,
)
from app.services.auth_session_service import AuthSessionService
from app.services.configuration_service import (
    ConfigConflictError,
    ConfigurationService,
    ConfigValidationError,
)
from app.websockets.manager import ws_manager

router = APIRouter(prefix="/platform/governance", tags=["Platform Governance"])

GOVERNANCE_MODULES = frozenset({
    "audit",
    "compliance",
    "data_governance",
    "feature_flags",
    "integrations",
    "logs",
    "releases",
    "security",
})


class PlatformIncidentCreate(WriteSchema):
    title: str
    summary: str
    severity: Literal["sev1", "sev2", "sev3", "sev4"]
    components: list[str] = Field(default_factory=list)
    owner_user_id: UUID | None = None


class PlatformIncidentUpdate(WriteSchema):
    status: Literal["investigating", "identified", "monitoring", "resolved"] | None = None
    summary: str | None = None
    severity: Literal["sev1", "sev2", "sev3", "sev4"] | None = None
    owner_user_id: UUID | None = None
    note: str | None = None


class PlatformReleaseWrite(WriteSchema):
    version: str = Field(min_length=1, max_length=120)
    environment: Literal["local", "staging", "production"]
    status: Literal["planned", "deploying", "active", "failed", "rolled_back"] = "planned"
    commit_sha: str | None = Field(default=None, max_length=64)
    source: str = Field(default="manual_blackbox", min_length=2, max_length=80)
    deployed_at: datetime | None = None
    previous_release_id: UUID | None = None
    rollback_target_id: UUID | None = None
    reason: str = Field(min_length=8, max_length=500)


class PlatformReleaseUpdate(WriteSchema):
    status: Literal["planned", "deploying", "active", "failed", "rolled_back"] | None = None
    deployed_at: datetime | None = None
    rollback_target_id: UUID | None = None
    reason: str = Field(min_length=8, max_length=500)


class PlatformReleaseRead(CamelCaseSchema):
    id: UUID
    version: str
    environment: str
    status: str
    commit_sha: str | None
    source: str
    deployed_at: datetime | None
    previous_release_id: UUID | None
    rollback_target_id: UUID | None
    revision: int
    created_at: datetime
    updated_at: datetime


async def _authorize(
    module: str,
    action: str,
    context: AuthContext,
    db: AsyncSession,
) -> User:
    if module not in GOVERNANCE_MODULES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GOV-001", "message": "Módulo de governança desconhecido."},
        )
    if context.surface != "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "BLACKBOX-IDENTITY-REQUIRED", "message": "Identidade Blackbox obrigatória."},
        )
    actor = await db.get(User, context.user_id)
    if not has_platform_permission(actor, module, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PLATFORM-PERMISSION-DENIED",
                "message": "Seu pacote de acesso não autoriza esta operação.",
                "module": module,
                "action": action,
            },
        )
    return actor


def _namespace(module: str) -> str:
    return f"blackbox.{module}"


def _config_error(exc: Exception) -> None:
    if isinstance(exc, ConfigConflictError):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "GOV-002", "message": str(exc)},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "GOV-003", "message": str(exc)},
    ) from exc


_AUDIT_REDACTED_VALUE = "[mascarado]"
_AUDIT_SECRET_PARTS = frozenset({
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "passwd",
    "password",
    "passwords",
    "pwd",
    "secret",
    "secrets",
    "token",
    "tokens",
})
_AUDIT_SAFE_SECRET_METADATA = frozenset({
    "algorithm",
    "configured",
    "created_at",
    "enabled",
    "expires_at",
    "fingerprint",
    "id",
    "key_id",
    "label",
    "last4",
    "method",
    "name",
    "provider",
    "rotated_at",
    "scopes",
    "status",
    "type",
    "updated_at",
    "verified_at",
})


def _audit_key_parts(key: object) -> tuple[str, ...]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return tuple(part for part in re.split(r"[^a-z0-9]+", expanded.lower()) if part)


def _audit_key_name(key: object) -> str:
    return "_".join(_audit_key_parts(key))


def _is_sensitive_audit_key(key: object) -> bool:
    parts = set(_audit_key_parts(key))
    if parts & _AUDIT_SECRET_PARTS:
        return True
    if parts & {"otp", "totp"}:
        return True
    if "mfa" in parts and parts & {"code", "codes", "key", "seed", "secret", "token"}:
        return True
    if parts & {"recovery", "backup"} and parts & {"code", "codes"}:
        return True
    if "key" in parts and (
        len(parts) == 1
        or bool(parts & {"access", "api", "encryption", "private", "secret", "signing"})
    ):
        return True
    return False


def _safe_audit_details(value: object, *, sensitive_container: bool = False) -> object:
    """Return an audit payload that can never disclose stored authentication material.

    Root authority changes authorization, not confidentiality. Secret containers retain
    only an allowlist of operational metadata; unknown leaves are redacted recursively.
    """
    if isinstance(value, dict):
        safe: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            key_is_sensitive = _is_sensitive_audit_key(key_text)
            key_is_safe_metadata = _audit_key_name(key_text) in _AUDIT_SAFE_SECRET_METADATA
            if key_is_sensitive:
                safe[key_text] = _safe_audit_details(item, sensitive_container=True)
            elif sensitive_container and not key_is_safe_metadata:
                safe[key_text] = _safe_audit_details(item, sensitive_container=True)
            else:
                safe[key_text] = _safe_audit_details(item)
        return safe
    if isinstance(value, list):
        return [
            _safe_audit_details(item, sensitive_container=sensitive_container)
            for item in value
        ]
    if sensitive_container:
        return None if value is None else _AUDIT_REDACTED_VALUE
    return value


_FEATURE_FLAG_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,79}$")


def _feature_flags(document: PlatformConfigDocument | None) -> dict[str, bool]:
    raw_flags = (document.payload or {}).get("flags", {}) if document else {}
    if not isinstance(raw_flags, dict):
        return {}
    return {
        str(key): value
        for key, value in raw_flags.items()
        if isinstance(key, str) and isinstance(value, bool)
    }


def _validate_governance_payload(module: str, payload: dict) -> None:
    if module != "feature_flags":
        return
    flags = payload.get("flags")
    if not isinstance(flags, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "GOV-FLAG-002", "message": "Feature flags exigem um objeto flags."},
        )
    invalid = [
        str(key) for key, value in flags.items()
        if not isinstance(key, str) or not _FEATURE_FLAG_KEY.fullmatch(key) or not isinstance(value, bool)
    ]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "GOV-FLAG-003", "message": "Toda flag deve ter chave válida e valor booleano.", "invalid": invalid[:20]},
        )


@router.get("/audit-events", summary="Consultar a trilha administrativa real do Blackbox")
async def list_platform_audit_events(
    event_type: str | None = Query(default=None, alias="eventType", max_length=80),
    outcome: str | None = Query(default=None, max_length=32),
    actor_user_id: str | None = Query(default=None, alias="actorUserId", max_length=36),
    workspace_id: str | None = Query(default=None, alias="workspaceId", max_length=36),
    search: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _authorize("audit", "view", context, db)
    clauses = []
    if event_type:
        clauses.append(AuthSecurityEvent.event_type == event_type)
    if outcome:
        clauses.append(AuthSecurityEvent.outcome == outcome)
    if actor_user_id:
        clauses.append(cast(AuthSecurityEvent.actor_user_id, String) == actor_user_id)
    if workspace_id:
        clauses.append(cast(AuthSecurityEvent.details, String).ilike(f"%{workspace_id}%"))
    if search:
        term = f"%{search.strip()}%"
        clauses.append(or_(
            AuthSecurityEvent.event_type.ilike(term),
            AuthSecurityEvent.outcome.ilike(term),
            cast(AuthSecurityEvent.details, String).ilike(term),
        ))
    statement = select(AuthSecurityEvent).order_by(AuthSecurityEvent.occurred_at.desc()).offset(offset).limit(limit)
    count_statement = select(func.count(AuthSecurityEvent.id))
    if clauses:
        statement = statement.where(*clauses)
        count_statement = count_statement.where(*clauses)
    events = list(await db.scalars(statement))
    actor_ids = {event.actor_user_id for event in events if event.actor_user_id is not None}
    actors = {
        user.id: user
        for user in (list(await db.scalars(select(User).where(User.id.in_(actor_ids)))) if actor_ids else [])
    }
    def serialize_event(event: AuthSecurityEvent) -> dict:
        details = _safe_audit_details(event.details)

        def detail(*keys: str):
            for key in keys:
                if key in details:
                    return details[key]
            return None

        workspace_value = detail("workspace_id", "workspaceId")
        if workspace_value is None:
            workspace_ids = detail("workspace_ids", "workspaceIds")
            workspace_value = workspace_ids[0] if isinstance(workspace_ids, list) and len(workspace_ids) == 1 else None
        return {
            "id": str(event.id),
            "eventType": event.event_type,
            "outcome": event.outcome,
            "result": detail("result") or event.outcome,
            "actorUserId": str(event.actor_user_id) if event.actor_user_id else None,
            "actorName": actors[event.actor_user_id].name if event.actor_user_id in actors else "Sistema",
            "actorRole": actors[event.actor_user_id].platform_role_package if event.actor_user_id in actors else None,
            "isPlatformRoot": is_platform_root(actors.get(event.actor_user_id)),
            "subjectUserId": str(event.subject_user_id) if event.subject_user_id else None,
            "administrativeSessionId": str(event.administrative_session_id) if event.administrative_session_id else None,
            "workspaceId": str(workspace_value) if workspace_value is not None else None,
            "module": detail("module"),
            "action": detail("action") or event.event_type,
            "resourceType": detail("resource_type", "resourceType"),
            "resourceId": detail("resource_id", "resourceId"),
            "correlationId": detail("correlation_id", "correlationId"),
            "reason": detail("reason"),
            "before": detail("before"),
            "after": detail("after"),
            "details": details,
            "occurredAt": event.occurred_at.isoformat(),
        }
    return {
        "items": [serialize_event(event) for event in events],
        "count": int(await db.scalar(count_statement) or 0),
        "limit": limit,
        "offset": offset,
    }


@router.get("/operational-logs", summary="Consultar erros operacionais persistidos da plataforma")
async def list_platform_operational_logs(
    log_status: ErrorStatus | None = Query(default=None, alias="status"),
    severity: ErrorSeverity | None = None,
    search: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=500),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _authorize("logs", "view", context, db)
    clauses = []
    if log_status:
        clauses.append(ErrorLog.status == log_status)
    if severity:
        clauses.append(ErrorLog.severity == severity)
    if search:
        term = f"%{search.strip()}%"
        clauses.append(or_(
            ErrorLog.error_code.ilike(term), ErrorLog.exception_type.ilike(term),
            ErrorLog.message.ilike(term), ErrorLog.endpoint.ilike(term),
            ErrorLog.request_id.ilike(term),
        ))
    if cursor:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
            cursor_time = datetime.fromisoformat(str(decoded["timestamp"]).replace("Z", "+00:00"))
            cursor_id = UUID(str(decoded["id"]))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Cursor de logs inválido.") from exc
        clauses.append(or_(
            ErrorLog.last_seen_at < cursor_time,
            and_(ErrorLog.last_seen_at == cursor_time, ErrorLog.id < cursor_id),
        ))
    statement = select(ErrorLog).order_by(
        ErrorLog.last_seen_at.desc(), ErrorLog.id.desc()
    ).limit(limit + 1)
    if clauses:
        statement = statement.where(*clauses)
    rows = list(await db.scalars(statement))
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        payload = json.dumps(
            {"timestamp": last.last_seen_at.isoformat(), "id": str(last.id)},
            separators=(",", ":"),
        )
        next_cursor = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return {
        "items": [{
            "id": str(item.id), "errorCode": item.error_code,
            "severity": item.severity.value if hasattr(item.severity, "value") else str(item.severity),
            "status": item.status.value if hasattr(item.status, "value") else str(item.status),
            "exceptionType": item.exception_type, "message": item.message[:500],
            "endpoint": item.endpoint, "method": item.method, "requestId": item.request_id,
            "workspaceId": str(item.workspace_id) if item.workspace_id else None,
            "occurrences": item.occurrences,
            "firstSeenAt": item.first_seen_at.isoformat(), "lastSeenAt": item.last_seen_at.isoformat(),
        } for item in items],
        "count": len(items),
        "limit": limit,
        "nextCursor": next_cursor,
        "hasMore": has_more,
    }


@router.get("/feature-flags/catalog", summary="Listar feature flags efetivas do Blackbox")
async def list_platform_feature_flags(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _authorize("feature_flags", "view", context, db)
    document = await db.scalar(select(PlatformConfigDocument).where(
        PlatformConfigDocument.namespace == _namespace("feature_flags")
    ))
    return {
        "flags": _feature_flags(document),
        "etag": document.etag if document else None,
        "currentVersion": document.current_version if document else 0,
        "updatedAt": document.updated_at.isoformat() if document else None,
    }


@router.patch("/feature-flags/{key}", summary="Ativar ou desativar uma feature flag")
async def update_platform_feature_flag(
    key: str,
    payload: PlatformFeatureFlagUpdate,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await _authorize("feature_flags", "manage", context, db)
    if not _FEATURE_FLAG_KEY.fullmatch(key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "GOV-FLAG-001", "message": "Chave de feature flag inválida."},
        )
    namespace = _namespace("feature_flags")
    before = await snapshot_platform_config_document(db, namespace, for_update=True)
    document = await db.scalar(select(PlatformConfigDocument).where(
        PlatformConfigDocument.namespace == namespace
    ))
    flags = _feature_flags(document)
    flags[key] = payload.enabled
    existing_payload = dict(document.payload or {}) if document else {}
    existing_payload["flags"] = flags
    try:
        updated = await ConfigurationService(db).write_platform(
            namespace=namespace,
            payload=existing_payload,
            actor_user_id=actor.id,
            schema_version=document.schema_version if document else "1.0",
            expected_etag=payload.expected_etag,
            reason=payload.reason,
        )
        after = await snapshot_platform_config_document(db, namespace)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="feature_flags",
            action="update_platform_feature_flag",
            reason=payload.reason,
        )
    except (ConfigConflictError, ConfigValidationError) as exc:
        await db.rollback()
        _config_error(exc)
    return {
        "flags": _feature_flags(updated),
        "etag": updated.etag,
        "currentVersion": updated.current_version,
        "updatedAt": updated.updated_at.isoformat(),
    }


@router.get("/security/overview", summary="Consultar sessões e postura de identidade da plataforma")
async def get_platform_security_overview(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=500),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    viewer = await _authorize("security", "view", context, db)
    now = datetime.now(UTC)
    active_clause = (UserSession.revoked_at.is_(None), UserSession.expires_at > now)
    session_statement = select(UserSession).where(*active_clause)
    if cursor:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
            cursor_time = datetime.fromisoformat(str(decoded["timestamp"]).replace("Z", "+00:00"))
            cursor_id = UUID(str(decoded["id"]))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "SECURITY-SESSION-CURSOR-INVALID", "message": "Cursor de sessões inválido."},
            ) from exc
        session_statement = session_statement.where(or_(
            UserSession.last_seen_at < cursor_time,
            and_(UserSession.last_seen_at == cursor_time, UserSession.id < cursor_id),
        ))
    rows = list(await db.scalars(session_statement.order_by(
        UserSession.last_seen_at.desc(), UserSession.id.desc()
    ).limit(limit + 1)))
    has_more = len(rows) > limit
    active_sessions = rows[:limit]
    next_cursor = None
    if has_more and active_sessions:
        last = active_sessions[-1]
        payload = json.dumps(
            {"timestamp": last.last_seen_at.isoformat(), "id": str(last.id)},
            separators=(",", ":"),
        )
        next_cursor = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    user_ids = {item.user_id for item in active_sessions}
    users = {
        user.id: user for user in (
            list(await db.scalars(select(User).where(User.id.in_(user_ids)))) if user_ids else []
        )
    }
    active_admin = int(await db.scalar(select(func.count(AdministrativeSession.id)).where(
        AdministrativeSession.revoked_at.is_(None), AdministrativeSession.expires_at > now
    )) or 0)
    mfa_enabled = int(await db.scalar(select(func.count(MfaCredential.id)).where(
        MfaCredential.is_enabled.is_(True)
    )) or 0)
    failed_events = int(await db.scalar(select(func.count(AuthSecurityEvent.id)).where(
        AuthSecurityEvent.outcome.in_(("denied", "failed"))
    )) or 0)

    def email_for(user: User | None) -> str | None:
        if user is None:
            return None
        if is_platform_root(viewer):
            return user.email
        local, separator, domain = user.email.partition("@")
        return f"{local[:2]}***{separator}{domain}" if separator else "[mascarado]"

    return {
        "generatedAt": now.isoformat(),
        "summary": {
            "activeUserSessions": int(await db.scalar(select(func.count(UserSession.id)).where(*active_clause)) or 0),
            "activeAdministrativeSessions": active_admin,
            "mfaEnabledUsers": mfa_enabled,
            "deniedOrFailedEvents": failed_events,
        },
        "sessions": [{
            "id": str(item.id),
            "userId": str(item.user_id),
            "userName": users[item.user_id].name if item.user_id in users else "Usuário removido",
            "userEmail": email_for(users.get(item.user_id)),
            "isPlatformStaff": bool(users[item.user_id].is_internal_staff) if item.user_id in users else False,
            "deviceLabel": item.device_label,
            "deviceType": item.device_type,
            "browser": item.browser,
            "operatingSystem": item.operating_system,
            "lastSeenAt": item.last_seen_at.isoformat(),
            "expiresAt": item.expires_at.isoformat(),
        } for item in active_sessions],
        "limit": limit,
        "nextCursor": next_cursor,
        "hasMore": has_more,
    }


@router.delete("/security/sessions/{session_id}", summary="Revogar uma sessão ativa pelo Blackbox")
async def revoke_platform_security_session(
    session_id: UUID,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await _authorize("security", "manage", context, db)
    before = await snapshot_platform_user_session(db, session_id, for_update=True)
    session = await db.get(UserSession, session_id)
    if session is None or before is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GOV-SEC-001", "message": "Sessão não encontrada."},
        )
    revoked = await AuthSessionService(db).revoke(
        session_id=session.id,
        user_id=session.user_id,
        reason=f"platform_security:{actor.id}",
    )
    after = await snapshot_platform_user_session(db, session_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="security",
        action="revoke_platform_security_session",
        reason="Revogação administrativa de sessão pelo Blackbox.",
        reason_source="system",
    )
    await db.commit()
    if revoked:
        await ws_manager.disconnect_session(session.id, code=4401)
    return {"ok": True, "revoked": revoked}


def _incident_read(incident: PlatformIncident) -> dict:
    return {
        "id": str(incident.id), "title": incident.title, "summary": incident.summary,
        "severity": incident.severity, "status": incident.status,
        "components": incident.components or [],
        "ownerUserId": str(incident.owner_user_id) if incident.owner_user_id else None,
        "createdByUserId": str(incident.created_by_user_id),
        "updates": incident.updates or [], "startedAt": incident.started_at.isoformat(),
        "resolvedAt": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "createdAt": incident.created_at.isoformat(), "updatedAt": incident.updated_at.isoformat(),
    }


def _incident_cursor(incident: PlatformIncident) -> str:
    payload = json.dumps(
        {"startedAt": incident.started_at.isoformat(), "id": str(incident.id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_incident_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return datetime.fromisoformat(payload["startedAt"]), UUID(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INCIDENT-CURSOR-INVALID", "message": "Cursor de incidentes inválido."},
        ) from error


@router.get("/security/incidents", summary="Listar incidentes operacionais da plataforma")
async def list_platform_incidents(
    incident_status: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _authorize("security", "view", context, db)
    statement = select(PlatformIncident)
    if incident_status:
        statement = statement.where(PlatformIncident.status == incident_status)
    if cursor:
        started_at, incident_id = _decode_incident_cursor(cursor)
        statement = statement.where(or_(
            PlatformIncident.started_at < started_at,
            and_(
                PlatformIncident.started_at == started_at,
                PlatformIncident.id < incident_id,
            ),
        ))
    statement = statement.order_by(
        PlatformIncident.started_at.desc(),
        PlatformIncident.id.desc(),
    ).limit(limit + 1)
    rows = list((await db.scalars(statement)).all())
    has_more = len(rows) > limit
    incidents = rows[:limit]
    open_count = int((await db.scalar(
        select(func.count(PlatformIncident.id)).where(PlatformIncident.status != "resolved")
    )) or 0)
    return {
        "items": [_incident_read(item) for item in incidents],
        "openCount": open_count,
        "nextCursor": _incident_cursor(incidents[-1]) if has_more and incidents else None,
        "hasMore": has_more,
    }


@router.post("/security/incidents", status_code=status.HTTP_201_CREATED, summary="Abrir incidente operacional")
async def create_platform_incident(
    body: PlatformIncidentCreate,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await _authorize("security", "manage", context, db)
    title, summary = body.title.strip(), body.summary.strip()
    if not title or not summary:
        raise HTTPException(status_code=422, detail={"code": "INCIDENT-001", "message": "Título e resumo são obrigatórios."})
    if body.owner_user_id and await db.get(User, body.owner_user_id) is None:
        raise HTTPException(status_code=422, detail={"code": "INCIDENT-002", "message": "Responsável não encontrado."})
    now = datetime.now(UTC)
    incident = PlatformIncident(
        title=title, summary=summary, severity=body.severity, status="investigating",
        components=sorted(set(body.components)), owner_user_id=body.owner_user_id,
        created_by_user_id=actor.id,
        updates=[{"status": "investigating", "note": summary, "actorId": str(actor.id), "occurredAt": now.isoformat()}],
        started_at=now,
    )
    db.add(incident)
    await db.flush()
    await db.refresh(incident)
    after = await snapshot_platform_incident(db, incident.id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=after,
        module="security",
        action="create_platform_incident",
        reason="Abertura de incidente operacional no Blackbox.",
        reason_source="system",
    )
    return _incident_read(incident)


@router.patch("/security/incidents/{incident_id}", summary="Atualizar ou resolver incidente operacional")
async def update_platform_incident(
    incident_id: UUID,
    body: PlatformIncidentUpdate,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await _authorize("security", "manage", context, db)
    before = await snapshot_platform_incident(db, incident_id, for_update=True)
    incident = await db.get(PlatformIncident, incident_id)
    if incident is None or before is None:
        raise HTTPException(status_code=404, detail={"code": "INCIDENT-404", "message": "Incidente não encontrado."})
    if body.owner_user_id and await db.get(User, body.owner_user_id) is None:
        raise HTTPException(status_code=422, detail={"code": "INCIDENT-002", "message": "Responsável não encontrado."})
    now = datetime.now(UTC)
    if body.summary is not None:
        incident.summary = body.summary.strip() or incident.summary
    if body.severity is not None:
        incident.severity = body.severity
    if body.owner_user_id is not None:
        incident.owner_user_id = body.owner_user_id
    if body.status is not None:
        incident.status = body.status
        incident.resolved_at = now if body.status == "resolved" else None
    if body.note or body.status:
        incident.updates = [*(incident.updates or []), {"status": incident.status, "note": (body.note or "Status atualizado").strip(), "actorId": str(actor.id), "occurredAt": now.isoformat()}]
    await db.flush()
    await db.refresh(incident)
    after = await snapshot_platform_incident(db, incident_id)
    reason = (body.note or "").strip()
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="security",
        action="update_platform_incident",
        reason=reason or "Atualização operacional do incidente.",
        reason_source="user" if reason else "system",
    )
    return _incident_read(incident)


@router.get("/integrations/overview", summary="Consultar evidências das integrações globais")
async def get_platform_integrations_overview(
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _authorize("integrations", "view", context, db)
    whatsapp_connections = list(await db.scalars(
        select(PlatformOfficialWhatsAppConnection).order_by(PlatformOfficialWhatsAppConnection.role.asc())
    ))
    connected_whatsapp = [
        item for item in whatsapp_connections
        if item.is_active and item.status == WaConnectionStatus.connected
    ]

    def configured(*values: str) -> bool:
        return all(bool(value and value.strip()) for value in values)

    transactional_email_provider = settings.EMAIL_TRANSPORT
    transactional_email_configured = (
        configured(settings.RESEND_API_KEY)
        if transactional_email_provider == "resend"
        else configured(settings.EMAIL_FROM)
        and not settings.EMAIL_FROM.endswith("@localhost")
    )
    transactional_email_label = (
        "E-mail transacional (Resend)"
        if transactional_email_provider == "resend"
        else "E-mail transacional (Amazon SES)"
    )
    transactional_email_detail = (
        "Credencial Resend presente; envio externo não verificado por esta leitura."
        if transactional_email_provider == "resend" and transactional_email_configured
        else "Credencial Resend obrigatória ausente."
        if transactional_email_provider == "resend"
        else (
            "Transporte SES e remetente configurados; sandbox, limite de envio e "
            "entrega externa não são comprovados por esta leitura."
        )
        if transactional_email_configured
        else "Transporte SES selecionado, mas o remetente operacional está ausente."
    )

    providers = [
        {
            "key": "whatsapp_cloud",
            "label": "WhatsApp Cloud API",
            "state": "connected" if connected_whatsapp else "not_configured",
            "evidenceLevel": "persisted_connection" if connected_whatsapp else "none",
            "detail": f"{len(connected_whatsapp)} número(s) ativo(s); {sum(1 for item in connected_whatsapp if item.webhook_registered)} webhook(s) registrado(s)." if connected_whatsapp else "Nenhuma conexão oficial ativa persistida.",
        },
        {
            "key": "webchat",
            "label": "Webchat próprio",
            "state": "available",
            "evidenceLevel": "local_runtime",
            "detail": "Entrada pública assinada e respostas persistidas no núcleo da Inbox.",
        },
        {
            "key": "stripe",
            "label": "Stripe",
            "state": "credentials_configured" if configured(settings.STRIPE_SECRET_KEY, settings.STRIPE_WEBHOOK_SECRET) else "not_configured",
            "evidenceLevel": "credential_presence" if configured(settings.STRIPE_SECRET_KEY, settings.STRIPE_WEBHOOK_SECRET) else "none",
            "detail": "Credenciais e segredo de webhook presentes; chamada externa não verificada por esta leitura." if configured(settings.STRIPE_SECRET_KEY, settings.STRIPE_WEBHOOK_SECRET) else "Credenciais obrigatórias ausentes.",
        },
        {
            "key": "saas_asaas",
            "label": "Asaas — assinatura do SaaS",
            "state": "credentials_configured"
            if configured(
                settings.SAAS_ASAAS_ACCESS_TOKEN,
                settings.SAAS_ASAAS_WEBHOOK_TOKEN,
            )
            else "not_configured",
            "evidenceLevel": "credential_presence"
            if configured(
                settings.SAAS_ASAAS_ACCESS_TOKEN,
                settings.SAAS_ASAAS_WEBHOOK_TOKEN,
            )
            else "none",
            "detail": (
                "Credenciais globais do SaaS presentes e isoladas do Asaas "
                "dos escritórios; chamada externa não verificada por esta leitura."
            )
            if configured(
                settings.SAAS_ASAAS_ACCESS_TOKEN,
                settings.SAAS_ASAAS_WEBHOOK_TOKEN,
            )
            else "Credenciais globais obrigatórias ausentes.",
        },
        {
            "key": "mercado_pago",
            "label": "Mercado Pago — assinatura do SaaS",
            "state": "credentials_configured"
            if configured(
                settings.MERCADO_PAGO_ACCESS_TOKEN,
                settings.MERCADO_PAGO_WEBHOOK_SECRET,
            )
            else "not_configured",
            "evidenceLevel": "credential_presence"
            if configured(
                settings.MERCADO_PAGO_ACCESS_TOKEN,
                settings.MERCADO_PAGO_WEBHOOK_SECRET,
            )
            else "none",
            "detail": (
                "Credencial e segredo de webhook presentes; chamada externa "
                "não verificada por esta leitura."
            )
            if configured(
                settings.MERCADO_PAGO_ACCESS_TOKEN,
                settings.MERCADO_PAGO_WEBHOOK_SECRET,
            )
            else "Credenciais obrigatórias ausentes.",
        },
        {
            "key": "zapsign",
            "label": "ZapSign",
            "state": "credentials_configured" if configured(settings.ZAPSIGN_API_TOKEN, settings.ZAPSIGN_WEBHOOK_SECRET) else "not_configured",
            "evidenceLevel": "credential_presence" if configured(settings.ZAPSIGN_API_TOKEN, settings.ZAPSIGN_WEBHOOK_SECRET) else "none",
            "detail": "Credencial e assinatura de webhook presentes; chamada externa não verificada por esta leitura." if configured(settings.ZAPSIGN_API_TOKEN, settings.ZAPSIGN_WEBHOOK_SECRET) else "Credenciais obrigatórias ausentes.",
        },
        {
            "key": "transactional_email",
            "label": transactional_email_label,
            "state": (
                "credentials_configured"
                if transactional_email_configured
                else "not_configured"
            ),
            "evidenceLevel": (
                "credential_presence"
                if transactional_email_provider == "resend" and transactional_email_configured
                else "runtime_configuration"
                if transactional_email_configured
                else "none"
            ),
            "detail": transactional_email_detail,
        },
        *[
            {
                "key": key,
                "label": label,
                "state": "credentials_configured" if is_configured else "not_configured",
                "evidenceLevel": "credential_presence" if is_configured else "none",
                "detail": "Adaptador implementado e credenciais presentes; webhook e entrega externa ainda exigem homologação." if is_configured else "Adaptador implementado, mas credenciais oficiais ausentes.",
            }
            for key, label, is_configured in (
                ("instagram", "Instagram", configured(settings.PLATFORM_INSTAGRAM_ACCOUNT_ID, settings.PLATFORM_INSTAGRAM_ACCESS_TOKEN)),
                ("messenger", "Messenger", configured(settings.PLATFORM_META_PAGE_ID, settings.PLATFORM_META_PAGE_ACCESS_TOKEN)),
                ("email_sales", "E-mail comercial", configured(settings.RESEND_API_KEY, settings.RESEND_WEBHOOK_SECRET, settings.PLATFORM_EMAIL_SALES_ADDRESS)),
                ("email_support", "E-mail de suporte", configured(settings.RESEND_API_KEY, settings.RESEND_WEBHOOK_SECRET, settings.PLATFORM_EMAIL_SUPPORT_ADDRESS)),
            )
        ],
    ]
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "summary": {
            "total": len(providers),
            "connected": sum(item["state"] in {"connected", "available"} for item in providers),
            "credentialsOnly": sum(item["state"] == "credentials_configured" for item in providers),
            "notConfigured": sum(item["state"] == "not_configured" for item in providers),
        },
        "providers": providers,
    }


def _release_snapshot(release: PlatformRelease) -> dict:
    return {
        "id": str(release.id),
        "version": release.version,
        "environment": release.environment,
        "status": release.status,
        "commitSha": release.commit_sha,
        "source": release.source,
        "deployedAt": release.deployed_at.isoformat() if release.deployed_at else None,
        "previousReleaseId": str(release.previous_release_id) if release.previous_release_id else None,
        "rollbackTargetId": str(release.rollback_target_id) if release.rollback_target_id else None,
        "revision": release.revision,
    }


async def _audit_release(
    db: AsyncSession,
    *,
    request: Request,
    context: AuthContext,
    release: PlatformRelease,
    action: str,
    reason: str,
    before: dict | None,
) -> None:
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=("platform_release", str(release.id), before) if before else None,
        after_snapshot=("platform_release", str(release.id), _release_snapshot(release)),
        module="releases",
        action=action,
        reason=reason,
    )


@router.get("/releases/records", response_model=list[PlatformReleaseRead])
async def list_platform_releases(
    environment: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[PlatformReleaseRead]:
    await _authorize("releases", "view", context, db)
    statement = select(PlatformRelease)
    if environment:
        statement = statement.where(PlatformRelease.environment == environment)
    rows = (
        await db.scalars(
            statement.order_by(
                PlatformRelease.deployed_at.desc().nullslast(),
                PlatformRelease.created_at.desc(),
                PlatformRelease.id.desc(),
            ).limit(limit)
        )
    ).all()
    return [PlatformReleaseRead.model_validate(row) for row in rows]


@router.get("/releases/records/page", summary="Paginar registros canônicos de release")
async def page_platform_releases(
    environment: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _authorize("releases", "view", context, db)
    clauses = [PlatformRelease.environment == environment] if environment else []
    total = int(await db.scalar(
        select(func.count(PlatformRelease.id)).where(*clauses)
    ) or 0)
    statement = select(PlatformRelease).where(*clauses).order_by(
        PlatformRelease.deployed_at.desc().nullslast(),
        PlatformRelease.created_at.desc(),
        PlatformRelease.id.desc(),
    ).offset(offset).limit(limit)
    rows = list(await db.scalars(statement))
    return {
        "items": [PlatformReleaseRead.model_validate(row).model_dump(by_alias=True) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(rows) < total,
        "sort": [
            {"field": "deployedAt", "direction": "desc"},
            {"field": "createdAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    }


@router.post(
    "/releases/records",
    response_model=PlatformReleaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform_release(
    payload: PlatformReleaseWrite,
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> PlatformReleaseRead:
    actor = await _authorize("releases", "manage", context, db)
    for related_id, label in (
        (payload.previous_release_id, "Release anterior"),
        (payload.rollback_target_id, "Alvo de rollback"),
    ):
        if related_id and await db.get(PlatformRelease, related_id) is None:
            raise HTTPException(status_code=422, detail={"code": "RELEASE-RELATED-NOT-FOUND", "message": f"{label} não existe."})
    release = PlatformRelease(
        version=payload.version.strip(),
        environment=payload.environment,
        status=payload.status,
        commit_sha=payload.commit_sha.strip() if payload.commit_sha else None,
        source=payload.source.strip(),
        deployed_at=payload.deployed_at,
        previous_release_id=payload.previous_release_id,
        rollback_target_id=payload.rollback_target_id,
        created_by_user_id=actor.id,
        revision=1,
    )
    db.add(release)
    await db.flush()
    await _audit_release(
        db,
        request=request,
        context=context,
        release=release,
        action="release.create",
        reason=payload.reason,
        before=None,
    )
    response.headers["ETag"] = f'"{release.revision}"'
    return PlatformReleaseRead.model_validate(release)


@router.patch("/releases/records/{release_id}", response_model=PlatformReleaseRead)
async def update_platform_release(
    release_id: UUID,
    payload: PlatformReleaseUpdate,
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> PlatformReleaseRead:
    await _authorize("releases", "manage", context, db)
    release = await db.scalar(
        select(PlatformRelease).where(PlatformRelease.id == release_id).with_for_update()
    )
    if release is None:
        raise HTTPException(status_code=404, detail={"code": "RELEASE-NOT-FOUND", "message": "Release não encontrada."})
    expected = if_match.strip('"') if if_match else None
    if expected is None:
        raise HTTPException(status_code=428, detail={"code": "RELEASE-IF-MATCH-REQUIRED", "message": "Informe a revisão observada."})
    if expected != str(release.revision):
        raise HTTPException(status_code=409, detail={"code": "RELEASE-CONFLICT", "message": "A release foi alterada por outra operação."})
    if payload.rollback_target_id and await db.get(PlatformRelease, payload.rollback_target_id) is None:
        raise HTTPException(status_code=422, detail={"code": "RELEASE-ROLLBACK-NOT-FOUND", "message": "Alvo de rollback não existe."})
    before = _release_snapshot(release)
    if payload.status is not None:
        release.status = payload.status
    if "deployed_at" in payload.model_fields_set:
        release.deployed_at = payload.deployed_at
    if "rollback_target_id" in payload.model_fields_set:
        release.rollback_target_id = payload.rollback_target_id
    release.revision += 1
    await db.flush()
    await _audit_release(
        db,
        request=request,
        context=context,
        release=release,
        action="release.update",
        reason=payload.reason,
        before=before,
    )
    response.headers["ETag"] = f'"{release.revision}"'
    return PlatformReleaseRead.model_validate(release)


@router.get("/{module}/overview", summary="Consultar evidências operacionais de governança")
async def get_platform_governance_overview(
    module: str,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return observed facts separately from the versioned policy document."""
    if module not in {"compliance", "data_governance", "releases"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GOV-OVERVIEW-001", "message": "Visão operacional indisponível para este módulo."},
        )
    await _authorize(module, "view", context, db)
    now = datetime.now(UTC)
    document = await db.scalar(select(PlatformConfigDocument).where(
        PlatformConfigDocument.namespace == _namespace(module)
    ))
    policy = {
        "configured": document is not None,
        "currentVersion": document.current_version if document else 0,
        "updatedAt": document.updated_at.isoformat() if document else None,
        "hasOwner": bool((document.payload or {}).get("owner")) if document else False,
        "hasReviewCycle": bool((document.payload or {}).get("reviewCycle")) if document else False,
    }

    if module == "data_governance":
        total = int(await db.scalar(select(func.count(StorageObject.id))) or 0)
        retained = int(await db.scalar(select(func.count(StorageObject.id)).where(
            StorageObject.retention_until.is_not(None), StorageObject.retention_until > now
        )) or 0)
        quarantined = int(await db.scalar(select(func.count(StorageObject.id)).where(
            or_(StorageObject.state == StorageObjectState.quarantined, StorageObject.av_status == StorageAVStatus.infected)
        )) or 0)
        deleted = int(await db.scalar(select(func.count(StorageObject.id)).where(
            StorageObject.deleted_at.is_not(None)
        )) or 0)
        return {
            "module": module, "generatedAt": now.isoformat(), "policy": policy,
            "state": "observed" if total else "insufficient_data",
            "metrics": [
                {"key": "objects", "label": "Objetos registrados", "value": total, "quality": "observed"},
                {"key": "retained", "label": "Com retenção vigente", "value": retained, "quality": "observed"},
                {"key": "quarantined", "label": "Em quarentena/contaminados", "value": quarantined, "quality": "observed"},
                {"key": "deleted", "label": "Exclusões lógicas", "value": deleted, "quality": "observed"},
            ],
            "facts": [
                {"label": "Fonte", "value": "storage_objects"},
                {"label": "Escopo", "value": "Metadados de ciclo de vida; conteúdo privado não é exposto."},
            ],
        }

    event_count = int(await db.scalar(select(func.count(AuthSecurityEvent.id))) or 0)
    denied_count = int(await db.scalar(select(func.count(AuthSecurityEvent.id)).where(
        AuthSecurityEvent.outcome.in_(("denied", "failed"))
    )) or 0)
    if module == "compliance":
        return {
            "module": module, "generatedAt": now.isoformat(), "policy": policy,
            "state": "observed" if event_count else "insufficient_data",
            "metrics": [
                {"key": "audit_events", "label": "Eventos auditáveis", "value": event_count, "quality": "observed"},
                {"key": "denied_failed", "label": "Eventos negados/falhos", "value": denied_count, "quality": "observed"},
                {"key": "policy_versions", "label": "Versões da política", "value": document.current_version if document else 0, "quality": "observed"},
            ],
            "facts": [
                {"label": "Declaração", "value": "Evidência interna; não representa certificação ou parecer jurídico."},
                {"label": "Fonte", "value": "auth_security_events e política versionada"},
            ],
        }

    release_versions = int(await db.scalar(select(func.count(PlatformConfigDocumentVersion.id)).where(
        PlatformConfigDocumentVersion.document_id == document.id
    )) or 0) if document else 0
    payload = dict(document.payload or {}) if document else {}
    rollback_configured = bool(payload.get("rollback") or payload.get("rollbackPlan"))
    return {
        "module": module, "generatedAt": now.isoformat(), "policy": policy,
        "state": "configured" if document else "not_configured",
        "metrics": [
            {"key": "backend_version", "label": "Versão do backend", "value": settings.APP_VERSION, "quality": "runtime"},
            {"key": "release_revisions", "label": "Revisões registradas", "value": release_versions, "quality": "observed"},
            {"key": "rollback", "label": "Plano de rollback", "value": "Configurado" if rollback_configured else "Ausente", "quality": "declared" if rollback_configured else "missing"},
        ],
        "facts": [
            {"label": "Ambiente", "value": settings.ENVIRONMENT or settings.APP_ENV},
            {"label": "Prova externa", "value": "Não verificada por esta leitura."},
        ],
    }


@router.get("/{module}", response_model=PlatformConfigurationDocumentRead)
async def get_governance_document(
    module: str,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _authorize(module, "view", context, db)
    document = await db.scalar(
        select(PlatformConfigDocument).where(
            PlatformConfigDocument.namespace == _namespace(module)
        )
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GOV-004", "message": "Controle ainda não configurado."},
        )
    response.headers["ETag"] = f'"{document.etag}"'
    return document


@router.put("/{module}", response_model=PlatformConfigurationDocumentRead)
async def put_governance_document(
    module: str,
    payload: PlatformConfigurationDocumentWrite,
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    actor = await _authorize(module, "manage", context, db)
    _validate_governance_payload(module, payload.payload)
    namespace = _namespace(module)
    before = await snapshot_platform_config_document(db, namespace, for_update=True)
    try:
        document = await ConfigurationService(db).write_platform(
            namespace=namespace,
            payload=payload.payload,
            actor_user_id=actor.id,
            schema_version=payload.schema_version,
            expected_etag=if_match,
            reason=payload.reason,
        )
        after = await snapshot_platform_config_document(db, namespace)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module=module,
            action="put_governance_document",
            reason=payload.reason or f"Atualização do controle {module}.",
            reason_source="user" if payload.reason else "system",
        )
    except (ConfigConflictError, ConfigValidationError) as exc:
        await db.rollback()
        _config_error(exc)
    response.headers["ETag"] = f'"{document.etag}"'
    return document


@router.get("/{module}/versions", response_model=list[ConfigVersionRead], deprecated=True)
async def list_governance_versions(
    module: str,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _authorize(module, "view", context, db)
    document = await db.scalar(
        select(PlatformConfigDocument).where(
            PlatformConfigDocument.namespace == _namespace(module)
        )
    )
    if document is None:
        return []
    return await ConfigurationService(db).platform_history(document.id)


@router.get("/{module}/versions/page", response_model=ConfigVersionPageRead)
async def page_governance_versions(
    module: str,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _authorize(module, "view", context, db)
    document = await db.scalar(
        select(PlatformConfigDocument).where(
            PlatformConfigDocument.namespace == _namespace(module)
        )
    )
    if document is None:
        return ConfigVersionPageRead(
            items=[], total=0, limit=limit, offset=offset, has_more=False
        )
    items, total = await ConfigurationService(db).page_platform_history(
        document.id,
        limit=limit,
        offset=offset,
    )
    return ConfigVersionPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@router.post("/{module}/rollback/{version}", response_model=PlatformConfigurationDocumentRead)
async def rollback_governance_document(
    module: str,
    version: int,
    response: Response,
    request: Request,
    context: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    actor = await _authorize(module, "manage", context, db)
    namespace = _namespace(module)
    before = await snapshot_platform_config_document(db, namespace, for_update=True)
    document = await db.scalar(
        select(PlatformConfigDocument).where(
            PlatformConfigDocument.namespace == namespace
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    try:
        restored = await ConfigurationService(db).rollback_platform(
            document=document,
            target_version=version,
            actor_user_id=actor.id,
            expected_etag=if_match,
        )
        after = await snapshot_platform_config_document(db, namespace)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module=module,
            action="rollback_governance_document",
            reason=f"Restauração do controle {module} para a versão {version}.",
            reason_source="system",
        )
    except (ConfigConflictError, ConfigValidationError) as exc:
        await db.rollback()
        _config_error(exc)
    response.headers["ETag"] = f'"{restored.etag}"'
    return restored
