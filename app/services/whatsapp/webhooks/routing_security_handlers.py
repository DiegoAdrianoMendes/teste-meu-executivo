from __future__ import annotations

from typing import Any

from app.services.whatsapp.webhooks.contracts import MetaWebhookContract, MetaWebhookProjection
from app.services.whatsapp.webhooks.handler_utils import projection


def handle_messaging_handovers(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="messaging_handover_updated")


def handle_partner_solutions(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="partner_solution_updated")


def handle_payment_configuration_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="payment_configuration_updated")


def handle_phone_number_name_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="phone_number_name_updated")


def handle_phone_number_quality_update(
    v: dict[str, Any], c: MetaWebhookContract
) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="phone_number_quality_updated")


def handle_security(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="security_event_observed")


def handle_smb_app_state_sync(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="smb_app_state_synchronized")


def handle_smb_message_echoes(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="smb_message_echo_observed")


def handle_standby(v: dict[str, Any], c: MetaWebhookContract) -> MetaWebhookProjection:
    return projection(v, c, fallback_state="standby_routing_observed")


HANDLERS = {
    "messaging_handovers": handle_messaging_handovers,
    "partner_solutions": handle_partner_solutions,
    "payment_configuration_update": handle_payment_configuration_update,
    "phone_number_name_update": handle_phone_number_name_update,
    "phone_number_quality_update": handle_phone_number_quality_update,
    "security": handle_security,
    "smb_app_state_sync": handle_smb_app_state_sync,
    "smb_message_echoes": handle_smb_message_echoes,
    "standby": handle_standby,
}
