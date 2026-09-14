"""Tenant-bound evidence for WhatsApp provider media uploads."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.omnichannel_v6 import (
    OmnichannelChannelConnection,
    OmnichannelProviderMediaReceipt,
    OmnichannelProviderMediaUploadIntent,
    OmnichannelProviderMediaUploadReconciliation,
)
from app.services.whatsapp.cloud_payload import provider_media_references


class ProviderMediaReceiptError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 422,
        intent_id: UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.intent_id = intent_id


def _sha256_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ProviderMediaReceiptService:
    """Records provider responses and verifies payload references before delivery."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _assert_same_upload_intent(
        intent: OmnichannelProviderMediaUploadIntent,
        *,
        connection_id: UUID,
        provider: str,
        payload_sha256: str,
    ) -> None:
        if (
            intent.connection_id != connection_id
            or intent.provider != provider
            or intent.payload_sha256 != payload_sha256
        ):
            raise ProviderMediaReceiptError(
                "A chave idempotente já foi usada por outro arquivo ou conexão.",
                code="provider_media_idempotency_conflict",
                status_code=409,
                intent_id=intent.id,
            )

    async def prepare_upload_intent(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        provider: str,
        idempotency_key_hash: str,
        payload_sha256: str,
        media_type: str,
        source_sha256: str,
        source_size_bytes: int,
        source_content_type: str,
        source_extension: str,
        source_filename_sha256: str,
    ) -> tuple[OmnichannelProviderMediaUploadIntent, bool]:
        """Commit an upload checkpoint before any provider request can start."""
        provider = provider.strip().lower()
        existing = await self.db.scalar(
            select(OmnichannelProviderMediaUploadIntent).where(
                OmnichannelProviderMediaUploadIntent.workspace_id == workspace_id,
                OmnichannelProviderMediaUploadIntent.idempotency_key_hash == idempotency_key_hash,
            )
        )
        if existing is not None:
            self._assert_same_upload_intent(
                existing,
                connection_id=connection_id,
                provider=provider,
                payload_sha256=payload_sha256,
            )
            return existing, False

        intent = OmnichannelProviderMediaUploadIntent(
            workspace_id=workspace_id,
            connection_id=connection_id,
            provider=provider,
            idempotency_key_hash=idempotency_key_hash,
            payload_sha256=payload_sha256,
            media_type=media_type,
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
            source_content_type=source_content_type,
            source_extension=source_extension,
            source_filename_sha256=source_filename_sha256,
            state="prepared",
        )
        self.db.add(intent)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            existing = await self.db.scalar(
                select(OmnichannelProviderMediaUploadIntent).where(
                    OmnichannelProviderMediaUploadIntent.workspace_id == workspace_id,
                    OmnichannelProviderMediaUploadIntent.idempotency_key_hash
                    == idempotency_key_hash,
                )
            )
            if existing is None:
                raise
            self._assert_same_upload_intent(
                existing,
                connection_id=connection_id,
                provider=provider,
                payload_sha256=payload_sha256,
            )
            return existing, False
        await self.db.refresh(intent)
        return intent, True

    async def get_upload_intent(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        intent_id: UUID,
    ) -> OmnichannelProviderMediaUploadIntent:
        intent = await self.db.scalar(
            select(OmnichannelProviderMediaUploadIntent).where(
                OmnichannelProviderMediaUploadIntent.workspace_id == workspace_id,
                OmnichannelProviderMediaUploadIntent.connection_id == connection_id,
                OmnichannelProviderMediaUploadIntent.id == intent_id,
            )
        )
        if intent is None:
            raise ProviderMediaReceiptError(
                "A intenção de upload não pertence a esta conexão e workspace.",
                code="provider_media_upload_intent_not_found",
                status_code=404,
                intent_id=intent_id,
            )
        return intent

    async def latest_reconciliation(
        self,
        intent: OmnichannelProviderMediaUploadIntent,
    ) -> OmnichannelProviderMediaUploadReconciliation | None:
        return await self.db.scalar(
            select(OmnichannelProviderMediaUploadReconciliation)
            .where(
                OmnichannelProviderMediaUploadReconciliation.workspace_id
                == intent.workspace_id,
                OmnichannelProviderMediaUploadReconciliation.connection_id
                == intent.connection_id,
                OmnichannelProviderMediaUploadReconciliation.upload_intent_id
                == intent.id,
            )
            .order_by(
                OmnichannelProviderMediaUploadReconciliation.resolved_at.desc(),
                OmnichannelProviderMediaUploadReconciliation.id.desc(),
            )
            .limit(1)
        )

    async def reconcile_upload_intent(
        self,
        intent: OmnichannelProviderMediaUploadIntent,
        *,
        actor_user_id: UUID,
        decision: str,
        reason: str,
        provider_media_id: str | None = None,
        provider_receipt: dict[str, Any] | None = None,
    ) -> OmnichannelProviderMediaUploadIntent:
        """Resolve an uncertain upload through an immutable, human-owned decision."""
        decision = decision.strip().lower()
        reason = reason.strip()
        provider_media_id = str(provider_media_id or "").strip() or None
        provider_receipt = dict(provider_receipt or {})
        if decision not in {"provider_accepted", "retry_authorized"}:
            raise ProviderMediaReceiptError(
                "A decisão de reconciliação do upload é inválida.",
                code="provider_media_reconciliation_decision_invalid",
            )
        if not 3 <= len(reason) <= 1_000:
            raise ProviderMediaReceiptError(
                "O motivo da reconciliação deve ter entre 3 e 1.000 caracteres.",
                code="provider_media_reconciliation_reason_invalid",
            )
        if len(json.dumps(provider_receipt, ensure_ascii=True, default=str)) > 65_536:
            raise ProviderMediaReceiptError(
                "A prova do provedor excede o limite de reconciliação.",
                code="provider_media_reconciliation_evidence_too_large",
            )
        if decision == "provider_accepted":
            if not provider_media_id or not provider_receipt:
                raise ProviderMediaReceiptError(
                    "A confirmação exige o ID e a resposta autenticada do provedor.",
                    code="provider_media_reconciliation_evidence_missing",
                )
            if str(provider_receipt.get("id") or "").strip() != provider_media_id:
                raise ProviderMediaReceiptError(
                    "O ID da prova diverge do ID de mídia confirmado.",
                    code="provider_media_provider_id_mismatch",
                )
        elif provider_media_id is not None or provider_receipt:
            raise ProviderMediaReceiptError(
                "A autorização de nova tentativa não pode conter prova de aceite.",
                code="provider_media_reconciliation_evidence_invalid",
            )

        locked = await self.db.scalar(
            select(OmnichannelProviderMediaUploadIntent)
            .where(
                OmnichannelProviderMediaUploadIntent.workspace_id == intent.workspace_id,
                OmnichannelProviderMediaUploadIntent.connection_id == intent.connection_id,
                OmnichannelProviderMediaUploadIntent.id == intent.id,
            )
            .with_for_update()
        )
        if locked is None:
            raise ProviderMediaReceiptError(
                "A intenção de upload não pertence a esta conexão e workspace.",
                code="provider_media_upload_intent_not_found",
                status_code=404,
                intent_id=intent.id,
            )
        latest = await self.latest_reconciliation(locked)
        if (
            decision == "provider_accepted"
            and locked.state in {"provider_accepted", "completed"}
            and latest is not None
            and latest.decision == decision
            and latest.provider_media_id == provider_media_id
            and latest.provider_receipt == provider_receipt
        ):
            return locked
        if (
            decision == "retry_authorized"
            and locked.state == "prepared"
            and latest is not None
            and latest.decision == decision
            and latest.reason == reason
        ):
            return locked
        if locked.state not in {"dispatching", "delivery_uncertain"}:
            raise ProviderMediaReceiptError(
                "O estado atual do upload não admite reconciliação humana.",
                code="provider_media_reconciliation_state_conflict",
                status_code=409,
                intent_id=locked.id,
            )

        now = datetime.now(UTC)
        if decision == "provider_accepted":
            locked.state = "provider_accepted"
            locked.provider_media_id = provider_media_id
            locked.provider_receipt = provider_receipt
            locked.provider_accepted_at = now
            locked.delivery_uncertain_at = None
            locked.last_error_code = None
            locked.last_error_message = None
        else:
            locked.state = "prepared"
            locked.provider_media_id = None
            locked.provider_receipt = {}
            locked.receipt_id = None
            locked.request_started_at = None
            locked.provider_accepted_at = None
            locked.delivery_uncertain_at = None
            locked.completed_at = None
            locked.last_error_code = "provider_media_retry_authorized"
            locked.last_error_message = reason

        event = OmnichannelProviderMediaUploadReconciliation(
            workspace_id=locked.workspace_id,
            connection_id=locked.connection_id,
            upload_intent_id=locked.id,
            actor_user_id=actor_user_id,
            decision=decision,
            reason=reason,
            provider_media_id=provider_media_id,
            provider_receipt=provider_receipt,
            resolved_at=now,
        )
        self.db.add(event)
        try:
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            raise ProviderMediaReceiptError(
                "A decisão de reconciliação não pôde ser registrada com segurança.",
                code="provider_media_reconciliation_write_failed",
                status_code=409,
                intent_id=locked.id,
            ) from exc
        await self.db.refresh(locked)
        return locked

    async def claim_upload_intent(
        self,
        intent: OmnichannelProviderMediaUploadIntent,
    ) -> tuple[OmnichannelProviderMediaUploadIntent, bool]:
        now = datetime.now(UTC)
        claim = await self.db.execute(
            update(OmnichannelProviderMediaUploadIntent)
            .where(
                OmnichannelProviderMediaUploadIntent.workspace_id == intent.workspace_id,
                OmnichannelProviderMediaUploadIntent.id == intent.id,
                OmnichannelProviderMediaUploadIntent.state == "prepared",
            )
            .values(
                state="dispatching",
                attempt_count=OmnichannelProviderMediaUploadIntent.attempt_count + 1,
                request_started_at=now,
                delivery_uncertain_at=None,
                last_error_code=None,
                last_error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        await self.db.refresh(intent)
        return intent, claim.rowcount == 1

    async def checkpoint_provider_acceptance(
        self,
        intent: OmnichannelProviderMediaUploadIntent,
        *,
        provider_media_id: str,
        provider_receipt: dict[str, Any],
    ) -> OmnichannelProviderMediaUploadIntent:
        intent_id = intent.id
        now = datetime.now(UTC)
        checkpoint = await self.db.execute(
            update(OmnichannelProviderMediaUploadIntent)
            .where(
                OmnichannelProviderMediaUploadIntent.workspace_id == intent.workspace_id,
                OmnichannelProviderMediaUploadIntent.id == intent.id,
                OmnichannelProviderMediaUploadIntent.state == "dispatching",
            )
            .values(
                state="provider_accepted",
                provider_media_id=provider_media_id,
                provider_receipt=dict(provider_receipt),
                provider_accepted_at=now,
                last_error_code=None,
                last_error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        if checkpoint.rowcount != 1:
            await self.db.rollback()
            raise ProviderMediaReceiptError(
                "O estado do upload mudou antes do checkpoint do provedor.",
                code="provider_media_reconciliation_required",
                status_code=409,
                intent_id=intent_id,
            )
        try:
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            raise ProviderMediaReceiptError(
                "A Meta aceitou a mídia, mas o checkpoint local exige reconciliação.",
                code="provider_media_reconciliation_required",
                status_code=409,
                intent_id=intent_id,
            ) from exc
        await self.db.refresh(intent)
        return intent

    async def record_upload_error(
        self,
        intent: OmnichannelProviderMediaUploadIntent,
        *,
        state: str,
        code: str,
        message: str,
    ) -> None:
        if state not in {"prepared", "failed", "delivery_uncertain"}:
            raise ValueError(f"invalid provider media upload error state: {state}")
        now = datetime.now(UTC)
        checkpoint = await self.db.execute(
            update(OmnichannelProviderMediaUploadIntent)
            .where(
                OmnichannelProviderMediaUploadIntent.workspace_id == intent.workspace_id,
                OmnichannelProviderMediaUploadIntent.id == intent.id,
                OmnichannelProviderMediaUploadIntent.state == "dispatching",
            )
            .values(
                state=state,
                delivery_uncertain_at=(now if state == "delivery_uncertain" else None),
                last_error_code=code[:120],
                last_error_message=message[:4_000],
            )
            .execution_options(synchronize_session=False)
        )
        if checkpoint.rowcount != 1:
            await self.db.rollback()
            raise ProviderMediaReceiptError(
                "O estado do upload mudou antes do registro da falha.",
                code="provider_media_reconciliation_required",
                status_code=409,
                intent_id=intent.id,
            )
        await self.db.commit()
        await self.db.refresh(intent)

    async def receipt_for_intent(
        self,
        intent: OmnichannelProviderMediaUploadIntent,
    ) -> OmnichannelProviderMediaReceipt:
        receipt = None
        if intent.receipt_id is not None:
            receipt = await self.db.scalar(
                select(OmnichannelProviderMediaReceipt).where(
                    OmnichannelProviderMediaReceipt.workspace_id == intent.workspace_id,
                    OmnichannelProviderMediaReceipt.id == intent.receipt_id,
                )
            )
        if receipt is None:
            raise ProviderMediaReceiptError(
                "O upload concluído não possui prova imutável recuperável.",
                code="provider_media_receipt_not_found",
                status_code=409,
                intent_id=intent.id,
            )
        return receipt

    async def complete_upload_intent(
        self,
        intent: OmnichannelProviderMediaUploadIntent,
        *,
        expires_at: datetime,
    ) -> OmnichannelProviderMediaReceipt:
        intent_id = intent.id
        if intent.state == "completed":
            return await self.receipt_for_intent(intent)
        if (
            intent.state != "provider_accepted"
            or not intent.provider_media_id
            or not intent.provider_receipt
        ):
            raise ProviderMediaReceiptError(
                "A resposta do provedor ainda não está pronta para ser finalizada.",
                code="provider_media_reconciliation_required",
                status_code=409,
                intent_id=intent_id,
            )
        uploaded_at = intent.provider_accepted_at or datetime.now(UTC)
        try:
            receipt = await self.record_verified_upload(
                workspace_id=intent.workspace_id,
                connection_id=intent.connection_id,
                provider=intent.provider,
                provider_media_id=intent.provider_media_id,
                media_type=intent.media_type,
                source_sha256=intent.source_sha256,
                source_size_bytes=intent.source_size_bytes,
                source_content_type=intent.source_content_type,
                provider_receipt=intent.provider_receipt,
                uploaded_at=uploaded_at,
                expires_at=expires_at,
            )
        except ProviderMediaReceiptError as exc:
            if exc.intent_id is not None:
                raise
            raise ProviderMediaReceiptError(
                str(exc),
                code=exc.code,
                status_code=exc.status_code,
                intent_id=intent_id,
            ) from exc
        intent.state = "completed"
        intent.receipt_id = receipt.id
        intent.completed_at = datetime.now(UTC)
        intent.last_error_code = None
        intent.last_error_message = None
        try:
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            raise ProviderMediaReceiptError(
                "A prova imutável do upload ainda precisa ser reconciliada.",
                code="provider_media_reconciliation_required",
                status_code=409,
                intent_id=intent_id,
            ) from exc
        await self.db.refresh(receipt)
        await self.db.refresh(intent)
        return receipt

    async def record_verified_upload(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        provider: str,
        provider_media_id: str,
        media_type: str,
        source_sha256: str,
        source_size_bytes: int,
        source_content_type: str,
        provider_receipt: dict[str, Any],
        uploaded_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> OmnichannelProviderMediaReceipt:
        provider = provider.strip().lower()
        provider_media_id = provider_media_id.strip()
        media_type = media_type.strip().lower()
        source_sha256 = source_sha256.strip().lower()
        source_content_type = source_content_type.split(";", 1)[0].strip().lower()
        if media_type not in {"image", "audio", "video", "document"}:
            raise ProviderMediaReceiptError(
                "O tipo da mídia carregada não é suportado.",
                code="provider_media_type_invalid",
            )
        if not provider_media_id:
            raise ProviderMediaReceiptError(
                "O provedor não retornou um ID de mídia.",
                code="provider_media_id_missing",
            )
        if not _valid_sha256(source_sha256):
            raise ProviderMediaReceiptError(
                "O hash da mídia carregada é inválido.",
                code="provider_media_source_hash_invalid",
            )
        if isinstance(source_size_bytes, bool) or source_size_bytes <= 0:
            raise ProviderMediaReceiptError(
                "O tamanho da mídia carregada é inválido.",
                code="provider_media_source_size_invalid",
            )
        if not source_content_type or len(source_content_type) > 160:
            raise ProviderMediaReceiptError(
                "O tipo de conteúdo da mídia carregada é inválido.",
                code="provider_media_content_type_invalid",
            )
        if not isinstance(provider_receipt, dict) or not provider_receipt:
            raise ProviderMediaReceiptError(
                "A resposta autenticada do provedor não foi recebida.",
                code="provider_media_receipt_missing",
            )
        if str(provider_receipt.get("id") or "").strip() != provider_media_id:
            raise ProviderMediaReceiptError(
                "O ID retornado pelo provedor diverge da mídia registrada.",
                code="provider_media_provider_id_mismatch",
            )

        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.id == connection_id,
                OmnichannelChannelConnection.provider == provider,
            )
        )
        if connection is None:
            raise ProviderMediaReceiptError(
                "A conexão da mídia não pertence a este workspace e provedor.",
                code="provider_media_connection_mismatch",
            )

        provider_payload_sha256 = _sha256_payload(provider_receipt)
        existing = await self.db.scalar(
            select(OmnichannelProviderMediaReceipt).where(
                OmnichannelProviderMediaReceipt.workspace_id == workspace_id,
                OmnichannelProviderMediaReceipt.connection_id == connection_id,
                OmnichannelProviderMediaReceipt.provider == provider,
                OmnichannelProviderMediaReceipt.provider_media_id == provider_media_id,
            )
        )
        if existing is not None:
            if (
                existing.media_type != media_type
                or existing.source_sha256 != source_sha256
                or existing.source_size_bytes != source_size_bytes
                or existing.source_content_type != source_content_type
                or existing.provider_payload_sha256 != provider_payload_sha256
            ):
                raise ProviderMediaReceiptError(
                    "O ID de mídia já possui outra prova imutável.",
                    code="provider_media_receipt_conflict",
                )
            return existing

        receipt = OmnichannelProviderMediaReceipt(
            workspace_id=workspace_id,
            connection_id=connection_id,
            provider=provider,
            provider_media_id=provider_media_id,
            media_type=media_type,
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
            source_content_type=source_content_type,
            provider_payload_sha256=provider_payload_sha256,
            provider_receipt=dict(provider_receipt),
            uploaded_at=uploaded_at or datetime.now(UTC),
            expires_at=expires_at,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(receipt)
                await self.db.flush()
            return receipt
        except IntegrityError:
            existing = await self.db.scalar(
                select(OmnichannelProviderMediaReceipt).where(
                    OmnichannelProviderMediaReceipt.workspace_id == workspace_id,
                    OmnichannelProviderMediaReceipt.connection_id == connection_id,
                    OmnichannelProviderMediaReceipt.provider == provider,
                    OmnichannelProviderMediaReceipt.provider_media_id == provider_media_id,
                )
            )
            if existing is None:
                raise
            if (
                existing.media_type != media_type
                or existing.source_sha256 != source_sha256
                or existing.source_size_bytes != source_size_bytes
                or existing.source_content_type != source_content_type
                or existing.provider_payload_sha256 != provider_payload_sha256
            ):
                raise ProviderMediaReceiptError(
                    "O ID de mídia já possui outra prova imutável.",
                    code="provider_media_receipt_conflict",
                    status_code=409,
                )
            return existing

    async def resolve_native_connection(
        self,
        *,
        workspace_id: UUID,
        legacy_connection_id: UUID,
        provider: str,
    ) -> OmnichannelChannelConnection:
        """Resolve the tenant-owned native route paired with a legacy credential row."""
        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.provider == provider.strip().lower(),
                OmnichannelChannelConnection.connection_key == f"whatsapp:{legacy_connection_id}",
            )
        )
        if connection is None:
            raise ProviderMediaReceiptError(
                "A rota nativa da conexão WhatsApp não foi encontrada.",
                code="provider_media_native_connection_missing",
            )
        return connection

    async def verify_payload(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        provider: str,
        payload: dict[str, Any],
    ) -> set[str]:
        references = provider_media_references(payload)
        if not references:
            return set()

        receipt_ids = {UUID(reference["receiptId"]) for reference in references}
        rows = list(
            (
                await self.db.scalars(
                    select(OmnichannelProviderMediaReceipt).where(
                        OmnichannelProviderMediaReceipt.workspace_id == workspace_id,
                        OmnichannelProviderMediaReceipt.connection_id == connection_id,
                        OmnichannelProviderMediaReceipt.provider == provider,
                        OmnichannelProviderMediaReceipt.id.in_(receipt_ids),
                    )
                )
            ).all()
        )
        by_id = {str(row.id): row for row in rows}
        now = datetime.now(UTC)
        verified: set[str] = set()
        for reference in references:
            receipt = by_id.get(reference["receiptId"])
            if receipt is None:
                raise ProviderMediaReceiptError(
                    "A prova do upload não pertence a este workspace e conexão.",
                    code="provider_media_receipt_not_found",
                )
            if (
                receipt.provider_media_id != reference["providerMediaId"]
                or receipt.media_type != reference["mediaType"]
            ):
                raise ProviderMediaReceiptError(
                    "A prova do upload não corresponde à mídia solicitada.",
                    code="provider_media_receipt_mismatch",
                )
            if receipt.expires_at is not None and _aware(receipt.expires_at) <= now:
                raise ProviderMediaReceiptError(
                    "A prova do upload da mídia expirou.",
                    code="provider_media_receipt_expired",
                )
            verified.add(str(receipt.id))
        return verified
