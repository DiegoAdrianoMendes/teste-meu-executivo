"""Lifecycle transacional de cadastro, convite e recuperacao de senha."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.identity_surface import assert_workspace_identity
from app.core.password_policy import validate_password
from app.core.security import hash_password, verify_password_async
from app.db.identity_tenant import (
    resolve_signup_verification_workspace,
    resolve_workspace_invitation_workspace,
)
from app.db.tenant import set_workspace_context
from app.models.identity import (
    IdentityOutboxMessage,
    IdentityOutboxPurpose,
    InvitationStatus,
    SignupVerification,
    WorkspaceInvitation,
)
from app.models.password_reset import PasswordResetToken
from app.models.user import AgentType, User, UserRole, normalize_email
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.identity import (
    IdentityRequestAcceptedRead,
    InvitationAcceptedRead,
    InvitationAcceptRequest,
    InvitationPreviewRead,
    InvitationRead,
    MessageRead,
    PublicIdentityRequestAcceptedRead,
    ResetPasswordRequest,
    SignupResendRequest,
    SignupVerificationRead,
    SignupVerificationRequest,
)
from app.schemas.user import RegisterRequest
from app.schemas.workspace import TeamMemberCreate
from app.services.auth_rate_limit_service import opaque_auth_digest
from app.services.auth_revocation_service import AuthRevocationService
from app.services.auth_service import AuthService
from app.services.auth_session_service import AuthSessionService
from app.services.commercial_legacy_bridge_service import (
    CanonicalContractCommand,
    CanonicalOfferUnavailableError,
    canonical_sku,
)
from app.services.identity_outbox_service import (
    IdentityIdempotencyService,
    LocalIdentityOutboxService,
)
from app.services.mfa_service import LocalMfaService
from app.services.workspace_policy_service import build_default_workspace_settings
from app.services.workspace_service import WorkspaceService, slugify

SIGNUP_TTL = timedelta(minutes=30)
INVITATION_TTL = timedelta(days=7)
PASSWORD_RESET_TTL = timedelta(hours=1)
GENERIC_SIGNUP_MESSAGE = "Se o cadastro puder prosseguir, enviaremos as instrucoes de verificacao."
GENERIC_RESET_MESSAGE = "Se o email estiver cadastrado, a entrega local estara disponivel."
INVITATION_MAX_FAILED_ATTEMPTS = 3


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _token_digest(purpose: str, token: str) -> str:
    return opaque_auth_digest(f"{purpose}:token", token)


def _code_digest(purpose: str, resource_id: UUID, code: str) -> str:
    normalized = "".join(code.strip().upper().split())
    return opaque_auth_digest(f"{purpose}:code:{resource_id}", normalized)


def _new_secret() -> tuple[str, str]:
    return secrets.token_urlsafe(32), f"{secrets.randbelow(1_000_000):06d}"


def _initials(name: str) -> str:
    parts = name.strip().split()
    return "".join(part[0].upper() for part in parts[:2])[:4] or "U"


def _invalid_verification() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "AUTH-VERIFICATION-INVALID",
            "message": "Verificacao invalida, expirada ou ja utilizada.",
        },
    )


def _invalid_invitation() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "AUTH-INVITATION-INVALID",
            "message": "Convite invalido, expirado ou revogado.",
        },
    )



# Coluna identity_idempotency_records.operation é String(100). A chave anterior,
# "workspace-invitation-resend:<workspace>:<convite>", tinha 101 caracteres (28 de
# prefixo, dois UUIDs de 36 e dois separadores) e estourava a coluna no PostgreSQL
# de produção com StringDataRightTruncationError; no SQLite dos testes passava.
IDENTITY_IDEMPOTENCY_OPERATION_MAX_LENGTH = 100


def invitation_resend_operation(workspace_id: UUID | str, invitation_id: UUID | str) -> str:
    operation = f"invitation-resend:{workspace_id}:{invitation_id}"
    if len(operation) > IDENTITY_IDEMPOTENCY_OPERATION_MAX_LENGTH:
        raise ValueError(
            "Chave de idempotência do reenvio de convite excede a coluna operation."
        )
    return operation

class IdentityLifecycleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.idempotency = IdentityIdempotencyService(db)
        self.outbox = LocalIdentityOutboxService(db)

    async def request_signup(
        self,
        payload: RegisterRequest,
        *,
        idempotency_key: str,
    ) -> IdentityRequestAcceptedRead:
        normalized_email = normalize_email(str(payload.email))
        try:
            plan_sku = canonical_sku(payload.plan)
        except CanonicalOfferUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "BILLING-422", "message": str(error)},
            ) from error

        prepared_password_hash = await asyncio.to_thread(hash_password, payload.password)

        reservation = await self.idempotency.reserve(
            operation="signup",
            key=idempotency_key,
            request_payload={
                "email": normalized_email,
                "name": payload.name.strip(),
                "workspace_name": payload.workspace_name.strip(),
                "plan": plan_sku,
                "password_digest": opaque_auth_digest("signup-password", payload.password),
            },
        )
        if reservation.replay:
            return IdentityRequestAcceptedRead.model_validate(reservation.record.response_data)

        await self._lock_identity_email(normalized_email)
        now = datetime.now(UTC)
        expires_at = now + SIGNUP_TTL
        user = await self.db.scalar(
            select(User)
            .where(func.lower(User.email) == normalized_email, User.deleted_at.is_(None))
            .with_for_update()
        )
        verification: SignupVerification | None = None
        if user is None:
            user = User(
                name=payload.name.strip(),
                email=normalized_email,
                hashed_password=prepared_password_hash,
                agent_type=AgentType.human,
                role=UserRole.admin,
                initials=_initials(payload.name),
                color="#1a6fa5",
                is_active=False,
                email_verified_at=None,
            )
            self.db.add(user)
            await self.db.flush()
            workspace = Workspace(
                name=payload.workspace_name.strip(),
                slug=f"{slugify(payload.workspace_name)}-{uuid4().hex[:8]}",
                status=WorkspaceStatus.provisioning,
                setup_mode=WorkspaceSetupMode.self_service,
                is_active=False,
                settings=build_default_workspace_settings(onboarding_mode="self_service"),
            )
            self.db.add(workspace)
            await self.db.flush()
            await set_workspace_context(self.db, workspace.id)
            membership = WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.workspace_owner,
                status=MembershipStatus.invited,
                is_default=True,
                access_package="office_owner",
                permission_grants=[],
                permission_denials=[],
            )
            self.db.add(membership)
            await self.db.flush()
            verification = SignupVerification(
                id=uuid4(),
                user_id=user.id,
                workspace_id=workspace.id,
                token_digest=opaque_auth_digest("signup-placeholder", uuid4().hex),
                code_digest=opaque_auth_digest("signup-placeholder", uuid4().hex),
                plan_tier=plan_sku,
                expires_at=expires_at,
            )
            self.db.add(verification)
            await self.db.flush()
        elif (
            not user.is_active
            and user.hashed_password
            and await self._bind_pending_signup_tenant(normalized_email) is not None
        ):
            verification = await self._pending_signup(user.id, lock=True)
            if verification is not None:
                user.hashed_password = prepared_password_hash
                verification.revoked_at = now
                await self.db.flush()
                replacement = SignupVerification(
                    id=uuid4(),
                    user_id=verification.user_id,
                    workspace_id=verification.workspace_id,
                    token_digest=opaque_auth_digest("signup-placeholder", uuid4().hex),
                    code_digest=opaque_auth_digest("signup-placeholder", uuid4().hex),
                    plan_tier=verification.plan_tier,
                    expires_at=expires_at,
                )
                self.db.add(replacement)
                await self.db.flush()
                verification = replacement

        delivery_id = uuid4()
        token, code = _new_secret()
        if verification is not None:
            verification.token_digest = _token_digest("signup", token)
            verification.code_digest = _code_digest("signup", verification.id, code)
            link = f"/verify-account?delivery={delivery_id}&token={token}"
            related_id = verification.id
        else:
            link = f"/verify-account?delivery={delivery_id}&token={token}"
            related_id = None
        delivery = await self.outbox.enqueue(
            message_id=delivery_id,
            purpose=IdentityOutboxPurpose.signup_verification,
            recipient_email=normalized_email,
            payload={"code": code, "link": link},
            related_type="signup_verification",
            related_id=related_id,
            expires_at=expires_at,
        )
        if verification is not None:
            verification.last_delivery_id = delivery.id

        accepted = IdentityRequestAcceptedRead(
            request_id=reservation.record.id,
            message=(
                "Se o cadastro puder prosseguir, a verificacao local estara disponivel."
                if settings.uses_local_infrastructure
                else GENERIC_SIGNUP_MESSAGE
            ),
            delivery_mode=(
                "privileged_local_simulation"
                if settings.uses_local_infrastructure
                else "transactional_email"
            ),
            delivery_id=delivery.id,
            expires_at=expires_at,
        )
        await self.idempotency.finish(
            reservation.record,
            accepted.model_dump(mode="json"),
        )
        # O token e o outbox cifrado precisam existir antes da chamada ao SES.
        # Assim, uma falha do provider deixa uma entrega retryable e nunca um
        # recibo apontando para um token que foi perdido por rollback.
        await self.db.commit()
        if not settings.uses_local_infrastructure and verification is not None:
            await self.outbox.deliver_message(delivery)
            await self.db.commit()
        return accepted

    async def resend_signup_verification(
        self,
        payload: SignupResendRequest,
        *,
        idempotency_key: str,
    ) -> PublicIdentityRequestAcceptedRead:
        normalized_email = normalize_email(str(payload.email))
        reservation = await self.idempotency.reserve(
            operation="signup_resend",
            key=idempotency_key,
            request_payload={"email": normalized_email},
        )
        if reservation.replay:
            return PublicIdentityRequestAcceptedRead.model_validate(
                reservation.record.response_data
            )

        await self._lock_identity_email(normalized_email)
        now = datetime.now(UTC)
        expires_at = now + SIGNUP_TTL
        delivery: IdentityOutboxMessage | None = None
        if await self._bind_pending_signup_tenant(normalized_email) is not None:
            user = await self.db.scalar(
                select(User)
                .where(
                    func.lower(User.email) == normalized_email,
                    User.is_active.is_(False),
                    User.deleted_at.is_(None),
                )
                .with_for_update()
            )
            verification = (
                await self._pending_signup(user.id, lock=True)
                if user is not None
                else None
            )
            if verification is not None:
                previous_delivery_id = verification.last_delivery_id
                token, code = _new_secret()
                delivery_id = uuid4()
                verification.token_digest = _token_digest("signup", token)
                verification.code_digest = _code_digest("signup", verification.id, code)
                verification.expires_at = expires_at
                delivery = await self.outbox.enqueue(
                    message_id=delivery_id,
                    purpose=IdentityOutboxPurpose.signup_verification,
                    recipient_email=normalized_email,
                    payload={
                        "code": code,
                        "link": f"/verify-account?delivery={delivery_id}&token={token}",
                    },
                    related_type="signup_verification",
                    related_id=verification.id,
                    expires_at=expires_at,
                    request_id=reservation.record.id,
                )
                verification.last_delivery_id = delivery.id
                await self.outbox.destroy(previous_delivery_id)

        accepted = PublicIdentityRequestAcceptedRead(
            request_id=reservation.record.id,
            message=GENERIC_SIGNUP_MESSAGE,
            delivery_mode=(
                "privileged_local_simulation"
                if settings.uses_local_infrastructure
                else "transactional_email"
            ),
            expires_at=expires_at,
        )
        await self.idempotency.finish(reservation.record, accepted.model_dump(mode="json"))
        await self.db.commit()
        if not settings.uses_local_infrastructure and delivery is not None:
            await self.outbox.deliver_message(delivery)
            await self.db.commit()
        return accepted

    async def verify_signup(
        self,
        payload: SignupVerificationRequest,
        *,
        request: Request,
    ) -> SignupVerificationRead:
        await self._bind_signup_tenant(payload)
        verification = await self._signup_from_secret(payload, lock=True)
        now = datetime.now(UTC)
        if verification is None:
            raise _invalid_verification()
        if _utc(verification.expires_at) <= now and verification.consumed_at is None:
            verification.revoked_at = verification.revoked_at or now
            await self.outbox.destroy(verification.last_delivery_id)
            verification.last_delivery_id = None
            await self.db.commit()
            raise _invalid_verification()
        if verification.consumed_at is not None or verification.revoked_at is not None:
            raise _invalid_verification()

        user = (
            await self.db.execute(
                select(User)
                .options(selectinload(User.memberships).selectinload(WorkspaceMembership.workspace))
                .where(User.id == verification.user_id, User.deleted_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        workspace = await self.db.scalar(
            select(Workspace).where(Workspace.id == verification.workspace_id).with_for_update()
        )
        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == verification.workspace_id,
                WorkspaceMembership.user_id == verification.user_id,
            )
            .with_for_update()
        )
        if (
            user is None
            or workspace is None
            or membership is None
            or membership.workspace_id != verification.workspace_id
            or membership.user_id != verification.user_id
            or not user.hashed_password
        ):
            raise _invalid_verification()

        verification.consumed_at = now
        signup_delivery_id = verification.last_delivery_id
        verification.last_delivery_id = None
        verification.token_digest = opaque_auth_digest("signup-erased", uuid4().hex)
        verification.code_digest = opaque_auth_digest("signup-erased", uuid4().hex)
        await self.db.flush()
        await self.outbox.destroy(signup_delivery_id)
        user.email_verified_at = now
        user.is_active = True
        workspace.status = WorkspaceStatus.active
        workspace.is_active = True
        membership.status = MembershipStatus.active
        await CanonicalContractCommand(self.db).provision_trial(
            workspace_id=workspace.id,
            plan=verification.plan_tier,
            idempotency_key=f"signup-verification:{verification.id}",
            actor_user_id=user.id,
        )
        await self.db.flush()
        loaded_user = await AuthService(self.db).get_user_by_id(user.id)
        if loaded_user is None:
            raise _invalid_verification()
        token_response = await AuthService(self.db).issue_new_session(
            loaded_user,
            preferred_workspace_id=workspace.id,
            request=request,
        )
        await self.db.commit()
        return SignupVerificationRead(
            **token_response.model_dump(),
            verification_status="verified",
        )

    async def create_invitation(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        payload: TeamMemberCreate,
        idempotency_key: str,
        permission_grants: list[dict[str, str]],
        permission_denials: list[dict[str, str]],
        access_package: str | None = None,
    ) -> WorkspaceInvitation:
        email = normalize_email(str(payload.email))
        reservation = await self.idempotency.reserve(
            operation=f"workspace-invitation:{workspace_id}",
            key=idempotency_key,
            request_payload={
                "email": email,
                "name": payload.name.strip(),
                "role": payload.workspace_role.value,
                "access_package": access_package,
                "grants": permission_grants,
                "denials": permission_denials,
            },
        )
        if reservation.replay:
            invitation_id = UUID(str(reservation.record.response_data["invitation_id"]))
            replayed = await self.db.get(WorkspaceInvitation, invitation_id)
            if replayed is None:
                raise _invalid_invitation()
            return replayed

        await self._lock_identity_email(email)
        current = await self.db.scalar(
            select(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.invited_email == email,
                WorkspaceInvitation.status == InvitationStatus.pending,
            )
            .with_for_update()
        )
        now = datetime.now(UTC)
        if current is not None and _utc(current.expires_at) > now:
            await self.idempotency.finish(
                reservation.record,
                {"invitation_id": str(current.id)},
            )
            return current
        if current is not None:
            await self._expire_invitation(current, now=now)

        user = await self.db.scalar(
            select(User)
            .where(func.lower(User.email) == email, User.deleted_at.is_(None))
            .with_for_update()
        )
        if user is not None and not user.is_active and user.hashed_password is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TEAM-IDENTITY-PENDING",
                    "message": (
                        "A identidade precisa concluir seu lifecycle atual antes do convite."
                    ),
                },
            )
        if user is None:
            user = User(
                name=payload.name.strip(),
                email=email,
                hashed_password=None,
                agent_type=AgentType.human,
                role=UserRole.lawyer,
                initials=_initials(payload.name),
                color="hsl(217 50% 40%)",
                is_active=False,
                email_verified_at=None,
            )
            self.db.add(user)
            await self.db.flush()
        assert_workspace_identity(user)

        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user.id,
            )
            .with_for_update()
        )
        if membership is not None and membership.status == MembershipStatus.active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TEAM-ALREADY-MEMBER",
                    "message": "A identidade ja pertence ao workspace.",
                },
            )
        await WorkspaceService(self.db)._assert_membership_limit(
            workspace_id,
            exclude_membership_id=membership.id if membership else None,
        )
        if membership is None:
            membership = WorkspaceMembership(
                workspace_id=workspace_id,
                user_id=user.id,
                role=payload.workspace_role,
                status=MembershipStatus.invited,
                is_default=False,
                permission_grants=permission_grants,
                permission_denials=permission_denials,
                access_package=access_package,
            )
            self.db.add(membership)
            await self.db.flush()
        else:
            membership.role = payload.workspace_role
            membership.status = MembershipStatus.invited
            membership.permission_grants = permission_grants
            membership.permission_denials = permission_denials
            membership.access_package = access_package

        invitation = WorkspaceInvitation(
            id=uuid4(),
            workspace_id=workspace_id,
            membership_id=membership.id,
            user_id=user.id,
            invited_by_user_id=actor_user_id,
            invited_email=email,
            invited_name=payload.name.strip(),
            token_digest=opaque_auth_digest("invite-placeholder", uuid4().hex),
            code_digest=opaque_auth_digest("invite-placeholder", uuid4().hex),
            status=InvitationStatus.pending,
            expires_at=now + INVITATION_TTL,
            last_sent_at=now,
            send_count=1,
        )
        self.db.add(invitation)
        await self.db.flush()
        await self._rotate_invitation_delivery(invitation)
        await self.idempotency.finish(
            reservation.record,
            {"invitation_id": str(invitation.id)},
        )
        return invitation

    async def resend_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        idempotency_key: str,
        commit_expiration: bool = True,
    ) -> WorkspaceInvitation:
        invitation = await self._workspace_invitation(workspace_id, invitation_id, lock=True)
        now = datetime.now(UTC)
        if invitation.status == InvitationStatus.pending and _utc(invitation.expires_at) <= now:
            # Pendente vencido: registra a expiração (trilha) e segue para reabrir.
            await self._expire_invitation(invitation, now=now)
            if commit_expiration:
                await self.db.commit()
                # O commit soltou o FOR UPDATE; reobter a trava antes de reabrir, senão
                # dois reenvios concorrentes reabrem o convite duas vezes.
                invitation = await self._workspace_invitation(
                    workspace_id, invitation_id, lock=True
                )
            else:
                await self.db.flush()
        if invitation.status not in (InvitationStatus.pending, InvitationStatus.expired):
            # Revogado ou aceito não se reenvia.
            raise _invalid_invitation()
        reservation = await self.idempotency.reserve(
            operation=invitation_resend_operation(workspace_id, invitation_id),
            key=idempotency_key,
            request_payload={"invitation_id": str(invitation_id)},
        )
        if reservation.replay:
            return invitation
        if invitation.status == InvitationStatus.expired:
            # V18: convite expirado era beco sem saída na tela e no backend. Reabrir
            # = voltar a pendente com prazo e segredos novos (a rotação abaixo renova
            # expires_at e a entrega); send_count conta o reenvio mesmo sem entrega
            # anterior viva, porque os segredos antigos foram apagados ao expirar.
            invitation.status = InvitationStatus.pending
            invitation.send_count += 1
            await self.db.flush()
        await self._rotate_invitation_delivery(invitation)
        await self.idempotency.finish(
            reservation.record,
            {"invitation_id": str(invitation.id)},
        )
        return invitation

    async def revoke_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> WorkspaceInvitation:
        invitation = await self._workspace_invitation(workspace_id, invitation_id, lock=True)
        if invitation.status == InvitationStatus.pending:
            now = datetime.now(UTC)
            invitation.status = InvitationStatus.revoked
            invitation.revoked_at = now
            await self._erase_invitation_secrets(invitation)
            membership = await self.db.get(WorkspaceMembership, invitation.membership_id)
            if membership is not None and membership.status == MembershipStatus.invited:
                membership.status = MembershipStatus.suspended
        return invitation

    async def invitation_preview(
        self,
        invitation_id: UUID,
        *,
        token: str,
    ) -> InvitationPreviewRead:
        await self._bind_invitation_tenant(
            invitation_id,
            token=token,
            delivery_id=None,
        )
        invitation = await self._invitation_by_id(invitation_id, lock=False)
        await self._validate_invitation_binding(invitation)
        if not self._invitation_token_matches(invitation, token):
            raise _invalid_invitation()
        workspace = await self.db.get(Workspace, invitation.workspace_id)
        user = await self.db.get(User, invitation.user_id)
        if workspace is None or user is None:
            raise _invalid_invitation()
        if (
            invitation.status == InvitationStatus.pending
            and _utc(invitation.expires_at) <= datetime.now(UTC)
        ):
            await self._expire_invitation(invitation)
        status_value = invitation.status
        return InvitationPreviewRead(
            invitation_id=invitation.id,
            workspace_name=workspace.name,
            recipient_hint=self._email_hint(invitation.invited_email),
            status=status_value,
            expires_at=invitation.expires_at,
            requires_password=user.hashed_password is None,
        )

    async def accept_invitation(
        self,
        invitation_id: UUID,
        payload: InvitationAcceptRequest,
        *,
        idempotency_key: str,
    ) -> InvitationAcceptedRead:
        reservation = await self.idempotency.reserve(
            operation=f"workspace-invitation-accept:{invitation_id}",
            key=idempotency_key,
            request_payload={
                "invitation_id": str(invitation_id),
                "token_digest": _token_digest("invitation", payload.token or ""),
                "code_digest": opaque_auth_digest("invitation-accept-code", payload.code or ""),
                "name": payload.name,
                "password_digest": opaque_auth_digest(
                    "invitation-password", payload.password or ""
                ),
            },
        )
        if reservation.replay:
            return InvitationAcceptedRead.model_validate(reservation.record.response_data)

        await self._bind_invitation_tenant(
            invitation_id,
            token=payload.token,
            delivery_id=payload.delivery_id if payload.code else None,
        )
        invitation = await self._invitation_by_id(invitation_id, lock=True)
        await self._validate_invitation_binding(invitation)
        now = datetime.now(UTC)
        if invitation.status == InvitationStatus.pending and _utc(invitation.expires_at) <= now:
            await self._expire_invitation(invitation, now=now)
            await self.db.delete(reservation.record)
            await self.db.commit()
            raise _invalid_invitation()
        if invitation.status != InvitationStatus.pending:
            raise _invalid_invitation()
        if not await self._invitation_secret_matches(invitation, payload):
            await self._record_invitation_failure(invitation, now=now)
            await self.db.delete(reservation.record)
            await self.db.commit()
            raise _invalid_invitation()

        membership = await self.db.scalar(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.id == invitation.membership_id)
            .with_for_update()
        )
        user = await self.db.scalar(
            select(User).where(User.id == invitation.user_id).with_for_update()
        )
        if membership is None or user is None or membership.status != MembershipStatus.invited:
            raise _invalid_invitation()
        if (
            membership.workspace_id != invitation.workspace_id
            or membership.user_id != invitation.user_id
            or not user.email
            or user.email != invitation.invited_email
        ):
            raise _invalid_invitation()
        if user.hashed_password is None:
            if payload.password is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "AUTH-INVITATION-PASSWORD-REQUIRED",
                        "message": "Defina uma senha para concluir o convite.",
                    },
                )
            user.hashed_password = await asyncio.to_thread(hash_password, payload.password)
            user.name = (payload.name or invitation.invited_name).strip()
            user.initials = _initials(user.name)
        user.is_active = True
        user.email_verified_at = user.email_verified_at or now
        membership.status = MembershipStatus.active
        active_default = await self.db.scalar(
            select(WorkspaceMembership.id).where(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.status == MembershipStatus.active,
                WorkspaceMembership.is_default.is_(True),
                WorkspaceMembership.id != membership.id,
            )
        )
        if active_default is None:
            membership.is_default = True
        invitation.status = InvitationStatus.accepted
        invitation.accepted_at = now
        accepted = self._invitation_accepted_read(invitation)
        await self._erase_invitation_secrets(invitation)
        await self.idempotency.finish(reservation.record, accepted.model_dump(mode="json"))
        await self.db.commit()
        return accepted

    async def request_password_reset(self, email: str) -> PublicIdentityRequestAcceptedRead:
        normalized_email = normalize_email(email)
        now = datetime.now(UTC)
        expires_at = now + PASSWORD_RESET_TTL
        user = await self.db.scalar(
            select(User)
            .where(
                func.lower(User.email) == normalized_email,
                User.agent_type == AgentType.human,
                User.is_active.is_(True),
                User.email_verified_at.is_not(None),
                User.deleted_at.is_(None),
            )
            .with_for_update()
        )
        reset: PasswordResetToken | None = None
        if user is not None:
            await self.db.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.used_at.is_(None),
                    PasswordResetToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            reset = PasswordResetToken(
                id=uuid4(),
                user_id=user.id,
                token_hash=opaque_auth_digest("reset-placeholder", uuid4().hex),
                expires_at=expires_at,
            )
            self.db.add(reset)
            await self.db.flush()

        request_id = uuid4()
        delivery: IdentityOutboxMessage | None = None
        if reset is not None:
            delivery_id = uuid4()
            token, code = _new_secret()
            reset.token_hash = _token_digest("password-reset", token)
            reset.code_hash = _code_digest("password-reset", reset.id, code)
            delivery = await self.outbox.enqueue(
                message_id=delivery_id,
                purpose=IdentityOutboxPurpose.password_reset,
                recipient_email=normalized_email,
                payload={
                    "code": code,
                    "link": f"/reset-password?token={token}",
                },
                related_type="password_reset",
                related_id=reset.id,
                expires_at=expires_at,
                request_id=request_id,
            )
            reset.last_delivery_id = delivery.id
        accepted = PublicIdentityRequestAcceptedRead(
            request_id=request_id,
            message=(
                GENERIC_RESET_MESSAGE
                if settings.uses_local_infrastructure
                else "Se o email estiver cadastrado, enviaremos as instrucoes de redefinicao."
            ),
            delivery_mode=(
                "privileged_local_simulation"
                if settings.uses_local_infrastructure
                else "transactional_email"
            ),
            expires_at=expires_at,
        )
        # Persist the one-time token and encrypted outbox before crossing the
        # external provider boundary. A provider receipt must never reference
        # a reset token that vanished because the database commit failed.
        await self.db.commit()
        if not settings.uses_local_infrastructure and delivery is not None:
            delivery_status = await self.outbox.deliver_message(delivery)
            await self.db.commit()
            # A resposta pública precisa ser indistinguível para endereços
            # existentes e inexistentes. A falha continua registrada no
            # outbox retryable e nos eventos internos, sem enumerar contas.
            _ = delivery_status
        return accepted

    async def reset_password(
        self,
        payload: ResetPasswordRequest,
        *,
        request: Request,
    ) -> MessageRead:
        validate_password(payload.new_password)
        reset = await self._reset_from_secret(payload, lock=True)
        now = datetime.now(UTC)
        if (
            reset is None
            or reset.used_at is not None
            or reset.revoked_at is not None
            or _utc(reset.expires_at) <= now
        ):
            raise _invalid_verification()
        user = await self.db.scalar(
            select(User)
            .where(
                User.id == reset.user_id,
                User.agent_type == AgentType.human,
                User.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if user is None:
            raise _invalid_verification()
        user.hashed_password = await asyncio.to_thread(hash_password, payload.new_password)
        user.password_change_required = False
        user.temporary_password_issued_at = None
        reset.used_at = now
        reset_delivery_id = reset.last_delivery_id
        reset.last_delivery_id = None
        reset.token_hash = opaque_auth_digest("password-reset-erased", uuid4().hex)
        reset.code_hash = None
        await self.db.flush()
        await self.outbox.destroy(reset_delivery_id)
        await self.db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.id != reset.id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await AuthSessionService(self.db).revoke_all(
            user_id=user.id,
            reason="password_reset",
        )
        revocation_intent = await AuthRevocationService(self.db).advance_epoch(
            user,
            reason="password_reset",
        )
        await LocalMfaService(self.db).revoke_all_administrative_sessions(
            user.id,
            actor_user_id=user.id,
            reason="password_reset",
            request=request,
            commit=False,
        )
        await self.db.commit()
        await AuthRevocationService(self.db).fanout_after_commit(revocation_intent.id)
        return MessageRead(message="Senha redefinida. Faca login novamente.")

    async def change_password(
        self,
        *,
        user_id: UUID,
        current_password: str,
        new_password: str,
        request: Request,
    ) -> MessageRead:
        validate_password(new_password)
        user = await self.db.scalar(
            select(User)
            .where(
                User.id == user_id,
                User.agent_type == AgentType.human,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .with_for_update()
        )
        password_matches = bool(
            user is not None
            and user.hashed_password
            and await verify_password_async(current_password, user.hashed_password)
        )
        if not password_matches:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "AUTH-PASSWORD-INVALID", "message": "A senha atual nao confere."},
            )
        user.hashed_password = await asyncio.to_thread(hash_password, new_password)
        user.password_change_required = False
        user.temporary_password_issued_at = None
        await AuthSessionService(self.db).revoke_all(
            user_id=user.id,
            reason="password_changed",
        )
        revocation_intent = await AuthRevocationService(self.db).advance_epoch(
            user,
            reason="password_changed",
        )
        await LocalMfaService(self.db).revoke_all_administrative_sessions(
            user.id,
            actor_user_id=user.id,
            reason="password_changed",
            request=request,
            commit=False,
        )
        await self.db.commit()
        await AuthRevocationService(self.db).fanout_after_commit(revocation_intent.id)
        return MessageRead(message="Senha alterada. Faca login novamente.")

    async def latest_invitations(
        self,
        workspace_id: UUID,
    ) -> dict[UUID, WorkspaceInvitation]:
        rows = list(
            (
                await self.db.scalars(
                    select(WorkspaceInvitation)
                    .where(WorkspaceInvitation.workspace_id == workspace_id)
                    .order_by(WorkspaceInvitation.created_at.desc())
                )
            ).all()
        )
        result: dict[UUID, WorkspaceInvitation] = {}
        now = datetime.now(UTC)
        for row in rows:
            if row.status == InvitationStatus.pending and _utc(row.expires_at) <= now:
                await self._expire_invitation(row, now=now)
            result.setdefault(row.membership_id, row)
        await self.db.flush()
        return result

    async def _pending_signup(self, user_id: UUID, *, lock: bool) -> SignupVerification | None:
        statement = select(SignupVerification).where(
            SignupVerification.user_id == user_id,
            SignupVerification.consumed_at.is_(None),
            SignupVerification.revoked_at.is_(None),
        )
        if lock:
            statement = statement.with_for_update()
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def _lock_identity_email(self, normalized_email: str) -> None:
        if self.db.get_bind().dialect.name != "postgresql":
            return
        unsigned = int(
            opaque_auth_digest("identity-email-lock", normalized_email)[:16],
            16,
        )
        lock_key = unsigned - 2**64 if unsigned >= 2**63 else unsigned
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    async def _signup_from_secret(
        self,
        payload: SignupVerificationRequest,
        *,
        lock: bool,
    ) -> SignupVerification | None:
        statement = select(SignupVerification)
        if payload.token:
            statement = statement.where(
                SignupVerification.token_digest == _token_digest("signup", payload.token)
            )
        elif payload.delivery_id:
            outbox = await self.db.get(IdentityOutboxMessage, payload.delivery_id)
            if (
                outbox is None
                or outbox.purpose != IdentityOutboxPurpose.signup_verification
                or outbox.related_type != "signup_verification"
                or outbox.related_id is None
            ):
                return None
            statement = statement.where(SignupVerification.id == outbox.related_id)
        else:
            return None
        if lock:
            statement = statement.with_for_update()
        verification = (await self.db.execute(statement)).scalar_one_or_none()
        if verification is None:
            return None
        if payload.delivery_id and verification.last_delivery_id != payload.delivery_id:
            return None
        if payload.code and not hmac.compare_digest(
            verification.code_digest,
            _code_digest("signup", verification.id, payload.code),
        ):
            return None
        return verification

    async def _reset_from_secret(
        self,
        payload: ResetPasswordRequest,
        *,
        lock: bool,
    ) -> PasswordResetToken | None:
        statement = select(PasswordResetToken)
        if payload.token:
            digests = {
                _token_digest("password-reset", payload.token),
                hashlib.sha256(payload.token.encode("utf-8")).hexdigest(),
            }
            statement = statement.where(PasswordResetToken.token_hash.in_(digests))
        elif payload.delivery_id and payload.code:
            outbox = await self.db.get(IdentityOutboxMessage, payload.delivery_id)
            if (
                outbox is None
                or outbox.purpose != IdentityOutboxPurpose.password_reset
                or outbox.related_type != "password_reset"
                or outbox.related_id is None
            ):
                return None
            statement = statement.where(PasswordResetToken.id == outbox.related_id)
        else:
            return None
        if lock:
            statement = statement.with_for_update()
        reset = (await self.db.execute(statement)).scalar_one_or_none()
        if reset is None:
            return None
        if payload.delivery_id and reset.last_delivery_id != payload.delivery_id:
            return None
        if payload.code and (
            reset.code_hash is None
            or not hmac.compare_digest(
                reset.code_hash,
                _code_digest("password-reset", reset.id, payload.code),
            )
        ):
            return None
        return reset

    async def _rotate_invitation_delivery(self, invitation: WorkspaceInvitation) -> None:
        now = datetime.now(UTC)
        previous_delivery_id = invitation.last_delivery_id
        invitation.last_delivery_id = None
        await self.db.flush()
        await self.outbox.destroy(previous_delivery_id)
        invitation.expires_at = now + INVITATION_TTL
        invitation.last_sent_at = now
        if previous_delivery_id is not None:
            invitation.send_count += 1
        token, code = _new_secret()
        invitation.token_digest = _token_digest("invitation", token)
        invitation.code_digest = _code_digest("invitation", invitation.id, code)
        delivery_id = uuid4()
        delivery = await self.outbox.enqueue(
            message_id=delivery_id,
            purpose=IdentityOutboxPurpose.workspace_invitation,
            recipient_email=invitation.invited_email,
            payload={
                "code": code,
                "link": f"/accept-invitation/{invitation.id}?token={token}",
            },
            related_type="workspace_invitation",
            related_id=invitation.id,
            expires_at=invitation.expires_at,
        )
        invitation.last_delivery_id = delivery.id
        await self.db.flush()

    async def _workspace_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        lock: bool,
    ) -> WorkspaceInvitation:
        statement = select(WorkspaceInvitation).where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == workspace_id,
        )
        if lock:
            statement = statement.with_for_update()
        invitation = (await self.db.execute(statement)).scalar_one_or_none()
        if invitation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "TEAM-INVITATION-NOT-FOUND", "message": "Convite nao encontrado."},
            )
        return invitation

    async def _invitation_by_id(self, invitation_id: UUID, *, lock: bool) -> WorkspaceInvitation:
        statement = select(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation_id)
        if lock:
            statement = statement.with_for_update()
        invitation = (await self.db.execute(statement)).scalar_one_or_none()
        if invitation is None:
            raise _invalid_invitation()
        return invitation

    @staticmethod
    def _invitation_token_matches(invitation: WorkspaceInvitation, token: str) -> bool:
        return hmac.compare_digest(
            invitation.token_digest,
            _token_digest("invitation", token),
        )

    async def _invitation_secret_matches(
        self,
        invitation: WorkspaceInvitation,
        payload: InvitationAcceptRequest,
    ) -> bool:
        if payload.token:
            return IdentityLifecycleService._invitation_token_matches(invitation, payload.token)
        if payload.code:
            if (
                payload.delivery_id is None
                or invitation.last_delivery_id != payload.delivery_id
            ):
                return False
            outbox = await self.db.get(IdentityOutboxMessage, payload.delivery_id)
            if (
                outbox is None
                or outbox.purpose != IdentityOutboxPurpose.workspace_invitation
                or outbox.related_type != "workspace_invitation"
                or outbox.related_id != invitation.id
            ):
                return False
            return hmac.compare_digest(
                invitation.code_digest,
                _code_digest("invitation", invitation.id, payload.code),
            )
        return False

    async def _bind_signup_tenant(self, payload: SignupVerificationRequest) -> UUID:
        workspace_id = await resolve_signup_verification_workspace(
            self.db,
            token_digest=_token_digest("signup", payload.token) if payload.token else None,
            delivery_id=payload.delivery_id if payload.code else None,
        )
        if workspace_id is None:
            raise _invalid_verification()
        await set_workspace_context(self.db, workspace_id)
        return workspace_id

    async def _bind_pending_signup_tenant(self, normalized_email: str) -> UUID | None:
        delivery_id = await self.outbox.latest_related_delivery_id(
            purpose=IdentityOutboxPurpose.signup_verification,
            recipient_email=normalized_email,
            related_type="signup_verification",
        )
        if delivery_id is None:
            return None
        workspace_id = await resolve_signup_verification_workspace(
            self.db,
            token_digest=None,
            delivery_id=delivery_id,
        )
        if workspace_id is None:
            return None
        await set_workspace_context(self.db, workspace_id)
        return workspace_id

    async def _bind_invitation_tenant(
        self,
        invitation_id: UUID,
        *,
        token: str | None,
        delivery_id: UUID | None,
    ) -> UUID:
        workspace_id = await resolve_workspace_invitation_workspace(
            self.db,
            invitation_id=invitation_id,
            token_digest=_token_digest("invitation", token) if token else None,
            delivery_id=delivery_id,
        )
        if workspace_id is None:
            raise _invalid_invitation()
        await set_workspace_context(self.db, workspace_id)
        return workspace_id

    async def _validate_invitation_binding(
        self,
        invitation: WorkspaceInvitation,
    ) -> None:
        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.id == invitation.membership_id,
                WorkspaceMembership.workspace_id == invitation.workspace_id,
                WorkspaceMembership.user_id == invitation.user_id,
            )
        )
        user_email = await self.db.scalar(
            select(User.email).where(
                User.id == invitation.user_id,
                User.deleted_at.is_(None),
            )
        )
        if (
            membership is None
            or user_email is None
            or user_email != invitation.invited_email
        ):
            raise _invalid_invitation()

    async def _record_invitation_failure(
        self,
        invitation: WorkspaceInvitation,
        *,
        now: datetime,
    ) -> None:
        invitation.failed_attempt_count += 1
        if invitation.failed_attempt_count < INVITATION_MAX_FAILED_ATTEMPTS:
            return
        invitation.status = InvitationStatus.revoked
        invitation.revoked_at = now
        invitation.locked_at = now
        membership = await self.db.get(WorkspaceMembership, invitation.membership_id)
        if membership is not None and membership.status == MembershipStatus.invited:
            membership.status = MembershipStatus.suspended
        await self._erase_invitation_secrets(invitation)

    async def _expire_invitation(
        self,
        invitation: WorkspaceInvitation,
        *,
        now: datetime | None = None,
    ) -> None:
        if invitation.status != InvitationStatus.pending:
            return
        effective_now = now or datetime.now(UTC)
        if _utc(invitation.expires_at) > effective_now:
            return
        invitation.status = InvitationStatus.expired
        await self._erase_invitation_secrets(invitation)

    async def _erase_invitation_secrets(
        self,
        invitation: WorkspaceInvitation,
    ) -> None:
        delivery_id = invitation.last_delivery_id
        invitation.last_delivery_id = None
        invitation.token_digest = opaque_auth_digest("invitation-erased", uuid4().hex)
        invitation.code_digest = opaque_auth_digest("invitation-erased", uuid4().hex)
        await self.db.flush()
        await self.outbox.destroy(delivery_id)

    @staticmethod
    def _invitation_accepted_read(invitation: WorkspaceInvitation) -> InvitationAcceptedRead:
        return InvitationAcceptedRead(
            invitation_id=invitation.id,
            membership_id=invitation.membership_id,
            message="Convite aceito. Entre com sua identidade para acessar o workspace.",
        )

    @staticmethod
    def invitation_read(invitation: WorkspaceInvitation) -> InvitationRead:
        return InvitationRead.model_validate(invitation)

    @staticmethod
    def _email_hint(email: str) -> str:
        local, _, domain = email.partition("@")
        return f"{local[:1]}{'*' * max(3, len(local) - 1)}@{domain}"
