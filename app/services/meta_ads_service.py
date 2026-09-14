"""
Service: Meta Ads — Integração com Meta Marketing API para rastreamento de CAC.

Fluxo:
  1. Workspace conecta sua conta Meta via OAuth2 (salvo em IntegrationConnection)
  2. Webhook de Lead Form recebe leads em tempo real e cria Matter com atribuição
  3. Relatórios puxam spend, CPL e conversões por campanha

Documentação: https://developers.facebook.com/docs/marketing-api/
Versão: acompanha settings.META_GRAPH_API_VERSION (fonte única de versão Graph)

Permissões necessárias:
  - ads_read (puxar spend e campanhas)
  - lead_retrieval (puxar leads de formulários)
  - pages_read_engagement (assinar webhooks)
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Dict, List

from app.core.config import settings
from app.core.klm_runtime import require_external_delivery_allowed
from app.core.logging import logger

META_GRAPH_BASE = f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}"


class MetaAdsService:
    """
    Integração com Meta Marketing API.
    Configuração por workspace (access_token salvo em IntegrationConnection.settings).
    """

    def __init__(self, access_token: str, ad_account_id: str):
        self.access_token = access_token
        self.ad_account_id = ad_account_id  # format: "act_123456789"

    async def get_campaign_spend(
        self,
        date_preset: str = "last_30d",
    ) -> List[Dict[str, Any]]:
        """
        Retorna spend, impressions, clicks e CPL por campanha nos últimos N dias.

        Args:
            date_preset: "last_7d", "last_30d", "last_90d", "this_month", "last_month"
        """
        require_external_delivery_allowed("Consulta ao Meta Ads")
        import httpx

        url = f"{META_GRAPH_BASE}/{self.ad_account_id}/campaigns"
        params = {
            "access_token": self.access_token,
            "fields": (
                "id,name,status,objective,"
                "insights.date_preset({preset}){{spend,impressions,clicks,cpl,"
                "actions,cost_per_action_type,reach}}"
            ).format(preset=date_preset),
            "limit": 100,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        campaigns = []
        for camp in data.get("data", []):
            insights = camp.get("insights", {}).get("data", [{}])[0]
            lead_count = 0
            cost_per_lead = None

            for action in insights.get("actions", []):
                if action.get("action_type") == "lead":
                    lead_count = int(action.get("value", 0))

            for cpa in insights.get("cost_per_action_type", []):
                if cpa.get("action_type") == "lead":
                    cost_per_lead = float(cpa.get("value", 0))

            campaigns.append({
                "campaign_id": camp["id"],
                "campaign_name": camp["name"],
                "status": camp.get("status"),
                "objective": camp.get("objective"),
                "spend_usd": float(insights.get("spend", 0) or 0),
                "impressions": int(insights.get("impressions", 0) or 0),
                "clicks": int(insights.get("clicks", 0) or 0),
                "reach": int(insights.get("reach", 0) or 0),
                "leads": lead_count,
                "cpl_usd": cost_per_lead,
                "date_preset": date_preset,
            })

        logger.info("meta_ads.campaigns_fetched", count=len(campaigns))
        return campaigns

    async def get_lead_form_data(self, leadgen_id: str) -> Dict[str, Any]:
        """
        Recupera dados completos de um lead via leadgen_id (recebido pelo webhook).
        Retorna campos do formulário (nome, email, telefone, etc).
        """
        require_external_delivery_allowed("Consulta de lead no Meta Ads")
        import httpx

        from app.services.whatsapp.media_security import trusted_meta_object_id

        url = f"{META_GRAPH_BASE}/{trusted_meta_object_id(leadgen_id, kind='lead')}"
        params = {
            "access_token": self.access_token,
            "fields": "id,created_time,field_data,ad_id,adset_id,campaign_id,form_id",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def verify_webhook_signature(
        payload_bytes: bytes,
        signature_header: str,
        app_secret: str,
    ) -> bool:
        """
        Valida assinatura HMAC-SHA256 do webhook Meta.
        Header: X-Hub-Signature-256: sha256=<hash>
        """
        if not signature_header.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(
            app_secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)


def parse_lead_webhook_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extrai leads de um payload de webhook Meta Lead Ads.

    Retorna lista de:
      { leadgen_id, page_id, form_id, adgroup_id, ad_id, campaign_id, created_time }
    """
    leads = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "leadgen":
                value = change.get("value", {})
                leads.append({
                    "leadgen_id": value.get("leadgen_id"),
                    "page_id": value.get("page_id"),
                    "form_id": value.get("form_id"),
                    "adgroup_id": value.get("adgroup_id"),
                    "ad_id": value.get("ad_id"),
                    "campaign_id": value.get("campaign_id", ""),
                    "created_time": value.get("created_time"),
                })
    return leads
