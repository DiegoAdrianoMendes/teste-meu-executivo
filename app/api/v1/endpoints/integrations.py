"""
Endpoints da plataforma de integrações abertas e nativas.
"""
from __future__ import annotations

import json
import secrets
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import enforce_workspace_feature_or_http
from app.core.deps import (
    AuthContext,
    get_current_workspace_id,
    get_db,
    require_integrations_access,
    require_permission,
)
from app.core.klm_runtime import external_delivery_allowed
from app.core.request_body import read_bounded_body
from app.models.entitlement import EntitlementKey
from app.schemas.integration import (
    GoogleDataManagerTrackingStatusRead,
    GoogleDataManagerTrackingUpsertRequest,
    IntegrationCatalogProviderRead,
    OpenApiSurfaceRead,
    WebhookReceiptRead,
)
from app.services.financial_upgrade_service import (
    FINANCIAL_UPGRADE_STATUS,
    FINANCIAL_UPGRADE_UNLOCK_TRIGGER,
    PAUSED_FINANCIAL_PROVIDERS,
    require_financial_upgrade_unlocked,
)
from app.services.integration_service import IntegrationService
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/integrations", tags=["Integrações"])

# Não existe módulo RBAC dedicado a "tracking"; a conexão de conversão paga
# (Meta CAPI / Google Data Manager) é uma configuração de integração, então
# reaproveita "integrations" (view/manage), mesmo padrão de
# app/api/v1/endpoints/paid_acquisition.py.
require_tracking_view = require_permission("integrations", "view")
require_tracking_manage = require_permission("integrations", "manage")

# ── Schemas internos ──────────────────────────────────────────────────────────

class ZapSignConnectRequest(BaseModel):
    api_token: str

class ZapSignConnectResponse(BaseModel):
    connected: bool
    message: str

class AsaasConnectRequest(BaseModel):
    api_key: str
    environment: str = "sandbox"  # sandbox | production

class AsaasConnectResponse(BaseModel):
    connected: bool
    message: str


def _require_online_onboarding() -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Onboarding externo bloqueado até a aprovação online do Meu Executivo.",
        )


def _require_financial_upgrade_unlocked() -> None:
    require_financial_upgrade_unlocked()


def _require_external_webhook_delivery() -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "KLM-EXT-001",
                "message": (
                    "Webhook externo bloqueado enquanto entregas externas estiverem "
                    "desabilitadas."
                ),
            },
        )


# ── Catálogo e Open API ───────────────────────────────────────────────────────

@router.get("/catalog", response_model=list[IntegrationCatalogProviderRead], summary="Catálogo de integrações suportadas")
async def get_integration_catalog(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_integrations_access),
    db: AsyncSession = Depends(get_db),
):
    workspace = await WorkspaceService(db).get_workspace(workspace_id)
    return await IntegrationService(db).get_catalog(workspace_id, workspace.slug)


@router.get("/open-api", response_model=OpenApiSurfaceRead, summary="Superfície aberta da API do workspace")
async def get_open_api_surface(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_integrations_access),
    db: AsyncSession = Depends(get_db),
):
    return await IntegrationService(db).get_open_api_surface(workspace_id)


# ── Conectar ZapSign (por workspace) ─────────────────────────────────────────

