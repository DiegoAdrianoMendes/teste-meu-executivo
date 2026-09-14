"""Outbox criptografada e idempotencia para fluxos locais de identidade."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth_security import AuthSecurityEvent
from app.models.identity import (
    IdentityDeliveryStatus,
    IdentityIdempotencyRecord,
    IdentityOutboxMessage,
    IdentityOutboxPurpose,
    InvitationStatus,
    WorkspaceInvitation,
)
from app.models.user import AgentType, User, normalize_email
from app.schemas.identity import LocalOutboxMessageRead
from app.services.auth_rate_limit_service import opaque_auth_digest

IDEMPOTENCY_TTL = timedelta(hours=24)
MAX_OUTBOX_PURGE_BATCH = 1_000


@dataclass(frozen=True)
class IdempotencyReservation:
    record: IdentityIdempotencyRecord
    replay: bool


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def _recipient_hint(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    visible = local[:1]
    return f"{visible}{'*' * max(3, len(local) - 1)}@{domain}"


def _canonical_digest(namespace: str, payload: Any) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return opaque_auth_digest(namespace, canonical)


class IdentityIdempotencyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def reserve(
        self,
        *,
        operation: str,
        key: str,
        request_payload: Any,
    ) -> IdempotencyReservation:
        normalized_key = key.strip()
        if not 8 <= len(normalized_key) <= 200:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "AUTH-IDEMPOTENCY-REQUIRED",
                    "message": "Idempotency-Key deve conter entre 8 e 200 caracteres.",
                },
            )
        key_digest = opaque_auth_digest(f"idempotency:{operation}", normalized_key)
        request_digest = _canonical_digest(f"idempotency-request:{operation}", request_payload)
        existing = await self._load(operation, key_digest, lock=True)
        if existing is not None:
            return await self._reserve_existing(existing, request_digest)

        record = IdentityIdempotencyRecord(
            operation=operation,
            key_digest=key_digest,
            request_digest=request_digest,
            response_data={},
            expires_at=datetime.now(UTC) + IDEMPOTENCY_TTL,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(record)
                await self.db.flush()
        except IntegrityError:
            existing = await self._load(operation, key_digest, lock=True)
            if existing is None:
                raise
            return await self._reserve_existing(existing, request_digest)
        return IdempotencyReservation(record=record, replay=False)

    async def _reserve_existing(
        self,
        record: IdentityIdempotencyRecord,
        request_digest: str,
    ) -> IdempotencyReservation:
        now = datetime.now(UTC)
        if _utc(record.expires_at) <= now:
            record.request_digest = request_digest
            record.response_data = {}
            record.created_at = now
            record.expires_at = now + IDEMPOTENCY_TTL
            await self.db.flush()
            return IdempotencyReservation(record=record, replay=False)
        return self._validate_existing(record, request_digest)

    async def finish(self, record: IdentityIdempotencyRecord, response_data: dict) -> None:
        record.response_data = response_data
        await self.db.flush()

    async def _load(
        self,
        operation: str,
        key_digest: str,
        *,
        lock: bool,
    ) -> IdentityIdempotencyRecord | None:
        statement = select(IdentityIdempotencyRecord).where(
            IdentityIdempotencyRecord.operation == operation,
            IdentityIdempotencyRecord.key_digest == key_digest,
        )
        if lock:
            statement = statement.with_for_update()
        return (await self.db.execute(statement)).scalar_one_or_none()

    @staticmethod
    def _validate_existing(
        record: IdentityIdempotencyRecord,
        request_digest: str,
    ) -> IdempotencyReservation:
        if record.request_digest != request_digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "AUTH-IDEMPOTENCY-CONFLICT",
                    "message": "A chave de idempotencia ja foi usada com outro conteudo.",
                },
            )
        if not record.response_data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "AUTH-IDEMPOTENCY-IN-PROGRESS",
                    "message": "A solicitacao com esta chave ainda esta em processamento.",
                },
            )
        return IdempotencyReservation(record=record, replay=True)


class LocalIdentityOutboxService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue(
        self,
        *,
        message_id: UUID | None = None,
        purpose: IdentityOutboxPurpose,
        recipient_email: str,
        payload: dict[str, Any],
        related_type: str,
        related_id: UUID | None,
        expires_at: datetime,
        request_id: UUID | None = None,
    ) -> IdentityOutboxMessage:
        normalized_email = normalize_email(recipient_email)
        encrypted_payload = {**payload, "recipient_email": normalized_email}
        encoded_payload = json.dumps(
            encrypted_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        message = IdentityOutboxMessage(
            id=message_id,
            purpose=purpose,
            recipient_digest=opaque_auth_digest("outbox-recipient", normalized_email),
            recipient_hint=_recipient_hint(normalized_email),
            payload_ciphertext=_fernet().encrypt(encoded_payload).decode("ascii"),
            related_type=related_type,
            related_id=related_id,
            request_id=request_id,
            expires_at=expires_at,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def latest_related_delivery_id(
        self,
        *,
        purpose: IdentityOutboxPurpose,
        recipient_email: str,
        related_type: str,
    ) -> UUID | None:
        normalized_email = normalize_email(recipient_email)
        return await self.db.scalar(
            select(IdentityOutboxMessage.id)
            .where(
                IdentityOutboxMessage.purpose == purpose,
                IdentityOutboxMessage.recipient_digest
                == opaque_auth_digest("outbox-recipient", normalized_email),
                IdentityOutboxMessage.related_type == related_type,
                IdentityOutboxMessage.related_id.is_not(None),
            )
            .order_by(
                IdentityOutboxMessage.created_at.desc(),
                IdentityOutboxMessage.id.desc(),
            )
            .limit(1)
        )

    async def purge_expired(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> int:
        """Delete one bounded batch; transaction ownership remains with the caller."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_OUTBOX_PURGE_BATCH
        ):
            raise ValueError(f"limit must be between 1 and {MAX_OUTBOX_PURGE_BATCH}")
        cutoff = now or datetime.now(UTC)
        candidates = (
            select(IdentityOutboxMessage.id)
            .where(IdentityOutboxMessage.expires_at <= cutoff)
            .order_by(IdentityOutboxMessage.expires_at, IdentityOutboxMessage.id)
            .limit(limit)
        )
        if self.db.get_bind().dialect.name == "postgresql":
            candidates = candidates.with_for_update(skip_locked=True)
        message_ids = list((await self.db.scalars(candidates)).all())
        if not message_ids:
            return 0
        await self.db.execute(
            delete(IdentityOutboxMessage)
            .where(IdentityOutboxMessage.id.in_(message_ids))
            .execution_options(synchronize_session=False)
        )
        await self.db.flush()
        return len(message_ids)

    async def read_privileged(
        self,
        delivery_id: UUID,
        *,
        administrative_session_token: str,
        request: Request,
    ) -> LocalOutboxMessageRead:
        actor_user_id, administrative_session_id = await self._authorize(
            administrative_session_token,
            request=request,
        )
        message = await self._message_by_delivery(delivery_id)
        return await self._read_message(
            message,
            actor_user_id=actor_user_id,
            administrative_session_id=administrative_session_id,
        )

    async def read_request_privileged(
        self,
        request_id: UUID,
        *,
        administrative_session_token: str,
        request: Request,
    ) -> LocalOutboxMessageRead:
        actor_user_id, administrative_session_id = await self._authorize(
            administrative_session_token,
            request=request,
        )
        message = (
            await self.db.execute(
                select(IdentityOutboxMessage)
                .where(
                    IdentityOutboxMessage.request_id == request_id,
                    IdentityOutboxMessage.purpose == IdentityOutboxPurpose.password_reset,
                )
                .order_by(IdentityOutboxMessage.created_at.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        return await self._read_message(
            message,
            actor_user_id=actor_user_id,
            administrative_session_id=administrative_session_id,
        )

    async def read_workspace_invitation(
        self,
        invitation_id: UUID,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> LocalOutboxMessageRead:
        """Expose only one pending invitation to its local workspace administrator."""
        self._require_local_runtime()
        return await self._read_workspace_invitation(
            invitation_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            require_local_runtime=True,
        )

    async def read_workspace_invitation_admin(
        self,
        invitation_id: UUID,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> LocalOutboxMessageRead:
        """Return one pending invite link to an authorized workspace administrator."""
        return await self._read_workspace_invitation(
            invitation_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            require_local_runtime=False,
        )

    async def _read_workspace_invitation(
        self,
        invitation_id: UUID,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        require_local_runtime: bool,
    ) -> LocalOutboxMessageRead:
        now = datetime.now(UTC)
        invitation = (
            await self.db.execute(
                select(WorkspaceInvitation)
                .where(
                    WorkspaceInvitation.id == invitation_id,
                    WorkspaceInvitation.workspace_id == workspace_id,
                    WorkspaceInvitation.status == InvitationStatus.pending,
                    WorkspaceInvitation.expires_at > now,
                    WorkspaceInvitation.last_delivery_id.is_not(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if invitation is None or invitation.last_delivery_id is None:
            raise self._not_found()

        message = (
            await self.db.execute(
                select(IdentityOutboxMessage)
                .where(
                    IdentityOutboxMessage.id == invitation.last_delivery_id,
                    IdentityOutboxMessage.purpose
                    == IdentityOutboxPurpose.workspace_invitation,
                    IdentityOutboxMessage.related_type == "workspace_invitation",
                    IdentityOutboxMessage.related_id == invitation.id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        return await self._read_message(
            message,
            actor_user_id=actor_user_id,
            administrative_session_id=None,
            subject_user_id=invitation.user_id,
            audit_details={
                "workspace_id_digest": opaque_auth_digest(
                    "outbox-workspace-audit",
                    str(workspace_id),
                ),
                "invitation_id_digest": opaque_auth_digest(
                    "outbox-invitation-audit",
                    str(invitation.id),
                ),
                "access_scope": "workspace_invitation_only",
            },
            require_local_runtime=require_local_runtime,
        )

    async def deliver_pending(self, *, limit: int = 25) -> dict[str, int]:
        """Deliver a bounded batch of encrypted identity messages.

        Database rows remain the durable source of truth. Recipient addresses
        are decrypted only inside the worker and are never written to logs.
        """
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        now = datetime.now(UTC)
        retry_before = now - timedelta(minutes=5)
        statement = (
            select(IdentityOutboxMessage)
            .where(
                IdentityOutboxMessage.expires_at > now,
                (
                    (IdentityOutboxMessage.delivery_status == IdentityDeliveryStatus.pending)
                    | (
                        (IdentityOutboxMessage.delivery_status == IdentityDeliveryStatus.failed)
                        & (IdentityOutboxMessage.delivery_attempt_count < 5)
                        & (
                            (IdentityOutboxMessage.last_attempt_at.is_(None))
                            | (IdentityOutboxMessage.last_attempt_at <= retry_before)
                        )
                    )
                    | (
                        (IdentityOutboxMessage.delivery_status == IdentityDeliveryStatus.blocked)
                        & (
                            (IdentityOutboxMessage.last_attempt_at.is_(None))
                            | (IdentityOutboxMessage.last_attempt_at <= retry_before)
                        )
                    )
                    | (
                        (IdentityOutboxMessage.delivery_status == IdentityDeliveryStatus.sending)
                        & (IdentityOutboxMessage.last_attempt_at <= retry_before)
                    )
                ),
            )
            .order_by(IdentityOutboxMessage.created_at, IdentityOutboxMessage.id)
            .limit(limit)
        )
        if self.db.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        messages = list((await self.db.scalars(statement)).all())
        counts = {"processed": 0, "sent": 0, "failed": 0, "blocked": 0}
        for message in messages:
            counts["processed"] += 1
            outcome = await self.deliver_message(message, attempted_at=now)
            if outcome == IdentityDeliveryStatus.sent:
                counts["sent"] += 1
            elif outcome == IdentityDeliveryStatus.blocked:
                counts["blocked"] += 1
            else:
                counts["failed"] += 1
        await self.db.flush()
        return counts

    async def deliver_message(
        self,
        message: IdentityOutboxMessage,
        *,
        attempted_at: datetime | None = None,
    ) -> IdentityDeliveryStatus:
        """Attempt one concrete delivery and persist an honest provider outcome."""
        from app.services.email_service import send_identity_outbox_email

        message.delivery_status = IdentityDeliveryStatus.sending
        message.delivery_attempt_count += 1
        message.last_attempt_at = attempted_at or datetime.now(UTC)
        try:
            payload = json.loads(
                _fernet().decrypt(message.payload_ciphertext.encode("ascii")).decode("utf-8")
            )
            recipient_email = str(payload["recipient_email"])
            link = str(payload["link"])
        except (KeyError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
            message.delivery_status = IdentityDeliveryStatus.failed
            message.last_delivery_error_code = "IDENTITY_PAYLOAD_UNREADABLE"
            await self.db.flush()
            return message.delivery_status

        result = await send_identity_outbox_email(
            to_email=recipient_email,
            purpose=message.purpose.value,
            link=link,
        )
        message.delivery_provider = result.provider
        message.provider_message_ref = result.message_ref
        message.last_delivery_error_code = result.error_code
        if result.sent:
            message.delivery_status = IdentityDeliveryStatus.sent
            message.delivered_at = datetime.now(UTC)
        elif result.blocked:
            message.delivery_status = IdentityDeliveryStatus.blocked
        else:
            message.delivery_status = IdentityDeliveryStatus.failed
        await self.db.flush()
        return message.delivery_status

    async def destroy(self, delivery_id: UUID | None) -> None:
        if delivery_id is None:
            return
        message = await self.db.get(IdentityOutboxMessage, delivery_id, with_for_update=True)
        if message is not None:
            await self.db.delete(message)
            await self.db.flush()

    async def _message_by_delivery(
        self,
        delivery_id: UUID,
    ) -> IdentityOutboxMessage | None:
        self._require_local_runtime()
        return (
            await self.db.execute(
                select(IdentityOutboxMessage)
                .where(IdentityOutboxMessage.id == delivery_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _read_message(
        self,
        message: IdentityOutboxMessage | None,
        *,
        actor_user_id: UUID,
        administrative_session_id: UUID | None,
        subject_user_id: UUID | None = None,
        audit_details: dict[str, Any] | None = None,
        require_local_runtime: bool = True,
    ) -> LocalOutboxMessageRead:
        if require_local_runtime:
            self._require_local_runtime()
        now = datetime.now(UTC)
        if message is None or _utc(message.expires_at) <= now:
            raise self._not_found()
        try:
            payload = json.loads(
                _fernet().decrypt(message.payload_ciphertext.encode("ascii")).decode("utf-8")
            )
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise self._not_found() from error
        message.opened_at = now
        message.open_count += 1
        self.db.add(
            AuthSecurityEvent(
                event_type="identity_outbox_opened",
                outcome="success",
                actor_user_id=actor_user_id,
                subject_user_id=subject_user_id,
                administrative_session_id=administrative_session_id,
                details={
                    "delivery_id_digest": opaque_auth_digest(
                        "outbox-delivery-audit",
                        str(message.id),
                    ),
                    "purpose": message.purpose.value,
                    **(audit_details or {}),
                },
            )
        )
        await self.db.flush()
        return LocalOutboxMessageRead(
            id=message.id,
            purpose=message.purpose,
            recipient_hint=message.recipient_hint,
            code=payload.get("code"),
            link=str(payload["link"]),
            expires_at=message.expires_at,
        )

    async def _authorize(
        self,
        administrative_session_token: str,
        *,
        request: Request,
    ) -> tuple[UUID, UUID]:
        self._require_local_runtime()
        if not administrative_session_token:
            raise self._not_authorized()
        from app.services.mfa_service import LocalMfaService

        try:
            session = await LocalMfaService(self.db).verify_administrative_session(
                administrative_session_token,
                request=request,
            )
        except HTTPException as error:
            raise self._not_authorized() from error
        platform_admin = await self.db.scalar(
            select(User).where(
                User.id == session.user_id,
                User.agent_type == AgentType.human,
                User.is_active.is_(True),
                User.is_platform_admin.is_(True),
                User.email_verified_at.is_not(None),
                User.deleted_at.is_(None),
            )
        )
        if platform_admin is None:
            raise self._not_authorized()
        return platform_admin.id, session.id

    @staticmethod
    def _require_local_runtime() -> None:
        if not settings.uses_local_infrastructure:
            raise LocalIdentityOutboxService._not_found()

    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AUTH-OUTBOX-NOT-FOUND", "message": "Entrega local indisponivel."},
        )

    @staticmethod
    def _not_authorized() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH-OUTBOX-PRIVILEGED",
                "message": "Sessao administrativa local valida e obrigatoria.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
