"""
Service: Google Ads — Integração com Google Ads API para rastreamento de CAC.

Fluxo:
  1. Workspace configura credenciais OAuth2 via IntegrationConnection
  2. Relatórios puxam spend, CPL e conversões por campanha
  3. Atribuição via gclid/UTM em leads capturados no site

Documentação: https://developers.google.com/google-ads/api/docs/start
Versão: v18 (estável, 2026)

Permissões necessárias:
  - Developer Token (conta Google Ads Manager)
  - OAuth2 refresh_token com escopo: https://www.googleapis.com/auth/adwords
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app.core.klm_runtime import require_external_delivery_allowed
from app.core.logging import logger

GOOGLE_ADS_API_VERSION = "v18"
GOOGLE_ADS_BASE = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"


class GoogleAdsService:
    """
    Integração com Google Ads API via REST.
    Credenciais por workspace (salvas em IntegrationConnection.settings).
    """

    def __init__(
        self,
        developer_token: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        customer_id: str,  # format: "1234567890" (sem hífens)
    ):
        self.developer_token = developer_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.customer_id = customer_id.replace("-", "")

    async def _get_access_token(self) -> str:
        """Obtém access token via OAuth2 refresh token."""
        require_external_delivery_allowed("Consulta ao Google Ads")
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def get_campaign_spend(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retorna spend, impressions, clicks, conversões e CPL por campanha.

        Args:
            start_date: Data de início (padrão: 30 dias atrás)
            end_date: Data de fim (padrão: hoje)
        """
        import httpx

        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=30)

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        query = f"""
            SELECT
                campaign.id,
                campaign.name,
                campaign.status,
                campaign.advertising_channel_type,
                metrics.cost_micros,
                metrics.impressions,
                metrics.clicks,
                metrics.conversions,
                metrics.cost_per_conversion,
                metrics.ctr,
                metrics.average_cpc
            FROM campaign
            WHERE segments.date BETWEEN '{start_str}' AND '{end_str}'
              AND campaign.status != 'REMOVED'
            ORDER BY metrics.cost_micros DESC
            LIMIT 100
        """

        access_token = await self._get_access_token()
        url = f"{GOOGLE_ADS_BASE}/customers/{self.customer_id}/googleAds:search"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json={"query": query},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "developer-token": self.developer_token,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        campaigns = []
        for row in data.get("results", []):
            campaign = row.get("campaign", {})
            metrics = row.get("metrics", {})

            cost_micros = int(metrics.get("costMicros", 0) or 0)
            spend_usd = cost_micros / 1_000_000

            conversions = float(metrics.get("conversions", 0) or 0)
            cost_per_conv_micros = int(metrics.get("costPerConversion", 0) or 0)
            cpl_usd = cost_per_conv_micros / 1_000_000 if cost_per_conv_micros else None

            campaigns.append({
                "campaign_id": campaign.get("id"),
                "campaign_name": campaign.get("name"),
                "status": campaign.get("status"),
                "channel_type": campaign.get("advertisingChannelType"),
                "spend_usd": spend_usd,
                "impressions": int(metrics.get("impressions", 0) or 0),
                "clicks": int(metrics.get("clicks", 0) or 0),
                "conversions": conversions,
                "cpl_usd": cpl_usd,
                "ctr": float(metrics.get("ctr", 0) or 0),
                "avg_cpc_usd": int(metrics.get("averageCpc", 0) or 0) / 1_000_000,
                "date_range": {"start": start_str, "end": end_str},
            })

        logger.info("google_ads.campaigns_fetched", count=len(campaigns))
        return campaigns

    async def get_ad_group_spend(
        self,
        campaign_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """Retorna spend por ad group de uma campanha específica."""
        import httpx

        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=30)

        query = f"""
            SELECT
                ad_group.id,
                ad_group.name,
                ad_group.status,
                metrics.cost_micros,
                metrics.impressions,
                metrics.clicks,
                metrics.conversions,
                metrics.cost_per_conversion
            FROM ad_group
            WHERE campaign.id = {campaign_id}
              AND segments.date BETWEEN '{start_date.strftime("%Y-%m-%d")}' AND '{end_date.strftime("%Y-%m-%d")}'
            ORDER BY metrics.cost_micros DESC
            LIMIT 50
        """

        access_token = await self._get_access_token()
        url = f"{GOOGLE_ADS_BASE}/customers/{self.customer_id}/googleAds:search"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json={"query": query},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "developer-token": self.developer_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        ad_groups = []
        for row in data.get("results", []):
            ag = row.get("adGroup", {})
            metrics = row.get("metrics", {})
            cost_micros = int(metrics.get("costMicros", 0) or 0)
            cpc_micros = int(metrics.get("costPerConversion", 0) or 0)
            ad_groups.append({
                "ad_group_id": ag.get("id"),
                "ad_group_name": ag.get("name"),
                "status": ag.get("status"),
                "spend_usd": cost_micros / 1_000_000,
                "impressions": int(metrics.get("impressions", 0) or 0),
                "clicks": int(metrics.get("clicks", 0) or 0),
                "conversions": float(metrics.get("conversions", 0) or 0),
                "cpl_usd": cpc_micros / 1_000_000 if cpc_micros else None,
            })

        return ad_groups


def extract_gclid_from_url(url: str) -> Optional[str]:
    """
    Extrai gclid de uma URL com parâmetro de tracking do Google Ads.
    Usado para atribuição de leads vindos do site.

    Exemplo: https://site.com/contato?gclid=CjwKCAjw...&utm_source=google
    """
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    gclid_values = params.get("gclid", [])
    return gclid_values[0] if gclid_values else None


def parse_utm_params(url: str) -> Dict[str, Optional[str]]:
    """
    Extrai parâmetros UTM de uma URL para atribuição de campanha.

    Retorna: { source, medium, campaign, term, content, gclid }
    """
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    def _first(key: str) -> Optional[str]:
        values = params.get(key, [])
        return values[0] if values else None

    return {
        "source": _first("utm_source"),
        "medium": _first("utm_medium"),
        "campaign": _first("utm_campaign"),
        "term": _first("utm_term"),
        "content": _first("utm_content"),
        "gclid": _first("gclid"),
    }