@router.post(
    "/connect/zapsign",
    response_model=ZapSignConnectResponse,
    summary="Conectar ZapSign ao workspace",
)
async def connect_zapsign(
    data: ZapSignConnectRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_integrations_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Valida o token ZapSign e salva na IntegrationConnection do workspace.
    Cada cliente usa seu próprio token — isolamento multi-tenant.
    """
    await enforce_workspace_feature_or_http(
        db,
        workspace_id,
        EntitlementKey.digital_signature,
    )

    from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
    from app.services.integration_secrets import (
        protect_integration_settings,
        reveal_integration_secret,
    )
    from app.services.zapsign_service import ZapSignService

    _require_online_onboarding()
    # Testar token antes de salvar
    svc = ZapSignService(api_token=data.api_token.strip())
    status_info = await svc.get_status()
    if not status_info["connected"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token ZapSign inválido ou sem acesso. Verifique em app.zapsign.com.br → Conta → Integrações.",
        )

    # Upsert da connection
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.zapsign,
        )
    )
    conn = result.scalar_one_or_none()
    webhook_secret = (
        reveal_integration_secret(conn.settings, "webhookSecret")
        if conn
        else ""
    ) or secrets.token_urlsafe(32)
    protected_secrets = protect_integration_settings(
        {
            "apiToken": data.api_token.strip(),
            "webhookSecret": webhook_secret,
        }
    )

    if conn:
        conn.status = IntegrationStatus.connected
        conn.settings = {
            **(conn.settings or {}),
            **protected_secrets,
        }
        conn.label = "ZapSign"
    else:
        conn = IntegrationConnection(
            workspace_id=workspace_id,
            provider=IntegrationProvider.zapsign,
            status=IntegrationStatus.connected,
            label="ZapSign",
            base_url="https://api.zapsign.com.br/api/v1",
            settings=protected_secrets,
            capabilities=["sign_documents", "webhooks"],
        )
        db.add(conn)

    await db.commit()
    return ZapSignConnectResponse(connected=True, message="ZapSign conectado com sucesso!")


@router.delete(
    "/connect/zapsign",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Desconectar ZapSign do workspace",
)
async def disconnect_zapsign(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_integrations_access),
    db: AsyncSession = Depends(get_db),
):
    from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.zapsign,
        )
    )
    conn = result.scalar_one_or_none()
    if conn:
        conn.status = IntegrationStatus.disconnected
        conn.settings = {}
        await db.commit()


# ── Conectar Asaas (por workspace) ───────────────────────────────────────────

@router.post(
    "/connect/asaas",
    response_model=AsaasConnectResponse,
    summary="Onboarding Asaas pausado (Em breve)",
)
async def connect_asaas(
    data: AsaasConnectRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_integrations_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Mantém o onboarding externo do Asaas congelado até o gatilho registrado.
    """
    _require_financial_upgrade_unlocked()

    import httpx

    from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
    from app.services.integration_secrets import protect_integration_settings

    _require_online_onboarding()
    # Testar conectividade básica
    base_url = (
        "https://api-sandbox.asaas.com/v3"
        if data.environment == "sandbox"
        else "https://api.asaas.com/v3"
    )
    connected = False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/finance/getCurrentBalance",
                headers={"access_token": data.api_key.strip()},
            )
            connected = resp.status_code == 200
    except Exception:
        connected = False

    if not connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key Asaas inválida ou sem acesso. Verifique em app.asaas.com → Integrações.",
        )

    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.asaas,
        )
    )
    conn = result.scalar_one_or_none()

    if conn:
        conn.status = IntegrationStatus.connected
        conn.settings = {
            **(conn.settings or {}),
            **protect_integration_settings(
                {"apiKey": data.api_key.strip(), "environment": data.environment}
            ),
        }
        conn.base_url = base_url
    else:
        conn = IntegrationConnection(
            workspace_id=workspace_id,
            provider=IntegrationProvider.asaas,
            status=IntegrationStatus.connected,
            label="Asaas",
            base_url=base_url,
            settings=protect_integration_settings(
                {"apiKey": data.api_key.strip(), "environment": data.environment}
            ),
            capabilities=["charges", "subscriptions", "pix", "boleto"],
        )
        db.add(conn)

    await db.commit()
    return AsaasConnectResponse(connected=True, message=f"Asaas ({data.environment}) conectado com sucesso!")


@router.delete(
    "/connect/asaas",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Desconectar Asaas do workspace",
)
async def disconnect_asaas(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_integrations_access),
    db: AsyncSession = Depends(get_db),
):
    from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.asaas,
        )
    )
    conn = result.scalar_one_or_none()
    if conn:
        conn.status = IntegrationStatus.disconnected
        conn.settings = {}
        await db.commit()


