from __future__ import annotations

from app.services.whatsapp.meta_webhook_operations_service import (
    META_WEBHOOK_FIELD_CATALOG,
    META_WEBHOOK_SEMANTIC_CONTRACTS,
    META_WEBHOOK_SPRINTS,
)
from app.services.whatsapp.webhooks.account_handlers import HANDLERS as ACCOUNT_HANDLERS
from app.services.whatsapp.webhooks.contracts import MetaWebhookContract, MetaWebhookHandler
from app.services.whatsapp.webhooks.group_handlers import HANDLERS as GROUP_HANDLERS
from app.services.whatsapp.webhooks.message_template_handlers import (
    HANDLERS as MESSAGE_TEMPLATE_HANDLERS,
)
from app.services.whatsapp.webhooks.routing_security_handlers import (
    HANDLERS as ROUTING_SECURITY_HANDLERS,
)
from app.services.whatsapp.webhooks.tracking_preferences_handlers import (
    HANDLERS as TRACKING_PREFERENCES_HANDLERS,
)

_CATEGORY_DESTINATIONS = {
    "messages": ("conversation", "/app/conversas"),
    "coexistence": ("coexistence", "/app/preferencias?origem=meta"),
    "templates": ("message_template", "/whatsapp/meta-templates"),
    "health": ("whatsapp_health", "/app/conexoes/whatsapp/webhooks"),
    "security": ("security_event", "/app/atividade"),
    "routing": ("conversation_routing", "/app/conversas"),
    "flows": ("whatsapp_flow", "/app/flows?origem=meta"),
    "calls": ("omnichannel_call", "/app/conversas?tab=calls"),
    "groups": ("whatsapp_group", "/app/grupos"),
    "payments": ("payment_capability", "/app/conexoes?foco=pagamentos-whatsapp"),
    "partner": ("partner_solution", "/app/conexoes"),
    "automation": ("meta_automatic_event", "/app/automacoes"),
    "tracking": ("tracking_event", "/app/rastreamento?tab=eventos&origem=meta"),
}

_ASSET_DEPENDENT = frozenset(
    {
        "calls",
        "flows",
        "group_lifecycle_update",
        "group_participants_update",
        "group_settings_update",
        "group_status_update",
        "payment_configuration_update",
    }
)
_MANUAL_ONLY = frozenset({"history", "message_echoes", "smb_app_state_sync", "smb_message_echoes"})
_ACTION_KEYS = {
    "account_alerts": "investigate_meta_account",
    "business_status_update": "open_meta_incident",
    "message_template_quality_update": "review_template_quality",
    "phone_number_quality_update": "review_phone_quality",
    "payment_configuration_update": "review_payment_configuration",
    "security": "investigate_meta_security",
}


def _build_registry() -> dict[str, MetaWebhookContract]:
    sprint_by_field = {
        field: sprint
        for sprint, fields in enumerate(META_WEBHOOK_SPRINTS, start=1)
        for field in fields
    }
    registry: dict[str, MetaWebhookContract] = {}
    for field, config in META_WEBHOOK_FIELD_CATALOG.items():
        category = str(config["category"])
        resource_type, app_href = _CATEGORY_DESTINATIONS[category]
        semantic = META_WEBHOOK_SEMANTIC_CONTRACTS[field]
        applicability = (
            "manual_only"
            if field in _MANUAL_ONLY
            else "asset_dependent"
            if field in _ASSET_DEPENDENT
            else "waba_level"
        )
        registry[field] = MetaWebhookContract(
            field=field,
            category=category,
            severity=str(config["severity"]),
            sprint=sprint_by_field[field],
            destination=semantic["destination"],
            description=semantic["description"],
            operator_action=semantic["operator_action"],
            resource_type=resource_type,
            app_href=app_href,
            blackbox_href=f"/blackbox/official-whatsapp?tab=webhooks&field={field}",
            applicability=applicability,
            action_key=_ACTION_KEYS.get(field),
            native_effect=bool(config.get("native")),
        )
    if len(registry) != 33:
        raise RuntimeError(
            f"Meta webhook registry must contain exactly 33 fields, got {len(registry)}"
        )
    return registry


META_WEBHOOK_REGISTRY = _build_registry()

META_WEBHOOK_HANDLERS: dict[str, MetaWebhookHandler] = {
    **ACCOUNT_HANDLERS,
    **GROUP_HANDLERS,
    **MESSAGE_TEMPLATE_HANDLERS,
    **ROUTING_SECURITY_HANDLERS,
    **TRACKING_PREFERENCES_HANDLERS,
}
if set(META_WEBHOOK_HANDLERS) != set(META_WEBHOOK_REGISTRY):
    missing = sorted(set(META_WEBHOOK_REGISTRY) - set(META_WEBHOOK_HANDLERS))
    extra = sorted(set(META_WEBHOOK_HANDLERS) - set(META_WEBHOOK_REGISTRY))
    raise RuntimeError(f"Meta webhook handler coverage mismatch; missing={missing}, extra={extra}")
