from __future__ import annotations

from typing import Any

from app.services.whatsapp.webhooks.contracts import MetaWebhookContract, MetaWebhookProjection

STATE_KEYS = (
    "event",
    "event_type",
    "status",
    "state",
    "quality_rating",
    "reason",
    "action",
)
RESOURCE_KEYS = (
    "id",
    "message_id",
    "message_template_id",
    "template_id",
    "flow_id",
    "call_id",
    "group_id",
    "phone_number_id",
    "business_account_id",
    "waba_id",
)


def projection(
    value: dict[str, Any],
    contract: MetaWebhookContract,
    *,
    fallback_state: str,
    severity: str | None = None,
) -> MetaWebhookProjection:
    state_code = next(
        (
            str(value.get(key) or "").strip()[:120]
            for key in STATE_KEYS
            if str(value.get(key) or "").strip()
        ),
        fallback_state,
    )
    provider_ref = next(
        (
            str(value.get(key) or "").strip()[:255]
            for key in RESOURCE_KEYS
            if str(value.get(key) or "").strip()
        ),
        None,
    )
    return MetaWebhookProjection(
        state_code=state_code,
        severity=severity or contract.severity,
        resource_type=contract.resource_type,
        resource_provider_ref=provider_ref,
        snapshot=value,
        action_key=contract.action_key,
    )