# ── Status das integrações do workspace ──────────────────────────────────────

@router.get(
    "/status",
    summary="Status de todas as integrações do workspace",
)
async def get_integrations_status(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_integrations_access),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.models.finance import IntegrationConnection, IntegrationStatus
    from app.services.financial_upgrade_service import is_paused_financial_integration
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
        )
    )
    connections = result.scalars().all()
    return {
        conn.provider.value: {
            "connected": (
                conn.status == IntegrationStatus.connected
                and not is_paused_financial_integration(
                    provider=conn.provider,
                    label=conn.label,
                    base_url=conn.base_url,
                    webhook_url=conn.webhook_url,
                    capabilities=conn.capabilities,
                    settings=conn.settings,
                    notes=conn.notes,
                )
            ),
            "label": conn.label,
            "last_sync_at": (
                None
                if is_paused_financial_integration(
                    provider=conn.provider,
                    label=conn.label,
                    base_url=conn.base_url,
                    webhook_url=conn.webhook_url,
                    capabilities=conn.capabilities,
                    settings=conn.settings,
                    notes=conn.notes,
                )
                else conn.last_sync_at.isoformat() if conn.last_sync_at else None
            ),
        }
        for conn in connections
    }


# ── Webhook genérico ──────────────────────────────────────────────────────────

@router.post(
    "/webhooks/{workspace_slug}/{provider}",
    response_model=WebhookReceiptRead,
    summary="Receber webhook genérico por provider",
)
async def receive_provider_webhook(
    workspace_slug: str,
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_klm_signature: str = Header(default=""),
):
    # The local-only gate must run before reading or parsing an external body.
    # This keeps blocked providers fail-closed and avoids doing work on data we
    # are not allowed to accept during the local launch phase.
    _require_external_webhook_delivery()
    if provider.strip().lower() in PAUSED_FINANCIAL_PROVIDERS:
        _require_financial_upgrade_unlocked()
    body = await read_bounded_body(request)
    try:
        payload: dict[str, Any] = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload JSON inválido.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload do webhook deve ser um objeto JSON.",
        )
    return await IntegrationService(db).ingest_provider_webhook(
        provider=provider,
        workspace_slug=workspace_slug,
        payload=payload,
        payload_bytes=body,
        signature=x_klm_signature,
    )


# ── Tracking de conversão paga (Google Data Manager) ────────────────────────
#
# Fecha o dormente #2 da auditoria: o despacho de
# ExternalActionIntent(action_type="ads_conversion_feedback") já existe e é
# provado por teste (Onda 10, app/workers/ads_feedback_tasks.py). O dormente
# #1 (Meta CAPI) NÃO é dormente — já existe conexão completa por
# IntegrationsPage.tsx (aba "Mensuração Meta (CAPI)") + endpoints
# GET/PUT /api/v1/klm/tracking/ads-measurement-config
# (app/api/v1/endpoints/klm_operations.py), resolvendo a mesma
# IntegrationConnection(provider=custom, label="meta_capi") que
# app.workers.ads_feedback_tasks.load_meta_capi_config já lê. Duplicar esse
# caminho aqui só criaria dois formulários escrevendo na mesma linha —
# por isso só o Google Data Manager (que não tinha nenhum caminho de escrita)
# ganha endpoint novo.
#
# O segredo nunca é devolvido: GET .../status só informa has*Token/booleans.
# PUT é um upsert idempotente (mesmo resultado final para a mesma entrada) e
# preserva o segredo já salvo quando o campo de segredo vem vazio/omitido.


