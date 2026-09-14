"""Hard gate for the finance upgrade that is intentionally paused for launch."""
from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException, status

FINANCIAL_UPGRADE_STATUS = "PAUSADO"
FINANCIAL_UPGRADE_UNLOCK_TRIGGER = "destravar upgrade de funções financeiras"
PAUSED_FINANCIAL_PROVIDERS = frozenset({"asaas", "conta_azul", "pluga", "zapier"})

_FINANCIAL_MARKERS = frozenset(
    {
        "asaas",
        "bank",
        "banco",
        "banking",
        "billing",
        "boleto",
        "charge",
        "charges",
        "cobranca",
        "conciliacao",
        "conta azul",
        "conta_azul",
        "finance",
        "financeiro",
        "financial",
        "payment",
        "payments",
        "pix",
        "pluga",
        "receivable",
        "receivables",
        "zapier",
    }
)


def financial_upgrade_pause_detail() -> dict[str, object]:
    return {
        "code": "FINANCIAL_UPGRADE_PAUSED",
        "status": FINANCIAL_UPGRADE_STATUS,
        "message": (
            "Upgrade financeiro avançado Em breve. O financeiro local permanece "
            "disponível."
        ),
        "unlock_trigger": FINANCIAL_UPGRADE_UNLOCK_TRIGGER,
        "external_calls_executed": 0,
    }


def require_financial_upgrade_unlocked() -> None:
    if FINANCIAL_UPGRADE_STATUS == "PAUSADO":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=financial_upgrade_pause_detail(),
        )


def is_paused_financial_integration(
    *,
    provider: object,
    label: str | None = None,
    base_url: str | None = None,
    webhook_url: str | None = None,
    capabilities: Sequence[str] | None = None,
    settings: Mapping[str, Any] | None = None,
    notes: str | None = None,
) -> bool:
    """Recognize direct providers and financial bridges stored as ``custom``."""

    provider_key = _normalized(getattr(provider, "value", provider))
    if provider_key in PAUSED_FINANCIAL_PROVIDERS:
        return True
    if provider_key != "custom":
        return False

    settings_text = ""
    if settings:
        try:
            settings_text = json.dumps(settings, ensure_ascii=True, sort_keys=True, default=str)
        except (TypeError, ValueError):
            settings_text = str(settings)
    searchable = " ".join(
        [
            label or "",
            base_url or "",
            webhook_url or "",
            notes or "",
            " ".join(capabilities or []),
            settings_text,
        ]
    )
    normalized = _normalized(searchable)
    return any(marker in normalized for marker in _FINANCIAL_MARKERS)


def enforce_financial_plan_only(
    *,
    provider: object,
    connection_status: object,
    label: str | None = None,
    base_url: str | None = None,
    external_account_id: str | None = None,
    webhook_url: str | None = None,
    capabilities: Sequence[str] | None = None,
    settings: Mapping[str, Any] | None = None,
    notes: str | None = None,
    last_sync_at: object | None = None,
) -> None:
    """Allow documentation-only records and reject any activation evidence."""

    if FINANCIAL_UPGRADE_STATUS != "PAUSADO" or not is_paused_financial_integration(
        provider=provider,
        label=label,
        base_url=base_url,
        webhook_url=webhook_url,
        capabilities=capabilities,
        settings=settings,
        notes=notes,
    ):
        return

    status_key = _normalized(getattr(connection_status, "value", connection_status))
    has_runtime_state = bool(
        status_key not in {"pending", "disconnected"}
        or (external_account_id and external_account_id.strip())
        or settings
        or last_sync_at is not None
    )
    if has_runtime_state:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=financial_upgrade_pause_detail(),
        )


def _normalized(value: object) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        "".join(character for character in raw if not unicodedata.combining(character))
        .strip()
        .lower()
        .replace("-", " ")
        .split()
    )


__all__ = [
    "FINANCIAL_UPGRADE_STATUS",
    "FINANCIAL_UPGRADE_UNLOCK_TRIGGER",
    "PAUSED_FINANCIAL_PROVIDERS",
    "enforce_financial_plan_only",
    "financial_upgrade_pause_detail",
    "is_paused_financial_integration",
    "require_financial_upgrade_unlocked",
]
