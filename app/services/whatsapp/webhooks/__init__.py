"""Registry-based Meta webhook processing."""

from app.services.whatsapp.webhooks.dispatcher import MetaWebhookDispatcher
from app.services.whatsapp.webhooks.registry import META_WEBHOOK_REGISTRY, MetaWebhookContract

__all__ = ["META_WEBHOOK_REGISTRY", "MetaWebhookContract", "MetaWebhookDispatcher"]
