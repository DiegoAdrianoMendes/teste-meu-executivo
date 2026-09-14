"""
ZapSign Service — Assinatura eletrônica por workspace
Documentação: https://docs.zapsign.com.br/
API Base: https://api.zapsign.com.br/api/v1
Auth: Bearer token (por workspace, salvo em IntegrationConnection.settings.apiToken)

Cada cliente do SaaS usa seu próprio token ZapSign.
Fallback: variável de ambiente ZAPSIGN_API_TOKEN (para instâncias self-hosted).
"""
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import (
    external_delivery_allowed,
    require_external_delivery_allowed,
)
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.schemas.zapsign import (
    ZapSignDocumentRead,
    ZapSignSigner,
)


class ZapSignService:
    BASE_URL = "https://api.zapsign.com.br/api/v1"

    def __init__(self, api_token: str, webhook_secret: str = "") -> None:
        self._token = api_token
        self._webhook_secret = webhook_secret

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _is_configured(self) -> bool:
        return bool(self._token)

    async def create_document(
        self,
        name: str,
        signers: List[ZapSignSigner],
        pdf_url: Optional[str] = None,
        base64_pdf: Optional[str] = None,
        external_id: Optional[str] = None,
        date_limit_to_sign: Optional[str] = None,
        brand_name: Optional[str] = None,
        brand_logo: Optional[str] = None,
        brand_primary_color: Optional[str] = "#0e3d5c",
    ) -> ZapSignDocumentRead:
        """
        Cria um documento para assinatura.
        Retorna doc_token e sign_url do primeiro signatário.

        POST https://api.zapsign.com.br/api/v1/docs/
        """
        require_external_delivery_allowed("Criação de documento na ZapSign")
        if not self._is_configured():
            logger.warning("zapsign.not_configured")
            raise ValueError("ZapSign API token não configurado. Conecte o ZapSign em Configurações → Integrações.")

        payload: Dict[str, Any] = {
            "name": name,
            "signers": [s.model_dump(exclude_none=True) for s in signers],
            "lang": "pt-br",
        }
        if pdf_url:
            payload["url_pdf"] = pdf_url
        elif base64_pdf:
            payload["base64_pdf"] = base64_pdf
        if external_id:
            payload["external_id"] = external_id
        if date_limit_to_sign:
            payload["date_limit_to_sign"] = date_limit_to_sign
        if brand_name:
            payload["brand_name"] = brand_name
        if brand_logo:
            payload["brand_logo"] = brand_logo
        if brand_primary_color:
            payload["brand_primary_color"] = brand_primary_color

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}/docs/",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()

        logger.info(
            "zapsign.document_created",
            document_ref=opaque_ref(data.get("token"), kind="zapsign_doc"),
            external_ref=opaque_ref(external_id, kind="external_id"),
        )
        return ZapSignDocumentRead(**data)

    async def get_document(self, doc_token: str) -> ZapSignDocumentRead:
        """
        Busca status e detalhes de um documento.

        GET https://api.zapsign.com.br/api/v1/docs/{doc_token}/
        """
        require_external_delivery_allowed("Consulta de documento na ZapSign")
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/docs/{doc_token}/",
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()

        return ZapSignDocumentRead(**data)

    async def register_webhook(self, webhook_url: str, event_type: str = "doc_signed") -> Dict[str, Any]:
        """
        Registra webhook na conta ZapSign.

        POST https://api.zapsign.com.br/api/v1/user/company/webhook/
        """
        require_external_delivery_allowed("Registro de webhook na ZapSign")
        if not self._is_configured():
            raise ValueError("ZapSign API token não configurado.")
        if not self._webhook_secret:
            raise ValueError("Segredo do webhook ZapSign não configurado.")

        payload = {
            "url": webhook_url,
            "type": event_type,
            "headers": [
                {
                    "name": "Authorization",
                    "value": f"Bearer {self._webhook_secret}",
                }
            ],
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}/user/company/webhook/",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_status(self) -> Dict[str, Any]:
        """Verifica se a integração está funcionando testando a API."""
        connected = False
        delivery_allowed = external_delivery_allowed()
        if self._is_configured() and delivery_allowed:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{self.BASE_URL}/user/company/",
                        headers=self._headers,
                    )
                    connected = resp.status_code == 200
            except Exception:
                connected = False

        return {
            "connected": connected,
            "api_token_configured": self._is_configured(),
            "webhook_registered": False,
            "external_actions_enabled": delivery_allowed,
        }


# ── Factory por workspace ─────────────────────────────────────────────────────

async def get_zapsign_service(
    workspace_id: Optional[UUID] = None,
    db: Optional[AsyncSession] = None,
) -> ZapSignService:
    """
    Retorna ZapSignService com o token correto para o workspace.

    Prioridade:
      1. IntegrationConnection.settings['apiToken'] do workspace (multi-tenant)
      2. Variável de ambiente ZAPSIGN_API_TOKEN (fallback / self-hosted)
    """
    token = ""
    webhook_secret = ""

    if workspace_id and db:
        try:
            from app.models.finance import (
                IntegrationConnection,
                IntegrationProvider,
                IntegrationStatus,
            )
            result = await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.provider == IntegrationProvider.zapsign,
                    IntegrationConnection.status == IntegrationStatus.connected,
                )
            )
            conn = result.scalar_one_or_none()
            if conn:
                from app.services.integration_secrets import reveal_integration_secret

                token = reveal_integration_secret(conn.settings, "apiToken")
                webhook_secret = reveal_integration_secret(
                    conn.settings,
                    "webhookSecret",
                )
        except Exception as e:
            logger.warning(
                "zapsign.workspace_token_lookup_failed",
                error_type=safe_error_type(e),
            )

    # Fallback para variável de ambiente
    if not token:
        token = getattr(settings, "ZAPSIGN_API_TOKEN", "")

    return ZapSignService(api_token=token, webhook_secret=webhook_secret)


async def get_zapsign_webhook_secret(
    *,
    workspace_id: UUID,
    db: AsyncSession,
) -> str:
    """Loads only the tenant-bound secret for the dedicated webhook route."""
    from app.models.finance import (
        IntegrationConnection,
        IntegrationProvider,
        IntegrationStatus,
    )
    from app.services.integration_secrets import reveal_integration_secret

    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.zapsign,
            IntegrationConnection.status == IntegrationStatus.connected,
        )
    )
    if connection is None:
        return ""
    return reveal_integration_secret(connection.settings, "webhookSecret").strip()


# Singleton legado (usa apenas env var — mantido para compatibilidade com workers)
def get_zapsign_service_sync() -> ZapSignService:
    return ZapSignService(
        api_token=getattr(settings, "ZAPSIGN_API_TOKEN", ""),
        webhook_secret=getattr(settings, "ZAPSIGN_WEBHOOK_SECRET", ""),
    )
