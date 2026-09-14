from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meta_webhook_operations import (
    MetaWebhookAutomationPolicy,
    MetaWebhookOperationalState,
    MetaWebhookReceipt,
    MetaWebhookSubscriptionState,
)
from app.models.platform_work_item import PlatformWorkItem
from app.models.whatsapp import WhatsAppConnection
from app.services.message_evidence_service import ProviderEnvelopeContext, canonical_sha256
from app.services.whatsapp.meta_webhook_operations_service import _safe_operational_payload
from app.services.whatsapp.webhooks.registry import META_WEBHOOK_HANDLERS, META_WEBHOOK_REGISTRY


def _now() -> datetime:
    return datetime.now(UTC)


def receipt_item_fingerprint(value: dict[str, Any]) -> str:
    """Hash the sanitized item, not its intentionally lossy UI projection."""

    from app.models.oss_bridge import sanitize_oss_json

    return canonical_sha256(sanitize_oss_json(value))


class MetaWebhookDispatcher:
    """Persist one bounded receipt and typed operational projection per item."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def dispatch(
        self,
        *,
        workspace_id: UUID,
        field: str,
        value: dict[str, Any],
        connection: WhatsAppConnection | None,
        envelope: ProviderEnvelopeContext | None,
    ) -> MetaWebhookReceipt | None:
        contract = META_WEBHOOK_REGISTRY.get(field)
        if contract is None or connection is None or envelope is None:
            return None
        safe_snapshot = _safe_operational_payload(field, value)
        fingerprint = receipt_item_fingerprint(value)
        receipt = await self.db.scalar(
            select(MetaWebhookReceipt).where(
                MetaWebhookReceipt.provider_envelope_id == envelope.id,
                MetaWebhookReceipt.field == field,
                MetaWebhookReceipt.item_fingerprint == fingerprint,
            )
        )
        if receipt is not None and receipt.status == "processed":
            return receipt
        now = _now()
        if receipt is None:
            receipt = MetaWebhookReceipt(
                provider_envelope_id=envelope.id,
                workspace_id=workspace_id,
                connection_id=connection.id,
                field=field,
                item_fingerprint=fingerprint,
                handler_version=contract.handler_version,
                status="received",
                attempt_count=0,
                observed_at=envelope.received_at,
            )
            self.db.add(receipt)
            await self.db.flush()
        receipt.status = "processing"
        receipt.attempt_count += 1
        try:
            async with self.db.begin_nested():
                normalized = META_WEBHOOK_HANDLERS[field](safe_snapshot, contract)
                state = await self._upsert_state(
                    workspace_id=workspace_id,
                    connection_id=connection.id,
                    field=field,
                    normalized=normalized,
                )
                await self._mark_observed_subscription(
                    workspace_id=workspace_id,
                    connection_id=connection.id,
                    field=field,
                    envelope_id=envelope.id,
                )
                await self._ensure_default_policy(workspace_id=workspace_id, field=field)
                if contract.severity in {"critical", "high"}:
                    await self._ensure_work_item(receipt=receipt, state=state)
                receipt.projection_type = contract.resource_type
                receipt.projection_id = state.id
                if contract.native_effect:
                    # Native handlers run in WebhookService after this bounded
                    # control-plane projection. They own the terminal status.
                    receipt.status = "processing"
                    receipt.processed_at = None
                else:
                    receipt.status = "processed"
                    receipt.processed_at = now
                    receipt.sanitized_error_code = None
        except Exception as exc:  # isolate one field without invalidating sibling items
            receipt.status = "failed"
            receipt.sanitized_error_code = type(exc).__name__[:120]
            receipt.processed_at = now
        self.db.add(receipt)
        await self.db.flush()
        return receipt

    async def complete_native(self, receipt: MetaWebhookReceipt | None) -> None:
        if receipt is None:
            return
        contract = META_WEBHOOK_REGISTRY.get(receipt.field)
        if contract is None or not contract.native_effect:
            return
        receipt.status = "processed"
        receipt.processed_at = _now()
        receipt.sanitized_error_code = None
        self.db.add(receipt)
        await self.db.flush()

    async def fail_native(
        self,
        receipt: MetaWebhookReceipt | None,
        exc: Exception,
    ) -> None:
        if receipt is None:
            return
        contract = META_WEBHOOK_REGISTRY.get(receipt.field)
        if contract is None or not contract.native_effect:
            return
        receipt.status = "failed"
        receipt.processed_at = _now()
        receipt.sanitized_error_code = type(exc).__name__[:120]
        self.db.add(receipt)
        await self.db.flush()

    async def _upsert_state(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        field: str,
        normalized: Any,
    ) -> MetaWebhookOperationalState:
        contract = META_WEBHOOK_REGISTRY[field]
        resource_ref = canonical_sha256(
            {
                "field": field,
                "providerRef": normalized.resource_provider_ref or str(connection_id),
            }
        )[:40]
        state = await self.db.scalar(
            select(MetaWebhookOperationalState).where(
                MetaWebhookOperationalState.workspace_id == workspace_id,
                MetaWebhookOperationalState.connection_id == connection_id,
                MetaWebhookOperationalState.field == field,
                MetaWebhookOperationalState.resource_type == contract.resource_type,
                MetaWebhookOperationalState.resource_ref == resource_ref,
            )
        )
        now = _now()
        if state is None:
            state = MetaWebhookOperationalState(
                workspace_id=workspace_id,
                connection_id=connection_id,
                field=field,
                resource_type=contract.resource_type,
                resource_ref=resource_ref,
                state_code=normalized.state_code,
                severity=normalized.severity,
                sanitized_snapshot=normalized.snapshot,
                projected_at=now,
                version=1,
            )
        else:
            state.state_code = normalized.state_code
            state.severity = normalized.severity
            state.sanitized_snapshot = normalized.snapshot
            state.projected_at = now
            state.version += 1
        self.db.add(state)
        await self.db.flush()
        return state

    async def _mark_observed_subscription(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        field: str,
        envelope_id: UUID,
    ) -> None:
        contract = META_WEBHOOK_REGISTRY[field]
        subscription = await self.db.scalar(
            select(MetaWebhookSubscriptionState).where(
                MetaWebhookSubscriptionState.connection_id == connection_id,
                MetaWebhookSubscriptionState.field == field,
            )
        )
        if subscription is None:
            subscription = MetaWebhookSubscriptionState(
                workspace_id=workspace_id,
                connection_id=connection_id,
                field=field,
                desired=True,
                applicability=contract.applicability,
            )
        subscription.provider_state = "subscribed"
        subscription.last_checked_at = _now()
        subscription.sanitized_error_code = None
        subscription.evidence_ref = f"receipt:{envelope_id}"
        self.db.add(subscription)

    async def _ensure_default_policy(self, *, workspace_id: UUID, field: str) -> None:
        policy = await self.db.scalar(
            select(MetaWebhookAutomationPolicy.id).where(
                MetaWebhookAutomationPolicy.workspace_id == workspace_id,
                MetaWebhookAutomationPolicy.field == field,
            )
        )
        if policy is None:
            self.db.add(
                MetaWebhookAutomationPolicy(
                    workspace_id=workspace_id,
                    field=field,
                    enabled=False,
                    mode="observe_only",
                    action_key=META_WEBHOOK_REGISTRY[field].action_key,
                    minimum_severity="warning",
                    version=1,
                )
            )

    async def _ensure_work_item(
        self,
        *,
        receipt: MetaWebhookReceipt,
        state: MetaWebhookOperationalState,
    ) -> None:
        contract = META_WEBHOOK_REGISTRY[receipt.field]
        dedupe_key = f"meta-webhook:{receipt.workspace_id}:{receipt.field}:{state.resource_ref}"
        existing = await self.db.scalar(
            select(PlatformWorkItem).where(PlatformWorkItem.dedupe_key == dedupe_key)
        )
        if existing is not None:
            now = _now()
            existing.source_id = receipt.id
            existing.source_version = str(state.version)
            existing.priority = "P0" if contract.severity == "critical" else "P1"
            existing.safe_title = f"Meta WhatsApp: {contract.destination}"
            existing.safe_summary = contract.operator_action
            existing.href = contract.blackbox_href
            existing.source_updated_at = now
            existing.projected_at = now
            existing.projection_version += 1
            existing.payload_safe = {
                "field": receipt.field,
                "severity": contract.severity,
            }
            self.db.add(existing)
            return
        now = _now()
        self.db.add(
            PlatformWorkItem(
                source_type="meta_webhook",
                source_id=receipt.id,
                source_version=str(state.version),
                dedupe_key=dedupe_key,
                kind="alert",
                state="open",
                priority="P0" if contract.severity == "critical" else "P1",
                workspace_id=receipt.workspace_id,
                safe_title=f"Meta WhatsApp: {contract.destination}",
                safe_summary=contract.operator_action,
                href=contract.blackbox_href,
                required_module="whatsapp",
                required_action="manage",
                allowed_commands=["assign", "investigate"],
                opened_at=now,
                due_at=None,
                source_updated_at=now,
                projected_at=now,
                projection_version=1,
                payload_safe={"field": receipt.field, "severity": contract.severity},
            )
        )
