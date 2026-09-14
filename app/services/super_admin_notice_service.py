"""Regras da Central de Avisos, incluindo fan-out local e auditoria."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.super_admin_notice import (
    SuperAdminNotice,
    SuperAdminNoticeAudienceKind,
    SuperAdminNoticeAuditAction,
    SuperAdminNoticeAuditEvent,
    SuperAdminNoticeChannel,
    SuperAdminNoticeIdempotencyRecord,
    SuperAdminNoticeReceipt,
    SuperAdminNoticeReceiptState,
    SuperAdminNoticeReceiptWorkspace,
    SuperAdminNoticeStatus,
)
from app.models.user import AgentType, User
from app.models.workspace import Workspace
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.super_admin_notice import (
    SuperAdminNoticeAudience,
    SuperAdminNoticeAuditEventRead,
    SuperAdminNoticeCreate,
    SuperAdminNoticeDispatchRead,
    SuperAdminNoticeInboxItemRead,
    SuperAdminNoticeInboxRead,
    SuperAdminNoticeListRead,
    SuperAdminNoticePublicationRead,
    SuperAdminNoticeRead,
    SuperAdminNoticeReceiptListRead,
    SuperAdminNoticeReceiptRead,
    SuperAdminNoticeRecipientPreviewRead,
    SuperAdminNoticeRecipientRead,
    SuperAdminNoticeRuleField,
    SuperAdminNoticeSchedule,
    SuperAdminNoticeUpdate,
)

IDEMPOTENCY_TTL = timedelta(hours=24)
UPSERT_CHUNK_SIZE = 500


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


class SuperAdminNoticeServiceError(Exception):
    status_code = 409
    code = "NOTICE-CONFLICT"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SuperAdminNoticeNotFoundError(SuperAdminNoticeServiceError):
    status_code = 404
    code = "NOTICE-NOT-FOUND"


class SuperAdminNoticeValidationError(SuperAdminNoticeServiceError):
    status_code = 422
    code = "NOTICE-VALIDATION"


class SuperAdminNoticeTransitionError(SuperAdminNoticeServiceError):
    status_code = 409
    code = "NOTICE-INVALID-TRANSITION"


class SuperAdminNoticeVersionConflictError(SuperAdminNoticeServiceError):
    status_code = 409
    code = "NOTICE-VERSION-CONFLICT"


class SuperAdminNoticeIdempotencyError(SuperAdminNoticeServiceError):
    status_code = 409
    code = "NOTICE-IDEMPOTENCY-CONFLICT"


@dataclass(frozen=True)
class _IdempotencyReservation:
    record: SuperAdminNoticeIdempotencyRecord
    replay: bool


@dataclass
class _ResolvedRecipient:
    user_id: UUID
    name: str
    email: str | None
    matched_by: str
    workspace_ids: set[UUID] = field(default_factory=set)

    @property
    def scope_all_workspaces(self) -> bool:
        return not self.workspace_ids


class _NoticeIdempotencyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def reserve(
        self,
        *,
        actor_user_id: UUID,
        operation: str,
        key: str,
        request_payload: Any,
    ) -> _IdempotencyReservation:
        normalized_key = key.strip()
        if not 8 <= len(normalized_key) <= 200:
            error = SuperAdminNoticeValidationError(
                "Idempotency-Key deve conter entre 8 e 200 caracteres."
            )
            error.code = "NOTICE-IDEMPOTENCY-REQUIRED"
            raise error

        key_digest = self._digest(f"notice-key:{operation}", normalized_key)
        request_digest = self._digest(
            f"notice-request:{operation}",
            request_payload,
        )
        existing = await self._load(
            actor_user_id=actor_user_id,
            operation=operation,
            key_digest=key_digest,
            lock=True,
        )
        if existing is not None:
            return await self._existing(existing, request_digest)

        record = SuperAdminNoticeIdempotencyRecord(
            actor_user_id=actor_user_id,
            operation=operation,
            key_digest=key_digest,
            request_digest=request_digest,
            response_data={},
            expires_at=utc_now() + IDEMPOTENCY_TTL,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(record)
                await self.db.flush()
        except IntegrityError:
            existing = await self._load(
                actor_user_id=actor_user_id,
                operation=operation,
                key_digest=key_digest,
                lock=True,
            )
            if existing is None:
                raise
            return await self._existing(existing, request_digest)
        return _IdempotencyReservation(record=record, replay=False)

    async def finish(
        self,
        record: SuperAdminNoticeIdempotencyRecord,
        response_data: dict,
    ) -> None:
        record.response_data = response_data
        await self.db.flush()

    async def _load(
        self,
        *,
        actor_user_id: UUID,
        operation: str,
        key_digest: str,
        lock: bool,
    ) -> SuperAdminNoticeIdempotencyRecord | None:
        statement = select(SuperAdminNoticeIdempotencyRecord).where(
            SuperAdminNoticeIdempotencyRecord.actor_user_id == actor_user_id,
            SuperAdminNoticeIdempotencyRecord.operation == operation,
            SuperAdminNoticeIdempotencyRecord.key_digest == key_digest,
        )
        if lock:
            statement = statement.with_for_update()
        return await self.db.scalar(statement)

    async def _existing(
        self,
        record: SuperAdminNoticeIdempotencyRecord,
        request_digest: str,
    ) -> _IdempotencyReservation:
        now = utc_now()
        if _as_utc(record.expires_at) <= now:
            record.request_digest = request_digest
            record.response_data = {}
            record.created_at = now
            record.expires_at = now + IDEMPOTENCY_TTL
            await self.db.flush()
            return _IdempotencyReservation(record=record, replay=False)
        if record.request_digest != request_digest:
            raise SuperAdminNoticeIdempotencyError(
                "A chave de idempotencia ja foi usada com outro conteudo."
            )
        if not record.response_data:
            error = SuperAdminNoticeIdempotencyError(
                "A solicitacao com esta chave ainda esta em processamento."
            )
            error.code = "NOTICE-IDEMPOTENCY-IN-PROGRESS"
            raise error
        return _IdempotencyReservation(record=record, replay=True)

    @staticmethod
    def _digest(namespace: str, payload: Any) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
        return hashlib.sha256(f"{namespace}:{canonical}".encode()).hexdigest()


class SuperAdminNoticeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.idempotency = _NoticeIdempotencyService(db)
        self.operation_was_replayed = False

    async def create(
        self,
        payload: SuperAdminNoticeCreate,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
    ) -> SuperAdminNoticeRead:
        request_data = payload.model_dump(mode="json", by_alias=False)
        reservation = await self.idempotency.reserve(
            actor_user_id=actor_user_id,
            operation="notice.create",
            key=idempotency_key,
            request_payload=request_data,
        )
        self.operation_was_replayed = reservation.replay
        if reservation.replay:
            return SuperAdminNoticeRead.model_validate(reservation.record.response_data)

        await self._validate_audience(payload.audience)
        notice = SuperAdminNotice(
            title=payload.title,
            body=payload.body,
            status=SuperAdminNoticeStatus.draft,
            audience_kind=payload.audience.kind,
            audience_definition=payload.audience.model_dump(mode="json", by_alias=False),
            channels=[channel.value for channel in payload.channels],
            requires_acknowledgement=payload.requires_acknowledgement,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        self.db.add(notice)
        await self.db.flush()
        self._audit(
            notice_id=notice.id,
            actor_user_id=actor_user_id,
            action=SuperAdminNoticeAuditAction.created,
            details={
                "status": notice.status.value,
                "audience_kind": notice.audience_kind.value,
                "channels": notice.channels,
                "idempotency_record_id": str(reservation.record.id),
            },
        )
        await self.db.flush()
        await self.db.refresh(notice)
        response = self._to_notice_read(notice)
        await self.idempotency.finish(
            reservation.record,
            response.model_dump(mode="json", by_alias=False),
        )
        return response

    async def list(
        self,
        *,
        offset: int,
        limit: int,
        notice_status: SuperAdminNoticeStatus | None = None,
    ) -> SuperAdminNoticeListRead:
        filters = []
        if notice_status is not None:
            filters.append(SuperAdminNotice.status == notice_status)
        total = int(
            await self.db.scalar(select(func.count(SuperAdminNotice.id)).where(*filters))
            or 0
        )
        notices = list(
            (
                await self.db.scalars(
                    select(SuperAdminNotice)
                    .where(*filters)
                    .order_by(SuperAdminNotice.created_at.desc(), SuperAdminNotice.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return SuperAdminNoticeListRead(
            items=[self._to_notice_read(item) for item in notices],
            total=total,
        )

    async def get(self, notice_id: UUID) -> SuperAdminNoticeRead:
        return self._to_notice_read(await self._notice(notice_id))

    async def update(
        self,
        notice_id: UUID,
        payload: SuperAdminNoticeUpdate,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
    ) -> SuperAdminNoticeRead:
        request_data = {
            "notice_id": str(notice_id),
            "payload": payload.model_dump(mode="json", by_alias=False),
        }
        reservation = await self.idempotency.reserve(
            actor_user_id=actor_user_id,
            operation="notice.update",
            key=idempotency_key,
            request_payload=request_data,
        )
        self.operation_was_replayed = reservation.replay
        if reservation.replay:
            return SuperAdminNoticeRead.model_validate(reservation.record.response_data)

        notice = await self._notice(notice_id, lock=True)
        if notice.status not in {
            SuperAdminNoticeStatus.draft,
            SuperAdminNoticeStatus.scheduled,
        }:
            raise SuperAdminNoticeTransitionError(
                "Somente avisos em rascunho ou agendados podem ser editados."
            )
        if notice.version != payload.expected_version:
            raise SuperAdminNoticeVersionConflictError(
                "O aviso foi alterado por outra operacao; recarregue antes de editar."
            )

        changes = payload.model_dump(exclude_unset=True)
        changes.pop("expected_version", None)
        changed_fields = sorted(changes)
        if payload.audience is not None:
            await self._validate_audience(payload.audience)
            notice.audience_kind = payload.audience.kind
            notice.audience_definition = payload.audience.model_dump(
                mode="json",
                by_alias=False,
            )
        if payload.title is not None:
            notice.title = payload.title
        if payload.body is not None:
            notice.body = payload.body
        if payload.channels is not None:
            notice.channels = [channel.value for channel in payload.channels]
        if payload.requires_acknowledgement is not None:
            notice.requires_acknowledgement = payload.requires_acknowledgement
        notice.updated_by_user_id = actor_user_id
        notice.version += 1
        self._audit(
            notice_id=notice.id,
            actor_user_id=actor_user_id,
            action=SuperAdminNoticeAuditAction.updated,
            details={
                "changed_fields": changed_fields,
                "version": notice.version,
                "idempotency_record_id": str(reservation.record.id),
            },
        )
        await self.db.flush()
        await self.db.refresh(notice)
        response = self._to_notice_read(notice)
        await self.idempotency.finish(
            reservation.record,
            response.model_dump(mode="json", by_alias=False),
        )
        return response

    async def preview_recipients(
        self,
        notice_id: UUID,
        *,
        actor_user_id: UUID,
        sample_limit: int,
    ) -> SuperAdminNoticeRecipientPreviewRead:
        notice = await self._notice(notice_id)
        recipients = await self._resolve_recipients(self._audience(notice))
        ordered = sorted(recipients, key=lambda item: (item.name.casefold(), str(item.user_id)))
        response = SuperAdminNoticeRecipientPreviewRead(
            notice_id=notice.id,
            total_recipients=len(ordered),
            email_eligible=sum(item.email is not None for item in ordered),
            email_blocked_missing_address=sum(item.email is None for item in ordered),
            sample=[self._to_recipient_read(item) for item in ordered[:sample_limit]],
            evaluated_at=utc_now(),
        )
        self._audit(
            notice_id=notice.id,
            actor_user_id=actor_user_id,
            action=SuperAdminNoticeAuditAction.recipient_previewed,
            details={
                "recipient_count": response.total_recipients,
                "email_eligible": response.email_eligible,
                "sample_size": len(response.sample),
            },
        )
        await self.db.flush()
        return response

    async def schedule(
        self,
        notice_id: UUID,
        payload: SuperAdminNoticeSchedule,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
    ) -> SuperAdminNoticeRead:
        requested_at = _as_utc(payload.scheduled_at)
        reservation = await self.idempotency.reserve(
            actor_user_id=actor_user_id,
            operation="notice.schedule",
            key=idempotency_key,
            request_payload={
                "notice_id": str(notice_id),
                "scheduled_at": requested_at.isoformat(),
            },
        )
        self.operation_was_replayed = reservation.replay
        if reservation.replay:
            return SuperAdminNoticeRead.model_validate(reservation.record.response_data)
        if requested_at <= utc_now():
            raise SuperAdminNoticeValidationError(
                "O agendamento precisa estar no futuro."
            )

        notice = await self._notice(notice_id, lock=True)
        if notice.status not in {
            SuperAdminNoticeStatus.draft,
            SuperAdminNoticeStatus.scheduled,
        }:
            raise SuperAdminNoticeTransitionError(
                "Somente avisos em rascunho ou ja agendados podem ser agendados."
            )
        notice.status = SuperAdminNoticeStatus.scheduled
        notice.scheduled_at = requested_at
        notice.updated_by_user_id = actor_user_id
        notice.version += 1
        self._audit(
            notice_id=notice.id,
            actor_user_id=actor_user_id,
            action=SuperAdminNoticeAuditAction.scheduled,
            details={
                "scheduled_at": requested_at.isoformat(),
                "idempotency_record_id": str(reservation.record.id),
            },
        )
        await self.db.flush()
        await self.db.refresh(notice)
        response = self._to_notice_read(notice)
        await self.idempotency.finish(
            reservation.record,
            response.model_dump(mode="json", by_alias=False),
        )
        return response

    async def publish(
        self,
        notice_id: UUID,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
    ) -> SuperAdminNoticePublicationRead:
        reservation = await self.idempotency.reserve(
            actor_user_id=actor_user_id,
            operation="notice.publish",
            key=idempotency_key,
            request_payload={"notice_id": str(notice_id)},
        )
        self.operation_was_replayed = reservation.replay
        if reservation.replay:
            return SuperAdminNoticePublicationRead.model_validate(
                reservation.record.response_data
            )
        notice = await self._notice(notice_id, lock=True)
        if notice.status == SuperAdminNoticeStatus.cancelled:
            raise SuperAdminNoticeTransitionError(
                "Avisos cancelados nao podem ser publicados."
            )
        if notice.status != SuperAdminNoticeStatus.published:
            await self._publish_notice(
                notice,
                actor_user_id=actor_user_id,
                idempotency_record_id=reservation.record.id,
            )
        response = await self._publication_read(notice)
        await self.idempotency.finish(
            reservation.record,
            response.model_dump(mode="json", by_alias=False),
        )
        return response

    async def cancel(
        self,
        notice_id: UUID,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
    ) -> SuperAdminNoticeRead:
        reservation = await self.idempotency.reserve(
            actor_user_id=actor_user_id,
            operation="notice.cancel",
            key=idempotency_key,
            request_payload={"notice_id": str(notice_id)},
        )
        self.operation_was_replayed = reservation.replay
        if reservation.replay:
            return SuperAdminNoticeRead.model_validate(reservation.record.response_data)
        notice = await self._notice(notice_id, lock=True)
        if notice.status != SuperAdminNoticeStatus.cancelled:
            previous_status = notice.status
            notice.status = SuperAdminNoticeStatus.cancelled
            notice.cancelled_at = utc_now()
            notice.cancelled_by_user_id = actor_user_id
            notice.updated_by_user_id = actor_user_id
            notice.version += 1
            result = await self.db.execute(
                update(SuperAdminNoticeReceipt)
                .where(
                    SuperAdminNoticeReceipt.notice_id == notice.id,
                    SuperAdminNoticeReceipt.state
                    != SuperAdminNoticeReceiptState.cancelled,
                )
                .values(state=SuperAdminNoticeReceiptState.cancelled)
            )
            self._audit(
                notice_id=notice.id,
                actor_user_id=actor_user_id,
                action=SuperAdminNoticeAuditAction.cancelled,
                details={
                    "previous_status": previous_status.value,
                    "receipts_cancelled": max(result.rowcount or 0, 0),
                    "idempotency_record_id": str(reservation.record.id),
                },
            )
            await self.db.flush()
            await self.db.refresh(notice)
        response = self._to_notice_read(notice)
        await self.idempotency.finish(
            reservation.record,
            response.model_dump(mode="json", by_alias=False),
        )
        return response

    async def dispatch_due(
        self,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        limit: int,
    ) -> SuperAdminNoticeDispatchRead:
        reservation = await self.idempotency.reserve(
            actor_user_id=actor_user_id,
            operation="notice.dispatch_due",
            key=idempotency_key,
            request_payload={"limit": limit},
        )
        self.operation_was_replayed = reservation.replay
        if reservation.replay:
            return SuperAdminNoticeDispatchRead.model_validate(
                reservation.record.response_data
            )

        statement = (
            select(SuperAdminNotice)
            .where(
                SuperAdminNotice.status == SuperAdminNoticeStatus.scheduled,
                SuperAdminNotice.scheduled_at.is_not(None),
                SuperAdminNotice.scheduled_at <= utc_now(),
            )
            .order_by(SuperAdminNotice.scheduled_at.asc(), SuperAdminNotice.id.asc())
            .limit(limit)
        )
        bind = self.db.get_bind()
        if bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        else:
            statement = statement.with_for_update()
        notices = list((await self.db.scalars(statement)).all())
        publications: list[SuperAdminNoticePublicationRead] = []
        for notice in notices:
            await self._publish_notice(
                notice,
                actor_user_id=actor_user_id,
                idempotency_record_id=reservation.record.id,
            )
            publications.append(await self._publication_read(notice))
        response = SuperAdminNoticeDispatchRead(
            processed_count=len(publications),
            publications=publications,
        )
        await self.idempotency.finish(
            reservation.record,
            response.model_dump(mode="json", by_alias=False),
        )
        return response

    async def list_receipts(
        self,
        notice_id: UUID,
        *,
        offset: int,
        limit: int,
    ) -> SuperAdminNoticeReceiptListRead:
        await self._notice(notice_id)
        total = int(
            await self.db.scalar(
                select(func.count(SuperAdminNoticeReceipt.id)).where(
                    SuperAdminNoticeReceipt.notice_id == notice_id
                )
            )
            or 0
        )
        receipt_rows = list(
            (
                await self.db.execute(
                    select(SuperAdminNoticeReceipt, User.name)
                    .join(User, User.id == SuperAdminNoticeReceipt.recipient_user_id)
                    .where(SuperAdminNoticeReceipt.notice_id == notice_id)
                    .order_by(
                        SuperAdminNoticeReceipt.created_at.asc(),
                        SuperAdminNoticeReceipt.id.asc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        receipts = [receipt for receipt, _ in receipt_rows]
        recipient_names = {receipt.id: name for receipt, name in receipt_rows}
        workspace_map = await self._receipt_workspace_map(
            [receipt.id for receipt in receipts]
        )
        return SuperAdminNoticeReceiptListRead(
            items=[
                self._to_receipt_read(
                    receipt,
                    workspace_map.get(receipt.id, []),
                    recipient_names[receipt.id],
                )
                for receipt in receipts
            ],
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(receipts) < total,
        )

    async def page_audit(
        self,
        notice_id: UUID,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[SuperAdminNoticeAuditEventRead], int]:
        await self._notice(notice_id)
        filters = [SuperAdminNoticeAuditEvent.notice_id == notice_id]
        total = int(
            await self.db.scalar(
                select(func.count(SuperAdminNoticeAuditEvent.id)).where(*filters)
            )
            or 0
        )
        event_rows = list(
            await self.db.execute(
                select(SuperAdminNoticeAuditEvent, User.name)
                .outerjoin(User, User.id == SuperAdminNoticeAuditEvent.actor_user_id)
                .where(*filters)
                .order_by(
                    SuperAdminNoticeAuditEvent.occurred_at.desc(),
                    SuperAdminNoticeAuditEvent.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return [
            SuperAdminNoticeAuditEventRead(
                id=event.id,
                notice_id=event.notice_id,
                actor_user_id=event.actor_user_id,
                actor_user_name=actor_name,
                action=event.action,
                details=event.details,
                occurred_at=event.occurred_at,
            )
            for event, actor_name in event_rows
        ], total

    async def list_audit(
        self,
        notice_id: UUID,
        *,
        limit: int,
    ) -> list[SuperAdminNoticeAuditEventRead]:
        await self._notice(notice_id)
        event_rows = list(
            (
                await self.db.execute(
                    select(SuperAdminNoticeAuditEvent, User.name)
                    .outerjoin(User, User.id == SuperAdminNoticeAuditEvent.actor_user_id)
                    .where(SuperAdminNoticeAuditEvent.notice_id == notice_id)
                    .order_by(
                        SuperAdminNoticeAuditEvent.occurred_at.asc(),
                        SuperAdminNoticeAuditEvent.id.asc(),
                    )
                    .limit(limit)
                )
            ).all()
        )
        return [
            SuperAdminNoticeAuditEventRead(
                id=event.id,
                notice_id=event.notice_id,
                actor_user_id=event.actor_user_id,
                actor_user_name=actor_name,
                action=event.action,
                details=event.details,
                occurred_at=_as_utc(event.occurred_at),
            )
            for event, actor_name in event_rows
        ]

    async def inbox(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID | None,
        offset: int,
        limit: int,
    ) -> SuperAdminNoticeInboxRead:
        visibility = self._receipt_visibility(workspace_id)
        filters = (
            SuperAdminNoticeReceipt.recipient_user_id == user_id,
            SuperAdminNoticeReceipt.channel == SuperAdminNoticeChannel.in_app,
            SuperAdminNoticeReceipt.state
            == SuperAdminNoticeReceiptState.in_app_available,
            SuperAdminNotice.status == SuperAdminNoticeStatus.published,
            visibility,
        )
        count_statement = (
            select(func.count(SuperAdminNoticeReceipt.id))
            .select_from(SuperAdminNoticeReceipt)
            .join(
                SuperAdminNotice,
                SuperAdminNotice.id == SuperAdminNoticeReceipt.notice_id,
            )
            .where(*filters)
        )
        total = int(await self.db.scalar(count_statement) or 0)
        unread = int(
            await self.db.scalar(
                count_statement.where(SuperAdminNoticeReceipt.read_at.is_(None))
            )
            or 0
        )
        pending_ack = int(
            await self.db.scalar(
                count_statement.where(
                    SuperAdminNotice.requires_acknowledgement.is_(True),
                    SuperAdminNoticeReceipt.acknowledged_at.is_(None),
                )
            )
            or 0
        )
        rows = (
            await self.db.execute(
                select(SuperAdminNoticeReceipt, SuperAdminNotice)
                .join(
                    SuperAdminNotice,
                    SuperAdminNotice.id == SuperAdminNoticeReceipt.notice_id,
                )
                .where(*filters)
                .order_by(
                    SuperAdminNotice.published_at.desc(),
                    SuperAdminNoticeReceipt.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return SuperAdminNoticeInboxRead(
            items=[self._to_inbox_item(receipt, notice) for receipt, notice in rows],
            total=total,
            unread_count=unread,
            pending_acknowledgement_count=pending_ack,
        )

    async def mark_read(
        self,
        receipt_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID | None,
    ) -> SuperAdminNoticeInboxItemRead:
        receipt, notice = await self._inbox_receipt(
            receipt_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        if receipt.read_at is None:
            receipt.read_at = utc_now()
            self._audit(
                notice_id=notice.id,
                actor_user_id=user_id,
                action=SuperAdminNoticeAuditAction.read,
                details={"receipt_id": str(receipt.id)},
            )
            await self.db.flush()
        return self._to_inbox_item(receipt, notice)

    async def acknowledge(
        self,
        receipt_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID | None,
    ) -> SuperAdminNoticeInboxItemRead:
        receipt, notice = await self._inbox_receipt(
            receipt_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        changed = False
        if receipt.read_at is None:
            receipt.read_at = utc_now()
            self._audit(
                notice_id=notice.id,
                actor_user_id=user_id,
                action=SuperAdminNoticeAuditAction.read,
                details={"receipt_id": str(receipt.id), "via": "acknowledge"},
            )
            changed = True
        if receipt.acknowledged_at is None:
            receipt.acknowledged_at = utc_now()
            self._audit(
                notice_id=notice.id,
                actor_user_id=user_id,
                action=SuperAdminNoticeAuditAction.acknowledged,
                details={"receipt_id": str(receipt.id)},
            )
            changed = True
        if changed:
            await self.db.flush()
        return self._to_inbox_item(receipt, notice)

    async def _publish_notice(
        self,
        notice: SuperAdminNotice,
        *,
        actor_user_id: UUID,
        idempotency_record_id: UUID,
    ) -> None:
        recipients = await self._resolve_recipients(self._audience(notice))
        now = utc_now()
        await self._register_receipts(notice, recipients, now=now)
        notice.status = SuperAdminNoticeStatus.published
        notice.published_at = now
        notice.published_by_user_id = actor_user_id
        notice.updated_by_user_id = actor_user_id
        notice.version += 1
        states = await self._receipt_state_counts(notice.id)
        self._audit(
            notice_id=notice.id,
            actor_user_id=actor_user_id,
            action=SuperAdminNoticeAuditAction.published,
            details={
                "recipient_count": len(recipients),
                "receipt_count": sum(states.values()),
                "receipts_by_state": states,
                "email_delivery_mode": (
                    "local_queue_only"
                    if SuperAdminNoticeChannel.email.value in notice.channels
                    else None
                ),
                "external_delivery_attempted": False,
                "idempotency_record_id": str(idempotency_record_id),
            },
        )
        await self.db.flush()
        await self.db.refresh(notice)

    async def _register_receipts(
        self,
        notice: SuperAdminNotice,
        recipients: list[_ResolvedRecipient],
        *,
        now: datetime,
    ) -> None:
        receipt_values: list[dict[str, Any]] = []
        scope_values: list[dict[str, UUID]] = []
        channels = [SuperAdminNoticeChannel(channel) for channel in notice.channels]
        for recipient in recipients:
            for channel in channels:
                receipt_id = uuid5(
                    NAMESPACE_URL,
                    f"klm:super-admin-notice:{notice.id}:{recipient.user_id}:{channel.value}",
                )
                if channel == SuperAdminNoticeChannel.in_app:
                    state = SuperAdminNoticeReceiptState.in_app_available
                    available_at = now
                    queued_at = None
                elif recipient.email:
                    state = SuperAdminNoticeReceiptState.email_queued_local
                    available_at = None
                    queued_at = now
                else:
                    state = SuperAdminNoticeReceiptState.email_blocked_missing_address
                    available_at = None
                    queued_at = None
                receipt_values.append(
                    {
                        "id": receipt_id,
                        "notice_id": notice.id,
                        "recipient_user_id": recipient.user_id,
                        "channel": channel,
                        "state": state,
                        "recipient_address_hint": self._email_hint(recipient.email),
                        "recipient_address_digest": self._email_digest(recipient.email),
                        "matched_by": recipient.matched_by,
                        "scope_all_workspaces": recipient.scope_all_workspaces,
                        "external_delivery_attempted": False,
                        "available_at": available_at,
                        "queued_at": queued_at,
                    }
                )
                scope_values.extend(
                    {
                        "receipt_id": receipt_id,
                        "workspace_id": workspace_id,
                    }
                    for workspace_id in sorted(recipient.workspace_ids, key=str)
                )
        await self._upsert_rows(
            SuperAdminNoticeReceipt,
            receipt_values,
            conflict_columns=("notice_id", "recipient_user_id", "channel"),
        )
        await self._upsert_rows(
            SuperAdminNoticeReceiptWorkspace,
            scope_values,
            conflict_columns=("receipt_id", "workspace_id"),
        )

    async def _upsert_rows(
        self,
        model,
        values: list[dict[str, Any]],
        *,
        conflict_columns: tuple[str, ...],
    ) -> None:
        if not values:
            return
        dialect = self.db.get_bind().dialect.name
        for start in range(0, len(values), UPSERT_CHUNK_SIZE):
            chunk = values[start : start + UPSERT_CHUNK_SIZE]
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert

                statement = insert(model).values(chunk).on_conflict_do_nothing(
                    index_elements=list(conflict_columns)
                )
                await self.db.execute(statement)
            elif dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert

                statement = insert(model).values(chunk).on_conflict_do_nothing(
                    index_elements=list(conflict_columns)
                )
                await self.db.execute(statement)
            else:
                for value in chunk:
                    try:
                        async with self.db.begin_nested():
                            self.db.add(model(**value))
                            await self.db.flush()
                    except IntegrityError:
                        continue
        await self.db.flush()

    async def _publication_read(
        self,
        notice: SuperAdminNotice,
    ) -> SuperAdminNoticePublicationRead:
        states = await self._receipt_state_counts(notice.id)
        recipient_count = int(
            await self.db.scalar(
                select(func.count(func.distinct(SuperAdminNoticeReceipt.recipient_user_id))).where(
                    SuperAdminNoticeReceipt.notice_id == notice.id
                )
            )
            or 0
        )
        return SuperAdminNoticePublicationRead(
            notice=self._to_notice_read(notice),
            recipient_count=recipient_count,
            receipt_count=sum(states.values()),
            receipts_by_state=states,
            email_delivery_mode=(
                "local_queue_only"
                if SuperAdminNoticeChannel.email.value in notice.channels
                else None
            ),
        )

    async def _receipt_state_counts(self, notice_id: UUID) -> dict[str, int]:
        rows = (
            await self.db.execute(
                select(
                    SuperAdminNoticeReceipt.state,
                    func.count(SuperAdminNoticeReceipt.id),
                )
                .where(SuperAdminNoticeReceipt.notice_id == notice_id)
                .group_by(SuperAdminNoticeReceipt.state)
            )
        ).all()
        return {state.value: int(count) for state, count in rows}

    async def _validate_audience(self, audience: SuperAdminNoticeAudience) -> None:
        if audience.kind == SuperAdminNoticeAudienceKind.workspaces:
            found = set(
                (
                    await self.db.scalars(
                        select(Workspace.id).where(
                            Workspace.id.in_(audience.workspace_ids),
                            Workspace.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            missing = set(audience.workspace_ids) - found
            if missing:
                missing_text = ", ".join(map(str, sorted(missing, key=str)))
                raise SuperAdminNoticeValidationError(
                    f"Workspaces inexistentes no publico: {missing_text}"
                )
        elif audience.kind == SuperAdminNoticeAudienceKind.users:
            found = set(
                (
                    await self.db.scalars(
                        select(User.id).where(
                            User.id.in_(audience.user_ids),
                            User.agent_type == AgentType.human,
                            User.is_active.is_(True),
                            User.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            missing = set(audience.user_ids) - found
            if missing:
                missing_text = ", ".join(map(str, sorted(missing, key=str)))
                raise SuperAdminNoticeValidationError(
                    f"Usuarios inexistentes ou inativos no publico: {missing_text}"
                )

    async def _resolve_recipients(
        self,
        audience: SuperAdminNoticeAudience,
    ) -> list[_ResolvedRecipient]:
        user_filters = (
            User.agent_type == AgentType.human,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        recipients: dict[UUID, _ResolvedRecipient] = {}

        def add(user: User, *, matched_by: str, workspace_id: UUID | None = None) -> None:
            recipient = recipients.setdefault(
                user.id,
                _ResolvedRecipient(
                    user_id=user.id,
                    name=user.name,
                    email=user.email,
                    matched_by=matched_by,
                ),
            )
            if workspace_id is not None:
                recipient.workspace_ids.add(workspace_id)

        if audience.kind == SuperAdminNoticeAudienceKind.global_:
            users = (
                await self.db.scalars(
                    select(User).where(*user_filters).order_by(User.id.asc())
                )
            ).all()
            for user in users:
                add(user, matched_by="global")
        elif audience.kind == SuperAdminNoticeAudienceKind.users:
            users = (
                await self.db.scalars(
                    select(User)
                    .where(User.id.in_(audience.user_ids), *user_filters)
                    .order_by(User.id.asc())
                )
            ).all()
            for user in users:
                add(user, matched_by="user")
        elif audience.kind == SuperAdminNoticeAudienceKind.workspaces:
            rows = (
                await self.db.execute(
                    select(User, WorkspaceMembership.workspace_id)
                    .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
                    .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
                    .where(
                        WorkspaceMembership.workspace_id.in_(audience.workspace_ids),
                        WorkspaceMembership.status == MembershipStatus.active,
                        Workspace.is_active.is_(True),
                        Workspace.deleted_at.is_(None),
                        *user_filters,
                    )
                    .order_by(User.id.asc(), WorkspaceMembership.workspace_id.asc())
                )
            ).all()
            for user, workspace_id in rows:
                add(user, matched_by="workspace", workspace_id=workspace_id)
        elif audience.rule is not None:
            if audience.rule.field == SuperAdminNoticeRuleField.user_role:
                users = (
                    await self.db.scalars(
                        select(User)
                        .where(User.role.in_(audience.rule.values), *user_filters)
                        .order_by(User.id.asc())
                    )
                ).all()
                for user in users:
                    add(user, matched_by="rule:user_role")
            elif audience.rule.field == SuperAdminNoticeRuleField.workspace_role:
                rows = (
                    await self.db.execute(
                        select(User, WorkspaceMembership.workspace_id)
                        .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
                        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
                        .where(
                            WorkspaceMembership.role.in_(audience.rule.values),
                            WorkspaceMembership.status == MembershipStatus.active,
                            Workspace.is_active.is_(True),
                            Workspace.deleted_at.is_(None),
                            *user_filters,
                        )
                        .order_by(User.id.asc(), WorkspaceMembership.workspace_id.asc())
                    )
                ).all()
                for user, workspace_id in rows:
                    add(
                        user,
                        matched_by="rule:workspace_role",
                        workspace_id=workspace_id,
                    )
        return list(recipients.values())

    async def _notice(
        self,
        notice_id: UUID,
        *,
        lock: bool = False,
    ) -> SuperAdminNotice:
        statement = select(SuperAdminNotice).where(SuperAdminNotice.id == notice_id)
        if lock:
            statement = statement.with_for_update()
        notice = await self.db.scalar(statement)
        if notice is None:
            raise SuperAdminNoticeNotFoundError("Aviso nao encontrado.")
        return notice

    async def _inbox_receipt(
        self,
        receipt_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID | None,
    ) -> tuple[SuperAdminNoticeReceipt, SuperAdminNotice]:
        row = (
            await self.db.execute(
                select(SuperAdminNoticeReceipt, SuperAdminNotice)
                .join(
                    SuperAdminNotice,
                    SuperAdminNotice.id == SuperAdminNoticeReceipt.notice_id,
                )
                .where(
                    SuperAdminNoticeReceipt.id == receipt_id,
                    SuperAdminNoticeReceipt.recipient_user_id == user_id,
                    SuperAdminNoticeReceipt.channel == SuperAdminNoticeChannel.in_app,
                    SuperAdminNoticeReceipt.state
                    == SuperAdminNoticeReceiptState.in_app_available,
                    SuperAdminNotice.status == SuperAdminNoticeStatus.published,
                    self._receipt_visibility(workspace_id),
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise SuperAdminNoticeNotFoundError("Recibo in-app nao encontrado.")
        return row[0], row[1]

    @staticmethod
    def _receipt_visibility(workspace_id: UUID | None):
        if workspace_id is None:
            return SuperAdminNoticeReceipt.scope_all_workspaces.is_(True)
        return or_(
            SuperAdminNoticeReceipt.scope_all_workspaces.is_(True),
            select(SuperAdminNoticeReceiptWorkspace.receipt_id)
            .where(
                SuperAdminNoticeReceiptWorkspace.receipt_id
                == SuperAdminNoticeReceipt.id,
                SuperAdminNoticeReceiptWorkspace.workspace_id == workspace_id,
            )
            .exists(),
        )

    async def _receipt_workspace_map(
        self,
        receipt_ids: list[UUID],
    ) -> dict[UUID, list[UUID]]:
        if not receipt_ids:
            return {}
        rows = (
            await self.db.execute(
                select(
                    SuperAdminNoticeReceiptWorkspace.receipt_id,
                    SuperAdminNoticeReceiptWorkspace.workspace_id,
                )
                .where(SuperAdminNoticeReceiptWorkspace.receipt_id.in_(receipt_ids))
                .order_by(
                    SuperAdminNoticeReceiptWorkspace.receipt_id.asc(),
                    SuperAdminNoticeReceiptWorkspace.workspace_id.asc(),
                )
            )
        ).all()
        result: dict[UUID, list[UUID]] = {}
        for receipt_id, workspace_id in rows:
            result.setdefault(receipt_id, []).append(workspace_id)
        return result

    def _audit(
        self,
        *,
        notice_id: UUID,
        actor_user_id: UUID,
        action: SuperAdminNoticeAuditAction,
        details: dict[str, Any],
    ) -> None:
        self.db.add(
            SuperAdminNoticeAuditEvent(
                notice_id=notice_id,
                actor_user_id=actor_user_id,
                action=action,
                details=details,
            )
        )

    @staticmethod
    def _audience(notice: SuperAdminNotice) -> SuperAdminNoticeAudience:
        data = dict(notice.audience_definition or {})
        data["kind"] = notice.audience_kind.value
        return SuperAdminNoticeAudience.model_validate(data)

    def _to_notice_read(self, notice: SuperAdminNotice) -> SuperAdminNoticeRead:
        return SuperAdminNoticeRead(
            id=notice.id,
            title=notice.title,
            body=notice.body,
            status=notice.status,
            audience=self._audience(notice),
            channels=[SuperAdminNoticeChannel(channel) for channel in notice.channels],
            requires_acknowledgement=notice.requires_acknowledgement,
            email_delivery_mode=(
                "local_queue_only"
                if SuperAdminNoticeChannel.email.value in notice.channels
                else None
            ),
            scheduled_at=_optional_utc(notice.scheduled_at),
            published_at=_optional_utc(notice.published_at),
            cancelled_at=_optional_utc(notice.cancelled_at),
            created_by_user_id=notice.created_by_user_id,
            updated_by_user_id=notice.updated_by_user_id,
            published_by_user_id=notice.published_by_user_id,
            cancelled_by_user_id=notice.cancelled_by_user_id,
            version=notice.version,
            created_at=_as_utc(notice.created_at),
            updated_at=_as_utc(notice.updated_at),
        )

    @classmethod
    def _to_recipient_read(
        cls,
        recipient: _ResolvedRecipient,
    ) -> SuperAdminNoticeRecipientRead:
        return SuperAdminNoticeRecipientRead(
            user_id=recipient.user_id,
            name=recipient.name,
            email_hint=cls._email_hint(recipient.email),
            matched_by=recipient.matched_by,
            workspace_ids=sorted(recipient.workspace_ids, key=str),
        )

    @staticmethod
    def _to_receipt_read(
        receipt: SuperAdminNoticeReceipt,
        workspace_ids: list[UUID],
        recipient_name: str,
    ) -> SuperAdminNoticeReceiptRead:
        return SuperAdminNoticeReceiptRead(
            id=receipt.id,
            notice_id=receipt.notice_id,
            recipient_user_id=receipt.recipient_user_id,
            recipient_name=recipient_name,
            channel=receipt.channel,
            state=receipt.state,
            delivery_mode=(
                "in_app_local"
                if receipt.channel == SuperAdminNoticeChannel.in_app
                else "local_queue_only"
            ),
            external_delivery_attempted=receipt.external_delivery_attempted,
            recipient_address_hint=receipt.recipient_address_hint,
            matched_by=receipt.matched_by,
            scope_all_workspaces=receipt.scope_all_workspaces,
            workspace_ids=workspace_ids,
            available_at=_optional_utc(receipt.available_at),
            queued_at=_optional_utc(receipt.queued_at),
            read_at=_optional_utc(receipt.read_at),
            acknowledged_at=_optional_utc(receipt.acknowledged_at),
            created_at=_as_utc(receipt.created_at),
            updated_at=_as_utc(receipt.updated_at),
        )

    @staticmethod
    def _to_inbox_item(
        receipt: SuperAdminNoticeReceipt,
        notice: SuperAdminNotice,
    ) -> SuperAdminNoticeInboxItemRead:
        if notice.published_at is None:
            raise RuntimeError("Aviso de inbox sem data de publicacao.")
        return SuperAdminNoticeInboxItemRead(
            receipt_id=receipt.id,
            notice_id=notice.id,
            title=notice.title,
            body=notice.body,
            requires_acknowledgement=notice.requires_acknowledgement,
            published_at=_as_utc(notice.published_at),
            read_at=_optional_utc(receipt.read_at),
            acknowledged_at=_optional_utc(receipt.acknowledged_at),
        )

    @staticmethod
    def _email_hint(email: str | None) -> str | None:
        if not email:
            return None
        local, separator, domain = email.partition("@")
        if not separator:
            return "***"
        return f"{local[:1]}{'*' * max(3, len(local) - 1)}@{domain}"

    @staticmethod
    def _email_digest(email: str | None) -> str | None:
        if not email:
            return None
        return hmac.new(
            settings.SECRET_KEY.encode(),
            f"notice-email:{email.strip().casefold()}".encode(),
            hashlib.sha256,
        ).hexdigest()
