from __future__ import annotations

from typing import Any

from app.services.whatsapp.webhooks.contracts import MetaWebhookContract, MetaWebhookProjection
from app.services.whatsapp.webhooks.handler_utils import projection


def handle_tracking_events(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="tracking_event_observed")


def handle_user_preferences(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="user_preferences_updated")


HANDLERS = {
    "tracking_events": handle_tracking_events,
    "user_preferences": handle_user_preferences,
}
