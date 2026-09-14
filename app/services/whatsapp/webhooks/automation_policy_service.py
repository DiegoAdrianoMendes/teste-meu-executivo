"""Versioned, owner-accepted automation policy for Meta webhook operations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meta_webhook_operations import MetaWebhookAutomationPolicy
from app.services.whatsapp.webhooks.registry import META_WEBHOOK_REGISTRY

ALLOWED_AUTOMATION_MODES = frozenset(
    {"observe_only", "suggest_action", "auto_reversible", "approval_each_time"}
)
ALLOWED_SEVERITIES = frozenset({"info", "warning", "high", "critical"})


class MetaWebhookAutomationPolicyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def update(
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
        contract = META_WEBHOOK_REGISTRY.get(field)
        if contract is None:
            raise ValueError("META_WEBHOOK_FIELD_UNKNOWN")
        if mode not in ALLOWED_AUTOMATION_MODES:
            raise ValueError("META_WEBHOOK_AUTOMATION_MODE_INVALID")
        if minimum_severity not in ALLOWED_SEVERITIES:
            raise ValueError("META_WEBHOOK_AUTOMATION_SEVERITY_INVALID")
        if enabled and contract.severity in {"critical", "high"} and mode != "approval_each_time":
            raise ValueError("META_WEBHOOK_HUMAN_APPROVAL_REQUIRED")
        policy = await self.db.scalar(
            select(MetaWebhookAutomationPolicy)
            .where(
                MetaWebhookAutomationPolicy.workspace_id == workspace_id,
                MetaWebhookAutomationPolicy.field == field,
            )
            .with_for_update()
        )
        if policy is None or policy.version != expected_version:
            raise ValueError("META_WEBHOOK_POLICY_VERSION_CONFLICT")
        accepted = enabled and mode != "observe_only"
        policy.enabled = enabled
        policy.mode = mode
        policy.minimum_severity = minimum_severity
        policy.owner_accepted_at = datetime.now(UTC) if accepted else None
        policy.owner_accepted_by = actor_id if accepted else None
        policy.version += 1
        self.db.add(policy)
        await self.db.flush()
        return policy