@router.put(
    "/tracking/google-data-manager",
    response_model=GoogleDataManagerTrackingStatusRead,
    summary="Configurar tracking de conversão via Google Data Manager API",
)
async def upsert_google_data_manager_tracking(
    payload: GoogleDataManagerTrackingUpsertRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_tracking_manage),
    db: AsyncSession = Depends(get_db),
) -> GoogleDataManagerTrackingStatusRead:
    from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
    from app.models.klm_operations import KlmAuditEvent
    from app.services.audit_actor_service import resolve_human_actor_label
    from app.services.google_conversions_service import GOOGLE_CONVERSIONS_LABEL
    from app.services.integration_secrets import protect_integration_settings

    conn = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.google_ads,
            IntegrationConnection.label == GOOGLE_CONVERSIONS_LABEL,
        )
    )
    existing_settings = (conn.settings or {}) if conn else {}
    existing_developer_token_encrypted = existing_settings.get("developerTokenEncrypted")
    existing_access_token_encrypted = existing_settings.get("accessTokenEncrypted")

    if payload.developer_token:
        developer_token_encrypted = protect_integration_settings(
            {"developerToken": payload.developer_token}
        )["developerTokenEncrypted"]
    elif existing_developer_token_encrypted:
        developer_token_encrypted = existing_developer_token_encrypted
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "TRACKING-GOOGLE-DM-422",
                "message": (
                    "developerToken é obrigatório na primeira configuração do "
                    "Google Data Manager."
                ),
            },
        )

    if payload.access_token:
        access_token_encrypted = protect_integration_settings(
            {"accessToken": payload.access_token}
        )["accessTokenEncrypted"]
    elif existing_access_token_encrypted:
        access_token_encrypted = existing_access_token_encrypted
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "TRACKING-GOOGLE-DM-422",
                "message": (
                    "accessToken é obrigatório na primeira configuração do "
                    "Google Data Manager."
                ),
            },
        )

    new_settings = {
        "customerId": payload.customer_id,
        "developerTokenEncrypted": developer_token_encrypted,
        "accessTokenEncrypted": access_token_encrypted,
    }

    was_configured = bool(
        existing_settings.get("customerId")
        and existing_developer_token_encrypted
        and existing_access_token_encrypted
    )

    if conn:
        conn.status = IntegrationStatus.connected
        conn.settings = new_settings
        conn.label = GOOGLE_CONVERSIONS_LABEL
    else:
        conn = IntegrationConnection(
            workspace_id=workspace_id,
            provider=IntegrationProvider.google_ads,
            status=IntegrationStatus.connected,
            label=GOOGLE_CONVERSIONS_LABEL,
            settings=new_settings,
            capabilities=["ads_conversion_feedback"],
        )
        db.add(conn)

    actor_label = await resolve_human_actor_label(db, auth.user_id)
    db.add(
        KlmAuditEvent(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            actor_type="human",
            actor_label=actor_label,
            event_type="tracking_google_data_manager_configured",
            entity_type="integration_connection",
            entity_id=conn.id,
            summary="Configuração de tracking de conversão Google Data Manager atualizada.",
            before_data={"configured": was_configured},
            after_data={
                "customerId": payload.customer_id,
                "developerTokenRotated": bool(payload.developer_token),
                "accessTokenRotated": bool(payload.access_token),
            },
        )
    )

    await db.commit()
    return GoogleDataManagerTrackingStatusRead(
        configured=bool(
            new_settings.get("customerId")
            and new_settings.get("developerTokenEncrypted")
            and new_settings.get("accessTokenEncrypted")
        ),
        customer_id=new_settings.get("customerId"),
        has_developer_token=bool(new_settings.get("developerTokenEncrypted")),
        has_access_token=bool(new_settings.get("accessTokenEncrypted")),
    )


@router.get(
    "/tracking/google-data-manager/status",
    response_model=GoogleDataManagerTrackingStatusRead,
    summary="Status do tracking de conversão Google Data Manager",
)
async def get_google_data_manager_tracking_status(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_tracking_view),
    db: AsyncSession = Depends(get_db),
) -> GoogleDataManagerTrackingStatusRead:
    from app.services.google_conversions_service import load_google_data_manager_config

    config = await load_google_data_manager_config(db, workspace_id)
    return GoogleDataManagerTrackingStatusRead(
        configured=config.configured,
        customer_id=config.customer_id,
        has_developer_token=bool(config.developer_token),
        has_access_token=bool(config.access_token),
    )
