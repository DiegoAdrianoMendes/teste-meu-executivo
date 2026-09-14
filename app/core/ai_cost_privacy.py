"""Privacy boundary for internal AI provider costs.

Workspace-facing contracts may expose tokens and quota telemetry, but provider
prices and reconciliation amounts belong exclusively to the platform root.
"""
from __future__ import annotations

from typing import Any


_INTERNAL_AI_COST_KEYS = {
    "actualcostusd",
    "budgettotalafterusd",
    "budgetreservedamountusd",
    "costusd",
    "estimatedcostusd",
    "reservedamountusd",
    "totalcostusd",
}


def strip_internal_ai_cost_fields(value: Any) -> Any:
    """Return a deep copy without internal AI monetary fields."""

    if isinstance(value, dict):
        return {
            key: strip_internal_ai_cost_fields(child)
            for key, child in value.items()
            if str(key).replace("_", "").lower() not in _INTERNAL_AI_COST_KEYS
        }
    if isinstance(value, list):
        return [strip_internal_ai_cost_fields(child) for child in value]
    if isinstance(value, tuple):
        return tuple(strip_internal_ai_cost_fields(child) for child in value)
    return value


__all__ = ["strip_internal_ai_cost_fields"]
