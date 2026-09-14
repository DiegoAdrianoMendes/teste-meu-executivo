"""Google Ads — Data Manager API emitter for prepared offline conversions.

Companion to `ads_conversion_feedback_service._google_envelope` (the envelope
builder) and `app/workers/ads_feedback_tasks.py` (the generic, gated
dispatcher consumer of `ExternalActionIntent(action_type="ads_conversion_feedback")`).
This module owns only the Google-specific pieces:

  * `GoogleDataManagerConfig` / `load_google_data_manager_config` — resolve
    the per-workspace Data Manager credentials from
    `IntegrationConnection(provider=google_ads, label=GOOGLE_CONVERSIONS_LABEL)`,
    mirroring `ads_feedback_tasks.load_meta_capi_config`.
  * `dispatch_google_conversion` — the real POST to the Data Manager API,
    only ever reached by the worker when `external_delivery_allowed()` is
    true, the intent is `approved_local` and the envelope is
    `readyForDispatch` (same gate discipline as `_dispatch_meta_real`).

Since 15/06/2026 the Google Ads API's `ClickConversionService` is BLOCKED for
offline conversion upload — this is the only supported path for server-side
conversion feedback to Google going forward (verified in
`docs/GOOGLE_TRACKING_KNOWLEDGE_2026-07.md` §1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import IntegrationConnection, IntegrationProvider
from app.services.integration_secrets import reveal_integration_secret

GOOGLE_CONVERSIONS_LABEL = "google_data_manager"
_DATA_MANAGER_API_VERSION = "v1"
_DATA_MANAGER_BASE = f"https://datamanager.googleapis.com/{_DATA_MANAGER_API_VERSION}"


@dataclass(frozen=True)
class GoogleDataManagerConfig:
    customer_id: str | None
    developer_token: str | None
    access_token: str | None
    connection_id: UUID | None

    @property
    def configured(self) -> bool:
        return bool(self.customer_id and self.developer_token and self.access_token)


async def load_google_data_manager_config(
    db: AsyncSession, workspace_id: UUID
) -> GoogleDataManagerConfig:
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.google_ads,
            IntegrationConnection.label == GOOGLE_CONVERSIONS_LABEL,
        )
    )
    if connection is None:
        return GoogleDataManagerConfig(None, None, None, None)
    stored = connection.settings or {}
    access_token = reveal_integration_secret(stored, "accessToken") or None
    return GoogleDataManagerConfig(
        customer_id=stored.get("customerId") or None,
        developer_token=reveal_integration_secret(stored, "developerToken") or None,
        access_token=access_token,
        connection_id=connection.id,
    )


async def dispatch_google_conversion(
    *, envelope: dict[str, Any], config: GoogleDataManagerConfig
) -> tuple[dict[str, Any], int, str | None]:
    """POST the prepared event(s) to the Data Manager API `:ingestEvents`
    endpoint for the configured customer account.

    Only ever reached when `external_delivery_allowed()` is true — callers
    must check the gate before invoking this function (same contract as
    `ads_feedback_tasks._dispatch_meta_real`).
    """
    destination = envelope.get("destination") or {}
    customer_id = config.customer_id or destination.get("customerId")
    url = destination.get("endpoint") or (
        f"{_DATA_MANAGER_BASE}/accounts/{customer_id}:ingestEvents" if customer_id else None
    )
    if not url:
        return (
            {"error": {"message": "customerId ausente na configuração."}},
            0,
            "missing_customer_id",
        )
    provider_payload = envelope.get("providerPayload") or {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {config.access_token}",
                "developer-token": config.developer_token or "",
                "Content-Type": "application/json",
            },
            json=provider_payload,
        )
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    return body, response.status_code, None


__all__ = [
    "GOOGLE_CONVERSIONS_LABEL",
    "GoogleDataManagerConfig",
    "dispatch_google_conversion",
    "load_google_data_manager_config",
]
