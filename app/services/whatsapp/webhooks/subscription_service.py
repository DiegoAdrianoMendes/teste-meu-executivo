from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meta_webhook_operations import (
    MetaWebhookAutomationPolicy,
    MetaWebhookOperationalState,
    MetaWebhookReceipt,
    MetaWebhookSubscriptionJob,
    MetaWebhookSubscriptionState,
)
from app.models.omnichannel_v6 import OmnichannelProviderWebhookEnvelope
from app.models.whatsapp import WhatsAppConnection
from app.services.message_evidence_service import ProviderEnvelopeContext
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import meta_request_scope
from app.services.whatsapp.webhooks.automation_policy_service import (
    MetaWebhookAutomationPolicyService,
)
from app.services.whatsapp.webhooks.dispatcher import (
    MetaWebhookDispatcher,
    receipt_item_fingerprint,
)
from app.services.whatsapp.webhooks.registry import META_WEBHOOK_REGISTRY


def _now() -> datetime:
    return datetime.now(UTC)


def _is_transient_provider_error(exc: WhatsAppProviderError) -> bool:
    """Retry only provider/network failures that can safely succeed later."""
    provider_status = exc.provider_status_code
    return bool(
        exc.http_status == 429
        or provider_status == 429
        or (provider_status is not None and provider_status >= 500)
        or (provider_status is None and exc.status_code >= 500 and not exc.connection_unhealthy)
    )


