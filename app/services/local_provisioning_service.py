"""Declarative, idempotent provisioning that never leaves the local process."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.configuration import ConfigDocument, ConfigScope
from app.models.klm_operations import ExternalActionIntent, ExternalActionIntentStatus
from app.schemas.local_provisioning import (
    LocalProvisioningCommandResult,
    LocalProvisioningPlanRead,
    LocalProvisioningResourceRead,
    LocalProvisioningStateRead,
)
from app.services.configuration_service import ConfigurationService

LOCAL_PROVISIONING_NAMESPACE = "provisioning.local"
LOCAL_PROVISIONING_PROVIDER = "klm_fake_local_provisioner"


class LocalProvisioningConflictError(RuntimeError):
    """Raised when an idempotency key is reused for another command."""


@dataclass(frozen=True)
class DesiredResource:
    provider: str
    resource_type: str
    logical_key: str
    desired: dict[str, Any]

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.resource_type}:{self.logical_key}"

    @property
    def desired_hash(self) -> str:
        return _digest(self.desired)


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LocalProvisioningService:
    """Compile desired local resources and reconcile them through fake adapters."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.config = ConfigurationService(db)

    @staticmethod
    def desired_resources(workspace_id: UUID) -> tuple[DesiredResource, ...]:
        workspace = str(workspace_id)
        return (
            DesiredResource(
                "klm",
                "workspace",
                "canonical",
                {
                    "workspaceId": workspace,
                    "sourceOfTruth": "klm",
                    "tenantIsolation": "workspace",
                },
            ),
            DesiredResource(
                "klm",
                "storage",
                "documents",
                {
                    "workspaceId": workspace,
                    "backend": "local_s3_compatible",
                    "quarantine": True,
                    "versioning": True,
                },
            ),
            DesiredResource(
                "chatwoot",
                "account",
                "workspace",
                {"workspaceId": workspace, "mode": "local_only", "canonicalContacts": "klm"},
            ),
            DesiredResource(
                "chatwoot",
                "inbox",
                "commercial",
                {"workspaceId": workspace, "channel": "fake", "handoff": True},
            ),
            DesiredResource(
                "rocketchat",
                "workspace",
                "internal",
                {"workspaceId": workspace, "roomIsolation": "matter", "mode": "local_only"},
            ),
            DesiredResource(
                "chattermate",
                "organization",
                "workspace",
                {"workspaceId": workspace, "runtime": "klm_gateway", "externalModels": False},
            ),
            DesiredResource(
                "chattermate",
                "agent_group",
                "legal_health",
                {"workspaceId": workspace, "area": "health_insurance_denials", "mode": "fake"},
            ),
            DesiredResource(
                "bottlecrm",
                "organization",
                "workspace",
                {"workspaceId": workspace, "canonicalCrm": "klm", "sync": "local_shadow"},
            ),
            DesiredResource(
                "bottlecrm",
                "pipeline",
                "health_denials",
                {"workspaceId": workspace, "area": "health_insurance_denials", "source": "klm"},
            ),
            DesiredResource(
                "embeddedchat",
                "client_contract",
                "internal_chat",
                {"workspaceId": workspace, "renderer": "klm_native", "licenseMode": "behavioral"},
            ),
        )

    async def preview(self, workspace_id: UUID) -> LocalProvisioningPlanRead:
        document = await self._document(workspace_id)
        observed = self._observed(document)
        resources: list[LocalProvisioningResourceRead] = []
        for desired in self.desired_resources(workspace_id):
            current = observed.get(desired.key, {})
            observed_hash = current.get("observedHash")
            if observed_hash is None:
                action: Literal["create", "update", "noop"] = "create"
                status: Literal["pending", "drift", "ready"] = "pending"
            elif observed_hash != desired.desired_hash:
                action = "update"
                status = "drift"
            else:
                action = "noop"
                status = "ready"
            resources.append(
                LocalProvisioningResourceRead(
                    key=desired.key,
                    provider=desired.provider,
                    resource_type=desired.resource_type,
                    logical_key=desired.logical_key,
                    action=action,
                    status=status,
                    desired_hash=desired.desired_hash,
                    observed_hash=observed_hash,
                )
            )

        plan_material = [
            {
                "key": item.key,
                "desiredHash": item.desired_hash,
                "adapter": item.adapter,
            }
            for item in resources
        ]
        creates = sum(item.action == "create" for item in resources)
        updates = sum(item.action == "update" for item in resources)
        noops = sum(item.action == "noop" for item in resources)
        return LocalProvisioningPlanRead(
            workspace_id=workspace_id,
            digest=_digest(plan_material),
            generated_at=_now(),
            resources=resources,
            creates=creates,
            updates=updates,
            noops=noops,
            ready=creates == 0 and updates == 0,
        )

    async def state(self, workspace_id: UUID) -> LocalProvisioningStateRead:
        document = await self._document(workspace_id)
        plan = await self.preview(workspace_id)
        payload = document.payload if document else {}
        return LocalProvisioningStateRead(
            workspace_id=workspace_id,
            configured=document is not None,
            document_version=document.current_version if document else None,
            etag=document.etag if document else None,
            desired_digest=str(payload.get("desiredDigest") or plan.digest),
            last_command=payload.get("lastCommand"),
            last_run_at=payload.get("lastRunAt"),
            last_actor_user_id=payload.get("lastActorUserId"),
            resources=plan.resources,
            ready=plan.ready,
        )

    async def apply(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        idempotency_key: str,
        reason: str | None = None,
    ) -> LocalProvisioningCommandResult:
        return await self._run(
            action="apply",
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    async def reconcile(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        idempotency_key: str,
        reason: str | None = None,
    ) -> LocalProvisioningCommandResult:
        return await self._run(
            action="reconcile",
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    async def _run(
        self,
        *,
        action: Literal["apply", "reconcile"],
        workspace_id: UUID,
        actor_user_id: UUID,
        idempotency_key: str,
        reason: str | None,
    ) -> LocalProvisioningCommandResult:
        key = idempotency_key.strip()
        existing = await self._intent(workspace_id, key)
        if existing is not None:
            return await self._replay(action, workspace_id, existing)

        plan = await self.preview(workspace_id)
        intent = ExternalActionIntent(
            workspace_id=workspace_id,
            requested_by_user_id=actor_user_id,
            action_type=f"local_provisioning.{action}",
            provider=LOCAL_PROVISIONING_PROVIDER,
            idempotency_key=key,
            status=ExternalActionIntentStatus.approved_local,
            payload={
                "planDigest": plan.digest,
                "creates": plan.creates,
                "updates": plan.updates,
                "noops": plan.noops,
                "externalEffects": 0,
            },
            reason=(reason or f"Local provisioning {action}")[:240],
        )
        try:
            async with self.db.begin_nested():
                self.db.add(intent)
                await self.db.flush()
        except IntegrityError:
            existing = await self._intent(workspace_id, key)
            if existing is None:
                raise
            return await self._replay(action, workspace_id, existing)

        now = _now()
        resources = {
            desired.key: {
                "provider": desired.provider,
                "resourceType": desired.resource_type,
                "logicalKey": desired.logical_key,
                "desiredHash": desired.desired_hash,
                "observedHash": desired.desired_hash,
                "status": "ready",
                "adapter": "fake_local",
                "externalEffects": 0,
                "lastReconciledAt": now.isoformat(),
            }
            for desired in self.desired_resources(workspace_id)
        }
        current = await self._document(workspace_id)
        document = await self.config.write_tenant(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=LOCAL_PROVISIONING_NAMESPACE,
            payload={
                "schemaVersion": "1.0",
                "desiredDigest": plan.digest,
                "adapterMode": "fake_local",
                "externalEffects": 0,
                "resources": resources,
                "lastCommand": action,
                "lastRunAt": now.isoformat(),
                "lastActorUserId": str(actor_user_id),
                "lastIntentId": str(intent.id),
            },
            actor_user_id=actor_user_id,
            expected_etag=current.etag if current else None,
            reason=(reason or f"Local provisioning {action}")[:240],
        )
        await self.db.flush()
        settled_plan = await self.preview(workspace_id)
        return LocalProvisioningCommandResult(
            action=action,
            replayed=False,
            intent_id=intent.id,
            plan=settled_plan,
            state=self._state_from(document, settled_plan),
        )

    async def _replay(
        self,
        action: Literal["apply", "reconcile"],
        workspace_id: UUID,
        intent: ExternalActionIntent,
    ) -> LocalProvisioningCommandResult:
        expected_action = f"local_provisioning.{action}"
        if intent.action_type != expected_action:
            raise LocalProvisioningConflictError(
                "The idempotency key belongs to a different provisioning command."
            )
        plan = await self.preview(workspace_id)
        document = await self._document(workspace_id)
        return LocalProvisioningCommandResult(
            action=action,
            replayed=True,
            intent_id=intent.id,
            plan=plan,
            state=self._state_from(document, plan),
        )

    def _state_from(
        self,
        document: ConfigDocument | None,
        plan: LocalProvisioningPlanRead,
    ) -> LocalProvisioningStateRead:
        payload = document.payload if document else {}
        return LocalProvisioningStateRead(
            workspace_id=plan.workspace_id,
            configured=document is not None,
            document_version=document.current_version if document else None,
            etag=document.etag if document else None,
            desired_digest=str(payload.get("desiredDigest") or plan.digest),
            last_command=payload.get("lastCommand"),
            last_run_at=payload.get("lastRunAt"),
            last_actor_user_id=payload.get("lastActorUserId"),
            resources=plan.resources,
            ready=plan.ready,
        )

    async def _document(self, workspace_id: UUID) -> ConfigDocument | None:
        return await self.config.get_tenant_document(
            workspace_id=workspace_id,
            scope=ConfigScope.workspace,
            namespace=LOCAL_PROVISIONING_NAMESPACE,
        )

    @staticmethod
    def _observed(document: ConfigDocument | None) -> dict[str, dict[str, Any]]:
        if document is None:
            return {}
        resources = document.payload.get("resources", {})
        if not isinstance(resources, dict):
            return {}
        return {
            str(key): value
            for key, value in resources.items()
            if isinstance(value, dict)
        }

    async def _intent(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> ExternalActionIntent | None:
        return await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == workspace_id,
                ExternalActionIntent.idempotency_key == idempotency_key,
            )
        )
