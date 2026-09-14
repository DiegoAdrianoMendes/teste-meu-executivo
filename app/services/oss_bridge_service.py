"""Database-only operations for generic OSS bridges."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oss_bridge import (
    ExternalEntityLink,
    IntegrationEventReceipt,
    IntegrationEventReceiptStatus,
    OSSBridgeProvider,
    sanitize_oss_json,
)
from app.schemas.oss_bridge import ExternalEntityLinkUpsert, IntegrationEventReceiptCreate

DEFAULT_RECEIPT_LEASE_TIMEOUT = timedelta(minutes=5)


class ExternalEntityLinkConflictError(ValueError):
    """Raised when an external identity is already linked elsewhere."""


class IntegrationEventPayloadConflictError(ValueError):
    """Raised when an idempotency key is replayed with a different payload."""


def compute_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OSSBridgeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_link(self, workspace_id: UUID, link_id: UUID) -> ExternalEntityLink | None:
        return await self.db.scalar(
            select(ExternalEntityLink).where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.id == link_id,
            )
        )

    async def find_link_by_klm_entity(
        self,
        workspace_id: UUID,
        provider: OSSBridgeProvider,
        external_account_id: str,
        klm_entity_type: str,
        klm_entity_id: UUID,
        external_entity_type: str,
    ) -> ExternalEntityLink | None:
        return await self.db.scalar(
            select(ExternalEntityLink).where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.provider == provider,
                ExternalEntityLink.external_account_id == external_account_id,
                ExternalEntityLink.klm_entity_type == klm_entity_type,
                ExternalEntityLink.klm_entity_id == klm_entity_id,
                ExternalEntityLink.external_entity_type == external_entity_type,
            )
        )

    async def find_link_by_external_entity(
        self,
        workspace_id: UUID,
        provider: OSSBridgeProvider,
        external_account_id: str,
        external_entity_type: str,
        external_entity_id: str,
    ) -> ExternalEntityLink | None:
        return await self.db.scalar(
            select(ExternalEntityLink).where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.provider == provider,
                ExternalEntityLink.external_account_id == external_account_id,
                ExternalEntityLink.external_entity_type == external_entity_type,
                ExternalEntityLink.external_entity_id == external_entity_id,
            )
        )

    async def upsert_link(
        self,
        workspace_id: UUID,
        data: ExternalEntityLinkUpsert,
    ) -> ExternalEntityLink:
        local_link = await self.find_link_by_klm_entity(
            workspace_id,
            data.provider,
            data.external_account_id,
            data.klm_entity_type,
            data.klm_entity_id,
            data.external_entity_type,
        )
        external_link = await self.find_link_by_external_entity(
            workspace_id,
            data.provider,
            data.external_account_id,
            data.external_entity_type,
            data.external_entity_id,
        )
        if external_link is not None and (local_link is None or external_link.id != local_link.id):
            raise ExternalEntityLinkConflictError(
                "External entity is already linked to another KLM entity in this workspace."
            )

        sanitized_metadata = cast(dict[str, Any], sanitize_oss_json(data.metadata))
        if local_link is not None:
            try:
                async with self.db.begin_nested():
                    local_link.external_entity_id = data.external_entity_id
                    local_link.link_metadata = sanitized_metadata
                    local_link.sync_state = data.sync_state
                    local_link.last_synced_at = data.last_synced_at
                    local_link.sync_error = data.sync_error
                    await self.db.flush()
            except IntegrityError as exc:
                raise ExternalEntityLinkConflictError(
                    "External entity is already linked in this workspace."
                ) from exc
            return local_link

        link = ExternalEntityLink(
            workspace_id=workspace_id,
            provider=data.provider,
            klm_entity_type=data.klm_entity_type,
            klm_entity_id=data.klm_entity_id,
            external_account_id=data.external_account_id,
            external_entity_type=data.external_entity_type,
            external_entity_id=data.external_entity_id,
            link_metadata=sanitized_metadata,
            sync_state=data.sync_state,
            last_synced_at=data.last_synced_at,
            sync_error=data.sync_error,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(link)
                await self.db.flush()
        except IntegrityError as exc:
            concurrent = await self.find_link_by_klm_entity(
                workspace_id,
                data.provider,
                data.external_account_id,
                data.klm_entity_type,
                data.klm_entity_id,
                data.external_entity_type,
            )
            if concurrent is not None and concurrent.external_entity_id == data.external_entity_id:
                return concurrent
            raise ExternalEntityLinkConflictError(
                "KLM or external entity is already linked in this workspace."
            ) from exc
        return link

    async def get_receipt(
        self,
        workspace_id: UUID,
        receipt_id: UUID,
    ) -> IntegrationEventReceipt | None:
        return await self.db.scalar(
            select(IntegrationEventReceipt).where(
                IntegrationEventReceipt.workspace_id == workspace_id,
                IntegrationEventReceipt.id == receipt_id,
            )
        )

    async def find_receipt(
        self,
        workspace_id: UUID,
        provider: OSSBridgeProvider,
        external_event_id: str,
    ) -> IntegrationEventReceipt | None:
        return await self.db.scalar(
            select(IntegrationEventReceipt).where(
                IntegrationEventReceipt.workspace_id == workspace_id,
                IntegrationEventReceipt.provider == provider,
                IntegrationEventReceipt.external_event_id == external_event_id,
            )
        )

    @staticmethod
    def _ensure_same_payload(
        receipt: IntegrationEventReceipt,
        payload_hash: str,
    ) -> None:
        if not hmac.compare_digest(receipt.payload_hash, payload_hash):
            raise IntegrationEventPayloadConflictError(
                "External event ID was already registered with a different payload."
            )

    async def register_receipt(
        self,
        workspace_id: UUID,
        data: IntegrationEventReceiptCreate,
    ) -> tuple[IntegrationEventReceipt, bool]:
        sanitized_payload = cast(dict[str, Any], sanitize_oss_json(data.payload))
        # A receipt may prove idempotency, but it must never fingerprint a credential.
        # Hash the same redacted representation that is allowed to reach persistence.
        payload_hash = compute_payload_hash(sanitized_payload)
        existing = await self.find_receipt(
            workspace_id,
            data.provider,
            data.external_event_id,
        )
        if existing is not None:
            self._ensure_same_payload(existing, payload_hash)
            return existing, True

        receipt = IntegrationEventReceipt(
            workspace_id=workspace_id,
            provider=data.provider,
            external_event_id=data.external_event_id,
            payload_hash=payload_hash,
            status=data.status,
            payload=sanitized_payload,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(receipt)
                await self.db.flush()
        except IntegrityError:
            existing = await self.find_receipt(
                workspace_id,
                data.provider,
                data.external_event_id,
            )
            if existing is None:
                raise
            self._ensure_same_payload(existing, payload_hash)
            return existing, True
        return receipt, False

    async def claim_receipt(
        self,
        workspace_id: UUID,
        provider: OSSBridgeProvider,
        receipt_id: UUID,
        *,
        lease_timeout: timedelta = DEFAULT_RECEIPT_LEASE_TIMEOUT,
    ) -> datetime | None:
        """Atomically claim a new, failed, or expired receipt for processing."""

        lease_started_at = datetime.now(UTC)
        lease_expired_before = lease_started_at - lease_timeout
        result = await self.db.execute(
            update(IntegrationEventReceipt)
            .where(
                IntegrationEventReceipt.id == receipt_id,
                IntegrationEventReceipt.workspace_id == workspace_id,
                IntegrationEventReceipt.provider == provider,
                or_(
                    IntegrationEventReceipt.status.in_(
                        (
                            IntegrationEventReceiptStatus.received,
                            IntegrationEventReceiptStatus.failed,
                        )
                    ),
                    and_(
                        IntegrationEventReceipt.status
                        == IntegrationEventReceiptStatus.processing,
                        IntegrationEventReceipt.updated_at <= lease_expired_before,
                    ),
                ),
            )
            .values(
                status=IntegrationEventReceiptStatus.processing,
                processed_at=None,
                updated_at=lease_started_at,
            )
            .execution_options(synchronize_session="fetch")
        )
        await self.db.commit()
        return lease_started_at if result.rowcount == 1 else None

    async def finish_receipt(
        self,
        workspace_id: UUID,
        provider: OSSBridgeProvider,
        receipt_id: UUID,
        lease_started_at: datetime,
        status: IntegrationEventReceiptStatus,
    ) -> bool:
        """Finish only the receipt lease identified by the fencing token."""

        finished_at = datetime.now(UTC)
        result = await self.db.execute(
            update(IntegrationEventReceipt)
            .where(
                IntegrationEventReceipt.id == receipt_id,
                IntegrationEventReceipt.workspace_id == workspace_id,
                IntegrationEventReceipt.provider == provider,
                IntegrationEventReceipt.status == IntegrationEventReceiptStatus.processing,
                IntegrationEventReceipt.updated_at == lease_started_at,
            )
            .values(
                status=status,
                processed_at=finished_at,
                updated_at=finished_at,
            )
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount == 1
