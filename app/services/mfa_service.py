"""MFA simulada local e sessões administrativas persistentes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_admin_session_token, verify_token
from app.models.auth_security import (
    AdministrativeSession,
    AuthSecurityEvent,
    MfaChallenge,
    MfaChallengePurpose,
    MfaCredential,
    MfaRecoveryCode,
    MfaVerificationMethod,
)
from app.models.user import AgentType, User
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.auth_security import (
    AdministrativeSessionRead,
    AdministrativeSessionTokenRead,
    AuthSecurityEventRead,
    MfaChallengeRead,
    MfaEnrollmentCompleteRead,
    MfaLoginChallengeRead,
    MfaStatusRead,
    MfaVerificationRequest,
)
from app.services.auth_rate_limit_service import (
    opaque_auth_digest,
    request_audit_digests,
)
from app.services.integration_secrets import IntegrationSecretCipher

MFA_CHALLENGE_TTL_SECONDS = 5 * 60
MFA_CHALLENGE_ATTEMPTS = 5
ADMIN_SESSION_TTL_SECONDS = 15 * 60
RECOVERY_CODE_COUNT = 8
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_SENTINEL = "totp"


@dataclass(frozen=True)
class VerifiedMfaChallenge:
    user_id: UUID
    workspace_id: UUID | None
    method: MfaVerificationMethod


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalize_code(value: str) -> str:
    return "".join(value.strip().upper().split())


def _challenge_digest(challenge_id: UUID, code: str) -> str:
    return opaque_auth_digest(f"mfa-challenge:{challenge_id}", _normalize_code(code))


def _recovery_digest(user_id: UUID, code: str) -> str:
    return opaque_auth_digest(f"mfa-recovery:{user_id}", _normalize_code(code))


def _bound_token_digest(token_id: str | None) -> str | None:
    return opaque_auth_digest("mfa-bound-token", token_id) if token_id else None


def _totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** TOTP_DIGITS)
    return f"{value:0{TOTP_DIGITS}d}"


def _totp_counter(at: datetime) -> int:
    return int(at.timestamp()) // TOTP_PERIOD_SECONDS


def _invalid_mfa() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "AUTH-016",
            "message": "Challenge MFA inválido, expirado ou já utilizado.",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def _mfa_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "AUTH-017",
            "message": "MFA local precisa estar habilitada para esta operação.",
        },
    )


class LocalMfaService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _credential(self, user_id: UUID, *, lock: bool = False) -> MfaCredential | None:
        statement = select(MfaCredential).where(MfaCredential.user_id == user_id)
        if lock:
            statement = statement.with_for_update()
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def is_enabled(self, user_id: UUID) -> bool:
        credential = await self._credential(user_id)
        return bool(credential and credential.is_enabled)

    async def require_active_human(self, user_id: UUID) -> User:
        user = (
            await self.db.execute(
                select(User).where(
                    User.id == user_id,
                    User.agent_type == AgentType.human,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH-012", "message": "Permissão insuficiente."},
            )
        return user

    async def require_administrator(
        self,
        user_id: UUID,
        *,
        workspace_id: UUID | None,
    ) -> User:
        user = await self.require_active_human(user_id)
        if user.is_platform_admin or (user.is_internal_staff and user.platform_access_active):
            return user
        if workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH-012", "message": "Permissão insuficiente."},
            )
        admin_membership = await self.db.scalar(
            select(WorkspaceMembership.id)
            .where(
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                WorkspaceMembership.role.in_(
                    {
                        WorkspaceRole.workspace_owner,
                        WorkspaceRole.workspace_admin,
                    }
                ),
            )
            .limit(1)
        )
        if admin_membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH-012", "message": "Permissão insuficiente."},
            )
        return user

    async def status(self, user_id: UUID) -> MfaStatusRead:
        credential = await self._credential(user_id)
        remaining = await self._recovery_codes_remaining(user_id, credential)
        return MfaStatusRead(
            enabled=bool(credential and credential.is_enabled),
            simulated=settings.uses_local_infrastructure,
            recovery_codes_remaining=remaining,
        )

    async def begin_enrollment(
        self,
        user_id: UUID,
        *,
        token_id: str | None,
        request: Request,
    ) -> MfaChallengeRead:
        self._require_bound_access_token(token_id)
        await self.require_active_human(user_id)
        credential = await self._credential(user_id, lock=True)
        if credential and credential.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "AUTH-015", "message": "MFA já está habilitada."},
            )
        if credential is None:
            credential = MfaCredential(user_id=user_id, is_enabled=False)
            self.db.add(credential)
            await self.db.flush()
        if settings.uses_local_infrastructure:
            challenge, code = await self._create_challenge(
                user_id,
                purpose=MfaChallengePurpose.enrollment,
                request=request,
                bound_token_id=token_id,
            )
            provisioning_uri = None
            manual_entry_key = None
        else:
            secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
            credential.totp_secret_encrypted = IntegrationSecretCipher().encrypt(secret)
            credential.last_totp_counter = None
            challenge, code = await self._create_totp_challenge(
                user_id,
                purpose=MfaChallengePurpose.enrollment,
                request=request,
                bound_token_id=token_id,
            )
            user = await self.require_active_human(user_id)
            issuer = settings.APP_NAME
            label = f"{issuer}:{user.email}"
            provisioning_uri = (
                f"otpauth://totp/{quote(label)}?secret={secret}&issuer={quote(issuer)}"
                f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
            )
            manual_entry_key = secret
        await self.db.commit()
        return await self._challenge_read(
            challenge,
            code=code,
            provisioning_uri=provisioning_uri,
            manual_entry_key=manual_entry_key,
        )

    async def complete_enrollment(
        self,
        challenge_id: UUID,
        user_id: UUID,
        verification: MfaVerificationRequest,
        *,
        token_id: str | None,
        request: Request,
    ) -> MfaEnrollmentCompleteRead:
        self._require_bound_access_token(token_id)
        if verification.recovery_code:
            raise _invalid_mfa()
        await self.require_active_human(user_id)
        await self._verify_challenge(
            challenge_id,
            verification,
            expected_purpose=MfaChallengePurpose.enrollment,
            expected_user_id=user_id,
            expected_token_id=token_id,
            request=request,
        )
        credential = await self._credential(user_id, lock=True)
        if credential is None:
            raise _invalid_mfa()
        now = datetime.now(UTC)
        credential.is_enabled = True
        credential.enabled_at = now
        credential.recovery_generation += 1
        await self.db.execute(
            update(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.revoked_at.is_(None),
                MfaRecoveryCode.used_at.is_(None),
            )
            .values(revoked_at=now)
        )
        recovery_codes = self._generate_recovery_codes(
            user_id,
            generation=credential.recovery_generation,
        )
        self._audit(
            "mfa_enabled",
            "success",
            request=request,
            actor_user_id=user_id,
            subject_user_id=user_id,
            challenge_id=challenge_id,
            details={"recovery_codes_issued": len(recovery_codes)},
        )
        await self.db.commit()
        return MfaEnrollmentCompleteRead(
            enabled=True,
            simulated=settings.uses_local_infrastructure,
            recovery_codes_remaining=len(recovery_codes),
            recovery_codes=recovery_codes,
        )

    async def begin_login(
        self,
        user_id: UUID,
        *,
        workspace_id: UUID | None,
        request: Request,
    ) -> MfaLoginChallengeRead:
        if not await self.is_enabled(user_id):
            raise _mfa_required()
        if settings.uses_local_infrastructure:
            challenge, code = await self._create_challenge(
                user_id,
                purpose=MfaChallengePurpose.login,
                workspace_id=workspace_id,
                request=request,
            )
        else:
            challenge, code = await self._create_totp_challenge(
                user_id,
                purpose=MfaChallengePurpose.login,
                workspace_id=workspace_id,
                request=request,
            )
        await self.db.commit()
        read = await self._challenge_read(challenge, code=code)
        return MfaLoginChallengeRead(**read.model_dump())

    async def complete_login(
        self,
        challenge_id: UUID,
        verification: MfaVerificationRequest,
        *,
        request: Request,
    ) -> VerifiedMfaChallenge:
        challenge, method = await self._verify_challenge(
            challenge_id,
            verification,
            expected_purpose=MfaChallengePurpose.login,
            expected_user_id=None,
            expected_token_id=None,
            request=request,
        )
        await self.db.commit()
        return VerifiedMfaChallenge(
            user_id=challenge.user_id,
            workspace_id=challenge.workspace_id,
            method=method,
        )

    async def begin_administrative_session(
        self,
        user_id: UUID,
        *,
        workspace_id: UUID | None,
        token_id: str | None,
        request: Request,
    ) -> MfaChallengeRead:
        self._require_bound_access_token(token_id)
        await self.require_administrator(user_id, workspace_id=workspace_id)
        if not await self.is_enabled(user_id):
            raise _mfa_required()
        if settings.uses_local_infrastructure:
            challenge, code = await self._create_challenge(
                user_id,
                purpose=MfaChallengePurpose.admin_session,
                workspace_id=workspace_id,
                request=request,
                bound_token_id=token_id,
            )
        else:
            challenge, code = await self._create_totp_challenge(
                user_id,
                purpose=MfaChallengePurpose.admin_session,
                workspace_id=workspace_id,
                request=request,
                bound_token_id=token_id,
            )
        await self.db.commit()
        return await self._challenge_read(challenge, code=code)

    async def complete_administrative_session(
        self,
        challenge_id: UUID,
        user_id: UUID,
        verification: MfaVerificationRequest,
        *,
        workspace_id: UUID | None,
        token_id: str | None,
        request: Request,
    ) -> AdministrativeSessionTokenRead:
        self._require_bound_access_token(token_id)
        administrator = await self.require_administrator(
            user_id,
            workspace_id=workspace_id,
        )
        challenge, method = await self._verify_challenge(
            challenge_id,
            verification,
            expected_purpose=MfaChallengePurpose.admin_session,
            expected_user_id=user_id,
            expected_token_id=token_id,
            request=request,
            expected_workspace_id=workspace_id,
            enforce_workspace_match=True,
        )
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ADMIN_SESSION_TTL_SECONDS)
        session_id = uuid4()
        token = create_admin_session_token(
            user_id,
            session_id=session_id,
            expires_at=expires_at,
            mfa_method=method.value,
            auth_epoch=administrator.auth_epoch,
        )
        payload = verify_token(token)
        if payload is None:
            raise RuntimeError("Falha ao validar a sessão administrativa recém-emitida.")
        session = AdministrativeSession(
            id=session_id,
            user_id=user_id,
            challenge_id=challenge.id,
            token_jti=str(payload["jti"]),
            mfa_method=method,
            expires_at=expires_at,
            last_verified_at=now,
        )
        self.db.add(session)
        await self.db.flush()
        self._audit(
            "administrative_session_created",
            "success",
            request=request,
            actor_user_id=user_id,
            subject_user_id=user_id,
            challenge_id=challenge.id,
            administrative_session_id=session.id,
            details={"mfa_method": method.value},
        )
        await self.db.commit()
        await self.db.refresh(session)
        return AdministrativeSessionTokenRead(
            administrative_session_token=token,
            expires_in=ADMIN_SESSION_TTL_SECONDS,
            session=AdministrativeSessionRead.model_validate(session),
        )

    async def verify_administrative_session(
        self,
        token: str,
        *,
        request: Request,
        commit: bool = True,
    ) -> AdministrativeSession:
        payload = verify_token(token)
        try:
            if not payload or payload.get("token_type") != "admin_session":
                raise ValueError
            session_id = UUID(str(payload["session_id"]))
            user_id = UUID(str(payload["sub"]))
            token_jti = str(payload["jti"])
            token_auth_epoch = int(payload["auth_epoch"])
        except (KeyError, TypeError, ValueError) as error:
            raise self._invalid_admin_session() from error

        session = (
            await self.db.execute(
                select(AdministrativeSession)
                .where(
                    AdministrativeSession.id == session_id,
                    AdministrativeSession.user_id == user_id,
                    AdministrativeSession.token_jti == token_jti,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            session is None
            or session.revoked_at is not None
            or _as_utc(session.expires_at) <= now
        ):
            raise self._invalid_admin_session()
        challenge = (
            await self.db.get(MfaChallenge, session.challenge_id)
            if session.challenge_id is not None
            else None
        )
        administrator = await self.require_administrator(
            user_id,
            workspace_id=challenge.workspace_id if challenge else None,
        )
        if token_auth_epoch != administrator.auth_epoch:
            raise self._invalid_admin_session()
        session.last_verified_at = now
        self._audit(
            "administrative_session_verified",
            "success",
            request=request,
            actor_user_id=user_id,
            subject_user_id=user_id,
            administrative_session_id=session.id,
        )
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        await self.db.refresh(session)
        return session

    async def require_linked_administrative_session(
        self,
        session_id: UUID,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
    ) -> AdministrativeSession:
        """Revalidate the MFA session that backs a derived support token.

        A derived token is not sufficient by itself: revoking or expiring the
        short administrative session must invalidate it on the next request.
        Platform administrative sessions are workspace-global (their MFA
        challenge has no workspace); a workspace-bound challenge may only back
        access to that same workspace.
        """
        session = (
            await self.db.execute(
                select(AdministrativeSession)
                .where(
                    AdministrativeSession.id == session_id,
                    AdministrativeSession.user_id == actor_user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            session is None
            or session.revoked_at is not None
            or _as_utc(session.expires_at) <= now
            or session.challenge_id is None
        ):
            raise self._invalid_admin_session()

        challenge = await self.db.get(MfaChallenge, session.challenge_id)
        if (
            challenge is None
            or challenge.user_id != actor_user_id
            or challenge.purpose != MfaChallengePurpose.admin_session
            or challenge.consumed_at is None
            or challenge.revoked_at is not None
            or (
                challenge.workspace_id is not None
                and challenge.workspace_id != workspace_id
            )
        ):
            raise self._invalid_admin_session()

        await self.require_administrator(
            actor_user_id,
            workspace_id=challenge.workspace_id,
        )
        session.last_verified_at = now
        await self.db.flush()
        return session

    async def list_administrative_sessions(
        self,
        user_id: UUID,
    ) -> list[AdministrativeSession]:
        return list(
            (
                await self.db.scalars(
                    select(AdministrativeSession)
                    .where(AdministrativeSession.user_id == user_id)
                    .order_by(AdministrativeSession.created_at.desc())
                    .limit(50)
                )
            ).all()
        )

    async def revoke_administrative_session(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        request: Request,
        reason: str = "user_requested",
    ) -> None:
        session = (
            await self.db.execute(
                select(AdministrativeSession)
                .where(
                    AdministrativeSession.id == session_id,
                    AdministrativeSession.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "AUTH-019", "message": "Sessão não encontrada."},
            )
        if session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            session.revoked_by_user_id = user_id
            session.revocation_reason = reason
            self._audit(
                "administrative_session_revoked",
                "success",
                request=request,
                actor_user_id=user_id,
                subject_user_id=user_id,
                administrative_session_id=session.id,
                details={"reason": reason},
            )
        await self.db.commit()

    async def revoke_all_administrative_sessions(
        self,
        user_id: UUID,
        *,
        actor_user_id: UUID | None,
        reason: str,
        request: Request | None = None,
        commit: bool = True,
    ) -> int:
        now = datetime.now(UTC)
        sessions = list(
            (
                await self.db.scalars(
                    select(AdministrativeSession)
                    .where(
                        AdministrativeSession.user_id == user_id,
                        AdministrativeSession.revoked_at.is_(None),
                        AdministrativeSession.expires_at > now,
                    )
                    .with_for_update()
                )
            ).all()
        )
        for session in sessions:
            session.revoked_at = now
            session.revoked_by_user_id = actor_user_id
            session.revocation_reason = reason
        await self.db.execute(
            update(MfaChallenge)
            .where(
                MfaChallenge.user_id == user_id,
                MfaChallenge.consumed_at.is_(None),
                MfaChallenge.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        self._audit(
            "administrative_sessions_revoked",
            "success",
            request=request,
            actor_user_id=actor_user_id,
            subject_user_id=user_id,
            details={"reason": reason, "revoked_count": len(sessions)},
        )
        if commit:
            await self.db.commit()
        return len(sessions)

    async def security_events(
        self,
        user_id: UUID,
        *,
        limit: int,
    ) -> list[AuthSecurityEventRead]:
        events = list(
            (
                await self.db.scalars(
                    select(AuthSecurityEvent)
                    .where(AuthSecurityEvent.subject_user_id == user_id)
                    .order_by(AuthSecurityEvent.occurred_at.desc())
                    .limit(max(1, min(limit, 100)))
                )
            ).all()
        )
        return [AuthSecurityEventRead.model_validate(event) for event in events]

    async def _create_challenge(
        self,
        user_id: UUID,
        *,
        purpose: MfaChallengePurpose,
        request: Request,
        workspace_id: UUID | None = None,
        bound_token_id: str | None = None,
    ) -> tuple[MfaChallenge, str]:
        now = datetime.now(UTC)
        await self.db.execute(
            update(MfaChallenge)
            .where(
                MfaChallenge.user_id == user_id,
                MfaChallenge.purpose == purpose,
                MfaChallenge.consumed_at.is_(None),
                MfaChallenge.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        challenge_id = uuid4()
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge = MfaChallenge(
            id=challenge_id,
            user_id=user_id,
            workspace_id=workspace_id,
            purpose=purpose,
            code_digest=_challenge_digest(challenge_id, code),
            bound_token_digest=_bound_token_digest(bound_token_id),
            attempts_remaining=MFA_CHALLENGE_ATTEMPTS,
            expires_at=now + timedelta(seconds=MFA_CHALLENGE_TTL_SECONDS),
        )
        self.db.add(challenge)
        await self.db.flush()
        self._audit(
            "mfa_challenge_created",
            "pending",
            request=request,
            actor_user_id=user_id if purpose != MfaChallengePurpose.login else None,
            subject_user_id=user_id,
            challenge_id=challenge.id,
            details={"purpose": purpose.value},
        )
        return challenge, code

    async def _create_totp_challenge(
        self,
        user_id: UUID,
        *,
        purpose: MfaChallengePurpose,
        request: Request,
        workspace_id: UUID | None = None,
        bound_token_id: str | None = None,
    ) -> tuple[MfaChallenge, str]:
        credential = await self._credential(user_id, lock=True)
        if credential is None or not credential.totp_secret_encrypted:
            raise _mfa_required()
        now = datetime.now(UTC)
        await self.db.execute(
            update(MfaChallenge)
            .where(
                MfaChallenge.user_id == user_id,
                MfaChallenge.purpose == purpose,
                MfaChallenge.consumed_at.is_(None),
                MfaChallenge.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        challenge = MfaChallenge(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            purpose=purpose,
            code_digest=TOTP_SENTINEL,
            bound_token_digest=_bound_token_digest(bound_token_id),
            attempts_remaining=MFA_CHALLENGE_ATTEMPTS,
            expires_at=now + timedelta(seconds=MFA_CHALLENGE_TTL_SECONDS),
        )
        self.db.add(challenge)
        await self.db.flush()
        self._audit(
            "mfa_challenge_created",
            "pending",
            request=request,
            actor_user_id=user_id if purpose != MfaChallengePurpose.login else None,
            subject_user_id=user_id,
            challenge_id=challenge.id,
            details={"purpose": purpose.value, "method": "totp"},
        )
        return challenge, ""

    async def _verify_challenge(
        self,
        challenge_id: UUID,
        verification: MfaVerificationRequest,
        *,
        expected_purpose: MfaChallengePurpose,
        expected_user_id: UUID | None,
        expected_token_id: str | None,
        request: Request,
        expected_workspace_id: UUID | None = None,
        enforce_workspace_match: bool = False,
    ) -> tuple[MfaChallenge, MfaVerificationMethod]:
        challenge = (
            await self.db.execute(
                select(MfaChallenge)
                .where(MfaChallenge.id == challenge_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if challenge is None:
            supplied = verification.code or verification.recovery_code or ""
            hmac.compare_digest(
                _challenge_digest(challenge_id, supplied),
                _challenge_digest(challenge_id, "000000"),
            )
            raise _invalid_mfa()

        invalid_state = (
            challenge.purpose != expected_purpose
            or (expected_user_id is not None and challenge.user_id != expected_user_id)
            or (enforce_workspace_match and challenge.workspace_id != expected_workspace_id)
            or challenge.consumed_at is not None
            or challenge.revoked_at is not None
            or challenge.attempts_remaining <= 0
            or _as_utc(challenge.expires_at) <= now
        )
        expected_binding = _bound_token_digest(expected_token_id)
        if challenge.bound_token_digest != expected_binding:
            invalid_state = True
        if invalid_state:
            if challenge.revoked_at is None and _as_utc(challenge.expires_at) <= now:
                challenge.revoked_at = now
                await self.db.commit()
            raise _invalid_mfa()

        method = MfaVerificationMethod.challenge
        valid = False
        if verification.code:
            if challenge.code_digest == TOTP_SENTINEL:
                credential = await self._credential(challenge.user_id, lock=True)
                secret = IntegrationSecretCipher().decrypt(
                    credential.totp_secret_encrypted if credential else None
                )
                current_counter = _totp_counter(now)
                matched_counter: int | None = None
                if secret:
                    for counter in range(current_counter - 1, current_counter + 2):
                        if hmac.compare_digest(
                            _totp_code(secret, counter),
                            _normalize_code(verification.code),
                        ):
                            matched_counter = counter
                            break
                valid = bool(
                    credential
                    and matched_counter is not None
                    and (
                        challenge.purpose == MfaChallengePurpose.enrollment
                        or credential.last_totp_counter is None
                        or matched_counter > credential.last_totp_counter
                    )
                )
                if valid and credential and challenge.purpose != MfaChallengePurpose.enrollment:
                    credential.last_totp_counter = matched_counter
            else:
                valid = hmac.compare_digest(
                    challenge.code_digest,
                    _challenge_digest(challenge.id, verification.code),
                )
        elif verification.recovery_code:
            method = MfaVerificationMethod.recovery
            valid = await self._consume_recovery_code(
                challenge.user_id,
                verification.recovery_code,
                now=now,
            )

        if not valid:
            challenge.attempts_remaining -= 1
            if challenge.attempts_remaining <= 0:
                challenge.revoked_at = now
            self._audit(
                "mfa_challenge_verified",
                "failure",
                request=request,
                actor_user_id=expected_user_id,
                subject_user_id=challenge.user_id,
                challenge_id=challenge.id,
                details={
                    "purpose": challenge.purpose.value,
                    "attempts_remaining": challenge.attempts_remaining,
                },
            )
            await self.db.commit()
            raise _invalid_mfa()

        challenge.consumed_at = now
        self._audit(
            "mfa_challenge_verified",
            "success",
            request=request,
            actor_user_id=expected_user_id,
            subject_user_id=challenge.user_id,
            challenge_id=challenge.id,
            details={
                "purpose": challenge.purpose.value,
                "method": method.value,
            },
        )
        return challenge, method

    async def _consume_recovery_code(
        self,
        user_id: UUID,
        supplied_code: str,
        *,
        now: datetime,
    ) -> bool:
        credential = await self._credential(user_id, lock=True)
        if credential is None or not credential.is_enabled:
            return False
        code = (
            await self.db.execute(
                select(MfaRecoveryCode)
                .where(
                    MfaRecoveryCode.user_id == user_id,
                    MfaRecoveryCode.generation == credential.recovery_generation,
                    MfaRecoveryCode.code_digest
                    == _recovery_digest(user_id, supplied_code),
                    MfaRecoveryCode.used_at.is_(None),
                    MfaRecoveryCode.revoked_at.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if code is None:
            return False
        code.used_at = now
        return True

    def _generate_recovery_codes(self, user_id: UUID, *, generation: int) -> list[str]:
        raw_codes: list[str] = []
        for _ in range(RECOVERY_CODE_COUNT):
            value = secrets.token_hex(6).upper()
            raw_code = f"KLM-{value[:4]}-{value[4:8]}-{value[8:]}"
            raw_codes.append(raw_code)
            self.db.add(
                MfaRecoveryCode(
                    user_id=user_id,
                    generation=generation,
                    code_digest=_recovery_digest(user_id, raw_code),
                )
            )
        return raw_codes

    async def _challenge_read(
        self,
        challenge: MfaChallenge,
        *,
        code: str,
        provisioning_uri: str | None = None,
        manual_entry_key: str | None = None,
    ) -> MfaChallengeRead:
        remaining = await self._recovery_codes_remaining(
            challenge.user_id,
            await self._credential(challenge.user_id),
        )
        return MfaChallengeRead(
            challenge_id=challenge.id,
            purpose=challenge.purpose,
            expires_in=MFA_CHALLENGE_TTL_SECONDS,
            attempts_remaining=challenge.attempts_remaining,
            recovery_available=remaining > 0,
            simulated_code=code or None,
            provisioning_uri=provisioning_uri,
            manual_entry_key=manual_entry_key,
        )

    async def _recovery_codes_remaining(
        self,
        user_id: UUID,
        credential: MfaCredential | None,
    ) -> int:
        if credential is None or not credential.is_enabled:
            return 0
        value = await self.db.scalar(
            select(func.count(MfaRecoveryCode.id)).where(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.generation == credential.recovery_generation,
                MfaRecoveryCode.used_at.is_(None),
                MfaRecoveryCode.revoked_at.is_(None),
            )
        )
        return int(value or 0)

    def _audit(
        self,
        event_type: str,
        outcome: str,
        *,
        request: Request | None,
        actor_user_id: UUID | None,
        subject_user_id: UUID | None,
        challenge_id: UUID | None = None,
        administrative_session_id: UUID | None = None,
        details: dict | None = None,
    ) -> None:
        network_digest = None
        user_agent_digest = None
        if request is not None:
            network_digest, user_agent_digest = request_audit_digests(request)
        self.db.add(
            AuthSecurityEvent(
                event_type=event_type,
                outcome=outcome,
                actor_user_id=actor_user_id,
                subject_user_id=subject_user_id,
                challenge_id=challenge_id,
                administrative_session_id=administrative_session_id,
                network_digest=network_digest,
                user_agent_digest=user_agent_digest,
                details=details or {},
            )
        )

    @staticmethod
    def _require_local_simulation() -> None:
        if not settings.uses_local_infrastructure:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "AUTH-020",
                    "message": "MFA simulada está disponível apenas no runtime local.",
                },
            )

    @staticmethod
    def _require_bound_access_token(token_id: str | None) -> None:
        if not token_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "AUTH-002",
                    "message": "Token de acesso inválido para challenge MFA.",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

    @staticmethod
    def _invalid_admin_session() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH-018",
                "message": "Sessão administrativa inválida, expirada ou revogada.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
