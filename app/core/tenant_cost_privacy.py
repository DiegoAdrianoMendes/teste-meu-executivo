"""Tenant boundary for internal AI and provider monetary data.

This filter is intentionally narrower than a generic money scrubber: legal fees,
office finance and commercial proposal values must remain visible to workspaces.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.ai_cost_privacy import strip_internal_ai_cost_fields

_INTERNAL_PROVIDER_COST_KEYS = {
    "actualchargecount",
    "actualcost",
    "actualcostbrl",
    "actualcostsbycurrency",
    "aicost",
    "aicostbrl",
    "averagecostbrl",
    "billablebymeta",
    "billablebyprovider",
    "billingcategory",
    "chargeablemessagecount",
    "costamount",
    "costcurrency",
    "costentries",
    "costentry",
    "costledger",
    "costledgerentryid",
    "costpolicy",
    "costquantity",
    "costrecorded",
    "costsbycurrency",
    "costunit",
    "dailybudgetbrl",
    "estimatedcostbrl",
    "estimatedcostsbycurrency",
    "estimatedexternalcost",
    "externalcost",
    "internalcost",
    "lastactualcostat",
    "lastcostconnectionkey",
    "lastcostprovider",
    "modelcost",
    "pricingcategory",
    "providercharge",
    "providercost",
    "providerfeecurrency",
    "providerinvoiceid",
    "providerinvoicereference",
    "providerprice",
    "tokencost",
    "totalprovidercost",
}

_COST_CONTAINER_KEYS = {
    "aicoststate",
    "coststate",
    "costsummary",
    "providercoststate",
}

_COST_CONTAINER_COMPONENTS = {
    "actual",
    "amount",
    "budget",
    "currency",
    "estimated",
    "price",
    "quantity",
    "rate",
    "total",
    "unit",
}

_PROVIDER_PRICE_SUBJECTS = ("ai", "external", "internal", "model", "provider", "token")
_PROVIDER_PRICE_TERMS = ("budget", "charge", "cost", "fee", "price", "pricing", "rate")


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def is_internal_provider_cost_key(key: Any, *, parent_key: Any = None) -> bool:
    """Return whether a key names provider/AI money rather than office finance."""

    normalized = _normalize_key(key)
    if normalized in _INTERNAL_PROVIDER_COST_KEYS:
        return True

    if normalized.startswith(("actualcost", "averagecost", "estimatedcost")):
        return True
    if normalized.startswith(("costledger", "dailybudget", "monthlyaibudget")):
        return True
    if normalized.endswith(("costamount", "costbrl", "costcurrency", "costusd")):
        return True
    if normalized.startswith(_PROVIDER_PRICE_SUBJECTS) and any(
        term in normalized for term in _PROVIDER_PRICE_TERMS
    ):
        return True

    parent = _normalize_key(parent_key) if parent_key is not None else ""
    return parent in _COST_CONTAINER_KEYS and normalized in _COST_CONTAINER_COMPONENTS


def _strip_internal_provider_cost_fields(value: Any, *, parent_key: Any = None) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_internal_provider_cost_fields(child, parent_key=key)
            for key, child in value.items()
            if not is_internal_provider_cost_key(key, parent_key=parent_key)
        }
    if isinstance(value, list):
        return [
            _strip_internal_provider_cost_fields(child, parent_key=parent_key) for child in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _strip_internal_provider_cost_fields(child, parent_key=parent_key) for child in value
        )
    return value


def strip_internal_provider_cost_fields(value: Any, *, parent_key: Any = None) -> Any:
    """Return a deep copy suitable for tenant-facing playbook/omnichannel data."""

    return _strip_internal_provider_cost_fields(
        strip_internal_ai_cost_fields(value),
        parent_key=parent_key,
    )


__all__ = ["is_internal_provider_cost_key", "strip_internal_provider_cost_fields"]
