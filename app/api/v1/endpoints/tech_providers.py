"""Endpoints do fluxo oficial Tech Provider / Embedded Signup."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.config import settings
from app.core.deps import get_current_user_id, get_current_workspace_id, get_db
from app.core.klm_runtime import external_delivery_allowed
from app.schemas.whatsapp import (
    TechProviderCallbackRequest,
    TechProviderConfigRead,
    WhatsAppConnectionRead,
)
from app.services.entitlement_service import EntitlementError
from app.services.whatsapp_service import WhatsAppService

router = APIRouter(prefix="/tech-providers", tags=["Tech Providers"])


def _get_service(db: AsyncSession = Depends(get_db)) -> WhatsAppService:
    return WhatsAppService(db)


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


@router.get(
    "/config",
    response_model=TechProviderConfigRead,
    summary="Ler configuração pública do Embedded Signup",
)
async def tech_provider_config(
    _current_user_id: UUID = Depends(get_current_user_id),
) -> TechProviderConfigRead:
    delivery_available = external_delivery_allowed()
    missing = [
        name
        for name, value in (
            ("META_APP_ID", settings.META_APP_ID),
            ("META_CONFIG_ID", settings.META_CONFIG_ID),
        )
        if not str(value or "").strip()
    ]
    if not delivery_available:
        missing.append("KLM_EXTERNAL_DELIVERY_GATE")
    return TechProviderConfigRead(
        available=not missing,
        manualCloudApiAvailable=delivery_available,
        coexistenceAvailable=not missing,
        techProviderReady=not missing and bool(settings.META_SOLUTION_ID.strip()),
        appId=settings.META_APP_ID or None,
        configId=settings.META_CONFIG_ID or None,
        solutionId=settings.META_SOLUTION_ID or None,
        graphApiVersion=settings.META_GRAPH_API_VERSION,
        missingConfiguration=missing,
        missingTechProviderConfiguration=(
            [] if settings.META_SOLUTION_ID.strip() else ["META_SOLUTION_ID"]
        ),
    )


@router.post(
    "/callback",
    response_model=WhatsAppConnectionRead,
    status_code=status.HTTP_200_OK,
    summary="Concluir Embedded Signup da Meta e persistir a conexão do workspace atual",
)
async def tech_provider_callback(
    payload: TechProviderCallbackRequest,
    svc: WhatsAppService = Depends(_get_service),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    _require_external_delivery("Callback Tech Provider do WhatsApp")
    try:
        return await svc.complete_embedded_signup(payload, workspace_id)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
