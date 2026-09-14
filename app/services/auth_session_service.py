"""Familias persistentes de sessoes normais e rotacao de refresh."""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import time_ns
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_session_token_id,
)
from app.models.identity import UserSession
from app.schemas.identity import UserSessionRead
from app.services.auth_rate_limit_service import opaque_auth_digest, request_audit_digests


@dataclass(frozen=True)
class IssuedSessionTokens:
    access_token: str
    refresh_token: str
    session: UserSession
    session_issued_at_ns: int


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _invalid_session() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "AUTH-009", "message": "Sessao revogada. Faca login novamente."},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _device_metadata(request: Request | None) -> tuple[str, str, str, str, str | None, str | None]:
    if request is None:
        return "Dispositivo desconhecido", "unknown", "Desconhecido", "Desconhecido", None, None
    user_agent = request.headers.get("user-agent", "")[:512]
    lowered = user_agent.casefold()
    if "mobile" in lowered or "android" in lowered or "iphone" in lowered:
        device_type = "mobile"
    elif "tablet" in lowered or "ipad" in lowered:
        device_type = "tablet"
    else:
        device_type = "desktop"

    browser = "Firefox" if "firefox" in lowered else "Safari" if "safari" in lowered and "chrome" not in lowered else "Chrome" if "chrome" in lowered else "Desconhecido"
    operating_system = "Windows" if "windows" in lowered else "macOS" if "mac os" in lowered or "macintosh" in lowered else "Android" if "android" in lowered else "iOS" if "iphone" in lowered or "ipad" in lowered else "Linux" if "linux" in lowered else "Desconhecido"
    device_label = f"{browser} em {operating_system}"
    network_digest, user_agent_digest = request_audit_digests(request)
    return (
        device_label,
        device_type,
        browser,
        operating_system,
        user_agent_digest,
        network_digest,
    )


class AuthSessionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID | None,
        role: str | None,
        membership_id: UUID | None,
        is_internal_staff: bool,
        auth_epoch: int,
        request: Request | None,
        session_issued_at_ns: int | None = None,
    ) -> IssuedSessionTokens:
        now = datetime.now(UTC)
        metadata = _device_metadata(request)
        family = UserSession(
            id=uuid4(),
            user_id=user_id,
            refresh_jti_digest=opaque_auth_digest("refresh-jti-placeholder", uuid4().hex),
            device_label=metadata[0],
            device_type=metadata[1],
            browser=metadata[2],
            operating_system=metadata[3],
            user_agent_digest=metadata[4],
            network_digest=metadata[5],
            last_workspace_id=workspace_id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
        )
        self.db.add(family)
        await self.db.flush()
        return await self._issue(
            family,
            workspace_id=workspace_id,
            role=role,
            membership_id=membership_id,
            is_internal_staff=is_internal_staff,
            auth_epoch=auth_epoch,
            session_issued_at_ns=session_issued_at_ns or time_ns(),
        )

    async def rotate(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        presented_refresh_jti: str,
        workspace_id: UUID | None,
        role: str | None,
        membership_id: UUID | None,
        is_internal_staff: bool,
        auth_epoch: int,
        session_issued_at_ns: int | None,
        request: Request | None,
    ) -> IssuedSessionTokens:
        family = await self.require_active(session_id, user_id, lock=True, touch=False)
        presented_digest = opaque_auth_digest("refresh-jti", presented_refresh_jti)
        if not hmac.compare_digest(family.refresh_jti_digest, presented_digest):
            family.revoked_at = datetime.now(UTC)
            family.revocation_reason = "refresh_reuse_detected"
            await self.db.commit()
            raise _invalid_session()
        family.last_seen_at = datetime.now(UTC)
        family.last_workspace_id = workspace_id
        if request is not None:
            metadata = _device_metadata(request)
            family.device_label = metadata[0]
            family.device_type = metadata[1]
            family.browser = metadata[2]
            family.operating_system = metadata[3]
            family.user_agent_digest = metadata[4]
            family.network_digest = metadata[5]
        return await self._issue(
            family,
            workspace_id=workspace_id,
            role=role,
            membership_id=membership_id,
            is_internal_staff=is_internal_staff,
            auth_epoch=auth_epoch,
            session_issued_at_ns=session_issued_at_ns or time_ns(),
        )

    async def switch_workspace(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        role: str,
        membership_id: UUID,
        is_internal_staff: bool,
        auth_epoch: int,
        session_issued_at_ns: int | None,
    ) -> IssuedSessionTokens:
        family = await self.require_active(session_id, user_id, lock=True, touch=False)
        family.last_seen_at = datetime.now(UTC)
        family.last_workspace_id = workspace_id
        return await self._issue(
            family,
            workspace_id=workspace_id,
            role=role,
            membership_id=membership_id,
            is_internal_staff=is_internal_staff,
            auth_epoch=auth_epoch,
            session_issued_at_ns=session_issued_at_ns or time_ns(),
        )

    async def require_active(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        lock: bool = False,
        touch: bool = True,
    ) -> UserSession:
        statement = select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        family = (await self.db.execute(statement)).scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            family is None
            or family.revoked_at is not None
            or _utc(family.expires_at) <= now
        ):
            raise _invalid_session()
        if touch and (now - _utc(family.last_seen_at)) >= timedelta(seconds=30):
            family.last_seen_at = now
        return family

    async def list(self, user_id: UUID, *, current_session_id: UUID | None) -> list[UserSessionRead]:
        sessions = list(
            (
                await self.db.scalars(
                    select(UserSession)
                    .where(UserSession.user_id == user_id)
                    .order_by(UserSession.last_seen_at.desc(), UserSession.created_at.desc())
                )
            ).all()
        )
        return [
            UserSessionRead(
                id=item.id,
                device_label=item.device_label,
                device_type=item.device_type,
                browser=item.browser,
                operating_system=item.operating_system,
                created_at=item.created_at,
                last_seen_at=item.last_seen_at,
                expires_at=item.expires_at,
                revoked_at=item.revoked_at,
                revocation_reason=item.revocation_reason,
                current=item.id == current_session_id,
            )
            for item in sessions
        ]

    async def revoke(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        reason: str,
        missing_ok: bool = False,
    ) -> bool:
        family = (
            await self.db.execute(
                select(UserSession)
                .where(UserSession.id == session_id, UserSession.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if family is None:
            if missing_ok:
                return False
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "AUTH-SESSION-NOT-FOUND", "message": "Sessao nao encontrada."},
            )
        if family.revoked_at is None:
            family.revoked_at = datetime.now(UTC)
            family.revocation_reason = reason
            return True
        return False

    async def revoke_all(
        self,
        *,
        user_id: UUID,
        reason: str,
        except_session_id: UUID | None = None,
    ) -> int:
        return len(
            await self.revoke_all_session_ids(
                user_id=user_id,
                reason=reason,
                except_session_id=except_session_id,
            )
        )

    async def revoke_all_session_ids(
        self,
        *,
        user_id: UUID,
        reason: str,
        except_session_id: UUID | None = None,
    ) -> list[UUID]:
        statement = select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        if except_session_id is not None:
            statement = statement.where(UserSession.id != except_session_id)
        sessions = list((await self.db.scalars(statement.with_for_update())).all())
        now = datetime.now(UTC)
        for family in sessions:
            family.revoked_at = now
            family.revocation_reason = reason
        return [family.id for family in sessions]

    async def _issue(
        self,
        family: UserSession,
        *,
        workspace_id: UUID | None,
        role: str | None,
        membership_id: UUID | None,
        is_internal_staff: bool,
        auth_epoch: int,
        session_issued_at_ns: int,
    ) -> IssuedSessionTokens:
        access_jti = create_session_token_id(
            family.user_id,
            issued_at_ns=session_issued_at_ns,
        )
        refresh_jti = create_session_token_id(
            family.user_id,
            issued_at_ns=session_issued_at_ns,
        )
        access_token = create_access_token(
            family.user_id,
            workspace_id=workspace_id,
            role=role,
            membership_id=membership_id,
            is_internal_staff=is_internal_staff,
            session_issued_at_ns=session_issued_at_ns,
            session_id=family.id,
            token_id=access_jti,
            auth_epoch=auth_epoch,
        )
        refresh_token = create_refresh_token(
            family.user_id,
            workspace_id=workspace_id,
            role=role,
            membership_id=membership_id,
            is_internal_staff=is_internal_staff,
            session_issued_at_ns=session_issued_at_ns,
            session_id=family.id,
            token_id=refresh_jti,
            auth_epoch=auth_epoch,
        )
        family.refresh_jti_digest = opaque_auth_digest("refresh-jti", refresh_jti)
        await self.db.flush()
        return IssuedSessionTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            session=family,
            session_issued_at_ns=session_issued_at_ns,
        )