class MetaWebhookControlPlaneService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ensure_inventory(self, *, workspace_id: UUID) -> None:
        connections = list(
            (
                await self.db.scalars(
                    select(WhatsAppConnection).where(
                        WhatsAppConnection.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        existing_subscriptions = {
            (item.connection_id, item.field): item
            for item in (
                await self.db.scalars(
                    select(MetaWebhookSubscriptionState).where(
                        MetaWebhookSubscriptionState.workspace_id == workspace_id
                    )
                )
            ).all()
        }
        existing_policies = {
            item.field: item
            for item in (
                await self.db.scalars(
                    select(MetaWebhookAutomationPolicy).where(
                        MetaWebhookAutomationPolicy.workspace_id == workspace_id
                    )
                )
            ).all()
        }
        for field, contract in META_WEBHOOK_REGISTRY.items():
            if field not in existing_policies:
                self.db.add(
                    MetaWebhookAutomationPolicy(
                        workspace_id=workspace_id,
                        field=field,
                        enabled=False,
                        mode="observe_only",
                        action_key=contract.action_key,
                        minimum_severity="warning",
                        version=1,
                    )
                )
            for connection in connections:
                existing = existing_subscriptions.get((connection.id, field))
                if existing is not None:
                    if not existing.desired:
                        existing.desired = True
                        self.db.add(existing)
                    continue
                self.db.add(
                    MetaWebhookSubscriptionState(
                        workspace_id=workspace_id,
                        connection_id=connection.id,
                        field=field,
                        desired=True,
                        applicability=contract.applicability,
                        provider_state=(
                            "manual_required"
                            if contract.applicability == "manual_only"
                            else "unknown"
                        ),
                    )
                )
        await self.db.flush()

    async def operations(self, *, workspace_id: UUID, field: str | None = None) -> dict[str, Any]:
        await self.ensure_inventory(workspace_id=workspace_id)
        state_query = select(MetaWebhookOperationalState).where(
            MetaWebhookOperationalState.workspace_id == workspace_id
        )
        receipt_query = select(MetaWebhookReceipt).where(
            MetaWebhookReceipt.workspace_id == workspace_id
        )
        subscription_query = select(MetaWebhookSubscriptionState).where(
            MetaWebhookSubscriptionState.workspace_id == workspace_id
        )
        policy_query = select(MetaWebhookAutomationPolicy).where(
            MetaWebhookAutomationPolicy.workspace_id == workspace_id
        )
        if field:
            if field not in META_WEBHOOK_REGISTRY:
                raise ValueError("META_WEBHOOK_FIELD_UNKNOWN")
            state_query = state_query.where(MetaWebhookOperationalState.field == field)
            receipt_query = receipt_query.where(MetaWebhookReceipt.field == field)
            subscription_query = subscription_query.where(
                MetaWebhookSubscriptionState.field == field
            )
            policy_query = policy_query.where(MetaWebhookAutomationPolicy.field == field)
        states = list(
            (
                await self.db.scalars(
                    state_query.order_by(desc(MetaWebhookOperationalState.projected_at))
                )
            ).all()
        )
        receipts = list(
            (
                await self.db.scalars(
                    receipt_query.order_by(desc(MetaWebhookReceipt.observed_at)).limit(200)
                )
            ).all()
        )
        subscriptions = list((await self.db.scalars(subscription_query)).all())
        policies = list((await self.db.scalars(policy_query)).all())
        latest_state: dict[str, MetaWebhookOperationalState] = {}
        for state in states:
            latest_state.setdefault(state.field, state)
        subscriptions_by_field: dict[str, list[MetaWebhookSubscriptionState]] = {}
        for subscription in subscriptions:
            subscriptions_by_field.setdefault(subscription.field, []).append(subscription)
        policies_by_field = {policy.field: policy for policy in policies}
        receipt_counts: dict[str, dict[str, int]] = {}
        for receipt in receipts:
            counts = receipt_counts.setdefault(receipt.field, {})
            counts[receipt.status] = counts.get(receipt.status, 0) + 1
        catalog = []
        for name, contract in META_WEBHOOK_REGISTRY.items():
            if field and name != field:
                continue
            state = latest_state.get(name)
            field_subscriptions = subscriptions_by_field.get(name, [])
            provider_states = sorted({item.provider_state for item in field_subscriptions})
            policy = policies_by_field.get(name)
            catalog.append(
                {
                    "field": name,
                    "category": contract.category,
                    "severity": contract.severity,
                    "implementation": "native" if contract.native_effect else "domain_projection",
                    "recommended": True,
                    "sprint": contract.sprint,
                    "destination": contract.destination,
                    "description": contract.description,
                    "operatorAction": contract.operator_action,
                    "resourceType": contract.resource_type,
                    "appHref": contract.app_href,
                    "blackboxHref": contract.blackbox_href,
                    "observationState": "observed" if state else "not_observed",
                    "stateCode": state.state_code if state else None,
                    "lastObservedAt": state.projected_at if state else None,
                    "lastSummary": state.sanitized_snapshot if state else None,
                    "eventCount": sum(receipt_counts.get(name, {}).values()),
                    "receiptCounts": receipt_counts.get(name, {}),
                    # The registry is the source of desired coverage.  An empty
                    # connection inventory must not turn all([]) into provider
                    # evidence or silently flip the product intent.
                    "desiredSubscription": True,
                    "subscriptionCount": len(field_subscriptions),
                    "applicability": contract.applicability,
                    "providerStates": provider_states or ["unknown"],
                    "automation": {
                        "enabled": bool(policy.enabled) if policy else False,
                        "mode": policy.mode if policy else "observe_only",
                        "actionKey": policy.action_key if policy else contract.action_key,
                        "version": policy.version if policy else 1,
                    },
                }
            )
        return {
            "catalog": catalog,
            "receipts": [self.serialize_receipt(item) for item in receipts],
            "receiptCount": len(receipts),
            "latestReceiptAt": receipts[0].observed_at if receipts else None,
        }

    async def platform_operations(
        self, *, field: str | None = None, workspace_id: UUID | None = None
    ) -> dict[str, Any]:
        """Return the cross-workspace sanitized control-plane view for Blackbox root."""
        if field and field not in META_WEBHOOK_REGISTRY:
            raise ValueError("META_WEBHOOK_FIELD_UNKNOWN")
        state_query = select(MetaWebhookOperationalState)
        receipt_query = select(MetaWebhookReceipt)
        subscription_query = select(MetaWebhookSubscriptionState)
        policy_query = select(MetaWebhookAutomationPolicy)
        if workspace_id is not None:
            state_query = state_query.where(
                MetaWebhookOperationalState.workspace_id == workspace_id
            )
            receipt_query = receipt_query.where(MetaWebhookReceipt.workspace_id == workspace_id)
            subscription_query = subscription_query.where(
                MetaWebhookSubscriptionState.workspace_id == workspace_id
            )
            policy_query = policy_query.where(
                MetaWebhookAutomationPolicy.workspace_id == workspace_id
            )
        if field:
            state_query = state_query.where(MetaWebhookOperationalState.field == field)
            receipt_query = receipt_query.where(MetaWebhookReceipt.field == field)
            subscription_query = subscription_query.where(
                MetaWebhookSubscriptionState.field == field
            )
            policy_query = policy_query.where(MetaWebhookAutomationPolicy.field == field)
        states = list(
            (
                await self.db.scalars(
                    state_query.order_by(desc(MetaWebhookOperationalState.projected_at)).limit(500)
                )
            ).all()
        )
        receipts = list(
            (
                await self.db.scalars(
                    receipt_query.order_by(desc(MetaWebhookReceipt.observed_at)).limit(500)
                )
            ).all()
        )
        subscriptions = list((await self.db.scalars(subscription_query)).all())
        policies = list((await self.db.scalars(policy_query)).all()) if workspace_id else []
        latest_state: dict[str, MetaWebhookOperationalState] = {}
        for state in states:
            latest_state.setdefault(state.field, state)
        receipt_counts: dict[str, dict[str, int]] = {}
        for receipt in receipts:
            counts = receipt_counts.setdefault(receipt.field, {})
            counts[receipt.status] = counts.get(receipt.status, 0) + 1
        subscriptions_by_field: dict[str, list[MetaWebhookSubscriptionState]] = {}
        for subscription in subscriptions:
            subscriptions_by_field.setdefault(subscription.field, []).append(subscription)
        policies_by_field = {policy.field: policy for policy in policies}
        catalog: list[dict[str, Any]] = []
        for name, contract in META_WEBHOOK_REGISTRY.items():
            if field and name != field:
                continue
            state = latest_state.get(name)
            rows = subscriptions_by_field.get(name, [])
            policy = policies_by_field.get(name)
            catalog.append(
                {
                    "field": name,
                    "category": contract.category,
                    "severity": contract.severity,
                    "implementation": "native" if contract.native_effect else "domain_projection",
                    "recommended": True,
                    "sprint": contract.sprint,
                    "destination": contract.destination,
                    "description": contract.description,
                    "operatorAction": contract.operator_action,
                    "resourceType": contract.resource_type,
                    "appHref": contract.app_href,
                    "blackboxHref": contract.blackbox_href,
                    "observationState": "observed" if state else "not_observed",
                    "stateCode": state.state_code if state else None,
                    "lastObservedAt": state.projected_at if state else None,
                    "lastSummary": state.sanitized_snapshot if state else None,
                    "eventCount": sum(receipt_counts.get(name, {}).values()),
                    "receiptCounts": receipt_counts.get(name, {}),
                    "desiredSubscription": True,
                    "subscriptionCount": len(rows),
                    "applicability": contract.applicability,
                    "providerStates": sorted({row.provider_state for row in rows}) or ["unknown"],
                    "automation": {
                        "enabled": bool(policy.enabled) if policy else False,
                        "mode": policy.mode if policy else "observe_only",
                        "actionKey": policy.action_key if policy else contract.action_key,
                        "version": policy.version if policy else 1,
                    },
                }
            )
        return {
            "catalog": catalog,
            "receipts": [self.serialize_receipt(item) for item in receipts],
            "receiptCount": len(receipts),
            "latestReceiptAt": receipts[0].observed_at if receipts else None,
        }

    async def update_policy(
        self,
        *,
        workspace_id: UUID,
        field: str,
        enabled: bool,
        mode: str,
        minimum_severity: str,
        expected_version: int,
        actor_id: UUID,
    ) -> MetaWebhookAutomationPolicy:
        await self.ensure_inventory(workspace_id=workspace_id)
        return await MetaWebhookAutomationPolicyService(self.db).update(
            workspace_id=workspace_id,
            field=field,
            enabled=enabled,
            mode=mode,
            minimum_severity=minimum_severity,
            expected_version=expected_version,
            actor_id=actor_id,
        )

    async def preview_reconciliation(self, *, workspace_id: UUID) -> dict[str, Any]:
        await self.ensure_inventory(workspace_id=workspace_id)
        rows = list(
            (
                await self.db.scalars(
                    select(MetaWebhookSubscriptionState).where(
                        MetaWebhookSubscriptionState.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        return {
            "connectionCount": len({row.connection_id for row in rows}),
            "fieldCount": len(META_WEBHOOK_REGISTRY),
            "changes": [
                {
                    "connectionId": row.connection_id,
                    "field": row.field,
                    "desired": row.desired,
                    "providerState": row.provider_state,
                    "applicability": row.applicability,
                    "action": (
                        "manual_review"
                        if row.applicability == "manual_only"
                        else "subscribe_app"
                        if row.provider_state not in {"subscribed", "not_eligible"}
                        else "none"
                    ),
                }
                for row in rows
            ],
        }

    async def reconcile(self, *, workspace_id: UUID) -> dict[str, Any]:
        await self.ensure_inventory(workspace_id=workspace_id)
        connection_service = WhatsAppConnectionService(self.db)
        connections = list(
            (
                await self.db.scalars(
                    select(WhatsAppConnection).where(
                        WhatsAppConnection.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        attempted = 0
        errors: list[dict[str, str | bool]] = []
        for connection in connections:
            waba_id = connection.waba_id or connection.business_account_id
            if not waba_id:
                await self._mark_connection_error(
                    connection.id, "not_eligible", "META_WABA_MISSING"
                )
                continue
            try:
                token = connection_service.get_meta_access_token(connection)
                async with meta_request_scope(connection_service.meta_client):
                    await connection_service.meta_client.subscribe_app(waba_id, token)
                attempted += 1
                await self._mark_connection_attempt(connection.id, succeeded=True)
            except WhatsAppProviderError as exc:
                attempted += 1
                code = str(exc.code or "META_SUBSCRIPTION_REJECTED")[:120]
                retryable = _is_transient_provider_error(exc)
                await self._mark_connection_error(
                    connection.id,
                    "error" if retryable else "rejected",
                    code,
                    retryable=retryable,
                )
                errors.append(
                    {
                        "connectionRef": str(connection.id),
                        "code": code,
                        "retryable": retryable,
                    }
                )
        await self.db.flush()
        return {
            "status": "completed_with_errors" if errors else "completed",
            "connectionCount": len(connections),
            "attempted": attempted,
            "errors": errors,
            "retryableCount": sum(1 for error in errors if error["retryable"]),
            "note": (
                "A assinatura do app na WABA não prova cada campo; "
                "eventos observados continuam sendo a evidência por campo."
            ),
        }

    async def create_reconciliation_job(
        self, *, workspace_id: UUID, requested_by_user_id: UUID
    ) -> MetaWebhookSubscriptionJob:
        job = MetaWebhookSubscriptionJob(
            workspace_id=workspace_id,
            requested_by_user_id=requested_by_user_id,
            status="queued",
        )
        self.db.add(job)
        await self.db.flush()
        return job

    async def get_reconciliation_job(
        self, *, workspace_id: UUID, job_id: UUID
    ) -> MetaWebhookSubscriptionJob | None:
        return await self.db.scalar(
            select(MetaWebhookSubscriptionJob).where(
                MetaWebhookSubscriptionJob.workspace_id == workspace_id,
                MetaWebhookSubscriptionJob.id == job_id,
            )
        )

    async def run_reconciliation_job(
        self, *, workspace_id: UUID, job_id: UUID
    ) -> MetaWebhookSubscriptionJob:
        job = await self.db.scalar(
            select(MetaWebhookSubscriptionJob)
            .where(
                MetaWebhookSubscriptionJob.workspace_id == workspace_id,
                MetaWebhookSubscriptionJob.id == job_id,
            )
            .with_for_update()
        )
        if job is None:
            raise ValueError("META_WEBHOOK_SUBSCRIPTION_JOB_NOT_FOUND")
        if job.status in {"completed", "completed_with_errors"}:
            return job
        job.status = "running"
        job.started_at = _now()
        self.db.add(job)
        await self.db.flush()
        result = await self.reconcile(workspace_id=workspace_id)
        job.attempted_count = int(result["attempted"])
        job.failed_count = len(result["errors"])
        job.succeeded_count = max(0, job.attempted_count - job.failed_count)
        job.result_summary = {
            "connectionCount": result["connectionCount"],
            "attempted": result["attempted"],
            "errors": result["errors"],
            "retryableCount": result["retryableCount"],
            "note": result["note"],
        }
        job.status = "retrying" if result["retryableCount"] else str(result["status"])
        job.completed_at = None if result["retryableCount"] else _now()
        self.db.add(job)
        await self.db.flush()
        return job

    async def retry_receipt(self, *, workspace_id: UUID, receipt_id: UUID) -> MetaWebhookReceipt:
        receipt = await self.db.scalar(
            select(MetaWebhookReceipt)
            .where(
                MetaWebhookReceipt.workspace_id == workspace_id,
                MetaWebhookReceipt.id == receipt_id,
            )
            .with_for_update()
        )
        if receipt is None:
            raise ValueError("META_WEBHOOK_RECEIPT_NOT_FOUND")
        if receipt.status == "processed":
            return receipt
        if receipt.attempt_count >= 3:
            raise ValueError("META_WEBHOOK_RECEIPT_RETRY_EXHAUSTED")
        if META_WEBHOOK_REGISTRY[receipt.field].native_effect:
            # Native mutations are replayed only through the durable provider
            # envelope worker, which owns their full transaction/idempotency.
            # Replaying only this projection would produce a false success.
            raise ValueError("META_WEBHOOK_NATIVE_RETRY_MANAGED_BY_ENVELOPE")
        envelope = await self.db.get(
            OmnichannelProviderWebhookEnvelope, receipt.provider_envelope_id
        )
        connection = await self.db.scalar(
            select(WhatsAppConnection).where(
                WhatsAppConnection.workspace_id == workspace_id,
                WhatsAppConnection.id == receipt.connection_id,
            )
        )
        if envelope is None or connection is None or not envelope.signature_verified:
            raise ValueError("META_WEBHOOK_RECEIPT_EVIDENCE_UNAVAILABLE")
        try:
            payload = json.loads(bytes(envelope.raw_payload).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("META_WEBHOOK_RECEIPT_PAYLOAD_INVALID") from exc
        candidate: dict[str, Any] | None = None
        for entry in payload.get("entry", []) if isinstance(payload, dict) else []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes", []):
                if not isinstance(change, dict) or change.get("field") != receipt.field:
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                if receipt_item_fingerprint(value) == receipt.item_fingerprint:
                    candidate = value
                    break
            if candidate is not None:
                break
        if candidate is None:
            raise ValueError("META_WEBHOOK_RECEIPT_ITEM_NOT_FOUND")
        receipt.status = "retrying"
        self.db.add(receipt)
        await self.db.flush()
        envelope_context = ProviderEnvelopeContext(
            id=envelope.id,
            payload_sha256=envelope.payload_sha256,
            signature_sha256=envelope.signature_sha256,
            received_at=envelope.received_at,
        )
        dispatched = await MetaWebhookDispatcher(self.db).dispatch(
            workspace_id=workspace_id,
            field=receipt.field,
            value=candidate,
            connection=connection,
            envelope=envelope_context,
        )
        if dispatched is None:
            raise ValueError("META_WEBHOOK_RECEIPT_RETRY_UNAVAILABLE")
        return dispatched

    @staticmethod
    def serialize_job(item: MetaWebhookSubscriptionJob) -> dict[str, Any]:
        return {
            "id": item.id,
            "workspaceId": item.workspace_id,
            "status": item.status,
            "attemptedCount": item.attempted_count,
            "succeededCount": item.succeeded_count,
            "failedCount": item.failed_count,
            "resultSummary": item.result_summary,
            "sanitizedErrorCode": item.sanitized_error_code,
            "startedAt": item.started_at,
            "completedAt": item.completed_at,
            "createdAt": item.created_at,
        }

    async def _mark_connection_attempt(self, connection_id: UUID, *, succeeded: bool) -> None:
        rows = list(
            (
                await self.db.scalars(
                    select(MetaWebhookSubscriptionState).where(
                        MetaWebhookSubscriptionState.connection_id == connection_id
                    )
                )
            ).all()
        )
        now = _now()
        for row in rows:
            row.last_attempt_at = now
            row.last_checked_at = now
            row.attempt_count = min(row.attempt_count + 1, 3)
            if succeeded and row.provider_state != "subscribed":
                row.provider_state = (
                    "manual_required" if row.applicability == "manual_only" else "unknown"
                )
            row.sanitized_error_code = None if succeeded else row.sanitized_error_code
            row.next_retry_at = None
            self.db.add(row)

    async def _mark_connection_error(
        self,
        connection_id: UUID,
        state: str,
        code: str,
        *,
        retryable: bool = False,
    ) -> None:
        rows = list(
            (
                await self.db.scalars(
                    select(MetaWebhookSubscriptionState).where(
                        MetaWebhookSubscriptionState.connection_id == connection_id
                    )
                )
            ).all()
        )
        now = _now()
        for row in rows:
            row.provider_state = state
            row.last_attempt_at = now
            row.last_checked_at = now
            row.attempt_count = min(row.attempt_count + 1, 3)
            row.sanitized_error_code = code
            row.next_retry_at = (
                now + timedelta(seconds=min(300, 30 * (2 ** max(0, row.attempt_count - 1))))
                if retryable and row.attempt_count < 3
                else None
            )
            self.db.add(row)

    @staticmethod
    def serialize_receipt(item: MetaWebhookReceipt) -> dict[str, Any]:
        native_effect = META_WEBHOOK_REGISTRY[item.field].native_effect
        return {
            "id": item.id,
            "workspaceId": item.workspace_id,
            "connectionId": item.connection_id,
            "field": item.field,
            "status": item.status,
            "attemptCount": item.attempt_count,
            "projectionType": item.projection_type,
            "sanitizedErrorCode": item.sanitized_error_code,
            "observedAt": item.observed_at,
            "processedAt": item.processed_at,
            "retryAvailable": bool(
                not native_effect
                and item.status == "failed"
                and item.attempt_count < 3
            ),
            "retryManagedBy": "provider_envelope" if native_effect else "receipt",
        }
