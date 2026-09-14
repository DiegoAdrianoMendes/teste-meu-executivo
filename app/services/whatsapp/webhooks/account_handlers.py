from __future__ import annotations

from typing import Any

from app.services.whatsapp.webhooks.contracts import MetaWebhookContract, MetaWebhookProjection
from app.services.whatsapp.webhooks.handler_utils import projection


def handle_account_alerts(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="account_alert_observed")


def handle_account_review_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="account_review_updated")


def handle_account_settings_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="account_settings_updated")


def handle_account_update(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="account_updated")


def handle_automatic_events(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="automatic_event_observed")


def handle_business_capability_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="business_capability_updated")


def handle_business_status_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="business_status_updated")


def handle_business_username_updates(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="business_username_updated")


def handle_calls(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="call_event_observed")


def handle_flows(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="whatsapp_flow_event_observed")


HANDLERS = {
    "account_alerts": handle_account_alerts,
    "account_review_update": handle_account_review_update,
    "account_settings_update": handle_account_settings_update,
    "account_update": handle_account_update,
    "automatic_events": handle_automatic_events,
    "business_capability_update": handle_business_capability_update,
    "business_status_update": handle_business_status_update,
    "business_username_updates": handle_business_username_updates,
    "calls": handle_calls,
    "flows": handle_flows,
}
