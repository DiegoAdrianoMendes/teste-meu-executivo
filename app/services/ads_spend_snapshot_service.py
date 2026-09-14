"""Snapshot de gasto de campanha (Meta Ads / Google Ads) para CAC real.

Fecha o item 10 da auditoria 2026-07-26: `MetaAdsService` e `GoogleAdsService`
existiam prontos (fetch de spend/CPL por campanha) mas sem NENHUM caller no
app inteiro — código morto. Este módulo é o caller: lê a conta de anúncios
conectada do workspace (se houver) e, com o gate de ações externas aberto,
busca o spend real por campanha, best-effort.

`tracking_summary`/`attribution_rows` (`klm_operations_service.py`) já
calculam CAC a partir de `AcquisitionEvent(event_name="ad_spend")` — hoje
alimentado manualmente/via seed. Este módulo não substitui esse cálculo (que
já funciona e é testado); ele fecha o gap real do item 10 dando aos dois
serviços mortos um consumidor de verdade, exposto como leitura auxiliar
("qual é o gasto real agora, direto da conta conectada") — ver
`GET /tracking/campaigns/ad-spend` em `app/api/v1/endpoints/klm_operations.py`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.services.integration_secrets import reveal_integration_secret

META_ADS_ACCOUNT_LABEL = "Meta Ads — Conta de anúncios"
GOOGLE_ADS_ACCOUNT_LABEL = "Google Ads — Conta de anúncios"


async def fetch_meta_campaign_spend_snapshot(
    db: AsyncSession, workspace_id: UUID, *, date_preset: str = "last_30d"
) -> dict[str, Any]:
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.meta_ads,
            IntegrationConnection.label == META_ADS_ACCOUNT_LABEL,
            IntegrationConnection.status == IntegrationStatus.connected,
        )
    )
    if connection is None:
        return {"available": False, "reason": "conta_meta_ads_nao_conectada", "campaigns": []}
    stored = connection.settings or {}
    access_token = reveal_integration_secret(stored, "accessToken") or None
    ad_account_id = str(stored.get("adAccountId") or "").strip() or None
    if not access_token or not ad_account_id:
        return {"available": False, "reason": "credenciais_meta_ads_incompletas", "campaigns": []}
    if not external_delivery_allowed():
        return {"available": False, "reason": "gate_externo_fechado", "campaigns": []}

    from app.services.meta_ads_service import MetaAdsService

    try:
        campaigns = await MetaAdsService(access_token, ad_account_id).get_campaign_spend(
            date_preset=date_preset
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca derruba o relatório
        logger.error(
            "ads_spend_snapshot.meta_fetch_failed",
            workspace_id=str(workspace_id),
            error_type=safe_error_type(exc),
        )
        return {"available": False, "reason": "erro_consulta_meta_ads", "campaigns": []}
    return {"available": True, "reason": None, "campaigns": campaigns}


async def fetch_google_campaign_spend_snapshot(
    db: AsyncSession, workspace_id: UUID
) -> dict[str, Any]:
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.google_ads,
            IntegrationConnection.label == GOOGLE_ADS_ACCOUNT_LABEL,
            IntegrationConnection.status == IntegrationStatus.connected,
        )
    )
    if connection is None:
        return {"available": False, "reason": "conta_google_ads_nao_conectada", "campaigns": []}
    stored = connection.settings or {}
    developer_token = reveal_integration_secret(stored, "developerToken") or None
    client_id = reveal_integration_secret(stored, "clientId") or None
    client_secret = reveal_integration_secret(stored, "clientSecret") or None
    refresh_token = reveal_integration_secret(stored, "refreshToken") or None
    customer_id = str(stored.get("customerId") or "").strip() or None
    if not all([developer_token, client_id, client_secret, refresh_token, customer_id]):
        return {"available": False, "reason": "credenciais_google_ads_incompletas", "campaigns": []}
    if not external_delivery_allowed():
        return {"available": False, "reason": "gate_externo_fechado", "campaigns": []}

    from app.services.google_ads_service import GoogleAdsService

    try:
        campaigns = await GoogleAdsService(
            developer_token=developer_token,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            customer_id=customer_id,
        ).get_campaign_spend()
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca derruba o relatório
        logger.error(
            "ads_spend_snapshot.google_fetch_failed",
            workspace_id=str(workspace_id),
            error_type=safe_error_type(exc),
        )
        return {"available": False, "reason": "erro_consulta_google_ads", "campaigns": []}
    return {"available": True, "reason": None, "campaigns": campaigns}


__all__ = [
    "GOOGLE_ADS_ACCOUNT_LABEL",
    "META_ADS_ACCOUNT_LABEL",
    "fetch_google_campaign_spend_snapshot",
    "fetch_meta_campaign_spend_snapshot",
]
