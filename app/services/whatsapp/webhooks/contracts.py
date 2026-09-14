from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MetaWebhookContract:
    field: str
    category: str
    severity: str
    sprint: int
    destination: str
    description: str
    operator_action: str
    resource_type: str
    app_href: str
    blackbox_href: str
    applicability: str = "waba_level"
    action_key: str | None = None
    handler_version: int = 1
    native_effect: bool = False


@dataclass(frozen=True)
class MetaWebhookProjection:
    """Normalized, sanitized result produced by one field-specific handler."""

    state_code: str
    severity: str
    resource_type: str
    resource_provider_ref: str | None
    snapshot: dict[str, Any]
    action_key: str | None


MetaWebhookHandler = Callable[[dict[str, Any], MetaWebhookContract], MetaWebhookProjection]
