from __future__ import annotations

from typing import Any

from app.services.whatsapp.webhooks.contracts import MetaWebhookContract, MetaWebhookProjection
from app.services.whatsapp.webhooks.handler_utils import projection


def handle_group_lifecycle_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="group_lifecycle_updated")


def handle_group_participants_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="group_participants_updated")


def handle_group_settings_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="group_settings_updated")


def handle_group_status_update(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="group_status_updated")


HANDLERS = {
    "group_lifecycle_update": handle_group_lifecycle_update,
    "group_participants_update": handle_group_participants_update,
    "group_settings_update": handle_group_settings_update,
    "group_status_update": handle_group_status_update,
}
