from __future__ import annotations

from typing import Any

from app.services.whatsapp.webhooks.contracts import MetaWebhookContract, MetaWebhookProjection
from app.services.whatsapp.webhooks.handler_utils import projection


def handle_history(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="history_batch_observed")


def handle_message_echoes(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="message_echo_observed")


def handle_message_template_components_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="template_components_updated")


def handle_message_template_quality_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="template_quality_updated")


def handle_message_template_status_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="template_status_updated")


def handle_messages(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="message_event_observed")


def handle_template_category_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="template_category_updated")


def handle_template_correct_category_detection(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="template_category_correction_detected")


HANDLERS = {
    "history": handle_history,
    "message_echoes": handle_message_echoes,
    "message_template_components_update": handle_message_template_components_update,
    "message_template_quality_update": handle_message_template_quality_update,
    "message_template_status_update": handle_message_template_status_update,
    "messages": handle_messages,
    "template_category_update": handle_template_category_update,
    "template_correct_category_detection": handle_template_correct_category_detection,
}
