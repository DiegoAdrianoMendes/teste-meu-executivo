"""
Endpoint: Webhook de Lead Form do Google Ads (auditoria 2026-07-26, item 4).

Rota pública: POST /api/v1/webhooks/google-leads (`google_key` vai NO corpo,
não na URL — é assim que o Google Ads envia o `WebhookLead`, ver
`docs/GOOGLE_TRACKING_KNOWLEDGE_2026-07.md` §2). Rota de configuração (RBAC
admin do workspace): POST .../integrations/google-ads/lead-webhook/key.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_workspace_id,
    get_db,
    require_workspace_admin,
)
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.request_body import read_bounded_body
from app.services.google_lead_webhook_service import (
    MAX_PAYLOAD_BYTES,
    generate_or_rotate_google_key,
    ingest_google_lead,
    resolve_workspace_by_google_key,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
config_router = APIRouter(
    prefix="/integrations/google-ads/lead-webhook", tags=["Integrações — Google Ads"]
)


@router.post("/google-leads", include_in_schema=False)
async def receive_google_lead(request: Request, db: AsyncSession = Depends(get_db)):
    """Recebe um `WebhookLead` do Google Ads. Google não retenta em 4xx e
    retenta em 5xx (§2 do dossiê) — respostas seguem esse contrato: `google_key`
    inválida -> 401 (não retentar); payload ilegível/sem `lead_id` -> 400 (não
    retentar); falha interna -> 500 (retentar)."""
    raw_body = await read_bounded_body(
        request,
        max_bytes=MAX_PAYLOAD_BYTES,
        too_large_detail="Payload excede o tamanho máximo.",
    )

    try:
        payload = json.loads(raw_body) if raw_body else {}
        if not isinstance(payload, dict):
            raise ValueError("payload não é um objeto")
    except Exception as exc:  # noqa: BLE001 — payload ilegível: 400, Google não retenta
        logger.warning("google_lead_webhook.payload_unparseable", error_type=safe_error_type(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "GADS-WH-002", "message": "Payload ilegível."},
        ) from exc

    google_key = str(payload.get("google_key") or payload.get("googleKey") or "").strip()
    workspace_id = await resolve_workspace_by_google_key(db, google_key)
    if workspace_id is None:
        logger.warning("google_lead_webhook.key_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "GADS-WH-001", "message": "google_key inválida."},
        )

    try:
        result = await ingest_google_lead(db, workspace_id=workspace_id, payload=payload)
    except Exception as exc:  # noqa: BLE001 — 500 para o Google retentar; nunca vaza stacktrace
        await db.rollback()
        logger.error(
            "google_lead_webhook.ingest_failed",
            workspace_id=str(workspace_id),
            error_type=safe_error_type(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "GADS-WH-003", "message": "Falha interna registrada."},
        ) from exc

    if not result.accepted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "GADS-WH-004", "message": result.reason},
        )

    return {"status": "accepted", "reason": result.reason}


@config_router.post("/key")
async def rotate_google_lead_webhook_key(
    workspace_id=Depends(get_current_workspace_id),
    _admin: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
):
    """Gera/rotaciona a `google_key` do webhook de Lead Form do Google Ads
    para o workspace atual. RBAC: apenas admin do workspace."""
    plain_key = await generate_or_rotate_google_key(db, workspace_id=workspace_id)
    await db.commit()
    return {
        "googleKey": plain_key,
        "webhookUrl": "/api/v1/webhooks/google-leads",
    }
