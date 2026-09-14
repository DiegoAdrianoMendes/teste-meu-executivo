from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.core.klm_runtime import external_delivery_allowed
from app.schemas.whatsapp import TechProviderCallbackRequest, WhatsAppConnectionRead
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import (
    MetaBusinessTokenGrant,
    MetaCloudClient,
    meta_request_scope,
)


def _require_external_delivery(action: str) -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "KLM-EXT-001",
                "message": (
                    f"{action} bloqueado enquanto entregas externas estiverem desabilitadas."
                ),
            },
        )


class TechProviderService:
    def __init__(
        self,
        connection_service: WhatsAppConnectionService,
        *,
        meta_client: MetaCloudClient | None = None,
    ) -> None:
        self.connection_service = connection_service
        self.meta_client = meta_client or MetaCloudClient()

    async def complete_embedded_signup(
        self,
        *,
        workspace_id: UUID,
        payload: TechProviderCallbackRequest,
    ) -> WhatsAppConnectionRead:
        _require_external_delivery("Callback Tech Provider do WhatsApp")
        await self.connection_service.preflight_tech_provider_connection(
            workspace_id=workspace_id,
            phone_number_id=payload.phone_number_id,
            preferred_default=payload.is_default,
        )
        try:
            async with meta_request_scope(self.meta_client):
                token_grant = await self.meta_client.exchange_code_for_business_token(
                    payload.code
                )
                if isinstance(token_grant, MetaBusinessTokenGrant):
                    business_token = token_grant.access_token
                    token_expires_at = token_grant.expires_at
                else:  # Compatibility for injected test doubles from the former string contract.
                    business_token = str(token_grant)
                    token_expires_at = None
                waba_id = payload.waba_id or await self.meta_client.resolve_waba_id_from_token(
                    business_token
                )
                if not waba_id:
                    raise WhatsAppProviderError(
                        code="WA-013",
                        message="Não foi possível descobrir o WABA ID desta autorização da Meta.",
                        status_code=422,
                    )

                phone_number_id = payload.phone_number_id
                if not phone_number_id:
                    phone_numbers = await self.meta_client.get_waba_phone_numbers(
                        waba_id, business_token
                    )
                    if not phone_numbers:
                        raise WhatsAppProviderError(
                            code="WA-014",
                            message=(
                                "Nenhum número de WhatsApp Business foi encontrado para este WABA."
                            ),
                            status_code=422,
                        )
                    phone_number_id = str(phone_numbers[0]["id"])

                await self.meta_client.subscribe_app(waba_id, business_token)
                registration_pin: str | None = None
                if payload.transport_mode != "coexistence":
                    registration_pin = (
                        payload.registration_pin
                        or await self.connection_service.get_stored_registration_pin(
                            workspace_id, phone_number_id
                        )
                        or self.connection_service.generate_registration_pin()
                    )
                    await self.meta_client.register_phone_number(
                        phone_number_id, business_token, registration_pin
                    )
                save_kwargs = {
                    "workspace_id": workspace_id,
                    "business_token": business_token,
                    "phone_number_id": phone_number_id,
                    "waba_id": waba_id,
                    "registration_pin": registration_pin,
                    "transport_mode": payload.transport_mode,
                    "is_default": payload.is_default,
                }
                if token_expires_at is not None:
                    save_kwargs["token_expires_at"] = token_expires_at
                connection = await self.connection_service.save_tech_provider_connection(
                    **save_kwargs,
                )
                if payload.transport_mode != "coexistence":
                    return connection

                contacts_request_id: str | None = None
                history_request_id: str | None = None
                try:
                    contacts_request_id = await self.meta_client.request_business_app_sync(
                        phone_number_id,
                        business_token,
                        sync_type="smb_app_state_sync",
                    )
                    history_request_id = await self.meta_client.request_business_app_sync(
                        phone_number_id,
                        business_token,
                        sync_type="history",
                    )
                    return await self.connection_service.record_coexistence_sync(
                        workspace_id=workspace_id,
                        connection_id=connection.id,
                        contacts_request_id=contacts_request_id,
                        history_request_id=history_request_id,
                    )
                except WhatsAppProviderError as sync_exc:
                    return await self.connection_service.record_coexistence_sync(
                        workspace_id=workspace_id,
                        connection_id=connection.id,
                        contacts_request_id=contacts_request_id,
                        history_request_id=history_request_id,
                        error=sync_exc,
                    )
        except WhatsAppProviderError as exc:
            raise self.connection_service.translate_registration_error(exc) from exc
