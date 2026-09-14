from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

import httpx

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.models.whatsapp import WhatsAppConnection
from app.services.whatsapp.errors import WhatsAppProviderError


class QrGatewayClient:
    def __init__(self) -> None:
        self._base_url = settings.WHATSAPP_QR_GATEWAY_BASE_URL.rstrip("/")

    async def create_instance(
        self,
        *,
        workspace_id: UUID,
        display_name: Optional[str],
        provider: str,
    ) -> Dict[str, Any]:
        if not external_delivery_allowed():
            raise WhatsAppProviderError(
                code="KLM-EXT-001",
                message=(
                    "Chamada ao gateway QR bloqueada enquanto entregas externas estiverem desabilitadas."
                ),
                status_code=503,
            )
        if not self._base_url:
            raise WhatsAppProviderError(
                code="WA-QR-001",
                message="WHATSAPP_QR_GATEWAY_BASE_URL não configurado.",
                status_code=503,
            )
        data = await self._request(
            "POST",
            "/instances",
            json={
                "workspaceId": str(workspace_id),
                "displayName": display_name,
                "provider": provider,
            },
        )
        return {
            "instance_id": data.get("instanceId") or data.get("instance_id") or data.get("id"),
            "instance_token": data.get("instanceToken") or data.get("instance_token") or data.get("token"),
            "status": data.get("status") or "pending",
            "phone_number": data.get("phoneNumber") or data.get("phone_number"),
            "display_phone_number": data.get("displayPhoneNumber") or data.get("display_phone_number"),
        }

    async def get_instance_status(self, connection: WhatsAppConnection, instance_token: Optional[str]) -> Dict[str, Any]:
        if not connection.instance_id:
            raise WhatsAppProviderError(
                code="WA-QR-002",
                message="A conexão QR não possui instance_id registrado.",
                status_code=400,
                connection_fault=True,
            )
        data = await self._request(
            "GET",
            f"/instances/{connection.instance_id}",
            headers=self._instance_headers(instance_token),
        )
        return {
            "status": data.get("status") or connection.qr_status or "pending",
            "phone_number": data.get("phoneNumber") or data.get("phone_number") or connection.phone_number,
            "display_phone_number": data.get("displayPhoneNumber") or data.get("display_phone_number") or connection.display_phone_number,
        }

    async def send_text_message(
        self,
        *,
        connection: WhatsAppConnection,
        instance_token: Optional[str],
        to: str,
        body: str,
    ) -> Dict[str, Any]:
        if not connection.instance_id:
            raise WhatsAppProviderError(
                code="WA-QR-002",
                message="A conexão QR não possui instance_id registrado.",
                status_code=400,
                connection_fault=True,
            )
        return await self._request(
            "POST",
            f"/instances/{connection.instance_id}/messages",
            headers=self._instance_headers(instance_token),
            json={"to": to, "type": "text", "text": {"body": body}},
        )

    async def send_document_message(
        self,
        *,
        connection: WhatsAppConnection,
        instance_token: Optional[str],
        to: str,
        url: str,
        filename: str,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not connection.instance_id:
            raise WhatsAppProviderError(
                code="WA-QR-002",
                message="A conexão QR não possui instance_id registrado.",
                status_code=400,
                connection_fault=True,
            )
        return await self._request(
            "POST",
            f"/instances/{connection.instance_id}/messages",
            headers=self._instance_headers(instance_token),
            json={
                "to": to,
                "type": "document",
                "document": {
                    "url": url,
                    "filename": filename,
                    "caption": caption,
                },
            },
        )

    def _instance_headers(self, instance_token: Optional[str]) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if settings.WHATSAPP_QR_GATEWAY_API_KEY:
            headers["X-API-Key"] = settings.WHATSAPP_QR_GATEWAY_API_KEY
        if instance_token:
            headers["X-Instance-Token"] = instance_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not external_delivery_allowed():
            raise WhatsAppProviderError(
                code="KLM-EXT-001",
                message=(
                    "Chamada ao gateway QR bloqueada enquanto entregas externas estiverem desabilitadas."
                ),
                status_code=503,
            )
        if not self._base_url:
            raise WhatsAppProviderError(
                code="WA-QR-001",
                message="WHATSAPP_QR_GATEWAY_BASE_URL não configurado.",
                status_code=503,
            )
        merged_headers = {"Content-Type": "application/json", **(headers or {})}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=merged_headers,
                    json=json,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise WhatsAppProviderError(
                code="WA-QR-CONNECT",
                message="Não foi possível iniciar a conexão com o gateway QR.",
                status_code=503,
                request_started=False,
            ) from exc
        except httpx.RequestError as exc:
            raise WhatsAppProviderError(
                code="WA-QR-DELIVERY-UNCERTAIN",
                message="A conexão com o gateway QR foi interrompida após o início da requisição.",
                status_code=502,
                request_started=True,
                delivery_outcome_known=False,
            ) from exc
        if response.status_code not in (200, 201):
            message = response.text or "Falha no gateway QR."
            raise WhatsAppProviderError(
                code="WA-QR-500" if response.status_code >= 500 else "WA-QR-003",
                message=message,
                status_code=400 if response.status_code < 500 else 502,
                request_started=True,
                delivery_outcome_known=response.status_code < 500,
                connection_fault=response.status_code in {401, 403},
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise WhatsAppProviderError(
                code="WA-QR-DELIVERY-UNCERTAIN",
                message="O gateway QR aceitou a requisição, mas retornou uma resposta inválida.",
                status_code=502,
                request_started=True,
                delivery_outcome_known=False,
            ) from exc
